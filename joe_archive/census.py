"""--joe-archive --census: every send of Joe's in every folder, counted
without downloading anything (JOE_ARCHIVE_DESIGN.md, status 2026-11-05).

The 11/5 check found two things a Sent Items backfill can't see past. The
oldest sends are Enterprise Vault shortcuts (a cut-down body, no
attachments; the full copy is in the Vault, which Graph can't reach). And
21,170 of Joe's sends sit in 512 other folders, 10,228 of them in Deleted
Items, counted in only the 600 largest folders.

The census lists each folder's messages from Joe (Sent Items: all of it),
with only ids, sent date and message class. It pairs copies of one send by
internetMessageId and reports:

  * unique sends, and how many exist only outside Sent Items (filed or
    deleted rather than copied), and how many of those only in Deleted Items;
  * Vault shortcuts: how many have no full copy anywhere in the mailbox,
    and how many Sent Items shortcuts have a full copy in another folder;
  * a year-by-year table and the folders holding the most sends found
    nowhere else.

It writes census.json beside check.json, and nothing else. Receipts and
meeting replies are counted, then left out, as the backfill leaves them out.
"""

import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import graph, ingest, store
from .graph import GraphError

SHORTCUT_PREFIX = "IPM.Note.EnterpriseVault.Shortcut"
CENSUS_NAME = "census.json"
WORKERS = 4                   # Exchange allows 4 concurrent requests per mailbox
FOLDER_PROGRESS_EVERY = 100
TOP_FOLDERS = 15


def is_shortcut(mclass: str) -> bool:
    return (mclass or "").startswith(SHORTCUT_PREFIX)


def _kind(path: str, folder_id: str, sent_id: str, deleted_path: str | None) -> str:
    if folder_id == sent_id:
        return "sent"
    if deleted_path and (path == deleted_path or path.startswith(deleted_path + "/")):
        return "deleted"
    return "filed"


def _list_folder(keeper, mb, f: dict, addrs: list[str] | None) -> list[tuple]:
    """(key, sent, class, skipped, has_imid) for each message in one folder."""
    rows = []
    for page in graph.iter_index(keeper, mb, f["id"], from_addrs=addrs):
        for msg in page:
            mclass = graph.message_class(msg)
            rows.append((store.msg_key(msg), store.norm_utc(msg.get("sentDateTime")),
                         mclass, bool(ingest.skip_reason(msg, mclass)),
                         bool((msg.get("internetMessageId") or "").strip())))
    return rows


def run(config: dict, root: Path, out=print) -> dict:
    state = ingest.load_state(root)
    keeper, mb = ingest.resolve_access(config, root, state)
    mailbox = ingest.config_mailbox(config)
    addrs = ingest.joe_addresses(mailbox, mb)
    out(f"Joe's Email Archive: census of {mb} ({keeper.path} path). Nothing is downloaded.")

    ids = {}
    for name in ("sentitems", "deleteditems", "drafts", "outbox"):
        try:
            ids[name] = graph.folder(keeper, mb, name)["id"]
        except GraphError:
            ids[name] = None
    if not ids["sentitems"]:
        raise GraphError(None, "Sent Items can't be read")

    out("Mapping Joe's folders ...")
    folders = ingest.walk_folders(keeper, mb,
                                  progress=lambda n: out(f"  ... {n} folders mapped"))
    by_id = {f["id"]: f for f in folders}
    deleted_path = by_id.get(ids["deleteditems"], {}).get("path")
    if ids["sentitems"] not in by_id:
        folders.append({"id": ids["sentitems"], "path": "Sent Items", "items": 1})
    skip_ids = {ids["drafts"], ids["outbox"]} - {None}
    todo = [f for f in folders if f["items"] and f["id"] not in skip_ids]
    out(f"{len(folders)} folders, {len(todo)} with mail. Listing Joe's sends in each ...")

    # key -> where its copies are and what they are
    recs = defaultdict(lambda: {"sent": None, "kinds": set(), "full": set(),
                                "short": set(), "folders": set()})
    per_folder = {}
    unreadable = []
    counts = Counter()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(_list_folder, keeper, mb, f,
                               None if f["id"] == ids["sentitems"] else addrs): f
                   for f in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            f = futures[fut]
            kind = _kind(f["path"], f["id"], ids["sentitems"], deleted_path)
            try:
                rows = fut.result()
            except Exception as e:      # one bad folder must not end the census
                unreadable.append({"path": f["path"], "error": f"{type(e).__name__}: {e}"})
                if f["id"] == ids["sentitems"]:
                    out(f"  Sent Items couldn't be listed: {e}")
                rows = []
            n_joe = 0
            for key, sent, mclass, skipped, has_imid in rows:
                if skipped:
                    counts["skipped"] += 1
                    continue
                n_joe += 1
                if not has_imid:
                    counts["no_message_id"] += 1
                r = recs[key]
                if sent and (r["sent"] is None or sent < r["sent"]):
                    r["sent"] = sent    # copies share a send date; keep the earliest
                r["kinds"].add(kind)
                (r["short"] if is_shortcut(mclass) else r["full"]).add(kind)
                r["folders"].add(f["path"])
            if n_joe:
                per_folder[f["path"]] = {"kind": kind, "joe": n_joe}
            if i % FOLDER_PROGRESS_EVERY == 0:
                out(f"  ... {i} of {len(todo)} folders listed")

    report = _summarize(recs, per_folder, counts, unreadable, mb, keeper.path, len(folders),
                        len(todo))
    report["sent_items_listed"] = not any(u["path"] == by_id.get(ids["sentitems"], {}).get(
        "path", "Sent Items") for u in unreadable)
    _print(report, out)
    root.mkdir(parents=True, exist_ok=True)
    (root / CENSUS_NAME).write_text(json.dumps(report, indent=2), encoding="utf-8")
    out(f"Saved {root / CENSUS_NAME}")
    return report


