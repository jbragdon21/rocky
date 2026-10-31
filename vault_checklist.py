"""
Vault Checklist — which documents the Vault holds for each LLT matter
=====================================================================

    python rocky.py --vault-checklist [--dry-run] [--no-write] [--no-archive]
                                      [--repaint] [--local [PATH]]
                                      [--sheet NAME] [--vault-root PATH]
                                      [--as-of YYYY-MM-DD]       (DAILY, 6:15 AM)
    python rocky.py --vault-checklist --consent
        One-time, interactive: consent rocky@ to the sheet-write scope.

PENDING LLT MATTERS carries four "The Vault" columns (Lease? / Final
Notice? / Affidavit of Service? / Ledger?). Each morning Rocky reads the
sheet, finds each matter's tenant folder in The Vault, and marks every
cell Yes (light green), No (light orange) or N/a (light gray, when that
kind of matter does not need that document). The team reads the columns
to see which matters have their papers and can move to the next step.

What each kind of matter needs (James, 2026-10-31):

    MD / VA breach, non-renewal   lease, final notice
    MD / VA rent, non-rent        lease, final notice, current ledger
    DC breach (and non-renewal)   lease, RAD-stamped notice, affidavit
    DC non-rent                   lease, RAD-stamped notice, affidavit,
                                  current ledger
    DC rent                       lease, notice (not RAD-stamped),
                                  affidavit, current ledger

A cell is left EMPTY, never guessed, when Rocky cannot tell the matter's
type from Status or its jurisdiction from the client, the property or a
case number. Those rows are listed in the daily report, and one line in
``_vault\\checklist_rules.json`` teaches the answer (no rebuild).

The same run manages the Vault's archive. A tenant folder whose matter
has been off the sheet for 14 days, or which never matched a sheet row
and has had no new document for 120 days, moves to ``_Archive\\<Property>
\\<Tenant>``. A row that comes back pulls its folder back out. Moves are
appended to ``_vault\\moves.jsonl``, which ``vault.load_catalog`` replays,
so the catalog itself is never rewritten (the 8/29 OneDrive fork).

Writes to the sheet go through the Graph Excel API, cell ranges inside a
workbook session, so co-authors in Excel Online keep their edits. Only
the four Vault columns are ever written, and only cells whose value
changes (``--repaint`` refills every marked cell). The write needs a
delegated SharePoint write scope (``SHEET_WRITE_SCOPES``) that rocky@ did
not hold on 2026-10-31. Until it does, the run computes everything,
writes the report, archives, and leaves the sheet alone.

Zero Claude tokens: matching names and dates is arithmetic.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger("rocky.vaultchecklist")

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

# Kept apart from rocky.GRAPH_SCOPES (the teams.py pattern) so no other
# command ever prompts for it. BUILD_REFERENCE prefers Sites.Selected on
# the MultifamilyHousing site; an admin grant of that scope works here
# unchanged if this list is edited to name it.
SHEET_WRITE_SCOPES = ["Sites.ReadWrite.All"]

DOCS = ("lease", "notice", "affidavit", "ledger")

# Header text (lowercased, trailing "?" and spaces dropped) -> document.
HEADER_DOCS = {
    "lease": "lease",
    "final notice": "notice",
    "affidavit of service": "affidavit",
    "ledger": "ledger",
}
DOC_LABELS = {"lease": "Lease", "notice": "Final Notice",
              "affidavit": "Affidavit of Service", "ledger": "Ledger"}

YES, NO, NA = "Yes", "No", "N/a"
FILLS = {YES: "#E2EFDA", NO: "#FCE4D6", NA: "#EDEDED"}

REQ = "required"
RAD = "rad"          # required, and must be the RAD-stamped notice

_MDVA_BREACH = {"lease": REQ, "notice": REQ, "affidavit": None, "ledger": None}
_MDVA_MONEY = {"lease": REQ, "notice": REQ, "affidavit": None, "ledger": REQ}
_DC_BREACH = {"lease": REQ, "notice": RAD, "affidavit": REQ, "ledger": None}
RULES = {
    ("MD", "breach"): _MDVA_BREACH, ("MD", "nonrenewal"): _MDVA_BREACH,
    ("VA", "breach"): _MDVA_BREACH, ("VA", "nonrenewal"): _MDVA_BREACH,
    ("MD", "rent"): _MDVA_MONEY, ("MD", "nonrent"): _MDVA_MONEY,
    ("VA", "rent"): _MDVA_MONEY, ("VA", "nonrent"): _MDVA_MONEY,
    ("DC", "breach"): _DC_BREACH,
    # DC tenants have no non-renewal for cause in the ordinary case; the
    # few that reach the sheet are handled like a breach.
    ("DC", "nonrenewal"): _DC_BREACH,
    ("DC", "nonrent"): {"lease": REQ, "notice": RAD, "affidavit": REQ,
                        "ledger": REQ},
    # A DC rent notice is not filed with RAD, so any notice counts.
    ("DC", "rent"): {"lease": REQ, "notice": REQ, "affidavit": REQ,
                     "ledger": REQ},
}

_DEFAULTS = {
    "vault_checklist_ledger_days": 30,     # a "current" ledger
    "vault_checklist_notice_days": 150,    # notice/affidavit window before ripe
    "vault_checklist_undated_days": 365,   # window when there is no ripe date
    "vault_checklist_min_rows": 300,       # fewer = parse failure, act on nothing
    "vault_archive_grace_days": 14,
    "vault_archive_idle_days": 120,
    "vault_archive_max_per_run": 250,
}

ARCHIVE_DIR = "_Archive"
RULES_FILE = "checklist_rules.json"
STATE_FILE = "checklist_state.json"
MOVES_FILE = "moves.jsonl"
REPORT_DIR = "checklist"

RULES_TEMPLATE = {
    "_comment": (
        "Teaching file for the Vault Checklist (rocky.exe --vault-checklist). "
        "Every run rereads it; no rebuild. 'jurisdictions' maps a sheet "
        "property or client name to DC, MD or VA. 'properties' maps a sheet "
        "property to its Vault property folder when the names differ too much "
        "to match. 'case_types' maps a phrase found in Status to rent, "
        "nonrent, nonrenewal or breach, or 'skip' to leave the row unmarked; "
        "the longest matching phrase wins over the built-in rules. "
        "Names match case-insensitively."),
    "jurisdictions": {},
    "properties": {},
    "case_types": {},
}


def _cfg(config: dict, key: str) -> int:
    try:
        return int(config.get(key, _DEFAULTS[key]))
    except (TypeError, ValueError):
        return _DEFAULTS[key]


def _argv_value(flag: str) -> str | None:
    if flag in sys.argv:
        idx = sys.argv.index(flag)
        if idx + 1 < len(sys.argv) and not sys.argv[idx + 1].startswith("--"):
            return sys.argv[idx + 1]
    return None


# =============================================================================
# Case type and jurisdiction
# =============================================================================

_SKIP_RE = re.compile(r"tenant filed|squatter|small claims|civil action|"
                      r"complaint\b")
_NONRENEW_RE = re.compile(r"non[\s-]?renew")
_NONRENT_RE = re.compile(r"non[\s-]?rent|\bwater\b|utilit|\bsewer\b|"
                         r"electric(?:ity)? bill|gas bill|\bbge\b|\bpepco\b")
_RENT_RE = re.compile(r"\brent\b|\bftpr\b|\bntpr\b")
_BREACH_RE = re.compile(
    r"conduct|smok|noise|housekeep|re-?cert|\bntcv\b|lease term|21/30|"
    r"\b14[\s-]?day|\b30[\s-]?day|breach|animal|\bpets?\b|occupant|"
    r"unauthori|sublet|insurance|fraud|theft|damage|minors|harass|airbnb|"
    r"violation|guest|drug|weapon|assault|threat|criminal|nuisance|"
    r"hoard|odor|pest|bed ?bug|illegal|safety|cure or vacate|\bntv\b|"
    r"notice to vacate|nonremediable|non-remediable|arrest|intoxicat|"
    r"urinat|firework|fire alarm|refused entry|profanity|misuse|"
    r"noncompliance|inappropriate|extended absence|electric|"
    r"warning notice")


def classify_case_type(status: str, overrides: dict | None = None) -> str | None:
    """rent / nonrent / nonrenewal / breach from the Status cell, or None
    when the row is not one of ours to mark (tenant-filed suits, a bare
    "Term letter sent" with no type, parking revocations)."""
    s = re.sub(r"\s+", " ", (status or "").strip().casefold())
    if not s:
        return None
    for phrase, kind in sorted((overrides or {}).items(),
                               key=lambda kv: -len(kv[0])):
        if phrase and phrase.casefold() in s:
            return None if kind == "skip" else kind
    if _SKIP_RE.search(s):
        return None
    if _NONRENEW_RE.search(s):
        return "nonrenewal"
    if _NONRENT_RE.search(s):
        return "nonrent"
    if _RENT_RE.search(s):
        return "rent"
    if _BREACH_RE.search(s):
        return "breach"
    return None


_STATE_LABEL_RE = re.compile(r"(?:^|[\s(\-])(DC|MD|VA)(?:$|[\s)\-])")
_CASE_NO_RULES = (
    (re.compile(r"\bG[VT]\d{2}", re.I), "VA"),
    (re.compile(r"\bD-\d{2}-CV-", re.I), "MD"),
    (re.compile(r"\b(?:20)?\d{2}-(?:LTB|SCB|CAB)-\d+", re.I), "DC"),
    (re.compile(r"\(\s*\d{2}-\d{3,6}\s*\)"), "DC"),
)


# Dropped when comparing community names, so "Residences at the Avenue"
# and "Residences on the Avenue", or "The 202" and "202", compare equal.
_NORM_DROP = {"the", "at", "on", "of", "a", "an", "and", "apartments",
              "apartment", "apts", "apt", "residences"}


def _norm(name) -> str:
    import pending_llt as pl
    s = pl._normalize_property(str(name or ""))
    words = re.sub(r"[^a-z0-9 ]+", " ", s).split()
    kept = [w for w in words if w not in _NORM_DROP]
    return " ".join(kept or words)


def _signature(norm_name: str) -> frozenset[str]:
    import pending_llt as pl
    return pl._property_signature(norm_name)


def signatures_conflict(na: str, nb: str) -> bool:
    """Two normalized names name different buildings: both carry street
    or building numbers and they differ ("Solaire 8200" / "Solaire
    8250"), or both carry roman numerals and they differ ("Weinberg
    Villages V" / "Weinberg Village IV"). A numeral against a street
    number is not a conflict: the Vault files Solstice I as "Solstice -
    3534 E Capitol"."""
    sa, sb = _signature(na), _signature(nb)
    for pick in (str.isdigit, lambda t: not t.isdigit()):
        ka = {t for t in sa if pick(t)}
        kb = {t for t in sb if pick(t)}
        if ka and kb and ka != kb:
            return True
    return False


def names_match(a: str, b: str, strong_only: bool = False) -> str | None:
    """'strong' (same name), 'weak' (one contains the other, or a close
    spelling) or None. Street and building numbers must agree when both
    names carry them, so "Solaire 8200" never matches "Solaire 8250"."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return None
    if na == nb:
        return "strong"
    if strong_only:
        return None
    if signatures_conflict(na, nb):
        return None
    if len(min(na, nb, key=len)) >= 3 and (
            f" {na} " in f" {nb} " or f" {nb} " in f" {na} "):
        return "weak"
    if SequenceMatcher(None, na, nb).ratio() >= 0.85:
        return "weak"
    return None


def load_property_states(config: dict) -> list[tuple[str, str]]:
    """(property name, state) from Remy's property table — the same file
    the Vault grounds property names against."""
    import csv
    explicit = (config.get("vault_property_table") or "").strip()
    if explicit:
        path = Path(explicit)
    else:
        remy_cli = (config.get("remy_cli_path") or "").strip()
        if not remy_cli:
            return []
        path = Path(remy_cli).parent / "data" / "property_table.csv"
    out: list[tuple[str, str]] = []
    try:
        with open(path, "r", newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                name = (r.get("Property Name") or "").strip()
                st = (r.get("State") or "").strip().upper()
                if name and st in ("DC", "MD", "VA"):
                    out.append((name, st))
    except (OSError, csv.Error) as e:
        log.warning(f"[vault-checklist] property table unreadable ({path}): {e}")
    return out


def _lookup(mapping: dict, name: str):
    """Case-insensitive, normalized lookup in a teaching-file map."""
    if not name:
        return None
    key = _norm(name)
    for k, v in mapping.items():
        if _norm(k) == key:
            return v
    return None


def resolve_jurisdiction(row: dict, table: list[tuple[str, str]],
                         rules: dict, aliases: dict,
                         history: dict | None = None) -> tuple[str | None, str]:
    """(state, how) for one sheet row. Order: the teaching file, a state
    in the client header ("Bozzuto MD", "Hanover on 8th (DC)"), Remy's
    property table, a case number in Next Steps, then the case numbers
    the property (or its client) has carried across the workbook."""
    prop, client = row.get("property") or "", row.get("client") or ""
    for name in (prop, client):
        st = _lookup(rules.get("jurisdictions") or {}, name)
        if st:
            return str(st).upper(), "rules file"
    m = _STATE_LABEL_RE.search(client.upper())
    if m:
        return m.group(1), "client label"
    for name in (prop, client):
        if not name:
            continue
        candidates = {name}
        snapped = aliases.get(re.sub(r"\s+", " ", name.strip()).casefold())
        if snapped:
            candidates.add(snapped)
        strong, weak = set(), set()
        for cand in candidates:
            for tname, st in table:
                hit = names_match(cand, tname)
                if hit == "strong":
                    strong.add(st)
                elif hit == "weak":
                    weak.add(st)
        found = strong or weak
        if len(found) == 1:
            return found.pop(), "property table"
    for rx, st in _CASE_NO_RULES:
        if rx.search(row.get("next_steps") or ""):
            return st, "case number"
    if history:
        # The case numbers (and DC-only "NTCV" statuses) this property's
        # matters have carried on every tab of the workbook.
        seen = history.get("property", {}).get(_norm(prop))
        if seen and len(seen) == 1:
            return next(iter(seen)), "property case history"
    return None, ""


def case_history(file_bytes: bytes) -> dict:
    """{"property": {norm name: {state, ...}}} from the case numbers in
    Next Steps, and DC's "NTCV" (notice to correct or vacate) in Status,
    on EVERY tab of the workbook."""
    import io
    import openpyxl
    out = {"property": {}}
    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True,
                                    data_only=True)
    except Exception as e:
        log.warning(f"[vault-checklist] case history unreadable: {e}")
        return out
    for ws in wb.worksheets:
        try:
            parsed = parse_sheet([list(r) for r in ws.iter_rows(values_only=True)])
        except Exception:
            continue
        for r in parsed["rows"]:
            st = next((st for rx, st in _CASE_NO_RULES
                       if rx.search(r["next_steps"])), None)
            if st is None and re.search(r"\bntcv\b", r["status"], re.I):
                st = "DC"
            if st:
                out["property"].setdefault(_norm(r["property"]), set()).add(st)
    wb.close()
    return out


def client_consensus(results: list[dict]) -> None:
    """Fill a row's missing jurisdiction from its client block on the
    CURRENT tab when every resolved row of that client agrees (Towner,
    Franklin Group, WPC). The current tab only: older tabs filed
    Hanover Tysons (VA) under Towner's block."""
    states: dict[str, set] = {}
    for r in results:
        if r["jurisdiction"]:
            states.setdefault(r["client"], set()).add(r["jurisdiction"])
    for r in results:
        if not r["jurisdiction"]:
            seen = states.get(r["client"])
            if seen and len(seen) == 1:
                r["jurisdiction"] = next(iter(seen))
                r["jurisdiction_how"] = "client block"


# =============================================================================
# Reading the sheet
# =============================================================================

def _cell_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"\s+", " ", str(v)).strip()


