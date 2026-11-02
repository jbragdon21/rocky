"""The deterministic diff: what happened to each recommendation.

TIME_ENTRIES_DESIGN.md, "1. Learn from the last round", without the
learning call (that is phase 4). Given what Rocky recommended for an export
and a later export of the same dates, each recommendation gets an outcome:

  accepted        the submitted narrative matches the draft (similarity >= 0.9)
  edited          same substance, changed wording (>= 0.5)
  rewritten       James wrote something different
  review_filled   Rocky said REVIEW and James filled the entry
  still_blank     James hasn't gotten to it
  rehoured        same date and matter, different hours
  recoded         gone from its matter; an entry with the same date and hours
                  and a similar narrative turned up on another matter
  deleted         gone
  uncaptured_taken / uncaptured_ignored
                  for an uncaptured suggestion: a new entry on that matter did
                  or did not appear within a day

Similarity is difflib's ratio on lowercased text. It is a wording measure:
"edited" and "rewritten" say nothing about substance, which is what the
learning call (and the phase 2 judge) are for.

links_for turns outcomes into the confirmed email-to-matter links the
design specifies. Only `submitted` links count as ground truth.
"""

import collections
import difflib
import re
from datetime import date, timedelta

ACCEPTED = 0.9
EDITED = 0.5


def norm_narr(s: str | None) -> str:
    s = re.sub(r"\s+", " ", (s or "").lower()).strip()
    return s.rstrip(" ;.")


def similarity(a: str | None, b: str | None) -> float:
    x, y = norm_narr(a), norm_narr(b)
    if not x or not y:
        return 0.0
    return round(difflib.SequenceMatcher(None, x, y).ratio(), 3)


def text_outcome(draft: str | None, final: str | None) -> str:
    s = similarity(draft, final)
    return "accepted" if s >= ACCEPTED else "edited" if s >= EDITED else "rewritten"


def ordinals(entries: list[dict]) -> dict:
    """row -> 0-based position among entries with the same (date, matter,
    hours), in file order. Two identical blanks are two entries."""
    seen = collections.Counter()
    out = {}
    for e in sorted(entries, key=lambda e: e.get("row") or 0):
        k = (e["work_date"], e["matter"], e.get("hours"))
        out[e.get("row")] = seen[k]
        seen[k] += 1
    return out


def _filled(narr: str | None, start: str | None, max_narr: int) -> bool:
    n = (narr or "").strip()
    return len(n) >= max_narr and n != (start or "").strip()


