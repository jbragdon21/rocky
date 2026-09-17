"""
PACER Monitor — federal case watching, discovery, and a shared case database
=============================================================================

    python rocky.py --pacer --sync [--dry-run] [--limit N] [--case <key>]
        The daily pass. Resolves watched cases to RECAP dockets, subscribes
        a free docket alert to each one, refreshes them in batches, records
        new docket entries, downloads whatever PDFs the archive already
        has, and (inside the daily spend cap) buys a fresh docket from
        PACER for anything RECAP has let go stale.

    python rocky.py --pacer --sweep [--name <sweep>] [--dry-run]
        Discovery. Runs the standing party-name searches in config
        pacer_sweeps against the PACER Case Locator — the nationwide index
        — to find NEW federal cases naming a client or a tenant. This is
        the one thing RECAP cannot do, and for a landlord-tenant practice
        it is mostly a bankruptcy tripwire: a tenant filing Chapter 7 or 13
        stays the eviction, and PCL sees that filing the day it happens.
        Billable per page of 54 results; every receipt is logged.

    python rocky.py --pacer --mail [--dry-run]
        Runs inside the --monitor loop. Scans rocky@'s inbox for mail whose
        subject carries the PACER keyword, works out what was asked
        (watch this case / what's the status / pull the docket / send me
        document 14 / search for this party), does it, and replies with
        the answer and any PDFs. The team's front door to all of this.

    python rocky.py --pacer --add <court> <docket-number> [--label "..."]
    python rocky.py --pacer --remove <court> <docket-number>
        Hand-manage the watchlist from the command line. The same list is
        editable as "PACER Watchlist.xlsx" in the database folder.

    python rocky.py --pacer --fetch <court> <docket-number> [--since YYYY-MM-DD]
        Buy a docket report from PACER right now (spend cap still applies).

    python rocky.py --pacer --docs <court> <docket-number> [--entry N]
        Download the documents RECAP already holds for a case. Free.

    python rocky.py --pacer --digest [--hours N] [--dry-run]
        Email the day's new filings across every watched case.

    python rocky.py --pacer --status | --reindex | --probe | --auth-test

Storage lives in the shared OneDrive folder (config pacer_monitor_root,
default "PACER Monitor Database" beside Rocky Cases):

    PACER Monitor Database\\
      PACER Watchlist.xlsx     hand-editable: what to watch
      PACER Index.xlsx         generated: every case, its last filing, its documents
      _db\\cases.jsonl         append-only case records (last write per key wins)
      _db\\entries.jsonl       append-only docket entries
      _db\\sweeps.jsonl        PCL discovery hits
      _db\\spend.jsonl         every billable transaction, with its receipt
      _db\\activity.jsonl      run events
      Documents\\<Court>\\<Case>\\  downloaded PDFs

The authoritative records are JSONL on the share — sync-safe, readable,
and recoverable, the same choice the Vault catalog makes. The queryable
SQLite index (C:\\Rocky\\pacer\\index.db) is derived and disposable:
--reindex rebuilds it from the JSONL at any time. A SQLite file living on
OneDrive would be a sync conflict waiting to happen.

Money: PCL page fees and RECAP Fetch purchases both draw down
pacer_daily_spend_cap. PCL reports its real fee in every receipt. PACER
does not tell the API what a docket report or PDF actually cost, so those
are recorded at the statutory $3.00 per-document ceiling — the ledger
therefore over-states spend rather than under-stating it, and the cap
bites early instead of late. Set pacer_purchase_enabled false to run on
free sources only.

Full guide: PACER_MONITOR.md
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import courtlistener as cl
import pacer_api

log = logging.getLogger("rocky.pacer")

CLAUDE_MODEL = "claude-sonnet-4-5"

# PACER's per-document fee ceiling. Used as the assumed cost of any
# purchase, because nothing in the API reports the actual charge.
PACER_DOCUMENT_FEE_CAP = 3.00

# CourtListener calls held back from the scheduled sync so an emailed
# request or a manual lookup later in the day still has budget. At the
# free tier's 125/day this leaves the sync 105.
DEFAULT_RESERVE_DAILY = 20

DEFAULT_DAILY_SPEND_CAP = 10.00
DEFAULT_STALE_DAYS = 3
DEFAULT_BACKFILL_DAYS = 30
DEFAULT_SWEEP_INTERVAL_DAYS = 7

_DEFAULT_CASES_ROOT = r"C:\Users\jbragdon\OneDrive - gejlaw.com\Rocky Cases"
_DB_DIRNAME = "_db"

WATCHLIST_HEADERS = [
    "Court", "Docket Number", "Label", "Client", "RRID", "Watch", "Notes",
]

README = """# PACER Monitor Database

Rocky's federal-court workspace. She watches the cases listed in
**PACER Watchlist.xlsx**, records every new docket entry, files the
documents she can get for free, and runs standing party-name searches
against the nationwide PACER Case Locator to catch new cases naming a
client or a tenant.

## What to edit

**PACER Watchlist.xlsx** is yours. One row per case:

| Column | What goes in it |
|---|---|
| Court | CourtListener/PACER court ID — `vaed`, `dcd`, `mdd`, `vaeb`, `dcb`, `mdb` |
| Docket Number | As the court writes it, e.g. `1:25-cv-00123` |
| Label | How you want it named in digests |
| Client | Who this is for |
| RRID | Matching Rocky Cases ID, if there is one |
| Watch | `Y` to watch it, `N` to park it |
| Notes | Anything |

Rocky adds rows herself when a sweep turns up a new case or someone emails
her one; those arrive with the source in Notes.

## What Rocky writes

**PACER Index.xlsx** — regenerated every run. Every case, when it last had
a filing, how many entries Rocky has recorded, and where its documents are.

**Documents\\** — PDFs, filed by court and case.

**_db\\** — the underlying records (JSONL). Don't hand-edit these; the
Excel files are the human surface. `_db\\spend.jsonl` is the money log:
every billable PACER transaction with its receipt.

## Costs

Reading the RECAP archive is free. Two things cost money and both draw
down a daily cap set in Rocky's config: PACER Case Locator searches
(about ten cents per page of 54 results) and buying a docket or PDF that
RECAP doesn't already have.
"""


# =============================================================================
# Paths and config
# =============================================================================

def resolve_root(config: dict) -> Path:
    """
    Where the shared database lives.

    Config wins. Failing that, look for an existing "PACER Monitor
    Database" beside each OneDrive root this machine actually has, since
    creating a second empty database in the wrong one is a silent,
    confusing failure. Only if nothing exists do we derive a path and
    create it.

    The dev laptop used to carry TWO roots — an unsynced
    "...\\OneDrive\\OneDrive - gejlaw.com" left behind when the account
    was relocated, alongside the real "...\\OneDrive - gejlaw.com". The
    unsynced one was removed 2026-09-06; the probe for it stays here so
    a machine that still has one is found rather than silently forked.
    """
    root_raw = (config.get("pacer_monitor_root") or "").strip()
    if root_raw:
        return Path(root_raw)

    cases_root = Path(config.get("cases_root") or _DEFAULT_CASES_ROOT)
    home = Path.home()
    bases = [cases_root.parent,
             home / "OneDrive - gejlaw.com",
             home / "OneDrive" / "OneDrive - gejlaw.com"]
    # A OneDrive folder shared into another account arrives prefixed with
    # the owner's name: on the Rocky laptop the same folder is
    # "James D. Bragdon's files - PACER Monitor Database". Probe both
    # spellings under every base before giving up and creating one.
    names = ["PACER Monitor Database",
             "James D. Bragdon's files - PACER Monitor Database"]
    candidates = [base / name for base in bases for name in names]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def get_paths(config: dict, data_dir: Path) -> dict:
    root = resolve_root(config)
    db = root / _DB_DIRNAME
    local = data_dir / "pacer"
    return {
        "root": root,
        "db": db,
        "documents": root / "Documents",
        "cases": db / "cases.jsonl",
        "entries": db / "entries.jsonl",
        "sweeps": db / "sweeps.jsonl",
        "spend": db / "spend.jsonl",
        "activity": db / "activity.jsonl",
        "watchlist": root / "PACER Watchlist.xlsx",
        "index_xlsx": root / "PACER Index.xlsx",
        "readme": root / "README.md",
        "local": local,
        "state": local / "state.json",
        # The mail sweep keeps its own cursor file so --pacer-mail (which
        # runs every monitor cycle) and a long --pacer --sync can hold
        # separate instance locks without clobbering each other's state.
        "mail_state": local / "mail_state.json",
        "sqlite": local / "index.db",
    }


def ensure_dirs(paths: dict) -> None:
    # Creating the shared root is worth a line in the log. This machine may
    # have more than one OneDrive tree (the dev laptop has two), and an
    # empty database quietly appearing in the wrong one is the failure mode
    # that costs an hour to notice. Set pacer_monitor_root to be sure.
    if not paths["root"].exists():
        log.warning(f"[pacer] creating the shared database at "
                    f"{paths['root']} — if that's the wrong OneDrive root, "
                    f"set pacer_monitor_root in config.json")
    for key in ("root", "db", "documents", "local"):
        paths[key].mkdir(parents=True, exist_ok=True)
    try:
        if (not paths["readme"].exists()
                or paths["readme"].read_text(encoding="utf-8") != README):
            paths["readme"].write_text(README, encoding="utf-8")
    except OSError as e:
        log.warning(f"[pacer] could not refresh README.md: {e}")


def load_state(paths: dict, which: str = "state") -> dict:
    try:
        return json.loads(paths[which].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(paths: dict, state: dict, which: str = "state") -> None:
    paths[which].parent.mkdir(parents=True, exist_ok=True)
    paths[which].write_text(json.dumps(state, indent=2), encoding="utf-8")


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(row)
    row.setdefault("recorded", cl.utc_now_iso())
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                # A torn last line (power loss mid-append) shouldn't cost
                # us the whole catalog.
                log.warning(f"[pacer] skipping unreadable line in {path.name}")
    return rows


def append_activity(paths: dict, event: dict) -> None:
    append_jsonl(paths["activity"], event)


def load_cases(paths: dict) -> dict[str, dict]:
    """Collapse the append-only case log to current state, last write wins."""
    cases: dict[str, dict] = {}
    for row in read_jsonl(paths["cases"]):
        key = row.get("key")
        if not key:
            continue
        merged = cases.get(key, {})
        merged.update({k: v for k, v in row.items() if v is not None})
        cases[key] = merged
    return cases


def load_entry_ids(paths: dict) -> set[int]:
    return {int(r["cl_entry_id"]) for r in read_jsonl(paths["entries"])
            if r.get("cl_entry_id")}


def cursor_datetime(cursor_iso: str | None, backfill_days: int) -> datetime:
    if cursor_iso:
        try:
            return datetime.fromisoformat(str(cursor_iso).replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc) - timedelta(days=backfill_days)


def advance_cursor(state: dict, key: str, message: dict) -> None:
    received = message.get("receivedDateTime")
    if received:
        state[key] = received


def case_key(court: str, docket_number: str) -> str:
    return f"{cl.normalize_court(court)}:{_normalize_docket_number(docket_number)}"


def _normalize_docket_number(number: str) -> str:
    """
    Docket numbers arrive spelled a dozen ways. Keep the human form but
    strip whitespace and lowercase the case-type letters so
    "1:25-CV-00123" and "1:25-cv-00123" are one case, not two.
    """
    return re.sub(r"\s+", "", (number or "")).lower()


def safe_name(text: str, limit: int = 80) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", (text or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return (cleaned or "Unknown")[:limit]


# =============================================================================
# Spend governor
# =============================================================================

class SpendGovernor:
    """
    Daily cap on billable PACER activity.

    Reads today's transactions out of the shared spend log rather than a
    local counter, so two machines running the monitor can't each spend a
    full cap. The log is the audit trail as well as the counter.
    """

    def __init__(self, paths: dict, config: dict):
        self.paths = paths
        self.cap = float(config.get("pacer_daily_spend_cap")
                         or DEFAULT_DAILY_SPEND_CAP)
        self.enabled = config.get("pacer_purchase_enabled", True) is not False
        self._today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._spent = self._sum_today()

    def _sum_today(self) -> float:
        total = 0.0
        for row in read_jsonl(self.paths["spend"]):
            if str(row.get("recorded", ""))[:10] == self._today:
                try:
                    total += float(row.get("fee") or 0)
                except (TypeError, ValueError):
                    continue
        return round(total, 2)

    @property
    def spent(self) -> float:
        return round(self._spent, 2)

    @property
    def remaining(self) -> float:
        return round(max(0.0, self.cap - self._spent), 2)

    def can_spend(self, estimate: float) -> bool:
        if not self.enabled:
            return False
        return (self._spent + estimate) <= self.cap

    def record(self, source: str, description: str, fee: float,
               pages: int = 0, receipt: dict | None = None) -> None:
        self._spent += float(fee or 0)
        append_jsonl(self.paths["spend"], {
            "source": source,
            "description": description,
            "fee": round(float(fee or 0), 2),
            "pages": int(pages or 0),
            "estimated": source != "pcl",
            "receipt": receipt or {},
            "day_total_after": round(self._spent, 2),
            "cap": self.cap,
        })

    def refuse(self, what: str, estimate: float) -> None:
        if not self.enabled:
            log.info(f"[pacer] purchases disabled — skipping {what}")
        else:
            log.warning(
                f"[pacer] daily cap reached (${self.spent:.2f} of "
                f"${self.cap:.2f}) — skipping {what} (est ${estimate:.2f})")


# =============================================================================
# Watchlist
# =============================================================================

def ensure_watchlist(paths: dict) -> None:
    if paths["watchlist"].exists():
        return
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Watchlist"
    ws.append(WATCHLIST_HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    # Watch=N so the example never gets looked up, and a docket number no
    # real case will ever carry so it can't collide with a case someone
    # actually adds.
    ws.append(["vaed", "0:00-cv-00000", "Example — delete this row",
               "Client name", "", "N", "Court IDs: vaed vawd dcd mdd "
               "vaeb vawb dcb mdb ca4 cadc"])
    widths = [10, 20, 40, 28, 12, 8, 50]
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width
    ws.freeze_panes = "A2"
    try:
        wb.save(paths["watchlist"])
        log.info(f"[pacer] created {paths['watchlist'].name}")
    except OSError as e:
        log.error(f"[pacer] could not create watchlist: {e}")


def read_watchlist(paths: dict) -> list[dict]:
    """Rows from PACER Watchlist.xlsx where Watch isn't explicitly off."""
    import openpyxl

    ensure_watchlist(paths)
    try:
        wb = openpyxl.load_workbook(paths["watchlist"], read_only=True,
                                    data_only=True)
    except (OSError, ValueError) as e:
        log.error(f"[pacer] could not read watchlist: {e}")
        return []

    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return []

    header = [str(h or "").strip().lower() for h in rows[0]]

    def col(name: str) -> int | None:
        for i, h in enumerate(header):
            if h == name.lower():
                return i
        return None

    idx = {name: col(name) for name in WATCHLIST_HEADERS}
    if idx["Court"] is None or idx["Docket Number"] is None:
        log.error("[pacer] watchlist needs 'Court' and 'Docket Number' columns")
        return []

    out: list[dict] = []
    for raw in rows[1:]:
        def val(name: str) -> str:
            i = idx.get(name)
            if i is None or i >= len(raw):
                return ""
            return str(raw[i] or "").strip()

        court, number = val("Court"), val("Docket Number")
        if not court or not number:
            continue
        watch = (val("Watch") or "Y").upper()
        if watch.startswith("N"):
            continue
        out.append({
            "key": case_key(court, number),
            "court": cl.normalize_court(court),
            "court_input": court,
            "docket_number": number,
            "label": val("Label"),
            "client": val("Client"),
            "rrid": val("RRID"),
            "notes": val("Notes"),
            "source": "watchlist",
        })
    return out


