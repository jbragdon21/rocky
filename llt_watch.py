"""
LLT Spreadsheet Watch — hourly change tracking on PENDING LLT MATTERS
=====================================================================

    python rocky.py --llt-watch [--force] [--dry-run] [--sheet NAME]
                                [--local] [--status]   (HOURLY, 8 AM - 7 PM)

Christina's ``PENDING LLT MATTERS.XLSX`` on the MultifamilyHousing Teams
site is the system of record for the landlord-tenant docket, and it is
edited continuously by hand. Nothing recorded when a court date lands, a
matter is deleted, or a new resident is added — the sheet just quietly
differs from what it said an hour ago. This process keeps the history:

  1. Check the file's driveItem metadata (one small Graph GET).
     If its ``cTag`` matches the last snapshot, the file has not changed
     and the run ends without downloading anything.
  2. Otherwise download the workbook, read EVERY column of the active
     sheet, and diff it row-by-row against the last snapshot.
  3. Append each change to the record of account and to a readable
     per-day markdown list on the share.

**Cost.** Deliberately zero Claude tokens — a spreadsheet diff is
arithmetic, not judgment, and the digest phrasing is rendered from
templates below. An hourly run on an untouched sheet is one metadata
request; a run on a touched sheet adds one ~1 MB download. Twelve runs a
day is not a measurable expense against any budget Rocky has.

**Why the snapshot lives on the share, not in C:\\Rocky.** The snapshot
IS the dedup key for the change log. A local snapshot on two machines
means both machines report the same edit, and the record of account
stops being a record. Same reasoning that put PACER's spend ledger on
the share. Writes are atomic (temp + os.replace) and a snapshot that
will not parse is treated as a FIRST RUN — see ``load_snapshot``.

**Where on the share.** Under the Multifamily Tracker and Dashboard
folder (config ``mf_tracker_root``), which is the one roof over this
process, the case brain, the folder-operation logs and the task-list
cache. ``llt_watch_root`` still overrides it outright if one component
ever needs to sit somewhere else.

Consumer: the Multifamily Digest's "LLT Spreadsheet" section
(``multifamily_digest.llt_section``). Nothing else reads the change log,
so its shape is free to grow.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

log = logging.getLogger("rocky.lltwatch")

SNAPSHOT_VERSION = 1

# LLT Watch is source (a) of the Multifamily Tracker, so its data lives
# under that subsystem's root rather than beside it. One folder to
# repoint per machine instead of four.
#
# Resolution order: --llt-watch-root, then config "llt_watch_root", then
# "<mf_tracker_root>\LLT Watch", then the default below. These stay
# EXPLICIT config keys rather than being derived from cases_root — the
# two OneDrive roots on the dev laptop made that kind of derivation a bug
# factory. The default is the DEV laptop path; the Rocky laptop mounts
# the share under the rocky profile and sets mf_tracker_root.
_DEFAULT_TRACKER_ROOT = (
    r"C:\Users\jbragdon\OneDrive - gejlaw.com"
    r"\Program Files\Rocky\Multifamily Tracker and Dashboard"
)
_LLT_WATCH_SUBDIR = "LLT Watch"

# Local hours the watch is allowed to run, inclusive: 8 AM through 7 PM,
# i.e. twelve runs. Enforced in CODE rather than by the schedule because
# schtasks cannot express "hourly, but only during the workday" without a
# /du duration, and a skipped run costs nothing. Override with config
# "llt_watch_hours": [8, 19]; bypass for a one-off with --force.
_DEFAULT_HOURS = (8, 19)

# Columns whose changes are never interesting (none by default — the
# point of the process is to catch changes in columns nobody anticipated,
# including ones added to the sheet after this code was written).
# Override with config "llt_watch_ignore_columns".
_DEFAULT_IGNORE_COLUMNS: tuple[str, ...] = ()

# Identity of a row on a sheet with no ID column. This groups by RESIDENT,
# not by matter: 109 of the 1,060 rows on the 7.25.26 sheet are a second
# matter for a resident who already has one (a rent case and a smoking
# case, say), so a key holds a LIST of rows and ``_pair_rows`` matches
# them up between snapshots.
#
# Resisted the obvious alternative of folding the matter type into the key.
# The type lives in the Status cell, and Status is also where progress is
# recorded ("Rent" -> "Rent sent" -> "Rent sent (re-issue/not properly
# served)"), so a status update would read as a deletion plus a new entry —
# the exact noise this process exists to avoid. pending_llt's
# ``_classify_matter`` is no help either: it reclassifies a matter as
# "court" once a hearing appears, which is a change we very much want to
# report as a change.
_KEY_COLUMNS = ("Property", "Name", "Unit")


# =============================================================================
# Paths
# =============================================================================

def _argv_value(flag: str) -> str | None:
    if flag in sys.argv:
        idx = sys.argv.index(flag)
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    return None


def tracker_root(config: dict) -> Path:
    """The Multifamily Tracker and Dashboard folder on the share — the
    one roof over LLT Watch, the case brain, the folder-operation logs
    and the task-list cache."""
    return Path((_argv_value("--mf-tracker-root")
                 or config.get("mf_tracker_root")
                 or _DEFAULT_TRACKER_ROOT).strip())


def get_paths(config: dict, data_dir: Path) -> dict:
    """Resolve every LLT Watch location. A CLI --llt-watch-root overrides
    config, the same escape hatch the Vault has — the share mounts at a
    different local path on each machine."""
    explicit = (_argv_value("--llt-watch-root")
                or config.get("llt_watch_root") or "").strip()
    root = (Path(explicit) if explicit
            else tracker_root(config) / _LLT_WATCH_SUBDIR)
    return {
        "root": root,
        "snapshot": root / "snapshot.json",
        "changes": root / "llt_changes.jsonl",
        "daily": root / "Daily Changes",
        "local": data_dir / "llt_watch",
    }


# =============================================================================
# Snapshot I/O
# =============================================================================

def load_snapshot(paths: dict) -> dict | None:
    """The last snapshot, or None meaning "treat this as a first run".

    A missing, empty, or UNPARSEABLE snapshot all return None, and the
    caller responds by recording a baseline WITHOUT emitting change
    events. That asymmetry is deliberate: the sheet carries ~1,000 rows,
    so mistaking a corrupt snapshot for "the sheet was empty an hour ago"
    would file a thousand phantom "new entry" lines and bury the real
    changes. Losing one hour of history is the cheaper failure. The file
    is on OneDrive, so a sync conflict is the realistic way this
    happens."""
    p = paths["snapshot"]
    if not p.exists():
        return None
    try:
        snap = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning(f"[llt-watch] Snapshot unreadable ({e}) — re-baselining "
                    f"instead of diffing against nothing. This hour's "
                    f"changes are lost; history resumes next run.")
        return None
    if not isinstance(snap, dict) or not isinstance(snap.get("rows"), dict):
        log.warning("[llt-watch] Snapshot has no rows map — re-baselining.")
        return None
    if snap.get("version") != SNAPSHOT_VERSION:
        log.warning(f"[llt-watch] Snapshot is version "
                    f"{snap.get('version')!r}, this build writes "
                    f"v{SNAPSHOT_VERSION} — re-baselining rather than "
                    f"diffing across shapes.")
        return None
    return snap


def matter_count(rows: dict) -> int:
    """Matters, not residents — a key maps to a list of that resident's
    pending matters."""
    return sum(len(v) for v in rows.values())


def save_snapshot(paths: dict, snap: dict) -> None:
    """Write the snapshot atomically, so a crash or a OneDrive grab
    mid-write can't leave a half-file behind."""
    paths["root"].mkdir(parents=True, exist_ok=True)
    target = paths["snapshot"]
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snap, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, target)


