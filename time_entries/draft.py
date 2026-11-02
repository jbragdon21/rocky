"""Drafting and verification: the two Claude calls per day sheet.

Phase 2 of TIME_ENTRIES_DESIGN.md. Each day sheet (daysheet.py) gets:

  1. a drafting call, which reads the sheet, may open any email's full body
     and reply chain (read_email) or search James's sends (search_mail),
     and answers through submit_drafts with one draft per entry ref plus
     the day's uncaptured threads;
  2. a verification call with the same sheet and tools plus the drafts,
     whose only job is Rule 0: it returns fixes through submit_review.

Rocky checks every submission in code (check_drafts) before taking it. A
failed check goes back to the model once, as an error tool result listing
the problems. On a second failure the mechanical problems are fixed in code
(a missing final semicolon, an em dash) and the rest become REVIEW rows with
the reason stated, as the design says. Both versions of each row are kept:
`first` is the drafter's, the row itself is after verification.

Opus 5.5 for both calls (time_entries_model in config), with the
server-side fallback beta Rocky's other Opus calls use. Calls stream,
because a busy day's submission can run long.
"""

import json
import logging
import re

log = logging.getLogger("rocky")

DEFAULT_MODEL = "claude-opus-5-5"
BETAS = ["server-side-fallback-2026-07-01"]
MAX_TOKENS = 64000
MAX_TURNS = 16
CONFIDENCE = ("High", "Medium", "REVIEW")


class DraftError(RuntimeError):
    pass


# =============================================================================
# Prompts
# =============================================================================

DRAFT_RULES = """\
You draft time-entry narratives for James D. Bragdon, an attorney at Gallagher LLP who represents \
multifamily owners and property managers: landlord-tenant work, property management advice, and \
litigation in Virginia, DC and Maryland. His Aspen timecard has entries with a date, matter and \
hours but no narrative. Each request is one day sheet: every entry on its dates, every email James \
sent, the correlator's ranked candidates, reply chains, matter reference rows, his own recent \
narratives, and the colleague list. Write each narrative from what the email bodies and reply \
chains show James did.

Rules:

0. Correlating to the right email is not the same as being right about what it says. In one hand \
run, 13 of 46 High drafts were wrong: two inverted the advice James gave, four were written from \
the subject line instead of the body, three billed one email to two entries. Read the body and \
the reply chain before drafting. When the sheet cuts a body off or doesn't show the chain, call \
read_email. Use search_mail to chase a resident, unit or person the correlator couldn't reach.
1. Draft from same-day mail. A Saturday or Sunday entry is supported by mail sent that weekend \
and by the Monday morning batch (Sunday work leaves the outbox Monday from about 07:20). A \
weekday entry may draw on the next morning's mail before 9:00 when it plainly finishes that day's \
work. Never stitch emails from different days into a High.
2. One email or thread bills to one entry. When two entries share a matter and a day, split the \
distinct threads between them and say "split" in the note.
3. Check that the email is on the coded matter (property, contacts, matter notes). If an entry \
looks miscoded, say so in the note.
4. Style: follow James's style guide below. Residents are Full Name (unit) whenever the mail or \
his prior narratives show them. Client contacts are first initial and last name (T. Jarrett). \
Formula: [Verb] [document/action] [for/regarding/re:] [name] ([unit]); [next task]; Every \
narrative ends with a semicolon. Never use an em dash, in a narrative or anywhere else.
5. Confidence. High: a same-day email was read and the narrative comes from its body. Medium: \
the thread is identified but the date, the split between entries, or the hours are inferred; the \
note says what is uncertain. REVIEW: no reliable basis; the narrative may be empty, and the note \
says why (no candidate; a zero-hit token, which usually means a missing alias; a matter flagged \
typically-no-email, which usually means calls or document review).
6. If the basis can be named, draft it. Don't flag REVIEW while your note names the supporting \
thread: write a Medium and say what is uncertain. Under-billing is the error nobody goes looking \
for.
7. A PARTIAL entry already holds James's start. The draft must begin with his text exactly, then \
complete it.
8. Read the reply chain, not just James's line, and check the domain before naming an advisee. \
Anyone @gallagherllp.com is a colleague, never the client. Don't credit James with drafting what a \
colleague drafted. Opposing counsel, courts and agencies are not clients.
9. Fit the narrative to the hours. A 0.1 is one short email; 1.0 or more needs a record that \
carries it. Say in the note when the hours outrun what the mail shows.
10. Notes are short and specific: hours that outrun the record, possible duplicates, coding \
questions, who an unfamiliar name is. Leave the note empty when there is nothing to say.
11. Uncaptured: list sends on the sheet's dates with substantive James text where no entry on \
that matter that day (written or to draft) covers the thread. Skip personal and family mail, \
billing-admin one-liners, marketing, and pure scheduling unless it is substantive. Use matter \
"(unknown)" when the matter can't be identified. Suggested hours are usually 0.1 to 0.3.

Answer by calling submit_drafts once, with exactly one draft for every [E#] ref on the sheet. \
`basis` is human-readable, in this form: 10/6 16:02 'RE: Marina Bay/Tenant Class Action -- class \
member information' to G. Sapire, T. Papadopoulos ('See attached, this is from our Yardi'). \
`basis_refs` lists the [M#] refs of every email the narrative relies on.

Examples of finished rows from James's hand runs:
- 0.3 High: Send Yardi class member contact data to G. Sapire for class period in Marina Bay \
tenant class action; Review draft email to DA and SA regarding notice to appear for hearing on \
motion for preliminary approval and confirm BMC/Delaney sign-off with T. Papadopoulos;
- 0.1 High: Review slip and fall summons and recommend tender to ownership's carrier; Offer to \
coordinate with carrier and confirm appointment of counsel;
- 0.2 High: Review S. Chan's report on 10/6 sheriff lockout of Ashwini Batchu (635) and 15-day \
hold for removal of belongings; Respond to S. Chan and D. McMurray;
- 0.5 High: Draft response to Brian Portny regarding demands that staff investigate alleged \
criminal activity and send to S. Blaisse and K. Tamimi for review; Provide background on prior \
interactions with Mr. Portny;
"""