def append_watchlist_row(paths: dict, court: str, docket_number: str,
                         label: str = "", client: str = "", rrid: str = "",
                         notes: str = "") -> bool:
    """Add a case to the sheet. No-op if the case is already on it."""
    import openpyxl

    ensure_watchlist(paths)
    key = case_key(court, docket_number)
    if any(r["key"] == key for r in read_watchlist(paths)):
        return False
    try:
        wb = openpyxl.load_workbook(paths["watchlist"])
        ws = wb.worksheets[0]
        ws.append([court, docket_number, label, client, rrid, "Y", notes])
        wb.save(paths["watchlist"])
    except (OSError, ValueError) as e:
        log.error(f"[pacer] could not add {key} to the watchlist: {e}")
        return False
    log.info(f"[pacer] watchlist += {key} ({label or 'unlabeled'})")
    return True


def set_watchlist_watch(paths: dict, court: str, docket_number: str,
                        watch: str) -> bool:
    import openpyxl

    key = case_key(court, docket_number)
    try:
        wb = openpyxl.load_workbook(paths["watchlist"])
        ws = wb.worksheets[0]
        header = [str(c.value or "").strip().lower() for c in ws[1]]
        try:
            c_court = header.index("court") + 1
            c_number = header.index("docket number") + 1
            c_watch = header.index("watch") + 1
        except ValueError:
            log.error("[pacer] watchlist is missing expected columns")
            return False
        # Update EVERY matching row. A case can appear twice if someone
        # pasted it in twice; parking only the first leaves it watched.
        hits = 0
        for row in range(2, ws.max_row + 1):
            got = case_key(str(ws.cell(row, c_court).value or ""),
                           str(ws.cell(row, c_number).value or ""))
            if got == key:
                ws.cell(row, c_watch).value = watch
                hits += 1
        if hits:
            wb.save(paths["watchlist"])
            return True
    except (OSError, ValueError) as e:
        log.error(f"[pacer] could not update the watchlist: {e}")
    return False


def seed_from_case_index(config: dict) -> list[dict]:
    """
    Pull federal cases out of Rocky Case Index.xlsx.

    The index has no docket columns today (its matters are mostly state
    court). Add "PACER Court" and "Docket Number" columns to the sheet and
    every federal matter enrolls itself here. Absent those columns this
    returns nothing and says so once — it is not an error.
    """
    import openpyxl

    if config.get("pacer_case_index_seed") is False:
        return []
    cases_root = Path(config.get("cases_root") or _DEFAULT_CASES_ROOT)
    index_path = cases_root / "Rocky Case Index.xlsx"
    if not index_path.exists():
        return []
    try:
        wb = openpyxl.load_workbook(index_path, read_only=True, data_only=True)
    except (OSError, ValueError) as e:
        log.warning(f"[pacer] could not read the case index: {e}")
        return []

    out: list[dict] = []
    for ws in wb.worksheets:
        if "closed" in (ws.title or "").lower():
            continue
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        header = [str(h or "").strip().lower() for h in rows[0]]

        def find(*names: str) -> int | None:
            for i, h in enumerate(header):
                if h in names:
                    return i
            return None

        c_court = find("pacer court", "court", "federal court")
        c_number = find("docket number", "federal docket", "pacer docket")
        if c_court is None or c_number is None:
            continue
        c_rrid = find("rrid#", "rrid")
        c_name = find("file name", "case name")
        c_client = find("client")
        for raw in rows[1:]:
            def val(i: int | None) -> str:
                if i is None or i >= len(raw):
                    return ""
                return str(raw[i] or "").strip()

            court, number = val(c_court), val(c_number)
            if not court or not number:
                continue
            out.append({
                "key": case_key(court, number),
                "court": cl.normalize_court(court),
                "docket_number": number,
                "label": val(c_name),
                "client": val(c_client),
                "rrid": val(c_rrid),
                "source": "case-index",
            })
    wb.close()
    if not out:
        log.info("[pacer] Rocky Case Index has no 'PACER Court' / "
                 "'Docket Number' columns — add them to auto-enroll federal "
                 "matters (skipping)")
    return out


def build_watchlist(paths: dict, config: dict) -> dict[str, dict]:
    """Watchlist sheet + case-index seeds + anything already in the database."""
    watched: dict[str, dict] = {}
    for row in seed_from_case_index(config):
        watched[row["key"]] = row
    for row in read_watchlist(paths):
        watched.setdefault(row["key"], {}).update(row)
    for key, case in load_cases(paths).items():
        if case.get("watch") is False or case.get("status") == "removed":
            watched.pop(key, None)
            continue
        if key in watched:
            # Carry forward what Rocky learned; the sheet only supplies
            # identity and labels.
            for field in ("cl_docket_id", "pacer_case_id", "alert_id",
                          "last_entry_seen", "last_synced", "case_name",
                          "date_last_filing", "entry_count"):
                if case.get(field) is not None:
                    watched[key].setdefault(field, case[field])
        elif case.get("source") in ("sweep", "mail"):
            watched[key] = dict(case)
    return watched


