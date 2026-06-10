"""
PMA manifest bootstrap.

Reads the HubSpot "Beth PMA Call View" ticket export and builds the deal
manifest (state/pma_manifest.json) that the PMA tracker matches incoming
pmateam@gallagherllp.com emails against.

Usage:
    python pma_bootstrap.py [path-to-export.xls|.xlsx]

If no path is given, the most recent hubspot-crm-exports-*.xls on the Desktop
is used. Legacy .xls is read via xlrd; .xlsx via openpyxl.

Idempotent: re-running merges by Ticket ID and PRESERVES any hand-tuned
"counterparty_keywords" and "hs_pipeline_stage_id" already in the manifest, so
keyword tuning during the observe week is never clobbered by a re-bootstrap.
The current status / summary / legal contact are always refreshed from the
export.
"""

import glob
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
MANIFEST_NAME = "pma_manifest.json"


def _manifest_dest() -> Path:
    """Where to write the manifest.

    Preferred: the PMA Team root on OneDrive (config['pma_team_root'] in
    config.json), so it syncs to the Rocky laptop automatically and Rocky reads
    it there regardless of where the .exe runs. Falls back to the source dir if
    config.json is absent or doesn't set pma_team_root. The manifest holds
    firm-confidential deal names + contacts, so it is git-ignored and delivered
    via OneDrive, never git."""
    cfg_path = ROOT / "config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            root = cfg.get("pma_team_root")
            if not root and cfg.get("cases_root"):
                root = str(Path(cfg["cases_root"]).parent / "PMA Team")
            if root:
                return Path(root) / MANIFEST_NAME
        except Exception as e:
            print(f"(could not read config.json for pma_team_root: {e})")
    return ROOT / MANIFEST_NAME


MANIFEST_PATH = _manifest_dest()

# Header name -> manifest field. Looked up by name so column reordering in
# future exports doesn't break the mapping.
COLUMN_MAP = {
    "Ticket ID": "ticket_id",
    "Ticket name": "deal_name",
    "Ticket status": "current_status",
    "Summary Status": "summary_status",
    "Legal Contact": "legal_contact",
    "Agreement Type BMC": "agreement_type",
    "Associated Deal": "associated_deal",
    "Associated Deal IDs": "hubspot_deal_id",
}

# Tokens that should never become a standalone counterparty keyword.
_KEYWORD_STOPWORDS = {
    "pma", "the", "and", "llc", "lp", "inc", "co", "company", "companies",
    "equities", "partners", "group", "management", "apartments", "apartment",
    "residential", "properties", "property", "associates", "holdings",
}


def _find_default_export() -> str | None:
    """Most recent hubspot-crm-exports-*.xls(x) on the Desktop, if any."""
    desktop = Path(os.path.expanduser("~")) / "Desktop"
    matches = sorted(
        glob.glob(str(desktop / "hubspot-crm-exports-*.xls*")),
        key=os.path.getmtime,
        reverse=True,
    )
    return matches[0] if matches else None


def _read_rows(path: str) -> list[list]:
    """Return all rows (incl. header) as lists of cell values."""
    p = path.lower()
    if p.endswith(".xlsx"):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        ws = wb.active
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        wb.close()
        return rows
    # Legacy .xls
    import xlrd
    wb = xlrd.open_workbook(path)
    ws = wb.sheet_by_index(0)
    return [ws.row_values(r) for r in range(ws.nrows)]


def _is_good_keyword(cleaned: str) -> bool:
    """Counterparty names are proper nouns (Title Case) and short. Reject
    sentence fragments produced by nested parentheses / descriptive text."""
    if not cleaned or len(cleaned) < 3 or len(cleaned) > 40:
        return False
    if cleaned.lower() in _KEYWORD_STOPWORDS:
        return False
    # First word should be capitalized or numeric (an address/proper noun),
    # not a lowercase fragment like "division of services...".
    first = cleaned.split()[0]
    return first[0].isupper() or first[0].isdigit()


