"""
MF Case Brain — the daily learning email
=========================================

    python rocky.py --mf-brain --digest [--dry-run] [--max-questions N]

One email a day from rocky@ to James: what the brain saw, what it thinks
is happening, how its earlier guesses turned out, what it would have
done had it been allowed to act, and a short list of questions with
fill-in boxes. James types into the boxes and replies; the next run
reads the reply out of rocky@'s inbox, files the answers, and folds them
into the standing rules.

Modelled on how the Maple updater started — suggested changes first,
authority later — and it reuses the answer-box mechanism the Inbox
Cleaner questionnaire already proved out (`inbox_cleaner`), including
the sentinel-before-outro fix that stopped quoted text bleeding into the
last answer.

**Nothing here acts.** The "what I would have done" section is the dry
run of Phase 2. Its accuracy, tracked against what actually happens, is
what eventually earns the right to do any of it.

Question budget is deliberate. An email with forty questions is an email
nobody answers, so the day's candidates are ranked by how many
ambiguities each answer would clear and the top few go out. The rest
wait; they are not lost.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from html import escape
from pathlib import Path

log = logging.getLogger("rocky.mfdigest")

SUBJECT_PREFIX = "Rocky — Multifamily Brain"
_ANSWER_MARKER_RE = re.compile(r"Answer\s*\[\s*mfq-([0-9a-f]{6,})\s*\]\s*:", re.I)
_ANSWER_STOP_RE = re.compile(r"Answer\s*\[\s*mfq-|\(End of questions\)")

DEFAULT_MAX_QUESTIONS = 5

_MUTED = "color:#666;"
_F = "font-family:'Segoe UI',Arial,sans-serif;"


# =============================================================================
# Question generation and ranking
# =============================================================================

def build_questions(paths: dict, cases: dict, mail_questions: list[dict],
                    max_q: int) -> list[dict]:
    """Rank the day's open questions by how much an answer buys.

    An identity question covering a property that appears on twelve
    unresolved folders is worth twelve times one that covers a single
    folder, so impact is the count of ambiguities the answer would
    clear. Questions already asked are not re-asked unless still
    unanswered after a week."""
    import mf_brain

    asked = {q.get("qid"): q for q in mf_brain.read_jsonl(paths["questions"])}
    answered = {a.get("qid") for a in mf_brain.read_jsonl(paths["answers"])}
    candidates: list[dict] = []

    # 1. Identity — the highest-leverage thing James can settle.
    #
    # Two shapes, and they need different questions. A cluster sharing a
    # property guess is a question ABOUT THE PROPERTY ("is Bridge
    # District another name for Stratos?"), and one answer clears the
    # cluster. A lone folder is a question about that one pairing. The
    # first live run asked the property question about everything and
    # produced nonsense like "is Press House another name for a
    # property?" off a coincidental surname, so the property framing is
    # now used only where the property really is the variable.
    nr = paths["needs_review"] / "unresolved_folders.json"
    if nr.exists():
        try:
            unresolved = json.loads(nr.read_text("utf-8"))
        except ValueError:
            unresolved = []
        by_prop: dict[str, list[dict]] = {}
        for u in unresolved:
            if u.get("why", "").startswith("name+unit"):
                by_prop.setdefault(u.get("property_guess") or "?", []).append(u)
        for prop, group in by_prop.items():
            others = sorted({(u.get("guess_label") or "").split(" — ")[0]
                             for u in group if u.get("guess_label")})
            if len(group) >= 3 and len(others) <= 2 and others:
                # A whole cluster of residents at "prop" matching the
                # same other property, by name AND unit. That is a
                # property alias, not a coincidence.
                candidates.append({
                    "qid": _qid("alias", prop, others[0]),
                    "kind": "identity", "impact": len(group),
                    "text": (
                        f"{len(group)} residents in my folder \"{prop}\" "
                        f"match sheet rows at \"{others[0]}\" by both name "
                        f"and unit — for example "
                        f"\"{group[0].get('name')}\". Are \"{prop}\" and "
                        f"\"{others[0]}\" the same property?"),
                    "context": {"property": prop, "other": others[0],
                                "folders": len(group)},
                })
                continue
            for u in group[:3]:
                candidates.append({
                    "qid": _qid("pair", u.get("folder_id", ""),
                                u.get("guess", "")),
                    "kind": "identity", "impact": 1,
                    "text": (
                        f"Is the folder \"{u.get('name')}\" (in "
                        f"{u.get('path')}) the same matter as the sheet row "
                        f"{u.get('guess_label')}? Same name and unit, "
                        f"different property."),
                    "context": {"folder_id": u.get("folder_id"),
                                "case": u.get("guess")},
                })

    # 2. Whatever reading the mail raised.
    for mq in mail_questions:
        candidates.append({
            "qid": _qid("mail", mq.get("case", ""), mq.get("text", "")[:60]),
            "kind": "mail", "impact": 1,
            "text": f"{mq.get('label')}: {mq.get('text')}",
            "context": {"case": mq.get("case")},
        })

    # 3. Matters with no system of record and no recent sign of life —
    #    the class nothing else in the firm would surface.
    stale = [c for c in cases.values()
             if c.get("matter_type") not in ("eviction", "insured_litigation")
             and c.get("first_seen_by") == "folder"]
    if stale:
        qid = _qid("untracked", datetime.now().strftime("%Y-%W"))
        sample = ", ".join(c.get("label", "")[:40] for c in stale[:3])
        candidates.append({
            "qid": qid, "kind": "untracked", "impact": 2,
            "text": (
                f"{len(stale)} matters exist only as an email folder — no "
                f"spreadsheet row, no task list entry (e.g. {sample}). "
                f"Should these be tracked somewhere, or is the folder "
                f"enough for this kind of matter?"),
            "context": {"count": len(stale)},
        })

    fresh = [c for c in candidates
             if c["qid"] not in answered
             and (c["qid"] not in asked or _stale_ask(asked[c["qid"]]))]
    fresh.sort(key=lambda c: -c["impact"])
    return fresh[:max_q]


def _qid(*parts: str) -> str:
    import hashlib
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:8]


def _stale_ask(q: dict) -> bool:
    try:
        asked = datetime.fromisoformat((q.get("ts") or "").replace("Z", "+00:00"))
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - asked).days >= 7


# =============================================================================
# "What I would have done"
# =============================================================================

def dry_run_actions(cases: dict, lines: dict, summary: dict) -> list[str]:
    """Phase 2, rehearsed. Every line is an action the brain believes is
    correct and is not permitted to take."""
    import mf_brain
    out: list[str] = []
    closed = [r for r in lines.values() if r.get("closed")]
    for r in closed[-8:]:
        c = cases.get(r.get("case")) or {}
        out.append(f"Archive <b>{escape(str(c.get('label', r.get('case'))))}"
                   f"</b> — its row left the spreadsheet, so I would have "
                   f"moved it to a Closed tab and flattened its mail folder")
    for o in (summary.get("mail_questions") or [])[:3]:
        out.append(f"Ask the property manager about "
                   f"<b>{escape(str(o.get('label', '')))}</b>")
    return out


# =============================================================================
# The email
# =============================================================================

def _ul(items: list[str]) -> str:
    lis = "".join(f"<li style='margin:3px 0;'>{i}</li>" for i in items)
    return f"<ul style='margin:0;padding-left:20px;font-size:13px;'>{lis}</ul>"


def _section(title: str, blurb: str, n: int | None = None) -> str:
    suffix = f" ({n})" if n is not None else ""
    return (f"<h2 style='margin:22px 0 2px 0;font-size:16px;'>"
            f"{escape(title)}{suffix}</h2>"
            f"<p style='margin:0 0 6px 0;font-size:12px;{_MUTED}'>"
            f"{escape(blurb)}</p>")


def scorecard_html(settled: list[dict], preds: dict) -> str:
    """Predictions the world resolved since the last email. This is the
    section that earns (or fails to earn) the right to act later, so it
    reports wrong answers as plainly as right ones."""
    if not settled:
        graded = [p for p in preds.values()
                  if p.get("status") in ("right", "wrong")]
        if not graded:
            return ""
        right = sum(1 for p in graded if p["status"] == "right")
        return _section(
            "Scorecard", "No predictions came due since the last email.") + \
            f"<p style='font-size:13px;'>Running accuracy: <b>{right}/" \
            f"{len(graded)}</b> ({right / len(graded):.0%}).</p>"
    items = []
    for p in settled:
        mark = {"right": "✓", "wrong": "✗", "moot": "–"}.get(p["status"], "?")
        colour = {"right": "#1a7f37", "wrong": "#b3261e"}.get(
            p["status"], "#666")
        items.append(
            f"<span style='color:{colour};font-weight:600;'>{mark}</span> "
            f"{escape(str(p.get('statement') or p.get('kind')))} "
            f"<span style='{_MUTED}'>({p['status']})</span>")
    graded = [p for p in preds.values()
              if p.get("status") in ("right", "wrong")]
    right = sum(1 for p in graded if p["status"] == "right")
    tail = (f"<p style='margin:8px 0 0 0;font-size:12px;{_MUTED}'>"
            f"Running accuracy: {right}/{len(graded)} "
            f"({right / len(graded):.0%}) across every graded prediction. "
            f"Moot means the matter resolved for a reason the prediction "
            f"was not about; those are excluded from the rate.</p>"
            ) if graded else ""
    return _section("Scorecard",
                    "How the guesses I made earlier actually turned out.",
                    len(settled)) + _ul(items) + tail


def questions_html(questions: list[dict]) -> str:
    if not questions:
        return ""
    parts = [_section(
        "Questions",
        "Type an answer in any box and reply. Anything you skip I will "
        "ask again in a week. Blank boxes are ignored.", len(questions))]
    for q in questions:
        parts.append(
            f"<p style='margin:14px 0 4px 0;font-size:13px;'>"
            f"{escape(q['text'])}</p>"
            f"<div style='border:1px solid #b5b5b5;background:#fafafa;"
            f"padding:10px 12px;margin:4px 0;min-height:48px;font-size:13px;'>"
            f"<span style='color:#8a8a8a;'>Answer [mfq-{q['qid']}]:</span>"
            f"<br><br></div>")
    # Sentinel BEFORE any closing text: the parser reads each answer up to
    # the next stop marker, and quoted-back trailing prose would otherwise
    # land inside the final answer.
    parts.append("<p style='color:#8a8a8a;font-size:12px;'>"
                 "(End of questions)</p>")
    return "\n".join(parts)


_THOUGHTS_SYSTEM = """You are Rocky, a paralegal assistant, writing the \
"what I think is happening" paragraph of your own daily learning email to \
James Bragdon, the supervising attorney.