VERIFY_RULES = """\
You check draft time-entry narratives for James D. Bragdon, an attorney at Gallagher LLP who \
represents multifamily owners and property managers. Another pass drafted them from the day \
sheet below. Your only job is Rule 0: correlating to the right email is not the same as being \
right about what it says. In one hand run, 13 of 46 High drafts were wrong. The High rows matter \
most, because nobody double-checks them.

For every High and Medium row, open its basis emails with read_email and check the narrative \
against the body and the reply chain for these errors:
- drafted from the subject line instead of the body
- inverted advice (the draft says James recommended the opposite of what he wrote)
- crediting James with a colleague's draft (anyone @gallagherllp.com is a colleague)
- naming a colleague, opposing counsel, a court or an agency as the client
- billing one email to two entries
- stitching together emails from different days (a weekend entry may use Monday's early batch)
- work on the wrong matter
- missing a resident name or unit that the mail or James's prior narratives show
- a confidence the evidence doesn't carry

Re-check every REVIEW row too: look for mail that supports it (search_mail helps), because \
writing off billable work is the error nobody looks for. If the basis can be named, the row \
should be a Medium draft.

Return fixes only for rows that need a change. Each fix gives the whole corrected row and \
`problem`, one sentence on what was wrong. Don't reword a narrative that is already accurate. \
Keep James's style: Full Name (unit) for residents, first initial and last name for client \
contacts, a semicolon at the end of every narrative, and no em dashes anywhere. A PARTIAL entry's \
draft must still begin with James's text exactly. Call submit_review once, with an empty fixes \
list if every row holds up.
"""


def system_blocks(rules: str, style_guide: str, lessons: str) -> list[dict]:
    text = rules + "\n\n# James's style guide\n\n" + (style_guide or "(missing)").strip()
    if (lessons or "").strip():
        text += "\n\n# Lessons James has approved\n\n" + lessons.strip()
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


# =============================================================================
# Tools
# =============================================================================

_STR = {"type": "string"}
_REFS = {"type": "array", "items": {"type": "string"}}
_CONF = {"type": "string", "enum": list(CONFIDENCE)}


def _obj(props: dict) -> dict:
    return {"type": "object", "additionalProperties": False,
            "required": list(props), "properties": props}


_ROW = {"entry": _STR, "narrative": _STR, "confidence": _CONF, "basis": _STR,
        "basis_refs": _REFS, "note": _STR}
