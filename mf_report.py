"""
MF Case Brain — the daily report
=================================

    python rocky.py --mf-brain --report [--dry-run] [--force]

The docket as of this morning, read from the brain's ledger and LLT
Watch's change log: court dates coming up, court dates that passed with
no outcome on the sheet, what was edited on the spreadsheet since the
last report, ripe matters nobody has filed, new matters, and the matters
that got mail but have no system of record anywhere.

**Different from the 7 AM learning email (`mf_digest`).** That one is
the brain talking about itself (its guesses, its scorecard, questions
with answer boxes) and is meant for James alone, since every recipient
sees the boxes. This one is a status report about the cases and could go
to staff. It is the static first cut of the staff dashboard's panels
(MF_CASE_BRAIN.md), minus the task list, which is still blocked on
Notes.Read.

**Zero Claude tokens.** Every line is arithmetic over the ledger and is
template-rendered here, the same as LLT Watch.

**One scheduled task, not two.** `--mf-brain --digest` writes this
report right after its own email, so the report always reads a ledger
the scan has just brought current. `--report` alone re-renders from the
ledger as it stands, for an off-cycle copy.

Output: an HTML file on the share,
``<mf_tracker_root>\\Daily Reports\\YYYY-MM-DD.html``, and an email from
rocky@ to ``mf_report_recipients`` (default James). Weekends are skipped
unless ``mf_report_weekends`` is true, and Monday's report covers
everything since Friday's because the window runs from the last report
rather than a fixed 24 hours.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path

log = logging.getLogger("rocky.mfreport")

SUBJECT_PREFIX = "Multifamily Daily Report"
REPORTS_SUBDIR = "Daily Reports"

# Look-ahead for the court calendar, and look-back for a court date that
# passed without the sheet recording what happened.
CALENDAR_DAYS = 14
OUTCOME_LOOKBACK_DAYS = 14
# "Newly ripe" and "ripening soon" windows, either side of today.
RIPE_WINDOW_DAYS = 7
# A missed report (the laptop was off) should not turn the next one into
# a month of sheet edits. Older changes are still in LLT Watch's log.
MAX_WINDOW_DAYS = 4
# Properties shown in the ripe backlog table; the rest are summed.
BACKLOG_ROWS = 15

# Cases whose mail activity is NOT "untracked": evictions live on the
# sheet, insured/monitored claims live on litigation_updater's Smartsheet.
_TRACKED_TYPES = {"eviction", "insured_litigation"}

_MUTED = "color:#666;"
_F = "font-family:Segoe UI,Arial,sans-serif;"
_TD = "padding:3px 8px;border-bottom:1px solid #eee;vertical-align:top;"
_TH = ("padding:4px 8px;border-bottom:2px solid #ccc;text-align:left;"
       "font-weight:600;")


# =============================================================================
# The window
# =============================================================================

def report_window(state: dict, now: datetime) -> datetime:
    """Start of the period this report covers: the last report, capped
    at MAX_WINDOW_DAYS back; 24 hours on the first run."""
    floor = now - timedelta(days=MAX_WINDOW_DAYS)
    try:
        last = datetime.fromisoformat(
            (state.get("last_report") or "").replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except ValueError:
        return now - timedelta(days=1)
    return max(last, floor)


def _ts(iso: str | None) -> datetime | None:
    try:
        dt = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# =============================================================================
# Sections (pure functions over the ledger, for testing)
# =============================================================================

def _open_lines(lines: dict) -> list[dict]:
    return [l for l in lines.values() if not l.get("closed")]


def _label(cases: dict, l: dict) -> str:
    c = cases.get(l.get("case")) or {}
    return str(c.get("label") or l.get("case") or "?")


def _property(cases: dict, l: dict) -> str:
    c = cases.get(l.get("case")) or {}
    return str(c.get("property") or "(no property)")


def court_dates(next_steps: str) -> list[tuple[date, str]]:
    """Every court date in a Next Steps cell, in order. A cell often
    carries two ("Mediation: 9.10.26 ...; Bell Hearing: 10.8.26"), and
    mf_brain.parse_court_date returns only the first."""
    import mf_brain
    out = []
    for m in mf_brain._COURT_DATE_RE.finditer(next_steps or ""):
        mo, d, y = int(m.group(2)), int(m.group(3)), int(m.group(4))
        y = y + 2000 if y < 100 else y
        try:
            out.append((date(y, mo, d), m.group(1).title()))
        except ValueError:
            continue
    return out


# Words that mean the cell already says how a past date went (or that it
# moved), so a passed date is not an unrecorded outcome.
_OUTCOME_RE = re.compile(
    r"(?i)(dismiss|settle|agreement|continu|judgment|awarded|non-?suit|"
    r"consent|resolved|default|possession granted|granted|vacated|stayed|"
    r"withdrawn|postponed|reschedul|cancel)")


def court_calendar(lines: dict, cases: dict, today: date,
                   days: int = CALENDAR_DAYS) -> list[dict]:
    """Court dates from today through today+days, soonest first."""
    out = []
    for l in _open_lines(lines):
        for cd, event in court_dates(l.get("next_steps", "")):
            if today <= cd <= today + timedelta(days=days):
                out.append({"date": cd, "event": event,
                            "label": _label(cases, l),
                            "status": l.get("status", ""),
                            "line": l["line_id"]})
    return sorted(out, key=lambda r: (r["date"], r["label"]))


def outcomes_missing(lines: dict, cases: dict, today: date,
                     days: int = OUTCOME_LOOKBACK_DAYS) -> list[dict]:
    """A court date in the last `days` days, with no later date in the
    cell and no outcome written: the hearing happened (or didn't) and
    the sheet hasn't said."""
    out = []
    for l in _open_lines(lines):
        steps = l.get("next_steps", "")
        dates = court_dates(steps)
        if not dates or _OUTCOME_RE.search(steps):
            continue
        last, event = max(dates)
        if today - timedelta(days=days) <= last < today:
            out.append({"date": last, "event": event,
                        "label": _label(cases, l),
                        "status": l.get("status", ""), "line": l["line_id"],
                        "days": (today - last).days})
    return sorted(out, key=lambda r: (r["date"], r["label"]))


def ripe_unfiled(lines: dict, cases: dict, today: date,
                 window: int = RIPE_WINDOW_DAYS) -> dict:
    """Unfiled matters by ripe date: newly ripe (last `window` days),
    ripening soon (next `window` days), and the older backlog counted
    per property."""
    import mf_brain
    newly, soon, older = [], [], {}
    for l in _open_lines(lines):
        case = cases.get(l.get("case")) or {}
        if case.get("matter_type") == "insured_litigation":
            continue
        if mf_brain.looks_filed(l.get("status", ""), l.get("next_steps", "")):
            continue
        rd = mf_brain.parse_ripe(l.get("ripe_date", ""))
        if not rd:
            continue
        row = {"date": rd, "label": _label(cases, l),
               "status": l.get("status", ""), "line": l["line_id"]}
        if today - timedelta(days=window) <= rd <= today:
            newly.append(row)
        elif today < rd <= today + timedelta(days=window):
            soon.append(row)
        elif rd < today - timedelta(days=window):
            prop = " ".join(_property(cases, l).split())
            # Group case-insensitively: the sheet has "City Ridge" and
            # "City ridge " as the same property.
            b = older.setdefault(prop.casefold(),
                                 {"name": prop, "n": 0, "oldest": rd})
            b["n"] += 1
            b["oldest"] = min(b["oldest"], rd)
    key = lambda r: (r["date"], r["label"])  # noqa: E731
    return {"newly": sorted(newly, key=key), "soon": sorted(soon, key=key),
            "older": dict(sorted(older.items(),
                                 key=lambda kv: (-kv[1]["n"], kv[0])))}


def new_matters(cases: dict, since: datetime) -> list[dict]:
    """Cases the brain registered during the window."""
    out = []
    for c in cases.values():
        created = _ts(c.get("created"))
        if created and created >= since:
            out.append(c)
    return sorted(out, key=lambda c: (c.get("matter_type") or "",
                                      c.get("label") or ""))


def untracked_activity(cases: dict, lines: dict, obs: list[dict],
                       since: datetime) -> list[dict]:
    """Matters that got mail in the window and are on no system of
    record: not on the LLT sheet and not a Smartsheet claim. These are
    the 164-style matters only the email folder knows about."""
    on_sheet = {l.get("case") for l in _open_lines(lines)}
    by_case: dict[str, dict] = {}
    for o in obs:
        if o.get("source") != "inbox":
            continue
        t = _ts(o.get("ts"))
        if not t or t < since:
            continue
        cid = o.get("case")
        c = cases.get(cid)
        if not c or cid in on_sheet or c.get("matter_type") in _TRACKED_TYPES:
            continue
        ev = o.get("evidence") or {}
        rec = by_case.setdefault(cid, {
            "case": cid, "label": c.get("label") or cid,
            "matter_type": c.get("matter_type") or "other",
            "messages": 0, "summary": ""})
        rec["messages"] = max(rec["messages"], int(ev.get("messages") or 0))
        if ev.get("summary"):
            rec["summary"] = str(ev["summary"])
    return sorted(by_case.values(), key=lambda r: (-r["messages"], r["label"]))


# =============================================================================
# Rendering
# =============================================================================

def _fd(d: date) -> str:
    import mf_brain
    return mf_brain.fmt_date(d)


def _dow(d: date) -> str:
    return d.strftime("%a")


def _h(title: str, blurb: str = "", n: int | None = None) -> str:
    suffix = f" ({n})" if n is not None else ""
    out = (f"<h2 style='margin:22px 0 2px 0;font-size:16px;'>"
           f"{escape(title)}{suffix}</h2>")
    if blurb:
        out += (f"<p style='margin:0 0 6px 0;font-size:12px;{_MUTED}'>"
                f"{escape(blurb)}</p>")
    return out


def _none(text: str) -> str:
    return f"<p style='margin:2px 0;font-size:13px;{_MUTED}'>{escape(text)}</p>"


def _table(head: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th style='{_TH}'>{escape(h)}</th>" for h in head)
    body = "".join(
        "<tr>" + "".join(f"<td style='{_TD}'>{c}</td>" for c in r) + "</tr>"
        for r in rows)
    return (f"<table style='border-collapse:collapse;font-size:13px;"
            f"margin:4px 0;'><tr>{th}</tr>{body}</table>")


def _ul(items: list[str]) -> str:
    lis = "".join(f"<li style='margin:3px 0;'>{i}</li>" for i in items)
    return f"<ul style='margin:0;padding-left:20px;font-size:13px;'>{lis}</ul>"


def _calendar_html(cal: list[dict]) -> str:
    out = _h("Court calendar",
             f"Hearings, trials and writs in the next {CALENDAR_DAYS} days, "
             f"from the sheet's Next Steps.", len(cal))
    if not cal:
        return out + _none("Nothing on the calendar.")
    rows = [[f"{_dow(r['date'])} {_fd(r['date'])}", escape(r["event"]),
             escape(r["label"]), escape(r["status"])] for r in cal]
    return out + _table(["Date", "Event", "Matter", "Status"], rows)


def _outcomes_html(miss: list[dict]) -> str:
    if not miss:
        return ""
    out = _h("Outcome not on the sheet",
             "The latest court date in Next Steps has passed and nothing "
             "says how it went. Either the outcome hasn't been entered or "
             "the date moved.", len(miss))
    rows = [[f"{_fd(r['date'])} ({r['days']}d ago)", escape(r["event"]),
             escape(r["label"]), escape(r["status"])] for r in miss]
    return out + _table(["Was set for", "Event", "Matter", "Status"], rows)


def _changes_html(changes: list[dict], since: datetime,
                  watch_running: bool) -> str:
    import llt_watch
    items = llt_watch.digest_items(changes)
    local = since.astimezone()
    blurb = (f"Hand edits LLT Watch recorded since "
             f"{local.strftime('%a')} {_fd(local.date())} "
             f"{local.strftime('%I:%M %p').lstrip('0')}.")
    out = _h("Spreadsheet changes", blurb, len(items))
    if not watch_running:
        return out + _none("LLT Watch hasn't recorded anything yet. It "
                           "needs its hourly task on the Rocky laptop.")
    if not items:
        return (out + _none("No rows changed.")
                + llt_watch.editor_trailer(changes))
    return out + _ul(items) + llt_watch.editor_trailer(changes)


def _ripe_html(r: dict) -> str:
    n_older = sum(b["n"] for b in r["older"].values())
    out = _h("Ripe and not filed",
             "Matters past their ripe date with no filing on the sheet.",
             len(r["newly"]) + n_older)
    if r["newly"]:
        out += (f"<p style='margin:6px 0 2px 0;font-size:13px;'>"
                f"<b>Ripened in the last {RIPE_WINDOW_DAYS} days</b></p>")
        out += _table(["Ripe", "Matter", "Status"],
                      [[_fd(x["date"]), escape(x["label"]),
                        escape(x["status"])] for x in r["newly"]])
    if r["soon"]:
        out += (f"<p style='margin:10px 0 2px 0;font-size:13px;'>"
                f"<b>Ripening in the next {RIPE_WINDOW_DAYS} days</b></p>")
        out += _table(["Ripe", "Matter", "Status"],
                      [[_fd(x["date"]), escape(x["label"]),
                        escape(x["status"])] for x in r["soon"]])
    if r["older"]:
        out += (f"<p style='margin:10px 0 2px 0;font-size:13px;'>"
                f"<b>Older backlog by property</b> "
                f"<span style='{_MUTED}'>({n_older})</span></p>")
        top = list(r["older"].values())
        out += _table(["Property", "Matters", "Oldest ripe"],
                      [[escape(b["name"]), str(b["n"]), _fd(b["oldest"])]
                       for b in top[:BACKLOG_ROWS]])
        if len(top) > BACKLOG_ROWS:
            rest = top[BACKLOG_ROWS:]
            out += _none(f"…and {sum(b['n'] for b in rest)} more across "
                         f"{len(rest)} other properties.")
    if not (r["newly"] or r["soon"] or r["older"]):
        out += _none("Nothing ripe and unfiled.")
    return out


_TYPE_LABEL = {
    "eviction": "Eviction", "agency_complaint": "Agency complaint",
    "demand": "Demand", "lease_exit": "Lease exit",
    "discrimination": "Discrimination", "habitability": "Habitability",
    "incident": "Incident", "bankruptcy": "Bankruptcy",
    "insured_litigation": "Insured/monitored", "other": "Other",
}


def _new_html(new: list[dict]) -> str:
    out = _h("New matters", "Cases the brain saw for the first time, from "
             "the sheet or a new mail folder.", len(new))
    if not new:
        return out + _none("None.")
    rows = [[escape(_TYPE_LABEL.get(c.get("matter_type"), "Other")),
             escape(str(c.get("label") or c.get("id"))),
             escape("spreadsheet" if c.get("first_seen_by") == "llt_sheet"
                    else "mail folder")] for c in new[:60]]
    tail = (_none(f"…and {len(new) - 60} more.") if len(new) > 60 else "")
    return out + _table(["Type", "Matter", "Seen first in"], rows) + tail


def _untracked_html(act: list[dict]) -> str:
    out = _h("Mail on matters with no system of record",
             "Not on the LLT sheet and not a Smartsheet claim. The mail "
             "folder is the only record these exist.", len(act))
    if not act:
        return out + _none("None in this window.")
    rows = [[escape(_TYPE_LABEL.get(a["matter_type"], "Other")),
             escape(a["label"]), str(a["messages"] or ""),
             escape(a["summary"][:300])] for a in act]
    return out + _table(["Type", "Matter", "Emails", "What it's about"], rows)


def build_report(config: dict, paths: dict, llt_paths: dict, *,
                 since: datetime, now: datetime | None = None) -> dict:
    """Assemble the report. Returns {"html", "subject", "counts"}."""
    import llt_watch
    import mf_brain

    now = now or datetime.now(timezone.utc)
    today = now.astimezone().date()
    cases = mf_brain.scoped_cases(paths, mf_brain.load_cases(paths))
    lines = mf_brain.load_lines(paths)
    obs = mf_brain.read_jsonl(paths["observations"])
    state = mf_brain.load_state(paths)

    cal = court_calendar(lines, cases, today)
    miss = outcomes_missing(lines, cases, today)
    ripe = ripe_unfiled(lines, cases, today)
    new = new_matters(cases, since)
    act = untracked_activity(cases, lines, obs, since)
    changes = llt_watch.changes_since(llt_paths, since)
    watch_running = llt_paths["snapshot"].exists()

    this_week = sum(1 for r in cal if r["date"] <= today + timedelta(days=7))
    counts = {
        "open_lines": len(_open_lines(lines)),
        "court_7d": this_week, "court_14d": len(cal),
        "outcomes_missing": len(miss),
        "sheet_changes": len(llt_watch.digest_items(changes)),
        "newly_ripe": len(ripe["newly"]), "ripe_soon": len(ripe["soon"]),
        "ripe_backlog": sum(b["n"] for b in ripe["older"].values()),
        "new_matters": len(new), "untracked_activity": len(act),
    }

    headline = (
        f"<b>{counts['court_7d']}</b> court date(s) this week · "
        f"<b>{counts['sheet_changes']}</b> sheet change(s) · "
        f"<b>{counts['newly_ripe']}</b> newly ripe · "
        f"<b>{counts['new_matters']}</b> new matter(s)")
    last_scan = _ts(state.get("last_scan"))
    stale = (last_scan is None
             or now - last_scan > timedelta(hours=36))
    scan_note = ("the brain has never scanned" if last_scan is None else
                 f"ledger as of {last_scan.astimezone():%a} "
                 f"{_fd(last_scan.astimezone().date())} "
                 f"{last_scan.astimezone().strftime('%I:%M %p').lstrip('0')}")
    warn = (f"<p style='margin:6px 0;padding:6px 10px;background:#fff4e5;"
            f"border-left:3px solid #e8a33d;font-size:13px;'>"
            f"Out of date: {escape(scan_note)}. The 7:00 AM MF Brain Digest "
            f"task may not be running.</p>") if stale else ""

    title_day = now.astimezone().strftime("%A, %B %d, %Y").replace(" 0", " ")
    body = [
        f"<div style='{_F}color:#222;max-width:900px;'>",
        f"<h2 style='margin:0 0 2px 0;'>Multifamily Daily Report — "
        f"{escape(title_day)}</h2>",
        f"<p style='margin:0 0 8px 0;font-size:13px;'>{headline}</p>",
        warn,
        _calendar_html(cal),
        _outcomes_html(miss),
        _changes_html(changes, since, watch_running),
        _ripe_html(ripe),
        _new_html(new),
        _untracked_html(act),
        f"<p style='margin:22px 0 0 0;font-size:12px;color:#999;'>"
        f"Rocky, from the PENDING LLT MATTERS sheet, James's matter folders "
        f"and LLT Watch; {escape(scan_note)}; "
        f"{counts['open_lines']:,} open matter lines. Read-only: nothing "
        f"here was changed anywhere. Court dates are read from Next Steps "
        f"text, so a date written oddly can be missed.</p></div>",
    ]
    subject = (f"{SUBJECT_PREFIX} — "
               f"{now.astimezone():%B %d, %Y}").replace(" 0", " ")
    return {"html": "\n".join(p for p in body if p), "subject": subject,
            "counts": counts}


# =============================================================================
# Writing and sending
# =============================================================================

def write_report(paths: dict, html: str, subject: str,
                 now: datetime) -> Path:
    """Save to <tracker_root>\\Daily Reports\\YYYY-MM-DD.html (atomic; a
    re-run the same day replaces that day's file)."""
    out_dir = paths["root"].parent / REPORTS_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{now.astimezone():%Y-%m-%d}.html"
    doc = (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
           f"<title>{escape(subject)}</title></head>"
           f"<body style='margin:24px;'>{html}</body></html>")
    tmp = out.with_suffix(".html.tmp")
    tmp.write_text(doc, encoding="utf-8")
    os.replace(tmp, out)
    return out


def run_report(config: dict, paths: dict, data_dir: Path, *,
               token: str | None, dry_run: bool = False,
               force: bool = False, now: datetime | None = None) -> dict:
    """Build, save and email the report. Never raises: a broken report
    must not take the digest run that called it down with it."""
    import llt_watch
    import mf_brain

    now = now or datetime.now(timezone.utc)
    if not force and not config.get("mf_report_weekends", False) \
            and now.astimezone().weekday() >= 5:
        log.info("[mf-report] weekend — skipped (Monday's report covers it)")
        return {"skipped": "weekend"}
    if config.get("mf_report_enabled", True) is False and not force:
        log.info("[mf-report] disabled (mf_report_enabled false)")
        return {"skipped": "disabled"}

    try:
        state = mf_brain.load_state(paths)
        since = report_window(state, now)
        llt_paths = llt_watch.get_paths(config, data_dir)
        rep = build_report(config, paths, llt_paths, since=since, now=now)
    except Exception as e:
        log.exception(f"[mf-report] build failed: {e}")
        return {"error": str(e)}

    c = rep["counts"]
    log.info(f"[mf-report] {c['court_7d']} court this week, "
             f"{c['outcomes_missing']} outcome(s) missing, "
             f"{c['sheet_changes']} sheet change(s), {c['newly_ripe']} newly "
             f"ripe, {c['new_matters']} new, {c['untracked_activity']} "
             f"untracked with mail")
    if dry_run:
        log.info(f"[mf-report] DRY-RUN — would save and send "
                 f"{rep['subject']!r}")
        return dict(rep, dry_run=True)

    try:
        path = write_report(paths, rep["html"], rep["subject"], now)
        log.info(f"[mf-report] saved {path}")
    except OSError as e:
        log.warning(f"[mf-report] could not save the report file: {e}")
        path = None

    sent = False
    to = config.get("mf_report_recipients") or [
        config.get("user_email", "jbragdon@gallagherllp.com")]
    if token:
        from outbound import send_mail_guarded
        result = send_mail_guarded(
            token, config.get("rocky_email", "rocky@gallagherllp.com"),
            to, rep["subject"], rep["html"], body_type="HTML")
        sent = bool(result.get("sent"))
        if sent:
            log.info(f"[mf-report] sent to {to}")
        else:
            log.warning(f"[mf-report] send FAILED: {result.get('reason')}")
    else:
        log.info("[mf-report] no token — saved only, not emailed")

    # Advance the window only when the report reached someone, so a
    # failed send (or a --no-email copy) doesn't swallow a day of changes
    # from the next emailed one.
    if sent:
        state = mf_brain.load_state(paths)
        state["last_report"] = now.isoformat()
        mf_brain.save_state(paths, state)
    return dict(rep, path=str(path) if path else None, sent=sent)


def run_cli(config: dict, data_dir: Path) -> None:
    """--mf-brain --report: re-render from the ledger as it stands."""
    import mf_brain
    paths = mf_brain.get_paths(config, data_dir)
    dry_run = "--dry-run" in sys.argv
    token = None
    if "--no-email" not in sys.argv:
        from rocky import acquire_token, audit_token_scopes, get_msal_app
        token = acquire_token(get_msal_app(config))
        audit_token_scopes(token)
    rep = run_report(config, paths, data_dir, token=token, dry_run=dry_run,
                     force="--force" in sys.argv)
    if dry_run and rep.get("html"):
        sys.stdout.buffer.write(rep["html"].encode("utf-8") + b"\n")