# =============================================================================
# SQLite index (derived, rebuildable)
# =============================================================================

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    key TEXT PRIMARY KEY, court TEXT, docket_number TEXT, case_name TEXT,
    label TEXT, client TEXT, rrid TEXT, cl_docket_id INTEGER,
    pacer_case_id TEXT, date_filed TEXT, date_terminated TEXT,
    date_last_filing TEXT, nature_of_suit TEXT, cause TEXT,
    assigned_to TEXT, source TEXT, status TEXT, alert_id INTEGER,
    last_synced TEXT, last_entry_seen TEXT, entry_count INTEGER
);
CREATE TABLE IF NOT EXISTS entries (
    cl_entry_id INTEGER PRIMARY KEY, key TEXT, entry_number REAL,
    date_filed TEXT, description TEXT, document_count INTEGER,
    recorded TEXT
);
CREATE TABLE IF NOT EXISTS documents (
    cl_document_id INTEGER PRIMARY KEY, cl_entry_id INTEGER, key TEXT,
    document_number TEXT, attachment_number TEXT, description TEXT,
    is_available INTEGER, page_count INTEGER, local_path TEXT
);
CREATE TABLE IF NOT EXISTS sweeps (
    sweep_key TEXT PRIMARY KEY, sweep_name TEXT, court TEXT,
    case_number TEXT, case_title TEXT, jurisdiction TEXT, date_filed TEXT,
    party TEXT, nature_of_suit TEXT, bankruptcy_chapter TEXT,
    case_link TEXT, first_seen TEXT
);
CREATE INDEX IF NOT EXISTS idx_entries_key ON entries(key);
CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(date_filed);
CREATE INDEX IF NOT EXISTS idx_documents_key ON documents(key);
CREATE INDEX IF NOT EXISTS idx_sweeps_name ON sweeps(sweep_name);
"""


def rebuild_index(paths: dict) -> dict:
    """Rebuild index.db and PACER Index.xlsx from the JSONL of record."""
    paths["local"].mkdir(parents=True, exist_ok=True)
    tmp = paths["sqlite"].with_suffix(".rebuilding")
    tmp.unlink(missing_ok=True)

    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(_SCHEMA)
        cases = load_cases(paths)
        for key, case in cases.items():
            conn.execute(
                "INSERT OR REPLACE INTO cases VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, case.get("court"), case.get("docket_number"),
                 case.get("case_name"), case.get("label"), case.get("client"),
                 case.get("rrid"), case.get("cl_docket_id"),
                 case.get("pacer_case_id"), case.get("date_filed"),
                 case.get("date_terminated"), case.get("date_last_filing"),
                 case.get("nature_of_suit"), case.get("cause"),
                 case.get("assigned_to"), case.get("source"),
                 case.get("status"), case.get("alert_id"),
                 case.get("last_synced"), case.get("last_entry_seen"),
                 case.get("entry_count")))

        entry_rows = read_jsonl(paths["entries"])
        for row in entry_rows:
            docs = row.get("documents") or []
            conn.execute(
                "INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?,?,?)",
                (row.get("cl_entry_id"), row.get("key"),
                 row.get("entry_number"), row.get("date_filed"),
                 row.get("description"), len(docs), row.get("recorded")))
            for doc in docs:
                conn.execute(
                    "INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?,?,?)",
                    (doc.get("id"), row.get("cl_entry_id"), row.get("key"),
                     doc.get("document_number"), doc.get("attachment_number"),
                     doc.get("description"),
                     1 if doc.get("is_available") else 0,
                     doc.get("page_count"), doc.get("local_path")))

        sweep_rows = read_jsonl(paths["sweeps"])
        for row in sweep_rows:
            conn.execute(
                "INSERT OR REPLACE INTO sweeps VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (row.get("sweep_key"), row.get("sweep_name"), row.get("court"),
                 row.get("case_number"), row.get("case_title"),
                 row.get("jurisdiction"), row.get("date_filed"),
                 row.get("party"), row.get("nature_of_suit"),
                 row.get("bankruptcy_chapter"), row.get("case_link"),
                 row.get("recorded")))
        conn.commit()
    finally:
        conn.close()

    paths["sqlite"].unlink(missing_ok=True)
    tmp.replace(paths["sqlite"])

    counts = {"cases": len(cases), "entries": len(entry_rows),
              "sweeps": len(sweep_rows)}
    write_index_workbook(paths, cases, entry_rows, sweep_rows)
    log.info(f"[pacer] index rebuilt: {counts}")
    return counts


def write_index_workbook(paths: dict, cases: dict[str, dict],
                         entries: list[dict], sweeps: list[dict]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    bold = Font(bold=True)

    ws = wb.active
    ws.title = "Cases"
    ws.append(["Case", "Court", "Docket Number", "Client", "RRID",
               "Filed", "Last Filing", "Entries", "Status", "RRID Folder",
               "CourtListener", "Last Synced"])
    for cell in ws[1]:
        cell.font = bold
    for key in sorted(cases, key=lambda k: cases[k].get("date_last_filing")
                      or "", reverse=True):
        case = cases[key]
        docket_id = case.get("cl_docket_id")
        ws.append([
            case.get("case_name") or case.get("label") or key,
            case.get("court"), case.get("docket_number"),
            case.get("client"), case.get("rrid"), case.get("date_filed"),
            case.get("date_last_filing"), case.get("entry_count") or 0,
            case.get("status") or "active", case.get("rrid"),
            (f"https://www.courtlistener.com/docket/{docket_id}/"
             if docket_id else ""),
            case.get("last_synced"),
        ])

    ws2 = wb.create_sheet("Recent Filings")
    ws2.append(["Date", "Case", "Entry", "Description", "Documents",
                "Recorded"])
    for cell in ws2[1]:
        cell.font = bold
    recent = sorted(entries, key=lambda r: (r.get("date_filed") or "",
                                            r.get("recorded") or ""),
                    reverse=True)[:2000]
    for row in recent:
        case = cases.get(row.get("key") or "", {})
        ws2.append([
            row.get("date_filed"),
            case.get("case_name") or case.get("label") or row.get("key"),
            row.get("entry_number"),
            (row.get("description") or "")[:500],
            len(row.get("documents") or []),
            (row.get("recorded") or "")[:10],
        ])

    ws3 = wb.create_sheet("Discovered")
    ws3.append(["First Seen", "Sweep", "Party", "Case", "Court", "Number",
                "Type", "Chapter", "Filed", "Link"])
    for cell in ws3[1]:
        cell.font = bold
    for row in sorted(sweeps, key=lambda r: r.get("recorded") or "",
                      reverse=True)[:2000]:
        ws3.append([(row.get("recorded") or "")[:10], row.get("sweep_name"),
                    row.get("party"), row.get("case_title"), row.get("court"),
                    row.get("case_number"), row.get("jurisdiction"),
                    row.get("bankruptcy_chapter"), row.get("date_filed"),
                    row.get("case_link")])

    for sheet, widths in ((ws, [42, 10, 20, 26, 10, 12, 12, 9, 10, 12, 46, 22]),
                          (ws2, [12, 42, 8, 90, 11, 12]),
                          (ws3, [12, 22, 28, 42, 10, 20, 8, 9, 12, 46])):
        sheet.freeze_panes = "A2"
        for i, width in enumerate(widths, start=1):
            sheet.column_dimensions[
                sheet.cell(row=1, column=i).column_letter].width = width

    try:
        wb.save(paths["index_xlsx"])
    except OSError as e:
        # Someone has it open in Excel. Not worth failing a run over.
        log.warning(f"[pacer] could not write PACER Index.xlsx: {e}")


# =============================================================================
# Sync — the daily pass
# =============================================================================

def make_cl_client(config: dict, paths: dict,
                   bulk: bool = False) -> cl.CourtListenerClient:
    """
    `bulk=True` for the scheduled sync, which holds back
    courtlistener_reserve_daily calls so interactive work (an emailed
    request, a manual --docs) still has budget later in the day.
    """
    reserve = None
    if bulk:
        held = int(config.get("courtlistener_reserve_daily")
                   if config.get("courtlistener_reserve_daily") is not None
                   else DEFAULT_RESERVE_DAILY)
        reserve = {"day": held} if held else None
    return cl.CourtListenerClient(
        config.get("courtlistener_token") or "",
        paths["local"],
        rate_limits=config.get("courtlistener_rate_limits"),
        reserve=reserve,
    )


def make_pacer_client(config: dict, paths: dict) -> pacer_api.PacerClient:
    return pacer_api.PacerClient(
        config.get("pacer_username") or "",
        config.get("pacer_password") or "",
        paths["local"],
        environment=config.get("pacer_environment") or "production",
        client_code=config.get("pacer_client_code"),
        otp_secret=config.get("pacer_otp_secret"),
        redact_flag=bool(config.get("pacer_redact_flag")),
    )


def run_sync(config: dict, paths: dict, dry_run: bool = False,
             limit: int | None = None, only_key: str | None = None) -> dict:
    ensure_dirs(paths)
    state = load_state(paths)
    watched = build_watchlist(paths, config)
    if only_key:
        watched = {k: v for k, v in watched.items()
                   if k == only_key or only_key in k}
    if not watched:
        log.info("[pacer] nothing on the watchlist — add cases to "
                 f"{paths['watchlist'].name}")
        return {"cases": 0}

    client = make_cl_client(config, paths, bulk=True)
    spend = SpendGovernor(paths, config)
    known_entries = load_entry_ids(paths)
    stale_days = int(config.get("pacer_fetch_stale_days") or DEFAULT_STALE_DAYS)
    backfill_days = int(config.get("pacer_backfill_days")
                        or DEFAULT_BACKFILL_DAYS)
    download = config.get("pacer_download_documents", True) is not False

    counts = {"cases": len(watched), "resolved": 0, "unresolved": 0,
              "refreshed": 0, "new_entries": 0, "documents": 0,
              "purchased": 0, "alerts": 0, "budget_stops": 0, "deferred": 0}
    deferred: list[str] = []
    append_activity(paths, {"event": "sync_started", "cases": len(watched),
                            "dry_run": dry_run,
                            "budget": client.remaining_budget(effective=True),
                            "spend_remaining": spend.remaining})

    try:
        # 1. Resolve cases we don't have a RECAP docket id for. One call
        #    each, so this is the expensive half — cap it per run.
        #    Least-recently-attempted first: a fixed order would re-try the
        #    same head of the list every run and starve the tail forever
        #    once the watchlist outgrows the daily budget.
        unresolved = sorted(
            (c for c in watched.values() if not c.get("cl_docket_id")),
            key=lambda c: str(c.get("last_resolve_attempt") or ""))
        resolve_cap = int(config.get("pacer_resolve_per_run") or 10)
        for case in unresolved[:resolve_cap]:
            case["last_resolve_attempt"] = cl.utc_now_iso()
            docket = client.find_docket(case["court"], case["docket_number"])
            if docket:
                case.update(_case_from_docket(case, docket))
                counts["resolved"] += 1
                log.info(f"[pacer] resolved {case['key']} -> docket "
                         f"{docket['id']} ({docket.get('case_name')})")
            else:
                case["status"] = "not-in-recap"
                counts["unresolved"] += 1
                log.info(f"[pacer] {case['key']} is not in RECAP yet")
        if len(unresolved) > resolve_cap:
            deferred.extend(c["key"] for c in unresolved[resolve_cap:])
            log.info(f"[pacer] {len(unresolved) - resolve_cap} case(s) left to "
                     f"resolve on the next run (per-run cap)")

        # 2. Free docket alerts for everything resolved. CourtListener then
        #    notices changes for us, which is what keeps the read budget
        #    survivable on a growing watchlist.
        for case in watched.values():
            if case.get("cl_docket_id") and not case.get("alert_id") \
                    and not dry_run:
                try:
                    alert = client.create_docket_alert(case["cl_docket_id"])
                    case["alert_id"] = alert.get("id")
                    counts["alerts"] += 1
                except cl.RateBudgetExhausted:
                    raise
                except cl.CourtListenerError as e:
                    # Already subscribed, or the tier won't allow more.
                    log.info(f"[pacer] no alert for {case['key']}: "
                             f"{str(e)[:160]}")
                    case["alert_id"] = case.get("alert_id") or -1

        # 3. Refresh docket headers, ONE CALL EACH — /dockets/ has no
        #    batch filter (id__in doesn't exist; id is a range filter, and
        #    a range would drag in every unrelated docket in the span).
        #    Oldest-synced first, so a run cut short by the budget always
        #    makes progress on whatever has waited longest, and anything
        #    it couldn't reach goes to the deferral queue.
        resolved = sorted(
            (c for c in watched.values() if c.get("cl_docket_id")),
            key=lambda c: str(c.get("last_synced") or ""))
        if limit:
            resolved = resolved[:limit]
        by_id = {c["cl_docket_id"]: c for c in resolved}
        refreshed_ids: set[int] = set()
        for docket_id, docket in client.dockets_by_id_progressive(
                [c["cl_docket_id"] for c in resolved]):
            case = by_id.get(docket_id)
            if case and docket:
                case.update(_case_from_docket(case, docket))
                refreshed_ids.add(docket_id)
        unreached = [c for c in resolved
                     if c["cl_docket_id"] not in refreshed_ids]
        if unreached:
            counts["budget_stops"] += 1
            deferred.extend(c["key"] for c in unreached)
            log.warning(
                f"[pacer] API budget ran out during the header refresh — "
                f"queued {len(unreached)} case(s) for the next run: "
                + ", ".join(c["key"] for c in unreached[:6])
                + (" ..." if len(unreached) > 6 else ""))
            resolved = [c for c in resolved
                        if c["cl_docket_id"] in refreshed_ids]

        # 4. Pull entries only where something actually moved. Cases the
        #    budget can't reach this run go on the deferred list; they sort
        #    to the front of step 3 next time.
        needs_pull = [c for c in resolved if _needs_entry_pull(c)]
        for i, case in enumerate(needs_pull):
            try:
                added, docs = _pull_entries(
                    client, paths, case, known_entries, backfill_days,
                    download, dry_run)
            except cl.RateBudgetExhausted as e:
                counts["budget_stops"] += 1
                still_waiting = [c["key"] for c in needs_pull[i:]]
                deferred.extend(still_waiting)
                log.warning(
                    f"[pacer] {e} — queued {len(still_waiting)} case(s) for "
                    f"the next run: {', '.join(still_waiting[:6])}"
                    + (" ..." if len(still_waiting) > 6 else ""))
                break
            counts["refreshed"] += 1
            counts["new_entries"] += added
            counts["documents"] += docs

        # 5. Buy a docket for anything RECAP has let go stale, inside cap.
        if spend.enabled and not dry_run:
            counts["purchased"] = _purchase_stale(
                config, paths, client, spend, resolved, stale_days,
                known_entries, backfill_days, download)

    except cl.RateBudgetExhausted as e:
        counts["budget_stops"] += 1
        log.warning(f"[pacer] {e} — run ended early, state held")
    except cl.CourtListenerError as e:
        log.error(f"[pacer] CourtListener error: {e}")

    counts["deferred"] = len(deferred)
    if not dry_run:
        deferred_set = set(deferred)
        for case in watched.values():
            # A deferred case keeps its old last_synced on purpose. Bumping
            # it would sort the case to the BACK of the oldest-first queue
            # and starve exactly the case the budget just failed to reach.
            if case["key"] not in deferred_set:
                case["last_synced"] = cl.utc_now_iso()
            append_jsonl(paths["cases"], _case_record(case))
        state["deferred"] = deferred
        state["last_sync"] = cl.utc_now_iso()
        save_state(paths, state)
        rebuild_index(paths)

    counts["spend_today"] = spend.spent
    counts["api_budget_left"] = client.remaining_budget(effective=True)
    append_activity(paths, {"event": "sync_finished", **counts,
                            "dry_run": dry_run})
    log.info(f"[pacer] sync: {counts}")
    return counts


def _case_from_docket(case: dict, docket: dict) -> dict:
    return {
        "cl_docket_id": docket.get("id"),
        "case_name": docket.get("case_name") or case.get("label"),
        "pacer_case_id": docket.get("pacer_case_id"),
        "date_filed": docket.get("date_filed"),
        "date_terminated": docket.get("date_terminated"),
        "date_last_filing": docket.get("date_last_filing"),
        "date_modified": docket.get("date_modified"),
        "nature_of_suit": docket.get("nature_of_suit"),
        "cause": docket.get("cause"),
        "assigned_to": docket.get("assigned_to_str"),
        "status": ("terminated" if docket.get("date_terminated")
                   else "active"),
    }


def _case_record(case: dict) -> dict:
    keep = ("key", "court", "docket_number", "case_name", "label", "client",
            "rrid", "cl_docket_id", "pacer_case_id", "date_filed",
            "date_terminated", "date_last_filing", "date_modified",
            "nature_of_suit", "cause", "assigned_to", "source", "status",
            "alert_id", "last_synced", "last_entry_seen", "entry_count",
            "notes")
    return {k: case.get(k) for k in keep if case.get(k) is not None}


def _needs_entry_pull(case: dict) -> bool:
    """
    Only spend a call where the docket header says something changed, or
    where we've never pulled entries at all.
    """
    last_seen = case.get("last_entry_seen")
    if not last_seen:
        return True
    last_filing = case.get("date_last_filing")
    if not last_filing:
        return False
    return str(last_filing) > str(last_seen)


def _pull_entries(client, paths: dict, case: dict, known: set[int],
                  backfill_days: int, download: bool,
                  dry_run: bool) -> tuple[int, int]:
    since = case.get("last_entry_seen") or cl.days_ago_iso(backfill_days)
    rows = client.docket_entries(case["cl_docket_id"], since=since)
    new_rows = [r for r in rows if int(r.get("id", 0)) not in known]
    if not new_rows:
        return 0, 0

    doc_count = 0
    for entry in sorted(new_rows, key=lambda r: r.get("date_filed") or ""):
        documents = []
        for doc in entry.get("recap_documents") or []:
            record = {
                "id": doc.get("id"),
                "document_number": doc.get("document_number"),
                "attachment_number": doc.get("attachment_number"),
                "description": doc.get("description"),
                "is_available": bool(doc.get("is_available")),
                "page_count": doc.get("page_count"),
                "pacer_doc_id": doc.get("pacer_doc_id"),
                "filepath_local": doc.get("filepath_local"),
                "local_path": None,
            }
            if download and not dry_run and doc.get("filepath_local"):
                dest = _document_path(paths, case, entry, record)
                if dest.exists() or client.download_document(
                        doc["filepath_local"], dest):
                    record["local_path"] = str(dest)
                    doc_count += 1
            documents.append(record)

        if not dry_run:
            append_jsonl(paths["entries"], {
                "key": case["key"],
                "cl_entry_id": entry.get("id"),
                "cl_docket_id": case["cl_docket_id"],
                "entry_number": entry.get("entry_number"),
                "date_filed": entry.get("date_filed"),
                "description": entry.get("description"),
                "documents": documents,
            })
        known.add(int(entry.get("id", 0)))
        if entry.get("date_filed"):
            case["last_entry_seen"] = max(
                str(case.get("last_entry_seen") or ""),
                str(entry["date_filed"]))

    case["entry_count"] = int(case.get("entry_count") or 0) + len(new_rows)
    log.info(f"[pacer] {case['key']}: {len(new_rows)} new entr"
             f"{'y' if len(new_rows) == 1 else 'ies'}"
             + (f", {doc_count} document(s) filed" if doc_count else ""))
    return len(new_rows), doc_count


def _document_path(paths: dict, case: dict, entry: dict, doc: dict) -> Path:
    court = safe_name(case.get("court") or "unknown", 12)
    case_dir = safe_name(
        f"{case.get('docket_number') or case['key']} "
        f"{case.get('case_name') or case.get('label') or ''}".strip(), 90)
    number = doc.get("document_number") or entry.get("entry_number") or "0"
    attachment = doc.get("attachment_number")
    stem = f"{number}" + (f"-{attachment}" if attachment else "")
    date = entry.get("date_filed") or ""
    name = safe_name(f"{stem} - {date} - "
                     f"{(doc.get('description') or entry.get('description') or '')[:60]}")
    return paths["documents"] / court / case_dir / f"{name}.pdf"


def _purchase_stale(config, paths, client, spend: SpendGovernor,
                    resolved: list[dict], stale_days: int, known: set[int],
                    backfill_days: int, download: bool) -> int:
    """
    Buy a docket report for cases RECAP hasn't seen movement on in a while.

    Staleness is a proxy, not a fact: a quiet case and a case nobody has
    bought look identical from outside PACER. That's why this is capped by
    money rather than by certainty.
    """
    username = config.get("pacer_username")
    password = config.get("pacer_password")
    if not (username and password):
        log.info("[pacer] no PACER credentials — skipping purchases")
        return 0
    blocked = _fetch_blocked(paths)
    if blocked:
        # Permanent for this login, so don't burn a call on it every day.
        log.info(f"[pacer] RECAP Fetch unavailable since "
                 f"{blocked.get('since')} (MFA) — skipping purchases. "
                 f"See 'rocky.exe --pacer --fetch ...' for the options, or "
                 f"pass --retry-fetch to try again.")
        return 0

    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=stale_days)).strftime("%Y-%m-%d")
    candidates = [
        c for c in resolved
        if c.get("status") != "terminated"
        and str(c.get("date_last_filing") or "0000-00-00") < cutoff
        and str(c.get("last_purchased") or "")[:10] != datetime.now(
            timezone.utc).strftime("%Y-%m-%d")
    ]
    if not candidates:
        return 0

    bought = 0
    for case in candidates:
        if not spend.can_spend(PACER_DOCUMENT_FEE_CAP):
            spend.refuse(f"docket purchase for {case['key']}",
                         PACER_DOCUMENT_FEE_CAP)
            break
        try:
            queued = client.fetch_docket(
                username, password,
                docket_id=case["cl_docket_id"],
                date_start=case.get("last_entry_seen")
                or cl.days_ago_iso(backfill_days),
                client_code=config.get("pacer_client_code"))
            spend.record("recap-fetch",
                         f"docket report {case['key']}",
                         PACER_DOCUMENT_FEE_CAP)
            result = client.wait_for_fetch(queued.get("id"))
        except cl.RecapFetchLoginError as e:
            # Not this case's problem — the login itself can't work. Record
            # it once and stop, rather than failing per case forever.
            _mark_fetch_blocked(paths, str(e))
            log.error(f"[pacer] RECAP Fetch disabled: {str(e)[:300]}")
            break
        except (cl.CourtListenerError, KeyError) as e:
            log.warning(f"[pacer] purchase failed for {case['key']}: "
                        f"{str(e)[:200]}")
            continue

        case["last_purchased"] = cl.utc_now_iso()
        if result.get("status") == 2:
            bought += 1
            try:
                _pull_entries(client, paths, case, known, backfill_days,
                              download, dry_run=False)
            except cl.RateBudgetExhausted:
                log.warning("[pacer] bought the docket but the read budget is "
                            "spent — entries land on the next run")
                break
    return bought


# =============================================================================
# Sweep — PCL discovery
# =============================================================================

def run_sweep(config: dict, paths: dict, dry_run: bool = False,
              only_name: str | None = None) -> dict:
    """
    Standing party-name searches against the nationwide case index.

    Each configured sweep is a party search: a name, optionally narrowed
    to a jurisdiction type and a filing-date window. New hits are recorded
    and, if the sweep says so, added to the watchlist.
    """
    ensure_dirs(paths)
    sweeps = [s for s in (config.get("pacer_sweeps") or [])
              if s.get("enabled", True) is not False]
    if only_name:
        sweeps = [s for s in sweeps
                  if (s.get("name") or "").lower() == only_name.lower()]
    if not sweeps:
        log.info("[pacer] no sweeps configured (config pacer_sweeps)")
        return {"sweeps": 0}

    state = load_state(paths)
    spend = SpendGovernor(paths, config)
    seen = {row.get("sweep_key") for row in read_jsonl(paths["sweeps"])}
    interval = int(config.get("pacer_sweep_interval_days")
                   or DEFAULT_SWEEP_INTERVAL_DAYS)
    last_run = state.get("sweep_last_run") or {}

    try:
        pacer = make_pacer_client(config, paths)
    except pacer_api.PacerError as e:
        log.error(f"[pacer] {e}")
        return {"sweeps": 0, "error": str(e)}

    counts = {"sweeps": 0, "hits": 0, "new": 0, "watchlisted": 0,
              "fee": 0.0, "skipped": 0}

    for sweep in sweeps:
        name = sweep.get("name") or sweep.get("party_last_name") or "unnamed"
        due = _sweep_due(last_run.get(name), sweep.get("interval_days")
                         or interval)
        if not due and not only_name:
            counts["skipped"] += 1
            continue

        criteria = _sweep_criteria(sweep)
        if not criteria:
            log.warning(f"[pacer] sweep {name!r} has no searchable name — "
                        f"needs party_last_name")
            continue

        max_pages = int(sweep.get("max_pages") or 2)
        estimate = 0.10 * max_pages if pacer.billable else 0.0
        if estimate and not spend.can_spend(estimate):
            spend.refuse(f"sweep {name!r}", estimate)
            continue

        try:
            rows, receipts = pacer.search_parties_all_pages(
                criteria, max_pages=max_pages)
        except pacer_api.PacerError as e:
            log.error(f"[pacer] sweep {name!r} failed: {e}")
            continue

        fee = sum(pacer_api.receipt_fee(r) for r in receipts)
        pages = sum(pacer_api.receipt_pages(r) for r in receipts)
        if fee and not dry_run:
            spend.record("pcl", f"sweep {name!r}", fee, pages,
                         receipt=receipts[-1] if receipts else {})
        counts["fee"] += fee
        counts["sweeps"] += 1
        counts["hits"] += len(rows)

        new_here = 0
        for row in rows:
            court_case = row.get("courtCase") or row
            key = f"{name}|{pacer_api.case_key(court_case)}"
            if key in seen:
                continue
            seen.add(key)
            new_here += 1
            record = {
                "sweep_key": key,
                "sweep_name": name,
                "party": " ".join(
                    p for p in (row.get("firstName"), row.get("lastName"))
                    if p and p.strip()).strip() or criteria.get("lastName"),
                "party_role": row.get("partyRole"),
                "court": court_case.get("courtId"),
                "case_number": court_case.get("caseNumberFull"),
                "case_title": court_case.get("caseTitle"),
                "jurisdiction": court_case.get("jurisdictionType"),
                "date_filed": court_case.get("dateFiled"),
                "nature_of_suit": court_case.get("natureOfSuit"),
                "bankruptcy_chapter": court_case.get("bankruptcyChapter"),
                "case_link": court_case.get("caseLink"),
                "pacer_case_id": court_case.get("caseId"),
            }
            if not dry_run:
                append_jsonl(paths["sweeps"], record)
            log.info(f"[pacer] sweep {name!r} found: "
                     f"{record['case_title']} ({record['court']} "
                     f"{record['case_number']}, filed {record['date_filed']})")

            if sweep.get("auto_watch") and not dry_run:
                court_cl = _pcl_court_to_cl(record["court"])
                number = _pcl_number_to_docket(record["case_number"])
                if court_cl and number and append_watchlist_row(
                        paths, court_cl, number,
                        label=record["case_title"] or "",
                        client=sweep.get("client") or "",
                        notes=f"discovered by sweep {name!r} "
                              f"{datetime.now().strftime('%Y-%m-%d')}"):
                    counts["watchlisted"] += 1

        counts["new"] += new_here
        if not dry_run:
            last_run[name] = cl.utc_now_iso()
        log.info(f"[pacer] sweep {name!r}: {len(rows)} row(s), "
                 f"{new_here} new, ${fee:.2f}")

    if not dry_run:
        state["sweep_last_run"] = last_run
        save_state(paths, state)
        rebuild_index(paths)
    counts["fee"] = round(counts["fee"], 2)
    append_activity(paths, {"event": "sweep_finished", **counts,
                            "dry_run": dry_run})
    log.info(f"[pacer] sweep: {counts}")
    return counts


def _sweep_due(last_iso: str | None, interval_days: int) -> bool:
    if not last_iso:
        return True
    try:
        last = datetime.fromisoformat(str(last_iso).replace("Z", "+00:00"))
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - last).days >= max(1, interval_days)


def _sweep_criteria(sweep: dict) -> dict:
    last_name = (sweep.get("party_last_name") or "").strip()
    if not last_name:
        return {}
    criteria: dict = {"lastName": last_name}
    if sweep.get("party_first_name"):
        criteria["firstName"] = sweep["party_first_name"]
    court_case: dict = {}
    if sweep.get("jurisdiction_type"):
        court_case["jurisdictionType"] = sweep["jurisdiction_type"]
    if sweep.get("courts"):
        court_case["courtId"] = list(sweep["courts"])
    if sweep.get("bankruptcy_chapters"):
        court_case["federalBankruptcyChapter"] = [
            str(c) for c in sweep["bankruptcy_chapters"]]
    days = int(sweep.get("date_filed_days") or 0)
    if days:
        court_case["dateFiledFrom"] = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).strftime("%Y-%m-%d")
    if sweep.get("date_filed_from"):
        court_case["dateFiledFrom"] = sweep["date_filed_from"]
    if court_case:
        criteria["courtCase"] = court_case
    return criteria


def _pcl_court_to_cl(pcl_court: str) -> str:
    """PCL spelling -> CourtListener spelling (vaedc -> vaed, vaebk -> vaeb)."""
    return cl.normalize_court(pacer_api.pcl_court_to_cl(pcl_court))


def _pcl_number_to_docket(case_number_full: str) -> str:
    """
    PCL writes "1:2025cv01234"; courts and CourtListener write
    "1:25-cv-01234". Convert when the shape is recognizable, otherwise
    hand back what we got.
    """
    text = (case_number_full or "").strip()
    m = re.match(r"^(?:(\d+):)?(\d{4})([a-z]{2})(\d+)$", text, re.I)
    if not m:
        return text
    office, year, case_type, number = m.groups()
    short_year = year[-2:]
    core = f"{short_year}-{case_type.lower()}-{number}"
    return f"{office}:{core}" if office else core


def _docket_to_pcl_number(docket_number: str) -> str:
    """
    The other direction: "1:25-cv-00123" -> "1:2025cv00123". PCL accepts
    several spellings, but the four-digit-year form is the one its own
    results use, so it's the safest retry.
    """
    text = (docket_number or "").strip()
    m = re.match(r"^(?:(\d+):)?(\d{2})-?([a-z]{2})-?(\d+)$", text, re.I)
    if not m:
        return text
    office, year, case_type, number = m.groups()
    century = "19" if int(year) > 60 else "20"
    core = f"{century}{year}{case_type.lower()}{number.zfill(5)}"
    return f"{office}:{core}" if office else core


# =============================================================================
# Mail — the team's front door
# =============================================================================

MAIL_INTENT_PROMPT = """You are reading an email sent to a law firm's \
paralegal assistant asking her to do something with PACER (the federal \
courts' records system). Work out what is being asked.