_UNCAPTURED = {"date": _STR, "matter": _STR, "matter_name": _STR,
               "hours": {"type": "number"}, "narrative": _STR, "basis": _STR,
               "basis_refs": _REFS, "note": _STR}

READ_TOOL = {
    "name": "read_email",
    "description": ("Full body and reply chain of emails on this sheet, by ref (M#). "
                    "Use it before drafting from any email whose text the sheet cuts off "
                    "or whose chain it doesn't show. Up to six refs per call."),
    "input_schema": _obj({"refs": _REFS}),
    "strict": True,
}
SEARCH_TOOL = {
    "name": "search_mail",
    "description": ("Search James's sent mail (subject, recipients and body) for a word or "
                    "phrase between two dates (YYYY-MM-DD, at most a month apart). Matches "
                    "get refs you can cite. Use it for residents, units and people the "
                    "correlator didn't reach."),
    "input_schema": _obj({"query": _STR, "date_from": _STR, "date_to": _STR}),
    "strict": True,
}
SUBMIT_DRAFTS = {
    "name": "submit_drafts",
    "description": ("Submit the finished drafts: exactly one per [E#] entry ref, plus the "
                    "uncaptured threads on the sheet's dates. Call once."),
    "input_schema": _obj({"drafts": {"type": "array", "items": _obj(_ROW)},
                          "uncaptured": {"type": "array", "items": _obj(_UNCAPTURED)},
                          "summary": _STR}),
    "strict": True,
}
SUBMIT_REVIEW = {
    "name": "submit_review",
    "description": ("Submit the verification result: one fix per row that needs a change, "
                    "each the whole corrected row plus the problem found. An empty list "
                    "means every row holds up. Call once."),
    "input_schema": _obj({"fixes": {"type": "array",
                                    "items": _obj({**_ROW, "problem": _STR})},
                          "summary": _STR}),
    "strict": True,
}


# =============================================================================
# Checks
# =============================================================================

_SPLIT = re.compile(r"\bsplit", re.I)
EM_DASH = "—"


def check_rows(sheet, rows: dict[str, dict]) -> list[str]:
    """Problems with a full set of drafts, keyed by entry ref. Empty = ok."""
    errs = []
    for ref in sheet.entry_refs:
        if ref not in rows:
            errs.append(f"{ref}: no draft. Every [E#] ref needs exactly one.")
    cited: dict[str, list[str]] = {}
    for ref, r in rows.items():
        e = sheet.entry_refs.get(ref)
        if e is None:
            errs.append(f"{ref}: not an entry ref on this sheet.")
            continue
        narr = r.get("narrative") or ""
        conf = r.get("confidence")
        if conf not in CONFIDENCE:
            errs.append(f"{ref}: confidence must be High, Medium or REVIEW.")
        start = (e.get("narrative") or "").strip()
        if start and narr.strip() and not narr.strip().startswith(start):
            errs.append(f'{ref}: a PARTIAL entry; the draft must begin with "{start}".')
        if narr.strip() and not narr.rstrip().endswith(";"):
            errs.append(f"{ref}: the narrative must end with a semicolon.")
        for field in ("narrative", "basis", "note"):
            if EM_DASH in (r.get(field) or ""):
                errs.append(f"{ref}: em dash in the {field}; use a comma, colon or semicolon.")
        if conf in ("High", "Medium") and not narr.strip():
            errs.append(f"{ref}: a {conf} row needs a narrative.")
        if conf == "High" and not r.get("basis_refs"):
            errs.append(f"{ref}: a High row needs basis_refs.")
        for m in r.get("basis_refs") or []:
            if sheet.email(m) is None:
                errs.append(f"{ref}: basis ref {m} is not an email on this sheet.")
            else:
                cited.setdefault(sheet.email(m)["id"], []).append(ref)
    for eid, refs in cited.items():
        if len(refs) > 1 and not any(_SPLIT.search(rows[r].get("note") or "") for r in refs):
            m = sheet._ref_of.get(eid, "?")
            errs.append(f"{m} is the basis for {', '.join(refs)}. One email bills to one "
                        f"entry, unless the note says the work was split.")
    return errs


