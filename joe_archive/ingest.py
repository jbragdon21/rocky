"""--check and --backfill / --sync.

--check finds which grant reaches the mailbox, sizes Sent Items, proves the
exact listing and MIME requests the backfill makes, and counts Joe's mail
filed outside Sent Items. It writes check.json and records the working auth
path in state.json, which the backfill then uses.

--backfill walks Sent Items oldest first, downloads each new send's MIME to
eml\\YYYY\\MM\\, and indexes it. It checkpoints after every page (the cursor
is the latest sentDateTime fully handled) and dedups on internetMessageId,
so a rerun resumes, and rerunning after it finishes picks up new sends.
--sync is that rerun with a two-day overlap, for the nightly schedule.
"""

import json
import logging
import shutil
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from time_entries.mail import parse_utc

from . import graph, store
from .graph import GRAPH, GraphError, TokenKeeper

log = logging.getLogger("rocky")

DB_NAME = "archive.db"
MAX_TRIES = 5                 # a send that fails this often waits for a human
MIN_FREE_GB = 2.0             # the backfill stops before the disk fills
FOLDER_SCAN_CAP = 600         # folders checked for Joe's filed sends
# Not worth archiving: read receipts, meeting replies, recall notices.
SKIP_CLASSES = ("REPORT.", "IPM.Schedule.Meeting.Resp", "IPM.Recall", "IPM.Outlook.Recall")
NOT_FILED = ("sentitems", "drafts", "outbox")


def state_path(root: Path) -> Path:
    return root / "state.json"


def load_state(root: Path) -> dict:
    p = state_path(root)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning(f"[joe-archive] unreadable {p}; starting a fresh state")
    return {}


