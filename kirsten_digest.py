"""
Kirsten Digest — the weekly Gallagher-Bozzuto litigation report
===============================================================

    python rocky.py --kirsten-digest [--days N] [--dry-run] [--force]
                                     [RRID-XXXX ...]          (Sunday 6:00 PM)

Internally "the Kirsten Digest"; externally (the email itself) "the Weekly
Gallagher-Bozzuto Litigation Report". Kirsten Woelper at Bozzuto is the
client. One DRAFT in James's Drafts folder, To Kirsten, cc
scowin@gallagherllp.com and jdugan@gallagherllp.com. James reviews and
sends: Rocky never sends this email.

Which cases: every row of the Rocky Case Index whose "Kirsten Digest?"
column (any header containing "kirsten") says yes. Rows on the Closed
sheet are skipped. Bare RRIDs on the command line restrict the run to those
cases (still only flagged ones), for testing one card.

What each case card says, from that case folder's activity.jsonl over the
last N days (default 7), with the Case Status Memorandum and CLAUDE.md
overview as background for posture and dates:

    STATUS          one line on where the case stands
    WEEK IN REVIEW  the substantive developments (none = "No significant
                    developments this week.")
    NEXT STEPS      what comes next, and anything needed from Bozzuto

One Claude call per case. The audience is the client, so the prompt keeps
out internal case-file housekeeping, Rocky, and the activity log itself.
A case whose call fails still gets a card, with a red fill-in-before-
sending note, so James can't miss it.

Layout follows the Maple digest (header bar, title, summary strip, one card
per case, footer). Colors live in THEME below.

A copy of each report is saved to <cases_root>\\_Kirsten Digests\\
YYYY-MM-DD.html. state\\kirsten_digest.json records the last drafted date,
so a second run the same day drafts nothing unless --force.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

log = logging.getLogger("rocky.kirsten")

# Draft addressing. External To is fine HERE: this only goes on a draft in
# James's mailbox, which he sends himself; the outbound allowlist still
# governs everything Rocky sends. Override with config kirsten_digest_to /
# kirsten_digest_cc / kirsten_digest_mailbox.
_DEFAULT_TO = ["kwoelper@bozzuto.com"]           # Kirsten Woelper
_DEFAULT_CC = ["scowin@gallagherllp.com", "jdugan@gallagherllp.com"]
_DEFAULT_MAILBOX = "jbragdon@gallagherllp.com"

REPORT_NAME = "Weekly Gallagher-Bozzuto Litigation Report"
ARCHIVE_DIRNAME = "_Kirsten Digests"
_YES = {"yes", "y", "true", "x", "1"}

# Placeholder palette (Gallagher navy) until James sends the theming.
THEME = {
    "page_bg": "#e9edf3",
    "header_bg": "#1f3557",
    "header_text": "#ffffff",
    "header_sub": "#c9d4e5",
    "title": "#1f2933",
    "muted": "#637083",
    "strip_bg": "#eef2f8",
    "strip_text": "#1f3557",
    "card_border": "#cfd8e3",
    "card_title": "#1f3557",
    "rule": "#e3e9f1",
    "label": "#b07d2b",       # section labels (WEEK IN REVIEW / NEXT STEPS)
    "body": "#1f2933",
    "warn": "#b42318",
}
_FONT = "Segoe UI,Helvetica,Arial,sans-serif"

SYSTEM_PROMPT = """You are drafting one case's section of the Weekly Gallagher-Bozzuto Litigation Report, a weekly email from Gallagher LLP (outside litigation counsel) to Kirsten Woelper, the in-house contact at Bozzuto who oversees these cases. James Bragdon, the Gallagher attorney, reviews it and sends it from his own account.

You receive the case's identity, the activity recorded on the case over the past week, and background: the case's status memorandum and case overview, when they exist. Activity may include emails received (with summaries), documents received or filed, drafting, research, calls, and the firm's own notes.

Return ONLY a JSON object with these keys:
{
  "status": "<one sentence, at most 25 words: where the case stands now (court or forum, stage, the next key date if one is known)>",
  "week": [{"date": "<YYYY-MM-DD the development itself happened, or null if unknown>", "text": "<bullet>"}, ...],
  "next_steps": ["<bullet>", ...]
}