def check_uncaptured(sheet, items: list[dict]) -> list[str]:
    errs = []
    for i, u in enumerate(items, 1):
        if u.get("date") not in sheet.dates and u.get("date") != _next_day(sheet):
            errs.append(f"uncaptured #{i}: date {u.get('date')} is not on this sheet.")
        for m in u.get("basis_refs") or []:
            if sheet.email(m) is None:
                errs.append(f"uncaptured #{i}: basis ref {m} is not an email on this sheet.")
        if EM_DASH in (u.get("narrative") or "") + (u.get("note") or "") + (u.get("basis") or ""):
            errs.append(f"uncaptured #{i}: em dash; use a comma, colon or semicolon.")
    return errs


def _next_day(sheet) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(sheet.dates[-1]) + timedelta(1)).isoformat()


def _clean(r: dict) -> dict:
    out = dict(r)
    for f in ("narrative", "basis", "note"):
        out[f] = re.sub(rf"\s*{EM_DASH}\s*", ", ", out.get(f) or "").strip()
    if out["narrative"] and not out["narrative"].endswith(";"):
        out["narrative"] = out["narrative"].rstrip(".,: ") + ";"
    out["basis_refs"] = [m.strip().upper() for m in out.get("basis_refs") or []]
    return out


def settle(sheet, rows: dict[str, dict]) -> tuple[dict[str, dict], list[str]]:
    """After the second failed check: fix the mechanical problems, turn the
    rest into REVIEW rows that say why. Returns (rows, what was done)."""
    done = []
    rows = {ref: _clean(r) for ref, r in rows.items() if ref in sheet.entry_refs}
    for ref, r in rows.items():
        bad = [m for m in r["basis_refs"] if sheet.email(m) is None]
        if bad:
            r["basis_refs"] = [m for m in r["basis_refs"] if m not in bad]
            done.append(f"{ref}: dropped unknown basis refs {', '.join(bad)}")
    for ref, e in sheet.entry_refs.items():
        if ref not in rows:
            rows[ref] = {"entry": ref, "narrative": (e.get("narrative") or "").strip(),
                         "confidence": "REVIEW", "basis": "", "basis_refs": [],
                         "note": "Rocky: the drafting call returned no draft for this entry."}
            done.append(f"{ref}: no draft returned; set to REVIEW")
    for err in check_rows(sheet, rows):
        refs = re.findall(r"\bE\d+\b", err)
        for ref in refs:
            r = rows.get(ref)
            if r is None or r["confidence"] == "REVIEW" and r.get("_settled"):
                continue
            r["confidence"] = "REVIEW"
            r["_settled"] = True
            r["note"] = ((r.get("note") or "") + f" Rocky's check: {err}").strip()
            done.append(f"{ref}: set to REVIEW ({err})")
    for r in rows.values():
        r.pop("_settled", None)
    return rows, done


# =============================================================================
# The conversation loop
# =============================================================================

class Usage:
    def __init__(self):
        self.calls = 0
        self.input = self.output = self.cache_read = self.cache_write = 0

    def add(self, u) -> None:
        self.calls += 1
        self.input += getattr(u, "input_tokens", 0) or 0
        self.output += getattr(u, "output_tokens", 0) or 0
        self.cache_read += getattr(u, "cache_read_input_tokens", 0) or 0
        self.cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0

    def merge(self, other: "Usage") -> None:
        for k in ("calls", "input", "output", "cache_read", "cache_write"):
            setattr(self, k, getattr(self, k) + getattr(other, k))

    def cost(self) -> float:
        """Opus 5.5 list prices: $4 in, $20 out, cache reads $0.20, cache
        writes at 1.25x input."""
        return round((self.input * 4 + self.output * 20 + self.cache_read * 0.2
                      + self.cache_write * 5) / 1e6, 2)

    def as_dict(self) -> dict:
        return {"calls": self.calls, "input": self.input, "output": self.output,
                "cache_read": self.cache_read, "cache_write": self.cache_write,
                "cost_usd": self.cost()}


def _create(client, model: str, effort: str, system, tools, messages):
    with client.beta.messages.stream(
            model=model, max_tokens=MAX_TOKENS, system=system, tools=tools,
            messages=messages, betas=BETAS, fallbacks="default",
            output_config={"effort": effort},
            cache_control={"type": "ephemeral"}) as stream:
        return stream.get_final_message()