def _cell_date(v) -> date | None:
    """A Ripe Date cell: text ("11.11.26"), a datetime from openpyxl, or
    an Excel serial number from the Graph Excel API."""
    import pending_llt as pl
    if isinstance(v, (int, float)) and not isinstance(v, bool) \
            and 20000 < v < 80000:
        return date(1899, 12, 30) + timedelta(days=int(v))
    return pl.parse_ripe_date(v)


def grid_from_workbook(file_bytes: bytes,
                       sheet: str | None = None) -> tuple[str, list[list]]:
    """(sheet name, rows of values) for the active sheet — the current
    weekly revision — or the named one."""
    import io
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True,
                                data_only=True)
    ws = wb[sheet] if sheet else wb.active
    name = ws.title
    grid = [list(r) for r in ws.iter_rows(min_row=1, values_only=True)]
    wb.close()
    return name, grid


def parse_sheet(grid: list[list]) -> dict:
    """Locate the header row and the Vault columns, and read each matter.

    Returns {"header_row", "columns": {doc: 0-based col}, "rows": [...]}
    where each row carries its 1-based spreadsheet row number and the
    current text of its Vault cells. The header row is the one holding
    "Name" and something "Property" (pending_llt's rule), so the "The
    Vault" banner James added above it on 10/31 is harmless."""
    header_idx = None
    for i, row in enumerate(grid[:25]):
        cells = [_cell_str(c).lower() for c in row]
        if "name" in cells and any("property" in c for c in cells):
            header_idx = i
            break
    if header_idx is None:
        return {"header_row": None, "columns": {}, "rows": []}
    headers = [_cell_str(c).lower().rstrip("? ").strip()
               for c in grid[header_idx]]
    idx = {h: i for i, h in enumerate(headers) if h}
    vault_cols = {doc: idx[h] for h, doc in HEADER_DOCS.items() if h in idx}

    def col(*names):
        for n in names:
            if n in idx:
                return idx[n]
        return None

    c_name, c_prop, c_unit = col("name"), col("property"), col("unit")
    c_status, c_ripe, c_next = col("status"), col("ripe date"), col("next steps")

    def get(r, c):
        return r[c] if c is not None and c < len(r) else None

    rows, client = [], ""
    for i, r in enumerate(grid[header_idx + 1:], start=header_idx + 2):
        if not r or all(v is None or _cell_str(v) == "" for v in r):
            continue
        if _cell_str(r[0]):
            client = _cell_str(r[0])
        name = _cell_str(get(r, c_name))
        if not name:
            continue
        rows.append({
            "row": i,
            "client": client,
            "name": name,
            "property": _cell_str(get(r, c_prop)) or client,
            "unit": _cell_str(get(r, c_unit)),
            "status": _cell_str(get(r, c_status)),
            "ripe": _cell_date(get(r, c_ripe)),
            "next_steps": _cell_str(get(r, c_next)),
            "current": {d: _cell_str(get(r, c)) for d, c in vault_cols.items()},
        })
    return {"header_row": header_idx + 1, "columns": vault_cols,
            "rows": rows}