"week" — WEEK IN REVIEW
- The substantive developments of the past week only: filings and orders, hearings held, discovery served or received, settlement communications, deadlines set or moved, significant correspondence with opposing counsel, the court, or an agency, and significant work Gallagher completed (a motion drafted, research on a dispositive issue).
- Only developments that happened on or after WEEK START. Activity lines carry the day they were recorded, not the day the event happened: a line recorded this week can describe a filing, hearing, or email from before WEEK START (for example, the filed copy of an earlier motion reaching the file). Check the event's own date in the text. Anything dated before WEEK START is not week-in-review; mention it in "status" if it matters.
- 0 to 5 bullets, one development per bullet, one or two sentences each, about 40 words at most. Give the facts that matter (dates, parties, amounts). Group related items. No bullet that only elaborates or restates the one before it.
- If nothing substantive happened, return an empty list. Do not pad a quiet week.

"next_steps" — NEXT STEPS
- 1 to 4 bullets in strict date order, soonest first (undated items last): what happens next and who does it. Write each as what we will do ("We will...", "Gallagher is preparing...") or what is expected of others, never as a bare internal to-do ("Return X's call", "Confirm whether..."). Lead with any upcoming date (e.g. "Nov. 4 — opposition due; Gallagher is preparing it."). Call out plainly anything Gallagher needs from Bozzuto (documents, a decision, settlement authority, a witness).
- Never list a date that is before TODAY as upcoming. A past hearing or deadline is done.
- If nothing is pending, return ["No action needed at this time; we will report any developments."]

AUDIENCE — the client. Write for an experienced in-house reader: plain, direct, professional. Refer to the firm as "we" or "Gallagher" and to the client as "Bozzuto". The reader is Kirsten herself: never name her in the third person. Write her own actions as "you" ("you coordinated with leadership on...") or as Bozzuto's.
- Never mention Rocky, AI, activity logs, case folders, file indexes, memoranda being updated, documents being saved, renamed, filed into folders, or any other internal file housekeeping.
- Leave out internal-only matters entirely, in every field: Gallagher's billing, invoices, e-billing platforms (a co-party or carrier changing how it receives our invoices is internal), fees, staffing and workload, internal to-dos about the file, and candid notes about the client's own personnel.
- Do not invent facts, dates, or outcomes. Use only what the sources say.
- A contemplated, threatened, or conditional event (a notice of contemplated dismissal, a motion drafted but not filed, a "will be dismissed unless") is pending, not done. Never say an order was entered or a motion granted unless a source says so. If a conditional deadline has passed with no confirmed result, say the result is unconfirmed.
- No emojis, no headings, no markdown inside the strings. Past tense for events.