def converse(client, model: str, effort: str, system, sheet, user_text: str,
             submit_tool: dict, on_submit, usage: Usage,
             max_turns: int = MAX_TURNS) -> bool:
    """Run one call to completion. on_submit(input) -> (accepted, message);
    returns True once a submission is accepted."""
    tools = [READ_TOOL, SEARCH_TOOL, submit_tool]
    messages = [{"role": "user", "content": [{"type": "text", "text": user_text}]}]
    nudged = False
    for _turn in range(max_turns):
        resp = _create(client, model, effort, system, tools, messages)
        usage.add(resp.usage)
        if resp.stop_reason == "refusal":
            raise DraftError("the model declined the request")
        messages.append({"role": "assistant", "content": resp.content})
        uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
        if not uses:
            if nudged:
                return False
            nudged = True
            messages.append({"role": "user", "content": [{"type": "text", "text":
                             f"Call {submit_tool['name']} now with your answer."}]})
            continue
        cut = resp.stop_reason == "max_tokens"
        results, accepted = [], False
        for b in uses:
            if cut:
                text, err = ("Your output was cut off at the length limit. Submit again, "
                             "with shorter notes and bases."), True
            elif b.name == "read_email":
                text, err = sheet.read_email(list(b.input.get("refs") or [])), False
            elif b.name == "search_mail":
                text, err = sheet.search_mail(b.input.get("query", ""),
                                              b.input.get("date_from", ""),
                                              b.input.get("date_to", "")), False
            elif b.name == submit_tool["name"]:
                ok, text = on_submit(b.input)
                err = not ok
                accepted = accepted or ok
            else:
                text, err = f"Unknown tool {b.name}.", True
            results.append({"type": "tool_result", "tool_use_id": b.id,
                            "content": text, **({"is_error": True} if err else {})})
        messages.append({"role": "user", "content": results})
        if accepted:
            return True
    return False


# =============================================================================
# One day sheet
# =============================================================================

def _rows_from(items: list[dict]) -> dict[str, dict]:
    rows = {}
    for d in items or []:
        ref = (d.get("entry") or "").strip().upper()
        if ref in rows:
            rows[ref + "#dup"] = d     # surfaces as "not an entry ref"
        else:
            rows[ref] = {k: d.get(k) for k in _ROW}
            rows[ref]["entry"] = ref
    return rows


def draft_sheet(client, sheet, style_guide: str, lessons: str,
                model: str = DEFAULT_MODEL, effort: str = "high",
                verify: bool = True) -> dict:
    """Draft, check and verify one day sheet. Never raises for a model
    problem: the rows it can't get become REVIEW, and `errors` says why."""
    usage = Usage()
    state = {"rows": None, "uncaptured": [], "summary": "", "attempts": 0,
             "settled": [], "fixes": [], "verify_summary": "", "errors": []}

    def take_drafts(inp: dict):
        state["attempts"] += 1
        rows = _rows_from(inp.get("drafts"))
        unc = list(inp.get("uncaptured") or [])
        errs = check_rows(sheet, rows) + check_uncaptured(sheet, unc)
        if errs and state["attempts"] < 2:
            return False, ("Rocky's check found problems. Fix them and call submit_drafts "
                           "again with the complete set:\n- " + "\n- ".join(errs))
        if errs:
            rows, state["settled"] = settle(sheet, rows)
            unc = [_clean(u) for u in unc
                   if all(sheet.email(m) for m in u.get("basis_refs") or [])]
        state["rows"] = {ref: _clean(r) for ref, r in rows.items()}
        state["uncaptured"] = [_clean(u) for u in unc]
        state["summary"] = inp.get("summary") or ""
        return True, "Accepted."

    system = system_blocks(DRAFT_RULES, style_guide, lessons)
    try:
        ok = converse(client, model, effort, system, sheet, sheet.text,
                      SUBMIT_DRAFTS, take_drafts, usage)
    except Exception as e:      # an API failure costs this sheet, not the run
        log.error(f"[time-entries] drafting {sheet.dates}: {e}")
        state["errors"].append(f"drafting call failed: {e}")
        ok = False
    if not ok or state["rows"] is None:
        if not state["errors"]:
            state["errors"].append("the drafting call never submitted drafts")
        state["rows"], _ = settle(sheet, {})

    for r in state["rows"].values():
        r["first"] = {k: r.get(k) for k in ("narrative", "confidence", "basis",
                                            "basis_refs", "note")}
        r["problem"] = ""

    if verify and ok:
        _verify(client, sheet, style_guide, lessons, model, effort, state, usage)

    return {"dates": sheet.dates, "rows": state["rows"],
            "uncaptured": state["uncaptured"], "summary": state["summary"],
            "verify_summary": state["verify_summary"], "fixes": state["fixes"],
            "settled": state["settled"], "errors": state["errors"],
            "usage": usage.as_dict()}


