"""rocky.py --time-entries-run: draft every pending export and email the
workbook (TIME_ENTRIES_DESIGN.md, steps 2-7).

Started detached by the mail step (trigger.py). It works through every
snapshot marked pending, oldest first, and checks for new ones before it
exits, so an export that lands mid-run is not stranded.

For each snapshot:

  1. Mail coverage. The drafter needs James's mail through the morning after
     the last work date, before the batch hour (Monday's for a weekend). If
     Minotaur's brain hasn't reached that yet, the snapshot stays pending and
     the next monitor cycle tries again, for up to time_entries_wait_hours
     (default 12) after the email arrived. Then it runs anyway and says which
     hours were missing.
  2. Correlate, draft and verify (run.draft_entries, as --draft does).
  3. Store the recommendations, write the workbook to the OneDrive
     Time Entries\\drafts folder, and email it from rocky@ to James, with the
     sender cc'd when someone else sent the export.

A failed run marks the snapshot failed and emails James. It is not retried
by itself, since every attempt costs Opus calls:
`--time-entries-run --snapshot N` runs it again.

By hand: `--time-entries-run --csv <export> [--from D --to D] [--no-send]
[--force]` treats a local file as if it had been emailed, which is how to
test the whole path. --no-send prints the email and keeps the workbook in
te.db's folder, off OneDrive.

Learning from the last round and the [TL-####] lessons email are phase 4.
"""

import argparse
import hashlib
import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from . import correlate, db, draft, mail, reference, run, timecards, workbook
from .paths import get_paths
from .trigger import JAMES, SUBJECT_PREFIX

log = logging.getLogger("rocky")

WAIT_HOURS = 12
REVIEW_LINES = 15


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rocky --time-entries-run", add_help=False)
    p.add_argument("--time-entries-run", action="store_true")
    p.add_argument("--snapshot", type=int)
    p.add_argument("--csv")
    p.add_argument("--from", dest="date_from")
    p.add_argument("--to", dest="date_to")
    p.add_argument("--no-send", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="run now even if the brain hasn't caught up")
    p.add_argument("--mail", choices=("minotaur", "corpus"))
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--time-entries-root")
    p.add_argument("--brain-db")
    return p


# =============================================================================
# Mail coverage
# =============================================================================

def _eastern_naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:          # corpus.db stores Eastern wall time
        return dt
    return mail.to_eastern(dt).replace(tzinfo=None)


def needed_through(dates, batch_hour: int) -> datetime:
    """The morning after the last work date (a weekend's Monday's morning
    after), at the batch hour, Eastern."""
    last = date.fromisoformat(max(dates))
    if last.weekday() >= 5:
        last += timedelta(days=7 - last.weekday())
    return datetime.combine(last + timedelta(days=1), time(batch_hour))


def coverage(src, dates, settings: dict) -> dict:
    needed = needed_through(dates, settings.get("window_batch_hour") or 9)
    newest = _eastern_naive(src.newest_sent())
    ingest = _eastern_naive(src.last_ingest())
    seen = max((x for x in (newest, ingest) if x), default=None)
    return {"ok": seen is not None and seen >= needed, "needed": needed,
            "newest_sent": newest, "last_ingest": ingest, "through": seen}