Output the JSON object and nothing else."""

# Event types that are pure file housekeeping. They never reach the prompt
# (on top of rocky._is_substantive_event, which drops scheduled no-ops).
_HOUSEKEEPING = {
    "document_indexed", "document_parked_unfiled", "document_discarded",
    "file_updated", "file_maintenance", "file_organization",
    "file_reorganization", "file_cleanup", "file_rename", "file_error",
    "case_file_maintenance", "case_file_organization", "case_organization",
    "case_maintenance", "index_update", "index_built", "spine_created",
    "spine_updated", "instructions_updated", "internal_suggestions",
    "folder_setup", "tooling", "skill_created", "session_log",
}
_PROMPT_CAP = 20000      # activity characters per case
_LINE_CAP = 700


# ---------------------------------------------------------------------------
# Case selection
# ---------------------------------------------------------------------------

def kirsten_column(cases: list[dict]) -> str | None:
    """The case-index header that holds the Kirsten Digest? flag."""
    for case in cases:
        for key in case:
            if key and "kirsten" in str(key).lower():
                return key
    return None


def select_cases(cases: list[dict], rrids: list[str] | None = None
                 ) -> list[dict]:
    """Flagged, open cases, de-duplicated by RRID (last row wins, matching
    how the rest of Rocky keys the index)."""
    col = kirsten_column(cases)
    if not col:
        return []
    by_rrid: dict[str, dict] = {}
    for case in cases:
        rrid = str(case.get("RRID#") or "").strip().upper()
        if not rrid:
            continue
        if str(case.get(col) or "").strip().lower() not in _YES:
            by_rrid.pop(rrid, None)
            continue
        if str(case.get("Open/Closed") or "").strip().lower() == "closed":
            by_rrid.pop(rrid, None)
            continue
        by_rrid[rrid] = case
    wanted = {r.upper() for r in rrids or []}
    return [c for r, c in sorted(by_rrid.items())
            if not wanted or r in wanted]


def _name_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").split("(")[0].lower())


def find_folder(cases_root: Path, case: dict) -> Path | None:
    """The case folder for an index row. Folder names carry the RRID, but a
    few RRIDs are shared by more than one folder (or are off by one from the
    index), so the file name breaks ties and is the fallback."""
    rrid = str(case.get("RRID#") or "").upper()
    name = _name_key(str(case.get("File Name") or ""))
    try:
        dirs = [p for p in cases_root.iterdir()
                if p.is_dir() and not p.name.startswith("_")]
    except OSError as e:
        log.warning(f"[kirsten] could not scan {cases_root}: {e}")
        return None
    by_rrid = [p for p in dirs if rrid and rrid in p.name.upper()]
    if len(by_rrid) == 1:
        return by_rrid[0]
    pool = by_rrid or dirs
    if name:
        named = [p for p in pool if _name_key(p.name).startswith(name)
                 or name.startswith(_name_key(p.name))]
        if named:
            return named[0]
    return by_rrid[0] if by_rrid else None


# ---------------------------------------------------------------------------
# Prompt material
# ---------------------------------------------------------------------------

def _clip(text, cap: int = _LINE_CAP) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= cap else text[:cap] + "..."


def _local_day(ts: str | None) -> str:
    try:
        dt = datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.astimezone()
        return dt.strftime("%a %m/%d")
    except ValueError:
        return "?"


def activity_lines(events: list[dict]) -> list[str]:
    """Compact, dated lines for the prompt. Housekeeping never appears."""
    from rocky import _is_substantive_event
    lines: list[str] = []
    for ev in sorted(events, key=lambda e: str(e.get("timestamp") or "")):
        kind = ev.get("event", "")
        if kind in _HOUSEKEEPING or not _is_substantive_event(ev):
            continue
        day = _local_day(ev.get("timestamp"))
        if kind == "daily_cases_email_summary":
            for em in ev.get("emails") or []:
                line = (f"{day} email from {em.get('from_name', '?')}: "
                        f"{em.get('one_line') or em.get('subject', '?')}")
                for dl in em.get("deadlines") or []:
                    line += f" [deadline {dl.get('date')}: {dl.get('description')}]"
                lines.append(_clip(line))
            if not ev.get("emails") and ev.get("summary"):
                lines.append(f"{day} emails: {_clip(ev['summary'])}")
            for item in ev.get("action_items") or []:
                lines.append(f"{day}   action item: {_clip(item, 300)}")
        elif kind == "email_ingested":
            lines.append(_clip(f"{day} email received: {ev.get('subject')!r} "
                               f"from {ev.get('from_name') or ev.get('from_address')}"))
        elif kind == "document_filed":
            target = Path(str(ev.get("target_path") or "")).name
            lines.append(_clip(f"{day} document received: {target} — "
                               f"{ev.get('summary') or ''}"))
        elif kind == "daily_run":
            if ev.get("analysis"):
                lines.append(f"{day} review of new documents: "
                             f"{_clip(ev['analysis'])}")
            for rec in ev.get("recommendations") or []:
                lines.append(f"{day}   suggested next step: {_clip(rec, 300)}")
        else:
            text = ev.get("summary") or ""
            details = ev.get("details")
            if isinstance(details, str) and details:
                text = f"{text} ({details})"
            if text:
                lines.append(_clip(f"{day} {kind.replace('_', ' ')}: {text}"))
    # Keep the newest material when a busy week runs over the cap.
    out, total = [], 0
    for line in reversed(lines):
        total += len(line) + 1
        if total > _PROMPT_CAP:
            out.append("[...earlier activity omitted...]")
            break
        out.append(line)
    return list(reversed(out))


def _parse_json(text: str) -> dict | None:
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def week_bullets(items, week_start: str) -> list[str]:
    """Bullet texts, dropping any the model dated before the week began
    (activity recorded this week often describes earlier events)."""
    out = []
    for item in items or []:
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            date = str(item.get("date") or "")[:10]
            dated = re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)
            if week_start and dated and date < week_start:
                log.info(f"[kirsten] dropped pre-week item ({date}): "
                         f"{text[:80]}")
                continue
        else:
            text = str(item).strip()
        if text:
            out.append(text)
    return out


def summarize_case(client, model: str, case: dict, lines: list[str],
                   memo: str, background: str, window: str,
                   week_start: str = "") -> dict:
    """One Claude call. Returns {status, week, next_steps} or {error}."""
    today = datetime.now().strftime("%Y-%m-%d (%A)")
    prompt = f"""TODAY: {today}
