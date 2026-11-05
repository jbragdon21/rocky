"""--check and --backfill / --sync.

--check finds which grant reaches the mailbox, sizes Sent Items, proves the
exact listing and MIME requests the backfill makes, and counts Joe's mail
filed outside Sent Items. It writes check.json and records the working auth
path in state.json, which the backfill then uses.

--backfill walks Sent Items oldest first, downloads each new send's MIME to
eml\\YYYY\\MM\\, and indexes it. It checkpoints after every page (the cursor
is the latest sentDateTime fully handled) and dedups on internetMessageId,
so a rerun resumes, and rerunning after it finishes picks up new sends.
Once Sent Items is done, a filed pass (once, until it completes) archives
Joe's sends filed only in other folders, Deleted Items left out (James,
11/5), and full copies of sends archived as Vault shortcuts. --sync is the
Sent Items rerun with a two-day overlap, for the nightly schedule.

Vault shortcuts are archived and flagged by their message class. A full
copy of the same send, found later, replaces one in place and keeps its ref.
"""

import json
import logging
import shutil
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
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

PHASES = ("all", "sent", "filed")
FILED_PROGRESS_EVERY = 100
COUNT_KEYS = ("archived", "shortcuts", "filed", "replaced", "duplicate", "skipped",
              "failed", "gone")


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
    """Download and index (msg, mclass, folder) triples; returns the latest
    sentDateTime handled. A full copy of a send archived as a Vault shortcut
    replaces it. Failures go to the failures table for the next run."""
    # Checked again here, not only by the callers: a retried shortcut whose
    # full copy was archived meanwhile would download over that copy's .eml
    # (the path is keyed on the send, not the copy).
    known = store.known_kinds(conn, [store.msg_key(t[0]) for t in batch])
    keep = []
    for t in batch:
        if store.wanted(known, store.msg_key(t[0]), t[1]):
            keep.append(t)
        else:
            counts["duplicate"] += 1
            conn.execute("DELETE FROM failures WHERE graph_id=?", (t[0].get("id"),))
    batch = keep
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(lambda t: _download(keeper, mb, root, t[0]), batch))
    latest = None
    for (msg, mclass, folder), (rel, size, err) in zip(batch, results):
        sent = store.norm_utc(msg.get("sentDateTime"))
        latest = max(latest or sent, sent)
        if err:
            store.record_failure(conn, msg, err, folder)
            counts["failed"] += 1
            log.warning(f"[joe-archive] {sent} {msg.get('subject', '')[:60]!r}: {err}")
            continue
        row = store.build_row(msg, folder, mclass, rel, size,
                              store.attachment_names(root / rel))
        outcome, old_eml = store.upsert(conn, row)
        if outcome == "new":
            counts["archived"] += 1
            if store.is_shortcut(mclass):
                counts["shortcuts"] += 1
            if folder != "Sent Items":
                counts["filed"] += 1
        elif outcome == "replaced":
            counts["replaced"] += 1
            if old_eml and (root / old_eml).exists():
                (root / old_eml).unlink()
        else:
            counts["duplicate"] += 1
    conn.commit()
    return latest


def _added(counts) -> int:
    return counts["archived"] + counts["replaced"]


def _retry_failures(conn, keeper, mb, root, counts) -> None:
    """Earlier failures first, so a flaky send isn't left behind the cursor."""
    for gid, folder in conn.execute("SELECT graph_id, folder FROM failures WHERE tries < ?",
                                    (MAX_TRIES,)).fetchall():
        try:
            msg = graph.message(keeper, mb, gid)
        except GraphError as e:
            log.warning(f"[joe-archive] retry of {gid[:20]}... failed: {e}")
            continue
        if msg is None:
            conn.execute("DELETE FROM failures WHERE graph_id=?", (gid,))
            conn.commit()
            continue
        _archive(conn, keeper, mb, root, [(msg, graph.message_class(msg),
                                           folder or "Sent Items")], 1, counts)