Return ONLY a JSON object:
{
  "intent": "watch" | "status" | "docket" | "document" | "search" | "unknown",
  "court": "<CourtListener/PACER court id if identifiable, else null>",
  "docket_number": "<e.g. 1:25-cv-00123, else null>",
  "case_name": "<case caption if given, else null>",
  "party_name": "<last name or entity name to search for, else null>",
  "jurisdiction_type": "cv" | "bk" | "cr" | "ap" | "mdl" | null,
  "document_number": <integer or null>,
  "label": "<short label for the case, else null>",
  "confidence": 0.0-1.0,
  "reasoning": "<one sentence>"
}

Intents:
- "watch": add a case to the monitoring list.
- "status": what has happened lately in a case we already track.
- "docket": send the docket / full list of filings for a case.
- "document": send a specific filing (document_number).
- "search": find federal cases involving a party (person or company).
- "unknown": anything else.

Court ids look like: vaed (E.D. Va.), vaew (W.D. Va.), dcd (D.D.C.),
mdd (D. Md.), vaeb / vawb / dcb / mdb (the matching bankruptcy courts),
ca4 (Fourth Circuit), cadc (D.C. Circuit).

Subject: {subject}
From: {sender}

Body:
{body}
"""


def run_mail(config: dict, paths: dict, dry_run: bool = False) -> dict:
    """Scan rocky@'s inbox for PACER requests and answer them."""
    from anthropic import Anthropic
    from rocky import acquire_app_token
    import outbound
    # vault.fetch_inbox_messages is the shared Graph paging helper; the
    # cursor bookkeeping is local so this module doesn't lean on vault's
    # private functions.
    import vault

    ensure_dirs(paths)
    state = load_state(paths, "mail_state")
    mailbox = (config.get("pacer_mailbox") or config.get("rocky_email")
               or "rocky@gallagherllp.com")
    keyword = (config.get("pacer_mail_subject_keyword") or "pacer").lower()
    backfill = int(config.get("pacer_backfill_days") or 7)

    token = acquire_app_token(config)
    since = cursor_datetime(state.get("mail_cursor"), backfill)
    messages = vault.fetch_inbox_messages(token, mailbox, since,
                                          attachments_only=False)
    requests_found = [m for m in messages
                      if keyword in (m.get("subject") or "").lower()]
    log.info(f"[pacer] mail: {len(requests_found)} request(s) of "
             f"{len(messages)} new message(s) in {mailbox}")

    counts = {"messages": len(requests_found), "handled": 0, "replied": 0,
              "unclear": 0}
    if not requests_found:
        if messages and not dry_run:
            state["mail_cursor"] = messages[-1].get("receivedDateTime")
            save_state(paths, state, "mail_state")
        return counts

    client = Anthropic(api_key=config["anthropic_api_key"])
    cl_client = None
    try:
        cl_client = make_cl_client(config, paths)
    except cl.CourtListenerError as e:
        log.error(f"[pacer] {e}")

    for message in messages:
        if message not in requests_found:
            if not dry_run:
                advance_cursor(state, "mail_cursor", message)
            continue

        sender = (((message.get("from") or {}).get("emailAddress") or {})
                  .get("address") or "")
        sender_name = (((message.get("from") or {}).get("emailAddress") or {})
                       .get("name") or sender)
        subject = message.get("subject") or ""
        body = ((message.get("body") or {}).get("content")
                or message.get("bodyPreview") or "")[:6000]

        intent = _classify_mail(client, subject, sender, body)
        log.info(f"[pacer] mail from {sender}: intent={intent.get('intent')} "
                 f"conf={intent.get('confidence')} — "
                 f"{intent.get('reasoning', '')[:120]}")

        reply = _handle_mail_intent(config, paths, cl_client, intent,
                                    sender_name, dry_run)
        counts["handled"] += 1
        if intent.get("intent") in (None, "unknown"):
            counts["unclear"] += 1

        if not dry_run and outbound.is_allowed_recipient(sender):
            result = outbound.send_mail_guarded(
                token, mailbox, [sender],
                f"Re: {subject}" if not subject.lower().startswith("re:")
                else subject,
                reply["body"], body_type="Text",
                attachments=reply.get("attachments") or None)
            if result.get("sent"):
                counts["replied"] += 1
        elif not outbound.is_allowed_recipient(sender):
            log.warning(f"[pacer] not replying to external sender {sender}")

        if not dry_run:
            append_activity(paths, {
                "event": "mail_request", "from": sender, "subject": subject,
                "intent": intent.get("intent"),
                "confidence": intent.get("confidence"),
                "case": intent.get("docket_number"),
            })
            advance_cursor(state, "mail_cursor", message)

    if not dry_run:
        save_state(paths, state, "mail_state")
        rebuild_index(paths)
    log.info(f"[pacer] mail: {counts}")
    return counts


