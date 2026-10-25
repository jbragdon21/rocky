"""Time-entry / sent-mail correlator, ported from the folder's correlate3.py.

Ranks candidate emails for each blank or partial entry. It does not draft
anything: the drafting call (phase 2) reads the day's mail, and these
rankings tell it where to look first. The eleven rules are in the Time
Entries folder's LEARNED_RULES.md; the numbers in comments refer to them.

With `window_batch_hour`, `window_pool_days` and `weight_matter_number` at 0
this reproduces correlate3.py's candidates exactly (checked on 257 entries
against corpus.db). The defaults differ in two ways, after the 10/25
backtest: the next morning's mail before 9 AM is searched with the entry's
own day, and a matter number written in the mail scores. The other inputs
changed too: mail comes from Minotaur's brain,
matter data from the reference workbook plus the overlay, and weights and
windows from time_entries_settings.json.
"""

import re
from datetime import date, timedelta

GENERIC = set("""claim class action lawsuit market property management general street avenue meeting call the
and of at on in a an llc inc lp llp company co apartments apartment residences residence house homes home
place park plaza tower towers court square center centre village station point view city town north south
east west new old one two three st ave rd dr blvd for re fw fwd accepted declined canceled copy tentative
flats lofts""".split())

# Rule 3 guardrail: common given names / surnames that must never become scoring tokens.
COMMON = set("""kelly joseph square beacon annapolis bozzuto james john david michael mary robert william
richard thomas charles christopher daniel matthew anthony donald mark paul steven andrew joshua kenneth
kevin brian george timothy ronald jason edward jeffrey ryan jacob gary nicholas eric jonathan stephen
larry justin scott brandon benjamin samuel gregory alexander patrick frank raymond jack dennis jerry tyler
aaron jose adam nathan henry douglas zachary peter kyle walter ethan jeremy harold carl keith roger gerald
christian terry sean arthur austin noah lawrence jesse joe bryan billy jordan albert dylan bruce willie
gabriel logan alan juan wayne roy ralph randy eugene vincent russell elijah louis bobby philip johnny
state either green harris white brown smith jones miller wilson davis moore taylor anderson thomas jackson
solaire novel broad halstead monroe kristie christina christine""".split())

# Rule 9: tokens that must never score bare, regardless of source.
COLLISION_BARE = {"halstead", "moore", "novel", "solaire", "broad", "monroe", "kristie"}
COLLISION_EXEMPT = {"006104-1289": {"halstead"}}

ALIAS_COL = "Aliases & Search Tokens (pipe-separated)"
CASE_NAME_COL = "Matter / Case Name"
NO_EMAIL_COL = "Typically No Sent Email"


# ---------------------------------------------------------------- Rule 8
def hay_has(hay: str, phrase: str, short_len: int = 4) -> bool:
    """Substring match, but word-boundary-anchored for short tokens.
    'HRI' matched 303 emails inside Christina/Christine; '465', '727',
    '100', '130' behave the same way."""
    if len(phrase) <= short_len:
        return re.search(r"(?<![A-Za-z0-9])" + re.escape(phrase)
                         + r"(?![A-Za-z0-9])", hay) is not None
    return phrase in hay


def toks(s) -> list[str]:
    return re.findall(r"[A-Za-z0-9&+']+", (s or "").lower())


def distinctive(name, settings: dict) -> list[tuple[str, int]]:
    if not name:
        return []
    wp, ww = settings["weight_phrase"], settings["weight_word"]
    n = re.sub(r"\((?:[A-Z]{2}|[A-Za-z ]+)\)", "", str(name))
    n = re.sub(r"-\s*General Property Management.*$", "", n)
    n = re.sub(r"\bb?\d{6,}\b", "", n)
    n = re.sub(r"\(formerly[^)]*\)", "", n)
    n = n.strip(" -–,")
    words = toks(n)
    core = [w for w in words if w not in GENERIC and len(w) > 2]
    out = []
    if len(core) >= 2:
        out.append((" ".join(core), wp))
    for w in core:
        out.append((w, ww))
    if n and len(n.split()) > 1 and core:
        # Keep the full phrase only when it carries a distinctive word. Some
        # names are stored inverted ("View, The"), which otherwise yields the
        # token "view, the" and matches unrelated mail.
        out.append((n.lower(), wp))
        m = re.match(r"^(.*),\s*(the|a|an)$", n.strip(), re.I)
        if m:
            out.append(("%s %s" % (m.group(2).lower(), m.group(1).lower()), wp))
    return out