# =============================================================================
# The Vault inventory
# =============================================================================

@dataclass
class Doc:
    kind: str                 # lease / notice / affidavit / ledger / other
    when: date | None
    rad: bool
    rel: str


@dataclass
class Folder:
    rel: str                  # "Prop\\Tenant" or "_Archive\\Prop\\Tenant"
    prop: str
    tenant: str
    archived: bool
    docs: list[Doc] = field(default_factory=list)
    newest: datetime | None = None


_DATE_IN_NAME = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_RAD_POS = re.compile(r"\bRAD\b|RAD[- ]stamp|RAD fil", re.I)
_RAD_NEG = re.compile(r"\b(?:no|not|without|un)[\w\s-]{0,15}RAD\b", re.I)

_PREFIX_KINDS = {
    "lease": "lease", "ledger": "ledger", "notice": "notice",
    "affidavit of service": "affidavit",
    "certified mailing affidavit": "affidavit",
    "proof of mailing": "affidavit",
    "rad filing packet": "notice",
}
_CATALOG_KINDS = {"lease": "lease", "ledger": "ledger", "notice": "notice",
                  "affidavit_of_service": "affidavit", "other": "other"}


def kind_from_filename(name: str) -> str:
    """The Vault's own filenames start with the document type ("Lease -
    Smith, John - 2026-05-01.pdf"); a hand-dropped file falls back to
    keywords, notice words first ("Lease Violation Notice" is a notice)."""
    prefix = name.split(" - ", 1)[0].strip().casefold()
    if prefix in _PREFIX_KINDS:
        return _PREFIX_KINDS[prefix]
    low = name.casefold()
    if re.search(r"notice|ntcv|ntpr|\bntq\b|quit|vacate|cure|termination", low):
        return "notice"
    if re.search(r"affidavit|proof of (?:service|mailing)|certificate of service",
                 low):
        return "affidavit"
    if re.search(r"ledger|statement of (?:deposit )?account|account statement",
                 low):
        return "ledger"
    if "lease" in low:
        return "lease"
    return "other"


def _date_from(name: str, entry: dict | None) -> date | None:
    m = _DATE_IN_NAME.search(name)
    raw = (m.group(0) if m else "") or str((entry or {}).get("document_date") or "")
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _is_rad(name: str, entry: dict | None) -> bool:
    if _RAD_POS.search(name):
        return True
    if not entry:
        return False
    detail = entry.get("source_detail") or {}
    where = " ".join(str(x or "") for x in (
        entry.get("original_name"), detail.get("path"),
        detail.get("shared_link"), detail.get("subject")))
    if re.search(r"RAD CASES|RAD fil|rad-notices|RAD[- ]stamp", where, re.I):
        return True
    why = str(entry.get("reasoning") or "")
    return bool(_RAD_POS.search(why)) and not _RAD_NEG.search(why)