def _classify_mail(client, subject: str, sender: str, body: str) -> dict:
    prompt = MAIL_INTENT_PROMPT.format(subject=subject, sender=sender,
                                       body=body)
    try:
        resp = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=800,
            messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in resp.content if b.type == "text")
    except Exception as e:
        log.error(f"[pacer] intent classification failed: {e}")
        return {"intent": "unknown", "confidence": 0.0,
                "reasoning": f"classifier error: {e}"}
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {"intent": "unknown", "confidence": 0.0,
                "reasoning": "no JSON in response"}
    try:
        return json.loads(match.group(0))
    except ValueError:
        return {"intent": "unknown", "confidence": 0.0,
                "reasoning": "unparseable JSON"}


def _handle_mail_intent(config: dict, paths: dict, cl_client, intent: dict,
                        sender_name: str, dry_run: bool) -> dict:
    """Do the thing and compose the reply. Returns {body, attachments}."""
    first = (sender_name or "").split(",")[-1].strip().split(" ")[0] or "there"
    what = intent.get("intent")
    court = cl.normalize_court(intent.get("court") or "")
    number = intent.get("docket_number") or ""
    lines = [f"Hi {first},", ""]
    attachments: list[dict] = []

    if what == "watch" and court and number:
        added = (not dry_run) and append_watchlist_row(
            paths, court, number, label=intent.get("label")
            or intent.get("case_name") or "",
            notes=f"added by email {datetime.now().strftime('%Y-%m-%d')}")
        lines.append(
            f"{'Added' if added else 'Already watching'} {court} {number}"
            + (f" ({intent.get('case_name')})" if intent.get("case_name")
               else "") + ".")
        lines.append("I'll pick up new filings on the next sync and include "
                     "them in the daily digest.")

    elif what in ("status", "docket") and court and number:
        key = case_key(court, number)
        cases = load_cases(paths)
        case = cases.get(key)
        if not case:
            lines.append(f"I'm not watching {court} {number} yet. Reply "
                         f"'watch it' and I'll add it.")
        else:
            entries = [e for e in read_jsonl(paths["entries"])
                       if e.get("key") == key]
            entries.sort(key=lambda e: (e.get("date_filed") or ""),
                         reverse=True)
            show = entries if what == "docket" else entries[:10]
            lines.append(f"{case.get('case_name') or key} — "
                         f"{court} {case.get('docket_number')}")
            if case.get("assigned_to"):
                lines.append(f"Before {case['assigned_to']}. "
                             f"Filed {case.get('date_filed')}."
                             + (f" Terminated {case['date_terminated']}."
                                if case.get("date_terminated") else ""))
            lines.append("")
            if not show:
                lines.append("No docket entries recorded yet.")
            for entry in show:
                docs = entry.get("documents") or []
                have = sum(1 for d in docs if d.get("local_path"))
                lines.append(
                    f"  {entry.get('date_filed')}  #{entry.get('entry_number')}"
                    f"  {(entry.get('description') or '')[:160]}"
                    + (f"   [{have}/{len(docs)} PDFs on file]" if docs else ""))
            if case.get("cl_docket_id"):
                lines.append("")
                lines.append("https://www.courtlistener.com/docket/"
                             f"{case['cl_docket_id']}/")

    elif what == "document" and court and number:
        key = case_key(court, number)
        wanted = intent.get("document_number")
        found = []
        for entry in read_jsonl(paths["entries"]):
            if entry.get("key") != key:
                continue
            if wanted is not None and str(entry.get("entry_number")) != str(wanted):
                continue
            for doc in entry.get("documents") or []:
                if doc.get("local_path") and Path(doc["local_path"]).exists():
                    found.append((entry, doc))
        if not found:
            lines.append(f"I don't have document {wanted} in {court} {number} "
                         f"on file yet. I'll try to pull it on the next sync; "
                         f"if RECAP doesn't have it I'll need to buy it from "
                         f"PACER.")
        else:
            for entry, doc in found[:5]:
                path = Path(doc["local_path"])
                attachments.append({"name": path.name, "path": str(path)})
                lines.append(f"Attached: #{entry.get('entry_number')} "
                             f"{(entry.get('description') or '')[:120]}")

    elif what == "search" and intent.get("party_name"):
        # Search the free archive first. Only a case nobody has ever
        # purchased is missing from it, and that's the case worth spending
        # a PCL page fee on — which James decides, not an emailed request.
        rows = []
        if cl_client:
            try:
                rows = cl_client.search_recap(
                    intent["party_name"],
                    extra={"court": court} if court else None,
                    result_type="d")
            except cl.CourtListenerError as e:
                log.warning(f"[pacer] search for "
                            f"{intent['party_name']!r} failed: {e}")
        if rows:
            lines.append(f"{len(rows)} case(s) in the RECAP archive matching "
                         f"'{intent['party_name']}':")
            lines.append("")
            for row in rows[:15]:
                lines.append(
                    f"  {row.get('court_id') or '?':<8} "
                    f"{row.get('docketNumber') or '?':<22} "
                    f"{(row.get('caseName') or '')[:60]}"
                    f"   filed {row.get('dateFiled') or '?'}")
            lines.append("")
            lines.append("Reply with the docket number of any of these and "
                         "I'll start watching it.")
        else:
            lines.append(f"Nothing in the RECAP archive matches "
                         f"'{intent['party_name']}'. That archive only holds "
                         f"what someone has already purchased, so a brand-new "
                         f"or obscure case can be missing from it.")
            lines.append("Searching the full nationwide index costs a PACER "
                         "page fee, so I haven't. James can add a standing "
                         "search for this name and the hits will show up in "
                         "the Discovered tab of PACER Index.xlsx.")

    else:
        lines.append("I couldn't tell what you needed from PACER. I can:")
        lines.append("  - watch a case:      'watch 1:25-cv-00123 in vaed'")
        lines.append("  - report status:     'status on 1:25-cv-00123 vaed'")
        lines.append("  - send the docket:   'docket for 1:25-cv-00123 vaed'")
        lines.append("  - send a filing:     'send document 14 in 1:25-cv-00123 vaed'")
        lines.append("Include the court and the full docket number and I'll "
                     "take it from there.")

    lines += ["", "— Rocky"]
    return {"body": "\n".join(lines), "attachments": attachments}