def append_changes(paths: dict, events: list[dict]) -> None:
    """Append to the record of account (JSONL) and the readable per-day
    list. The JSONL is the authority; the markdown is for a human
    browsing the share."""
    if not events:
        return
    paths["root"].mkdir(parents=True, exist_ok=True)
    with open(paths["changes"], "a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    paths["daily"].mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y-%m-%d")
    md_path = paths["daily"] / f"{day}.md"
    new_file = not md_path.exists()
    with open(md_path, "a", encoding="utf-8") as f:
        if new_file:
            f.write(f"# PENDING LLT MATTERS — changes on {day}\n\n"
                    f"Recorded by Rocky's hourly LLT Watch. Times are local.\n")
        stamp = datetime.now().strftime("%I:%M %p").lstrip("0")
        by = events[0].get("edited_by") or "unknown"
        f.write(f"\n## {stamp} — edited by {by}\n\n")
        for e in events:
            f.write(f"- {plain_line(e)}\n")


# =============================================================================
# SharePoint
# =============================================================================

def stat_llt_file(token: str, config: dict) -> dict | None:
    """driveItem metadata for the LLT sheet — cTag, size, and who last
    touched it — WITHOUT downloading the 1 MB workbook. This is the whole
    reason an hourly schedule is affordable.

    Returns {"ctag", "modified", "modified_by", "size", "drive_id",
    "path"} or None if the lookup failed."""
    import requests
    import pending_llt as pl

    hostname = config.get("sharepoint_hostname", "gejlaw.sharepoint.com")
    site_path = config.get("sharepoint_site_path", "/sites/MultifamilyHousing")
    site_id = pl.resolve_sharepoint_site_id(token, hostname, site_path)
    if not site_id:
        return None
    drive_id = pl.resolve_drive_id(token, site_id)
    if not drive_id:
        return None

    file_path = config.get("llt_file_path", pl.LLT_FILE_PATH)
    url = (f"{pl.GRAPH_API_BASE}/drives/{drive_id}/root:/"
           f"{file_path.replace(' ', '%20')}")
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}",
                                      "Accept": "application/json"},
                        timeout=30)
    if resp.status_code != 200:
        log.error(f"[llt-watch] Could not stat {file_path}: "
                  f"HTTP {resp.status_code}: {resp.text[:300]}")
        return None
    item = resp.json()
    who = (((item.get("lastModifiedBy") or {}).get("user") or {})
           .get("displayName"))
    return {
        # cTag changes on CONTENT changes only; eTag also moves when
        # metadata changes, which would make every run look like an edit.
        "ctag": item.get("cTag") or "",
        "modified": item.get("lastModifiedDateTime") or "",
        "modified_by": who or "unknown",
        "size": item.get("size"),
        "drive_id": drive_id,
        "path": file_path,
    }


