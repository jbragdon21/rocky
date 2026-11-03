"""rocky.py --time-entries-mail: the monitor step (TIME_ENTRIES_DESIGN.md,
"Trigger").

Every monitor cycle this scans rocky@'s Inbox for firm mail carrying a .csv
or .xlsx. Either of two signals makes it a time-entries email:

  * format: the attachment has the Aspen export's header (Work Date, Matter,
    Narrative in one of its first six rows), whatever the email says;
  * words: the subject or the note typed on top (vault.forwarding_note, so a
    mention deep in a quoted chain never counts) mentions time entries.

A readable export is stored under <local>\\snapshots, ingested into te.db as
a snapshot, queued, and the email moves to Inbox\\Time Entries. When the
words match but no attachment reads as an export, or the export has no rows
for James, the sender gets a short reply saying why. Anything else is left
alone.

The drafting run takes longer than the monitor's 15-minute child timeout, so
this step never drafts. It starts `--time-entries-run` detached whenever a
snapshot is pending; that run holds its own instance lock, so a second start
while one is going exits at once.
"""

import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import db, timecards
from .paths import get_paths

log = logging.getLogger("rocky")

SPREADSHEET_EXT = (".csv", ".xlsx", ".xlsm")
DEFAULT_KEYWORDS = ("time entries", "time entry", "timecard", "time card",
                    "timesheet", "time sheet")
# Rocky's own results emails carry this; a reply to one is not a new export.
SUBJECT_PREFIX = "Rocky — Time entries"
JAMES = "jbragdon@gallagherllp.com"
BACKFILL_DAYS = 3

_MD = r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?"
_RANGE_RE = re.compile(_MD + r"\s*(?:-|–|—|to|through|thru)\s*" + _MD, re.I)
_ONE_DAY_RE = re.compile(r"\b(?:just|only)\s+(?:for\s+|on\s+)?" + _MD, re.I)
_BLANKS_RE = re.compile(r"\b(blanks?\s+only|only\s+(?:the\s+)?blanks?|"
                        r"(?:no|skip|without)\s+(?:the\s+)?partials?)\b", re.I)


def _year_for(month: int, day: int, year: str | None, period) -> int:
    """A bare M/D takes the year that puts it nearest the export's period."""
    if year:
        y = int(year)
        return y + 2000 if y < 100 else y
    ref = date.fromisoformat(period[0]) if period and period[0] else date.today()
    best = None
    for y in (ref.year - 1, ref.year, ref.year + 1):
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        if best is None or abs((d - ref).days) < abs((best - ref).days):
            best = d
    return best.year if best else ref.year


def _iso(m: str, d: str, y: str | None, period) -> str | None:
    try:
        return date(_year_for(int(m), int(d), y, period), int(m), int(d)).isoformat()
    except ValueError:
        return None


def parse_scope(text: str, period=None) -> dict:
    """What the note on top asks the run to cover. {} means every blank and
    partial in the export."""
    scope: dict = {}
    m = _RANGE_RE.search(text or "")
    if m:
        lo = _iso(m.group(1), m.group(2), m.group(3), period)
        hi = _iso(m.group(4), m.group(5), m.group(6) or m.group(3), period)
        if lo and hi:
            scope["from"], scope["to"] = min(lo, hi), max(lo, hi)
    else:
        m = _ONE_DAY_RE.search(text or "")
        if m:
            d = _iso(m.group(1), m.group(2), m.group(3), period)
            if d:
                scope["from"] = scope["to"] = d
    if _BLANKS_RE.search(text or ""):
        scope["blanks_only"] = True
    return scope


def _sender(message: dict) -> str:
    return (((message.get("from") or {}).get("emailAddress") or {})
            .get("address") or "").strip()


def note_text(message: dict) -> str:
    from vault import forwarding_note  # lazy: shared note extractor
    return f"{message.get('subject') or ''}\n{forwarding_note(message)}"


def mentions_time_entries(message: dict, keywords) -> bool:
    text = note_text(message).lower()
    return any(k.lower() in text for k in keywords)