# =============================================================================
# Digest
# =============================================================================

def run_digest(config: dict, paths: dict, hours: int = 24,
               dry_run: bool = False) -> dict:
    """Email the day's new federal filings across every watched case."""
    from rocky import acquire_app_token
    import outbound

    ensure_dirs(paths)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    cases = load_cases(paths)
    fresh = [e for e in read_jsonl(paths["entries"])
             if str(e.get("recorded") or "") >= cutoff]
    discovered = [s for s in read_jsonl(paths["sweeps"])
                  if str(s.get("recorded") or "") >= cutoff]

    if not fresh and not discovered:
        log.info("[pacer] nothing new in the window — no digest")
        return {"sent": False, "reason": "quiet"}

    by_case: dict[str, list[dict]] = {}
    for entry in fresh:
        by_case.setdefault(entry.get("key") or "?", []).append(entry)

    today = datetime.now().strftime("%A, %B %d, %Y")
    lines = [f"PACER Monitor — {today}", ""]
    if by_case:
        lines.append(f"NEW FILINGS ({len(fresh)} across {len(by_case)} case"
                     f"{'s' if len(by_case) != 1 else ''})")
        lines.append("")
        for key, entries in by_case.items():
            case = cases.get(key, {})
            lines.append(f"{case.get('case_name') or case.get('label') or key}"
                         f"  ({case.get('court')} {case.get('docket_number')})")
            for entry in sorted(entries,
                                key=lambda e: e.get("date_filed") or ""):
                docs = entry.get("documents") or []
                have = sum(1 for d in docs if d.get("local_path"))
                lines.append(f"    {entry.get('date_filed')}  "
                             f"#{entry.get('entry_number')}  "
                             f"{(entry.get('description') or '')[:180]}"
                             + (f"   [{have}/{len(docs)} PDFs filed]"
                                if docs else ""))
            lines.append("")

    if discovered:
        lines.append(f"NEW CASES FOUND ({len(discovered)})")
        lines.append("")
        for row in discovered:
            lines.append(f"    {row.get('date_filed')}  "
                         f"{row.get('case_title')}  "
                         f"({row.get('court')} {row.get('case_number')}, "
                         f"{row.get('jurisdiction')}"
                         + (f" ch. {row['bankruptcy_chapter']}"
                            if row.get("bankruptcy_chapter") else "") + ")")
            lines.append(f"        matched sweep '{row.get('sweep_name')}' "
                         f"on {row.get('party')}")
        lines.append("")

    spend = SpendGovernor(paths, config)
    lines.append(f"PACER spend today: ${spend.spent:.2f} of "
                 f"${spend.cap:.2f}.")
    lines.append(f"Database: {paths['root']}")

    body = "\n".join(lines)
    recipients = config.get("pacer_digest_recipients") or [
        config.get("user_email") or "jbragdon@gallagherllp.com"]
    if dry_run:
        print(body)
        return {"sent": False, "reason": "dry_run", "cases": len(by_case)}

    token = acquire_app_token(config)
    mailbox = config.get("rocky_email") or "rocky@gallagherllp.com"
    result = outbound.send_mail_guarded(
        token, mailbox, recipients,
        f"PACER Monitor — {len(fresh)} new filing"
        f"{'s' if len(fresh) != 1 else ''}"
        + (f", {len(discovered)} new case"
           f"{'s' if len(discovered) != 1 else ''}" if discovered else ""),
        body)
    append_activity(paths, {"event": "digest", "entries": len(fresh),
                            "discovered": len(discovered),
                            "sent": result.get("sent")})
    return {"sent": bool(result.get("sent")), "cases": len(by_case),
            "entries": len(fresh)}