def download_llt(token: str, config: dict, stat: dict) -> bytes | None:
    """Download just the LLT workbook (the contacts sheet this process
    never needs)."""
    import pending_llt as pl
    return pl.download_sharepoint_file(token, stat["drive_id"], stat["path"])


# =============================================================================
# Reading the sheet — every column, not just the nine the drafter uses
# =============================================================================

def _clean(v) -> str:
    """One cell -> a comparable string. Dates are rendered M/D/YYYY so a
    typed "9.18.26" and a real date cell holding 9/18/2026 don't show up
    as an edit every time Excel reformats the column."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return f"{v.month}/{v.day}/{v.year}"
    s = str(v).replace("\u00a0", " ")
    return re.sub(r"\s+", " ", s).strip()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def snapshot_rows(file_bytes: bytes,
                  sheet: str | None = None) -> tuple[str, list[str], dict]:
    """Read the workbook into (sheet_name, columns, {resident_key: [rows]}).

    Column-agnostic ON PURPOSE. ``pending_llt.parse_llt_spreadsheet``
    projects the sheet down to the nine fields the drafting templates
    need; this reads every column, so a court-date column — or any
    column added to the sheet next month — is tracked without a code
    change.

    Defaults to the workbook's active sheet — the workbook keeps one
    dated sheet per weekly revision and the active one is current.
    """
    import openpyxl
    from openpyxl.utils import get_column_letter

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True,
                                data_only=True)
    ws = wb[sheet] if sheet else wb.active
    sheet_name = ws.title
    rows = list(ws.iter_rows(min_row=1, values_only=True))
    wb.close()
    if not rows:
        return sheet_name, [], {}

    # Header row = the one carrying "Name" and something "Property"
    # (same detection as pending_llt, so the two agree on the layout).
    header_idx = 0
    for i, row in enumerate(rows):
        cells = [_clean(c).lower() for c in row]
        if "name" in cells and any("property" in c for c in cells):
            header_idx = i
            break

    # An unnamed column is named for its spreadsheet letter, so a value
    # appearing in one reads as "column AS added: ..." rather than
    # "col_44 added: ..." — the reader can go look at it.
    raw = [_clean(h) for h in rows[header_idx]]
    columns = [h or f"column {get_column_letter(i + 1)}"
               for i, h in enumerate(raw)]

    # Column A carries the CLIENT ("Bozzuto MD", "Horning", "Towner"),
    # written once above its block of rows, and it is unlabeled. Diffing
    # it as an ordinary column turns a single inserted row into "column A
    # cleared" on one matter and "column A added" on another, so it is
    # dropped in favour of a synthetic "Client" column holding the
    # carried-forward value on EVERY row — which also means a matter
    # moving between clients is reported, and a row insertion is not.
    # If a future sheet actually labels column A, it is treated as an
    # ordinary column and the carry-forward still feeds Property.
    client_carry = bool(raw) and not raw[0]
    if client_carry:
        columns = ["Client"] + columns[1:]

    col_idx = {c.lower().rstrip(): i for i, c in enumerate(columns)}

    def cell(row, key) -> str:
        i = col_idx.get(key.lower().rstrip())
        return _clean(row[i]) if i is not None and i < len(row) else ""

    out: dict[str, list[dict]] = {}
    current_client = ""

    for row in rows[header_idx + 1:]:
        if not row or all(c is None for c in row):
            continue
        # Read the client header BEFORE the name check, so a header
        # sitting on an otherwise nameless row still carries forward.
        if row and row[0] is not None and _clean(row[0]):
            current_client = _clean(row[0])

        name = cell(row, "Name")
        if not name:
            continue

        values = {c: (_clean(row[i]) if i < len(row) else "")
                  for i, c in enumerate(columns)}
        if client_carry:
            values["Client"] = current_client
        prop = (values.get("Property") or values.get("Property ")
                or current_client or "Unknown")
        values["_property"] = prop

        key = "|".join(_norm(values.get(c) or (prop if c == "Property" else ""))
                       for c in _KEY_COLUMNS)
        out.setdefault(key, []).append(values)

    # 66 of the 77 columns on the live sheet are unnamed spacer columns,
    # blank in all 1,060 rows. Carrying them makes the hourly snapshot
    # several times larger for nothing. Named columns are kept even when
    # empty — their appearance and disappearance in the header row IS a
    # layout change worth reporting; an unnamed empty column is padding.
    named = set(_clean(h) for h in raw if _clean(h)) | {"Client"}
    keep = [c for c in columns
            if c in named
            or any(r.get(c) for lst in out.values() for r in lst)]
    if len(keep) != len(columns):
        dropped = len(columns) - len(keep)
        log.info(f"[llt-watch] Ignoring {dropped} unnamed empty column(s) "
                 f"on '{sheet_name}' (spacers)")
        columns = keep
        for lst in out.values():
            for r in lst:
                for c in list(r):
                    if c not in named and c not in keep and not c.startswith("_"):
                        r.pop(c, None)

    # Mark the residents with more than one pending matter, so their
    # change lines can name which matter ("Stratos — Boykins, Gary (1001)"
    # alone is ambiguous when he has both a rent case and a smoking case).
    multi = 0
    for lst in out.values():
        if len(lst) > 1:
            multi += 1
            for r in lst:
                r["_multi"] = True
    if multi:
        log.info(f"[llt-watch] {multi} resident(s) on '{sheet_name}' have "
                 f"more than one pending matter — rows paired by similarity")
    return sheet_name, columns, out


# =============================================================================
# The diff
# =============================================================================

def _label(row: dict) -> str:
    """How a matter is named in the log and the digest: the property, the
    tenant, the unit when there is one, and the matter type when the
    resident has more than one pending matter (which is what makes
    "Stratos — Boykins, Gary (1001)" ambiguous on this sheet)."""
    prop = row.get("_property") or row.get("Property") or "?"
    name = row.get("Name") or "?"
    unit = row.get("Unit") or ""
    label = f"{prop} — {name}" + (f" ({unit})" if unit else "")
    if row.get("_multi") and row.get("Status"):
        label += f" [{row['Status']}]"
    return label


def _row_similarity(a: dict, b: dict, columns: list[str]) -> int:
    """How many tracked cells two rows agree on. Used to decide which of
    a resident's matters an edited row used to be."""
    return sum(1 for c in columns if a.get(c, "") == b.get(c, ""))