def _hours_since(received_at: str | None, now: datetime) -> float:
    try:
        t = datetime.fromisoformat((received_at or "").replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if t.tzinfo is None:
        t = t.astimezone()         # naive = this machine's local time
    return (now - t).total_seconds() / 3600


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%a %m/%d %I:%M %p").replace(" 0", " ") if dt else "nothing"


# =============================================================================
# The email
# =============================================================================

def _md(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.month}/{d.day}"


MAIL_LABEL = {"minotaur": "Minotaur's email brain",
              "corpus": "the Time Entries folder's corpus.db"}


def results_body(snap, res: dict, work: list[dict], cov: dict, xlsx: Path,
                 copy_note: str, mail_kind: str = "minotaur") -> str:
    recs = res["recs"]
    drafts = [r for r in recs if r["kind"] == "draft"]
    unc = [r for r in recs if r["kind"] == "uncaptured"]
    lo, hi = min(e["work_date"] for e in work), max(e["work_date"] for e in work)

    def tally(c):
        rows = [r for r in drafts if r["confidence"] == c]
        return f"{c} {len(rows)} ({round(sum(r.get('hours') or 0 for r in rows), 1)} h)"

    who = snap["sender"] or ""
    L = [f"Rocky drafted narratives for {len(drafts)} blank or partial entries, "
         f"{_md(lo)} to {_md(hi)}"
         + (f", from the export {who} sent." if who and who.lower() != JAMES else "."),
         "", ", ".join(tally(c) for c in draft.CONFIDENCE),
         f"Uncaptured threads: {len(unc)}, about "
         f"{round(sum(u.get('hours') or 0 for u in unc), 1)} suggested hours.", ""]

    review = [r for r in drafts if r["confidence"] == "REVIEW"]
    if review:
        L.append("REVIEW rows (no reliable basis in the mail):")
        for r in review[:REVIEW_LINES]:
            L.append(f"  {_md(r['work_date'])} {r['matter']} {r.get('hours')} h: "
                     f"{_short((r.get('note') or 'no note').strip())}")
        if len(review) > REVIEW_LINES:
            L.append(f"  ... and {len(review) - REVIEW_LINES} more in the workbook")
        L.append("")

    props = res.get("props") or {}
    unknown: dict[str, float] = {}
    for e in work:
        if e["matter"] and e["matter"] not in props:
            unknown[e["matter"]] = unknown.get(e["matter"], 0) + (e.get("hours") or 0)
    if unknown:
        top = sorted(unknown.items(), key=lambda kv: -kv[1])
        L.append("Matters not in the Property Reference Table (adding them helps the "
                 "matching): " + ", ".join(f"{m} ({round(h, 1)} h)" for m, h in top[:12])
                 + (f", and {len(top) - 12} more" if len(top) > 12 else ""))
    gaps = sorted({f"{r['matter']} (\"{r['zero_hit_token']}\")"
                   for r in res["results"] if r.get("zero_hit_token")})
    if gaps:
        L.append("No email anywhere mentions these matters' key word, which usually "
                 "means a missing alias: " + ", ".join(gaps[:12]))
    errors = [f"{', '.join(o['dates'])}: {e}" for o in res["outs"] for e in o["errors"]]
    if errors:
        L.append("Problems Rocky hit (those rows are REVIEW): " + "; ".join(errors[:8]))
    if unknown or gaps or errors:
        L.append("")

    source = MAIL_LABEL.get(mail_kind, mail_kind)
    if cov["ok"]:
        L.append(f"Mail: your sent mail from {source}, current through "
                 f"{_fmt(cov['through'])}.")
    else:
        L.append(f"Mail: {source} only reached {_fmt(cov['through'])}; Rocky needed "
                 f"{_fmt(cov['needed'])}. Entries on the last day may be missing mail.")
    u = res["usage"]
    L += [f"Run: {u['calls']} calls to {res['model']}, about ${u['cost_usd']}.",
          "", f"The workbook is attached ({xlsx.name}). {copy_note}".rstrip(),
          "Nothing goes into Aspen until you enter it."]
    return "\n".join(L)


def _short(text: str, n: int = 200) -> str:
    if len(text) <= n:
        return text
    return text[:n].rsplit(" ", 1)[0].rstrip(",;:") + " …"


# =============================================================================
# One snapshot
# =============================================================================

def _work(entries: list[dict], scope: dict, settings: dict) -> list[dict]:
    max_narr = 1 if scope.get("blanks_only") else settings["partial_max_chars"]
    return [e for e in entries if correlate.needs_work(e, max_narr)
            and (not scope.get("from") or e["work_date"] >= scope["from"])
            and (not scope.get("to") or e["work_date"] <= scope["to"])]


def _workbook_path(paths: dict, lo: str, hi: str, publish: bool = True) -> tuple[Path, str]:
    """OneDrive Time Entries\\drafts when it's there, else te.db's folder.
    publish=False (a --no-send run) keeps it local."""
    name = f"{lo} to {hi} - draft narratives"
    folders = [(paths["local"] / "drafts", "")]
    if publish:
        folders.insert(0, (paths["drafts"], "A copy is in Time Entries\\drafts on OneDrive."))
    for folder, note in folders:
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        p = folder / f"{name}.xlsx"
        n = 2
        while p.exists():
            p = folder / f"{name} ({n}).xlsx"
            n += 1
        return p, note
    raise OSError("no folder to write the workbook to")


def run_snapshot(config: dict, paths: dict, cx, snap, mail_kind: str, send, now=None,
                 force: bool = False, workers: int = 3, client=None,
                 publish: bool = True) -> str:
    """Returns 'waiting', 'nothing', 'done' or 'failed'."""
    now = now or datetime.now(timezone.utc)
    sid = snap["id"]
    settings = reference.load_settings(paths["settings"])
    scope = json.loads(snap["scope"] or "{}")
    tc = timecards.load_timecard(snap["file_path"],
                                 timekeeper_id=timecards.JAMES_TIMEKEEPER_ID)
    entries = tc["entries"]
    work = _work(entries, scope, settings)
    to = list(config.get("time_entries_results_to") or [JAMES])
    sender = snap["sender"] or ""
    cc = [sender] if sender and sender.lower() not in {t.lower() for t in to} else []
    subject = snap["subject"] or snap["source"] or ""

    if not work:
        db.set_snapshot(cx, sid, status="nothing")
        span = " ".join(f"{k} {v}" for k, v in scope.items()) if scope else ""
        send(to, cc, f"{SUBJECT_PREFIX}: nothing to draft",
             f"Every entry in that export{(' (' + span + ')') if span else ''} already "
             f"has a narrative, so there was nothing to draft. Rocky saved the "
             f"{len(entries)} entries for learning.", [])
        return "nothing"

    src = mail.open_mail(mail_kind, paths)
    try:
        cov = coverage(src, [e["work_date"] for e in work], settings)
    finally:
        src.close()
    waited = _hours_since(snap["received_at"], now)
    wait_hours = float(config.get("time_entries_wait_hours") or WAIT_HOURS)
    if not cov["ok"] and not force and waited < wait_hours:
        msg = (f"waiting for mail through {_fmt(cov['needed'])}; brain reaches "
               f"{_fmt(cov['through'])} ({waited:.1f} of {wait_hours:g} h)")
        log.info(f"[time-entries] snapshot {sid}: {msg}")
        db.set_snapshot(cx, sid, error=msg)
        return "waiting"

    db.set_snapshot(cx, sid, status="running", error=None)
    lo, hi = min(e["work_date"] for e in work), max(e["work_date"] for e in work)
    try:
        res = run.draft_entries(config, paths, entries, work, mail_kind, cx,
                                workers=workers, verify=True, client=client,
                                progress=lambda s: log.info(f"[time-entries] {s}"))
        run_id = db.record_run(cx, "mail-run",
                               {"snapshot": sid, "scope": scope, "mail": mail_kind,
                                "model": res["model"], "effort": res["effort"]},
                               {"started": now.isoformat(timespec="seconds")})
        db.store_recommendations(cx, run_id, res["recs"])
        period = f"{lo} to {hi}"
        rm = run.readme(f"Time entries: {period} - draft narratives (Rocky)", period, res,
                        mail_kind)
        if not cov["ok"]:
            rm["emails"] += (f". The brain reached only {_fmt(cov['through'])}; "
                             f"the drafts needed {_fmt(cov['needed'])}.")
        xlsx, copy_note = _workbook_path(paths, lo, hi, publish)
        workbook.build(xlsx, res["recs"], rm)
        body = results_body(snap, res, work, cov, xlsx, copy_note, mail_kind)
        sent = send(to, cc, f"{SUBJECT_PREFIX}: {_md(lo)} to {_md(hi)} draft narratives",
                    body, [xlsx])
        summary = {"period": period, "entries": len(work), "usage": res["usage"],
                   "workbook": str(xlsx), "sent": sent, "coverage_ok": cov["ok"],
                   "confidence": {c: sum(1 for r in res["recs"] if r["kind"] == "draft"
                                         and r["confidence"] == c)
                                  for c in draft.CONFIDENCE}}
        db.update_run(cx, run_id, summary)
        db.set_snapshot(cx, sid, status="done", run_id=run_id)
        log.info(f"[time-entries] snapshot {sid} done: {summary['confidence']}, "
                 f"${res['usage']['cost_usd']}, {xlsx}")
        return "done"
    except Exception as e:
        log.exception(f"[time-entries] snapshot {sid} failed")
        db.set_snapshot(cx, sid, status="failed", error=f"{type(e).__name__}: {e}"[:500])
        send([JAMES], [], f"{SUBJECT_PREFIX}: run failed",
             f"Rocky couldn't finish drafting the time entries from \"{subject}\" "
             f"({lo} to {hi}): {type(e).__name__}: {e}\n\nNothing was sent to anyone "
             f"else. Details are in rocky.log. To try again, run:\n"
             f"  rocky.exe --time-entries-run --snapshot {sid}", [])
        return "failed"


def run_pending(config: dict, paths: dict, mail_kind: str, send, force: bool = False,
                workers: int = 3, only: int | None = None, client=None, now=None,
                publish: bool = True) -> dict:
    """Every pending snapshot, oldest first; re-checks before exiting."""
    cx = db.connect(paths["db"])
    counts: dict[str, int] = {}
    tried: set[int] = set()
    try:
        while True:
            snaps = [s for s in db.pending_snapshots(cx)
                     if s["id"] not in tried and (only is None or s["id"] == only)]
            if not snaps:
                break
            snap = snaps[0]
            tried.add(snap["id"])
            out = run_snapshot(config, paths, cx, snap, mail_kind, send, now=now,
                               force=force, workers=workers, client=client,
                               publish=publish)
            counts[out] = counts.get(out, 0) + 1
    finally:
        cx.close()
    return counts


def _mail_sender(config: dict):
    """send(to, cc, subject, body, attachments) through the outbound guard."""
    import outbound
    from rocky import acquire_token, get_msal_app  # lazy
    mailbox = config.get("rocky_email") or "rocky@gallagherllp.com"
    tok: dict = {}

    def send(to, cc, subject, body, attachments):
        if "t" not in tok:
            tok["t"] = acquire_token(get_msal_app(config))
        res = outbound.send_mail_guarded(
            token=tok["t"], sender_mailbox=mailbox, to=to, cc=cc, subject=subject,
            body=body, attachments=[{"name": Path(a).name, "path": str(a)}
                                    for a in attachments])
        if not res.get("sent"):
            log.error(f"[time-entries] email not sent ({subject}): {res}")
        return bool(res.get("sent"))
    return send


def _print_sender(to, cc, subject, body, attachments):
    print(f"\n--- would send to {to} cc {cc}: {subject}\n{body}\n"
          + "".join(f"[attached] {a}\n" for a in attachments))
    return False


def run_cli(config: dict, data_dir: Path, argv: list[str] | None = None) -> int:
    import sys
    args = sys.argv[1:] if argv is None else argv
    a, _ = _parser().parse_known_args(args)
    paths = get_paths(config, data_dir, args)
    mail_kind = a.mail or config.get("time_entries_mail_source") or "minotaur"
    only = a.snapshot
    if a.csv:
        p = Path(a.csv)
        p = p if p.is_absolute() else paths["root"] / p
        content = p.read_bytes()
        tc = timecards.load_timecard(p, content=content,
                                     timekeeper_id=timecards.JAMES_TIMEKEEPER_ID)
        cx = db.connect(paths["db"])
        sha = hashlib.sha256(content).hexdigest()
        sid, _new = db.ingest_timecard(cx, tc, sha, source=f"file:{p.name}",
                                       received_at=datetime.now(timezone.utc).isoformat())
        scope = {k: v for k, v in (("from", a.date_from), ("to", a.date_to)) if v}
        db.queue_snapshot(cx, sid, file_path=str(p), subject=p.name,
                          scope=json.dumps(scope))
        cx.close()
        only = sid
        print(f"Snapshot {sid}: {p.name}, {len(tc['entries'])} rows")
    elif a.snapshot:
        cx = db.connect(paths["db"])
        if db.snapshot(cx, a.snapshot) is None:
            print(f"No snapshot {a.snapshot}")
            return 2
        db.set_snapshot(cx, a.snapshot, status="pending")
        cx.close()
    send = _print_sender if a.no_send else _mail_sender(config)
    counts = run_pending(config, paths, mail_kind, send, force=a.force,
                         workers=a.workers, only=only, publish=not a.no_send)
    print(f"time-entries run: {counts or 'nothing pending'}")
    return 0