def scan_vault(root: Path, catalog: list[dict]) -> tuple[list[Folder], list[dict]]:
    """Every tenant folder, live and archived, with its documents; plus
    the "As Issued Rent Notices" files, which are filed by issue date
    rather than by tenant."""
    by_rel = {}
    for e in catalog:
        rel = (e.get("path") or "").replace("/", "\\").casefold()
        if rel and not e.get("dry_run"):
            by_rel[rel] = e

    folders: list[Folder] = []
    issued: list[dict] = []

    def tenant_folders(base: Path, rel_base: str, archived: bool):
        try:
            props = [p for p in base.iterdir() if p.is_dir()]
        except OSError:
            return
        for p in props:
            if not archived and p.name.startswith("_"):
                if "rent notice" in p.name.casefold():
                    _scan_issued(p, f"{p.name}", issued)
                continue
            try:
                tenants = [t for t in p.iterdir() if t.is_dir()]
            except OSError:
                continue
            for t in tenants:
                rel = f"{rel_base}{p.name}\\{t.name}"
                f = Folder(rel=rel, prop=p.name, tenant=t.name,
                           archived=archived)
                newest = None
                try:
                    files = [x for x in t.rglob("*") if x.is_file()]
                except OSError:
                    files = []
                for x in files:
                    if x.name.casefold() == "desktop.ini":
                        continue
                    file_rel = f"{rel}\\{x.relative_to(t)}".replace("/", "\\")
                    entry = by_rel.get(file_rel.casefold())
                    kind = (_CATALOG_KINDS.get(entry.get("doc_type") or "")
                            if entry else None) or kind_from_filename(x.name)
                    f.docs.append(Doc(kind=kind, when=_date_from(x.name, entry),
                                      rad=_is_rad(x.name, entry), rel=file_rel))
                    try:
                        mt = datetime.fromtimestamp(x.stat().st_mtime,
                                                    tz=timezone.utc)
                    except OSError:
                        mt = None
                    added = None
                    if entry and entry.get("ts"):
                        try:
                            added = datetime.fromisoformat(
                                entry["ts"].replace("Z", "+00:00"))
                        except ValueError:
                            pass
                    for cand in (mt, added):
                        if cand and (newest is None or cand > newest):
                            newest = cand
                if newest is None:
                    try:
                        newest = datetime.fromtimestamp(t.stat().st_mtime,
                                                        tz=timezone.utc)
                    except OSError:
                        pass
                f.newest = newest
                folders.append(f)

    tenant_folders(root, "", archived=False)
    arch = root / ARCHIVE_DIR
    if arch.is_dir():
        tenant_folders(arch, f"{ARCHIVE_DIR}\\", archived=True)
    return folders, issued


_ISSUED_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})")


def _scan_issued(folder: Path, rel_base: str, out: list[dict]) -> None:
    try:
        files = [x for x in folder.rglob("*") if x.is_file()]
    except OSError:
        return
    for x in files:
        if x.name.casefold() == "desktop.ini":
            continue
        rel_parts = x.relative_to(folder).parts
        when = None
        for part in reversed(rel_parts):   # nearest dated folder wins
            m = _ISSUED_DATE.search(part)
            if m:
                y = int(m.group(3))
                y = y + 2000 if y < 100 else y
                try:
                    when = date(y, int(m.group(1)), int(m.group(2)))
                    break
                except ValueError:
                    pass
        out.append({"rel": f"{rel_base}\\{x.relative_to(folder)}",
                    "tokens": set(re.findall(r"[a-z0-9]+",
                                             x.stem.casefold().replace("_", " "))),
                    "when": when})


# =============================================================================
# Matching a sheet row to its tenant folders
# =============================================================================

_ET_AL = re.compile(r",?\s*et\.?\s*al\.?,?", re.I)


def clean_person(name: str) -> str:
    """'Britt, Wanda, et al. ' -> 'Britt, Wanda'; 'Smith, Jo & Al Smith'
    -> 'Smith, Jo'; 'John Smith' -> 'Smith, John'."""
    s = _ET_AL.sub("", name or "")
    s = re.split(r"\s+(?:&|and)\s+|/", s)[0]
    s = re.sub(r"\s+", " ", s).strip(" ,")
    if "," not in s:
        parts = s.split()
        if len(parts) >= 2:
            s = f"{parts[-1]}, {' '.join(parts[:-1])}"
    return s


def same_person(a: str, b: str) -> bool:
    import vault
    ca, cb = clean_person(a), clean_person(b)
    return ca.casefold() == cb.casefold() or vault._same_person(ca, cb)


def property_folders(row: dict, prop_names: list[str], rules: dict,
                     aliases: dict) -> list[str]:
    """Vault property folders that are this row's property. Exact matches
    (the teaching file, the Vault's alias map, equal names) win outright;
    otherwise every close match is returned, because one community is
    sometimes split across folders ("450 K" / "450K") and the person
    match that follows is the real filter."""
    for name in (row.get("property"), row.get("client")):
        if not name:
            continue
        pinned = _lookup(rules.get("properties") or {}, name)
        cands = {name}
        if pinned:
            cands = {pinned}
        else:
            snapped = aliases.get(re.sub(r"\s+", " ", name.strip()).casefold())
            if snapped:
                cands.add(snapped)
        strong = [p for p in prop_names
                  if any(names_match(c, p, strong_only=True) for c in cands)]
        if strong or pinned:
            return strong
        weak = [p for p in prop_names if any(names_match(c, p) for c in cands)]
        if weak:
            return weak
    return []


# Words too common in community names to tie a sheet property to a Vault
# folder on their own ("Franklin Commons" and "Benning Courts" share
# nothing that matters).
_GENERIC_WORDS = {
    "the", "at", "on", "of", "and", "street", "st", "house", "place", "park",
    "square", "court", "courts", "gardens", "garden", "apartments", "towers",
    "tower", "village", "villages", "commons", "residences", "manor",
    "terrace", "lofts", "homes", "senior", "station", "district", "bridge",
    "capitol", "avenue", "ave", "yards", "market", "east", "west", "north",
    "south", "hill", "point", "pointe", "flats", "first", "new"}


def _distinctive(*names: str) -> set[str]:
    out = set()
    for n in names:
        for t in _norm(n).split():
            if len(t) >= 4 and not t.isdigit() and t not in _GENERIC_WORDS:
                out.add(t)
    return out


def _pick(row: dict, hits: list[Folder]) -> list[Folder] | None:
    """Hits that are one person, or None when they are two different
    plausible people (ambiguous)."""
    if len(hits) <= 1:
        return hits
    names = [f.tenant for f in hits]
    if all(same_person(x, y) for x in names for y in names):
        return hits
    exact = [f for f in hits if clean_person(f.tenant).casefold()
             == clean_person(row["name"]).casefold()]
    if exact and all(same_person(x.tenant, y.tenant)
                     for x in exact for y in exact):
        return exact
    return None


def match_row(row: dict, by_prop: dict[str, list[Folder]],
              rules: dict, aliases: dict) -> tuple[list[Folder], str]:
    """(tenant folders, how). Tried in tiers, first hit wins:

      1. the property's own folder(s) — exact or close names;
      2. folders sharing a distinctive word with the property or client
         ("Solstice I" -> "Solstice - 3534 E Capitol"), which is how a
         community misnamed at ingest still gets found.

    Several folders are returned only when they are plainly one person
    ("Holland, D" and "Holland, Deleona"); two different plausible people
    is ambiguous and returns none — a wrong Yes is worse than a No."""
    names = list(by_prop)
    props = property_folders(row, names, rules, aliases)
    tiers = []
    if props:
        tiers.append(("property + name", props))
        # The exact folder may exist and still not hold this person
        # ("Cloisters I" beside "The Cloisters"): widen to close names.
        weak = [p for p in names if p not in props and any(
            names_match(c, p) for c in (row.get("property"), row.get("client"))
            if c)]
        if weak:
            tiers.append(("similar property + name", weak))
    words = _distinctive(row.get("property") or "", row.get("client") or "")
    if words:
        shared = [p for p in names if p not in props
                  and words & _distinctive(p)
                  and not signatures_conflict(_norm(row.get("property")),
                                              _norm(p))]
        if shared:
            tiers.append(("shared property word + name", shared))
    for how, pool_props in tiers:
        hits = [f for p in pool_props for f in by_prop[p]
                if same_person(row["name"], f.tenant)]
        if not hits:
            continue
        picked = _pick(row, hits)
        return (picked, how) if picked else ([], "ambiguous")
    # Deliberately no vault-wide name search after this: tried on the
    # 10/31 sheet, it tied "Smith, Michael" at 101 Cross Street to a
    # Solstice folder. A No the team can correct beats a wrong Yes.
    return [], ("no property folder" if not props else "no tenant folder")