def _pair_rows(old_list: list[dict], new_list: list[dict],
               columns: list[str]) -> tuple[list[tuple[dict, dict]],
                                            list[dict], list[dict]]:
    """Match one resident's old rows to their new rows.

    ((old, new) pairs, unmatched old, unmatched new). For the ~90% of
    residents with exactly one matter this is the trivial single pair. For
    a resident with a rent case AND a conduct case, rows are paired by
    how many cells they still agree on — a status update leaves every
    other cell matching its own counterpart, so the pairing holds.

    Worst case (both of a resident's matters edited in the same hour) the
    two can cross, which misattributes an old value in one line. That is
    strictly better than the alternative of reporting both matters as
    deleted and both as new every time a status moves.
    """
    if len(old_list) == 1 and len(new_list) == 1:
        return [(old_list[0], new_list[0])], [], []

    scored = sorted(
        ((_row_similarity(o, n, columns), oi, ni)
         for oi, o in enumerate(old_list)
         for ni, n in enumerate(new_list)),
        key=lambda t: -t[0])
    used_o: set[int] = set()
    used_n: set[int] = set()
    pairs: list[tuple[dict, dict]] = []
    for _score, oi, ni in scored:
        if oi in used_o or ni in used_n:
            continue
        used_o.add(oi)
        used_n.add(ni)
        pairs.append((old_list[oi], new_list[ni]))
    return (pairs,
            [o for i, o in enumerate(old_list) if i not in used_o],
            [n for i, n in enumerate(new_list) if i not in used_n])


