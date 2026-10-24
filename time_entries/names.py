"""Resident / counterparty name index (Rule 3), ported from build_names.py.

Harvests person names and unit numbers from James's accepted narratives
and maps each to its matter. Guardrails beat coverage here: a wrong name
injects a false basis at weight 2-3, which is worse than no match at all.
"""

import collections
import re

STOP = set("""respond prepare review follow up regarding call with clients client discuss draft revise
finalize submit process outline analyze confirm email emails send next steps and the for re to of from
strategy response notice agreement invoice invoices issues issue counsel owner owners ownership same
claim class action lawsuit status further attend coordinate schedule advise research generate provide
motion complaint answer discovery deposition subpoena hearing trial settlement demand letter update
lease amendment consent default judgment court order brief memo memorandum notices filings prepare
monday tuesday wednesday thursday friday saturday sunday january february march april may june july
august september october november december general property management llc inc lp company co apartments
residences resident residents unit units apt suite building buildings site team teams
dc va md ca il ma ny nj pa wi nh ct mn fl ga tx co az nc sc tn oh mi wa or nv ut
ag oag hud ohr mccr fhc realpage bozzuto bmc gallagher pma nda msa
either way makes sense one two three four five all also any per via new old their there this that
non renewal nonrenewal cure violation violations smoking pet pets fire water damage mold
close file open matter status check follow-up""".split())

_NAME_RE = re.compile(r"\b([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){0,2})\b")
_UNIT_RE = re.compile(r"\((\d{3,4}[A-Za-z]?)\)|\b#(\d{3,4})\b|\b([A-Z]\d{3,4})\b")


def build_index(rows) -> tuple[dict, dict]:
    """rows: iterable of (matter, narrative). Returns (name_index, unit_index)
    in build_names.py's JSON shapes:
      name_index[name] = {"matter", "n", "tot", "amb"}
      unit_index[unit] = {matter: count}"""
    name2m = collections.defaultdict(collections.Counter)
    unit2m = collections.defaultdict(collections.Counter)
    for matter, narr in rows:
        if not matter or not narr:
            continue
        txt = narr.replace("&amp;", "&")
        for m in _NAME_RE.finditer(txt):
            cand = m.group(1).strip(" ,.;:'-")
            # "The View, the resident..." would otherwise yield "view, the".
            if any(ch in cand for ch in ',;:.()/"'):
                continue
            words = [w for w in cand.split() if w.lower() not in STOP]
            if not words:
                continue
            for w in words:
                if len(w) >= 4 and w.lower() not in STOP:
                    name2m[w.lower()][matter] += 1
            if len(words) >= 2:
                name2m[" ".join(words).lower()][matter] += 1
        for m in _UNIT_RE.finditer(txt):
            u = m.group(1) or m.group(2) or m.group(3)
            if u:
                unit2m[u][matter] += 1
    idx = {}
    for nm, c in name2m.items():
        tot = sum(c.values())
        top, tc = c.most_common(1)[0]
        if tc / tot >= 0.75:
            idx[nm] = {"matter": top, "n": tc, "tot": tot, "amb": len(c)}
    return idx, {k: dict(v) for k, v in unit2m.items()}


def scoring_names(index: dict, props: dict, common: set, settings: dict) -> dict:
    """matter -> [(name, weight)] the scorer adds, after the guardrails:
    ambiguous names, common names, names under five letters, and names the
    property scoring already covers are all dropped."""
    m2n = collections.defaultdict(list)
    for nm, v in index.items():
        mn = v["matter"]
        if v.get("amb", 1) > 1:
            continue
        if nm in common or len(nm) < 5:
            continue
        p = props.get(mn, {})
        blob = (str(p.get("property") or "") + " "
                + str(p.get("matter_name") or "")).lower()
        if nm in blob:
            continue
        w = settings["weight_name_phrase"] if " " in nm else settings["weight_name_word"]
        m2n[mn].append((nm, w))
    return m2n