def diff(recs: list[dict], early: list[dict], final: list[dict],
         max_narr: int = 35) -> list[dict]:
    """One outcome per recommendation, in order.

    recs: dicts with kind, work_date, matter, hours, ordinal, narrative,
    confidence, and `start` (James's partial text, if any).
    early: the export the recommendations were drafted from.
    final: the later export. Each final entry is matched at most once."""
    by_key = collections.defaultdict(list)
    for e in sorted(final, key=lambda e: e.get("row") or 0):
        by_key[(e["work_date"], e["matter"], e.get("hours"))].append(e)
    used: set = set()
    out: list[dict | None] = [None] * len(recs)

    def result(i, outcome, fe=None, sim=None, text=None):
        if fe is not None:
            used.add(id(fe))
        out[i] = {"outcome": outcome, "text_outcome": text, "similarity": sim,
                  "final": fe,
                  "final_matter": fe["matter"] if fe else None,
                  "final_hours": fe.get("hours") if fe else None,
                  "final_narrative": fe.get("narrative") if fe else None}

    def settle(i, r, fe, outcome=None):
        start = r.get("start")
        if not _filled(fe.get("narrative"), start, max_narr):
            return result(i, outcome or "still_blank", fe)
        sim = similarity(r.get("narrative"), fe.get("narrative"))
        txt = text_outcome(r.get("narrative"), fe.get("narrative"))
        if outcome is None:
            outcome = "review_filled" if r.get("confidence") == "REVIEW" else txt
        result(i, outcome, fe, sim, txt)

    drafts = [(i, r) for i, r in enumerate(recs) if r["kind"] == "draft"]
    # Pass 1: exact (date, matter, hours, ordinal).
    for i, r in drafts:
        cands = by_key.get((r["work_date"], r["matter"], r.get("hours")), [])
        k = r.get("ordinal") or 0
        if k < len(cands) and id(cands[k]) not in used:
            settle(i, r, cands[k])
    # Pass 2: same date and matter, other hours.
    for i, r in drafts:
        if out[i] is not None:
            continue
        pool = [e for e in final if e["work_date"] == r["work_date"]
                and e["matter"] == r["matter"] and id(e) not in used]
        if pool:
            settle(i, r, pool[0], outcome="rehoured")
    # Pass 3: another matter, same date and hours, similar narrative.
    for i, r in drafts:
        if out[i] is not None:
            continue
        pool = [e for e in final if e["work_date"] == r["work_date"]
                and e.get("hours") == r.get("hours") and e["matter"] != r["matter"]
                and id(e) not in used and _filled(e.get("narrative"), None, max_narr)]
        best = max(pool, key=lambda e: similarity(r.get("narrative"), e.get("narrative")),
                   default=None)
        if best is not None and similarity(r.get("narrative"), best.get("narrative")) >= EDITED:
            settle(i, r, best, outcome="recoded")
        else:
            result(i, "deleted")

    # Uncaptured: a final entry that the early export didn't have, on the
    # suggested matter, within a day.
    early_n = collections.Counter((e["work_date"], e["matter"], e.get("hours")) for e in early)
    new = []
    for k, es in by_key.items():
        new += [e for e in es[early_n.get(k, 0):] if id(e) not in used]
    for i, r in enumerate(recs):
        if r["kind"] != "uncaptured":
            continue
        try:
            d0 = date.fromisoformat(r["work_date"])
        except ValueError:
            result(i, "uncaptured_ignored")
            continue
        days = {(d0 + timedelta(k)).isoformat() for k in (-1, 0, 1)}
        hit = next((e for e in new if e["matter"] == r["matter"] and e["work_date"] in days
                    and id(e) not in used), None)
        if hit is not None:
            result(i, "uncaptured_taken", hit,
                   similarity(r.get("narrative"), hit.get("narrative")),
                   text_outcome(r.get("narrative"), hit.get("narrative")))
        else:
            result(i, "uncaptured_ignored")
    return out


def links_for(rec: dict, out: dict, mail_by_id: dict, src_file: str | None = None,
              recommendation_id: int | None = None) -> list[dict]:
    """The links an outcome writes (design, "Each outcome writes rows this way").

    accepted / edited, and a rehoured row whose text was accepted or edited:
    each basis email is confirmed for the entry's matter. uncaptured_taken:
    confirmed for the new entry. recoded: rejected for the old matter.
    Everything else writes nothing; a deleted entry may be non-billable work
    on the right matter, so deletion proves nothing about the email."""
    o = out["outcome"]
    fe = out.get("final")
    if o in ("accepted", "edited") or (o == "rehoured" and out.get("text_outcome")
                                       in ("accepted", "edited")):
        verdict, d, mn = "confirmed", rec["work_date"], rec["matter"]
    elif o == "uncaptured_taken" and fe is not None:
        verdict, d, mn = "confirmed", fe["work_date"], fe["matter"]
    elif o == "recoded":
        verdict, d, mn = "rejected", rec["work_date"], rec["matter"]
    else:
        return []
    links = []
    for mid in rec.get("basis_ids") or []:
        e = mail_by_id.get(mid) or {}
        links.append({
            "work_date": d, "matter": mn,
            "entry_id": (fe or {}).get("entry_id"),
            "snapshot_id": (fe or {}).get("snapshot_id"),
            "src_file": src_file, "recommendation_id": recommendation_id,
            "internet_message_id": mid,
            "conversation_id": e.get("conversation_id"),
            "sent_at": e.get("sent_at"), "subject": e.get("subject"),
            "verdict": verdict, "source": "submitted", "outcome": o,
            "note": rec.get("link_note"),
        })
    return links


def tally(outs: list[dict]) -> dict:
    return dict(collections.Counter(o["outcome"] for o in outs if o))