def _pair_renames(added: list[dict], removed: list[dict],
                  ) -> list[tuple[dict, dict]]:
    """Match an added row to a removed row that is plainly the same
    matter re-typed.

    Without this, fixing a spelling in a tenant's name reads as a
    deletion plus a new entry — and deletions matter here (a deleted row
    usually means the matter resolved), so they must not be diluted with
    noise. A pair requires the same property AND either the same unit or
    the same name, mutually best-scoring.
    """
    pairs: list[tuple[dict, dict]] = []
    used: set[int] = set()
    for arow in added:
        best, best_score = None, 0
        for i, rrow in enumerate(removed):
            if i in used:
                continue
            if _norm(arow.get("_property")) != _norm(rrow.get("_property")):
                continue
            score = 0
            if arow.get("Unit") and _norm(arow["Unit"]) == _norm(rrow.get("Unit")):
                score += 2
            if _norm(arow.get("Name")) == _norm(rrow.get("Name")):
                score += 2
            if score > best_score:
                best, best_score = i, score
        if best is not None and best_score >= 2:
            pairs.append((removed[best], arow))
            used.add(best)
    return pairs


def diff_snapshots(old: dict, new_sheet: str, new_columns: list[str],
                   new_rows: dict, ignore: tuple[str, ...] = ()) -> list[dict]:
    """Change records, oldest concern first: sheet structure, then new
    entries, removals, renames, and field edits."""
    old_rows: dict = old.get("rows") or {}
    old_columns: list[str] = old.get("columns") or []
    events: list[dict] = []

    if old.get("sheet") and old["sheet"] != new_sheet:
        # Christina revises the workbook weekly by adding a dated sheet.
        # The diff below is still meaningful (matters carry over), but a
        # big change count on a rollover day needs this line to explain
        # it — otherwise a future session reads it as data loss.
        events.append({"change": "sheet_rollover",
                       "old_sheet": old["sheet"], "sheet": new_sheet})

    gone = [c for c in old_columns if c not in new_columns]
    fresh = [c for c in new_columns if c not in old_columns]
    if gone or fresh:
        events.append({"change": "columns_changed", "sheet": new_sheet,
                       "added_columns": fresh, "removed_columns": gone})

    # Only columns present in BOTH snapshots can be diffed; a brand-new
    # column's values are reported by the columns_changed event above,
    # not as a thousand simultaneous field edits.
    shared = [c for c in new_columns
              if c in old_columns and c not in ignore]

    def field_changes(orow: dict, nrow: dict) -> list[dict]:
        out = []
        for c in shared:
            o, n = orow.get(c, ""), nrow.get(c, "")
            if o != n:
                out.append({"column": c, "old": o, "new": n})
        return out

    # Pass 1 — within each resident, pair their matters and diff. Rows
    # left unpaired (the resident went from two matters to one, or one to
    # two) fall through to the add/remove pools below with everything
    # else, so a matter that moved to a different unit can still be
    # recognized as the same matter re-typed.
    modified: list[dict] = []
    add_pool: list[dict] = []
    remove_pool: list[dict] = []

    for key in new_rows:
        if key not in old_rows:
            add_pool.extend(new_rows[key])
            continue
        pairs, unpaired_old, unpaired_new = _pair_rows(
            old_rows[key], new_rows[key], shared)
        for orow, nrow in pairs:
            fields = field_changes(orow, nrow)
            if fields:
                modified.append({"change": "modified", "key": key,
                                 "sheet": new_sheet, "label": _label(nrow),
                                 "fields": fields, "row": nrow})
        add_pool.extend(unpaired_new)
        remove_pool.extend(unpaired_old)

    for key in old_rows:
        if key not in new_rows:
            remove_pool.extend(old_rows[key])

    # Pass 2 — a removal and an addition that are plainly the same matter
    # re-typed (a fixed spelling, a corrected unit) become one "renamed"
    # record instead of a deletion plus a new entry.
    renames = _pair_renames(add_pool, remove_pool)
    renamed_new = {id(n) for _, n in renames}
    renamed_old = {id(o) for o, _ in renames}

    for nrow in add_pool:
        if id(nrow) in renamed_new:
            continue
        events.append({"change": "added", "sheet": new_sheet,
                       "label": _label(nrow), "row": nrow})

    for orow in remove_pool:
        if id(orow) in renamed_old:
            continue
        events.append({"change": "removed", "sheet": new_sheet,
                       "label": _label(orow), "row": orow})

    for orow, nrow in renames:
        events.append({
            "change": "renamed", "sheet": new_sheet,
            "label": _label(nrow), "old_label": _label(orow),
            "fields": field_changes(orow, nrow), "row": nrow,
        })

    events.extend(modified)
    return events