def read_export(name: str, content: bytes) -> dict:
    """{'kind': 'timecard', 'timecard': ...} | {'kind': 'no_james', ...} |
    {'kind': 'unreadable', 'error': ...}"""
    try:
        rows = timecards.read_rows(name, content)
    except Exception as e:   # a corrupt workbook, a password-protected file
        return {"kind": "unreadable", "error": f"{type(e).__name__}: {e}"}
    if not timecards.is_timecard(rows):
        return {"kind": "unreadable", "error": "no export header"}
    tc = timecards.parse_rows(rows, source=name,
                              timekeeper_id=timecards.JAMES_TIMEKEEPER_ID)
    if not tc["entries"] and tc["skipped_other_timekeepers"]:
        return {"kind": "no_james", "timecard": tc}
    if not tc["entries"]:
        return {"kind": "unreadable", "error": "header but no entry rows"}
    return {"kind": "timecard", "timecard": tc}


EXPECTED_COLUMNS = ("Work Date, Matter, Hours (or HH:MM:SS) and Narrative, in one of the "
                    "first six rows, as Aspen's Time Entries List export has them")


def scan(config: dict, paths: dict, messages: list[dict], get_attachments, reply,
         file_mail, dry_run: bool = False) -> dict:
    """Process one batch of Inbox messages (oldest first). The mail calls are
    passed in, so this runs the same under test:

      get_attachments(message) -> [{"name", "contentBytes"}]
      reply(to, subject, body)
      file_mail(message)        move it to Inbox\\Time Entries
    """
    keywords = config.get("time_entries_keywords") or DEFAULT_KEYWORDS
    rocky_addr = (config.get("rocky_email") or "rocky@gallagherllp.com").lower()
    from outbound import is_allowed_recipient
    counts = {"messages": len(messages), "queued": 0, "duplicates": 0, "replied": 0,
              "ignored": 0, "done": 0}
    cx = None if dry_run else db.connect(paths["db"])
    try:
        for message in messages:
            sender = _sender(message)
            subject = message.get("subject") or ""
            if (not is_allowed_recipient(sender) or sender.lower() == rocky_addr):
                counts["ignored"] += 1
                counts["done"] += 1
                continue
            words = (mentions_time_entries(message, keywords)
                     and SUBJECT_PREFIX.lower() not in subject.lower())
            atts = get_attachments(message)
            if not atts:
                # The Inbox query asked for hasAttachments, so an empty list
                # is a failed download. Stop here; the cursor holds and the
                # next cycle retries this email.
                log.warning(f"[time-entries] mail: attachments for {subject[:60]!r} "
                            f"didn't download — retrying next cycle")
                break
            counts["done"] += 1
            sheets = [a for a in atts
                      if Path(a.get("name") or "").suffix.lower() in SPREADSHEET_EXT
                      and a.get("contentBytes")]
            reads = [(a, read_export(a["name"], a["contentBytes"])) for a in sheets]
            good = [(a, r) for a, r in reads if r["kind"] == "timecard"]
            no_james = [(a, r) for a, r in reads if r["kind"] == "no_james"]

            if not good:
                if not words:
                    counts["ignored"] += 1
                    continue
                if no_james:
                    body = (f"Rocky read {no_james[0][0]['name']}, but it has no rows for "
                            f"James D. Bragdon (timekeeper {timecards.JAMES_TIMEKEEPER_ID}). "
                            f"Rocky drafts narratives only for James's entries, since the "
                            f"style guide and lessons are built on his. Nothing was drafted.")
                elif sheets:
                    why = "; ".join(f"{a['name']}: {r['error']}" for a, r in reads)
                    body = (f"Rocky couldn't read a time-entries export in this email "
                            f"({why}). It looks for {EXPECTED_COLUMNS}. Re-export the "
                            f"Time Entries List from Aspen and send it again.")
                else:
                    body = ("Rocky saw a time-entries email but no .csv or .xlsx attached. "
                            "Attach the Aspen Time Entries List export and send it again.")
                log.info(f"[time-entries] mail: replying to {sender}: {body[:90]}")
                if not dry_run:
                    reply(sender, f"{SUBJECT_PREFIX}: {subject}"[:150], body)
                    file_mail(message)
                counts["replied"] += 1
                continue

            note = note_text(message)
            new_any = False
            dupes = []
            for a, r in good:
                tc = r["timecard"]
                sha = hashlib.sha256(a["contentBytes"]).hexdigest()
                scope = parse_scope(note, tc.get("period"))
                log.info(f"[time-entries] mail: {a['name']} from {sender}: "
                         f"{len(tc['entries'])} rows, {tc.get('period')}"
                         + (f", scope {scope}" if scope else ""))
                if dry_run:
                    counts["queued"] += 1
                    continue
                store = paths["local"] / "snapshots"
                store.mkdir(parents=True, exist_ok=True)
                fpath = store / f"{sha[:12]}{Path(a['name']).suffix.lower()}"
                fpath.write_bytes(a["contentBytes"])
                sid, new = db.ingest_timecard(
                    cx, tc, sha, source=f"mail:{a['name']}", sender=sender,
                    received_at=message.get("receivedDateTime"))
                if not new:
                    dupes.append(a["name"])
                    counts["duplicates"] += 1
                    continue
                db.queue_snapshot(cx, sid, file_path=str(fpath), subject=subject,
                                  internet_message_id=message.get("internetMessageId"),
                                  scope=json.dumps(scope))
                counts["queued"] += 1
                new_any = True
            if dupes and not new_any and not dry_run:
                reply(sender, f"{SUBJECT_PREFIX}: {subject}"[:150],
                      f"Rocky already has this exact export ({', '.join(dupes)}), so there "
                      f"is nothing new to draft. To redraft it, change the export or ask "
                      f"James.")
                counts["replied"] += 1
            if not dry_run:
                file_mail(message)
    finally:
        if cx is not None:
            cx.close()
    return counts