# =============================================================================
# Status / probes / one-shots
# =============================================================================

def print_status(config: dict, paths: dict) -> None:
    ensure_dirs(paths)
    cases = load_cases(paths)
    entries = read_jsonl(paths["entries"])
    sweeps = read_jsonl(paths["sweeps"])
    state = load_state(paths)
    spend = SpendGovernor(paths, config)

    print("PACER Monitor")
    print(f"  Database:      {paths['root']}")
    print(f"  Local index:   {paths['sqlite']} "
          f"({'built' if paths['sqlite'].exists() else 'not built'})")
    print(f"  Cases:         {len(cases)} "
          f"({sum(1 for c in cases.values() if c.get('cl_docket_id'))} "
          f"resolved in RECAP)")
    print(f"  Docket entries:{len(entries):>6}")
    print(f"  Discovered:    {len(sweeps):>6}")
    print(f"  Last sync:     {state.get('last_sync') or 'never'}")
    print(f"  Spend today:   ${spend.spent:.2f} of ${spend.cap:.2f}"
          f"{'' if spend.enabled else '  (purchases DISABLED)'}")
    blocked = state.get("recap_fetch_blocked")
    if blocked:
        print(f"  RECAP Fetch:   UNAVAILABLE since "
              f"{str(blocked.get('since'))[:19]} — CourtListener can't log "
              f"into an MFA-enabled PACER account.")
        print("                 Docket contents come from @recap.email "
              "instead (free). Run --pacer --fetch for the full note.")

    queued = state.get("deferred") or []
    if queued:
        print(f"  Queued:        {len(queued)} case(s) waiting on API budget")
        for key in queued[:8]:
            print(f"      {key}")
        if len(queued) > 8:
            print(f"      ... and {len(queued) - 8} more")

    token = config.get("courtlistener_token")
    print(f"  CourtListener: {'token set' if token else 'NO TOKEN'}")
    if token:
        try:
            client = make_cl_client(config, paths, bulk=True)
            gov = client.governor
            print(f"  API limits:    {gov.limits}"
                  + (f"  (sync holds back {gov.reserve.get('day')}/day"
                     f" for interactive work)" if gov.reserve else ""))
            print(f"  Left now:      {gov.remaining()} total, "
                  f"{gov.remaining(effective=True)} available to a sync")
            # Their count, not ours. The /api-usage endpoint is on its own
            # throttle scope, so asking costs nothing from the main budget.
            usage = client.api_usage()
            print(f"  Their count:   {json.dumps(usage)[:300]}")
        except cl.RateBudgetExhausted as e:
            print(f"  API budget:    exhausted ({e})")
        except cl.CourtListenerError as e:
            print(f"  API budget:    unavailable — {e}")
    creds = bool(config.get("pacer_username") and config.get("pacer_password"))
    print(f"  PACER:         "
          f"{'credentials set' if creds else 'no credentials'} "
          f"({config.get('pacer_environment') or 'production'})")
    if config.get("pacer_otp_secret"):
        try:
            print(f"  PACER MFA:     secret set — current code "
                  f"{pacer_api.totp_now(config['pacer_otp_secret'])} "
                  f"(rolls in "
                  f"{pacer_api.seconds_until_next_totp():.0f}s)")
        except pacer_api.PacerError as e:
            print(f"  PACER MFA:     BAD SECRET — {e}")
    elif creds:
        print("  PACER MFA:     no pacer_otp_secret set (fine if the "
              "account has no MFA)")
    if creds:
        print(f"  Filer flag:    "
              f"{'redaction certification WILL be sent' if config.get('pacer_redact_flag') else 'off (required if the account can e-file)'}")

    sweep_cfg = config.get("pacer_sweeps") or []
    print(f"  Sweeps:        {len(sweep_cfg)} configured")
    for sweep in sweep_cfg:
        name = sweep.get("name") or sweep.get("party_last_name")
        last = (state.get("sweep_last_run") or {}).get(name) or "never"
        print(f"      {name:<28} last run {last}")


def run_auth_test(config: dict, paths: dict) -> None:
    ensure_dirs(paths)
    print("CourtListener:")
    try:
        client = make_cl_client(config, paths)
        usage = client.api_usage()
        print(f"  OK — usage: {json.dumps(usage)[:400]}")
        print(f"  Local budget left: {client.remaining_budget()}")
    except cl.CourtListenerError as e:
        print(f"  FAILED — {e}")

    print("PACER:")
    try:
        pacer = make_pacer_client(config, paths)
        token = pacer.authenticate(force=True)
        print(f"  OK — {pacer.env} token acquired ({len(token)} chars)")
    except pacer_api.PacerError as e:
        print(f"  FAILED — {e}")


def run_probe(config: dict, paths: dict, court: str, number: str) -> None:
    """One case end to end, printing what each source knows. Calibration."""
    ensure_dirs(paths)
    print(f"Probing {court} {number}\n")
    try:
        client = make_cl_client(config, paths)
        docket = client.find_docket(court, number)
        if docket:
            print("CourtListener/RECAP:")
            for field in ("id", "case_name", "docket_number", "date_filed",
                          "date_last_filing", "date_terminated",
                          "assigned_to_str", "nature_of_suit", "cause",
                          "pacer_case_id"):
                print(f"  {field:<20} {docket.get(field)}")
            entries = client.docket_entries(docket["id"], max_pages=1)
            print(f"\n  {len(entries)} recent entr"
                  f"{'y' if len(entries) == 1 else 'ies'}:")
            for entry in entries[:10]:
                docs = entry.get("recap_documents") or []
                available = sum(1 for d in docs if d.get("is_available"))
                print(f"    {entry.get('date_filed')}  "
                      f"#{entry.get('entry_number')}  "
                      f"{(entry.get('description') or '')[:100]}"
                      f"   [{available}/{len(docs)} PDFs in RECAP]")
        else:
            print("CourtListener/RECAP: case not in the archive")
        print(f"\n  API budget left: {client.remaining_budget()}")
    except cl.CourtListenerError as e:
        print(f"CourtListener: FAILED — {e}")

    try:
        pacer = make_pacer_client(config, paths)
        # Scope to the court. A case-number search with no courtId matches
        # the same number in every district in the country — 46 hits on the
        # first live probe — and PCL bills per page retrieved.
        pcl_court = pacer_api.cl_court_to_pcl(court)
        if not pcl_court:
            print(f"\nPACER Case Locator: can't map {court!r} to a PCL court "
                  f"id, so this search would cover every district and bill "
                  f"for it. Skipping. Use the PACER spelling (vaedc, vaebk, "
                  f"dcdc, mddc, 04ca) to search directly.")
            return
        courts = [pcl_court]
        data = pacer.find_case_by_number(number, court_ids=courts)
        rows = data.get("content") or []
        receipt = data.get("receipt") or {}
        fee = pacer_api.receipt_fee(receipt)
        if not rows:
            # PCL writes 1:2025cv00123 where the court writes 1:25-cv-00123.
            alt = _docket_to_pcl_number(number)
            if alt and alt != number:
                data = pacer.find_case_by_number(alt, court_ids=courts)
                rows = data.get("content") or []
                receipt = data.get("receipt") or {}
                fee += pacer_api.receipt_fee(receipt)
        print(f"\nPACER Case Locator ({pcl_court}): {len(rows)} match(es), "
              f"fee ${fee:.2f}")
        for row in rows[:10]:
            print(f"  {row.get('courtId')}  {row.get('caseNumberFull')}  "
                  f"{row.get('caseTitle')}  filed {row.get('dateFiled')}"
                  + (f"  closed {row.get('effectiveDateClosed')}"
                     if row.get("effectiveDateClosed") else ""))
        if fee:
            SpendGovernor(paths, config).record(
                "pcl", f"probe {court} {number}", fee,
                pacer_api.receipt_pages(receipt), receipt)
    except pacer_api.PacerError as e:
        print(f"\nPACER Case Locator: FAILED — {e}")


def run_find(config: dict, paths: dict, query: str,
             court: str | None = None) -> list[dict]:
    """
    Find a case by name so you have a docket number to feed --add.

    Searches the RECAP archive, which is free and costs one API call.
    Everything else in PACER Monitor needs a court and a docket number up
    front; this is how you get them when all you have is a caption.
    """
    ensure_dirs(paths)
    client = make_cl_client(config, paths)
    extra: dict = {"order_by": "dateFiled desc"}
    if court:
        extra["court"] = cl.normalize_court(court)
    try:
        rows = client.search_recap(query, extra=extra, result_type="d")
    except cl.RateBudgetExhausted as e:
        print(f"API budget spent: {e}")
        return []
    except cl.CourtListenerError as e:
        print(f"Search failed: {e}")
        return []

    if not rows:
        print(f"Nothing in the RECAP archive matches {query!r}"
              + (f" in {court}" if court else "")
              + ".\nA case nobody has ever purchased won't be there. Try a "
                "PCL sweep (config pacer_sweeps) to search the nationwide "
                "index, which is billable per page.")
        return []

    print(f"{len(rows)} match(es) in the RECAP archive:\n")
    for row in rows:
        court_id = row.get("court_id") or ""
        number = row.get("docketNumber") or row.get("docket_number") or ""
        name = (row.get("caseName") or row.get("case_name") or "")[:70]
        # Search results carry docket_id and docket_absolute_url, not the
        # plain absolute_url a docket detail response has.
        url = row.get("docket_absolute_url") or row.get("absolute_url") or ""
        if url:
            url = f"https://www.courtlistener.com{url}"
        elif row.get("docket_id"):
            url = (f"https://www.courtlistener.com/docket/"
                   f"{row['docket_id']}/")
        print(f"  {court_id:<8} {number:<22} {name}")
        print(f"           filed {row.get('dateFiled') or '?'}"
              f"   judge {row.get('assignedTo') or '?'}"
              + (f"   {url}" if url else ""))
        if court_id and number:
            print(f"           rocky.exe --pacer --add {court_id} {number} "
                  f"--label \"{name}\"")
        print()
    print(f"API budget left: {client.remaining_budget()}")
    return rows


