"""Phase 2 backtest: draft an earlier export, score it against what James
submitted later, and seed the links table from the hand runs.

TIME_ENTRIES_DESIGN.md, Phase 2: "Backtest against periods where James's
final narratives exist in a later export ... Go live only if the High rows
hold up at least as well as the hand runs did after verification."

Two periods qualify so far: September 1-5 (the September export's blanks,
filled in the September Part 2 export) and August 1-20 (Part 2's blanks,
filled in Part 3).

Wording similarity alone can't decide this. In August James pasted most
hand-run drafts into Aspen as they were (35 of 37 High rows match at 0.9 or
more), so a wording test would score the hand run against itself. Each
scored row therefore also goes to a judge call (Opus 5.5) that sees James's
final narrative and the candidates, unlabeled and shuffled, and grades each:

  same       describes the same work, with the specifics right (people,
             document, action); wording may differ
  partial    the same thread or task, but a specific is missing, extra or off
  different  describes other work
  wrong      misstates the work: wrong person, inverted action, wrong document

The candidates are Rocky's verified draft, Rocky's first draft when the
verifier changed it, the hand run's draft, and (with --compare) the drafts
from an earlier backtest of the same period. Grading the two runs in one
call matters: on the 11/1 September re-run, 5 of 35 hand drafts graded
differently on identical text when the runs were judged apart, enough to
hide a lesson's effect.
"""

import collections
import json
import logging
import random
from datetime import date, timedelta

from . import backtest, draft, learn

log = logging.getLogger("rocky")

GRADES = ("same", "partial", "different", "wrong")
JUDGE_BATCH = 20
CANDIDATES = ("rocky", "first", "hand", "compare")

JUDGE_PROMPT = """\
You grade draft time-entry narratives for an attorney's billing. For each row you get the \
matter, the hours, the narrative the attorney actually submitted (FINAL), and one or more \
candidate drafts labeled with letters. Grade every candidate against FINAL:

- same: describes the same work with the specifics right (the people, the document, the \
action, the outcome). Wording, order and length may differ. A candidate that covers FINAL's \
work and adds a small accurate detail is still "same".
- partial: the same thread or task, but a specific is missing, extra or off (a different \
resident named, one of two tasks left out, "draft" where FINAL says "review").
- different: describes other work than FINAL.
- wrong: misstates the work: the wrong person or party, inverted advice, the wrong document, \
or work credited to the wrong side.

Grade each candidate on its own; the labels are shuffled and carry no meaning. Keep `why` to \
one short clause."""

JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["grades"],
    "properties": {"grades": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["row", "label", "grade", "why"],
        "properties": {"row": {"type": "integer"}, "label": {"type": "string"},
                       "grade": {"type": "string", "enum": list(GRADES)},
                       "why": {"type": "string"}}}}},
}


def hand_index(hand: list[dict]) -> dict:
    """(date, matter, hours, ordinal) -> hand row, ordinal by occurrence."""
    seen = collections.Counter()
    out = {}
    for h in hand:
        k = (h["date"], h["matter"], float(h["hours"]))
        out[k + (seen[k],)] = h
        seen[k] += 1
    return out


def score_rows(recs: list[dict], early: list[dict], final: list[dict],
               hand: list[dict] | None = None, max_narr: int = 35) -> tuple[list, list]:
    """(scored, outcomes). scored: one row per draft whose entry James has
    since filled, with Rocky's text, the hand run's and his."""
    outs = learn.diff(recs, early, final, max_narr)
    hidx = hand_index(hand or [])
    scored = []
    for r, o in zip(recs, outs):
        if r["kind"] != "draft" or o is None:
            continue
        if o["outcome"] in ("still_blank", "deleted"):
            continue
        h = hidx.get((r["work_date"], r["matter"], float(r.get("hours") or 0),
                      r.get("ordinal") or 0))
        first = r.get("first") or {}
        fin = o["final_narrative"] or ""
        row = {
            "date": r["work_date"], "matter": r["matter"],
            "matter_name": r.get("matter_name"), "hours": r.get("hours"),
            "final": fin, "outcome": o["outcome"],
            "rocky": r.get("narrative") or "", "rocky_conf": r.get("confidence"),
            "rocky_sim": learn.similarity(r.get("narrative"), fin),
            "first": first.get("narrative") or "", "first_conf": first.get("confidence"),
            "verify_problem": r.get("problem") or "",
            "basis": r.get("basis") or "",
        }
        if h is not None:
            row.update({"hand": h["narrative"] or "", "hand_conf": h["confidence"],
                        "hand_sim": learn.similarity(h["narrative"], fin),
                        "hand_basis": h.get("basis") or ""})
        scored.append(row)
    return scored, outs


