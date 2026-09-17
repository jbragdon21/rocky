"""
MF Case Brain — Stage 0: observe, predict, ask. Never act.
===========================================================

    python rocky.py --mf-brain --scan [--dry-run] [--local]   (daily, ~6 AM)
    python rocky.py --mf-brain --status
    python rocky.py --mf-brain --cases [--type T] [--needs-review]

A per-matter view of the multifamily landlord-tenant practice, built
from sources that each know part of the truth. Stage 0 writes nothing
outside its own ledger: no spreadsheet edits, no mail moves, no folder
changes. It watches, records what it *would* have concluded, and asks
about what it cannot settle.

**Why "observe and predict" rather than "observe and report."** A
divergence report a human has to grade costs James time on every line. A
*prediction* with a resolution horizon grades itself: the brain says
"this ripe matter gets filed inside three weeks", and three weeks later
the spreadsheet answers. Most of the accuracy record accrues with nobody
doing anything, which leaves James's typed answers for the calls no
amount of data settles — identity, mostly, and judgment.

Sources, and what each knows:

    (a) PENDING LLT MATTERS.XLSX ... stage, ripe date, court date.
                                     EVICTIONS ONLY.
    (b) the Teams OneNote task lists . who owes what this week.
                                     Blocked on delegated Notes.Read.
    (c) James's inbox ............... what actually happened, first.
    (d) the Outlook folder tree ..... WHICH MATTERS EXIST AT ALL.

(d) is a source of case existence, not an index. 201 of the 391 live
matter folders never reach the spreadsheet, and 164 of those have no
system of record anywhere. Any code here that starts "for each row on
the sheet" re-creates the blind spot this exists to remove.

**There is no join key.** Client.Matter is populated on 6 of 1,060 sheet
rows, so identity is *scored* from property + unit + surname plus the
folder's ancestor path, never looked up. See ``resolve()``.

**Two levels, and why.** A *case* is a RESIDENT AT A UNIT — what the
folder tree models, one folder per resident. A *line* is one matter
under that case. 106 residents carry two concurrent matters at one unit
(a rent case and a smoking case), with separate stages, separate ripe
dates, resolving independently, so an archive acts on a line and not on
the resident. Line identity is carried across scans by similarity
pairing (``pair_lines``) because the matter type cannot be part of the
key: it lives in the Status cell, which is also the progress field, so
keying on it would make every status update look like a new matter.

Storage: <mf_tracker_root>\\Case Brain\\ — see llt_watch.tracker_root().
Everything is append-only; a correction is a new line, not an edit.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("rocky.mfbrain")

LEDGER_VERSION = 1

# Confidence floor for attaching an observation to a case, matching the
# Vault's. Below it the observation is parked and a question raised
# rather than guessed at — a wrong merge is far more expensive to unpick
# than an unresolved one.
CONFIDENCE_FLOOR = 0.75

# The folder branch that belongs to litigation_updater's Smartsheet, not
# here. Several of those are federal cases pacer_monitor already
# watches. The brain registers them so it can say "tracked elsewhere"
# and then leaves them alone.
FOREIGN_BRANCHES = {"__bozzuto insured/monitored litigation"}

# matter_type decides which rules may fire. A lease_exit matter absent
# from the eviction spreadsheet is CORRECT, not a finding, and
# forgetting that would bury every report in false positives.
MATTER_TYPES = (
    "eviction", "agency_complaint", "demand", "lease_exit",
    "discrimination", "habitability", "incident", "bankruptcy",
    "insured_litigation", "other",
)

# Ordered: first hit wins, so specific before general.
_TYPE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("agency_complaint", re.compile(
        r"(?i)\b(OHR|OAG|HUD complaint|civil rights|human rights|"
        r"attorney general|fair housing|DHCD)\b")),
    ("discrimination", re.compile(
        r"(?i)(discriminat|harass|retaliat|\bADA\b|"
        r"reasonable accommodation|\bRA request\b)")),
    ("bankruptcy", re.compile(r"(?i)bankrupt|chapter (7|13)|automatic stay")),
    ("lease_exit", re.compile(
        r"(?i)(release from lease|lease release|roommate release|"
        r"move.?out|early termination|buy.?out)")),
    ("incident", re.compile(
        r"(?i)(stalk|threat|assault|weapon|knife|\bgun\b|violence|"
        r"trespass|police|altercation|dog bite|injur)")),
    ("habitability", re.compile(
        r"(?i)(bed ?bug|mold|repair|habitab|water damage|leak|pest|"
        r"roach|\bheat\b|HVAC|elevator|storage)")),
    ("demand", re.compile(
        r"(?i)(demand letter|letter of demand|subrogation|"
        r"settlement demand)")),
]

MATTER_FOLDER_RE = re.compile(
    r"^([A-Z][A-Za-z'’\-]+(?:\s[A-Z][a-z]+)?),\s*([A-Z])")

# Stage vocabulary, from LLT_SHEET_STYLE_GUIDE.md. Used to tell
# pre-filing from filed without needing the style guide to be adopted
# first — these match what the sheet says TODAY, variants and all.
_FILED_RE = re.compile(
    r"(?i)\b(filed|filing|complaint filed|BOL|THO|FTPR|judgment|"
    r"awarded|writ|eviction|dismissed|hearing|trial|mediation)\b")
_PREFILE_RE = re.compile(
    r"(?i)(drafted|sent|drafting|reissu|to be sent|need to draft)")

# "Initial Hearing: 9/18/26 at 9:00am (26-7159)" and its variants.
_COURT_DATE_RE = re.compile(
    r"(?i)(hearing|trial|mediation|ex parte|conference|writ|eviction)"
    r"[^0-9]{0,20}(\d{1,2})[./](\d{1,2})[./](\d{2,4})")


# =============================================================================
# Paths
# =============================================================================

def _argv_value(flag: str) -> str | None:
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def get_paths(config: dict, data_dir: Path) -> dict:
    """Everything under the Multifamily Tracker's roof. One config key
    (mf_tracker_root) repoints the whole subsystem per machine."""
    import llt_watch
    root = llt_watch.tracker_root(config) / "Case Brain"
    return {
        "root": root,
        "cases": root / "cases.jsonl",
        "lines": root / "lines.jsonl",
        "observations": root / "observations.jsonl",
        "predictions": root / "predictions.jsonl",
        "questions": root / "questions.jsonl",
        "answers": root / "answers.jsonl",
        "folder_map": root / "folder_map.json",
        "aliases": root / "aliases.md",
        "learning": root / "learning.md",
        "state": root / "state.json",
        "needs_review": root / "_Needs Review",
        "local": data_dir / "mf_brain",
    }


# =============================================================================
# Append-only stores
# =============================================================================

def read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def append_jsonl(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _last_wins(rows: list[dict], key: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        k = r.get(key)
        if k:
            out[k] = r
    return out


def load_cases(paths: dict) -> dict[str, dict]:
    """cases.jsonl is append-only and LAST LINE WINS per MF-#####, so
    amending a case preserves what the brain used to believe."""
    return _last_wins(read_jsonl(paths["cases"]), "id")


def load_lines(paths: dict) -> dict[str, dict]:
    return _last_wins(read_jsonl(paths["lines"]), "line_id")


def load_state(paths: dict) -> dict:
    if paths["state"].exists():
        try:
            return json.loads(paths["state"].read_text("utf-8"))
        except ValueError:
            log.warning("[mf-brain] state.json unreadable — starting fresh")
    return {}


def save_state(paths: dict, state: dict) -> None:
    paths["root"].mkdir(parents=True, exist_ok=True)
    tmp = paths["state"].with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    import os
    os.replace(tmp, paths["state"])


def next_case_id(cases: dict[str, dict]) -> str:
    n = 0
    for cid in cases:
        m = re.fullmatch(r"MF-(\d+)", cid or "")
        if m:
            n = max(n, int(m.group(1)))
    return f"MF-{n + 1:05d}"


def observation_id(source: str, source_key: str, kind: str,
                   value: str, as_of: str) -> str:
    """Idempotent by content: a cursor that gets re-read cannot
    double-write, which is what lets cursors live anywhere."""
    blob = "|".join((source, source_key, kind, str(value), as_of or ""))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


# =============================================================================
# Normalization — shared with pending_llt's hard-won matching rules
# =============================================================================

def norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def surname(name: str | None) -> str:
    return norm((name or "").split(",")[0])


def _property_key(prop: str) -> str:
    """Borrows pending_llt's signature guard so 'Cloisters I' never
    merges with 'Cloisters II'. That module carries the scars from this
    exact problem; do not write a fourth matcher."""
    try:
        import pending_llt as pl
        return f"{norm(prop)}|{pl._property_signature(prop or '')}"
    except Exception:
        return norm(prop)


# =============================================================================
# matter_type
# =============================================================================

def classify_matter_type(*, on_sheet: bool, branch: str = "",
                         text: str = "") -> str:
    if norm(branch) and any(norm(b) == norm(branch) for b in FOREIGN_BRANCHES):
        return "insured_litigation"
    for kind, pat in _TYPE_PATTERNS:
        if pat.search(text or ""):
            return kind
    return "eviction" if on_sheet else "other"


# =============================================================================
# Identity resolution
# =============================================================================

def first_initial(name: str | None) -> str:
    """First initial only, not the given name.

    The surname alone is too coarse: roommates and a parent with an
    adult child share a surname at one unit, and the first live scan
    merged them into one case (986 lines out of 1,026 matters, with
    lines wrongly closing on a first run). The full given name is too
    strict the other way, because the folder tree and the spreadsheet
    disagree about spelling — "Banks, Jasmin" in the mailbox is
    "Banks, Jasmine" on the sheet. An initial separates John from Jane
    and still tolerates Jasmin from Jasmine."""
    parts = (name or "").split(",", 1)
    given = parts[1].strip() if len(parts) > 1 else ""
    m = re.search(r"[A-Za-z]", given)
    return m.group(0).lower() if m else ""


def case_signature(*, prop: str, name: str, unit: str) -> dict:
    return {"p": _property_key(prop), "s": surname(name),
            "f": first_initial(name), "u": norm(unit)}


def resolve(sig: dict, cases: dict[str, dict], folder_id: str | None = None,
            folder_map: dict | None = None) -> tuple[str | None, float, str]:
    """Best (case_id, confidence, why). Scored rather than keyed, because
    no source carries an ID. Anything under the floor becomes a question
    instead of a guess."""
    if folder_id and folder_map and folder_id in folder_map:
        return folder_map[folder_id], 1.0, "folder_map"
    best: tuple[str | None, float, str] = (None, 0.0, "no candidate")
    for cid, c in cases.items():
        csig = c.get("sig") or {}
        p = bool(csig.get("p")) and csig.get("p") == sig.get("p")
        s = bool(csig.get("s")) and csig.get("s") == sig.get("s")
        u = bool(csig.get("u")) and csig.get("u") == sig.get("u")
        # A differing first initial is a positive signal that these are
        # DIFFERENT people (roommates, a parent and an adult child), so
        # it vetoes a surname match outright rather than just scoring
        # lower. A missing initial on either side abstains.
        fa, fb = csig.get("f") or "", sig.get("f") or ""
        if fa and fb and fa != fb:
            s = False
        if p and s and u:
            score, why = 0.97, "property+unit+name"
        elif p and s:
            score, why = 0.88, "property+name"
        elif p and u:
            score, why = 0.80, "property+unit"
        elif s and u:
            score, why = 0.62, "name+unit (no property)"
        elif s:
            score, why = 0.40, "name only"
        else:
            continue
        if score > best[1]:
            best = (cid, score, why)
    return best


# =============================================================================
# Matter lines — the second level
# =============================================================================

LINE_FIELDS = ("status", "ripe_date", "next_steps", "subsidized",
               "vawa_notice", "vawa_complies", "client_matter")


def _line_from_row(r: dict) -> dict:
    return {
        "status": (r.get("Status") or "").strip(),
        "ripe_date": (r.get("Ripe Date") or "").strip(),
        "next_steps": (r.get("Next Steps") or "").strip(),
        "subsidized": (r.get("Subsidized.Non-subsidized")
                       or r.get("Subsidized/Non-subsidized") or "").strip(),
        "vawa_notice": (r.get("Notice Contains VAWA") or "").strip(),
        "vawa_complies": (r.get("Complies w.VAWA")
                          or r.get("Complies w/VAWA") or "").strip(),
        "client_matter": (r.get("Client.Matter")
                          or r.get("Client/Matter") or "").strip(),
    }


def pair_lines(old: list[dict], new: list[dict]) -> list[tuple[dict | None,
                                                               dict | None]]:
    """Match a case's previously-known matter lines to this scan's rows.

    Same shape as llt_watch._pair_rows and for the same reason: a
    resident with a rent case and a smoking case must keep each line's
    history attached to the right matter when one of them changes
    status. Pairs by how many fields still agree; a status update leaves
    every other field matching its own counterpart.

    Returns (old_or_None, new_or_None) pairs — a None on the left is a
    new line, a None on the right is a line that left the sheet.
    """
    if len(old) == 1 and len(new) == 1:
        return [(old[0], new[0])]
    scored = sorted(
        ((sum(1 for f in LINE_FIELDS if (o.get(f) or "") == (n.get(f) or "")),
          oi, ni)
         for oi, o in enumerate(old) for ni, n in enumerate(new)),
        key=lambda t: -t[0])
    used_o: set[int] = set()
    used_n: set[int] = set()
    pairs: list[tuple[dict | None, dict | None]] = []
    for _s, oi, ni in scored:
        if oi in used_o or ni in used_n:
            continue
        used_o.add(oi)
        used_n.add(ni)
        pairs.append((old[oi], new[ni]))
    pairs += [(None, n) for i, n in enumerate(new) if i not in used_n]
    pairs += [(o, None) for i, o in enumerate(old) if i not in used_o]
    return pairs


def next_line_id(case_id: str, lines: dict[str, dict]) -> str:
    n = 0
    for lid in lines:
        m = re.fullmatch(re.escape(case_id) + r"\.(\d+)", lid or "")
        if m:
            n = max(n, int(m.group(1)))
    return f"{case_id}.{n + 1}"


# =============================================================================
# Dates
# =============================================================================

def parse_ripe(raw: str) -> date | None:
    try:
        import pending_llt as pl
        return pl.parse_ripe_date(raw)
    except Exception:
        return None


def parse_court_date(next_steps: str) -> tuple[date | None, str]:
    """First court date in a Next Steps cell, plus the event word."""
    m = _COURT_DATE_RE.search(next_steps or "")
    if not m:
        return None, ""
    mo, d, y = int(m.group(2)), int(m.group(3)), int(m.group(4))
    y = y + 2000 if y < 100 else y
    try:
        return date(y, mo, d), m.group(1).title()
    except ValueError:
        return None, ""


def fmt_date(d: date) -> str:
    """M/D/YY without leading zeros. Written out rather than using the
    %-m strftime flag, which is glibc-only and raises on Windows — the
    Rocky laptop runs Windows."""
    return f"{d.month}/{d.day}/{d.year % 100:02d}"


def looks_filed(status: str, next_steps: str) -> bool:
    blob = f"{status} {next_steps}"
    return bool(_FILED_RE.search(blob)) and not (
        _PREFILE_RE.search(status or "") and not _FILED_RE.search(status or ""))


# =============================================================================
# Source (a) — the LLT spreadsheet
# =============================================================================

def _fetch_sheet(config: dict, token: str | None, local: bool):
    import llt_watch
    if local:
        import pending_llt as pl
        lp = pl.newest_local_llt()
        if lp is None:
            log.error("[mf-brain] no local LLT spreadsheet found")
            return None, ""
        return lp.read_bytes(), f"{lp.name} (local)"
    stat = llt_watch.stat_llt_file(token, config)
    if not stat:
        log.error("[mf-brain] could not reach the LLT spreadsheet")
        return None, ""
    return llt_watch.download_llt(token, config, stat), stat["path"]


def scan_spreadsheet(config: dict, cases: dict, lines: dict, *,
                     local: bool = False, token: str | None = None) -> dict:
    """Read the current revision tab; create/close matter lines and emit
    observations. Returns a work dict of things to append."""
    import llt_watch

    raw, src = _fetch_sheet(config, token, local)
    if not raw:
        return {"obs": [], "new_cases": [], "line_rows": []}
    sheet, _cols, rows = llt_watch.snapshot_rows(raw)
    log.info(f"[mf-brain] (a) {src}: sheet {sheet!r}, "
             f"{llt_watch.matter_count(rows)} matters")

    ts = datetime.now(timezone.utc).isoformat()
    obs: list[dict] = []
    new_cases: list[dict] = []
    line_rows: list[dict] = []

    # Two passes. The first resolves every resident group to a case and
    # COLLECTS its rows; the second pairs lines once per case.
    #
    # Doing it in one pass was wrong: llt_watch groups by full name, this
    # resolver is deliberately looser (it has to tolerate "Jasmin" vs
    # "Jasmine"), so two resident groups can legitimately land on one
    # case. Processing them independently made the second group pair
    # against the first group's lines and close them — 4 lines "closed"
    # on a first scan, when nothing existed to close.
    per_case: dict[str, list[dict]] = {}
    conf_of: dict[str, tuple[float, str]] = {}

    for lst in rows.values():
        prop = lst[0].get("_property") or lst[0].get("Property") or ""
        sig = case_signature(prop=prop, name=lst[0].get("Name", ""),
                             unit=lst[0].get("Unit", ""))
        cid, conf, why = resolve(sig, cases)
        if cid is None or conf < CONFIDENCE_FLOOR:
            cid = next_case_id(cases)
            rec = {
                "id": cid, "created": ts, "sig": sig,
                "label": f"{prop} — {lst[0].get('Name')}"
                         + (f" ({lst[0]['Unit']})" if lst[0].get("Unit") else ""),
                "property": prop, "name": lst[0].get("Name"),
                "unit": lst[0].get("Unit"), "client": lst[0].get("Client"),
                "matter_type": "eviction", "sources": ["llt_sheet"],
                "first_seen_by": "llt_sheet",
            }
            cases[cid] = rec
            new_cases.append(rec)
            conf, why = 1.0, "new"
        per_case.setdefault(cid, []).extend(lst)
        conf_of[cid] = (conf, why)

    for cid, group in per_case.items():
        conf, why = conf_of[cid]
        known = [l for l in lines.values()
                 if l.get("case") == cid and not l.get("closed")]
        fresh = [_line_from_row(r) for r in group]
        for old, new in pair_lines(known, fresh):
            if new is None:                       # line left the sheet
                rec = dict(old, closed=ts, last_seen=old.get("last_seen"),
                           close_reason="left_sheet")
                lines[rec["line_id"]] = rec
                line_rows.append(rec)
                continue
            if old is None:                       # a new matter line
                lid = next_line_id(cid, lines)
                rec = {"line_id": lid, "case": cid, "created": ts,
                       "last_seen": ts, "sheet": sheet,
                       "matter_type": classify_matter_type(
                           on_sheet=True,
                           text=f"{new['status']} {new['next_steps']}"),
                       **new}
                lines[lid] = rec
                line_rows.append(rec)
            else:
                lid = old["line_id"]
                changed = [f for f in LINE_FIELDS
                           if (old.get(f) or "") != (new.get(f) or "")]
                # Append only when something actually moved, so the store
                # grows with activity rather than with scans.
                if changed:
                    rec = dict(old, **new, last_seen=ts, sheet=sheet,
                               matter_type=classify_matter_type(
                                   on_sheet=True,
                                   text=f"{new['status']} {new['next_steps']}"))
                    lines[lid] = rec
                    line_rows.append(rec)

            for kind, field in (("stage", "status"), ("ripe_date", "ripe_date"),
                                ("next_step", "next_steps")):
                val = new.get(field) or ""
                if not val:
                    continue
                obs.append({
                    "obs_id": observation_id("llt_sheet", lid, kind, val, sheet),
                    "ts": ts, "source": "llt_sheet", "case": cid, "line": lid,
                    "confidence": round(conf, 2), "resolved_by": why,
                    "fact": {"kind": kind, "value": val, "as_of": sheet},
                })
    return {"obs": obs, "new_cases": new_cases, "line_rows": line_rows,
            "sheet": sheet}


# =============================================================================
# Source (d) — the Outlook folder tree
# =============================================================================

def walk_folders(token: str, mailbox: str, max_depth: int = 4) -> list[dict]:
    import requests
    import pending_llt as pl
    H = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    out: list[dict] = []

    def kids(fid):
        res, url = [], (
            f"{pl.GRAPH_API_BASE}/users/{mailbox}/mailFolders/{fid}"
            f"/childFolders?$top=200&$select=id,displayName,"
            f"totalItemCount,childFolderCount")
        while url:
            r = requests.get(url, headers=H, timeout=60)
            if r.status_code != 200:
                log.warning(f"[mf-brain] folder list {r.status_code}: "
                            f"{r.text[:160]}")
                return res
            j = r.json()
            res += j.get("value", [])
            url = j.get("@odata.nextLink")
        return res

    def rec(fid, depth, path):
        for f in kids(fid):
            p = path + [f["displayName"]]
            out.append({"id": f["id"], "name": f["displayName"], "path": p,
                        "depth": depth, "n": f.get("totalItemCount", 0),
                        "kids": f.get("childFolderCount", 0)})
            if f.get("childFolderCount") and depth < max_depth:
                rec(f["id"], depth + 1, p)

    root = [f for f in kids("msgfolderroot") if f["displayName"] == "Inbox"]
    if root:
        rec(root[0]["id"], 1, ["Inbox"])
    return out


def _unit_from_folder(name: str) -> str:
    """Conservative: the parenthetical is NOT reliably the unit.
    'Negron, Damon (5333)' is the PROPERTY (unit 704)."""
    tail = re.sub(r"^[^,]+,\s*[A-Za-z'’\.\- ]+", "", name).strip()
    m = re.match(r"^\(?\s*#?(\d{1,4}[A-Za-z\-]?)\s*[)\-–]", tail + "-")
    if m:
        return m.group(1)
    m = re.match(r"^#?(\d{1,4}[A-Za-z\-]?)$", tail)
    return m.group(1) if m else ""


def scan_folders(cases: dict, folder_map: dict, token: str,
                 mailbox: str) -> dict:
    tree = walk_folders(token, mailbox)
    log.info(f"[mf-brain] (d) {len(tree)} folders under Inbox")
    ts = datetime.now(timezone.utc).isoformat()
    obs, new_cases, unresolved = [], [], []

    for f in tree:
        if f["name"].startswith("_") or not MATTER_FOLDER_RE.match(f["name"]):
            continue
        branch = f["path"][1] if len(f["path"]) > 1 else ""
        prop = (f["path"][-2] if len(f["path"]) >= 2 else "").lstrip("_").strip()
        unit = _unit_from_folder(f["name"])
        sig = case_signature(prop=prop, name=f["name"], unit=unit)
        cid, conf, why = resolve(sig, cases, folder_id=f["id"],
                                 folder_map=folder_map)

        if cid and conf >= CONFIDENCE_FLOOR:
            folder_map[f["id"]] = cid
            obs.append({
                "obs_id": observation_id("folder", f["id"], "folder_link",
                                         "\\".join(f["path"]), ""),
                "ts": ts, "source": "folder", "case": cid,
                "confidence": round(conf, 2), "resolved_by": why,
                "fact": {"kind": "folder_link",
                         "value": "\\".join(f["path"]), "as_of": ""},
                "evidence": {"folder_id": f["id"], "messages": f["n"]},
            })
            continue

        # Only genuinely ambiguous folders go to the review queue.
        #
        # A bare surname match (0.40) is NOT ambiguity in a population of
        # 2,000 residents, it is coincidence: the first live run asked
        # James whether "Adams, Deborah" was "Adams, Durrel" and whether
        # "Chase, Ashley" was "Chase, Andrew". Queueing those buried the
        # one real find (a folder literally named "Baldwin, Shalaana
        # (stratos)" matching Stratos/Baldwin on the sheet) under noise.
        # Anything weaker than property+unit or name+unit becomes its own
        # case instead, which a later answer can still merge.
        if conf >= 0.60 and not (
                norm(branch) in {norm(b) for b in FOREIGN_BRANCHES}):
            unresolved.append({
                "folder_id": f["id"], "path": "\\".join(f["path"]),
                "name": f["name"], "messages": f["n"], "guess": cid,
                "guess_label": (cases.get(cid) or {}).get("label"),
                "confidence": round(conf, 2), "why": why,
                "property_guess": prop, "unit_guess": unit,
            })
            continue

        mtype = classify_matter_type(on_sheet=False, branch=branch,
                                     text=f["name"])
        cid = next_case_id(cases)
        rec = {"id": cid, "created": ts, "sig": sig,
               "label": f"{prop} — {f['name']}" if prop else f["name"],
               "property": prop, "name": f["name"], "unit": unit,
               "client": branch.lstrip("_").strip(), "matter_type": mtype,
               "sources": ["folder"], "first_seen_by": "folder",
               "folder_id": f["id"], "folder_path": "\\".join(f["path"])}
        cases[cid] = rec
        new_cases.append(rec)
        folder_map[f["id"]] = cid

    return {"obs": obs, "new_cases": new_cases, "unresolved": unresolved,
            "tree": tree}


# =============================================================================
# Source (c) — James's inbox
# =============================================================================

_MAIL_SYSTEM = """You are Rocky, reading a day's email in one \
landlord-tenant matter folder and extracting only what is factually \
established. Be conservative: this feeds a case ledger, and a wrong \
fact is worse than a missing one.