REPORTING WEEK: {window}
WEEK START: {week_start}

CASE
Name: {case.get('File Name')}
Client entity: {case.get('Client')}
Description: {case.get('Description') or '(none)'}

ACTIVITY THIS WEEK ({len(lines)} items):
{chr(10).join(lines) if lines else '(no recorded activity this week)'}

CASE OVERVIEW (background):
{background or '(none)'}

CASE STATUS MEMORANDUM (background, may be out of date):
{memo or '(no memo on file)'}

Write the JSON object for this case."""
    try:
        resp = client.messages.create(
            model=model, max_tokens=1200, system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}])
        data = _parse_json(resp.content[0].text)
    except Exception as e:
        return {"error": str(e)}
    if not data:
        return {"error": "the summary came back unreadable"}
    week = week_bullets(data.get("week"), week_start[:10])
    steps = [str(b).strip() for b in data.get("next_steps") or []
             if str(b).strip()]
    return {"status": str(data.get("status") or "").strip(),
            "week": week[:6], "next_steps": steps[:5]}


# ---------------------------------------------------------------------------
# Rendering (Maple digest layout)
# ---------------------------------------------------------------------------

def _bullets(items: list[str]) -> str:
    t = THEME
    rows = "".join(
        f'<tr><td style="padding:0 8px 6px 0;vertical-align:top;'
        f'color:{t["label"]};font-size:14px;line-height:1.5;">&#8226;</td>'
        f'<td style="padding:0 0 6px;font-size:14px;color:{t["body"]};'
        f'line-height:1.5;">{escape(i)}</td></tr>' for i in items)
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:collapse;">{rows}</table>')


def _label(text: str) -> str:
    return (f'<div style="font-size:11px;font-weight:700;letter-spacing:.06em;'
            f'text-transform:uppercase;color:{THEME["label"]};'
            f'margin:0 0 6px;">{text}</div>')


def render_card(title: str, subtitle: str, section: dict) -> str:
    t = THEME
    if section.get("error"):
        body = (f'<div style="font-size:14px;color:{t["warn"]};font-weight:600;">'
                f'[Rocky could not summarize this case ({escape(section["error"])}) '
                f'— fill in or delete this card before sending.]</div>')
    else:
        week = section.get("week") or []
        week_html = (_bullets(week) if week else
                     f'<div style="font-size:14px;color:{t["muted"]};'
                     f'margin:0 0 6px;">No significant developments this week.</div>')
        status = section.get("status") or ""
        status_html = (f'<div style="font-size:13px;color:{t["muted"]};'
                       f'margin:0 0 14px;line-height:1.45;">'
                       f'<span style="font-weight:600;">Status:</span> '
                       f'{escape(status)}</div>') if status else ""
        body = (status_html
                + f'<div style="padding:0 0 0 12px;border-left:3px solid '
                  f'{t["rule"]};margin:0 0 14px;">{_label("Week in review")}'
                  f'{week_html}</div>'
                + f'<div style="padding:0 0 0 12px;border-left:3px solid '
                  f'{t["rule"]};">{_label("Next steps")}'
                  f'{_bullets(section.get("next_steps") or [])}</div>')
    sub = (f'<div style="font-size:12px;color:{t["muted"]};margin:-10px 0 12px;">'
           f'{escape(subtitle)}</div>') if subtitle else ""
    return (f'<table role="presentation" width="100%" cellpadding="0" '
            f'cellspacing="0" style="border-collapse:separate;background:#ffffff;'
            f'border:1px solid {t["card_border"]};border-radius:10px;'
            f'margin:0 0 16px;"><tr><td style="padding:18px 20px;">'
            f'<div style="font-size:16px;font-weight:700;color:{t["card_title"]};'
            f'margin:0 0 14px;line-height:1.35;">{escape(title)}</div>{sub}'
            f'{body}</td></tr></table>')


def render_report(cards: list[tuple[str, str, dict]], week_label: str,
                  generated: datetime) -> str:
    t = THEME
    active = sum(1 for _, _, s in cards if s.get("week"))
    strip = (f'{len(cards)} case{"s" if len(cards) != 1 else ""} &nbsp;&middot;'
             f'&nbsp; {active} with activity this week')
    body = "".join(render_card(*c) for c in cards)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{REPORT_NAME}</title></head>
<body style="margin:0;padding:0;background:{t['page_bg']};font-family:{_FONT};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
 style="background:{t['page_bg']};padding:24px 12px;"><tr><td align="center">
<table role="presentation" width="640" cellpadding="0" cellspacing="0"
 style="width:640px;max-width:640px;background:#ffffff;border-radius:14px;overflow:hidden;
 box-shadow:0 10px 30px rgba(31,41,51,0.10);">

  <!-- header -->
  <tr><td style="background:{t['header_bg']};padding:22px 28px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="font-size:20px;font-weight:800;color:{t['header_text']};letter-spacing:.01em;">
        Gallagher LLP</td>
      <td align="right" style="font-size:12px;color:{t['header_sub']};font-weight:600;">
        Weekly Litigation Report</td>
    </tr></table>
  </td></tr>

  <!-- title bar -->
  <tr><td style="padding:22px 28px 6px;">
    <div style="font-size:20px;font-weight:700;color:{t['title']};margin:0;">Gallagher-Bozzuto Litigation Report</div>
    <div style="font-size:13px;color:{t['muted']};margin:4px 0 0;">{escape(week_label)}</div>
    <div style="font-size:11px;color:{t['muted']};margin:6px 0 0;font-weight:600;letter-spacing:.04em;">PRIVILEGED &amp; CONFIDENTIAL &mdash; ATTORNEY-CLIENT COMMUNICATION</div>
  </td></tr>

  <!-- summary strip -->
  <tr><td style="padding:10px 28px 4px;">
    <table role="presentation" cellpadding="0" cellspacing="0"
     style="background:{t['strip_bg']};border-radius:8px;"><tr>
      <td style="padding:10px 16px;font-size:13px;color:{t['strip_text']};font-weight:600;">
        {strip}</td>
    </tr></table>
  </td></tr>

  <!-- cards -->
  <tr><td style="padding:14px 28px 4px;">
    {body}
  </td></tr>

  <!-- footer -->
  <tr><td style="padding:8px 28px 26px;border-top:1px solid {t['card_border']};">
    <div style="font-size:11px;color:{t['muted']};line-height:1.6;margin-top:14px;">
      Prepared by Gallagher LLP for Bozzuto, {generated.strftime('%B')} {generated.day}, {generated.year}.<br>
      Each case shows where it stands, what happened this week, and what comes next.
      Please reply with any questions.
    </div>
  </td></tr>

</table></td></tr></table>
</body></html>"""