def matter_tokens(mn: str, matter_name: str, props: dict, notes: dict,
                  m2n: dict, settings: dict) -> dict[str, int]:
    t: dict[str, int] = {}
    p = props.get(mn, {})
    for src in (p.get("property"), p.get("matter_name"), matter_name):
        for ph, w in distinctive(src, settings):
            t[ph] = max(t.get(ph, 0), w)
    nt = notes.get(mn)
    if nt:
        for a in str(nt.get(ALIAS_COL) or "").split("|"):
            a = a.strip().lower()
            if len(a) > 2:
                w = (settings["weight_alias_phrase"] if " " in a
                     else settings["weight_alias_word"])
                t[a] = max(t.get(a, 0), w)
        for ph, w in distinctive(nt.get(CASE_NAME_COL), settings):
            t[ph] = max(t.get(ph, 0), w)
    for part in re.split(r"[;,]", str(p.get("contacts") or "")):
        part = re.sub(r"\([^)]*\)", "", part).strip()
        if not part:
            continue
        ln = part.split()[-1].lower()
        if len(ln) > 3 and ln not in GENERIC:
            t.setdefault(ln, settings["weight_contact"])
    for nm, w in m2n.get(mn, []):                                # Rule 3
        t[nm] = max(t.get(nm, 0), w)
    # Rule 1 (learned aliases) is curated by hand into the Matter Notes
    # sheet or the overlay; automatic harvesting mapped at&t to a property
    # literally named "Billing" and stays off.
    exempt = COLLISION_EXEMPT.get(mn, set())                     # Rule 9
    return {k: v for k, v in t.items() if k not in (COLLISION_BARE - exempt)}


def windows(d0: str, settings: dict) -> list[tuple[str, list[tuple[str, int, int | None]]]]:
    """Rule 7 (±3 days, distance-penalized) and Rule 6 (Sunday entries are
    week backfills, so they reach forward across the week).

    Each stage is a label and its (date, penalty, before_hour) days; a day
    with a before_hour only counts mail sent earlier than that hour, Eastern.
    The search stops at the first stage with a candidate, so on a busy day
    six weak same-day matches used to fill the list before the next morning's
    07:xx batch was ever read (the 10/25 backtest). Two settings widen the
    first stage:
      * window_batch_hour: the next morning's mail before this hour joins the
        entry's own day, unpenalized. 0 turns it off.
      * window_pool_days: whole neighboring days join it, each penalized by
        window_pool_penalty.
    With both at 0 this is correlate3's same-day-first search."""
    d = date.fromisoformat(d0)
    pool, batch = settings["window_pool_days"], settings["window_batch_hour"]
    pool_pen = settings["window_pool_penalty"]
    near, far = settings["window_near_days"], settings["window_far_days"]

    def ring(lo: int, hi: int, pen: int) -> list[tuple[date, int, None]]:
        ks = range(lo, hi + 1)
        return ([(d - timedelta(k), pen, None) for k in ks]
                + [(d + timedelta(k), pen, None) for k in ks])

    first = [(d, 0, None)]
    if batch and pool < 1:
        first.append((d + timedelta(1), 0, batch))
    first += ring(1, pool, pool_pen)
    label = "same-day"
    if pool:
        label += f" ±{pool}d"
    elif batch:
        label += f" + next day before {batch}:00"
    stages = [(label, first)]
    if near > pool:
        stages.append((f"±{near}d", ring(pool + 1, near, 1)))
    if far > max(near, pool):
        stages.append((f"±{far}d", ring(max(near, pool) + 1, far, 2)))
    if d.weekday() == 6:
        lo, hi = settings["sunday_forward_from"], settings["sunday_forward_to"]
        stages.append(("week-forward(Sun backfill)",
                       [(d + timedelta(k), 3, None) for k in range(lo, hi + 1)]))
    return [(lbl, [(x.isoformat(), p, h) for x, p, h in ds]) for lbl, ds in stages]