def issued_notices_for(row: dict, issued: list[dict]) -> list[Doc]:
    """'As Issued Rent Notices' files for this row: the surname plus the
    unit or the first name, and a word of the property."""
    person = clean_person(row["name"])
    if "," not in person:
        return []
    last, given = person.split(",", 1)
    last_t = set(re.findall(r"[a-z0-9]+", last.casefold()))
    first = (re.findall(r"[a-z0-9]+", given.casefold()) or [""])[0]
    unit = re.sub(r"[^a-z0-9]", "", (row.get("unit") or "").casefold())
    prop_t = {t for t in re.findall(r"[a-z0-9]+", _norm(row.get("property")))
              if t not in ("the", "at", "on", "of", "and")}
    out = []
    for n in issued:
        toks = n["tokens"]
        if not last_t or not last_t <= toks:
            continue
        if not ((unit and unit in toks) or (first and first in toks)):
            continue
        if prop_t and not (prop_t & toks):
            continue
        out.append(Doc(kind="notice", when=n["when"], rad=False, rel=n["rel"]))
    return out


# =============================================================================
# Evaluating a row
# =============================================================================

def _in_window(d: Doc, row: dict, today: date, config: dict) -> bool:
    """A notice or affidavit belongs to THIS matter when it is dated in
    the notice period before the ripe date (or, with no ripe date, within
    the last year). An undated one is given the benefit of the doubt."""
    if d.when is None:
        return True
    ripe = row.get("ripe")
    if ripe:
        lo = ripe - timedelta(days=_cfg(config, "vault_checklist_notice_days"))
        return lo <= d.when <= ripe + timedelta(days=7)
    return d.when >= today - timedelta(
        days=_cfg(config, "vault_checklist_undated_days"))


def evaluate_row(row: dict, juris: str | None, kind: str | None,
                 folders: list[Folder], extra_notices: list[Doc],
                 today: date, config: dict) -> tuple[dict, dict]:
    """({doc: Yes/No/N/a/""}, {doc: why}) for one matter. "" = leave the
    cell alone (type or jurisdiction unknown)."""
    rule = RULES.get((juris, kind)) if juris and kind else None
    if rule is None:
        return {d: "" for d in DOCS}, {}
    docs = [d for f in folders for d in f.docs] + list(extra_notices)
    out, why = {}, {}
    ledger_floor = today - timedelta(days=_cfg(config, "vault_checklist_ledger_days"))
    for doc in DOCS:
        need = rule[doc]
        if need is None:
            out[doc] = NA
            continue
        if doc == "lease":
            hits = [d for d in docs if d.kind == "lease"]
        elif doc == "ledger":
            hits = [d for d in docs if d.kind == "ledger"
                    and d.when is not None and d.when >= ledger_floor]
            if not hits and any(d.kind == "ledger" for d in docs):
                why[doc] = "ledger in the Vault is not current"
        elif doc == "notice":
            hits = [d for d in docs if d.kind == "notice"
                    and _in_window(d, row, today, config)
                    and (need != RAD or d.rad)]
            if not hits and need == RAD and any(
                    d.kind == "notice" and _in_window(d, row, today, config)
                    for d in docs):
                why[doc] = "notice found but not RAD-stamped"
        else:
            hits = [d for d in docs if d.kind == "affidavit"
                    and _in_window(d, row, today, config)]
        out[doc] = YES if hits else NO
        if hits:
            why[doc] = hits[0].rel
    return out, why


# =============================================================================
# Teaching file, state, moves
# =============================================================================