def _seed_keywords(deal_name: str, associated_deal: str) -> list[str]:
    """
    Derive starter counterparty keywords from the ticket name.

    "2600 Biscayne (Related & Oak Row Equities) - PMA"
        -> ["2600 Biscayne", "Related", "Oak Row"]

    Conservative on purpose: gives the observe week some signal; James refines
    the lists by hand afterward.
    """
    name = str(deal_name or "").strip()
    keywords: list[str] = []

    # Primary phrase: text before the first "(" or " - ".
    primary = re.split(r"[(\-]", name, maxsplit=1)[0].strip()
    if _is_good_keyword(primary):
        keywords.append(primary)

    # Parenthetical parties: split on & , "and".
    paren = re.search(r"\(([^)]*)\)", name)
    if paren:
        for part in re.split(r"\s*(?:&|,|\band\b)\s*", paren.group(1)):
            part = part.strip()
            if not part:
                continue
            # Drop a trailing generic word ("Oak Row Equities" -> "Oak Row").
            tokens = part.split()
            while tokens and tokens[-1].lower() in _KEYWORD_STOPWORDS:
                tokens.pop()
            cleaned = " ".join(tokens).strip()
            if _is_good_keyword(cleaned):
                keywords.append(cleaned)

    # Associated deal name, if distinct.
    ad = str(associated_deal or "").strip()
    if _is_good_keyword(ad):
        keywords.append(ad)

    # Dedupe case-insensitively, preserve order.
    seen: set[str] = set()
    out: list[str] = []
    for kw in keywords:
        k = kw.lower()
        if k not in seen:
            seen.add(k)
            out.append(kw)
    return out


def build_manifest(export_path: str) -> list[dict]:
    rows = _read_rows(export_path)
    if not rows:
        raise SystemExit(f"No rows read from {export_path}")

    headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    print(f"Discovered {len(headers)} columns:")
    for i, h in enumerate(headers):
        mapped = COLUMN_MAP.get(h)
        print(f"  [{i}] {h!r}" + (f"  -> {mapped}" if mapped else ""))

    missing = [h for h in COLUMN_MAP if h not in headers]
    if missing:
        print(f"\nWARNING: expected columns not found: {missing}")

    idx = {h: i for i, h in enumerate(headers)}
    entries: list[dict] = []
    for row in rows[1:]:
        ticket_id = row[idx["Ticket ID"]] if "Ticket ID" in idx else None
        if ticket_id is None or str(ticket_id).strip() == "":
            continue
        entry: dict = {}
        for header, field in COLUMN_MAP.items():
            if header in idx and idx[header] < len(row):
                val = row[idx[header]]
                # xlrd returns floats for numeric cells; ids should be strings.
                if isinstance(val, float) and val.is_integer():
                    val = str(int(val))
                entry[field] = ("" if val is None else str(val)).strip()
            else:
                entry[field] = ""
        entry["counterparty_keywords"] = _seed_keywords(
            entry.get("deal_name", ""), entry.get("associated_deal", "")
        )
        entry["hs_pipeline_stage_id"] = ""  # filled once stage IDs are pulled
        entries.append(entry)
    return entries


def merge_preserving(new: list[dict], existing_path: Path) -> list[dict]:
    """Merge new entries with an existing manifest, preserving hand-tuned
    counterparty_keywords and hs_pipeline_stage_id."""
    if not existing_path.exists():
        return new
    try:
        old = json.loads(existing_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Could not read existing manifest ({e}); writing fresh.")
        return new
    old_by_id = {str(e.get("ticket_id")): e for e in old}
    preserved = 0
    for entry in new:
        prev = old_by_id.get(entry["ticket_id"])
        if not prev:
            continue
        # Preserve hand-tuned keyword lists (only if the operator changed them
        # from whatever the seeder produced last time).
        if prev.get("counterparty_keywords"):
            entry["counterparty_keywords"] = prev["counterparty_keywords"]
        if prev.get("hs_pipeline_stage_id"):
            entry["hs_pipeline_stage_id"] = prev["hs_pipeline_stage_id"]
        preserved += 1
    if preserved:
        print(f"Merged: preserved keywords/stage IDs for {preserved} existing ticket(s).")
    return new


def main() -> None:
    export_path = sys.argv[1] if len(sys.argv) > 1 else _find_default_export()
    if not export_path or not Path(export_path).exists():
        raise SystemExit(
            "No export file found. Pass the path explicitly:\n"
            "  python pma_bootstrap.py <export.xls>"
        )
    print(f"Reading export: {export_path}\n")

    entries = build_manifest(export_path)
    entries = merge_preserving(entries, MANIFEST_PATH)

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(entries, indent=2), encoding="utf-8")

    print(f"\nWrote {len(entries)} tickets -> {MANIFEST_PATH}")
    # Spot-check sample.
    if entries:
        s = entries[0]
        print("\nSample entry:")
        print(f"  ticket_id:    {s['ticket_id']}")
        print(f"  deal_name:    {s['deal_name']}")
        print(f"  status:       {s['current_status']}")
        print(f"  legal:        {s['legal_contact']}")
        print(f"  keywords:     {s['counterparty_keywords']}")
    no_kw = [e["ticket_id"] for e in entries if not e["counterparty_keywords"]]
    if no_kw:
        print(f"\n{len(no_kw)} ticket(s) got NO seeded keywords — add some by hand: {no_kw[:10]}")


if __name__ == "__main__":
    main()