def _summarize(recs, per_folder, counts, unreadable, mb, path, n_folders, n_listed) -> dict:
    t = Counter()
    years = defaultdict(Counter)
    only_here = Counter()
    vault_only_dates = []
    for r in recs.values():
        kinds, full = r["kinds"], r["full"]
        in_sent = "sent" in kinds
        year = (r["sent"] or "????")[:4]
        y = years[year]
        t["unique"] += 1
        y["unique"] += 1
        if in_sent:
            t["in_sent_items"] += 1
            y["in_sent_items"] += 1
            if kinds - {"sent"}:
                t["copied_elsewhere"] += 1
        else:
            t["only_outside"] += 1
            y["only_outside"] += 1
            if kinds == {"deleted"}:
                t["only_deleted"] += 1
            elif "deleted" in kinds:
                t["only_filed_and_deleted"] += 1
            else:
                t["only_filed"] += 1
            for folder in r["folders"]:
                only_here[folder] += 1
        if r["short"]:
            t["shortcut_somewhere"] += 1
            if not full:
                t["vault_only"] += 1
                y["vault_only"] += 1
                if r["sent"]:
                    vault_only_dates.append(r["sent"])
            elif "sent" in r["short"] and "sent" not in full:
                t["sent_shortcut_full_elsewhere"] += 1
    folders = sorted(({"path": p, **v, "only_here": only_here.get(p, 0)}
                      for p, v in per_folder.items()),
                     key=lambda f: (-f["only_here"], -f["joe"]))
    return {
        "census_utc": store.now_utc(), "mailbox": mb, "auth": path,
        "folders_total": n_folders, "folders_listed": n_listed,
        "folders_unreadable": unreadable,
        "sends": {**{k: t.get(k, 0) for k in (
            "unique", "in_sent_items", "copied_elsewhere", "only_outside", "only_filed",
            "only_deleted", "only_filed_and_deleted", "shortcut_somewhere", "vault_only",
            "sent_shortcut_full_elsewhere")},
            "skipped_receipts_and_replies": counts.get("skipped", 0),
            "no_message_id": counts.get("no_message_id", 0)},
        "vault_only_range": ([min(vault_only_dates), max(vault_only_dates)]
                             if vault_only_dates else None),
        "years": {y: dict(c) for y, c in sorted(years.items())},
        "folders": folders,
    }


def _print(report: dict, out) -> None:
    s = report["sends"]
    out("")
    out(f"Joe's sends, unique by message id ({s['skipped_receipts_and_replies']:,} receipts "
        f"and meeting replies left out):")
    out(f"  {s['unique']:>8,}  in all")
    out(f"  {s['in_sent_items']:>8,}  in Sent Items ({s['copied_elsewhere']:,} of them also "
        f"filed or deleted elsewhere)")
    out(f"  {s['only_outside']:>8,}  only outside Sent Items: {s['only_filed']:,} filed, "
        f"{s['only_deleted']:,} only in Deleted Items, {s['only_filed_and_deleted']:,} in both")
    if s["no_message_id"]:
        out(f"  {s['no_message_id']:,} copies had no message id, so they couldn't be paired "
            f"(counted once per copy)")
    out("")
    out("Enterprise Vault shortcuts (cut-down copies; the full email is in the Vault):")
    out(f"  {s['shortcut_somewhere']:>8,}  sends with a shortcut copy")
    rng = report["vault_only_range"]
    out(f"  {s['vault_only']:>8,}  with no full copy anywhere in the mailbox"
        + (f" (sent {rng[0][:10]} to {rng[1][:10]})" if rng else ""))
    out(f"  {s['sent_shortcut_full_elsewhere']:>8,}  Sent Items shortcuts with a full copy "
        f"in another folder")
    out("")
    out("  year    sends  in Sent Items  only outside  Vault only")
    for year, c in report["years"].items():
        out(f"  {year}  {c.get('unique', 0):>7,}  {c.get('in_sent_items', 0):>13,}  "
            f"{c.get('only_outside', 0):>12,}  {c.get('vault_only', 0):>10,}")
    top = [f for f in report["folders"] if f["only_here"]][:TOP_FOLDERS]
    if top:
        out("")
        out("Folders holding the most sends found nowhere else:")
        for f in top:
            out(f"  {f['only_here']:>8,}  {f['path']}")
    if not report.get("sent_items_listed", True):
        out("")
        out("INCOMPLETE: Sent Items couldn't be listed, so every send above is counted as "
            "outside it. Rerun the census.")
    if report["folders_unreadable"]:
        out("")
        out(f"{len(report['folders_unreadable'])} folders couldn't be listed "
            f"(census.json names them).")
