"""
Litigation Updater — Bozzuto claims tracking on Smartsheet
==========================================================

Rocky tracks litigation for Bozzuto Management Company and certain
affiliates (BMC, B&A, BHI, BCC, BDC) for audit disclosures on
Smartsheet: MULTIPLE open claims sheets (the BMC master, and the
BCC/BDC master for the other Bozzuto entities — config
litigation_open_sheets routes entities to their home sheet) sharing one
CLOSED claims sheet. Rocky watches the mail,
proposes every change over a Teams chat called "Litigation Updates",
and only writes to the Smartsheet after James says YES.

    python rocky.py --litigation --poll [--dry-run] [--backfill-days N]
        The main cycle (schedule a few times a day, or hourly):
          1. Scan rocky@'s inbox since the cursor for litigation mail:
             - anything from legalnotices@bozzuto.com (or a forward of
               it) — a new legal notice. The attachment goes to Claude
               to identify the document (complaint, demand letter,
               suggestion of bankruptcy, garnishment, ...), and Rocky
               proposes a new claims entry over Teams.
             - forwards saying "add to the claims/litigation smartsheet"
               — same proposal path.
             - forwards saying "move the <X> claim to Closed claims" —
               Rocky correlates the chain with an open-sheet row, drafts
               a closure note (in the closure voice), and proposes the
               move.
             - forwards saying "update the claims smartsheet" — same
               correlation, but the drafted text updates the open row
               in place.
          2. Run the Teams chat cycle (below).

    python rocky.py --litigation --chat [--follow [--minutes N]]
        Teams cycle only: read replies in the "Litigation Updates" chat,
        resolve the pending ask (YES executes exactly the stored effects,
        NO declines), then propose the next queued ask. Free-text also
        works: "do you have the settlement agreement in the Johnson
        case" (Rocky emails the document from the Litigation Update
        Vault), "run a litigation update for BMC" (audit report), and
        anything else is logged as feedback for the weekly learn pass.
        --follow keeps the cycle hot (a live session: every YES gets an
        immediate ack and the next proposal) for up to --minutes N
        (default 30), exiting early once the queue drains and the chat
        goes quiet — built for burning through the cleanup fixes.

    python rocky.py --litigation --digest [--date YYYY-MM-DD] [--dry-run]
        For any day a claim was added, updated, or closed: draft a
        plain-English digest of the activity log into James's Drafts
        (never sent by Rocky). New brain rules learned since the last
        digest are included. Quiet days exit quietly.

    python rocky.py --litigation --report <BMC|B&A|BHI|BCC> [--dry-run]
        On-demand litigation audit report for one entity, rendered
        STRICTLY through the Jinja template at
        _litigation\\report_template.j2 (a placeholder is created on
        first run — replace it with the required format). Saved under
        Reports\\ and emailed from rocky@ to James. Also available by
        chat: "please run a litigation update for BMC".

    python rocky.py --litigation --cleanup [--limit N] [--dry-run]
        Initial learning phase: review every open-sheet entry against
        the sheet's own conventions, queue out-of-compliance fixes as
        one-at-a-time Teams proposals (approve/decline each), and write
        the missing-information list to _litigation\\missing_info.md.

    python rocky.py --litigation --learn [--days N]
        Weekly: send the Teams conversation since the last learn pass to
        Claude and distill durable instructions ("no entry needed for a
        notice of wage garnishment") into the brain file. Additions are
        written in plain English into the next digest.

    python rocky.py --litigation --voice-rebuild
        Rebuild the drafting voices from the sheets themselves: update
        entries teach the update voice, the claim-summary column teaches
        the summary voice, closed-sheet closure notes teach the closure
        voice, and the third-party-disclosure column plus past audit
        reports (litigation_audit_reports_dir) teach the disclosure
        voice. Exemplar documents dropped into
        _litigation\\voices\\seeds\\<updates|summary|closure|disclosure>\\
        are folded into that voice's rebuild as gold-standard references.

    python rocky.py --litigation --strip-formatting [--apply]
        One-time cleanup: remove ALL bolding, cell highlighting, and
        column-default formats from every configured sheet (both masters
        + closed). Dry-run by default — prints what would be cleared;
        --apply executes. Values are kept; formulas are preserved as
        formulas. Rocky's own writes never carry formatting, and
        clearing the column defaults stops Smartsheet from auto-filling
        formatting onto future rows.

    python rocky.py --litigation --status
        Print config readiness, cursors, pending asks, and vault counts.

Safety: the Smartsheet is only written after a YES in the Teams chat
(cleanup fixes, new entries, updates, closures alike). "Move to closed"
logs a full snapshot of the row to the activity log BEFORE the row is
added to the closed sheet and deleted from the open sheet, so every move
is reconstructible. Mail reads use the app token (Application Access
Policy — no new Graph permission); mail out goes through
outbound.send_mail_guarded (internal-only allowlist); the digest is a
DRAFT in James's mailbox, never a send.

Shared data (pin "Always keep on this device"):
    <litigation_root>\\
      _litigation\\            activity.jsonl, communications.jsonl,
                               catalog.jsonl, brain.md, missing_info.md,
                               conventions.md, report_template.j2,
                               voices\\voice_updates.md / voice_summary.md /
                               voice_closure.md / voice_disclosure.md,
                               voices\\seeds\\<voice>\\ exemplar documents
      Litigation Update Vault\\<Claim>\\<Type> - <Claim> - <Date>.<ext>
      Reports\\                generated audit reports
Local state (cursors, chat id, pending asks): C:\\Rocky\\litigation\\state.json
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

log = logging.getLogger("rocky.litigation")

CLAUDE_MODEL = "claude-sonnet-4-5"

SS_BASE = "https://api.smartsheet.com/2.0"

# Attachment types worth processing/filing (same family as the Vault).
LIT_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".xlsm", ".csv",
                  ".txt", ".rtf"}

# Text caps for Claude prompts.
DOC_TEXT_CAP = 6000
BODY_TEXT_CAP = 3000
ROW_LIST_CAP = 400          # max open rows listed in a correlation prompt
VOICE_SAMPLE_CAP = 40       # sample entries per voice
VOICE_SAMPLE_CHARS = 1200
REPORT_FILE_TEXT_CAP = 8000

# The drafting voices. Each may have a seeds\<name>\ folder of exemplar
# documents that survive every --voice-rebuild.
VOICE_NAMES = ("updates", "summary", "closure", "disclosure")

# Below this correlation confidence Rocky asks which claim instead of guessing.
CORRELATE_FLOOR = 0.7

_DEFAULT_CASES_ROOT = r"C:\Users\jbragdon\OneDrive\OneDrive - gejlaw.com\Rocky Cases"

# Document types the notice classifier knows. "other" carries a label.
NOTICE_DOC_TYPES = [
    "complaint", "demand_letter", "suggestion_of_bankruptcy",
    "notice_of_garnishment", "subpoena", "summons", "judgment",
    "settlement_agreement", "dismissal", "discovery_request",
    "notice_of_appeal", "lien", "other",
]

BRAIN_SEED = """# Litigation Updater — Brain

Standing instructions Rocky has learned about how James runs the Bozzuto
claims Smartsheet. Rocky includes this file in every classification and
drafting call, and the weekly --learn pass appends new rules from the
"Litigation Updates" Teams chat. Edit freely — plain English is the
format.

## Rules

- (none learned yet)
"""

# The placeholder report template, written to _litigation\report_template.j2
# on first run. James replaces this with the strict audit-report format;
# the variables below are the contract the renderer provides.
REPORT_TEMPLATE_SEED = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>{{ entity_name }} — Litigation Report</title>
<style>
 body { font-family: Calibri, Arial, sans-serif; margin: 32px; color: #222; }
 h1 { font-size: 20px; } h2 { font-size: 16px; margin-top: 28px; }
 table { border-collapse: collapse; width: 100%; margin-top: 8px; }
 th, td { border: 1px solid #999; padding: 6px 8px; font-size: 12px;
          vertical-align: top; text-align: left; }
 th { background: #eee; }
 .meta { color: #666; font-size: 12px; }
</style></head>
<body>
<h1>Litigation Update — {{ entity_name }} ({{ entity_key }})</h1>
<p class="meta">Generated {{ generated }} · {{ open_rows|length }} open
claim(s), {{ closed_rows|length }} closed claim(s) on the sheet.</p>

{# PLACEHOLDER FORMAT — replace this template with the required audit-report
   format. Available variables:
     entity_key      "BMC" / "B&A" / "BHI" / "BCC"
     entity_name     full entity name from config litigation_entities
     generated       e.g. "August 2, 2026"
     columns         open-sheet column titles, in sheet order
     closed_columns  closed-sheet column titles, in sheet order
     open_rows       list of dicts (column title -> display value), one per
                     open claim for this entity
     closed_rows     same, from the closed sheet
   Values render exactly as they appear on the Smartsheet. #}

<h2>Open claims</h2>
<table>
 <tr>{% for c in columns %}<th>{{ c }}</th>{% endfor %}</tr>
 {% for row in open_rows %}
 <tr>{% for c in columns %}<td>{{ row.get(c, "") }}</td>{% endfor %}</tr>
 {% endfor %}
</table>

<h2>Closed claims</h2>
<table>
 <tr>{% for c in closed_columns %}<th>{{ c }}</th>{% endfor %}</tr>
 {% for row in closed_rows %}
 <tr>{% for c in closed_columns %}<td>{{ row.get(c, "") }}</td>{% endfor %}</tr>
 {% endfor %}
</table>
</body>
</html>
"""


# =============================================================================
# Paths, config, state, logging
# =============================================================================

def get_paths(config: dict, data_dir: Path) -> dict:
    explicit = (config.get("litigation_root") or "").strip()
    if explicit:
        root = Path(explicit)
    else:
        cases = (config.get("cases_root") or "").strip() or _DEFAULT_CASES_ROOT
        root = Path(cases).parent / "Litigation Updates"

    meta = root / "_litigation"
    local = data_dir / "litigation"
    return {
        "root": root,
        "meta": meta,
        "vault": root / "Litigation Update Vault",
        "reports": root / "Reports",
        "activity": meta / "activity.jsonl",
        "comms": meta / "communications.jsonl",
        "catalog": meta / "catalog.jsonl",
        "brain": meta / "brain.md",
        "missing": meta / "missing_info.md",
        "conventions": meta / "conventions.md",
        "template": meta / "report_template.j2",
        "voices": meta / "voices",
        "local": local,
        "state": local / "state.json",
        "tag": "[litigation]",
    }