def load_rules(meta: Path) -> dict:
    path = meta / RULES_FILE
    if not path.exists():
        try:
            path.write_text(json.dumps(RULES_TEMPLATE, indent=2),
                            encoding="utf-8")
        except OSError:
            pass
        return dict(RULES_TEMPLATE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning(f"[vault-checklist] {RULES_FILE} unreadable ({e}) — "
                    f"built-in rules only this run")
        return dict(RULES_TEMPLATE)
    return {k: data.get(k) or {} for k in ("jurisdictions", "properties",
                                            "case_types")}


def load_state(meta: Path) -> dict:
    try:
        return json.loads((meta / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"folders": {}}


def save_state(meta: Path, state: dict) -> None:
    path = meta / STATE_FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, path)


def _append_move(meta: Path, src: str, dst: str, reason: str) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "from": src,
           "to": dst, "reason": reason}
    with open(meta / MOVES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while True:
        cand = path.with_name(f"{path.stem}_({n}){path.suffix}")
        if not cand.exists():
            return cand
        n += 1


def move_folder(root: Path, meta: Path, src_rel: str, dst_rel: str,
                reason: str) -> int:
    """Move a tenant folder, merging file by file when the destination
    already exists. Returns files moved. Each move is logged to
    moves.jsonl before the next starts, so a crash mid-merge still leaves
    the catalog able to find every file."""
    src, dst = root / src_rel, root / dst_rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        count = sum(1 for x in src.rglob("*") if x.is_file())
        os.rename(src, dst)
        _append_move(meta, src_rel, dst_rel, reason)
    else:
        count = 0
        for x in sorted(src.rglob("*")):
            if not x.is_file():
                continue
            target = _unique(dst / x.relative_to(src))
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(x, target)
            _append_move(meta, str(Path(src_rel) / x.relative_to(src)),
                         str(target.relative_to(root)), reason)
            count += 1
        for d in sorted((p for p in src.rglob("*") if p.is_dir()),
                        key=lambda p: -len(p.parts)):
            try:
                d.rmdir()
            except OSError:
                pass
        try:
            src.rmdir()
        except OSError:
            pass
    try:                       # an emptied property folder goes too
        if src.parent != root and not any(src.parent.iterdir()):
            src.parent.rmdir()
    except OSError:
        pass
    return count


# =============================================================================
# Archive planning
# =============================================================================

def plan_archive(folders: list[Folder], matched: set[str], state: dict,
                 today: date, config: dict) -> dict:
    """What moves today. Pure — no filesystem access — so it can be
    tested and dry-run. Returns {"archive": [(folder, reason)],
    "restore": [folder], "pending": [(folder, days_left)]}."""
    grace = _cfg(config, "vault_archive_grace_days")
    idle = _cfg(config, "vault_archive_idle_days")
    seen = state.get("folders") or {}
    plan = {"archive": [], "restore": [], "pending": []}
    for f in folders:
        if f.archived:
            if f.rel in matched:
                plan["restore"].append(f)
            continue
        if f.rel in matched:
            continue
        last = (seen.get(f.rel) or {}).get("last_matched")
        if last:
            off = (today - date.fromisoformat(last)).days
            if off >= grace:
                plan["archive"].append((f, f"off the sheet since {last}"))
            else:
                plan["pending"].append((f, grace - off))
            continue
        if f.newest is not None:
            quiet = (today - f.newest.date()).days
            if quiet >= idle:
                plan["archive"].append(
                    (f, f"never on the current sheet; no new document "
                        f"in {quiet} days"))
    return plan


def archive_target(f: Folder) -> str:
    return f"{ARCHIVE_DIR}\\{f.prop}\\{f.tenant}"


def restore_target(f: Folder) -> str:
    return f"{f.prop}\\{f.tenant}"


# =============================================================================
# Graph: the workbook
# =============================================================================

class SheetWriteUnavailable(RuntimeError):
    pass


def acquire_write_token(config: dict, interactive: bool = False) -> str | None:
    """A delegated token carrying SHEET_WRITE_SCOPES, silently from the
    cache. Scheduled runs never start a device flow; --consent does."""
    from rocky import get_msal_app
    app = get_msal_app(config)
    accounts = app.get_accounts()
    result = None
    if accounts:
        result = app.acquire_token_silent(SHEET_WRITE_SCOPES, account=accounts[0])
    if not result and interactive:
        flow = app.initiate_device_flow(scopes=SHEET_WRITE_SCOPES)
        if "user_code" not in flow:
            print(f"Could not start consent: {flow.get('error_description', flow)}")
            return None
        print("\n" + "=" * 60)
        print("The Vault Checklist needs a one-time consent as rocky@:")
        print(flow["message"])
        print("=" * 60 + "\n")
        result = app.acquire_token_by_device_flow(flow)
    if hasattr(app, "_save_cache"):
        app._save_cache()
    if not result or "access_token" not in result:
        return None
    return result["access_token"]


class Workbook:
    """Cell-range reads and writes on the LLT workbook through the Graph
    Excel API, inside one persisted workbook session."""

    def __init__(self, token: str, drive_id: str, item_id: str, sheet: str):
        import requests
        self.http = requests.Session()
        self.token = token
        self.base = f"/drives/{drive_id}/items/{item_id}/workbook"
        self.ws = f"{self.base}/worksheets/{quote(sheet, safe='')}"
        self.session_id = None

    def _req(self, method: str, path: str, body=None, timeout=60):
        headers = {"Authorization": f"Bearer {self.token}",
                   "Content-Type": "application/json"}
        if self.session_id:
            headers["workbook-session-id"] = self.session_id
        for attempt in range(5):
            r = self.http.request(method, GRAPH_API_BASE + path,
                                  headers=headers,
                                  data=json.dumps(body) if body is not None else None,
                                  timeout=timeout)
            if r.status_code in (429, 503, 504):
                time.sleep(int(r.headers.get("Retry-After", 2 ** attempt)))
                continue
            if r.status_code in (401, 403):
                raise SheetWriteUnavailable(
                    f"HTTP {r.status_code}: {r.text[:300]}")
            r.raise_for_status()
            return r.json() if r.content else {}
        r.raise_for_status()

    def open(self):
        self.session_id = self._req("POST", f"{self.base}/createSession",
                                    {"persistChanges": True})["id"]

    def close(self):
        if self.session_id:
            try:
                self._req("POST", f"{self.base}/closeSession", {})
            except Exception as e:   # closing is courtesy; sessions expire
                log.info(f"[vault-checklist] closeSession: {e}")
            self.session_id = None

    def read(self, address: str) -> list[list]:
        return self._req(
            "GET", f"{self.ws}/range(address='{address}')?$select=values"
        ).get("values") or []

    def batch(self, ops: list[tuple[str, str, dict | None]]) -> int:
        """Run (method, path, body) ops twenty to a $batch, chained with
        dependsOn so Excel applies them in order. Returns failures."""
        failures = 0
        for start in range(0, len(ops), 20):
            chunk = ops[start:start + 20]
            reqs = []
            for i, (method, path, body) in enumerate(chunk):
                req = {"id": str(i + 1), "method": method, "url": path,
                       "headers": {"Content-Type": "application/json",
                                   "workbook-session-id": self.session_id}}
                if body is not None:
                    req["body"] = body
                if i:
                    req["dependsOn"] = [str(i)]
                reqs.append(req)
            for attempt in range(5):
                out = self._req("POST", "/$batch", {"requests": reqs},
                                timeout=120)
                codes = [x.get("status", 0) for x in out.get("responses", [])]
                if any(c == 429 for c in codes) and attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                bad = [x for x in out.get("responses", [])
                       if x.get("status", 0) >= 400]
                for x in bad[:3]:
                    log.warning(f"[vault-checklist] batch op failed: "
                                f"{x.get('status')} {str(x.get('body'))[:200]}")
                failures += len(bad)
                break
        return failures


def _col_letter(idx0: int) -> str:
    from openpyxl.utils import get_column_letter
    return get_column_letter(idx0 + 1)


def _runs(rows: list[int]) -> list[tuple[int, int]]:
    """Consecutive row numbers grouped into (first, last) runs."""
    out = []
    for r in sorted(rows):
        if out and r == out[-1][1] + 1:
            out[-1] = (out[-1][0], r)
        else:
            out.append((r, r))
    return out


def plan_writes(rows: list[dict], desired: dict, columns: dict,
                repaint: bool = False) -> list[tuple[str, str, dict]]:
    """(address, kind, body) ops for the cells that change. kind is
    "values" or "fill". A blank desired value leaves the cell alone."""
    ops = []
    for doc, col in columns.items():
        letter = _col_letter(col)
        changed = {}
        for r in rows:
            want = desired.get(r["row"], {}).get(doc, "")
            if not want:
                continue
            if repaint or r["current"].get(doc, "") != want:
                changed[r["row"]] = want
        for first, last in _runs(list(changed)):
            vals = [[changed[i]] for i in range(first, last + 1)]
            ops.append((f"{letter}{first}:{letter}{last}", "values",
                        {"values": vals}))
            # fills: one op per run of equal values inside this run
            seg_start = first
            for i in range(first, last + 1):
                if i == last or changed[i + 1] != changed[i]:
                    ops.append((f"{letter}{seg_start}:{letter}{i}", "fill",
                                {"color": FILLS[changed[i]]}))
                    seg_start = i + 1
    return ops


def write_sheet(wb: Workbook, parsed: dict, desired: dict, name_col: int,
                repaint: bool) -> dict:
    """Re-read the Name column, refuse if any row moved since the read
    the marks were computed from, then write the changed cells."""
    rows = parsed["rows"]
    if not rows:
        return {"written": 0, "skipped": "no rows"}
    last = max(r["row"] for r in rows)
    letter = _col_letter(name_col)
    live = wb.read(f"{letter}1:{letter}{last}")
    for r in rows:
        got = _cell_str(live[r["row"] - 1][0]) if r["row"] - 1 < len(live) else ""
        if got != r["name"]:
            return {"written": 0,
                    "skipped": f"row {r['row']} changed during the run "
                               f"({r['name']!r} -> {got!r}); retry next run"}
    ops = plan_writes(rows, desired, parsed["columns"], repaint)
    batch_ops = []
    for address, kind, body in ops:
        path = f"{wb.ws}/range(address='{address}')"
        if kind == "fill":
            path += "/format/fill"
        batch_ops.append(("PATCH", path, body))
    failures = wb.batch(batch_ops) if batch_ops else 0
    cells = sum(len(b["values"]) for _, k, b in ops if k == "values")
    return {"written": cells, "ops": len(ops), "failures": failures}


def locate_item(token: str, config: dict) -> tuple[str, str] | None:
    import requests
    import pending_llt as pl
    site_id = pl.resolve_sharepoint_site_id(
        token, config.get("sharepoint_hostname", "gejlaw.sharepoint.com"),
        config.get("sharepoint_site_path", "/sites/MultifamilyHousing"))
    if not site_id:
        return None
    drive_id = pl.resolve_drive_id(token, site_id)
    if not drive_id:
        return None
    path = config.get("llt_file_path", pl.LLT_FILE_PATH)
    r = requests.get(f"{GRAPH_API_BASE}/drives/{drive_id}/root:/"
                     f"{quote(path)}?$select=id",
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if r.status_code != 200:
        log.error(f"[vault-checklist] could not find {path}: "
                  f"HTTP {r.status_code}")
        return None
    return drive_id, r.json()["id"]


# =============================================================================
# The run
# =============================================================================

def compute(parsed: dict, folders: list[Folder], issued: list[dict],
            config: dict, rules: dict, aliases: dict,
            table: list[tuple[str, str]], today: date,
            history: dict | None = None) -> dict:
    """Marks for every row, plus what the report needs. No I/O."""
    by_prop: dict[str, list[Folder]] = {}
    for f in folders:
        by_prop.setdefault(f.prop, []).append(f)
    results, matched = [], set()
    for row in parsed["rows"]:
        kind = classify_case_type(row["status"], rules.get("case_types"))
        juris, juris_how = resolve_jurisdiction(row, table, rules, aliases,
                                                history)
        hits, how = match_row(row, by_prop, rules, aliases)
        results.append({"_row": row, "_hits": hits, "case_type": kind,
                        "client": row["client"], "jurisdiction": juris,
                        "jurisdiction_how": juris_how, "match": how})
    client_consensus(results)
    desired = {}
    for r in results:
        row, hits = r.pop("_row"), r.pop("_hits")
        juris, kind = r["jurisdiction"], r["case_type"]
        for f in hits:
            matched.add(f.rel)
        extra = issued_notices_for(row, issued)             if (juris, kind) == ("DC", "rent") else []
        marks, why = evaluate_row(row, juris, kind, hits, extra, today, config)
        desired[row["row"]] = marks
        r.update({**row, "ripe": row["ripe"].isoformat() if row["ripe"] else "",
                  "folders": [f.rel for f in hits], "marks": marks,
                  "why": why})
    return {"desired": desired, "results": results, "matched": matched}


def run(config: dict, paths: dict, *, file_bytes: bytes, sheet: str | None,
        token_write: str | None, item: tuple[str, str] | None,
        dry_run: bool, do_archive: bool, repaint: bool,
        today: date) -> dict:
    import vault
    meta, root = paths["meta"], paths["root"]
    summary: dict = {"date": today.isoformat(), "dry_run": dry_run}

    sheet_name, grid = grid_from_workbook(file_bytes, sheet)
    wb = None
    if token_write and item and not dry_run:
        # Compute from the LIVE grid, not the saved file, so the row
        # numbers we write to are the ones co-authors see right now.
        wb = Workbook(token_write, item[0], item[1], sheet_name)
        try:
            wb.open()
            from openpyxl.utils import get_column_letter
            width = max((len(r) for r in grid[:25]), default=16)
            grid = wb.read(f"A1:{get_column_letter(width)}{len(grid) + 50}")
        except (SheetWriteUnavailable, Exception) as e:
            log.warning(f"[vault-checklist] live read failed ({e}); "
                        f"report only this run")
            summary["write"] = {"written": 0, "skipped": f"live read failed: {e}"}
            if wb:
                wb.close()
            wb = None
    parsed = parse_sheet(grid)
    summary["sheet"] = sheet_name
    summary["rows"] = len(parsed["rows"])
    summary["columns"] = {d: _col_letter(c) for d, c in parsed["columns"].items()}
    if len(parsed["rows"]) < _cfg(config, "vault_checklist_min_rows"):
        summary["error"] = (f"only {len(parsed['rows'])} matters parsed from "
                            f"{sheet_name!r} — treating as a parse failure")
        log.error(f"[vault-checklist] {summary['error']}")
        if wb:
            wb.close()
        return summary

    vault._load_grounding(config, paths)
    aliases = dict(vault._PROPERTY_ALIASES)
    rules = load_rules(meta)
    table = load_property_states(config)
    catalog = vault.load_catalog(paths)
    folders, issued = scan_vault(root, catalog)
    summary["folders"] = {"live": sum(not f.archived for f in folders),
                          "archived": sum(f.archived for f in folders)}

    history = case_history(file_bytes)
    out = compute(parsed, folders, issued, config, rules, aliases, table,
                  today, history)

    # ----- the sheet -----
    if wb is not None:
        name_col = None
        for i, h in enumerate(grid[parsed["header_row"] - 1]):
            if _cell_str(h).lower() == "name":
                name_col = i
        try:
            summary["write"] = write_sheet(wb, parsed, out["desired"],
                                           name_col, repaint)
        except SheetWriteUnavailable as e:
            summary["write"] = {"written": 0, "skipped": f"no permission: {e}"}
        except Exception as e:
            log.exception("[vault-checklist] sheet write failed")
            summary["write"] = {"written": 0, "skipped": f"error: {e}"}
        finally:
            wb.close()
    elif "write" not in summary:
        summary["write"] = {"written": 0, "skipped": (
            "dry run" if dry_run else
            f"rocky@ lacks {'/'.join(SHEET_WRITE_SCOPES)} (run --consent)"
            if not token_write else "workbook not located")}
        summary["would_write"] = sum(
            len(b["values"]) for _, k, b in plan_writes(
                parsed["rows"], out["desired"], parsed["columns"], repaint)
            if k == "values")

    # ----- the archive -----
    state = load_state(meta)
    state.setdefault("folders", {})
    plan = plan_archive(folders, out["matched"], state, today, config)
    prev = state.get("last_matched_count") or 0
    guard = None
    if do_archive and not dry_run:
        if summary["folders"]["live"] < 100:
            guard = "fewer than 100 live tenant folders — is the Vault mounted?"
        elif prev and len(out["matched"]) < prev * 0.5:
            guard = (f"matched folders fell from {prev} to "
                     f"{len(out['matched'])} — not archiving on that")
        elif vault._recent_unfinished_run(paths):
            guard = "a Vault ingest is running — archive deferred"
    cap = _cfg(config, "vault_archive_max_per_run")
    done_arch, done_rest = [], []
    if do_archive and not dry_run and not guard:
        for f in plan["restore"]:
            try:
                n = move_folder(root, meta, f.rel, restore_target(f),
                                "back on the LLT sheet")
                done_rest.append((f, n))
            except OSError as e:
                log.warning(f"[vault-checklist] restore {f.rel} failed: {e}")
        for f, reason in plan["archive"][:cap]:
            try:
                n = move_folder(root, meta, f.rel, archive_target(f), reason)
                done_arch.append((f, reason, n))
                state["folders"].pop(f.rel, None)
            except OSError as e:
                log.warning(f"[vault-checklist] archive {f.rel} failed: {e}")
    if not dry_run:
        for rel in out["matched"]:
            live_rel = rel.split("\\", 1)[1] if rel.startswith(ARCHIVE_DIR + "\\") \
                else rel
            state["folders"].setdefault(live_rel, {})["last_matched"] = \
                today.isoformat()
        state["last_matched_count"] = len(out["matched"])
        state["last_run"] = datetime.now(timezone.utc).isoformat()
        save_state(meta, state)
    if done_arch or done_rest:
        vault.rebuild_index(paths)
    summary["archive"] = {
        "planned": len(plan["archive"]), "restore_planned": len(plan["restore"]),
        "archived": len(done_arch), "restored": len(done_rest),
        "pending": len(plan["pending"]), "guard": guard,
        "enabled": do_archive}

    report = write_report(paths, today, summary, out, plan, done_arch,
                          done_rest, dry_run)
    summary["report"] = str(report) if report else None
    if not dry_run:
        vault.append_activity(paths, {"event": "checklist_run",
                                      **{k: v for k, v in summary.items()
                                         if k != "report"}})
    return summary


# =============================================================================
# Report
# =============================================================================

def write_report(paths: dict, today: date, summary: dict, out: dict,
                 plan: dict, done_arch, done_rest, dry_run: bool) -> Path | None:
    from collections import Counter
    res = out["results"]
    marked = [r for r in res if any(r["marks"].values())]
    ready = [r for r in marked
             if all(v in (YES, NA) for v in r["marks"].values())]
    cells = Counter(v for r in marked for v in r["marks"].values())
    L = [f"# Vault Checklist — {today.isoformat()}"
         f"{' (dry run)' if dry_run else ''}", ""]
    L.append(f"Sheet `{summary['sheet']}`: {summary['rows']} matters. Vault "
             f"columns found: "
             + (", ".join(f"{DOC_LABELS[d]} ({c})"
                          for d, c in summary["columns"].items()) or "NONE"))
    L.append(f"Marked {len(marked)} matters: {cells.get(YES, 0)} Yes, "
             f"{cells.get(NO, 0)} No, {cells.get(NA, 0)} N/a. "
             f"{len(ready)} have every document they need.")
    w = summary.get("write") or {}
    L.append(f"Sheet write: {w.get('written', 0)} cells written"
             + (f"; skipped: {w['skipped']}" if w.get("skipped") else "")
             + (f"; {w['failures']} failed ops" if w.get("failures") else "")
             + (f" (would write {summary['would_write']})"
                if summary.get("would_write") else ""))
    by = Counter((r["jurisdiction"] or "?", r["case_type"] or "?") for r in res)
    L += ["", "## Matters by jurisdiction and type", "",
          "| Jurisdiction | Type | Matters |", "|---|---|---|"]
    for (j, k), n in sorted(by.items()):
        L.append(f"| {j} | {k} | {n} |")

    unk_type = Counter(r["status"] for r in res if not r["case_type"])
    if unk_type:
        L += ["", "## Not marked: type unknown from Status", "",
              "Teach these in `_vault\\checklist_rules.json` → `case_types` "
              "(or `skip`).", ""]
        for s, n in unk_type.most_common():
            L.append(f"- {n} × `{s or '(blank)'}`")
    unk_j = Counter((r["client"], r["property"]) for r in res
                    if r["case_type"] and not r["jurisdiction"])
    if unk_j:
        L += ["", "## Not marked: jurisdiction unknown", "",
              "Teach these in `checklist_rules.json` → `jurisdictions`.", ""]
        for (c, p), n in unk_j.most_common():
            L.append(f"- {n} × {p} (client {c})")
    no_folder = [r for r in marked if not r["folders"]]
    if no_folder:
        L += ["", f"## No Vault folder found ({len(no_folder)})", "",
              "Every required document is No for these. If the tenant is in "
              "the Vault under another spelling, fix the folder name or add "
              "a `properties` line.", ""]
        for r in sorted(no_folder, key=lambda r: (r["property"], r["name"]))[:300]:
            L.append(f"- {r['property']} — {r['name']} ({r['unit']}) "
                     f"[{r['match']}]")
    multi = Counter()
    for r in res:
        props = {f.split("\\")[-2] for f in r["folders"]}
        if len(props) > 1:
            multi[" / ".join(sorted(props))] += 1
    if multi:
        L += ["", "## Property split across Vault folders", "",
              "One community filed under more than one folder. "
              "`rocky.exe --vault --cleanup` plus an alias line merges them.", ""]
        for k, n in multi.most_common():
            L.append(f"- {k} ({n} matters)")
    renamed = Counter()
    for r in res:
        if r["match"] in ("similar property + name",
                          "shared property word + name"):
            for f in r["folders"]:
                renamed[(r["property"], f.split("\\")[-2])] += 1
    if renamed:
        L += ["", "## Found under a different Vault property name", "",
              "Matched through a looser name, on the same tenant name. If "
              "the Vault folder is the same community under another name, "
              "an alias line in `_vault\\property_aliases.json` makes it "
              "exact. If it is a different community (Classic @ Modern on M "
              "is not Modern on M), the documents were filed under the "
              "wrong property and should be moved.", ""]
        for (p, v), n in renamed.most_common():
            L.append(f"- sheet {p!r} → Vault {v!r} ({n} matters)")
    L += ["", "## Archive", ""]
    a = summary["archive"]
    if a.get("guard"):
        L.append(f"Archive held: {a['guard']}")
    L.append(f"{a['archived']} archived, {a['restored']} restored"
             f" (planned {a['planned']} / {a['restore_planned']}); "
             f"{a['pending']} folders off the sheet inside the "
             f"grace period.")
    rows = done_arch or [(f, why, None) for f, why in plan["archive"]]
    if rows:
        L += ["", "| Folder | Why | Files |", "|---|---|---|"]
        for f, why, n in rows[:500]:
            L.append(f"| {f.rel} | {why} | {'' if n is None else n} |")
    rest = done_rest or [(f, None) for f in plan["restore"]]
    if rest:
        L += ["", "Restored (back on the sheet):", ""]
        for f, n in rest:
            L.append(f"- {f.rel}")

    folder = paths["meta"] / REPORT_DIR
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{today.isoformat()}{'-dry-run' if dry_run else ''}.md"
        path.write_text("\n".join(L) + "\n", encoding="utf-8")
        latest = folder / "latest.json"
        tmp = latest.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"summary": summary, "rows": res},
                                  indent=1, ensure_ascii=False, default=str),
                       encoding="utf-8")
        os.replace(tmp, latest)
        log.info(f"[vault-checklist] report: {path}")
        return path
    except OSError as e:
        log.warning(f"[vault-checklist] could not write report: {e}")
        return None