You have been watching this practice for a short time and you are still \
calibrating. Write 2-4 short paragraphs of plain English about what the \
day's numbers suggest, what surprised you, and what you are unsure about. \
Be concrete and name specific matters. Say plainly when you do not know \
something.

Do not pad. Do not flatter. Do not recommend that anyone "consider" \
anything. No bullet lists, no headings. If the day was quiet, say so in \
two sentences and stop."""


def thoughts_html(config: dict, summary: dict, cases: dict) -> str:
    """The 'thoughts' James asked for. One Claude call per day."""
    try:
        from anthropic import Anthropic
        from rocky import CLAUDE_MODEL
        client = Anthropic(api_key=config["anthropic_api_key"])
        facts = {
            "_glossary": "A CASE is one resident at one unit. A matter "
                         "LINE is one dispute under that case; a resident "
                         "can have two at once (a rent case and a smoking "
                         "case). Counts below are cases unless the key "
                         "says line.",
            # Today's date explicitly: the first run inferred "late July"
            # from the sheet's revision-tab name, which is the date
            # Christina last revised the workbook, not today.
            "today": datetime.now().strftime("%A %d %B %Y"),
            "sheet_revision_tab": summary.get("sheet"),
            "_sheet_note": "sheet_revision_tab is the name of the "
                           "spreadsheet tab, i.e. when the workbook was "
                           "last revised. It is NOT today's date.",
            "cases_total": summary.get("cases_after"),
            "new_cases": summary.get("new_cases"),
            "matter_lines_changed": summary.get("line_changes"),
            "lines_closed": summary.get("lines_closed"),
            "mail_messages": summary.get("mail_messages"),
            "mail_folders": summary.get("mail_folders"),
            "predictions_new": summary.get("predictions_new"),
            "predictions_settled": [
                {k: p.get(k) for k in ("kind", "status", "statement")}
                for p in summary.get("predictions_settled", [])][:20],
            "unresolved_folders": summary.get("unresolved_folders"),
            "by_matter_type": summary.get("by_type"),
        }
        resp = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=700, system=_THOUGHTS_SYSTEM,
            messages=[{"role": "user",
                       "content": json.dumps(facts, indent=1)}])
        text = "".join(b.text for b in resp.content
                       if getattr(b, "type", "") == "text").strip()
    except Exception as e:
        log.warning(f"[mf-digest] thoughts failed ({e}) — section omitted")
        return ""
    if not text:
        return ""
    paras = "".join(f"<p style='margin:6px 0;font-size:13px;'>{escape(p)}</p>"
                    for p in text.split("\n\n") if p.strip())
    return _section("What I think is happening",
                    "My read on the day. I am still calibrating; correct "
                    "me freely.") + paras


def build_email(config: dict, paths: dict, summary: dict,
                questions: list[dict]) -> str:
    import mf_brain
    cases = mf_brain.load_cases(paths)
    lines = mf_brain.load_lines(paths)
    preds = mf_brain._last_wins(
        mf_brain.read_jsonl(paths["predictions"]), "pred_id")

    saw = [
        f"<b>{summary.get('line_changes', 0)}</b> matter line(s) changed on "
        f"the spreadsheet, <b>{summary.get('lines_closed', 0)}</b> left it",
        f"<b>{summary.get('mail_messages', 0)}</b> new email(s) across "
        f"<b>{summary.get('mail_folders', 0)}</b> matter folder(s)",
        f"<b>{summary.get('new_cases', 0)}</b> matter(s) I had not seen before",
    ]
    if summary.get("unresolved_folders"):
        saw.append(f"<b>{summary['unresolved_folders']}</b> folder(s) I cannot "
                   f"confidently match to a matter "
                   f"<span style='{_MUTED}'>(the questions below chip at "
                   f"this)</span>")

    would = dry_run_actions(cases, lines, summary)
    today = datetime.now().strftime("%B %d, %Y").replace(" 0", " ")
    body = [
        f"<div style='{_F}color:#222;max-width:820px;'>",
        f"<h2 style='margin:0 0 2px 0;'>Multifamily Brain — {today}</h2>",
        f"<p style='margin:0 0 10px 0;font-size:12px;{_MUTED}'>"
        f"I am watching only. Nothing in this email has been acted on, and "
        f"I cannot change the spreadsheet, your mail or the task list.</p>",
        _section("What I saw", "Across the spreadsheet, your matter folders "
                               "and the task list.") + _ul(saw),
        scorecard_html(summary.get("predictions_settled", []), preds),
        thoughts_html(config, summary, cases),
    ]
    if would:
        body.append(
            _section("What I would have done",
                     "Had I been allowed to act. I have not. Tell me which "
                     "of these are wrong.", len(would)) + _ul(would))
    body.append(questions_html(questions))
    body.append(
        f"<p style='margin:20px 0 0 0;font-size:12px;color:#999;'>"
        f"Rocky — MF Case Brain, Stage 0. Ledger: "
        f"{escape(str(paths['root']))}. Reply with the boxes filled in and "
        f"I will read it on the next run.</p></div>")
    return "\n".join(p for p in body if p)


# =============================================================================
# Reading the reply
# =============================================================================

def _clean_answer(text: str) -> str:
    """The typed answer only — not the quoted email around it.

    Two rules, both learned from how a reply actually arrives:

    1. **Drop quoted lines outright** rather than un-quoting them.
       `inbox_cleaner` strips the ">" and keeps the content, which means
       a quoted question sitting between two boxes gets absorbed into
       the previous answer.
    2. **Stop at the first blank line after something has been
       collected.** James types into the box, and the next question's
       prose is separated from it by the box border, which survives
       tag-stripping as a blank line. Without this, everything up to the
       next marker lands in the answer.
    """
    out: list[str] = []
    for raw in (text or "").splitlines():
        if re.match(r"^\s*>", raw):
            continue
        ln = raw.strip()
        if not ln:
            if out:
                break
            continue
        out.append(ln)
    return " ".join(out).strip()


def parse_answers(body: str) -> dict[str, str]:
    """{qid: answer} from a reply. Empty boxes (the unanswered original
    quoted back) parse to nothing."""
    out: dict[str, str] = {}
    for m in _ANSWER_MARKER_RE.finditer(body or ""):
        qid = m.group(1)
        stop = _ANSWER_STOP_RE.search(body, pos=m.end())
        text = _clean_answer(body[m.end():stop.start()] if stop
                             else body[m.end():])
        if text and qid not in out:
            out[qid] = text[:2000]
    return out


def collect_answers(config: dict, paths: dict, token: str) -> list[dict]:
    """Look in rocky@'s inbox for replies to any digest and file the
    answers. Runs before the new digest is built, so an answer given
    yesterday stops the question being asked again today."""
    import requests
    import mf_brain
    import pending_llt as pl

    rocky_email = config.get("rocky_email", "rocky@gallagherllp.com")
    H = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    url = (f"{pl.GRAPH_API_BASE}/users/{rocky_email}/mailFolders/inbox/messages"
           f"?$search=\"subject:{SUBJECT_PREFIX}\"&$top=25"
           f"&$select=id,subject,receivedDateTime,body,from")
    r = requests.get(url, headers={**H, "ConsistencyLevel": "eventual"},
                     timeout=60)
    if r.status_code != 200:
        log.warning(f"[mf-digest] reply search {r.status_code}: {r.text[:160]}")
        return []

    known = {a.get("qid") for a in mf_brain.read_jsonl(paths["answers"])}
    asked = {q.get("qid"): q
             for q in mf_brain.read_jsonl(paths["questions"])}
    ts = datetime.now(timezone.utc).isoformat()
    new: list[dict] = []
    for m in r.json().get("value", []):
        sender = ((m.get("from") or {}).get("emailAddress") or {}).get(
            "address", "").lower()
        if sender != config.get("user_email",
                                "jbragdon@gallagherllp.com").lower():
            continue
        body = ((m.get("body") or {}).get("content") or "")
        body = re.sub(r"<[^>]+>", "\n", body)
        for qid, answer in parse_answers(body).items():
            if qid in known:
                continue
            new.append({"qid": qid, "ts": ts, "answer": answer,
                        "question": (asked.get(qid) or {}).get("text"),
                        "kind": (asked.get(qid) or {}).get("kind"),
                        "message_id": m.get("id"),
                        "received": m.get("receivedDateTime")})
            known.add(qid)
    if new:
        mf_brain.append_jsonl(paths["answers"], new)
        _fold_into_learning(paths, new)
        log.info(f"[mf-digest] filed {len(new)} new answer(s)")
    return new


def _fold_into_learning(paths: dict, answers: list[dict]) -> None:
    """Append answers to learning.md, the plain-English standing rules
    every future run reads. Same pattern as litigation_updater's brain
    file: human-editable, and the long-term value of the process."""
    p = paths["learning"]
    header = not p.exists()
    with open(p, "a", encoding="utf-8") as f:
        if header:
            f.write("# What James has taught the Multifamily Brain\n\n"
                    "Answers typed into the daily email. Every run reads "
                    "this. Edit freely — it is meant to be corrected.\n")
        f.write(f"\n## {datetime.now():%Y-%m-%d}\n\n")
        for a in answers:
            f.write(f"- **Q ({a.get('kind')}):** {a.get('question')}\n"
                    f"  **A:** {a.get('answer')}\n")


# =============================================================================
# Entry point
# =============================================================================

def run_cli(config: dict, data_dir: Path) -> None:
    import mf_brain
    from rocky import acquire_token, audit_token_scopes, get_msal_app

    paths = mf_brain.get_paths(config, data_dir)
    dry_run = "--dry-run" in sys.argv
    max_q = int(_argv("--max-questions") or DEFAULT_MAX_QUESTIONS)

    token = acquire_token(get_msal_app(config))
    audit_token_scopes(token)

    # Answers first: a question settled yesterday should not be re-asked.
    collect_answers(config, paths, token)

    summary = mf_brain.run_scan(config, paths, token=token, dry_run=dry_run)
    cases = mf_brain.load_cases(paths)
    questions = build_questions(paths, cases,
                                summary.get("mail_questions", []), max_q)
    html = build_email(config, paths, summary, questions)
    subject = f"{SUBJECT_PREFIX} — {datetime.now():%B %d, %Y}".replace(" 0", " ")

    if dry_run:
        log.info(f"[mf-digest] DRY-RUN — would send {subject!r} with "
                 f"{len(questions)} question(s)")
        print(html)
        return

    from outbound import send_mail_guarded
    to = config.get("mf_digest_recipients") or [
        config.get("user_email", "jbragdon@gallagherllp.com")]
    result = send_mail_guarded(
        token, config.get("rocky_email", "rocky@gallagherllp.com"),
        to, subject, html, body_type="HTML")
    if result.get("sent"):
        mf_brain.append_jsonl(paths["questions"], [
            dict(q, ts=datetime.now(timezone.utc).isoformat(),
                 subject=subject) for q in questions])
        log.info(f"[mf-digest] sent to {to} with {len(questions)} question(s)")
    else:
        log.warning(f"[mf-digest] send FAILED: {result.get('reason')}")


def _argv(flag: str) -> str | None:
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None