def drafts_text(sheet, rows: dict[str, dict], uncaptured: list[dict]) -> str:
    out = ["== DRAFTS TO CHECK =="]
    for ref, e in sheet.entry_refs.items():
        r = rows.get(ref) or {}
        start = (e.get("narrative") or "").strip()
        out.append(f"[{ref}] {e['work_date']} | {e['matter'] or '(no matter)'} | "
                   f"{e.get('hours')}h | {(e.get('matter_name') or '')[:60]}"
                   + (f' | PARTIAL start: "{start}"' if start else ""))
        out.append(f"   confidence: {r.get('confidence')}")
        out.append(f"   narrative: {r.get('narrative') or ''}")
        out.append(f"   basis: {r.get('basis') or ''}  refs: {', '.join(r.get('basis_refs') or [])}")
        if r.get("note"):
            out.append(f"   note: {r['note']}")
    if uncaptured:
        out.append("")
        out.append("Listed as uncaptured (for context; not under review):")
        for u in uncaptured:
            out.append(f"   {u.get('date')} {u.get('matter')} {u.get('hours')}h: "
                       f"{u.get('narrative')}  refs: {', '.join(u.get('basis_refs') or [])}")
    return "\n".join(out)


def _verify(client, sheet, style_guide, lessons, model, effort, state, usage) -> None:
    rows = state["rows"]
    attempts = {"n": 0}

    def take_review(inp: dict):
        attempts["n"] += 1
        fixes = list(inp.get("fixes") or [])
        merged = {ref: dict(r) for ref, r in rows.items()}
        bad = []
        for f in fixes:
            ref = (f.get("entry") or "").strip().upper()
            if ref not in merged:
                bad.append(f"fix for {ref}: not an entry ref on this sheet.")
                continue
            merged[ref].update({k: f.get(k) for k in _ROW if k != "entry"})
        errs = bad + check_rows(sheet, merged)
        if errs and attempts["n"] < 2:
            return False, ("Rocky's check found problems with the fixed set. Correct them and "
                           "call submit_review again with all your fixes:\n- " + "\n- ".join(errs))
        applied = []
        for f in fixes:
            ref = (f.get("entry") or "").strip().upper()
            if ref not in rows:
                continue
            trial = {**{r: dict(v) for r, v in rows.items()},
                     ref: {**rows[ref], **{k: f.get(k) for k in _ROW if k != "entry"}}}
            trial[ref] = _clean(trial[ref])
            if any(re.search(rf"\b{ref}\b", e) for e in check_rows(sheet, trial)):
                state["errors"].append(f"verifier fix for {ref} failed Rocky's check; kept the draft")
                continue
            rows[ref] = {**trial[ref], "first": rows[ref]["first"],
                         "problem": (f.get("problem") or "").strip()}
            applied.append({"entry": ref, "problem": rows[ref]["problem"],
                            "from_confidence": rows[ref]["first"]["confidence"],
                            "to_confidence": rows[ref]["confidence"]})
        state["fixes"] = applied
        state["verify_summary"] = inp.get("summary") or ""
        return True, "Accepted."

    system = system_blocks(VERIFY_RULES, style_guide, lessons)
    text = sheet.text + "\n\n" + drafts_text(sheet, rows, state["uncaptured"])
    try:
        ok = converse(client, model, effort, system, sheet, text, SUBMIT_REVIEW,
                      take_review, usage)
        if not ok:
            state["errors"].append("the verification call never submitted a review")
    except Exception as e:
        log.error(f"[time-entries] verifying {sheet.dates}: {e}")
        state["errors"].append(f"verification call failed: {e}")


def to_json(result: dict) -> str:
    return json.dumps(result, indent=1, default=str)