def _sent_items(conn, keeper, mb, root, bf, state, since, limit, workers, min_free,
                counts, out) -> bool:
    """Sent Items, oldest first, checkpointed per page. True when it reached
    the newest send."""
    for page in graph.iter_pages(keeper, mb, "sentitems", since):
        if shutil.disk_usage(root).free < min_free:
            out(f"Stopping: under {_gb(min_free)} free on the archive's disk.")
            return False
        known = store.known_kinds(conn, [store.msg_key(m) for m in page])
        batch, seen = [], set()
        for msg in page:
            key = store.msg_key(msg)
            if key in seen:
                counts["duplicate"] += 1
                continue
            seen.add(key)
            mclass = graph.message_class(msg)
            if skip_reason(msg, mclass):
                counts["skipped"] += 1
            elif not store.wanted(known, key, mclass):
                counts["duplicate"] += 1
            else:
                batch.append((msg, mclass, "Sent Items"))
        cut = False
        if limit is not None and len(batch) > limit - _added(counts):
            batch = batch[:max(0, limit - _added(counts))]
            cut = True
        failed_before = counts["failed"]
        latest = _archive(conn, keeper, mb, root, batch, workers, counts) if batch else None
        if len(batch) >= 3 and counts["failed"] - failed_before == len(batch):
            # Every download failed (a lost token, a revoked grant): stop
            # before the whole folder lands in the failures table.
            out("Stopping: every download on this page failed (see rocky.log). "
                "The checkpoint holds; rerun once the cause is fixed.")
            return False
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
            return False
    return True


def _under(path: str, parent: str | None) -> bool:
    return bool(parent) and (path == parent or path.startswith(parent + "/"))


def _list_joe(keeper, mb, folder_id: str, addrs: list[str]) -> list[dict]:
    rows = []
    for page in graph.iter_index(keeper, mb, folder_id, from_addrs=addrs):
        rows.extend(page)
    return rows


def _fetch(keeper, mb, msg):
    """The full listing shape (body, recipients) of one indexed message:
    (msg, or None if it's gone; error)."""
    try:
        return graph.message(keeper, mb, msg["id"]), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def filed_pass(conn, keeper, mb, root, config, fs: dict, limit, workers, min_free,
               counts, out) -> bool:
    """Joe's sends in every other folder (Deleted Items left out unless
    joe_archive_include_deleted): those not yet archived, and full copies of
    sends archived only as a Vault shortcut. Lists the folders as the census
    does (ids and class only, 4 at a time), then fetches and downloads only
    what's wanted. A rerun lists again and dedups. True when every folder was
    listed and every wanted send handled."""
    ids = {}
    for name in ("sentitems", "deleteditems", "drafts", "outbox"):
        try:
            ids[name] = graph.folder(keeper, mb, name)["id"]
        except GraphError:
            ids[name] = None
    out("Filed pass: mapping Joe's folders ...")
    folders = walk_folders(keeper, mb, progress=lambda n: out(f"  ... {n} folders mapped"))
    by_id = {f["id"]: f for f in folders}
    deleted_path = by_id.get(ids["deleteditems"], {}).get("path")
    include_deleted = bool(config.get("joe_archive_include_deleted"))
    skip_ids = {ids["sentitems"], ids["drafts"], ids["outbox"]} - {None}
    todo = [f for f in folders if f["items"] and f["id"] not in skip_ids
            and (include_deleted or not _under(f["path"], deleted_path))]
    out(f"Listing Joe's sends in {len(todo)} folders outside Sent Items"
        + ("" if include_deleted else " (Deleted Items left out)") + " ...")
    addrs = joe_addresses(config_mailbox(config), mb)

    best, unreadable = {}, []           # key -> (msg, mclass, folder path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_list_joe, keeper, mb, f["id"], addrs): f for f in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            f = futures[fut]
            try:
                rows = fut.result()
            except Exception as e:      # one bad folder must not end the pass
                unreadable.append(f["path"])
                log.warning(f"[joe-archive] couldn't list {f['path']}: {e}")
                rows = []
            for msg in rows:
                mclass = graph.message_class(msg)
                if skip_reason(msg, mclass):
                    continue
                key = store.msg_key(msg)
                cur = best.get(key)
                if cur is None or (store.is_shortcut(cur[1]) and not store.is_shortcut(mclass)):
                    best[key] = (msg, mclass, f["path"])
            if i % FILED_PROGRESS_EVERY == 0:
                out(f"  ... {i} of {len(todo)} folders listed")

    known = store.known_kinds(conn, list(best))
    wanted = sorted(((m, c, p) for k, (m, c, p) in best.items() if store.wanted(known, k, c)),
                    key=lambda t: store.norm_utc(t[0].get("sentDateTime")))
    n_new = sum(1 for m, _c, _p in wanted if store.msg_key(m) not in known)
    out(f"  {len(best):,} of Joe's sends found there; {n_new:,} not yet archived, "
        f"{len(wanted) - n_new:,} full copies of sends archived as Vault shortcuts"
        + (f"; {len(unreadable)} folders couldn't be listed" if unreadable else ""))
    fs.update(last_run_utc=store.now_utc(), found=len(best), wanted=len(wanted),
              unreadable=unreadable[:50])

    for i in range(0, len(wanted), graph.PAGE_SIZE):
        if shutil.disk_usage(root).free < min_free:
            out(f"Stopping: under {_gb(min_free)} free on the archive's disk.")
            return False
        room = None if limit is None else limit - _added(counts)
        if room is not None and room <= 0:
            return False
        batch = wanted[i:i + graph.PAGE_SIZE][:room]
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            fetched = list(pool.map(lambda t: _fetch(keeper, mb, t[0]), batch))
        ready = []
        for (msg, mclass, path), (full, err) in zip(batch, fetched):
            if err:
                store.record_failure(conn, msg, err, path)
                counts["failed"] += 1
            elif full is None:
                counts["gone"] += 1     # moved or deleted since the listing
            else:
                ready.append((full, mclass, path))
        conn.commit()
        failed_before = counts["failed"]
        if ready:
            _archive(conn, keeper, mb, root, ready, workers, counts)
        if len(ready) >= 3 and counts["failed"] - failed_before == len(ready):
            out("Stopping: every download in this batch failed (see rocky.log). "
                "Rerun once the cause is fixed.")
            return False
        if i + len(batch) < min(len(wanted), i + graph.PAGE_SIZE):
            return False                # cut short by --limit
    if unreadable:
        return False
    fs["complete_utc"] = store.now_utc()
    return True