Return STRICT JSON:
{"facts":[{"kind":"...","value":"...","confidence":0.0-1.0}],
 "summary":"one sentence, plain English",
 "question": "one question for the attorney, or null"}

kind is one of: payment (resident paid or a balance changed),
court_date (a hearing/trial date was set, moved or held),
resolution (the matter resolved, settled, or the client withdrew),
move_out (the resident is leaving or has left),
escalation (counsel appeared, an agency complaint, a threat to sue),
document (a lease/ledger/notice was provided), instruction (the client
told us to do something), other.

Only report what the mail actually says. No inference about what should \
happen next. If nothing factual happened, return an empty facts list."""


def scan_inbox(config: dict, paths: dict, cases: dict, folder_map: dict,
               token: str, mailbox: str, state: dict, *,
               dry_run: bool = False) -> dict:
    """New mail since the last run, grouped by folder, one Claude call
    per folder that actually received something.

    ONE Graph query for the whole mailbox rather than 1,564 per-folder
    polls: messages carry parentFolderId, so grouping is free and the
    folders with no new mail cost nothing.
    """
    import requests
    import pending_llt as pl

    since = state.get("inbox_cursor")
    if not since:
        since = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        log.info(f"[mf-brain] (c) no cursor — first pass reads the last 2 days")
    H = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    url = (f"{pl.GRAPH_API_BASE}/users/{mailbox}/messages"
           f"?$filter=receivedDateTime ge {since[:19]}Z"
           f"&$select=id,subject,receivedDateTime,from,parentFolderId,"
           f"bodyPreview&$top=200&$orderby=receivedDateTime desc")
    msgs: list[dict] = []
    newest = since
    while url and len(msgs) < 1000:
        r = requests.get(url, headers=H, timeout=90)
        if r.status_code != 200:
            log.warning(f"[mf-brain] (c) mail query {r.status_code}: "
                        f"{r.text[:200]}")
            break
        j = r.json()
        msgs += j.get("value", [])
        url = j.get("@odata.nextLink")
    for m in msgs:
        if (m.get("receivedDateTime") or "") > newest:
            newest = m["receivedDateTime"]

    by_folder: dict[str, list[dict]] = {}
    for m in msgs:
        fid = m.get("parentFolderId")
        if fid in folder_map:
            by_folder.setdefault(fid, []).append(m)
    log.info(f"[mf-brain] (c) {len(msgs)} new message(s) since {since[:10]}; "
             f"{len(by_folder)} mapped folder(s) affected")

    ts = datetime.now(timezone.utc).isoformat()
    obs: list[dict] = []
    questions: list[dict] = []
    if by_folder and not dry_run:
        from anthropic import Anthropic
        from rocky import CLAUDE_MODEL
        client = Anthropic(api_key=config["anthropic_api_key"])
        for fid, group in by_folder.items():
            cid = folder_map[fid]
            label = (cases.get(cid) or {}).get("label", cid)
            digest = "\n\n".join(
                f"From: {(m.get('from') or {}).get('emailAddress', {}).get('address', '?')}\n"
                f"Date: {m.get('receivedDateTime', '')[:10]}\n"
                f"Subject: {m.get('subject', '')}\n"
                f"{(m.get('bodyPreview') or '')[:700]}"
                for m in group[:25])
            try:
                resp = client.messages.create(
                    model=CLAUDE_MODEL, max_tokens=900,
                    system=_MAIL_SYSTEM,
                    messages=[{"role": "user",
                               "content": f"Matter: {label}\n\n{digest}"}])
                text = "".join(b.text for b in resp.content
                               if getattr(b, "type", "") == "text")
                data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
            except Exception as e:
                log.warning(f"[mf-brain] (c) {label}: {e}")
                continue
            for fact in (data.get("facts") or []):
                val = str(fact.get("value") or "")[:400]
                if not val:
                    continue
                obs.append({
                    "obs_id": observation_id("inbox", fid,
                                             str(fact.get("kind")), val,
                                             group[0].get("receivedDateTime", "")),
                    "ts": ts, "source": "inbox", "case": cid,
                    "confidence": float(fact.get("confidence") or 0.5),
                    "resolved_by": "folder_map",
                    "fact": {"kind": str(fact.get("kind") or "other"),
                             "value": val,
                             "as_of": group[0].get("receivedDateTime", "")[:10]},
                    "evidence": {"folder_id": fid, "messages": len(group),
                                 "summary": data.get("summary")},
                })
            if data.get("question"):
                questions.append({"case": cid, "label": label,
                                  "kind": "mail",
                                  "text": str(data["question"])[:400]})
    return {"obs": obs, "questions": questions, "cursor": newest,
            "folders_touched": len(by_folder), "messages": len(msgs)}


# =============================================================================
# Predictions — the part that grades itself
# =============================================================================

def _pred_id(line_id: str, kind: str, basis: str) -> str:
    return hashlib.sha1(f"{line_id}|{kind}|{basis}".encode()).hexdigest()[:12]


def make_predictions(lines: dict, cases: dict,
                     existing: dict[str, dict]) -> list[dict]:
    """Expectations the world will answer on its own.

    Each carries a horizon and a basis. Nobody grades these by hand; the
    next scan past the horizon reads the ledger and marks them."""
    ts = datetime.now(timezone.utc).isoformat()
    today = date.today()
    out: list[dict] = []

    for lid, l in lines.items():
        if l.get("closed"):
            continue
        case = cases.get(l.get("case")) or {}
        if case.get("matter_type") == "insured_litigation":
            continue
        status, steps = l.get("status", ""), l.get("next_steps", "")

        # 1. A ripe, unfiled matter gets filed.
        rd = parse_ripe(l.get("ripe_date", ""))
        if rd and rd <= today and not looks_filed(status, steps):
            basis = f"ripe {rd.isoformat()}"
            pid = _pred_id(lid, "will_file", basis)
            if pid not in existing:
                out.append({
                    "pred_id": pid, "ts": ts, "case": l["case"], "line": lid,
                    "kind": "will_file", "status": "open",
                    "statement": f"ripe since {fmt_date(rd)} and not filed "
                                 f"— I expect a filing within 21 days",
                    "basis": basis,
                    "horizon": (today + timedelta(days=21)).isoformat(),
                })

        # 2. A scheduled court date holds.
        cd, event = parse_court_date(steps)
        if cd and cd > today:
            basis = f"{event} {cd.isoformat()}"
            pid = _pred_id(lid, "date_holds", basis)
            if pid not in existing:
                out.append({
                    "pred_id": pid, "ts": ts, "case": l["case"], "line": lid,
                    "kind": "date_holds", "status": "open",
                    "statement": f"{event} set for {fmt_date(cd)} — "
                                 f"I expect it to hold",
                    "basis": basis, "horizon": cd.isoformat(),
                })

        # 3. A long-ripe matter that never files resolves off the sheet.
        if rd and (today - rd).days > 90 and not looks_filed(status, steps):
            basis = f"stale ripe {rd.isoformat()}"
            pid = _pred_id(lid, "will_resolve", basis)
            if pid not in existing:
                out.append({
                    "pred_id": pid, "ts": ts, "case": l["case"], "line": lid,
                    "kind": "will_resolve", "status": "open",
                    "statement": f"ripe {(today - rd).days} days ago and still "
                                 f"unfiled — I expect this to leave the sheet "
                                 f"without a filing",
                    "basis": basis,
                    "horizon": (today + timedelta(days=60)).isoformat(),
                })
    return out


def score_predictions(preds: dict[str, dict], lines: dict) -> list[dict]:
    """Resolve every open prediction whose horizon has passed.

    A prediction can also come out *moot* — the line left the sheet for a
    reason the prediction was not about. Recording that separately keeps
    the accuracy rate honest rather than flattering."""
    today = date.today()
    settled: list[dict] = []
    for pid, p in preds.items():
        if p.get("status") != "open":
            continue
        try:
            horizon = date.fromisoformat(p.get("horizon", ""))
        except ValueError:
            continue
        l = lines.get(p.get("line"))
        kind = p.get("kind")
        verdict = None

        if kind == "will_file":
            if l and not l.get("closed") and looks_filed(
                    l.get("status", ""), l.get("next_steps", "")):
                verdict = "right"
            elif l and l.get("closed"):
                verdict = "moot"      # resolved instead of filed
            elif today >= horizon:
                verdict = "wrong"
        elif kind == "date_holds":
            if today >= horizon:
                if not l or l.get("closed"):
                    verdict = "moot"
                else:
                    cd, _ = parse_court_date(l.get("next_steps", ""))
                    basis_date = (p.get("basis") or "").split()[-1]
                    verdict = ("right" if cd and cd.isoformat() == basis_date
                               else "wrong")
        elif kind == "will_resolve":
            if l and l.get("closed"):
                verdict = "right"
            elif l and looks_filed(l.get("status", ""), l.get("next_steps", "")):
                verdict = "wrong"     # it filed after all
            elif today >= horizon:
                verdict = "wrong"

        if verdict:
            settled.append(dict(p, status=verdict,
                                settled_on=today.isoformat()))
    return settled


# =============================================================================
# The scan
# =============================================================================

def run_scan(config: dict, paths: dict, *, token: str | None = None,
             local: bool = False, dry_run: bool = False) -> dict:
    """One Stage 0 pass. Writes only to the brain's own ledger."""
    paths["root"].mkdir(parents=True, exist_ok=True)
    cases = load_cases(paths)
    lines = load_lines(paths)
    state = load_state(paths)
    preds = _last_wins(read_jsonl(paths["predictions"]), "pred_id")
    folder_map = {}
    if paths["folder_map"].exists():
        try:
            folder_map = json.loads(paths["folder_map"].read_text("utf-8"))
        except ValueError:
            log.warning("[mf-brain] folder_map.json unreadable — ignoring")
    before_cases, before_lines = len(cases), len(lines)

    a = scan_spreadsheet(config, cases, lines, local=local, token=token)
    d = {"obs": [], "new_cases": [], "unresolved": [], "tree": []}
    c = {"obs": [], "questions": [], "cursor": state.get("inbox_cursor"),
         "folders_touched": 0, "messages": 0}
    if token:
        mailbox = config.get("user_email", "jbragdon@gallagherllp.com")
        d = scan_folders(cases, folder_map, token, mailbox)
        c = scan_inbox(config, paths, cases, folder_map, token, mailbox,
                       state, dry_run=dry_run)
    else:
        log.info("[mf-brain] (c)/(d) skipped — no token (--local)")

    settled = score_predictions(preds, lines)
    for s in settled:
        preds[s["pred_id"]] = s
    fresh_preds = make_predictions(lines, cases, preds)

    seen = {o.get("obs_id") for o in read_jsonl(paths["observations"])}
    all_obs = a["obs"] + d["obs"] + c["obs"]
    fresh_obs = [o for o in all_obs if o.get("obs_id") not in seen]

    summary = {
        "sheet": a.get("sheet"),
        "cases_before": before_cases, "cases_after": len(cases),
        "new_cases": len(a["new_cases"]) + len(d["new_cases"]),
        "lines_before": before_lines, "lines_after": len(lines),
        "line_changes": len(a["line_rows"]),
        "lines_closed": sum(1 for r in a["line_rows"] if r.get("closed")),
        "observations_new": len(fresh_obs),
        "unresolved_folders": len(d["unresolved"]),
        "mail_messages": c["messages"], "mail_folders": c["folders_touched"],
        "predictions_new": len(fresh_preds),
        "predictions_settled": settled,
        "mail_questions": c["questions"],
        "by_type": {},
    }
    for cs in cases.values():
        t = cs.get("matter_type", "other")
        summary["by_type"][t] = summary["by_type"].get(t, 0) + 1

    if dry_run:
        log.info("[mf-brain] DRY-RUN — ledger untouched")
        return summary

    append_jsonl(paths["cases"], a["new_cases"] + d["new_cases"])
    append_jsonl(paths["lines"], a["line_rows"])
    append_jsonl(paths["observations"], fresh_obs)
    append_jsonl(paths["predictions"], settled + fresh_preds)
    paths["folder_map"].write_text(json.dumps(folder_map, indent=1),
                                   encoding="utf-8")
    if d["unresolved"]:
        paths["needs_review"].mkdir(parents=True, exist_ok=True)
        (paths["needs_review"] / "unresolved_folders.json").write_text(
            json.dumps(d["unresolved"], indent=1, ensure_ascii=False),
            encoding="utf-8")
    state["inbox_cursor"] = c["cursor"]
    state["last_scan"] = datetime.now(timezone.utc).isoformat()
    save_state(paths, state)
    return summary