FETCH_BLOCKED_HELP = """
RECAP Fetch purchases are unavailable for this PACER login.

CourtListener logs into PACER on your behalf to buy a docket or PDF, and
Free Law Project states plainly that they do not support MFA-enabled
PACER or CM/ECF accounts. The AO has made MFA mandatory for CM/ECF-level
accounts, so this is a dead end rather than a setting to fix. Rocky's own
PACER integration still works — she sends otpCode, so --sweep and
Case Locator searches are unaffected.

What to do instead, in order of value:

  1. Set up @recap.email. Add your personal @recap.email address as a
     secondary recipient in PACER (Utilities -> Maintain Your E-mail ->
     Add a New E-mail Address; notices yes, all cases yes, verify free
     look no, per-filing HTML), in every jurisdiction where you're
     admitted. CourtListener then ingests every filing you're noticed on,
     plus its free-look PDF, at no PACER cost — and Rocky reads it
     through the API she already uses. This covers every case where the
     firm is counsel of record, which is most of them, and it is FREE.
     It is also immune to this problem, being email-driven rather than
     login-driven.

  2. Ask the PACER Service Center (800-676-6856) whether a search-only
     PACER account — one with no CM/ECF filing privileges — can run
     without MFA. If so, a dedicated account for Rocky would restore
     RECAP Fetch and put her charges on a separate bill. Worth a phone
     call; don't assume the answer.

  3. Buy the occasional docket by hand in PACER and drop the PDF where
     Rocky can see it.

Set pacer_purchase_enabled false to stop Rocky considering purchases at
all. She has recorded this and will not retry until you clear
recap_fetch_blocked from C:\\Rocky\\pacer\\state.json (or pass
--retry-fetch).
""".strip()


def _mark_fetch_blocked(paths: dict, reason: str) -> None:
    state = load_state(paths)
    state["recap_fetch_blocked"] = {"reason": reason[:400],
                                    "since": cl.utc_now_iso()}
    save_state(paths, state)
    append_activity(paths, {"event": "recap_fetch_blocked",
                            "reason": reason[:400]})


def _fetch_blocked(paths: dict) -> dict | None:
    if "--retry-fetch" in sys.argv:
        state = load_state(paths)
        if state.pop("recap_fetch_blocked", None):
            save_state(paths, state)
            log.info("[pacer] cleared the RECAP Fetch block — retrying")
        return None
    return load_state(paths).get("recap_fetch_blocked")


def run_fetch(config: dict, paths: dict, court: str, number: str,
              since: str | None) -> None:
    ensure_dirs(paths)
    blocked = _fetch_blocked(paths)
    if blocked:
        print(f"RECAP Fetch is marked unavailable since "
              f"{blocked.get('since')}.\n")
        print(FETCH_BLOCKED_HELP)
        return
    spend = SpendGovernor(paths, config)
    if not spend.can_spend(PACER_DOCUMENT_FEE_CAP):
        spend.refuse(f"docket {court} {number}", PACER_DOCUMENT_FEE_CAP)
        return
    client = make_cl_client(config, paths)
    try:
        queued = client.fetch_docket(
            config.get("pacer_username"), config.get("pacer_password"),
            court=court, docket_number=number, date_start=since,
            client_code=config.get("pacer_client_code"))
    except cl.RecapFetchLoginError as e:
        _mark_fetch_blocked(paths, str(e))
        print(f"PACER login through CourtListener failed.\n\n"
              f"{FETCH_BLOCKED_HELP}")
        return
    except cl.CourtListenerError as e:
        print(f"RECAP Fetch failed: {e}")
        return
    spend.record("recap-fetch", f"docket report {court} {number}",
                 PACER_DOCUMENT_FEE_CAP)
    result = client.wait_for_fetch(queued.get("id"))
    status = cl.FETCH_STATUS.get(result.get("status"), result.get("status"))
    print(f"RECAP Fetch {queued.get('id')}: {status}")
    if result.get("error_message"):
        print(f"  {result['error_message']}")
    if result.get("status") == 2:
        key = case_key(court, number)
        watched = build_watchlist(paths, config)
        case = watched.get(key) or {"key": key, "court": cl.normalize_court(court),
                                    "docket_number": number, "source": "manual"}
        docket = client.find_docket(court, number)
        if docket:
            case.update(_case_from_docket(case, docket))
            known = load_entry_ids(paths)
            _pull_entries(client, paths, case, known,
                          int(config.get("pacer_backfill_days")
                              or DEFAULT_BACKFILL_DAYS),
                          config.get("pacer_download_documents", True)
                          is not False, dry_run=False)
            case["last_synced"] = cl.utc_now_iso()
            append_jsonl(paths["cases"], _case_record(case))
            rebuild_index(paths)


def run_docs(config: dict, paths: dict, court: str, number: str,
             entry_number: str | None) -> None:
    """Download whatever RECAP already holds for a case. Free."""
    ensure_dirs(paths)
    key = case_key(court, number)
    cases = load_cases(paths)
    case = cases.get(key)
    if not case or not case.get("cl_docket_id"):
        print(f"{key} isn't resolved yet — run --pacer --sync first.")
        return
    client = make_cl_client(config, paths)
    entries = client.docket_entries(case["cl_docket_id"], max_pages=5)
    if entry_number:
        entries = [e for e in entries
                   if str(e.get("entry_number")) == str(entry_number)]
    got = 0
    for entry in entries:
        for doc in entry.get("recap_documents") or []:
            if not doc.get("filepath_local"):
                continue
            record = {"document_number": doc.get("document_number"),
                      "attachment_number": doc.get("attachment_number"),
                      "description": doc.get("description")}
            dest = _document_path(paths, case, entry, record)
            if dest.exists():
                continue
            if client.download_document(doc["filepath_local"], dest):
                got += 1
                print(f"  {dest}")
    print(f"{got} document(s) downloaded to "
          f"{paths['documents']}")


# =============================================================================
# CLI
# =============================================================================

def _argv_value(flag: str) -> str | None:
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def _positional_after(flag: str, count: int) -> list[str]:
    if flag not in sys.argv:
        return []
    i = sys.argv.index(flag)
    out = []
    for arg in sys.argv[i + 1:i + 1 + count]:
        if arg.startswith("--"):
            break
        out.append(arg)
    return out


def run_cli(config: dict, data_dir: Path) -> None:
    """Entry point, called from rocky.py's dispatch."""
    paths = get_paths(config, data_dir)
    dry_run = "--dry-run" in sys.argv
    # Dashboard/Task Scheduler aliases — one flag per scheduled job, same
    # pattern as --litigation-digest.
    argv = set(sys.argv[1:])
    if "--pacer-sweep" in argv:
        sys.argv.append("--sweep")
    if "--pacer-digest" in argv:
        sys.argv.append("--digest")

    if "--status" in sys.argv:
        print_status(config, paths)
        return
    if "--auth-test" in sys.argv:
        run_auth_test(config, paths)
        return
    if "--reindex" in sys.argv:
        ensure_dirs(paths)
        rebuild_index(paths)
        return

    if "--filters" in sys.argv:
        # Ask the API what it supports instead of assuming. An assumed
        # id__in filter on /dockets/ shipped and failed on the first live
        # sync; this is how to check before the next one.
        endpoint = _argv_value("--filters") or "dockets"
        try:
            meta = make_cl_client(config, paths).options(endpoint)
        except cl.CourtListenerError as e:
            print(f"OPTIONS {endpoint} failed: {e}")
            return
        filters = meta.get("filters") or {}
        print(f"/{endpoint}/ — {len(filters)} filter(s)")
        for name in sorted(filters):
            spec = filters[name]
            lookups = spec.get("lookup_types") if isinstance(spec, dict) else None
            print(f"  {name:<38} {lookups if lookups else spec}")
        if meta.get("ordering"):
            print(f"\n  order_by: {meta['ordering']}")
        return

    if "--find" in sys.argv:
        query = _argv_value("--find")
        if not query:
            print("Usage: rocky.py --pacer --find \"case name\" "
                  "[--court vaed]")
            sys.exit(1)
        run_find(config, paths, query, _argv_value("--court"))
        return

    if "--probe" in sys.argv:
        args = _positional_after("--probe", 2)
        if len(args) < 2:
            print("Usage: rocky.py --pacer --probe <court> <docket-number>")
            sys.exit(1)
        run_probe(config, paths, args[0], args[1])
        return

    if "--add" in sys.argv:
        args = _positional_after("--add", 2)
        if len(args) < 2:
            print("Usage: rocky.py --pacer --add <court> <docket-number> "
                  "[--label \"...\"]")
            sys.exit(1)
        ensure_dirs(paths)
        added = append_watchlist_row(paths, args[0], args[1],
                                     label=_argv_value("--label") or "",
                                     client=_argv_value("--client") or "",
                                     rrid=_argv_value("--rrid") or "",
                                     notes="added from the command line")
        print(f"{'Added' if added else 'Already on the watchlist'}: "
              f"{case_key(args[0], args[1])}")
        return

    if "--remove" in sys.argv:
        args = _positional_after("--remove", 2)
        if len(args) < 2:
            print("Usage: rocky.py --pacer --remove <court> <docket-number>")
            sys.exit(1)
        ensure_dirs(paths)
        ok = set_watchlist_watch(paths, args[0], args[1], "N")
        print(f"{'Parked' if ok else 'Not found on the watchlist'}: "
              f"{case_key(args[0], args[1])}")
        return

    if "--fetch" in sys.argv:
        args = _positional_after("--fetch", 2)
        if len(args) < 2:
            print("Usage: rocky.py --pacer --fetch <court> <docket-number> "
                  "[--since YYYY-MM-DD]")
            sys.exit(1)
        run_fetch(config, paths, args[0], args[1], _argv_value("--since"))
        return

    if "--docs" in sys.argv:
        args = _positional_after("--docs", 2)
        if len(args) < 2:
            print("Usage: rocky.py --pacer --docs <court> <docket-number> "
                  "[--entry N]")
            sys.exit(1)
        run_docs(config, paths, args[0], args[1], _argv_value("--entry"))
        return

    if "--sweep" in sys.argv:
        run_sweep(config, paths, dry_run=dry_run,
                  only_name=_argv_value("--name"))
        return

    if "--mail" in sys.argv:
        run_mail(config, paths, dry_run=dry_run)
        return

    if "--digest" in sys.argv:
        hours_raw = _argv_value("--hours")
        run_digest(config, paths, hours=int(hours_raw) if hours_raw else 24,
                   dry_run=dry_run)
        return

    limit_raw = _argv_value("--limit")
    run_sync(config, paths, dry_run=dry_run,
             limit=int(limit_raw) if limit_raw else None,
             only_key=_argv_value("--case"))