def pending_count(paths: dict) -> int:
    if not paths["db"].exists():
        return 0
    cx = db.connect(paths["db"])
    try:
        return len(db.pending_snapshots(cx))
    finally:
        cx.close()


def spawn_run() -> bool:
    """Start --time-entries-run detached. It unpacks its own onefile copy
    (PYINSTALLER_RESET_ENVIRONMENT) because it outlives this step."""
    import os
    import subprocess
    from rocky import _self_command  # lazy
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    try:
        subprocess.Popen(
            _self_command("--time-entries-run"), env=env, close_fds=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(subprocess.DETACHED_PROCESS
                           | subprocess.CREATE_NEW_PROCESS_GROUP))
        return True
    except Exception as e:
        log.exception(f"[time-entries] could not start the run: {e}")
        return False


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def run_mail_cli(config: dict, data_dir: Path, argv: list[str] | None = None) -> int:
    import sys
    args = sys.argv[1:] if argv is None else argv
    dry_run = "--dry-run" in args
    paths = get_paths(config, data_dir, args)
    mailbox = config.get("rocky_email") or "rocky@gallagherllp.com"
    state_path = paths["local"] / "mail_state.json"
    state = _load_state(state_path)
    since = (datetime.fromisoformat(state["cursor"].replace("Z", "+00:00"))
             if state.get("cursor") else
             datetime.now(timezone.utc) - timedelta(
                 days=int(config.get("time_entries_backfill_days") or BACKFILL_DAYS)))

    import outbound
    import vault
    from rocky import (acquire_app_token, acquire_token, fetch_attachments,  # lazy
                       file_processed_mail, get_msal_app)
    token = acquire_app_token(config)
    messages = vault.fetch_inbox_messages(token, mailbox, since, attachments_only=True)

    def get_attachments(message):
        return fetch_attachments(token, mailbox, message["id"])

    send_token: dict = {}

    def reply(to, subject, body):
        if "t" not in send_token:
            send_token["t"] = acquire_token(get_msal_app(config))
        res = outbound.send_mail_guarded(token=send_token["t"], sender_mailbox=mailbox,
                                         to=[to], subject=subject, body=body)
        if not res.get("sent"):
            log.warning(f"[time-entries] reply to {to} not sent: {res}")

    folders = state.setdefault("mail_folders", {})

    def file_mail(message):
        file_processed_mail(config, mailbox, message["id"],
                            "time_entries_processed_folder", "Time Entries", folders)

    counts = scan(config, paths, messages, get_attachments, reply, file_mail,
                  dry_run=dry_run)
    if not dry_run:
        if counts["done"]:
            state["cursor"] = (messages[counts["done"] - 1].get("receivedDateTime")
                               or state.get("cursor"))
        elif not messages:
            state.setdefault("cursor", since.isoformat())
        paths["local"].mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=1), encoding="utf-8")
    log.info(f"[time-entries] mail: {counts}")
    pending = 0 if dry_run else pending_count(paths)
    if pending:
        log.info(f"[time-entries] {pending} export(s) pending — starting the run")
        spawn_run()
    print(f"time-entries mail: {counts}; pending {pending}")
    return 0