def attach_compare(rows: list[dict], earlier: list[dict]) -> int:
    """Put an earlier backtest's Rocky draft on each matching row as the
    'compare' candidate. Rows match on (date, matter, hours, James's final
    text), in order, which survives a re-run's different row numbering.
    Returns how many matched."""
    pool = collections.defaultdict(list)
    for e in earlier:
        pool[(e["date"], e["matter"], e.get("hours"), e.get("final"))].append(e)
    n = 0
    for r in rows:
        hit = pool.get((r["date"], r["matter"], r.get("hours"), r.get("final")))
        if hit:
            e = hit.pop(0)
            r["compare"], r["compare_conf"] = e.get("rocky") or "", e.get("rocky_conf")
            n += 1
    return n


def judge(client, rows: list[dict], model: str = draft.DEFAULT_MODEL,
          effort: str = "medium", seed: int = 7) -> draft.Usage:
    """Fills grade_<who> and why_<who> on each row for every candidate it
    carries (CANDIDATES). Identical texts on one row are shown once and
    share a grade, so the judge can't grade the same words two ways. An
    empty candidate grades 'none' without a call."""
    usage = draft.Usage()
    rng = random.Random(seed)
    for start in range(0, len(rows), JUDGE_BATCH):
        batch = rows[start:start + JUDGE_BATCH]
        lines, labels = [], {}
        for i, row in enumerate(batch, 1):
            texts: dict[str, list[str]] = {}
            for who in CANDIDATES:
                if who not in row:
                    continue
                text = (row.get(who) or "").strip()
                if not text:
                    row[f"grade_{who}"] = "none"
                    continue
                texts.setdefault(text, []).append(who)
            cands = list(texts.items())
            rng.shuffle(cands)
            lines.append(f"ROW {i}: {row['matter']} {row.get('matter_name') or ''} | "
                         f"{row['hours']}h | {row['date']}")
            lines.append(f"  FINAL: {row['final']}")
            for j, (text, whos) in enumerate(cands):
                lab = "ABCDEFGH"[j]
                labels[(i, lab)] = whos
                lines.append(f"  {lab}: {text}")
        if not labels:
            continue
        resp = client.beta.messages.create(
            model=model, max_tokens=16000, system=JUDGE_PROMPT,
            messages=[{"role": "user", "content": "\n".join(lines)}],
            betas=draft.BETAS, fallbacks="default",
            output_config={"effort": effort,
                           "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}})
        usage.add(resp.usage)
        if resp.stop_reason == "refusal":
            log.error("[time-entries] judge declined a batch")
            continue
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        try:
            grades = json.loads(text)["grades"]
        except (ValueError, KeyError) as e:
            log.error(f"[time-entries] judge reply unreadable: {e}")
            continue
        for g in grades:
            whos = labels.get((g.get("row"), (g.get("label") or "").strip().upper()))
            if whos is None:
                continue
            row = batch[g["row"] - 1]
            for who in whos:
                row[f"grade_{who}"] = g["grade"]
                row[f"why_{who}"] = g.get("why") or ""
    return usage


def agreement(passes: list[list[dict]]) -> dict:
    """How often repeated judge passes gave one candidate the same grade."""
    same = total = 0
    for rows in zip(*passes):
        for who in CANDIDATES:
            grades = [r.get(f"grade_{who}") for r in rows]
            if any(g is None for g in grades) or grades[0] == "none":
                continue
            total += 1
            same += len(set(grades)) == 1
    return {"compared": total, "agreed": same,
            "rate": round(same / total, 3) if total else None}


def _rates(rows: list[dict], who: str) -> dict:
    g = collections.Counter(r.get(f"grade_{who}") or "ungraded" for r in rows)
    n = len(rows)
    return {"rows": n, **{k: g.get(k, 0) for k in GRADES + ("none", "ungraded")},
            "same_rate": round(g.get("same", 0) / n, 3) if n else None,
            "same_or_partial_rate": round((g.get("same", 0) + g.get("partial", 0)) / n, 3)
            if n else None}


def summarize(rows: list[dict], outs: list[dict]) -> dict:
    s = {"scored": len(rows), "outcomes": learn.tally(outs),
         "rocky_all": _rates(rows, "rocky"), "first_all": _rates(rows, "first")}
    for c in draft.CONFIDENCE:
        s[f"rocky_{c}"] = _rates([r for r in rows if r["rocky_conf"] == c], "rocky")
    s["first_High"] = _rates([r for r in rows if r["first_conf"] == "High"], "first")
    compared = [r for r in rows if "compare" in r]
    if compared:
        s["compare_all"] = _rates(compared, "compare")
        s["rocky_on_compare_rows"] = _rates(compared, "rocky")
        for c in draft.CONFIDENCE:
            s[f"compare_{c}"] = _rates([r for r in compared if r["compare_conf"] == c],
                                       "compare")
    handed = [r for r in rows if "hand" in r]
    if handed:
        s["hand_all"] = _rates(handed, "hand")
        s["rocky_on_hand_rows"] = _rates(handed, "rocky")
        for c in draft.CONFIDENCE:
            s[f"hand_{c}"] = _rates([r for r in handed if r["hand_conf"] == c], "hand")
        rh, hh = s["rocky_High"]["same_rate"], s["hand_High"]["same_rate"]
        s["pass_mark"] = "Rocky's High rows grade 'same' at least as often as the hand run's"
        s["passed"] = rh is not None and hh is not None and rh >= hh
        s["hand_wording_match"] = sum(1 for r in handed if r.get("hand_sim", 0) >= learn.ACCEPTED)
    s["rocky_wording_match"] = sum(1 for r in rows if r["rocky_sim"] >= learn.ACCEPTED)
    return s


def _line(name: str, r: dict) -> str:
    if not r or not r["rows"]:
        return f"  {name:28s}    0 rows"
    pct = lambda x: "n/a" if x is None else f"{x * 100:.0f}%"  # noqa: E731
    return (f"  {name:28s} {r['rows']:4d} rows | same {r['same']:3d} ({pct(r['same_rate'])}) "
            f"partial {r['partial']:3d} different {r['different']:3d} wrong {r['wrong']:3d} "
            f"none {r['none']:3d} | same+partial {pct(r['same_or_partial_rate'])}")


def format_report(label: str, s: dict, run_usage: dict, judge_usage: dict,
                  rows: list[dict]) -> str:
    L = [f"Time entries drafting backtest: {label}",
         f"Scored rows (James has since filled the entry): {s['scored']}"]
    if s["outcomes"]:
        L.append("Outcomes against the later export: "
                 + ", ".join(f"{k} {v}" for k, v in sorted(s["outcomes"].items())))
    L += ["", "Judge grades against James's final narrative:"]
    for c in draft.CONFIDENCE:
        L.append(_line(f"Rocky, rows it called {c}", s.get(f"rocky_{c}")))
    L.append(_line("Rocky, all rows", s["rocky_all"]))
    L.append(_line("Rocky before verification", s["first_all"]))
    L.append(_line("  (first draft's High rows)", s["first_High"]))
    if "compare_all" in s:
        L.append("")
        for c in draft.CONFIDENCE:
            L.append(_line(f"Compared run, rows it called {c}", s.get(f"compare_{c}")))
        L.append(_line("Compared run, all rows", s["compare_all"]))
        L.append(_line("This run on the same rows", s["rocky_on_compare_rows"]))
    if "hand_all" in s:
        L.append("")
        for c in draft.CONFIDENCE:
            L.append(_line(f"Hand run, rows it called {c}", s.get(f"hand_{c}")))
        L.append(_line("Hand run, all rows", s["hand_all"]))
        L.append(_line("Rocky on the same rows", s["rocky_on_hand_rows"]))
        L.append("")
        L.append(f"Wording match (>= {learn.ACCEPTED}): hand {s['hand_wording_match']}, "
                 f"Rocky {s['rocky_wording_match']}. James pastes hand drafts, so the hand run "
                 f"wins this by construction where he did.")
        L.append(f"Pass mark: {s['pass_mark']} -> " + ("PASSED" if s["passed"] else "NOT MET"))
    L.append("")
    L.append(f"Cost: drafting ${run_usage['cost_usd']} ({run_usage['calls']} calls), "
             f"judge ${judge_usage['cost_usd']}")
    bad = [r for r in rows if r["rocky_conf"] == "High"
           and r.get("grade_rocky") in ("different", "wrong")]
    if bad:
        L += ["", f"Rocky High rows graded different or wrong ({len(bad)}):"]
        for r in bad:
            L.append(f"  {r['date']} {r['matter']} {r['hours']}h [{r['grade_rocky']}: "
                     f"{r.get('why_rocky', '')}]")
            L.append(f"      Rocky: {r['rocky'][:200]}")
            L.append(f"      James: {r['final'][:200]}")
            if r.get("hand"):
                L.append(f"      Hand ({r.get('grade_hand')}): {r['hand'][:200]}")
    return "\n".join(L)


# =============================================================================
# Seeding the links table from hand runs
# =============================================================================

def hand_recs(hand: list[dict], emails: list[dict]) -> list[dict]:
    """Hand-run rows as recommendations, each Basis resolved to the emails it
    cites (same day through three days later, for the weekend batch)."""
    by_date: dict[str, list] = {}
    for e in emails:
        by_date.setdefault(e["date"], []).append(e)
    seen = collections.Counter()
    recs = []
    for h in hand:
        k = (h["date"], h["matter"], float(h["hours"]))
        d0 = date.fromisoformat(h["date"])
        nb = backtest.norm(h["basis"])
        cited = backtest.cited_subjects(h["basis"])
        ids = []
        for off in range(-1, 4):
            for e in by_date.get((d0 + timedelta(off)).isoformat(), []):
                if backtest.basis_hit(e, h["basis"], nb, cited) and e["id"] not in ids:
                    ids.append(e["id"])
        recs.append({"kind": "draft", "work_date": h["date"], "matter": h["matter"],
                     "hours": float(h["hours"]), "ordinal": seen[k],
                     "narrative": h["narrative"], "confidence": h["confidence"],
                     "basis": h["basis"], "basis_ids": ids, "start": ""})
        seen[k] += 1
    return recs