def _sent_hour(e: dict) -> int:
    try:
        return int(str(e.get("sent_at") or "")[11:13])
    except ValueError:
        return 0


def matter_number_re(mn: str):
    """The matter number the way it gets written in mail: "pay this under
    6104-1524", "006104-1524", "the c/m is 6104-881". Leading zeros on
    either half are optional. None for a blank or odd-shaped matter."""
    m = re.fullmatch(r"(\d+)-(\d+)", (mn or "").strip())
    if not m:
        return None
    client = m.group(1).lstrip("0") or "0"
    number = m.group(2).lstrip("0") or "0"
    return re.compile(rf"(?<!\d)0*{client}\s?[-–.]\s?0*{number}(?!\d)")


def mail_window(entries: list[dict], settings: dict) -> tuple[str, str]:
    """The Eastern date range of mail correlation needs for these entries:
    every candidate window, plus the Rule 4 lookback."""
    dates = sorted(e["work_date"] for e in entries)
    lo = date.fromisoformat(dates[0]) - timedelta(days=settings["gap_lookback_days"])
    hi = date.fromisoformat(dates[-1]) + timedelta(
        days=max(settings["window_far_days"], settings["window_pool_days"],
                 settings["sunday_forward_to"]))
    return lo.isoformat(), hi.isoformat()


def needs_work(entry: dict, max_narr: int) -> bool:
    return len((entry.get("narrative") or "").strip()) < max_narr


def correlate(entries: list[dict], emails: list[dict], props: dict,
              notes: dict, m2n: dict, settings: dict) -> list[dict]:
    """One result per entry, in entry order. Each email needs date, hay,
    subject, to, body, inv and id (see mail.py)."""
    short = settings["short_token_len"]
    by_date: dict[str, list[dict]] = {}
    for e in emails:
        by_date.setdefault(e["date"], []).append(e)

    results = []
    for ent in entries:
        mn = ent["matter"]
        d = ent["work_date"]
        t = matter_tokens(mn, ent.get("matter_name") or "", props, notes, m2n,
                          settings) if mn else {}
        w_num = settings["weight_matter_number"]
        num_re = matter_number_re(mn) if w_num else None
        cands, stage = [], None
        for lbl, days in windows(d, settings):
            for dt, pen, before_hour in days:
                for e in by_date.get(dt, []):
                    if before_hour is not None and _sent_hour(e) >= before_hour:
                        continue
                    sc, hits = 0, []
                    for ph, w in t.items():
                        if hay_has(e["hay"], ph, short):
                            sc += w
                            hits.append(ph)
                    if num_re is not None and num_re.search(e["hay"]):
                        sc += w_num
                        hits.append("#" + mn)
                    if sc > 0:
                        cands.append({
                            "score": sc - pen, "date": dt, "id": e["id"],
                            "sent_at": e.get("sent_at"),
                            "conversation_id": e.get("conversation_id"),
                            "file": e.get("file"),
                            "subj": e.get("subject") or e.get("file") or "",
                            "to": (e.get("to") or "")[:100], "hits": hits,
                            "inv": e["inv"],
                            "body": " ".join((e.get("body") or "").split())[:400],
                        })
            if cands:
                stage = lbl
                break
        cands.sort(key=lambda x: -x["score"])

        # Rule 4: a distinctive token with zero hits anywhere in the loaded
        # mail. With Minotaur's complete Sent Items this usually means a
        # missing alias, where the .msg exports usually meant an export gap.
        gap = None
        if not cands and t:
            best = max(t, key=lambda k: t[k])
            if not any(hay_has(e["hay"], best, short) for e in emails):
                gap = best

        results.append({
            "row": ent.get("row"), "date": d, "hours": ent.get("hours"),
            "matter": mn, "matter_name": ent.get("matter_name"),
            "client": ent.get("client"), "narr": ent.get("narrative") or "",
            "stage": stage,
            "tokens": sorted(t, key=lambda k: -t[k])[:14],
            "cands": cands[:settings["max_candidates"]],
            "zero_hit_token": gap,
            "noemail": (notes.get(mn, {}) or {}).get(NO_EMAIL_COL),
        })
    return results