# =============================================================================
# CLI
# =============================================================================

def run_status(config: dict, paths: dict) -> None:
    cases = load_cases(paths)
    lines = load_lines(paths)
    obs = read_jsonl(paths["observations"])
    preds = _last_wins(read_jsonl(paths["predictions"]), "pred_id")
    state = load_state(paths)
    open_l = [l for l in lines.values() if not l.get("closed")]
    print("MF Case Brain — Stage 0 (observe only)")
    print(f"  Ledger      : {paths['root']}")
    print(f"  Last scan   : {(state.get('last_scan') or 'never')[:19]}")
    print(f"  Cases       : {len(cases):,}")
    print(f"  Matter lines: {len(open_l):,} open of {len(lines):,}")
    print(f"  Observations: {len(obs):,}")
    by_v: dict[str, int] = {}
    for p in preds.values():
        by_v[p.get("status", "?")] = by_v.get(p.get("status", "?"), 0) + 1
    if by_v:
        graded = by_v.get("right", 0) + by_v.get("wrong", 0)
        rate = (f"{by_v.get('right', 0) / graded:.0%}" if graded else "n/a")
        print(f"  Predictions : " + ", ".join(
            f"{v} {k}" for k, v in sorted(by_v.items())) +
            f"   (accuracy {rate})")
    by_type: dict[str, int] = {}
    for cs in cases.values():
        by_type[cs.get("matter_type", "other")] = \
            by_type.get(cs.get("matter_type", "other"), 0) + 1
    if by_type:
        print("  By matter type:")
        for k, v in sorted(by_type.items(), key=lambda t: -t[1]):
            print(f"    {v:>6}  {k}")
    nr = paths["needs_review"] / "unresolved_folders.json"
    if nr.exists():
        try:
            print(f"  Needs review: {len(json.loads(nr.read_text('utf-8')))} "
                  f"folder(s) below the {CONFIDENCE_FLOOR} floor")
        except ValueError:
            pass


def run_cli(config: dict, data_dir: Path) -> None:
    paths = get_paths(config, data_dir)
    if "--status" in sys.argv:
        run_status(config, paths)
        return
    if "--digest" in sys.argv:
        import mf_digest
        mf_digest.run_cli(config, data_dir)
        return

    local = "--local" in sys.argv
    dry_run = "--dry-run" in sys.argv
    token = None
    if not local:
        from rocky import acquire_token, audit_token_scopes, get_msal_app
        token = acquire_token(get_msal_app(config))
        audit_token_scopes(token)

    s = run_scan(config, paths, token=token, local=local, dry_run=dry_run)
    log.info(f"[mf-brain] cases {s['cases_before']} -> {s['cases_after']}; "
             f"lines {s['lines_before']} -> {s['lines_after']} "
             f"({s['line_changes']} changed, {s['lines_closed']} closed); "
             f"{s['observations_new']} new observations; "
             f"{s['mail_messages']} mail across {s['mail_folders']} folders; "
             f"predictions +{s['predictions_new']}, "
             f"{len(s['predictions_settled'])} settled; "
             f"{s['unresolved_folders']} folders need review")