# =============================================================================
# Rendering — plain text for the markdown log, HTML for the digest
# =============================================================================

def _field_phrase(f: dict) -> str:
    """One field edit in English. Phrasing follows James's examples:
    an empty-to-value edit reads as "added", not as a change from
    nothing."""
    col, old, new = f.get("column", "?"), f.get("old", ""), f.get("new", "")
    if not old:
        return f"{col} added: {new}"
    if not new:
        return f"{col} cleared (was {old})"
    return f"{col}: {old} → {new}"


def plain_line(e: dict) -> str:
    kind = e.get("change")
    if kind == "added":
        row = e.get("row") or {}
        detail = row.get("Status") or row.get("Next Steps") or ""
        return (f"New entry for {e.get('label')}"
                + (f" — {detail}" if detail else ""))
    if kind == "removed":
        return f"{e.get('label')} deleted from spreadsheet"
    if kind == "renamed":
        extra = "; ".join(_field_phrase(f) for f in (e.get("fields") or []))
        return (f"{e.get('old_label')} re-entered as {e.get('label')}"
                + (f" — {extra}" if extra else ""))
    if kind == "modified":
        return (f"{e.get('label')} — "
                + "; ".join(_field_phrase(f) for f in (e.get("fields") or [])))
    if kind == "sheet_rollover":
        return (f"New weekly revision: sheet '{e.get('old_sheet')}' → "
                f"'{e.get('sheet')}' (changes below are measured across "
                f"the rollover)")
    if kind == "columns_changed":
        bits = []
        if e.get("added_columns"):
            bits.append("new column(s): " + ", ".join(e["added_columns"]))
        if e.get("removed_columns"):
            bits.append("removed column(s): " + ", ".join(e["removed_columns"]))
        return "Sheet layout changed — " + "; ".join(bits)
    if kind == "touched":
        return "Sheet edited, but no row or column changed"
    return kind or "?"


def _pretty_ts(iso: str | None) -> str:
    """An ISO stamp as a local time-of-day, for "edited at 2:03 PM"."""
    try:
        dt = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return ""
    return dt.astimezone().strftime("%I:%M %p").lstrip("0")


def digest_items(changes: list[dict]) -> list[str]:
    """HTML <li> bodies for the Multifamily Digest's LLT section.

    Ordered so the things that change what the practice does come first:
    new matters, deletions, then everything else. Rendered from templates
    — no Claude call, which is what keeps an hourly watch free.
    """
    order = {"sheet_rollover": 0, "columns_changed": 1, "added": 2,
             "removed": 3, "renamed": 4, "modified": 5}
    items: list[str] = []
    for e in sorted(changes, key=lambda c: (order.get(c.get("change"), 9),
                                            c.get("label") or "")):
        kind = e.get("change")
        if kind == "touched":
            continue
        who = e.get("edited_by") or ""
        when = _pretty_ts(e.get("edited_at"))
        trail = (f" <span style='color:#666;'>({escape(who)}"
                 + (f", {escape(when)}" if when else "") + ")</span>"
                 ) if who else ""

        if kind == "added":
            row = e.get("row") or {}
            detail = row.get("Status") or row.get("Next Steps") or ""
            body = (f"<b>New entry</b> for {escape(str(e.get('label')))}"
                    + (f" — {escape(str(detail))}" if detail else ""))
        elif kind == "removed":
            body = (f"<b>Deleted</b> — {escape(str(e.get('label')))} "
                    f"removed from the spreadsheet")
        elif kind == "renamed":
            extra = "; ".join(escape(_field_phrase(f))
                              for f in (e.get("fields") or []))
            body = (f"<b>Re-entered</b> — {escape(str(e.get('old_label')))} "
                    f"is now {escape(str(e.get('label')))}"
                    + (f"; {extra}" if extra else ""))
        elif kind == "modified":
            body = (f"{escape(str(e.get('label')))} — "
                    + "; ".join(escape(_field_phrase(f))
                                for f in (e.get("fields") or [])))
        else:
            body = f"<i>{escape(plain_line(e))}</i>"
        items.append(body + trail)
    return items