def backfill(config: dict, root: Path, *, limit: int | None = None, workers: int = 4,
             overlap_days: int = 0, start: str | None = None, phase: str = "all",
             out=print) -> dict:
    """phase "all": Sent Items, then the filed pass once Sent Items is done
    (and until that pass completes). "sent": Sent Items only (--sync).
    "filed": the filed pass only. start (YYYY-MM-DD, trial archives only)
    begins Sent Items there instead of at the oldest send."""
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}")
    state = load_state(root)
    keeper, mb = resolve_access(config, root, state)
    conn = store.open_db(root / DB_NAME)
    bf = state.setdefault("backfill", {})
    fs = state.setdefault("filed", {})
    counts = Counter()
    min_free = float(config.get("joe_archive_min_free_gb") or MIN_FREE_GB) * 1024 ** 3

    _retry_failures(conn, keeper, mb, root, counts)

    sent_done = bool(bf.get("complete_utc"))
    finished = True
    if phase in ("all", "sent"):
        since = bf.get("cursor")
        if since and overlap_days:
            since = store.norm_utc((parse_utc(since) - timedelta(days=overlap_days)).isoformat())
        if start:
            since = max(since or "", f"{start}T00:00:00Z")
        out(f"Joe's Email Archive: {'syncing' if overlap_days else 'backfilling'} {mb} "
            f"({keeper.path} path), Sent Items "
            + (f"from {since}" if since else "from the oldest send"))
        sent_done = _sent_items(conn, keeper, mb, root, bf, state, since, limit, workers,
                                min_free, counts, out)
        if sent_done:
            bf["complete_utc"] = store.now_utc()
        finished = sent_done
        save_state(root, state)
    filed_due = sent_done and not fs.get("complete_utc")
    if phase == "filed" or (phase == "all" and filed_due
                            and (limit is None or _added(counts) < limit)):
        if phase == "filed":
            out(f"Joe's Email Archive: filed pass over {mb} ({keeper.path} path)")
        finished = filed_pass(conn, keeper, mb, root, config, fs, limit, workers, min_free,
                              counts, out)
        save_state(root, state)
    elif phase == "all" and filed_due:
        finished = False                # --limit used up before the filed pass

    s = store.stats(conn)
    conn.close()
    out(f"Done: {counts['archived']} archived this run ({counts['shortcuts']} Vault shortcuts, "
        f"{counts['filed']} from other folders), {counts['replaced']} shortcuts replaced by "
        f"a full copy, {counts['duplicate']} already archived, {counts['skipped']} skipped "
        f"(receipts and meeting replies), {counts['failed']} failed"
        + (f", {counts['gone']} gone since listed" if counts["gone"] else "") + ".")
    out(f"Archive: {s['messages']} sends ({s['shortcuts']} Vault shortcuts, {s['filed']} from "
        f"other folders), {s['oldest'] or '-'} to {s['newest'] or '-'}, "
        f"{_gb(s['eml_bytes'])} of .eml; {s['failures']} waiting on a retry.")
    if not finished:
        out("Not finished: run it again to continue from the checkpoint.")
    return {**{k: 0 for k in COUNT_KEYS}, **counts, "finished": finished, "stats": s}