def _week_label(start: datetime, end: datetime) -> str:
    if start.year == end.year:
        return (f"Week of {start.strftime('%B')} {start.day} – "
                f"{end.strftime('%B')} {end.day}, {end.year}")
    return (f"Week of {start.strftime('%B')} {start.day}, {start.year} – "
            f"{end.strftime('%B')} {end.day}, {end.year}")


def _card_title(case: dict) -> tuple[str, str]:
    name = str(case.get("File Name") or case.get("RRID#") or "").strip()
    return name, str(case.get("Description") or "").strip()


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def build(config: dict, days: int, rrids: list[str] | None = None
          ) -> tuple[str | None, str, list[str]]:
    """Returns (html or None, reason, card names)."""
    import rocky
    from anthropic import Anthropic

    cases = rocky.load_case_index()
    if not cases:
        return None, "case index unreadable (see warning above)", []
    if not kirsten_column(cases):
        return None, ("no 'Kirsten Digest?' column in the Rocky Case Index "
                      "(add it with yes/no per case)"), []
    selected = select_cases(cases, rrids)
    if not selected:
        return None, "no open case is marked yes for the Kirsten Digest", []

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    local_start, local_end = start.astimezone(), end.astimezone()
    window = (f"{local_start.strftime('%a %Y-%m-%d %H:%M')} to "
              f"{local_end.strftime('%a %Y-%m-%d %H:%M')}")
    client = Anthropic(api_key=config["anthropic_api_key"])
    model = config.get("kirsten_digest_model", rocky.CLAUDE_MODEL)

    cards = []
    for case in selected:
        rrid = str(case.get("RRID#") or "").upper()
        title, subtitle = _card_title(case)
        folder = find_folder(rocky.ROCKY_CASES_ROOT, case)
        if folder is None:
            log.warning(f"[kirsten] {rrid} {title}: no case folder found")
            cards.append((title, subtitle,
                          {"error": "no case folder found under Rocky Cases"}))
            continue
        lines = activity_lines(rocky._read_activity_since(folder, start))
        memo = rocky._extract_status_memo_text(folder)
        background = rocky._read_case_digest_instructions(folder)
        section = summarize_case(client, model, case, lines, memo,
                                 background, window,
                                 local_start.strftime("%Y-%m-%d (%A)"))
        if section.get("error"):
            log.warning(f"[kirsten] {rrid} {title}: {section['error']}")
        log.info(f"[kirsten] {rrid} {folder.name}: {len(lines)} activity "
                 f"line(s), {len(section.get('week') or [])} week bullet(s)")
        cards.append((title, subtitle, section))

    # Cases with something to report first, then the quiet ones.
    cards.sort(key=lambda c: (not c[2].get("week") and not c[2].get("error"),
                              c[0].lower()))
    html = render_report(cards, _week_label(local_start, local_end),
                         datetime.now())
    return html, "ok", [c[0] for c in cards]


