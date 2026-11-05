"""rocky.py --joe-archive: Joe's Email Archive (JOE_ARCHIVE_DESIGN.md).

    --joe-archive --check [--no-folders]
        Which grant reaches jdugan@, how big Sent Items is, whether the
        backfill's requests work, and how many of Joe's sends are filed in
        other folders (--no-folders skips that scan). Writes check.json and
        records the working auth path for the backfill.

    --joe-archive --census
        Every send of Joe's in every folder, counted without downloading
        anything: unique sends, those found only outside Sent Items (and only
        in Deleted Items), Enterprise Vault shortcuts with no full copy in the
        mailbox, a year-by-year table and the folders holding the most sends
        found nowhere else. Writes census.json.

    --joe-archive --vault-check
        Can Rocky reach the full copies behind the Vault shortcuts? Rocky's
        Graph permissions, how much text a shortcut keeps next to a full
        send, a whole-mailbox lookup for a full copy of each sampled
        shortcut, Graph's archive-mailbox routes, and the Vault server the
        shortcuts link to. Numbers only, safe to paste. Writes
        vault_check.json.

    --joe-archive --backfill [--limit N] [--workers N]
        Copy Sent Items, oldest first, to eml\\YYYY\\MM\\ and index it.
        Checkpointed: rerun to resume. --limit N stops after N new sends
        (try --limit 200 first). --workers: parallel MIME downloads (4;
        Exchange allows 4 at once per mailbox).

    --joe-archive --sync
        The backfill again with a two-day overlap: picks up new sends. Made
        for a nightly scheduled task.

    --joe-archive --search "query" [--from YYYY-MM-DD] [--to YYYY-MM-DD]
                  [--recipient TEXT] [--subject TEXT] [--own]
                  [--newest | --oldest] [--limit N]
        Search the archive. FTS5 syntax: "exact phrase", AND / OR / NOT,
        NEAR(a b, 10), prefix*. --own searches only what Joe typed (subject
        and his text above the quoted history). An empty query with filters
        lists sends, newest first.

    --joe-archive --show REF      One archived send in full, by its [ref].
    --joe-archive --stats         Size and date range of the archive.

Common: --root <folder> overrides joe_archive_root (default
<data>\\joe_archive_data: C:\\Rocky\\joe_archive_data on the Rocky laptop).
Config: joe_archive_mailbox (jdugan@gallagherllp.com), joe_archive_auth
(auto | app | delegated-read | delegated), joe_archive_min_free_gb (2).
"""

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

from . import census, evault, ingest, search, store
from .graph import GraphError

log = logging.getLogger("rocky")

# These share the joe-archive lock: they call Graph hard (Exchange allows 4
# requests at once per mailbox) or write the archive.
WRITE_FLAGS = ("--check", "--census", "--vault-check", "--backfill", "--sync")


def get_root(config: dict, data_dir: Path, override: str | None = None) -> Path:
    raw = override or config.get("joe_archive_root")
    return Path(raw) if raw else Path(data_dir) / "joe_archive_data"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rocky.py --joe-archive", add_help=False)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--census", action="store_true")
    mode.add_argument("--vault-check", action="store_true")
    mode.add_argument("--backfill", action="store_true")
    mode.add_argument("--sync", action="store_true")
    mode.add_argument("--search", metavar="QUERY")
    mode.add_argument("--show", type=int, metavar="REF")
    mode.add_argument("--stats", action="store_true")
    p.add_argument("--no-folders", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--from", dest="date_from")
    p.add_argument("--to", dest="date_to")
    p.add_argument("--recipient")
    p.add_argument("--subject")
    p.add_argument("--own", action="store_true")
    order = p.add_mutually_exclusive_group()
    order.add_argument("--newest", action="store_true")
    order.add_argument("--oldest", action="store_true")
    p.add_argument("--root")
    return p


def open_archive(root: Path) -> sqlite3.Connection | None:
    db = root / ingest.DB_NAME
    if not db.exists():
        return None
    try:
        return store.open_db(db, read_only=True)
    except sqlite3.OperationalError:
        return store.open_db(db)


def run_cli(config: dict, data_dir: Path, argv: list[str] | None = None) -> int:
    argv = [a for a in (sys.argv[1:] if argv is None else argv) if a != "--joe-archive"]
    args, unknown = _parser().parse_known_args(argv)
    if unknown:
        print(f"Unknown options for --joe-archive: {' '.join(unknown)}")
        print(__doc__)
        return 2
    root = get_root(config, data_dir, args.root)

    try:
        if args.check:
            report = ingest.check(config, root, scan_folders=not args.no_folders)
            return 0 if report.get("auth") else 1
        if args.census:
            report = census.run(config, root)
            return 0 if report.get("sent_items_listed") else 1
        if args.vault_check:
            evault.run(config, root)
            return 0
        if args.backfill or args.sync:
            result = ingest.backfill(config, root, limit=args.limit,
                                     workers=max(1, min(args.workers, 4)),
                                     overlap_days=2 if args.sync else 0)
            return 0 if result["finished"] or args.limit else 1
    except GraphError as e:
        log.error(f"[joe-archive] {e}")
        print(f"Stopped: {e}")
        return 1

    if args.search is None and args.show is None and not args.stats:
        print(__doc__)
        return 2
    conn = open_archive(root)
    if conn is None:
        print(f"No archive at {root} yet. Run --joe-archive --check, then --backfill.")
        return 1
    try:
        if args.stats:
            s = store.stats(conn)
            print(f"{s['messages']} sends archived, {search.eastern(s['oldest']) or '-'} to "
                  f"{search.eastern(s['newest']) or '-'}; {s['with_attachments']} with attachments; "
                  f"{s['eml_bytes'] / 1024 ** 3:.1f} GB of .eml; "
                  f"{s['failures']} failed downloads waiting on a retry.")
            state = ingest.load_state(root)
            bf = state.get("backfill") or {}
            print(f"Auth path {state.get('auth') or '?'}; backfill cursor "
                  f"{bf.get('cursor') or '-'}; "
                  + (f"finished {bf['complete_utc']}" if bf.get("complete_utc")
                     else "not finished"))
            return 0
        if args.show is not None:
            row = search.read_message(conn, args.show)
            if not row:
                print(f"No archived send with ref {args.show}.")
                return 1
            print(search.format_message(row))
            print(f"\n.eml: {root / row['eml_path']}" if row.get("eml_path") else "")
            return 0
        order = "newest" if args.newest else "oldest" if args.oldest else "rank"
        try:
            result = search.search(conn, args.search, date_from=args.date_from,
                                   date_to=args.date_to, recipient=args.recipient,
                                   subject=args.subject,
                                   fields="own" if args.own else "all",
                                   order=order, limit=args.limit or 20)
        except search.SearchError as e:
            print(f"Search failed: {e}")
            return 1
        print(search.format_hits(result, args.search))
        return 0
    finally:
        conn.close()