def ensure_dirs(paths: dict) -> None:
    paths["meta"].mkdir(parents=True, exist_ok=True)
    paths["vault"].mkdir(parents=True, exist_ok=True)
    paths["reports"].mkdir(parents=True, exist_ok=True)
    paths["voices"].mkdir(parents=True, exist_ok=True)
    for _v in VOICE_NAMES:
        (paths["voices"] / "seeds" / _v).mkdir(parents=True, exist_ok=True)
    paths["local"].mkdir(parents=True, exist_ok=True)
    if not paths["brain"].exists():
        paths["brain"].write_text(BRAIN_SEED, encoding="utf-8")
    if not paths["template"].exists():
        paths["template"].write_text(REPORT_TEMPLATE_SEED, encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state(paths: dict) -> dict:
    try:
        return json.loads(paths["state"].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(paths: dict, state: dict) -> None:
    paths["state"].parent.mkdir(parents=True, exist_ok=True)
    paths["state"].write_text(json.dumps(state, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, record: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning(f"[litigation] could not write {path.name}: {e}")


def log_event(paths: dict, kind: str, **fields) -> None:
    _append_jsonl(paths["activity"], {"ts": _now_iso(), "event": kind, **fields})


def log_comm(paths: dict, direction: str, text: str, **fields) -> None:
    _append_jsonl(paths["comms"], {"ts": _now_iso(), "direction": direction,
                                   "text": text[:4000], **fields})


def _read_jsonl(path: Path) -> list[dict]:
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


def load_brain(paths: dict) -> str:
    try:
        return paths["brain"].read_text(encoding="utf-8")
    except OSError:
        return BRAIN_SEED


def load_voice(paths: dict, name: str) -> str:
    try:
        return (paths["voices"] / f"voice_{name}.md").read_text(encoding="utf-8")
    except OSError:
        return ""


# =============================================================================
# Smartsheet client (raw REST)
# =============================================================================

class SmartsheetError(RuntimeError):
    pass


def _ss_call(method: str, path: str, token: str, *, payload=None,
             params: dict | None = None, max_retries: int = 5) -> dict:
    """Smartsheet REST call with 429/5xx backoff. Raises SmartsheetError."""
    url = f"{SS_BASE}{path}"
    headers = {"Authorization": f"Bearer {token}",
               "Content-Type": "application/json"}
    delay = 2.0
    last = "no response"
    for _ in range(max_retries):
        try:
            resp = requests.request(method, url, headers=headers, json=payload,
                                    params=params, timeout=60)
        except requests.RequestException as e:
            last = f"network error: {e}"
            time.sleep(delay)
            delay = min(delay * 2, 60)
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after else delay
            log.info(f"[litigation] Smartsheet throttled ({resp.status_code}); "
                     f"sleeping {wait:.0f}s")
            time.sleep(wait)
            delay = min(delay * 2, 60)
            last = f"{resp.status_code} {resp.text[:200]}"
            continue
        if resp.status_code not in (200, 201):
            raise SmartsheetError(
                f"Smartsheet {method} {path}: {resp.status_code} "
                f"{resp.text[:300]}")
        return resp.json()
    raise SmartsheetError(f"Smartsheet {method} {path}: retries exhausted ({last})")


def get_sheet(token: str, sheet_id) -> dict:
    return _ss_call("GET", f"/sheets/{sheet_id}", token,
                    params={"pageSize": "10000"})


def sheet_column_titles(sheet: dict) -> list[str]:
    return [c.get("title") or "" for c in sheet.get("columns", [])]


def _col_map(sheet: dict) -> dict:
    """lowercased column title -> column dict."""
    return {(c.get("title") or "").strip().lower(): c
            for c in sheet.get("columns", [])}


def primary_column_title(sheet: dict) -> str:
    for c in sheet.get("columns", []):
        if c.get("primary"):
            return c.get("title") or ""
    cols = sheet.get("columns", [])
    return (cols[0].get("title") or "") if cols else ""


def claim_column_title(spec_or_config: dict, sheet: dict) -> str:
    """The column that NAMES a claim — used for correlation listings, vault
    folders, and chat proposals ("Combined Claimant (Project)" on the BMC
    master, "Combined Claimant/Project" on the BCC/BDC master). Accepts an
    open-sheet spec ("claim_column") or the raw config
    ("litigation_claim_column"); falls back to the sheet's primary column
    when unset or absent."""
    want = (spec_or_config.get("claim_column")
            or spec_or_config.get("litigation_claim_column") or "").strip()
    if want:
        col = _col_map(sheet).get(want.lower())
        if col is not None:
            return col.get("title") or want
    return primary_column_title(sheet)


def row_to_dict(sheet: dict, row: dict) -> dict:
    """{column title: display value} for one row."""
    by_id = {c["id"]: (c.get("title") or "") for c in sheet.get("columns", [])}
    out: dict = {}
    for cell in row.get("cells", []):
        title = by_id.get(cell.get("columnId"))
        if not title:
            continue
        value = cell.get("displayValue")
        if value is None:
            value = cell.get("value")
        out[title] = "" if value is None else str(value)
    return out


def build_cells(sheet: dict, cells_by_title: dict) -> list[dict]:
    """Map {column title: value} onto the sheet's column ids. Unknown
    titles are skipped with a warning (Claude sometimes invents one)."""
    cmap = _col_map(sheet)
    cells: list[dict] = []
    for title, value in (cells_by_title or {}).items():
        col = cmap.get((title or "").strip().lower())
        if col is None:
            log.warning(f"[litigation] no column {title!r} on sheet "
                        f"{sheet.get('name')!r} — value skipped")
            continue
        if value is None or str(value).strip() == "":
            continue
        cells.append({"columnId": col["id"], "value": str(value),
                      "strict": False})
    return cells


def add_row(token: str, sheet: dict, cells_by_title: dict) -> int | None:
    cells = build_cells(sheet, cells_by_title)
    if not cells:
        raise SmartsheetError("add_row: no populatable cells")
    data = _ss_call("POST", f"/sheets/{sheet['id']}/rows", token,
                    payload=[{"toBottom": True, "cells": cells}])
    rows = data.get("result") or []
    if isinstance(rows, dict):
        rows = [rows]
    return rows[0].get("id") if rows else None


def update_row(token: str, sheet: dict, row_id: int,
               cells_by_title: dict) -> None:
    cells = build_cells(sheet, cells_by_title)
    if not cells:
        raise SmartsheetError("update_row: no populatable cells")
    _ss_call("PUT", f"/sheets/{sheet['id']}/rows", token,
             payload=[{"id": row_id, "cells": cells}])


def delete_row(token: str, sheet_id, row_id: int) -> None:
    _ss_call("DELETE", f"/sheets/{sheet_id}/rows", token,
             params={"ids": str(row_id), "ignoreRowsNotFound": "true"})


def find_row(sheet: dict, row_id: int) -> dict | None:
    return next((r for r in sheet.get("rows", []) if r.get("id") == row_id),
                None)


# =============================================================================
# Open-sheet specs — claims live on MULTIPLE open sheets (the BMC master
# and the BCC/BDC master for the other Bozzuto entities), sharing one
# closed sheet. Each spec routes entities and carries per-sheet column
# quirks; global litigation_* keys are the fallbacks.
# =============================================================================

def open_sheet_specs(config: dict) -> list[dict]:
    """Normalize config litigation_open_sheets (falling back to the legacy
    single litigation_open_sheet_id) into
    [{key, sheet_id, entities, claim_column, column_map, update_columns}]."""
    raw = config.get("litigation_open_sheets")
    if not raw:
        sid = config.get("litigation_open_sheet_id")
        raw = [{"key": "OPEN", "sheet_id": sid}] if sid else []
    specs = []
    for s in raw:
        if not s.get("sheet_id"):
            continue
        specs.append({
            "key": s.get("key") or str(s["sheet_id"]),
            "sheet_id": s["sheet_id"],
            "entities": [str(e).upper() for e in (s.get("entities") or [])],
            "claim_column": (s.get("claim_column")
                             or config.get("litigation_claim_column") or ""),
            "column_map": {**(config.get("litigation_column_map") or {}),
                           **(s.get("column_map") or {})},
            "update_columns": (s.get("update_columns")
                               or config.get("litigation_update_columns")
                               or []),
        })
    return specs


def fetch_open_sheets(ss_token: str, config: dict) -> list[dict]:
    """[{**spec, "sheet": <sheet>}] for every loadable open sheet. A sheet
    that fails to load is skipped (logged) so one bad id doesn't take the
    whole cycle down — callers treat an empty list as 'not reachable'."""
    out = []
    for spec in open_sheet_specs(config):
        try:
            out.append({**spec, "sheet": get_sheet(ss_token, spec["sheet_id"])})
        except SmartsheetError as e:
            log.error(f"[litigation] open sheet {spec['key']!r} "
                      f"({spec['sheet_id']}) failed to load: {e}")
    return out


def spec_for_entity(config: dict, entity_key: str | None) -> dict | None:
    """The open sheet a NEW claim for this entity belongs on (first spec
    listing the entity; first spec overall as the fallback)."""
    specs = open_sheet_specs(config)
    if entity_key:
        for s in specs:
            if str(entity_key).upper() in s["entities"]:
                return s
    return specs[0] if specs else None


def spec_by_key(config: dict, key: str | None) -> dict | None:
    return next((s for s in open_sheet_specs(config) if s["key"] == key), None)


def _locate_row(sheets: list[dict], row_id) -> tuple[dict | None, dict | None]:
    """Find a row id across the loaded open sheets -> (spec_entry, row)."""
    if row_id:
        for entry in sheets:
            row = find_row(entry["sheet"], row_id)
            if row is not None:
                return entry, row
    return None, None


def move_row_to_closed(token: str, open_sheet: dict, closed_sheet: dict,
                       row: dict, closure_note: str, closure_col: str,
                       paths: dict, dry_run: bool,
                       config: dict | None = None,
                       column_map: dict | None = None) -> dict:
    """Snapshot -> add to closed sheet (+ closure note, + Date Closed) ->
    delete from open. The snapshot event makes every move reconstructible
    by replay. litigation_column_map handles the open->closed title drift
    (e.g. "Property Name (State)" -> "Property" on the real sheets)."""
    config = config or {}
    snapshot = row_to_dict(open_sheet, row)
    log_event(paths, "claim_closed_snapshot", row_id=row.get("id"),
              open_sheet=open_sheet.get("name"), snapshot=snapshot,
              closure_note=closure_note, dry_run=dry_run)

    if column_map is None:
        column_map = config.get("litigation_column_map") or {}
    column_map = {(k or "").strip().lower(): v for k, v in column_map.items()}
    # lowercased title -> canonical closed-sheet title
    closed_titles = {t.strip().lower(): t
                     for t in sheet_column_titles(closed_sheet) if t}
    cells: dict = {}
    for t, v in snapshot.items():
        target = column_map.get(t.strip().lower(), t)
        canon = closed_titles.get((target or "").strip().lower())
        if canon:
            cells[canon] = v

    note_placed = False
    canon_note = closed_titles.get((closure_col or "").strip().lower())
    if canon_note:
        existing = cells.get(canon_note, "")
        cells[canon_note] = (f"{existing}\n\n{closure_note}".strip()
                             if existing else closure_note)
        note_placed = True
    else:
        log.warning(f"[litigation] closed sheet has no {closure_col!r} column "
                    f"— closure note kept in the activity log only")

    date_col = config.get("litigation_date_closed_column") or "Date Closed"
    canon_date = closed_titles.get(date_col.strip().lower())
    if canon_date and not (cells.get(canon_date) or "").strip():
        cells[canon_date] = datetime.now().strftime("%Y-%m-%d")

    if dry_run:
        return {"moved": False, "reason": "dry_run", "cells": cells,
                "note_placed": note_placed}

    new_id = add_row(token, closed_sheet, cells)
    delete_row(token, open_sheet["id"], row["id"])
    return {"moved": True, "closed_row_id": new_id, "note_placed": note_placed}


# Smartsheet format descriptor with every position empty = platform
# defaults (no bold, no fill, default font). Assigning it to a row/cell/
# column CLEARS its formatting.
_DEFAULT_FORMAT = ",,,,,,,,,,,,,,,,"


def strip_formatting(config: dict, paths: dict, apply: bool) -> dict:
    """Remove all row/cell formatting (bold, highlights, fonts) and
    column-default formats from every configured sheet. Dry-run unless
    `apply` — this touches every formatted row. Cells keep their values;
    formula cells keep their formulas (never converted to static values);
    cells carrying inbound cell-links are skipped (a value write would
    sever the link). Clearing column defaults is what stops FUTURE rows
    from inheriting formatting."""
    ss_token = (config.get("smartsheet_token") or "").strip()
    if not ss_token:
        log.error("[litigation] smartsheet_token missing")
        return {"error": "no_smartsheet_token"}

    targets = [(s["key"], s["sheet_id"]) for s in open_sheet_specs(config)]
    if config.get("litigation_closed_sheet_id"):
        targets.append(("CLOSED", config["litigation_closed_sheet_id"]))

    totals: dict = {}
    for key, sid in targets:
        sheet = _ss_call("GET", f"/sheets/{sid}", ss_token,
                         params={"pageSize": "10000", "include": "format"})
        updates: list[dict] = []
        cells_touched = 0
        for row in sheet.get("rows", []):
            row_fmt = bool(row.get("format"))
            cells: list[dict] = []
            for c in row.get("cells", []):
                if not c.get("format"):
                    continue
                if c.get("linkInFromCell"):
                    continue  # value write would sever the cell link
                cell: dict = {"columnId": c["columnId"],
                              "format": _DEFAULT_FORMAT}
                if c.get("formula"):
                    cell["formula"] = c["formula"]
                elif c.get("value") is not None:
                    cell["value"] = c["value"]
                    cell["strict"] = False
                else:
                    cell["value"] = ""
                cells.append(cell)
            if row_fmt or cells:
                upd: dict = {"id": row["id"]}
                if row_fmt:
                    upd["format"] = _DEFAULT_FORMAT
                if cells:
                    upd["cells"] = cells
                    cells_touched += len(cells)
                updates.append(upd)

        fmt_columns = [c for c in sheet.get("columns", []) if c.get("format")]
        totals[key] = {"rows": len(updates), "cells": cells_touched,
                       "columns": len(fmt_columns)}

        if apply:
            for i in range(0, len(updates), 100):
                _ss_call("PUT", f"/sheets/{sid}/rows", ss_token,
                         payload=updates[i:i + 100])
            for col in fmt_columns:
                _ss_call("PUT", f"/sheets/{sid}/columns/{col['id']}",
                         ss_token, payload={"format": _DEFAULT_FORMAT})
        log.info(f"[litigation] strip-formatting {key}: "
                 f"{'cleared' if apply else 'DRY RUN — would clear'} "
                 f"{len(updates)} row(s), {cells_touched} cell(s), "
                 f"{len(fmt_columns)} column default(s)")

    log_event(paths, "formatting_stripped", targets=totals, applied=apply)
    if not apply:
        log.info("[litigation] dry run — add --apply to actually clear")
    return totals


def _entity_spec(config: dict) -> dict:
    """{KEY: {"key", "name", "matches": [...]}} from config
    litigation_entities. Entries may be plain strings (used as both name
    and match text) or dicts with "name" and "match" (string or list —
    the master sheet's Entity cells carry variants like
    "B&A d/b/a The Bozzuto Group" and "Bozzuto Group")."""
    raw = config.get("litigation_entities") or {
        "BMC": "Bozzuto Management Company",
        "B&A": {"name": "Bozzuto & Associates",
                "match": ["B&A", "Bozzuto & Associates", "Bozzuto Group"]},
        "BHI": "Bozzuto Homes",
        "BCC": "Bozzuto Construction Company",
        "BDC": "Bozzuto Development Company",
    }
    out = {}
    for key, val in raw.items():
        if isinstance(val, dict):
            match = val.get("match") or val.get("name") or key
        else:
            match = str(val)
        matches = [m for m in (match if isinstance(match, list) else [match])
                   if str(m).strip()]
        name = (val.get("name") if isinstance(val, dict) else str(val)) or key
        out[key.upper()] = {"key": key.upper(), "name": name,
                            "matches": matches}
    return out


def rows_for_entity(config: dict, sheet: dict, entity: dict) -> list[dict]:
    """Rows (as title->value dicts) whose entity column matches. Falls back
    to all rows when the entity column doesn't exist (warned)."""
    col = (config.get("litigation_entity_column") or "Entity").strip()
    titles = {t.strip().lower() for t in sheet_column_titles(sheet)}
    dicts = [row_to_dict(sheet, r) for r in sheet.get("rows", [])]
    if col.lower() not in titles:
        log.warning(f"[litigation] sheet {sheet.get('name')!r} has no "
                    f"{col!r} column — report includes ALL rows")
        return dicts
    # The cell may hold the abbreviation ("BMC"), the full entity name, or
    # a variant ("B&A d/b/a The Bozzuto Group") — match in both directions.
    needles = {m.lower() for m in entity.get("matches", [])}
    needles.update({entity["name"].lower(), entity.get("key", "").lower()})
    needles.discard("")
    out = []
    for d in dicts:
        val = (d.get(col) or "").lower()
        # Match either direction: the cell may hold the abbreviation or the
        # full entity name.
        if val and any(n in val or val in n for n in needles):
            out.append(d)
    return out


# =============================================================================
# Claude helpers
# =============================================================================

def _claude_text(client, prompt: str, max_tokens: int = 3000) -> str | None:
    for attempt in (1, 2):
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL, max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            if getattr(response, "stop_reason", None) == "max_tokens":
                log.warning(f"[litigation] Claude response hit the "
                            f"{max_tokens}-token cap — output truncated")
            return response.content[0].text
        except Exception as e:
            log.warning(f"[litigation] Claude call attempt {attempt} failed: {e}")
    return None


def _extract_json(text: str):
    """First JSON object or array in a Claude response, tolerantly."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                continue
    raise ValueError("no JSON in response")


def _claude_json(client, prompt: str, max_tokens: int = 3000):
    text = _claude_text(client, prompt, max_tokens)
    if text is None:
        return None
    try:
        return _extract_json(text)
    except ValueError as e:
        log.warning(f"[litigation] Claude returned non-JSON: {e}")
        return None


def _preamble(config: dict, paths: dict) -> str:
    # Today's date is load-bearing: without it the model assumes current-
    # year dates on the sheet are "in the future" and proposes bogus
    # typo fixes (caught live 2026-08-02 on a 2026-04-16 Date of Loss).
    return (
        "You are Rocky, a virtual paralegal at Gallagher LLP working for "
        "James Bragdon. Today's date is "
        + datetime.now().strftime("%B %d, %Y")
        + ". You maintain the Bozzuto litigation/claims "
        "Smartsheet (entities: "
        + ", ".join(f"{k} = {v['name']}" for k, v in _entity_spec(config).items())
        + ") used for audit disclosures.\n\n"
        "Standing instructions James has taught you (FOLLOW THESE):\n"
        + load_brain(paths) + "\n"
    )


def _rows_compact(sheet: dict, name_col: str, extra_cols: list[str],
                  cap: int = ROW_LIST_CAP) -> str:
    """One line per row: row_id | claim name | selected columns."""
    lines = []
    for r in sheet.get("rows", [])[:cap]:
        d = row_to_dict(sheet, r)
        bits = [str(r.get("id")), d.get(name_col, "")]
        for c in extra_cols:
            v = d.get(c, "")
            if v:
                bits.append(f"{c}: {v[:80]}")
        lines.append(" | ".join(bits))
    return "\n".join(lines)


def classify_notice(client, config: dict, paths: dict, docs: list[dict],
                    email_ctx: str) -> dict | None:
    """Identify the incoming legal document(s) and recommend whether a new
    claims entry is warranted. Returns None on Claude failure (callers hold
    the mail cursor)."""
    blocks = []
    for d in docs:
        text = (d.get("text") or "").strip()
        blocks.append(f"--- Document: {d['filename']} ---\n"
                      f"{text[:DOC_TEXT_CAP] or '(no text could be extracted)'}")
    prompt = (
        _preamble(config, paths)
        + f"A new legal notice arrived:\n{email_ctx}\n\n"
        + ("\n\n".join(blocks) if blocks else "(no attachments — the notice "
                                              "is in the email body itself)")
        + "\n\nReturn ONLY a JSON object:\n"
          '  "doc_type": one of ' + json.dumps(NOTICE_DOC_TYPES) + "\n"
          '  "doc_label": short human label, e.g. "Complaint (unlawful '
          'detainer counterclaim)"\n'
          '  "claim_name": the claim identifier in the sheet\'s '
          '"Claimant (Property)" convention, e.g. '
          '"Dany Aboulkhier (Meriel Marina Bay)" — claimant name, then the '
          'property/community in parentheses (omit the parenthetical if no '
          'property is involved, e.g. employment claims by office staff)\n'
          '  "entity": which Bozzuto entity is implicated ('
        + ", ".join(_entity_spec(config)) + ', or null if unclear)\n'
          '  "parties": short parties description\n'
          '  "court_or_forum": court/agency/none\n'
          '  "case_number": docket/case number or null\n'
          '  "summary": 2-3 sentences on what this is and what it alleges\n'
          '  "recommend_entry": true/false — should this become a new row '
          "on the claims sheet? Apply the standing instructions above "
          "(some notice types never get entries).\n"
          '  "recommend_reason": one sentence\n'
          '  "confidence": 0.0-1.0'
    )
    result = _claude_json(client, prompt)
    return result if isinstance(result, dict) else None


def draft_entry(client, config: dict, paths: dict, open_sheet: dict,
                docs: list[dict], email_ctx: str, classification: dict,
                instructions: list[str] | None = None) -> dict | None:
    """Draft a full new row ({column title: value}) in the sheet's voice."""
    columns = sheet_column_titles(open_sheet)
    # A few recent rows teach the house style for each column.
    samples = [row_to_dict(open_sheet, r) for r in open_sheet.get("rows", [])[-5:]]
    blocks = [f"--- Document: {d['filename']} ---\n"
              f"{(d.get('text') or '')[:DOC_TEXT_CAP]}" for d in docs]
    voice = load_voice(paths, "updates")
    svoice = load_voice(paths, "summary")
    dvoice = load_voice(paths, "disclosure")
    summary_col = (config.get("litigation_summary_column")
                   or "Summary of Claim")
    disclosure_col = (config.get("litigation_disclosure_column")
                      or "Third Party Disclosure Summary")
    prompt = (
        _preamble(config, paths)
        + (f"Voice guide for sheet entries:\n{voice}\n\n" if voice else "")
        + (f"Voice guide for the {summary_col!r} column (the internal "
           f"claim-summary narrative; draft it in this voice):\n{svoice}\n\n"
           if svoice else "")
        + (f"Voice guide for the {disclosure_col!r} column (the "
           f"audit-disclosure narrative — nearly every entry carries one; "
           f"draft it in this voice):\n{dvoice}\n\n" if dvoice else "")
        + f"The claims sheet columns are: {json.dumps(columns)}\n\n"
          f"Recent entries (style examples):\n{json.dumps(samples, indent=1)[:4000]}\n\n"
          f"James approved creating a new claims entry for this notice "
          f"(identified as: {classification.get('doc_label')}; "
          f"{classification.get('summary')}).\n\n"
          f"Source email:\n{email_ctx}\n\n" + "\n\n".join(blocks)
        + (("\n\nJames gave these instructions for this entry — follow "
            "them:\n- " + "\n- ".join(instructions)) if instructions else "")
        + "\n\nDraft the new row. Return ONLY a JSON object mapping column "
          "titles (exactly as given) to values. Match the existing entries' "
          "conventions for dates, abbreviations, and phrasing. Leave out "
          "any column you have no information for — do NOT guess facts."
    )
    result = _claude_json(client, prompt, max_tokens=2000)
    return result if isinstance(result, dict) else None


def correlate_claim(client, config: dict, paths: dict, sheets: list[dict],
                    email_ctx: str, hint: str | None) -> dict | None:
    """Match an email chain to ONE row across all the open claims sheets.
    `sheets` is fetch_open_sheets() output. Returns {"row_id": int|None,
    "confidence": float, "reason": str} — locate the row (and its sheet)
    with _locate_row."""
    blocks = []
    for entry in sheets:
        sheet = entry["sheet"]
        titles = set(sheet_column_titles(sheet))
        extra_cfg = config.get("litigation_correlate_columns")
        if extra_cfg:
            extra = [c for c in extra_cfg if c in titles]
        else:
            extra = [c for c in ((config.get("litigation_entity_column")
                                  or "Entity"), "Claimant",
                                 "Property Name (State)", "Project",
                                 "Type of Case", "Status", "Property")
                     if c in titles]
        listing = _rows_compact(sheet, claim_column_title(entry, sheet), extra)
        blocks.append(f"-- Sheet: {entry['key']} --\n{listing}")
    prompt = (
        _preamble(config, paths)
        + "Match this email chain to ONE row on the open claims sheets.\n\n"
        + (f"James referred to it as the {hint!r} claim.\n\n" if hint else "")
        + f"Email:\n{email_ctx}\n\n"
          f"Open claims (row_id | claim | ...):\n" + "\n\n".join(blocks)
        + "\n\nReturn ONLY a JSON object: {\"row_id\": <int or null>, "
          "\"confidence\": 0.0-1.0, \"reason\": \"...\"} — null row_id if "
          "no row plausibly matches."
    )
    result = _claude_json(client, prompt, max_tokens=500)
    return result if isinstance(result, dict) else None


def draft_closure_note(client, config: dict, paths: dict, row_dict: dict,
                       email_ctx: str, docs: list[dict]) -> str | None:
    voice = load_voice(paths, "closure")
    blocks = [f"--- Document: {d['filename']} ---\n"
              f"{(d.get('text') or '')[:DOC_TEXT_CAP]}" for d in docs]
    prompt = (
        _preamble(config, paths)
        + (f"Closure-note voice guide:\n{voice}\n\n" if voice else "")
        + f"This claim is being moved to the Closed claims sheet.\n\n"
          f"Current sheet entry:\n{json.dumps(row_dict, indent=1)[:3000]}\n\n"
          f"The email chain James forwarded with the closure instruction:\n"
          f"{email_ctx}\n\n" + "\n\n".join(blocks)
        + "\n\nDraft the closure note for the closed-claims sheet: how the "
          "matter resolved (settled/dismissed/judgment/withdrawn), material "
          "terms if any, and the effective date. Match the voice guide. "
          "Return ONLY the note text — no preamble, no JSON."
    )
    text = _claude_text(client, prompt, max_tokens=800)
    return text.strip() if text else None


def draft_update(client, config: dict, paths: dict, open_sheet: dict,
                 row_dict: dict, email_ctx: str, docs: list[dict],
                 update_cols: list[str] | None = None) -> dict | None:
    """Draft updated cell values for an existing row.
    Returns {column title: new value} (typically the update columns)."""
    voice = load_voice(paths, "updates")
    if update_cols is None:
        update_cols = config.get("litigation_update_columns") or []
    columns = sheet_column_titles(open_sheet)
    blocks = [f"--- Document: {d['filename']} ---\n"
              f"{(d.get('text') or '')[:DOC_TEXT_CAP]}" for d in docs]
    prompt = (
        _preamble(config, paths)
        + (f"Voice guide for sheet updates:\n{voice}\n\n" if voice else "")
        + f"The claims sheet columns are: {json.dumps(columns)}\n"
        + (f"Updates normally go in these columns: {json.dumps(update_cols)}\n"
           if update_cols else "")
        + f"\nCurrent entry:\n{json.dumps(row_dict, indent=1)[:3000]}\n\n"
          f"James forwarded this chain with an instruction to update the "
          f"entry:\n{email_ctx}\n\n" + "\n\n".join(blocks)
        + "\n\nReturn ONLY a JSON object mapping column titles to their NEW "
          "full values (rewrite the whole cell — Smartsheet cells are "
          "replaced, not appended). When adding to a running-update column, "
          "keep the prior text and add the new development in the same "
          "style, most recent first if that is the sheet's convention. "
          "Only include columns that should change."
    )
    result = _claude_json(client, prompt, max_tokens=2000)
    return result if isinstance(result, dict) else None


# =============================================================================
# Litigation Update Vault (key-document store + catalog)
# =============================================================================

def _load_catalog(paths: dict) -> list[dict]:
    return _read_jsonl(paths["catalog"])


def file_documents(paths: dict, claim: str, doc_label: str,
                   attachments: list[dict], source: dict,
                   dry_run: bool) -> list[dict]:
    """File attachment bytes under Litigation Update Vault\\<Claim>\\ and
    catalog them. SHA-256 dedup against the catalog."""
    from rocky import _sanitize_filename, _dedup_path  # lazy

    seen = {e.get("sha256") for e in _load_catalog(paths)}
    claim_dir = _sanitize_filename(re.sub(r"\s+", " ", (claim or "").strip()))[:80] \
        or "_Unmatched"
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filed: list[dict] = []
    for att in attachments:
        content = att.get("contentBytes")
        name = att.get("name") or "document"
        if not content:
            continue
        sha256 = hashlib.sha256(content).hexdigest()
        if sha256 in seen:
            continue
        label = _sanitize_filename((doc_label or "Document").strip())[:60]
        ext = Path(name).suffix.lower() or ".pdf"
        dest_dir = paths["vault"] / claim_dir
        dest = _dedup_path(dest_dir / f"{label} - {claim_dir} - {date}{ext}")
        if not dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
        entry = {"ts": _now_iso(), "sha256": sha256, "claim": claim,
                 "doc_label": doc_label, "original_name": name,
                 "path": str(dest.relative_to(paths["root"])),
                 "source": source, "dry_run": dry_run}
        if not dry_run:
            _append_jsonl(paths["catalog"], entry)
        seen.add(sha256)
        filed.append(entry)
        log_event(paths, "doc_filed", claim=claim, doc_label=doc_label,
                  original_name=name, path=entry["path"], dry_run=dry_run)
    return filed


def find_vault_document(client, config: dict, paths: dict,
                        request_text: str) -> dict | None:
    """Resolve 'do you have the settlement agreement in the Johnson case'
    against the catalog. Returns the best catalog entry or None."""
    catalog = [e for e in _load_catalog(paths) if not e.get("dry_run")]
    if not catalog:
        return None
    listing = "\n".join(
        f"{i} | {e.get('claim') or '?'} | {e.get('doc_label') or '?'} | "
        f"{e.get('original_name') or ''} | filed {(e.get('ts') or '')[:10]}"
        for i, e in enumerate(catalog[-500:])
    )
    prompt = (
        _preamble(config, paths)
        + f"James asked: {request_text!r}\n\n"
          f"Litigation Update Vault catalog (index | claim | type | original "
          f"name | filed):\n{listing}\n\n"
          "Return ONLY a JSON object {\"index\": <int or null>, "
          "\"confidence\": 0.0-1.0} — the single best matching document, or "
          "null if nothing matches what he asked for."
    )
    result = _claude_json(client, prompt, max_tokens=300)
    if not isinstance(result, dict) or result.get("index") is None:
        return None
    try:
        entry = catalog[-500:][int(result["index"])]
    except (ValueError, IndexError):
        return None
    if float(result.get("confidence") or 0) < 0.5:
        return None
    return entry


def email_vault_document(config: dict, paths: dict, entry: dict,
                         request_text: str) -> bool:
    """Send the requested document from rocky@ to James (guarded)."""
    try:
        import outbound
        from rocky import get_msal_app, acquire_token  # lazy
        token = acquire_token(get_msal_app(config))
        doc_path = paths["root"] / entry["path"]
        result = outbound.send_mail_guarded(
            token=token,
            sender_mailbox=config.get("rocky_email", "rocky@gallagherllp.com"),
            to=[config.get("user_email", "jbragdon@gallagherllp.com")],
            subject=f"Rocky — {entry.get('doc_label') or 'Document'} — "
                    f"{entry.get('claim') or ''}"[:150],
            body=(f"You asked: {request_text}\n\nAttached: "
                  f"{entry.get('doc_label')} for {entry.get('claim')} "
                  f"(filed {entry.get('ts', '')[:10]}, vault path "
                  f"{entry.get('path')})."),
            attachments=[{"name": Path(entry["path"]).name,
                          "path": str(doc_path)}],
        )
        ok = bool(result.get("sent"))
        log_event(paths, "doc_sent" if ok else "doc_send_failed",
                  claim=entry.get("claim"), path=entry.get("path"),
                  request=request_text[:200])
        return ok
    except Exception as e:
        log.warning(f"[litigation] emailing vault document failed: {e}")
        log_event(paths, "doc_send_failed", path=entry.get("path"),
                  error=str(e)[:200])
        return False


# =============================================================================
# Mail intake
# =============================================================================

_ADD_RE = re.compile(
    r"\badd\b.{0,60}?\b(?:claims?|litigation)\b.{0,30}?"
    r"\b(?:smartsheet|spread\s*sheet|sheet)\b", re.I | re.S)
_CLOSE_RE = re.compile(
    r"\b(?:move|transfer)\b.{0,100}?\bclosed\s+(?:claims?|cases?)\b", re.I | re.S)
_CLOSE_NAME_RE = re.compile(
    r"\b(?:move|transfer)\s+(?:the\s+)?(.{2,60}?)\s+"
    r"(?:claim|case|matter|entry)\b", re.I)
_UPDATE_RE = re.compile(
    r"\bupdate\b.{0,60}?\b(?:claims?|litigation)\b.{0,30}?"
    r"\b(?:smartsheet|spread\s*sheet|sheet)\b", re.I | re.S)


def _message_text(msg: dict) -> str:
    body = (msg.get("body") or {}).get("content") or msg.get("bodyPreview") or ""
    return re.sub(r"[ \t]+\n", "\n", body).strip()


def _sender_address(msg: dict) -> str:
    return (((msg.get("from") or {}).get("emailAddress") or {})
            .get("address") or "").lower()


def _email_context(msg: dict) -> str:
    sender = ((msg.get("from") or {}).get("emailAddress") or {})
    return (f"From: {sender.get('name') or ''} <{sender.get('address') or ''}>\n"
            f"Subject: {msg.get('subject') or '(no subject)'}\n"
            f"Received: {msg.get('receivedDateTime') or ''}\n"
            f"Body:\n{_message_text(msg)[:BODY_TEXT_CAP] or '(empty)'}\n")


def detect_intent(msg: dict, notice_senders: set[str]) -> tuple[str | None, str | None]:
    """(intent, claim_hint). Intents: notice | add | closure | update."""
    if _sender_address(msg) in notice_senders:
        return "notice", None
    text = f"{msg.get('subject') or ''}\n{_message_text(msg)[:4000]}"
    # A forward of the Bozzuto notice mailbox counts as a notice even when
    # the From is James (auto-forward/redirect either way).
    if any(s in text.lower() for s in notice_senders):
        return "notice", None
    if _CLOSE_RE.search(text):
        m = _CLOSE_NAME_RE.search(text)
        return "closure", (m.group(1).strip() if m else None)
    if _ADD_RE.search(text):
        return "add", None
    if _UPDATE_RE.search(text):
        return "update", None
    return None, None


def _eligible_attachments(token: str, mailbox: str, msg: dict) -> list[dict]:
    from rocky import fetch_attachments, is_signature_image  # lazy
    keep = []
    for att in fetch_attachments(token, mailbox, msg["id"]):
        name = att.get("name") or ""
        if Path(name).suffix.lower() not in LIT_EXTENSIONS:
            continue
        if is_signature_image(name, att.get("contentType"),
                              att.get("size") or 0, att.get("isInline", False)):
            continue
        if not att.get("contentBytes"):
            continue
        keep.append(att)
    return keep


def _docs_from_attachments(attachments: list[dict]) -> list[dict]:
    from rocky import extract_text_from_attachment  # lazy
    return [{"filename": a["name"],
             "text": extract_text_from_attachment(
                 a["name"], a.get("contentType") or "", a["contentBytes"])}
            for a in attachments]


def _next_ask_id(state: dict) -> str:
    n = int(state.get("ask_seq") or 0) + 1
    state["ask_seq"] = n
    return f"L{n:04d}"


def queue_ask(paths: dict, state: dict, ask: dict) -> dict:
    ask = {"id": _next_ask_id(state), "status": "queued",
           "created": _now_iso(), **ask}
    state.setdefault("asks", []).append(ask)
    log_event(paths, "ask_queued", ask_id=ask["id"], ask_kind=ask.get("kind"),
              claim=ask.get("claim"))
    return ask


def _msg_ref(msg: dict, mailbox: str) -> dict:
    return {"mailbox": mailbox, "message_id": msg.get("id"),
            "subject": msg.get("subject"),
            "from": _sender_address(msg),
            "received": msg.get("receivedDateTime"),
            "internet_message_id": msg.get("internetMessageId")}


def intake_pass(client, graph_token: str, ss_token: str | None, config: dict,
                paths: dict, state: dict, backfill_days: int,
                dry_run: bool) -> dict:
    """Scan rocky@'s inbox for litigation mail and queue Teams asks.
    Claude failures hold the cursor (nothing is silently lost)."""
    from vault import fetch_inbox_messages  # shared paged fetcher

    mailbox = (config.get("litigation_mailbox")
               or config.get("rocky_email") or "rocky@gallagherllp.com")
    notice_senders = {s.lower() for s in
                      (config.get("litigation_notice_senders")
                       or ["legalnotices@bozzuto.com"])}

    cursor = state.get("mail_cursor")
    if cursor:
        try:
            since = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
        except ValueError:
            since = datetime.now(timezone.utc) - timedelta(days=backfill_days)
    else:
        since = datetime.now(timezone.utc) - timedelta(days=backfill_days)
    run_start = datetime.now(timezone.utc)

    messages = fetch_inbox_messages(graph_token, mailbox, since,
                                    attachments_only=False)
    counts = {"scanned": len(messages), "queued": 0}
    # Open sheets fetched lazily, only when a closure/update needs them.
    sheets_cache: list[dict] = []
    sheets_loaded = [False]

    def _get_open_sheets() -> list[dict]:
        if not sheets_loaded[0] and ss_token:
            sheets_cache.extend(fetch_open_sheets(ss_token, config))
            sheets_loaded[0] = True
        return sheets_cache

    for msg in messages:
        intent, hint = detect_intent(msg, notice_senders)
        if intent is None:
            state["mail_cursor"] = msg.get("receivedDateTime") or state.get("mail_cursor")
            continue

        attachments = _eligible_attachments(graph_token, mailbox, msg)
        docs = _docs_from_attachments(attachments)
        ctx = _email_context(msg)
        log_event(paths, "mail_intake", intent=intent,
                  subject=msg.get("subject"), sender=_sender_address(msg),
                  attachments=[a.get("name") for a in attachments])

        if intent in ("notice", "add"):
            meta = classify_notice(client, config, paths, docs, ctx)
            if meta is None:
                log.warning("[litigation] classification failed — cursor held; "
                            "this mail retries next run")
                break
            home = spec_for_entity(config, meta.get("entity"))
            queue_ask(paths, state, {
                "kind": "new_entry", "msg": _msg_ref(msg, mailbox),
                "claim": meta.get("claim_name"),
                "sheet_key": home["key"] if home else None,
                "classification": meta, "intent": intent,
            })
            counts["queued"] += 1

        elif intent in ("closure", "update"):
            sheets = _get_open_sheets()
            if not sheets:
                log.error("[litigation] Smartsheet not configured/reachable — "
                          "cursor held for closure/update mail")
                break
            corr = correlate_claim(client, config, paths, sheets, ctx, hint)
            if corr is None:
                log.warning("[litigation] correlation failed — cursor held")
                break
            entry, row = _locate_row(sheets, corr.get("row_id"))
            if row is None or float(corr.get("confidence") or 0) < CORRELATE_FLOOR:
                queue_ask(paths, state, {
                    "kind": "identify", "msg": _msg_ref(msg, mailbox),
                    "intent": intent, "hint": hint,
                    "reason": corr.get("reason"),
                })
                counts["queued"] += 1
            else:
                sheet = entry["sheet"]
                row_dict = row_to_dict(sheet, row)
                claim = row_dict.get(claim_column_title(entry, sheet), "")
                if intent == "closure":
                    note = draft_closure_note(client, config, paths,
                                              row_dict, ctx, docs)
                    if note is None:
                        log.warning("[litigation] closure-note draft failed — "
                                    "cursor held")
                        break
                    queue_ask(paths, state, {
                        "kind": "closure", "msg": _msg_ref(msg, mailbox),
                        "row_id": row["id"], "claim": claim, "note": note,
                        "sheet_key": entry["key"],
                    })
                else:
                    cells = draft_update(client, config, paths, sheet,
                                         row_dict, ctx, docs,
                                         update_cols=entry["update_columns"])
                    if cells is None:
                        log.warning("[litigation] update draft failed — "
                                    "cursor held")
                        break
                    queue_ask(paths, state, {
                        "kind": "update", "msg": _msg_ref(msg, mailbox),
                        "row_id": row["id"], "claim": claim, "cells": cells,
                        "sheet_key": entry["key"],
                    })
                counts["queued"] += 1

        state["mail_cursor"] = msg.get("receivedDateTime") or state.get("mail_cursor")

    if not messages:
        state["mail_cursor"] = state.get("mail_cursor") or run_start.isoformat()
    return counts


# =============================================================================
# Ask execution (runs only after a YES in the chat)
# =============================================================================

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _move_token(config: dict) -> str | None:
    """Token for moving mail in rocky@'s mailbox. Default "delegated":
    rocky@'s cached sign-in with Mail.ReadWrite.Shared (consented
    2026-07-05 for the Inbox Cleaner — covers her own mailbox; no Azure
    change). Set litigation_mail_move_via to "app" if Mail.ReadWrite
    (Application) is ever granted. Returns None on failure — the move is
    best-effort and must never break an ask."""
    via = (config.get("litigation_mail_move_via") or "delegated").strip().lower()
    try:
        if via == "app":
            from rocky import acquire_app_token  # lazy
            return acquire_app_token(config)
        from rocky import get_msal_app  # lazy
        app = get_msal_app(config)
        accounts = app.get_accounts()
        result = app.acquire_token_silent(
            ["Mail.ReadWrite.Shared"], account=accounts[0]) if accounts else None
        if hasattr(app, "_save_cache"):
            app._save_cache()
        if result and "access_token" in result:
            return result["access_token"]
        log.warning("[litigation] no delegated write token in rocky@'s "
                    "cached sign-in — source mail left in the inbox")
    except Exception as e:
        log.warning(f"[litigation] move-token acquisition failed: {e}")
    return None


def _ensure_processed_folder(token: str, mailbox: str, state: dict,
                             name: str) -> str | None:
    """Find-or-create the processed-mail subfolder under the inbox;
    the id is cached in state."""
    cached = (state.get("mail_folders") or {}).get(name)
    if cached:
        return cached
    base = f"{GRAPH_BASE}/users/{mailbox}/mailFolders/inbox/childFolders"
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(base, headers=headers, params={"$top": "200"}, timeout=30)
    r.raise_for_status()
    folder_id = next((f["id"] for f in r.json().get("value", [])
                      if (f.get("displayName") or "").strip().lower()
                      == name.lower()), None)
    if folder_id is None:
        r = requests.post(base, headers=headers, json={"displayName": name},
                          timeout=30)
        r.raise_for_status()
        folder_id = r.json()["id"]
    state.setdefault("mail_folders", {})[name] = folder_id
    return folder_id


def _file_source_mail(config: dict, paths: dict, state: dict,
                      ask: dict) -> None:
    """After a YES executes, move the ask's source mail out of rocky@'s
    inbox into the processed subfolder. Best-effort: any failure is
    logged and the ask's outcome stands. (Runs after execution because a
    Graph move changes the message id the executor re-fetches by.)"""
    ref = ask.get("msg") or {}
    msg_id = ref.get("message_id")
    name = (config.get("litigation_processed_folder")
            if "litigation_processed_folder" in config
            else "Litigation Updater")
    if not msg_id or not (name or "").strip():
        return  # no source mail, or filing disabled with ""
    mailbox = ref.get("mailbox") or (config.get("litigation_mailbox")
                                     or "rocky@gallagherllp.com")
    token = _move_token(config)
    if token is None:
        return
    try:
        for attempt in (1, 2):
            folder_id = _ensure_processed_folder(token, mailbox, state, name)
            r = requests.post(
                f"{GRAPH_BASE}/users/{mailbox}/messages/{msg_id}/move",
                headers={"Authorization": f"Bearer {token}"},
                json={"destinationId": folder_id}, timeout=30)
            if r.status_code == 404 and attempt == 1:
                # Stale cached folder id (folder renamed/deleted) — or the
                # message is already gone. Refresh the folder and retry once.
                (state.get("mail_folders") or {}).pop(name, None)
                continue
            if r.status_code == 404:
                log.info(f"[litigation] source mail for {ask.get('id')} not "
                         f"found — probably already moved")
                return
            r.raise_for_status()
            log_event(paths, "mail_filed", ask_id=ask.get("id"),
                      subject=ref.get("subject"), folder=name)
            return
    except Exception as e:
        log.warning(f"[litigation] couldn't move the source mail for "
                    f"{ask.get('id')} to {name!r}: {e}")


def _refetch_attachments(config: dict, ask: dict) -> list[dict]:
    """Attachments for the ask's source mail, re-fetched at execution time
    (mail is durable; asks stay small)."""
    ref = ask.get("msg") or {}
    if not ref.get("message_id"):
        return []
    try:
        from rocky import acquire_app_token  # lazy
        token = acquire_app_token(config)
        return _eligible_attachments(token, ref["mailbox"],
                                     {"id": ref["message_id"]})
    except Exception as e:
        log.warning(f"[litigation] could not re-fetch attachments: {e}")
        return []


def execute_ask(client, ss_token: str | None, config: dict, paths: dict,
                state: dict, ask: dict, dry_run: bool) -> str:
    """Apply an approved ask. Returns the Teams acknowledgement text."""
    kind = ask.get("kind")
    if ss_token is None:
        return ("I can't reach Smartsheet (no smartsheet_token in config) — "
                f"nothing was written. ({ask['id']})")

    # The ask's home sheet: sheet_key when stored, entity routing as the
    # new-entry fallback, first configured sheet otherwise.
    spec = spec_by_key(config, ask.get("sheet_key")) \
        or spec_for_entity(config, (ask.get("classification") or {}).get("entity"))
    if spec is None:
        return ("No open claims sheets are configured "
                f"(litigation_open_sheets) — nothing was written. ({ask['id']})")

    if kind == "new_entry":
        open_sheet = get_sheet(ss_token, spec["sheet_id"])
        attachments = _refetch_attachments(config, ask)
        docs = _docs_from_attachments(attachments)
        ref = ask.get("msg") or {}
        ctx = (f"From: {ref.get('from')}\nSubject: {ref.get('subject')}\n"
               f"Received: {ref.get('received')}")
        cells = draft_entry(client, config, paths, open_sheet, docs, ctx,
                            ask.get("classification") or {},
                            instructions=ask.get("instructions"))
        if not cells:
            return (f"Drafting the entry failed (Claude error) — nothing was "
                    f"added. I'll leave {ask['id']} for you to re-approve "
                    f"once the API is happier.")
        row_id = None
        if not dry_run:
            row_id = add_row(ss_token, open_sheet, cells)
        claim = ask.get("claim") or \
            cells.get(claim_column_title(spec, open_sheet)) or "(unnamed)"
        ask["claim"] = claim
        filed = file_documents(paths, claim,
                               (ask.get("classification") or {}).get("doc_label")
                               or "Document",
                               attachments, {"ask": ask["id"], **ref}, dry_run)
        log_event(paths, "claim_added", ask_id=ask["id"], claim=claim,
                  sheet=spec["key"], row_id=row_id, cells=cells,
                  dry_run=dry_run)
        ask["applied"] = not dry_run
        entry_lines = "\n".join(f"  {t}: {v}" for t, v in cells.items())
        return (f"{'DRY RUN — would add' if dry_run else 'Added'} "
                f"\"{claim}\" to the {spec['key']} claims sheet:\n{entry_lines}\n"
                f"({len(filed)} document(s) filed to the vault.) ({ask['id']})")

    if kind == "closure":
        open_sheet = get_sheet(ss_token, spec["sheet_id"])
        closed_sheet = get_sheet(ss_token, config["litigation_closed_sheet_id"])
        row = find_row(open_sheet, ask.get("row_id"))
        if row is None:
            return (f"That row is no longer on the open sheet — nothing "
                    f"moved. ({ask['id']})")
        closure_col = (config.get("litigation_closure_column")
                       or "Closure Notes")
        result = move_row_to_closed(ss_token, open_sheet, closed_sheet, row,
                                    ask.get("note") or "", closure_col,
                                    paths, dry_run, config,
                                    column_map=spec["column_map"])
        attachments = _refetch_attachments(config, ask)
        file_documents(paths, ask.get("claim") or "", "Closure documents",
                       attachments, {"ask": ask["id"]}, dry_run)
        log_event(paths, "claim_closed", ask_id=ask["id"],
                  claim=ask.get("claim"), row_id=ask.get("row_id"),
                  note=ask.get("note"), dry_run=dry_run, **{
                      k: v for k, v in result.items() if k != "cells"})
        ask["applied"] = not dry_run
        extra = ("" if result.get("note_placed", True) else
                 "\n(NOTE: the closed sheet has no closure-note column — the "
                 "note is in the activity log; add the column and I'll use "
                 "it next time.)")
        return (f"{'DRY RUN — would move' if dry_run else 'Moved'} "
                f"\"{ask.get('claim')}\" to Closed claims with the closure "
                f"note.{extra} ({ask['id']})")

    if kind == "update":
        open_sheet = get_sheet(ss_token, spec["sheet_id"])
        row = find_row(open_sheet, ask.get("row_id"))
        if row is None:
            return (f"That row is no longer on the open sheet — nothing "
                    f"updated. ({ask['id']})")
        if not dry_run:
            update_row(ss_token, open_sheet, ask["row_id"],
                       ask.get("cells") or {})
        attachments = _refetch_attachments(config, ask)
        file_documents(paths, ask.get("claim") or "", "Update documents",
                       attachments, {"ask": ask["id"]}, dry_run)
        log_event(paths, "claim_updated", ask_id=ask["id"],
                  claim=ask.get("claim"), row_id=ask.get("row_id"),
                  cells=ask.get("cells"), dry_run=dry_run)
        ask["applied"] = not dry_run
        return (f"{'DRY RUN — would update' if dry_run else 'Updated'} "
                f"\"{ask.get('claim')}\". ({ask['id']})")

    if kind == "cleanup_fix":
        open_sheet = get_sheet(ss_token, spec["sheet_id"])
        row = find_row(open_sheet, ask.get("row_id"))
        if row is None:
            return f"That row no longer exists — skipped. ({ask['id']})"
        if not dry_run:
            update_row(ss_token, open_sheet, ask["row_id"],
                       {ask["column"]: ask["proposed"]})
        log_event(paths, "cleanup_fix_applied", ask_id=ask["id"],
                  claim=ask.get("claim"), column=ask.get("column"),
                  before=ask.get("current"), after=ask.get("proposed"),
                  dry_run=dry_run)
        return (f"{'DRY RUN — would fix' if dry_run else 'Fixed'} "
                f"{ask.get('column')!r} on \"{ask.get('claim')}\". ({ask['id']})")

    return f"I don't know how to execute a {kind!r} ask. ({ask['id']})"


def resolve_identify(client, ss_token: str | None, config: dict, paths: dict,
                     state: dict, ask: dict, claim_text: str) -> tuple[str, bool]:
    """James named the claim for an ambiguous closure/update — correlate by
    name and queue the real ask. Returns (ack, resolved); when resolved is
    False the identify ask stays pending so the next reply retries."""
    if ss_token is None:
        return ("I can't reach Smartsheet, so I can't look that claim up "
                "yet.", False)
    sheets = fetch_open_sheets(ss_token, config)
    if not sheets:
        return ("None of the open claims sheets would load — tell me the "
                "claim name again in a bit.", False)
    ref = ask.get("msg") or {}
    ctx = (f"From: {ref.get('from')}\nSubject: {ref.get('subject')}\n"
           f"(James says this concerns the {claim_text!r} claim.)")
    corr = correlate_claim(client, config, paths, sheets, ctx, claim_text)
    entry, row = _locate_row(sheets, (corr or {}).get("row_id"))
    if row is None:
        return (f"I still can't find \"{claim_text}\" on the open sheets — "
                f"check the name and tell me again. ({ask['id']})", False)
    open_sheet = entry["sheet"]
    row_dict = row_to_dict(open_sheet, row)
    claim = row_dict.get(claim_column_title(entry, open_sheet), claim_text)

    attachments = _refetch_attachments(config, ask)
    docs = _docs_from_attachments(attachments)
    if ask.get("intent") == "closure":
        note = draft_closure_note(client, config, paths, row_dict,
                                  ctx, docs)
        if note is None:
            return ("Drafting the closure note failed — tell me again in a "
                    "bit.", False)
        queue_ask(paths, state, {"kind": "closure", "msg": ref,
                                 "row_id": row["id"], "claim": claim,
                                 "note": note, "sheet_key": entry["key"]})
    else:
        cells = draft_update(client, config, paths, open_sheet, row_dict,
                             ctx, docs, update_cols=entry["update_columns"])
        if cells is None:
            return ("Drafting the update failed — tell me again in a bit.",
                    False)
        queue_ask(paths, state, {"kind": "update", "msg": ref,
                                 "row_id": row["id"], "claim": claim,
                                 "cells": cells, "sheet_key": entry["key"]})
    return (f"Got it — that's \"{claim}\". I'll propose the "
            f"{ask.get('intent')} next. ({ask['id']})", True)


# =============================================================================
# Teams chat
# =============================================================================

_YES_RE = re.compile(r"^\s*(yes|y|yep|yeah|approve[d]?|ok(ay)?|go ahead|do it|"
                     r"sure|confirm(ed)?|👍)\b", re.I)
_NO_RE = re.compile(r"^\s*(no|n|nope|decline[d]?|skip( it)?|don'?t|do not|"
                    r"pass|leave it)\b", re.I)
# A yes/no carrying conditions ("yes but shorten it") must NOT resolve the
# ask as-is — it goes through the revision router instead.
_COND_RE = re.compile(r"\b(but|except|however|instead|chang(e|ing)|revise|"
                      r"edit|add|remove|drop|shorten|expand|reword|mention|"
                      r"include)\b", re.I)
_REPORT_RE = re.compile(
    r"\b(?:create|run|generate|prepare|give me|please\s+(?:create|run))\b"
    r".{0,40}?\blitigation\b.{0,30}?\b(?:update|report|audit)\b"
    r".{0,20}?\bfor\s+[\"']?([\w&][\w&\s.]{0,30}?)[\"']?\s*[.?!]?\s*$",
    re.I | re.S)
_DOC_RE = re.compile(
    r"\bdo\s+(?:you|we)\s+have\b|\bcan\s+you\s+(?:send|find|pull)\b", re.I)


def _proposal_text(ask: dict) -> str:
    kind = ask.get("kind")
    if kind == "new_entry":
        meta = ask.get("classification") or {}
        rec = ("I suggest creating a new claims entry."
               if meta.get("recommend_entry")
               else "Based on your standing rules I do NOT think this needs "
                    "an entry, but it's your call.")
        return (f"[{ask['id']}] New legal notice — "
                f"{meta.get('doc_label') or meta.get('doc_type') or 'document'}"
                f" ({meta.get('entity') or 'entity unclear'}).\n"
                f"Parties: {meta.get('parties') or '?'}"
                + (f" | {meta.get('court_or_forum')}"
                   if meta.get("court_or_forum") else "")
                + (f" | No. {meta.get('case_number')}"
                   if meta.get("case_number") else "")
                + f"\n{meta.get('summary') or ''}\n"
                  f"{rec} ({meta.get('recommend_reason') or ''})\n"
                + (("Your instructions so far: "
                    + "; ".join(ask["instructions"]) + "\n")
                   if ask.get("instructions") else "")
                + (f"Reply YES to draft and add the entry to the "
                   f"{ask['sheet_key']} sheet, NO to skip."
                   if ask.get("sheet_key") else
                   "Reply YES to draft and add the entry, NO to skip."))
    if kind == "closure":
        return (f"[{ask['id']}] Move \"{ask.get('claim')}\" to Closed claims "
                f"with this closure note?\n---\n{ask.get('note')}\n---\n"
                f"Reply YES to move it, NO to leave it open.")
    if kind == "update":
        lines = "\n".join(f"  {t}: {v}" for t, v in (ask.get("cells") or {}).items())
        return (f"[{ask['id']}] Update \"{ask.get('claim')}\" on the claims "
                f"sheet:\n{lines}\nReply YES to apply, NO to skip.")
    if kind == "cleanup_fix":
        return (f"[{ask['id']}] Cleanup — \"{ask.get('claim')}\", column "
                f"{ask.get('column')!r}:\n  now: {ask.get('current') or '(blank)'}\n"
                f"  proposed: {ask.get('proposed')}\n"
                f"  why: {ask.get('reason') or ''}\n"
                f"Reply YES to fix, NO to leave as is.")
    if kind == "identify":
        return (f"[{ask['id']}] You forwarded a {ask.get('intent')} request "
                f"(\"{(ask.get('msg') or {}).get('subject') or ''}\") but I "
                f"couldn't match it to an open claim"
                + (f" ({ask.get('reason')})" if ask.get("reason") else "")
                + ". Which claim is it? (Reply with the claim name, or NO to "
                  "drop it.)")
    return f"[{ask['id']}] (unknown ask)"


def _route_pending_reply(client, config: dict, paths: dict, ask: dict,
                         text: str) -> dict:
    """Interpret a non-yes/no reply to the pending ask. Returns
    {"action": "approve"|"decline"|"revise"|"unrelated",
     "instruction": <for revise>}. Claude-failure fallback is "unrelated"
    (the ask stays pending; nothing executes on a guess)."""
    prompt = (
        _preamble(config, paths)
        + f"Rocky proposed this to James over Teams:\n\n"
        + _proposal_text(ask)[:1500]
        + f"\n\nJames replied: {text!r}\n\n"
          "Classify the reply. Return ONLY a JSON object:\n"
          '  "action": "approve" (an unconditional yes) | "decline" (a '
          'no/leave-it) | "revise" (he wants the proposed content changed '
          'before it is applied — including a yes WITH conditions) | '
          '"unrelated" (the message is about something else)\n'
          '  "instruction": for revise, the change James wants, imperative '
          "form; else null"
    )
    result = _claude_json(client, prompt, max_tokens=300)
    if not isinstance(result, dict) or result.get("action") not in (
            "approve", "decline", "revise", "unrelated"):
        return {"action": "unrelated", "instruction": None}
    return result


def revise_ask(client, config: dict, paths: dict, ask: dict,
               instruction: str) -> str | None:
    """Apply James's revision to the PENDING ask's stored content (the ask
    is then re-proposed for a clean YES). Returns an error string, or None
    on success."""
    kind = ask.get("kind")
    if kind == "new_entry":
        # The entry is drafted AFTER the YES — store the instruction and
        # honor it at drafting time.
        ask.setdefault("instructions", []).append(instruction)
        return None
    if kind == "closure":
        voice = load_voice(paths, "closure")
        text = _claude_text(
            client,
            _preamble(config, paths)
            + (f"Closure-note voice guide:\n{voice}\n\n" if voice else "")
            + f"Current draft closure note for \"{ask.get('claim')}\":\n"
              f"{ask.get('note')}\n\nJames wants this change: {instruction}\n\n"
              "Return ONLY the revised note text.",
            max_tokens=800)
        if text:
            ask["note"] = text.strip()
            return None
        return "redrafting the note failed"
    if kind == "update":
        result = _claude_json(
            client,
            _preamble(config, paths)
            + f"Current proposed cell updates for \"{ask.get('claim')}\":\n"
              f"{json.dumps(ask.get('cells') or {}, indent=1)}\n\n"
              f"James wants this change: {instruction}\n\n"
              "Return ONLY a JSON object mapping column titles to their "
              "revised FULL values (same format, whole cell contents).",
            max_tokens=2000)
        if isinstance(result, dict) and result:
            ask["cells"] = result
            return None
        return "redrafting the update failed"
    if kind == "cleanup_fix":
        result = _claude_json(
            client,
            _preamble(config, paths)
            + f"A cleanup fix for \"{ask.get('claim')}\", column "
              f"{ask.get('column')!r}: current value {ask.get('current')!r}, "
              f"proposed value {ask.get('proposed')!r}.\n"
              f"James wants this change to the proposal: {instruction}\n\n"
              "Return ONLY a JSON object: {\"proposed\": \"<revised value>\"}",
            max_tokens=300)
        if isinstance(result, dict) and (result.get("proposed") or "").strip():
            ask["proposed"] = str(result["proposed"]).strip()
            return None
        return "revising the value failed"
    return f"a {kind!r} ask can't be revised — reply with the claim name, or NO"


def _route_free_text(client, config: dict, paths: dict, text: str) -> dict:
    """Claude router for owner messages that aren't a yes/no or an obvious
    command. Returns {intent, entity, request, reply}."""
    entities = list(_entity_spec(config).keys())
    prompt = (
        _preamble(config, paths)
        + f"James sent this in the Litigation Updates chat: {text!r}\n\n"
          "Classify it. Return ONLY a JSON object:\n"
          '  "intent": one of "report_request" (asking for a litigation '
          'update/audit report for an entity), "doc_request" (asking whether '
          "you have / to send a document from the vault), \"feedback\" "
          "(an instruction, correction, or preference about how you should "
          'work), "question", "other"\n'
          f'  "entity": for report_request, one of {json.dumps(entities)} '
          'or null\n'
          '  "reply": a one-or-two-sentence reply to send back (for '
          "feedback: acknowledge and say you'll fold it into your rules on "
          "the weekly learning pass; for questions: answer if you can)"
    )
    result = _claude_json(client, prompt, max_tokens=400)
    if not isinstance(result, dict):
        return {"intent": "other",
                "reply": "Noted — I've logged that for my weekly learning pass."}
    return result


def _match_entity(config: dict, text: str) -> str | None:
    spec = _entity_spec(config)
    cleaned = re.sub(r"[\"'.]", "", text or "").strip().upper()
    if cleaned in spec:
        return cleaned
    for key, ent in spec.items():
        if cleaned and (cleaned in ent["name"].upper()
                        or ent["name"].upper() in cleaned):
            return key
    return None


def chat_cycle(config: dict, paths: dict, dry_run: bool) -> dict:
    """One Teams poll: ingest replies, resolve the pending ask, handle
    free-text (reports, vault lookups, feedback), propose the next ask."""
    import teams
    from anthropic import Anthropic  # lazy
    from rocky import get_msal_app  # lazy

    state = load_state(paths)
    client = Anthropic(api_key=config["anthropic_api_key"])
    ss_token = (config.get("smartsheet_token") or "").strip() or None

    app = get_msal_app(config)
    try:
        token = teams.acquire_teams_token(app)
    except teams.TeamsNotEnabled as e:
        log_event(paths, "teams_unavailable", detail=str(e)[:300])
        log.error(f"[litigation] Teams not available: {e}")
        return {"error": "teams_not_enabled"}

    rocky_upn = config.get("rocky_email", "rocky@gallagherllp.com")
    owner_upn = (config.get("litigation_chat_owner")
                 or config.get("user_email", "jbragdon@gallagherllp.com")).lower()
    observers = [o.lower() for o in (config.get("litigation_observers") or [])
                 if o and o.lower() != owner_upn]

    if not state.get("self_id"):
        state["self_id"] = teams.get_self_user_id(token)
    if not state.get("chat_id"):
        # A named conversation requires a group chat (1:1 chats can't carry
        # a topic). Group chats are created anew on every POST, so the id
        # MUST be persisted and reused forever.
        topic = config.get("litigation_chat_topic") or "Litigation Updates"
        chat_id = teams.ensure_group_chat(
            token, topic, [rocky_upn, owner_upn] + observers)
        if not chat_id:
            log_event(paths, "chat_create_failed")
            return {"error": "chat_create_failed"}
        state["chat_id"] = chat_id
        log_event(paths, "chat_created", chat_id=chat_id, topic=topic)
        intro = ("Hi James — this is the Litigation Updates chat. I'll "
                 "propose claims-sheet changes here one at a time (new "
                 "entries from Bozzuto legal notices, updates, closures, and "
                 "cleanup fixes) — reply YES or NO. You can also ask me "
                 "things like \"do you have the settlement agreement in the "
                 "Johnson case\" or \"run a litigation update for BMC\", and "
                 "anything you teach me here gets folded into my rules on "
                 "the weekly learning pass. Nothing touches the Smartsheet "
                 "without your YES.")
        sent = teams.send_chat_message(token, chat_id, intro)
        log_comm(paths, "out", intro, message_id=(sent or {}).get("id"))
    save_state(paths, state)
    chat_id = state["chat_id"]

    if not state.get("owner_id"):
        members = teams.list_chat_members(token, chat_id)
        state["members"] = {m["userId"]: m["email"]
                            for m in members if m.get("userId")}
        owner_addrs = {owner_upn, teams.swap_legacy_domain(owner_upn).lower()}
        state["owner_id"] = next(
            (m["userId"] for m in members if m.get("email") in owner_addrs),
            None)
        save_state(paths, state)
    owner_id = state.get("owner_id")

    msgs = teams.fetch_chat_messages(token, chat_id,
                                     since_iso=state.get("teams_cursor"))
    inbound: list[dict] = []
    for m in msgs:
        created = m.get("createdDateTime") or ""
        if created > (state.get("teams_cursor") or ""):
            state["teams_cursor"] = created
        sender = teams.message_sender_id(m)
        if sender and sender != state.get("self_id"):
            text = teams.message_text(m)
            log_comm(paths, "in", text, message_id=m.get("id"),
                     sender=(state.get("members") or {}).get(sender, sender))
            inbound.append({"text": text, "created": created,
                            "sender_id": sender})
    save_state(paths, state)

    asks = state.setdefault("asks", [])
    result: dict = {"inbound": len(inbound)}

    def _send(text: str, **fields):
        sent = teams.send_chat_message(token, chat_id, text)
        log_comm(paths, "out", text, message_id=(sent or {}).get("id"), **fields)
        return sent

    def _approve_pending(ask: dict, reply_text: str) -> None:
        ask["status"] = "approved"
        ask["decided"] = _now_iso()
        log_event(paths, "ask_decided", ask_id=ask["id"],
                  decision="approved", reply=reply_text[:200])
        try:
            ack = execute_ask(client, ss_token, config, paths, state,
                              ask, dry_run)
            ask["status"] = "done"
            if ask.get("applied"):
                _file_source_mail(config, paths, state, ask)
        except (SmartsheetError, KeyError) as e:
            log.error(f"[litigation] executing {ask['id']} failed: {e}")
            log_event(paths, "ask_execute_failed", ask_id=ask["id"],
                      error=str(e)[:300])
            ack = (f"Smartsheet balked while I was applying {ask['id']} — "
                   f"nothing may have been written. I've logged the error; "
                   f"say YES again later to retry.")
            ask["status"] = "queued"
        _send(ack, ask_id=ask["id"])
        save_state(paths, state)
        result.setdefault("decided", []).append(ask["id"])

    def _decline_pending(ask: dict, reply_text: str) -> None:
        ask["status"] = "declined"
        ask["decided"] = _now_iso()
        log_event(paths, "ask_decided", ask_id=ask["id"],
                  decision="declined", reply=reply_text[:200])
        _send(f"Understood — skipped. ({ask['id']})", ask_id=ask["id"])
        save_state(paths, state)
        result.setdefault("decided", []).append(ask["id"])

    # --- Handle the owner's messages, oldest first. -------------------------
    # Only the owner decides asks (fail-safe when owner_id is unresolved).
    pending = next((a for a in asks if a.get("status") == "proposed"), None)
    for m in inbound:
        if owner_id is None or m["sender_id"] != owner_id:
            continue
        text = m["text"]

        # 1. A decision on the pending ask. A clean YES/NO resolves it;
        #    a yes/no CARRYING CONDITIONS ("yes but shorten the note") or
        #    free-form direction goes through the revision router — the
        #    revised proposal comes back for a final clean YES.
        if pending is not None and \
                m["created"] > (pending.get("proposed_at") or ""):
            conditional = bool(_COND_RE.search(text))
            if _YES_RE.match(text) and not conditional:
                _approve_pending(pending, text)
                pending = None
                continue
            if _NO_RE.match(text) and not conditional:
                _decline_pending(pending, text)
                pending = None
                continue
            if pending.get("kind") == "identify":
                try:
                    ack, resolved = resolve_identify(
                        client, ss_token, config, paths, state, pending, text)
                except (SmartsheetError, KeyError) as e:
                    log.error(f"[litigation] identify {pending['id']} "
                              f"failed: {e}")
                    ack, resolved = ("Smartsheet balked while I was looking "
                                     "that up — tell me the claim name again "
                                     "in a bit.", False)
                if resolved:
                    pending["status"] = "done"
                    pending["decided"] = _now_iso()
                    pending = None
                # Unresolved: stays proposed so the next reply retries.
                _send(ack)
                save_state(paths, state)
                continue

            routed = _route_pending_reply(client, config, paths, pending, text)
            action = routed.get("action")
            if action == "approve":
                _approve_pending(pending, text)
                pending = None
                continue
            if action == "decline":
                _decline_pending(pending, text)
                pending = None
                continue
            if action == "revise":
                instruction = (routed.get("instruction") or text).strip()
                err = revise_ask(client, config, paths, pending, instruction)
                if err:
                    _send(f"I couldn't apply that revision ({err}). The "
                          f"proposal stands as-is — YES/NO, or try "
                          f"rewording. ({pending['id']})",
                          ask_id=pending["id"])
                else:
                    log_event(paths, "ask_revised", ask_id=pending["id"],
                              instruction=instruction[:300])
                    sent = _send("Revised — updated proposal:\n\n"
                                 + _proposal_text(pending),
                                 ask_id=pending["id"])
                    if sent:
                        pending["proposed_at"] = (sent.get("createdDateTime")
                                                  or _now_iso())
                save_state(paths, state)
                continue
            # "unrelated" — the ask stays pending; fall through to the
            # free-text handling below (reports, doc requests, feedback).

        # 2. Deterministic commands.
        rep = _REPORT_RE.search(text)
        if rep:
            entity_key = _match_entity(config, rep.group(1))
            if entity_key:
                ack = run_report(config, paths, entity_key, dry_run)
                _send(ack)
                continue
        if _DOC_RE.search(text):
            entry = find_vault_document(client, config, paths, text)
            if entry is None:
                _send("I don't have that in the Litigation Update Vault — "
                      "I only hold documents I've processed. If you forward "
                      "it to me I'll file it with the claim.")
                log_event(paths, "doc_request_unmatched", request=text[:200])
            else:
                ok = (not dry_run) and email_vault_document(config, paths,
                                                            entry, text)
                _send(f"Yes — {entry.get('doc_label')} for "
                      f"\"{entry.get('claim')}\" (filed "
                      f"{(entry.get('ts') or '')[:10]}). "
                      + ("I've emailed it to you." if ok else
                         "DRY RUN — I would email it to you." if dry_run else
                         "Emailing it failed — it's at "
                         f"{entry.get('path')} in the vault."))
            continue

        # 3. Everything else through the Claude router.
        routed = _route_free_text(client, config, paths, text)
        intent = routed.get("intent")
        if intent == "report_request" and _match_entity(config, routed.get("entity") or ""):
            ack = run_report(config, paths,
                             _match_entity(config, routed["entity"]), dry_run)
            _send(ack)
        elif intent == "doc_request":
            entry = find_vault_document(client, config, paths, text)
            if entry is not None:
                ok = (not dry_run) and email_vault_document(config, paths,
                                                            entry, text)
                _send(f"Yes — {entry.get('doc_label')} for "
                      f"\"{entry.get('claim')}\". "
                      + ("I've emailed it to you." if ok else
                         "DRY RUN — I would email it to you." if dry_run else
                         f"Emailing failed — it's at {entry.get('path')}."))
            else:
                _send("I don't have that one in the vault.")
        else:
            if intent == "feedback":
                log_event(paths, "feedback_received", text=text[:500])
            _send(routed.get("reply")
                  or "Noted — I've logged that for my weekly learning pass.")

    # --- Propose the next queued ask (one live ask at a time). --------------
    still_pending = any(a.get("status") == "proposed" for a in asks)
    if not still_pending:
        nxt = next((a for a in asks if a.get("status") == "queued"), None)
        if nxt is not None:
            sent = _send(_proposal_text(nxt), ask_id=nxt["id"])
            if sent:
                nxt["status"] = "proposed"
                nxt["proposed_at"] = sent.get("createdDateTime") or _now_iso()
                log_event(paths, "ask_proposed", ask_id=nxt["id"],
                          ask_kind=nxt.get("kind"), claim=nxt.get("claim"))
                result["proposed"] = nxt["id"]
    save_state(paths, state)
    return result


def follow_chat(config: dict, paths: dict, dry_run: bool, minutes: int,
                interval: int = 15) -> None:
    """Interactive session mode (--chat --follow): keep the Teams cycle hot
    so a YES/NO gets an immediate response and the next proposal — built for
    burning through the cleanup queue. Exits after `minutes` (default 30),
    or sooner once the ask queue is drained and the chat has gone quiet for
    a couple of minutes. Ctrl+C exits cleanly. Idle cycles make no Claude
    calls (just a Teams fetch), so the session is cheap."""
    deadline = time.time() + minutes * 60
    quiet_exit_cycles = max(1, 120 // interval)  # ~2 min of quiet
    idle = 0
    log.info(f"[litigation] following the Teams chat for up to {minutes} "
             f"min (checking every {interval}s; Ctrl+C to stop)")
    try:
        while time.time() < deadline:
            result = chat_cycle(config, paths, dry_run)
            if result.get("error"):
                log.error(f"[litigation] follow session ending: "
                          f"{result['error']}")
                return
            state = load_state(paths)
            outstanding = any(a.get("status") in ("queued", "proposed")
                              for a in (state.get("asks") or []))
            active = bool(result.get("inbound") or result.get("proposed")
                          or result.get("decided"))
            idle = 0 if active else idle + 1
            if not outstanding and idle >= quiet_exit_cycles:
                log.info("[litigation] ask queue drained and chat quiet — "
                         "ending follow session")
                return
            time.sleep(interval)
        log.info(f"[litigation] follow session reached the {minutes}-minute "
                 f"limit — schedule --poll for ongoing coverage")
    except KeyboardInterrupt:
        log.info("[litigation] follow session stopped (Ctrl+C)")


# =============================================================================
# Voices
# =============================================================================

def _column_samples(sheet: dict, column_titles: list[str]) -> list[str]:
    out: list[str] = []
    wanted = {t.strip().lower() for t in column_titles if t}
    for r in sheet.get("rows", []):
        d = row_to_dict(sheet, r)
        for t, v in d.items():
            if t.strip().lower() in wanted and v and len(v) > 20:
                out.append(v[:VOICE_SAMPLE_CHARS])
    return out[-VOICE_SAMPLE_CAP:]


def _voice_seed_text(paths: dict, name: str) -> str:
    """Exemplar documents dropped in voices\\seeds\\<name>\\ — extracted and
    folded into that voice's rebuild so hand-picked references survive
    every --voice-rebuild."""
    seed_dir = paths["voices"] / "seeds" / name
    if not seed_dir.exists():
        return ""
    from rocky import extract_text_from_path  # lazy
    chunks = []
    for f in sorted(seed_dir.glob("*"))[:5]:
        if f.suffix.lower() in {".docx", ".pdf", ".txt", ".md", ".html"}:
            text = extract_text_from_path(f)
            if text:
                chunks.append(f"--- {f.name} ---\n{text[:REPORT_FILE_TEXT_CAP]}")
    return "\n\n".join(chunks)


def _build_voice(client, config: dict, paths: dict, name: str, what: str,
                 samples: list[str], extra_context: str = "") -> bool:
    if not samples and not extra_context:
        (paths["voices"] / f"voice_{name}.md").write_text(
            f"# Voice: {name}\n\n(No source material yet — rerun "
            f"--voice-rebuild once the sheet has {what} entries.)\n",
            encoding="utf-8")
        return False
    prompt = (
        _preamble(config, paths)
        + f"Below are real examples of {what} from the Bozzuto claims "
          f"Smartsheet. Write a compact STYLE GUIDE (a 'voice') that would "
          f"let a paralegal produce new ones indistinguishable from these: "
          f"tone, tense, person, typical length, date and citation formats, "
          f"abbreviations, what is always/never included, and 2-3 verbatim "
          f"exemplars. Markdown, under 500 words.\n\n"
        + "\n---\n".join(samples)
        + (f"\n\nGold-standard exemplar documents (these show the target "
           f"voice best — weight them above the sheet samples):\n"
           f"{extra_context}" if extra_context else "")
    )
    text = _claude_text(client, prompt, max_tokens=1500)
    if text is None:
        return False
    (paths["voices"] / f"voice_{name}.md").write_text(
        f"# Voice: {name}\n_(built {datetime.now():%Y-%m-%d} from "
        f"{len(samples)} sample(s))_\n\n{text.strip()}\n", encoding="utf-8")
    return True


def rebuild_voices(config: dict, paths: dict) -> dict:
    from anthropic import Anthropic  # lazy
    client = Anthropic(api_key=config["anthropic_api_key"])
    ss_token = (config.get("smartsheet_token") or "").strip()
    if not ss_token:
        log.error("[litigation] smartsheet_token missing — cannot rebuild voices")
        return {"error": "no_smartsheet_token"}
    open_entries = fetch_open_sheets(ss_token, config)
    if not open_entries:
        log.error("[litigation] no open sheets loaded — check "
                  "smartsheet_token and litigation_open_sheets ids")
        return {"error": "no_open_sheets"}
    closed_sheet = get_sheet(ss_token, config["litigation_closed_sheet_id"])

    update_samples: list[str] = []
    for e in open_entries:
        cols = e["update_columns"] or [
            t for t in sheet_column_titles(e["sheet"])
            if t != primary_column_title(e["sheet"])]
        update_samples += _column_samples(e["sheet"], cols)
    update_samples += _column_samples(
        closed_sheet, config.get("litigation_update_columns")
        or ["Status/Action Items", "Overall Status"])
    results = {}
    results["updates"] = _build_voice(
        client, config, paths, "updates",
        "status/update entries", update_samples[-VOICE_SAMPLE_CAP:],
        extra_context=_voice_seed_text(paths, "updates"))

    summary_col = (config.get("litigation_summary_column")
                   or "Summary of Claim")
    summary_samples: list[str] = []
    for e in open_entries:
        summary_samples += _column_samples(e["sheet"], [summary_col])
    summary_samples += _column_samples(closed_sheet, [summary_col])
    results["summary"] = _build_voice(
        client, config, paths, "summary",
        "internal claim-summary narratives",
        summary_samples[-VOICE_SAMPLE_CAP:],
        extra_context=_voice_seed_text(paths, "summary"))

    closure_col = config.get("litigation_closure_column") or "Closure Notes"
    results["closure"] = _build_voice(
        client, config, paths, "closure",
        "closure notes", _column_samples(closed_sheet, [closure_col]),
        extra_context=_voice_seed_text(paths, "closure"))

    disclosure_col = (config.get("litigation_disclosure_column")
                      or "Third Party Disclosure Summary")
    reports_text = ""
    reports_dir = (config.get("litigation_audit_reports_dir") or "").strip()
    if reports_dir and Path(reports_dir).exists():
        from rocky import extract_text_from_path  # lazy
        chunks = []
        for f in sorted(Path(reports_dir).glob("*"))[:5]:
            if f.suffix.lower() in {".docx", ".pdf", ".txt", ".md", ".html"}:
                text = extract_text_from_path(f)
                if text:
                    chunks.append(f"--- {f.name} ---\n{text[:REPORT_FILE_TEXT_CAP]}")
        reports_text = "\n\n".join(chunks)
    disclosure_samples: list[str] = []
    for e in open_entries:
        disclosure_samples += _column_samples(e["sheet"], [disclosure_col])
    disclosure_samples += _column_samples(closed_sheet, [disclosure_col])
    seed_text = _voice_seed_text(paths, "disclosure")
    results["disclosure"] = _build_voice(
        client, config, paths, "disclosure",
        "third-party-disclosure entries",
        disclosure_samples[-VOICE_SAMPLE_CAP:],
        extra_context="\n\n".join(t for t in (reports_text, seed_text) if t))

    log_event(paths, "voices_rebuilt", **{k: bool(v) for k, v in results.items()})
    return results


# =============================================================================
# Cleanup phase (initial learning / compliance review)
# =============================================================================

def run_cleanup(config: dict, paths: dict, limit: int | None,
                dry_run: bool) -> dict:
    from anthropic import Anthropic  # lazy
    client = Anthropic(api_key=config["anthropic_api_key"])
    ss_token = (config.get("smartsheet_token") or "").strip()
    if not ss_token:
        log.error("[litigation] smartsheet_token missing")
        return {"error": "no_smartsheet_token"}
    open_entries = fetch_open_sheets(ss_token, config)
    if not open_entries:
        log.error("[litigation] no open sheets loaded — check "
                  "litigation_open_sheets")
        return {"error": "no_open_sheets"}
    state = load_state(paths)

    # The narrative columns are also reviewed against their voice guides
    # (rewording only — the prompt forbids changing facts).
    summary_col = (config.get("litigation_summary_column")
                   or "Summary of Claim")
    disclosure_col = (config.get("litigation_disclosure_column")
                      or "Third Party Disclosure Summary")
    svoice = load_voice(paths, "summary")
    dvoice = load_voice(paths, "disclosure")
    voice_block = (
        (f"Voice guide for the {summary_col!r} column:\n{svoice}\n\n"
         if svoice else "")
        + (f"Voice guide for the {disclosure_col!r} column:\n{dvoice}\n\n"
           if dvoice else ""))

    fixes: list[dict] = []
    missing: list[dict] = []
    conventions_parts: list[str] = []
    total_rows = 0
    # Voice review invites full rewrites of two narrative columns, so the
    # responses are much longer — smaller chunks keep the JSON inside the
    # output cap.
    chunk_size = 8 if voice_block else 20
    for entry in open_entries:
        sheet = entry["sheet"]
        rows = sheet.get("rows", [])
        total_rows += len(rows)

        # Pass 1 — derive each sheet's own conventions from a sample.
        sample = [row_to_dict(sheet, r) for r in rows[:30]]
        conventions = _claude_text(
            client,
            _preamble(config, paths)
            + f"Here are entries from the {entry['key']} open claims sheet:\n"
              f"{json.dumps(sample, indent=1)[:12000]}\n\n"
              "Write a short CONVENTIONS memo: for each column, the format the "
              "sheet evidently uses (date formats, naming patterns, "
              "abbreviations, status vocabulary). Note where the sheet is "
              "internally inconsistent. Markdown, under 400 words.",
            max_tokens=1200)
        if conventions:
            conventions_parts.append(f"# Conventions — {entry['key']} sheet\n\n"
                                     + conventions)

        # Pass 2 — per-chunk compliance review.
        for i in range(0, len(rows), chunk_size):
            if limit is not None and len(fixes) >= limit:
                break
            chunk = rows[i:i + chunk_size]
            payload = [{"row_id": r["id"], **row_to_dict(sheet, r)}
                       for r in chunk]
            result = _claude_json(
                client,
                _preamble(config, paths)
                + f"Sheet conventions:\n{conventions or '(none derived)'}\n\n"
                + voice_block
                + f"Review these entries for compliance with the conventions"
                + (f" and, for the {summary_col!r} and {disclosure_col!r} "
                   f"columns, with their voice guides" if voice_block else "")
                + f":\n{json.dumps(payload, indent=1)[:14000]}\n\n"
                  "Return ONLY a JSON object:\n"
                  '  "fixes": [{"row_id": <int>, "claim": "<claim name>", '
                  '"column": "<title>", "current": "...", "proposed": "...", '
                  '"reason": "..."}] — clear formatting/consistency fixes, '
                  "plus voice-guide rewrites of the narrative columns where "
                  "the current text strays from its guide (rewording ONLY — "
                  "keep every fact, date, and figure exactly; never invent "
                  "facts)\n"
                  '  "missing": [{"row_id": <int>, "claim": "...", '
                  '"what": "<information the entry is missing>"}]',
                max_tokens=8000)
            if isinstance(result, dict):
                fixes.extend({**f, "sheet_key": entry["key"]}
                             for f in (result.get("fixes") or [])
                             if isinstance(f, dict))
                missing.extend({**m, "sheet_key": entry["key"]}
                               for m in (result.get("missing") or [])
                               if isinstance(m, dict))
    if limit is not None:
        fixes = fixes[:limit]

    if conventions_parts:
        paths["conventions"].write_text("\n\n".join(conventions_parts),
                                        encoding="utf-8")

    # Queue fixes as one-at-a-time chat asks. Dedup on (row, column,
    # proposed value) against EVERY prior cleanup ask — a re-run must not
    # re-propose a fix James already declined (or double-queue a pending
    # one); a DIFFERENT proposed value for the same cell is still allowed.
    def _fix_key(f: dict):
        return (f.get("row_id"), (f.get("column") or "").strip().lower(),
                str(f.get("proposed") or "").strip())

    seen_fixes = {_fix_key(a) for a in (state.get("asks") or [])
                  if a.get("kind") == "cleanup_fix"}
    queued = 0
    skipped_dup = 0
    for f in fixes:
        if _fix_key(f) in seen_fixes:
            skipped_dup += 1
            continue
        seen_fixes.add(_fix_key(f))
        queue_ask(paths, state, {"kind": "cleanup_fix",
                                 "row_id": f.get("row_id"),
                                 "claim": f.get("claim"),
                                 "sheet_key": f.get("sheet_key"),
                                 "column": f.get("column"),
                                 "current": f.get("current"),
                                 "proposed": f.get("proposed"),
                                 "reason": f.get("reason")})
        queued += 1
    if skipped_dup:
        log.info(f"[litigation] cleanup: {skipped_dup} fix(es) skipped — "
                 f"already proposed/declined previously")
    save_state(paths, state)

    # Missing-information list.
    if missing:
        lines = [f"# Missing information — {datetime.now():%Y-%m-%d}", ""]
        lines += [f"- **{m.get('claim') or m.get('row_id')}** "
                  f"({m.get('sheet_key')}) — {m.get('what')}"
                  for m in missing]
        paths["missing"].write_text("\n".join(lines) + "\n", encoding="utf-8")

    log_event(paths, "cleanup_reviewed", rows=total_rows, fixes_queued=queued,
              missing=len(missing), dry_run=dry_run)
    log.info(f"[litigation] cleanup: {total_rows} rows reviewed across "
             f"{len(open_entries)} sheet(s), {queued} fix(es) queued for chat "
             f"approval, {len(missing)} missing-info item(s) -> "
             f"{paths['missing'].name}")
    return {"rows": total_rows, "fixes_queued": queued, "missing": len(missing)}


# =============================================================================
# Audit report
# =============================================================================

def run_report(config: dict, paths: dict, entity_key: str,
               dry_run: bool) -> str:
    """Generate the entity report through the Jinja template; save it, email
    it from rocky@ to James. Returns a one-line ack for Teams/CLI."""
    ss_token = (config.get("smartsheet_token") or "").strip()
    if not ss_token:
        return "I can't reach Smartsheet (no smartsheet_token configured)."
    spec = _entity_spec(config)
    entity = spec.get(entity_key.upper())
    if entity is None:
        return (f"I don't know the entity {entity_key!r} — I track "
                f"{', '.join(spec)}.")

    try:
        open_entries = fetch_open_sheets(ss_token, config)
        closed_sheet = get_sheet(ss_token, config["litigation_closed_sheet_id"])
    except (SmartsheetError, KeyError) as e:
        log.error(f"[litigation] report: {e}")
        return f"Smartsheet error while building the {entity_key} report."
    if not open_entries:
        return ("None of the open claims sheets would load — check "
                "litigation_open_sheets and the token.")

    # Filter across ALL open sheets (a claim mis-filed on the "wrong"
    # sheet still shows up); column order is the union across the sheets
    # that contributed rows, first-seen wins.
    open_rows: list[dict] = []
    columns: list[str] = []
    for e in open_entries:
        rows = rows_for_entity(config, e["sheet"], entity)
        if rows:
            open_rows.extend(rows)
            for t in sheet_column_titles(e["sheet"]):
                if t and t not in columns:
                    columns.append(t)
    if not columns:
        columns = [t for t in sheet_column_titles(open_entries[0]["sheet"]) if t]
    closed_rows = rows_for_entity(config, closed_sheet, entity)

    template_path = Path((config.get("litigation_report_template") or "").strip()
                         or paths["template"])
    try:
        from jinja2 import Template
        template = Template(template_path.read_text(encoding="utf-8"))
    except Exception as e:
        log.error(f"[litigation] report template failed to load: {e}")
        return f"The report template at {template_path} failed to load: {e}"

    now = datetime.now()
    html = template.render(
        entity_key=entity_key.upper(), entity_name=entity["name"],
        generated=now.strftime("%B %d, %Y"),
        columns=columns,
        closed_columns=sheet_column_titles(closed_sheet),
        open_rows=open_rows, closed_rows=closed_rows,
    )
    safe_key = re.sub(r"[^\w&-]", "", entity_key.upper())
    out_path = paths["reports"] / (
        f"Litigation Report - {safe_key} - {now:%Y-%m-%d %H%M}.html")
    if not dry_run:
        out_path.write_text(html, encoding="utf-8")

    emailed = False
    if not dry_run:
        try:
            import outbound
            from rocky import get_msal_app, acquire_token  # lazy
            token = acquire_token(get_msal_app(config))
            result = outbound.send_mail_guarded(
                token=token,
                sender_mailbox=config.get("rocky_email",
                                          "rocky@gallagherllp.com"),
                to=[config.get("user_email", "jbragdon@gallagherllp.com")],
                subject=f"Rocky — Litigation Report — {entity['name']} "
                        f"({now:%m/%d/%Y})",
                body=html, body_type="HTML",
                attachments=[{"name": out_path.name, "path": str(out_path)}],
            )
            emailed = bool(result.get("sent"))
        except Exception as e:
            log.warning(f"[litigation] report email failed: {e}")

    log_event(paths, "report_generated", entity=entity_key.upper(),
              open_rows=len(open_rows), closed_rows=len(closed_rows),
              path=str(out_path), emailed=emailed, dry_run=dry_run)
    return (f"{'DRY RUN — would generate' if dry_run else 'Generated'} the "
            f"{entity['name']} litigation report: {len(open_rows)} open, "
            f"{len(closed_rows)} closed claim(s). "
            + ("Emailed to you and saved under Reports\\."
               if emailed else f"Saved to {out_path.name} under Reports\\."))


# =============================================================================
# Daily digest (drafted into James's Drafts)
# =============================================================================

_DIGEST_EVENTS = {"claim_added", "claim_updated", "claim_closed",
                  "cleanup_fix_applied", "report_generated", "doc_sent"}


def run_digest(config: dict, paths: dict, date_str: str, dry_run: bool) -> dict:
    events = [e for e in _read_jsonl(paths["activity"])
              if (e.get("ts") or "").startswith(date_str)
              and e.get("event") in _DIGEST_EVENTS
              and not e.get("dry_run")]
    state = load_state(paths)
    brain_notes = state.get("brain_additions_pending") or []
    if not events and not brain_notes:
        log.info(f"[litigation] digest: no claim activity on {date_str} — "
                 f"nothing drafted")
        return {"drafted": False, "reason": "no_activity"}

    from anthropic import Anthropic  # lazy
    client = Anthropic(api_key=config["anthropic_api_key"])
    facts = json.dumps(events, indent=1)[:12000]
    body_md = _claude_text(
        client,
        _preamble(config, paths)
        + f"Write the daily Bozzuto litigation digest for {date_str} from "
          f"this activity log. Plain English, grouped by claim, brief — "
          f"what was added, updated, or closed, and any reports or "
          f"documents sent. Markdown.\n\n{facts}"
        + (("\n\nAlso include a short 'What I learned this week' section "
            "covering these new standing rules:\n- " + "\n- ".join(brain_notes))
           if brain_notes else ""),
        max_tokens=1500)
    if body_md is None:
        # Fallback: a bare list beats a lost day.
        body_md = "\n".join(
            f"- {e.get('event')}: {e.get('claim') or e.get('entity') or ''}"
            for e in events)

    html = ("<html><body style='font-family:Calibri,Arial,sans-serif'>"
            + "".join(f"<p>{line}</p>" for line in body_md.splitlines() if line.strip())
            + "</body></html>")
    subject = f"Rocky — Litigation activity {date_str}"

    if dry_run:
        log.info(f"[litigation] DRY RUN digest for {date_str}: "
                 f"{len(events)} event(s)")
        return {"drafted": False, "reason": "dry_run", "events": len(events)}

    import pending_llt
    from rocky import get_msal_app, acquire_token  # lazy
    token = acquire_token(get_msal_app(config))
    mailbox = config.get("user_email", "jbragdon@gallagherllp.com")
    result = pending_llt.create_draft_email(
        token=token, user_email=mailbox,
        to_addresses=[mailbox], subject=subject, html_body=html)
    if result.get("created"):
        if brain_notes:
            state["brain_additions_pending"] = []
            save_state(paths, state)
        log_event(paths, "digest_drafted", date=date_str, events=len(events))
        log.info(f"[litigation] digest drafted into {mailbox}'s Drafts "
                 f"({len(events)} event(s))")
        return {"drafted": True, "events": len(events)}
    log.warning(f"[litigation] digest draft failed: {result.get('reason')}")
    return {"drafted": False, "reason": result.get("reason")}


# =============================================================================
# Weekly learn (chat -> brain)
# =============================================================================

def run_learn(config: dict, paths: dict, days: int) -> dict:
    from anthropic import Anthropic  # lazy
    client = Anthropic(api_key=config["anthropic_api_key"])
    state = load_state(paths)
    cursor = state.get("learn_cursor")
    since = cursor or (datetime.now(timezone.utc)
                       - timedelta(days=days)).isoformat()
    comms = [c for c in _read_jsonl(paths["comms"]) if (c.get("ts") or "") > since]
    if not comms:
        log.info("[litigation] learn: no new chat traffic — brain unchanged")
        return {"additions": 0}

    transcript = "\n".join(
        f"[{(c.get('ts') or '')[:16]}] "
        f"{'ROCKY' if c.get('direction') == 'out' else 'JAMES'}: {c.get('text')}"
        for c in comms)[:20000]
    result = _claude_json(
        client,
        _preamble(config, paths)
        + "Here is the Litigation Updates Teams conversation since the last "
          "learning pass. Extract DURABLE standing instructions — things "
          "James taught, corrected, or decided that should change how you "
          "process future notices, entries, closures, and reports (e.g. "
          "'do not create an entry for a notice of garnishment of wages'). "
          "Skip one-off decisions that don't generalize. Do not repeat "
          "rules already in the standing instructions above.\n\n"
          f"{transcript}\n\n"
          "Return ONLY a JSON object: {\"additions\": [{\"rule\": "
          "\"<imperative instruction>\", \"why\": \"<one sentence, quoting "
          "James where helpful>\"}]} — an empty list if nothing durable.",
        max_tokens=1500)
    additions = (result or {}).get("additions") or []
    additions = [a for a in additions
                 if isinstance(a, dict) and (a.get("rule") or "").strip()]

    if additions:
        stamp = datetime.now().strftime("%Y-%m-%d")
        with open(paths["brain"], "a", encoding="utf-8") as f:
            for a in additions:
                f.write(f"- {a['rule'].strip()}"
                        + (f" — {a['why'].strip()}" if a.get("why") else "")
                        + f" [learned {stamp}]\n")
        notes = [a["rule"].strip() for a in additions]
        state["brain_additions_pending"] = \
            (state.get("brain_additions_pending") or []) + notes
        log_event(paths, "brain_updated", additions=notes)
        log.info(f"[litigation] learn: {len(additions)} rule(s) added to the "
                 f"brain — they'll appear in the next digest")
    else:
        log.info("[litigation] learn: nothing durable in this week's chat")

    state["learn_cursor"] = _now_iso()
    save_state(paths, state)
    return {"additions": len(additions)}


# =============================================================================
# Poll (intake + chat), status, CLI
# =============================================================================

def run_poll(config: dict, paths: dict, backfill_days: int,
             dry_run: bool) -> dict:
    from anthropic import Anthropic  # lazy
    from rocky import acquire_app_token  # lazy

    client = Anthropic(api_key=config["anthropic_api_key"])
    ss_token = (config.get("smartsheet_token") or "").strip() or None
    state = load_state(paths)
    log_event(paths, "run_started", mode="poll", dry_run=dry_run)

    graph_token = acquire_app_token(config)
    counts = intake_pass(client, graph_token, ss_token, config, paths, state,
                         backfill_days, dry_run)
    save_state(paths, state)
    log.info(f"[litigation] intake: {counts}")

    chat = chat_cycle(config, paths, dry_run)
    log.info(f"[litigation] chat: {chat}")
    return {"intake": counts, "chat": chat}


def print_status(config: dict, paths: dict) -> None:
    state = load_state(paths)
    asks = state.get("asks") or []
    by_status: dict[str, int] = {}
    for a in asks:
        by_status[a.get("status") or "?"] = by_status.get(a.get("status") or "?", 0) + 1
    catalog = [e for e in _load_catalog(paths) if not e.get("dry_run")]

    print(f"Litigation Updater root: {paths['root']}")
    print(f"  smartsheet_token:        "
          f"{'set' if (config.get('smartsheet_token') or '').strip() else 'MISSING'}")
    specs = open_sheet_specs(config)
    if specs:
        for s in specs:
            sid = s["sheet_id"]
            configured = "MISSING" if str(sid).startswith("PASTE") else sid
            print(f"  open sheet [{s['key']}]:    {configured}  "
                  f"(entities: {', '.join(s['entities']) or 'any'})")
    else:
        print("  open sheets:             MISSING (litigation_open_sheets)")
    print(f"  closed sheet id:         "
          f"{config.get('litigation_closed_sheet_id') or 'MISSING'}")
    print(f"  mail cursor:             {state.get('mail_cursor') or '(backfills on first run)'}")
    print(f"  Teams chat:              "
          f"{'created' if state.get('chat_id') else 'not yet created'}")
    print(f"  asks:                    {by_status or 'none'}")
    print(f"  vault documents:         {len(catalog)}")
    print(f"  brain:                   {paths['brain']}")
    print(f"  learn cursor:            {state.get('learn_cursor') or '(never run)'}")
    for v in VOICE_NAMES:
        built = (paths["voices"] / f"voice_{v}.md").exists()
        seeds = len([f for f in (paths["voices"] / "seeds" / v).glob("*")
                     if f.is_file()]) if (paths["voices"] / "seeds" / v).exists() else 0
        print(f"  voice_{v + ':':<18}{'built' if built else 'not built'}"
              + (f"  ({seeds} seed doc(s))" if seeds else ""))


def _argv_value(flag: str) -> str | None:
    if flag in sys.argv:
        idx = sys.argv.index(flag)
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    return None


def run_cli(config: dict, data_dir: Path) -> None:
    """Entry point, called from rocky.py's dispatch for --litigation
    (and the dashboard aliases --litigation-digest / --litigation-learn)."""
    paths = get_paths(config, data_dir)

    # Dashboard aliases -> canonical subflags.
    if "--litigation-digest" in sys.argv:
        sys.argv.append("--digest")
    if "--litigation-learn" in sys.argv:
        sys.argv.append("--learn")

    if "--status" in sys.argv:
        print_status(config, paths)
        return

    ensure_dirs(paths)
    dry_run = "--dry-run" in sys.argv

    try:
        _dispatch(config, paths, dry_run)
    except SmartsheetError as e:
        # A clean exit beats a PyInstaller traceback: bad tokens/ids are
        # config problems, not crashes.
        log.error(f"[litigation] {e}")
        print(f"\nSmartsheet error: {e}\n"
              f"Check smartsheet_token and the sheet ids in config.json "
              f"(see LITIGATION_UPDATER.md > Troubleshooting).")
        sys.exit(1)


def _dispatch(config: dict, paths: dict, dry_run: bool) -> None:

    if "--report" in sys.argv:
        entity = _argv_value("--report")
        if not entity:
            print("Usage: rocky.py --litigation --report <BMC|B&A|BHI|BCC>")
            sys.exit(1)
        print(run_report(config, paths, entity, dry_run))
        return

    if "--digest" in sys.argv:
        date_str = _argv_value("--date") or datetime.now().strftime("%Y-%m-%d")
        run_digest(config, paths, date_str, dry_run)
        return

    if "--cleanup" in sys.argv:
        limit_raw = _argv_value("--limit")
        run_cleanup(config, paths, int(limit_raw) if limit_raw else None,
                    dry_run)
        return

    if "--learn" in sys.argv:
        days = int(_argv_value("--days") or 7)
        run_learn(config, paths, days)
        return

    if "--voice-rebuild" in sys.argv:
        rebuild_voices(config, paths)
        return

    if "--strip-formatting" in sys.argv:
        strip_formatting(config, paths, apply="--apply" in sys.argv)
        return

    if "--chat" in sys.argv:
        if "--follow" in sys.argv:
            follow_chat(config, paths, dry_run,
                        int(_argv_value("--minutes") or 30))
        else:
            chat_cycle(config, paths, dry_run)
        return

    # Default (and --poll): the full cycle.
    backfill_days = int(_argv_value("--backfill-days")
                        or config.get("litigation_backfill_days") or 7)
    run_poll(config, paths, backfill_days, dry_run)