def editor_trailer(changes: list[dict]) -> str:
    """A muted one-liner naming who edited the sheet during the window,
    including edits that produced no row change (a formatting pass). The
    "touched" events exist for exactly this — so a quiet section can
    still distinguish "nobody opened the sheet" from "someone worked in
    it and nothing material moved"."""
    seen: dict[str, str] = {}
    for e in changes:
        who = e.get("edited_by")
        if who:
            seen[who] = _pretty_ts(e.get("edited_at"))
    if not seen:
        return ""
    parts = [f"{w}{f' at {t}' if t else ''}" for w, t in sorted(seen.items())]
    return (f"<p style='margin:4px 0 0 0;font-size:12px;color:#666;'>"
            f"Sheet edited by {escape(', '.join(parts))}.</p>")


# =============================================================================
# Reading the log back (for the digest)
# =============================================================================

def changes_since(paths: dict, since: datetime) -> list[dict]:
    """Change records with ts >= since. Dry-run records are skipped, same
    contract as every other Rocky activity log."""
    out: list[dict] = []
    p = paths["changes"]
    if not p.exists():
        return out
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("dry_run"):
                continue
            try:
                ts = datetime.fromisoformat(
                    (e.get("ts") or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            # run_watch always writes an aware UTC stamp, but a naive one
            # (a hand-edited line, an older record) would otherwise raise
            # comparing against `since` and take the whole Multifamily
            # Digest down with it. Read it as UTC and move on.
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= since:
                out.append(e)
    return out


# =============================================================================
# The run
# =============================================================================

def in_window(config: dict, now: datetime | None = None) -> bool:
    hours = config.get("llt_watch_hours") or list(_DEFAULT_HOURS)
    try:
        start, end = int(hours[0]), int(hours[1])
    except (TypeError, ValueError, IndexError):
        start, end = _DEFAULT_HOURS
    return start <= (now or datetime.now()).hour <= end


def run_watch(token: str | None, config: dict, paths: dict,
              sheet: str | None = None, local: bool = False,
              dry_run: bool = False) -> dict:
    """One watch pass. Returns a summary dict; never raises on a
    SharePoint or parse failure — it reports and leaves the snapshot
    alone, so the next run picks up the same edits rather than losing
    them."""
    ignore = tuple(config.get("llt_watch_ignore_columns")
                   or _DEFAULT_IGNORE_COLUMNS)
    old = load_snapshot(paths)

    # --- get the file (and, when possible, skip getting it) ---
    if local:
        import pending_llt as pl
        lp = pl.newest_local_llt(config.get("local_llt_dir"),
                                 config.get("local_llt_glob"))
        if lp is None:
            return {"error": "no local LLT spreadsheet found"}
        modified = datetime.fromtimestamp(lp.stat().st_mtime, timezone.utc)
        log.info(f"[llt-watch] LLT sheet: {lp.name} "
                 f"(modified {modified.astimezone():%Y-%m-%d %H:%M})")
        stat = {"ctag": f"local:{lp.stat().st_mtime_ns}",
                "modified": modified.isoformat(),
                "modified_by": "local file", "size": lp.stat().st_size,
                "path": str(lp)}
        file_bytes = lp.read_bytes()
    else:
        stat = stat_llt_file(token, config)
        if not stat:
            return {"error": "could not reach the LLT spreadsheet"}
        # Name the file and its last editor on every run. The Pending LLT
        # lesson applies here too: a "done" line proves nothing about
        # WHICH spreadsheet was read, and that is the question any
        # stale-data incident turns on.
        log.info(f"[llt-watch] LLT sheet: {stat['path']} "
                 f"({stat.get('size'):,} bytes, last edited "
                 f"{stat.get('modified')} by {stat.get('modified_by')})")
        if old and old.get("ctag") and old["ctag"] == stat["ctag"]:
            log.info(f"[llt-watch] Unchanged since "
                     f"{(old.get('taken') or '')[:19]} — no download "
                     f"(cTag match)")
            return {"unchanged": True, "changes": 0}
        file_bytes = download_llt(token, config, stat)
        if not file_bytes:
            return {"error": "download failed"}

    # --- read it ---
    try:
        sheet_name, columns, rows = snapshot_rows(file_bytes, sheet)
    except Exception as e:
        log.exception(f"[llt-watch] Could not read the workbook: {e}")
        return {"error": f"unreadable workbook: {e}"}
    if not rows:
        # An empty parse against a populated snapshot would otherwise
        # delete the whole docket in one line. Refuse and keep the
        # snapshot: a sheet with zero matters is a parse failure, not a
        # cleared docket.
        log.error(f"[llt-watch] Read 0 matters from '{sheet_name}' — "
                  f"treating as a parse failure, snapshot untouched.")
        return {"error": "parsed zero rows"}
    matters = matter_count(rows)

    snap = {
        "version": SNAPSHOT_VERSION,
        "taken": datetime.now(timezone.utc).isoformat(),
        "source": stat.get("path"),
        "ctag": stat.get("ctag"),
        "edited_at": stat.get("modified"),
        "edited_by": stat.get("modified_by"),
        "sheet": sheet_name,
        "columns": columns,
        "rows": rows,
    }

    # --- first run: baseline only ---
    if old is None:
        log.info(f"[llt-watch] First run — baselining {matters} matters "
                 f"from sheet '{sheet_name}' ({len(columns)} columns). No "
                 f"changes reported; the next run is the first real diff.")
        if not dry_run:
            save_snapshot(paths, snap)
        return {"baseline": True, "matters": matters, "changes": 0}

    # --- diff ---
    events = diff_snapshots(old, sheet_name, columns, rows, ignore)
    ts = datetime.now(timezone.utc).isoformat()
    for e in events:
        e["ts"] = ts
        e["edited_by"] = stat.get("modified_by")
        e["edited_at"] = stat.get("modified")
        if dry_run:
            e["dry_run"] = True
    if not events:
        # The file changed but no tracked value did: a formatting pass, a
        # comment, a re-save. Recorded so a quiet digest can still say
        # someone worked in the sheet.
        events = [{"change": "touched", "ts": ts, "sheet": sheet_name,
                   "edited_by": stat.get("modified_by"),
                   "edited_at": stat.get("modified"),
                   **({"dry_run": True} if dry_run else {})}]

    real = [e for e in events if e.get("change") != "touched"]
    log.info(f"[llt-watch] {len(real)} change(s) on '{sheet_name}' "
             f"({matters} matters) — last edited by "
             f"{stat.get('modified_by')}")
    for e in real:
        log.info(f"[llt-watch]   {plain_line(e)}")

    if dry_run:
        log.info("[llt-watch] DRY-RUN — snapshot and change log untouched")
        return {"changes": len(real), "matters": matters,
                "events": events, "dry_run": True}

    append_changes(paths, events)
    save_snapshot(paths, snap)
    return {"changes": len(real), "matters": matters, "events": events}


def run_status(config: dict, paths: dict) -> None:
    snap = load_snapshot(paths)
    print("LLT Watch")
    print(f"  Record dir : {paths['root']}")
    hours = config.get("llt_watch_hours") or list(_DEFAULT_HOURS)
    print(f"  Hours      : {hours[0]}:00–{hours[1]}:00 local "
          f"({'in window' if in_window(config) else 'outside window'})")
    if not snap:
        print("  Snapshot   : none — the next run baselines the sheet")
    else:
        rows = snap.get("rows") or {}
        print(f"  Snapshot   : {(snap.get('taken') or '')[:19]}Z — sheet "
              f"'{snap.get('sheet')}', {matter_count(rows)} matters across "
              f"{len(rows)} residents, {len(snap.get('columns') or [])} "
              f"columns")
        print(f"  Last edit  : {snap.get('edited_by')} at "
              f"{snap.get('edited_at')}")
    recent = changes_since(paths, datetime.now(timezone.utc) - timedelta(hours=24))
    real = [e for e in recent if e.get("change") != "touched"]
    print(f"  Last 24h   : {len(real)} change(s)")
    for e in real[-15:]:
        print(f"    - {plain_line(e)}")


def run_cli(config: dict, data_dir: Path) -> None:
    paths = get_paths(config, data_dir)
    if "--status" in sys.argv:
        run_status(config, paths)
        return

    forced = "--force" in sys.argv
    if not forced and not in_window(config):
        hours = config.get("llt_watch_hours") or list(_DEFAULT_HOURS)
        log.info(f"[llt-watch] {datetime.now():%H:%M} is outside the "
                 f"{hours[0]}:00–{hours[1]}:00 watch window — skipping "
                 f"(--force to run anyway)")
        return

    dry_run = "--dry-run" in sys.argv
    local = "--local" in sys.argv
    sheet = _argv_value("--sheet")

    token = None
    if not local:
        from rocky import acquire_token, audit_token_scopes, get_msal_app
        token = acquire_token(get_msal_app(config))
        audit_token_scopes(token)

    result = run_watch(token, config, paths, sheet=sheet, local=local,
                       dry_run=dry_run)
    if result.get("error"):
        log.error(f"[llt-watch] {result['error']}")
        return
    if result.get("unchanged"):
        return
    if result.get("baseline"):
        return
    log.info(f"[llt-watch] Done — {result.get('changes')} change(s) recorded "
             f"to {paths['changes']}")