def save_state(root: Path, state: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    tmp = state_path(root).with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(state_path(root))


def _gb(n: int | float | None) -> str:
    return "?" if n is None else f"{n / 1024 ** 3:.1f} GB"


# =============================================================================
# Finding a way in
# =============================================================================

def _alt_mailbox(mailbox: str) -> str | None:
    import teams  # lazy; rocky@gallagherllp.com <-> rocky@gejlaw.com
    alt = teams.swap_legacy_domain(mailbox)
    return alt if alt.lower() != mailbox.lower() else None


def find_access(config: dict, mailbox: str, paths=graph.AUTH_ORDER):
    """Try each auth path against Sent Items. Returns (path, keeper, mailbox,
    sent_items_folder, attempts); path is None when nothing works. Each
    attempt is {path, mailbox, ok, note}."""
    attempts = []
    for path in paths:
        token, note = graph.token_for(config, path)
        if not token:
            attempts.append({"path": path, "mailbox": mailbox, "ok": False, "note": note})
            continue
        keeper = TokenKeeper(config, path, token)
        for mb in [mailbox] + ([_alt_mailbox(mailbox)] if _alt_mailbox(mailbox) else []):
            try:
                sent = graph.folder(keeper, mb, "sentitems")
            except GraphError as e:
                attempts.append({"path": path, "mailbox": mb, "ok": False,
                                 "note": str(e)})
                if e.status != 404:
                    break       # only a not-found is worth the other domain
                continue
            attempts.append({"path": path, "mailbox": mb, "ok": True, "note": "reads Sent Items"})
            return path, keeper, mb, sent, attempts
    return None, None, mailbox, None, attempts


def resolve_access(config: dict, root: Path, state: dict):
    """The auth path and mailbox the backfill uses: config joe_archive_auth if
    set, else what --check recorded, else a fresh try of each path."""
    mailbox = state.get("mailbox") or config_mailbox(config)
    forced = (config.get("joe_archive_auth") or "").strip().lower()
    if forced and forced != "auto":
        paths = (forced,)
    elif state.get("auth"):
        paths = (state["auth"],) + tuple(p for p in graph.AUTH_ORDER if p != state["auth"])
    else:
        paths = graph.AUTH_ORDER
    path, keeper, mb, _sent, attempts = find_access(config, mailbox, paths)
    if not path:
        raise GraphError(None, "no auth path reaches the mailbox: " + "; ".join(
            f"{a['path']}: {a['note']}" for a in attempts))
    if state.get("auth") != path or state.get("mailbox") != mb:
        state.update(auth=path, mailbox=mb)
        save_state(root, state)
    return keeper, mb


def config_mailbox(config: dict) -> str:
    return (config.get("joe_archive_mailbox") or "jdugan@gallagherllp.com").strip()


# =============================================================================
# --check
# =============================================================================

def _first_sent(keeper, mb, direction: str) -> str | None:
    data = graph.get_json(keeper, f"{GRAPH}/users/{mb}/mailFolders/sentitems/messages",
                          {"$top": "1", "$select": "sentDateTime",
                           "$orderby": f"sentDateTime {direction}"})
    vals = data.get("value") or []
    return store.norm_utc(vals[0].get("sentDateTime")) if vals else None


WALK_PROGRESS_EVERY = 250


def walk_folders(keeper, mb, cap: int = 5000, progress=None) -> list[dict]:
    """Every (non-hidden) folder: id, path, totalItemCount. progress(n) is
    called every WALK_PROGRESS_EVERY folders: mapping 2,400 folders takes
    minutes, and the 11/5 check sat silent through it."""
    out, stack = [], [(None, "")]
    params = {"$top": "250", "$select": "id,displayName,totalItemCount,childFolderCount"}
    while stack and len(out) < cap:
        pid, prefix = stack.pop()
        url = f"{GRAPH}/users/{mb}/mailFolders" + (f"/{pid}/childFolders" if pid else "")
        next_url, first = None, True
        while first or next_url:
            data = graph.get_json(keeper, next_url or url, None if next_url else params)
            first = False
            for f in data.get("value") or []:
                path = f"{prefix}/{f.get('displayName')}" if prefix else f.get("displayName")
                out.append({"id": f["id"], "path": path,
                            "items": f.get("totalItemCount") or 0})
                if progress and len(out) % WALK_PROGRESS_EVERY == 0:
                    progress(len(out))
                if f.get("childFolderCount"):
                    stack.append((f["id"], path))
            next_url = data.get("@odata.nextLink")
    return out


def joe_addresses(mailbox: str, mb: str) -> list[str]:
    """Every address Joe's sends can carry: the configured one, the one that
    answered, and the legacy-domain twin."""
    alt = _alt_mailbox(mailbox)
    return sorted({mailbox.lower(), mb.lower()} | ({alt.lower()} if alt else set()))


def authored_count(keeper, mb, folder_id: str, addrs: list[str]) -> int | None:
    flt = " or ".join(f"from/emailAddress/address eq '{a}'" for a in addrs)
    resp = graph.get(keeper, f"{GRAPH}/users/{mb}/mailFolders/{folder_id}/messages",
                     params={"$filter": flt, "$count": "true", "$top": "1", "$select": "id"})
    if resp.status_code != 200:
        return None
    return resp.json().get("@odata.count")


def check(config: dict, root: Path, *, scan_folders: bool = True, out=print) -> dict:
    mailbox = config_mailbox(config)
    out(f"Joe's Email Archive: checking access to {mailbox}")
    out("")
    path, keeper, mb, sent, attempts = find_access(config, mailbox)
    for a in attempts:
        out(f"  {'OK ' if a['ok'] else 'no '} {a['path']:<15} {a['mailbox']}: {a['note']}")
    report = {"checked_utc": store.now_utc(), "mailbox": mb, "auth": path,
              "attempts": attempts}
    if not path:
        out("")
        out("Nothing reaches the mailbox. IT needs to do one of these:")
        out(f"  - add {mailbox} to the Application Access Policy group (the app path), or")
        out(f"  - give rocky@ Full Access on {mailbox} (Add-MailboxPermission ... "
            f"-User rocky@gallagherllp.com -AccessRights FullAccess -AutoMapping $false)")
        _save_check(root, report)
        return report

    state = load_state(root)
    state.update(auth=path, mailbox=mb)
    save_state(root, state)

    out("")
    total, size = sent.get("totalItemCount"), sent.get("sizeInBytes")
    oldest, newest = _first_sent(keeper, mb, "asc"), _first_sent(keeper, mb, "desc")
    out(f"Sent Items: {total if total is not None else '?'} items, {_gb(size)}"
        f"{'' if size is not None else ' (Graph did not report a size)'}")
    out(f"  oldest send {oldest or '?'}, newest {newest or '?'}")
    report["sent_items"] = {"items": total, "bytes": size, "oldest": oldest, "newest": newest}

    # The exact listing request the backfill makes, then one MIME download.
    try:
        sample = next(graph.iter_pages(keeper, mb, "sentitems", top=10, max_pages=1), [])
        classes = Counter(graph.message_class(m) or "(none)" for m in sample)
        out(f"  listing request works; sample classes: "
            + ", ".join(f"{k} x{v}" for k, v in classes.most_common()))
        report["listing"] = {"ok": True, "classes": dict(classes)}
        if "(none)" in classes:
            # Show what Graph sent back, so a blank class can be told apart
            # from a missing property without another round trip.
            ids = Counter(p.get("id") or "?" for m in sample
                          for p in m.get("singleValueExtendedProperties") or [])
            out("  (none) means no message class was read; extended properties "
                "returned: " + (", ".join(f"{k!r} x{v}" for k, v in ids.most_common())
                                or "none"))
            report["listing"]["property_ids"] = dict(ids)
        if sample:
            tmp = root / "_check" / "sample.eml"
            try:
                n = graph.download_mime(keeper, mb, sample[-1]["id"], tmp)
                out(f"  MIME download works ({n:,} bytes for one message)")
                report["mime"] = {"ok": True, "bytes": n}
            finally:
                if tmp.exists():
                    tmp.unlink()
                if tmp.parent.exists() and not any(tmp.parent.iterdir()):
                    tmp.parent.rmdir()
    except GraphError as e:
        out(f"  the backfill's requests FAIL: {e}")
        report["listing"] = {"ok": False, "error": str(e)}

    root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(root).free
    out(f"Disk: {_gb(free)} free where the archive goes ({root})"
        + ("" if size is None or free > size * 1.2 + MIN_FREE_GB * 1024 ** 3
           else "  NOT ENOUGH ROOM for Sent Items"))
    report["disk_free"] = free

    if scan_folders:
        report["filed"] = _scan_filed(keeper, mb, mailbox, out)

    out("")
    out("Online Archive: Graph can't see one. If Joe has an Online Archive, his older")
    out("sends there need moving into a folder Rocky can read, as James's were in August.")
    _save_check(root, report)
    out(f"Saved {root / 'check.json'}; the backfill will use the {path} path.")
    return report


def _scan_filed(keeper, mb, mailbox, out) -> dict:
    """Joe-authored mail outside Sent Items, folder by folder."""
    skip = set()
    for name in NOT_FILED:
        try:
            skip.add(graph.folder(keeper, mb, name)["id"])
        except GraphError:
            pass
    out("")
    out("Mapping Joe's folders ...")
    try:
        folders = walk_folders(keeper, mb,
                               progress=lambda n: out(f"  ... {n} folders mapped"))
    except GraphError as e:
        out(f"Folder scan failed: {e}")
        return {"ok": False, "error": str(e)}
    with_mail = [f for f in folders if f["items"] and f["id"] not in skip]
    todo = sorted(with_mail, key=lambda f: -f["items"])[:FOLDER_SCAN_CAP]
    addrs = joe_addresses(mailbox, mb)
    out(f"Looking for Joe's sends filed outside Sent Items: {len(folders)} folders, "
        f"{len(with_mail)} with mail; checking "
        + ("all of them" if len(todo) == len(with_mail) else f"the largest {len(todo)}")
        + " (--census lists every one) ...")
    found, unknown = [], 0
    for i, f in enumerate(todo, 1):
        n = authored_count(keeper, mb, f["id"], addrs)
        if n is None:
            unknown += 1
        elif n:
            found.append({"path": f["path"], "from_joe": n, "items": f["items"]})
        if i % 100 == 0:
            out(f"  ... {i} folders checked")
    found.sort(key=lambda f: -f["from_joe"])
    total = sum(f["from_joe"] for f in found)
    out(f"  {total} of Joe's sends sit in {len(found)} other folders"
        + (f" ({unknown} folders couldn't be counted)" if unknown else ""))
    for f in found[:15]:
        out(f"    {f['from_joe']:>6}  {f['path']}")
    if len(found) > 15:
        out(f"    ... and {len(found) - 15} more folders (check.json lists them all)")
    return {"ok": True, "total": total, "folders": found, "uncounted": unknown,
            "scanned": len(todo)}


def _save_check(root: Path, report: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "check.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


# =============================================================================
# --backfill / --sync
# =============================================================================

def skip_reason(msg: dict, mclass: str) -> str | None:
    if mclass and mclass.startswith(SKIP_CLASSES):
        return mclass
    if "eventMessageResponse" in (msg.get("@odata.type") or ""):
        return "meeting response"
    return None


def _download(keeper, mb, root: Path, msg: dict):
    rel = store.eml_relpath(msg)
    try:
        size = graph.download_mime(keeper, mb, msg["id"], root / rel)
        return rel, size, None
    except Exception as e:   # one bad send must not end an hours-long run
        return rel, None, f"{type(e).__name__}: {e}"


def _archive(conn, keeper, mb, root, batch, workers, counts) -> str | None:
    """Download and index (msg, mclass) pairs; returns the latest sentDateTime
    handled. Failures go to the failures table for the next run."""
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(lambda pair: _download(keeper, mb, root, pair[0]), batch))
    latest = None
    for (msg, mclass), (rel, size, err) in zip(batch, results):
        sent = store.norm_utc(msg.get("sentDateTime"))
        latest = max(latest or sent, sent)
        if err:
            store.record_failure(conn, msg, err)
            counts["failed"] += 1
            log.warning(f"[joe-archive] {sent} {msg.get('subject', '')[:60]!r}: {err}")
            continue
        row = store.build_row(msg, "Sent Items", mclass, rel, size,
                              store.attachment_names(root / rel))
        counts["archived" if store.insert(conn, row) else "duplicate"] += 1
    conn.commit()
    return latest


def backfill(config: dict, root: Path, *, limit: int | None = None, workers: int = 4,
             overlap_days: int = 0, out=print) -> dict:
    state = load_state(root)
    keeper, mb = resolve_access(config, root, state)
    conn = store.open_db(root / DB_NAME)
    bf = state.setdefault("backfill", {})
    counts = Counter()
    min_free = float(config.get("joe_archive_min_free_gb") or MIN_FREE_GB) * 1024 ** 3

    # Earlier failures first, so a flaky send isn't left behind the cursor.
    for (gid,) in conn.execute("SELECT graph_id FROM failures WHERE tries < ?",
                               (MAX_TRIES,)).fetchall():
        msg = graph.message(keeper, mb, gid)
        if msg is None:
            conn.execute("DELETE FROM failures WHERE graph_id=?", (gid,))
            conn.commit()
            continue
        _archive(conn, keeper, mb, root, [(msg, graph.message_class(msg))], 1, counts)

    since = bf.get("cursor")
    if since and overlap_days:
        since = store.norm_utc((parse_utc(since) - timedelta(days=overlap_days)).isoformat())
    out(f"Joe's Email Archive: {'syncing' if overlap_days else 'backfilling'} {mb} "
        f"({keeper.path} path) " + (f"from {since}" if since else "from the oldest send"))
    finished = True
    for page in graph.iter_pages(keeper, mb, "sentitems", since):
        if shutil.disk_usage(root).free < min_free:
            out(f"Stopping: under {_gb(min_free)} free on the archive's disk.")
            finished = False
            break
        known = store.known_keys(conn, [store.msg_key(m) for m in page])
        batch, seen, cursor = [], set(), None
        for msg in page:
            key = store.msg_key(msg)
            if key in known or key in seen:
                counts["duplicate"] += 1
                continue
            seen.add(key)
            mclass = graph.message_class(msg)
            if skip_reason(msg, mclass):
                counts["skipped"] += 1
                continue
            batch.append((msg, mclass))
        cut = False
        if limit is not None and len(batch) > limit - counts["archived"]:
            batch = batch[:max(0, limit - counts["archived"])]
            cut = True
        failed_before = counts["failed"]
        latest = _archive(conn, keeper, mb, root, batch, workers, counts) if batch else None
        if len(batch) >= 3 and counts["failed"] - failed_before == len(batch):
            # Every download failed (a lost token, a revoked grant): stop
            # before the whole folder lands in the failures table.
            out("Stopping: every download on this page failed (see rocky.log). "
                "The checkpoint holds; rerun once the cause is fixed.")
            finished = False
            break
        # A page cut short by --limit resumes from its last handled send; a
        # whole page resumes from its newest.
        cursor = latest if cut else max(store.norm_utc(m.get("sentDateTime")) for m in page)
        if cursor:
            bf["cursor"] = max(bf.get("cursor") or cursor, cursor)
        bf["last_run_utc"] = store.now_utc()
        save_state(root, state)
        log.info(f"[joe-archive] {counts['archived']} archived this run "
                 f"(through {bf.get('cursor')}); {counts['duplicate']} already had, "
                 f"{counts['skipped']} skipped, {counts['failed']} failed")
        if cut:
            finished = False
            break
    if finished:
        bf["complete_utc"] = store.now_utc()
    save_state(root, state)
    s = store.stats(conn)
    conn.close()
    out(f"Done: {counts['archived']} archived this run, {counts['duplicate']} already "
        f"archived, {counts['skipped']} skipped (receipts and meeting replies), "
        f"{counts['failed']} failed.")
    out(f"Archive: {s['messages']} sends, {s['oldest'] or '-'} to {s['newest'] or '-'}, "
        f"{_gb(s['eml_bytes'])} of .eml; {s['failures']} waiting on a retry.")
    if not finished:
        out("Not finished: run it again to continue from the checkpoint.")
    return {**counts, "finished": finished, "stats": s}