def _state_path(data_dir: Path) -> Path:
    return data_dir / "state" / "kirsten_digest.json"


def _read_state(data_dir: Path) -> dict:
    try:
        return json.loads(_state_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def run_cli(config: dict, data_dir: Path) -> None:
    import rocky

    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    force = "--force" in args
    days = 7
    if "--days" in args:
        i = args.index("--days")
        try:
            days = int(args[i + 1])
        except (IndexError, ValueError):
            log.error("--days needs a whole number")
            sys.exit(1)
    rrids = [a.upper() for a in args if rocky.RRID_PATTERN.fullmatch(a)]

    today = datetime.now().strftime("%Y-%m-%d")
    state = _read_state(data_dir)
    if not dry_run and not force and state.get("last_drafted") == today:
        log.info(f"[kirsten] already drafted today ({today}); --force to "
                 f"draft another")
        return

    log.info("=" * 60)
    log.info(f"Rocky — Kirsten Digest ({REPORT_NAME}), last {days} days"
             f"{' — DRY RUN' if dry_run else ''}")
    log.info("=" * 60)
    html, reason, names = build(config, days, rrids)
    if html is None:
        log.warning(f"[kirsten] no report: {reason}")
        return

    end = datetime.now()
    subject = (f"{REPORT_NAME} — Week Ending "
               f"{end.strftime('%B')} {end.day}, {end.year}")
    to = config.get("kirsten_digest_to") or _DEFAULT_TO
    cc = config.get("kirsten_digest_cc")
    if cc is None:
        cc = _DEFAULT_CC
    mailbox = config.get("kirsten_digest_mailbox", _DEFAULT_MAILBOX)

    if dry_run:
        preview = data_dir / "state" / "kirsten_digest_preview.html"
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_text(html, encoding="utf-8")
        log.info(f"[kirsten] DRY RUN — would draft {subject!r} into "
                 f"{mailbox}'s Drafts, To {to}, Cc {cc}, {len(names)} "
                 f"case(s): {names}. Preview: {preview}")
        return

    archive = rocky.ROCKY_CASES_ROOT / ARCHIVE_DIRNAME / f"{today}.html"
    try:
        archive.parent.mkdir(exist_ok=True)
        archive.write_text(html, encoding="utf-8")
    except OSError as e:
        log.warning(f"[kirsten] could not save {archive}: {e}")

    import pending_llt
    token = rocky.acquire_token(rocky.get_msal_app(config))
    rocky.audit_token_scopes(token)
    result = pending_llt.create_draft_email(
        token=token, user_email=mailbox, to_addresses=to, subject=subject,
        html_body=html, cc_addresses=cc or None)
    if not result.get("created"):
        log.warning(f"[kirsten] draft FAILED: {result.get('reason')}")
        return
    state.update(last_drafted=today, message_id=result.get("message_id"),
                 cases=names)
    try:
        _state_path(data_dir).parent.mkdir(parents=True, exist_ok=True)
        _state_path(data_dir).write_text(json.dumps(state, indent=2),
                                         encoding="utf-8")
    except OSError as e:
        log.warning(f"[kirsten] could not record state: {e}")
    log.info(f"[kirsten] drafted {subject!r} into {mailbox}'s Drafts, To "
             f"{to}, Cc {cc} ({len(names)} case(s)). James reviews and sends.")