# =============================================================================
# CLI
# =============================================================================

def run_cli(config: dict, data_dir: Path) -> None:
    import vault
    paths = vault.get_paths(config, data_dir)
    if "--consent" in sys.argv:
        tok = acquire_write_token(config, interactive=True)
        print("Consent recorded — the checklist can write the sheet."
              if tok else "No token — the sheet stays read-only.")
        return
    if not paths["meta"].is_dir():
        log.error(f"[vault-checklist] Vault not found at {paths['root']} "
                  f"(set vault_root or pass --vault-root)")
        sys.exit(1)

    dry_run = "--dry-run" in sys.argv
    as_of = _argv_value("--as-of")
    today = date.fromisoformat(as_of) if as_of else date.today()
    sheet = _argv_value("--sheet")

    token_write, item = None, None
    if "--local" in sys.argv:
        import pending_llt as pl
        explicit = _argv_value("--local")
        lp = Path(explicit) if explicit else pl.newest_local_llt(
            config.get("local_llt_dir"), config.get("local_llt_glob"))
        if not lp or not lp.exists():
            print("No local LLT sheet found.")
            sys.exit(1)
        file_bytes = lp.read_bytes()
        log.info(f"[vault-checklist] local sheet {lp}")
    else:
        from rocky import acquire_token, audit_token_scopes, get_msal_app
        import llt_watch
        token = acquire_token(get_msal_app(config))
        audit_token_scopes(token)
        stat = llt_watch.stat_llt_file(token, config)
        file_bytes = llt_watch.download_llt(token, config, stat) if stat else None
        if not file_bytes:
            log.error("[vault-checklist] could not download the LLT sheet")
            sys.exit(1)
        if "--no-write" not in sys.argv and not dry_run:
            token_write = acquire_write_token(config)
            if token_write:
                audit_token_scopes(token_write)
                item = locate_item(token_write, config)
            else:
                log.warning(f"[vault-checklist] no {SHEET_WRITE_SCOPES} token — "
                            f"report only; run --vault-checklist --consent once")

    summary = run(config, paths, file_bytes=file_bytes, sheet=sheet,
                  token_write=token_write, item=item, dry_run=dry_run,
                  do_archive="--no-archive" not in sys.argv,
                  repaint="--repaint" in sys.argv, today=today)
    log.info(f"[vault-checklist] {json.dumps(summary, default=str)}")
    print(json.dumps(summary, indent=1, default=str))
