"""
Pending LLT Matters — SharePoint-to-Drafts pipeline.

Downloads the PENDING LLT MATTERS spreadsheet and BMC Contacts spreadsheet
from SharePoint (MultifamilyHousing site), groups pending matters by property,
matches each property to its contacts, and creates a draft email per property
in James's Drafts folder.

Usage:
    rocky.exe --pending-llt [--dry-run]

    --dry-run   Show what would be drafted without creating any emails.
"""

import io
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import openpyxl
import requests
from jinja2 import Template

log = logging.getLogger("rocky")

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

# SharePoint file paths (relative to the document library root).
LLT_FILE_PATH = "General/PENDING LLT MATTERS.XLSX"
CONTACTS_FILE_PATH = "General/BMC Contacts (1).xlsx"

# Default template path — overridden by config["templates_path"] if present.
_DEFAULT_TEMPLATES = (
    r"C:\Users\jbragdon\OneDrive\OneDrive - gejlaw.com"
    r"\Program Files\Rocky\Rocky reference files\templates"
)


# ============================================================================
# SharePoint file download via Graph API
# ============================================================================

def resolve_sharepoint_site_id(token: str, hostname: str, site_path: str) -> str | None:
    """Resolve a SharePoint site to its Graph API site ID.

    hostname: e.g. "gejlaw.sharepoint.com"
    site_path: e.g. "/sites/MultifamilyHousing"
    """
    url = f"{GRAPH_API_BASE}/sites/{hostname}:{site_path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code != 200:
        log.error(f"Failed to resolve SharePoint site {hostname}{site_path}: "
                  f"HTTP {resp.status_code}: {resp.text[:300]}")
        return None
    return resp.json().get("id")


def resolve_drive_id(token: str, site_id: str) -> str | None:
    """Get the default document library drive ID for a SharePoint site."""
    url = f"{GRAPH_API_BASE}/sites/{site_id}/drive"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code != 200:
        log.error(f"Failed to get drive for site {site_id}: "
                  f"HTTP {resp.status_code}: {resp.text[:300]}")
        return None
    return resp.json().get("id")


def download_sharepoint_file(token: str, drive_id: str, file_path: str) -> bytes | None:
    """Download a file from a SharePoint document library by path.

    file_path: path relative to the drive root, e.g. "General/PENDING LLT MATTERS.XLSX"
    Returns the raw file bytes, or None on failure.
    """
    encoded_path = file_path.replace(" ", "%20")
    url = f"{GRAPH_API_BASE}/drives/{drive_id}/root:/{encoded_path}:/content"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers, timeout=60, allow_redirects=True)
    if resp.status_code != 200:
        log.error(f"Failed to download {file_path}: HTTP {resp.status_code}: {resp.text[:300]}")
        return None
    log.info(f"Downloaded {file_path} ({len(resp.content):,} bytes)")
    return resp.content


# ============================================================================
# Spreadsheet parsing
# ============================================================================

def parse_llt_spreadsheet(file_bytes: bytes) -> list[dict]:
    """Parse the PENDING LLT MATTERS spreadsheet into a list of matter dicts.

    Each dict has: name, property, unit, status, ripe_date, next_steps,
    subsidized, vawa_notice, vawa_complies, client_matter, property_group.
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(min_row=1, values_only=True))
    wb.close()
    if not rows:
        return []

    # Find the header row (contains "Name" and "Property").
    header_idx = None
    for i, row in enumerate(rows):
        cells = [str(c).strip().lower() if c else "" for c in row]
        if "name" in cells and any("property" in c for c in cells):
            header_idx = i
            break

    if header_idx is None:
        # Assume row 0 is headers (matches the downloaded sample).
        header_idx = 0

    headers_raw = rows[header_idx]
    headers = [str(h).strip() if h else f"col_{i}" for i, h in enumerate(headers_raw)]

    # Build column index map (case-insensitive, strip whitespace).
    col_map = {}
    for i, h in enumerate(headers):
        col_map[h.lower().rstrip()] = i

    def get(row, key):
        idx = col_map.get(key.lower().rstrip())
        if idx is None or idx >= len(row):
            return None
        v = row[idx]
        if v is None:
            return None
        return str(v).strip() if not isinstance(v, datetime) else v

    matters = []
    current_group = None

    for row in rows[header_idx + 1:]:
        if not row or all(c is None for c in row):
            continue

        name = get(row, "Name")
        if not name:
            continue

        # Column 0 holds property group headers (the long-form property name).
        col0 = row[0]
        if col0 is not None and str(col0).strip():
            current_group = str(col0).strip()

        prop = get(row, "Property") or get(row, "Property ")
        if not prop:
            prop = current_group or "Unknown"

        ripe_raw = get(row, "Ripe Date")
        if isinstance(ripe_raw, datetime):
            ripe_date = f"{ripe_raw.month}/{ripe_raw.day}/{ripe_raw.year}"
            ripe_dt = ripe_raw
        elif ripe_raw:
            ripe_date = str(ripe_raw)
            ripe_dt = None
        else:
            ripe_date = ""
            ripe_dt = None

        matters.append({
            "name": name,
            "property": str(prop).strip(),
            "unit": get(row, "Unit") or "",
            "status": get(row, "Status") or "",
            "ripe_date": ripe_date,
            "ripe_dt": ripe_dt,
            "next_steps": get(row, "Next Steps") or "",
            "subsidized": get(row, "Subsidized.Non-subsidized") or "",
            "vawa_notice": get(row, "Notice Contains VAWA") or "",
            "vawa_complies": get(row, "Complies w.VAWA") or "",
            "client_matter": get(row, "Client.Matter") or "",
            "property_group": current_group or "",
        })

    return matters


def parse_contacts_spreadsheet(file_bytes: bytes) -> dict[str, dict]:
    """Parse the BMC Contacts spreadsheet into a property→contact mapping.

    Returns a dict keyed by normalized property name:
    {
        "blackbird": {
            "property_name": "Blackbird",
            "llt_name": "Blackbird",   # from LLT Name column, if present
            "contacts": [{"name": "...", "email": "...", "role": "..."}],
            "state": "Maryland",
            "notes": "...",
        }
    }

    If an "LLT Name" column exists, it's used as the primary match key.
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(min_row=1, values_only=True))
    wb.close()
    if not rows:
        return {}

    # Find header row.
    header_idx = 0
    for i, row in enumerate(rows):
        cells = [str(c).strip().lower() if c else "" for c in row]
        if any("property" in c for c in cells):
            header_idx = i
            break

    headers = [str(h).strip() if h else f"col_{i}" for i, h in enumerate(rows[header_idx])]
    col_map = {h.lower().rstrip(): i for i, h in enumerate(headers)}

    def get(row, key):
        idx = col_map.get(key.lower().rstrip())
        if idx is None or idx >= len(row):
            return None
        v = row[idx]
        return str(v).strip() if v is not None else None

    has_llt_col = "llt name" in col_map

    contacts_map = {}
    current_state = None

    for row in rows[header_idx + 1:]:
        if not row or all(c is None for c in row):
            continue

        # State column marks state section headers.
        state_val = get(row, "State")
        if state_val:
            current_state = state_val

        prop_name = get(row, "Property Name") or get(row, "Property Name ")
        if not prop_name:
            continue

        contact_raw = get(row, "Contact Info & Position") or get(row, "Contact Info & Position ")
        contacts = _parse_contact_field(contact_raw) if contact_raw else []

        llt_name = get(row, "LLT Name") if has_llt_col else None
        notes = get(row, "Unnamed: 3") or ""

        entry = {
            "property_name": prop_name,
            "llt_name": llt_name,
            "contacts": contacts,
            "state": current_state or "",
            "notes": notes,
        }

        # Key by normalized property name.
        norm_key = _normalize_property(prop_name)
        contacts_map[norm_key] = entry

        # Also key by LLT Name if present and different.
        if llt_name:
            llt_key = _normalize_property(llt_name)
            if llt_key != norm_key:
                contacts_map[llt_key] = entry

    return contacts_map


_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def _parse_contact_field(raw: str) -> list[dict]:
    """Parse a contact field like 'Chris Seegren <Chris.Seegren@bozzuto.com> - GM'.

    Handles multiple contacts separated by newlines.
    """
    contacts = []
    for line in raw.replace("\\n", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue

        email_match = _EMAIL_RE.search(line)
        if not email_match:
            continue

        email = email_match.group(0)

        # Name is everything before the email or '<'.
        before_email = line[:line.index(email)].rstrip(" <")
        name = before_email.strip()

        # Role is everything after the email/'>'.
        after_email = line[line.index(email) + len(email):].lstrip("> ")
        role = after_email.lstrip("- ").strip()

        contacts.append({"name": name, "email": email, "role": role})

    return contacts


def _normalize_property(name: str) -> str:
    """Normalize a property name for fuzzy matching.

    Strips whitespace, lowercases, removes 'the ', standardizes punctuation.
    """
    s = name.strip().lower()
    s = re.sub(r"^the\s+", "", s)
    s = s.replace("&", "and")
    s = s.replace(".", "")
    s = s.replace(",", "")
    s = s.replace("'", "")
    s = s.replace("'", "")
    s = re.sub(r"\s+", " ", s)
    return s


# ============================================================================
# Property matching
# ============================================================================

def match_property_to_contacts(
    property_name: str,
    contacts_map: dict[str, dict],
) -> dict | None:
    """Try to match an LLT property name to a contacts entry.

    Match strategy (in order):
    1. Exact normalized match
    2. One name contains the other (for abbreviation cases)
    """
    norm = _normalize_property(property_name)

    # Exact normalized match.
    if norm in contacts_map:
        return contacts_map[norm]

    # Containment match — only if unambiguous.
    candidates = []
    for key, entry in contacts_map.items():
        if norm in key or key in norm:
            candidates.append(entry)

    if len(candidates) == 1:
        return candidates[0]

    return None


# ============================================================================
# Draft creation via Graph API
# ============================================================================

def create_draft_email(
    token: str,
    user_email: str,
    to_addresses: list[str],
    subject: str,
    html_body: str,
) -> dict:
    """Create a draft email in the user's Drafts folder.

    Uses POST /users/{email}/messages which creates a message in Drafts
    (NOT sendMail — no mail is sent).

    Returns {"created": True, "message_id": ...} or {"created": False, "reason": ...}.
    """
    url = f"{GRAPH_API_BASE}/users/{user_email}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html_body},
        "toRecipients": [{"emailAddress": {"address": addr}} for addr in to_addresses],
        "isDraft": True,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
    except requests.RequestException as e:
        log.error(f"Draft creation network error: {e}")
        return {"created": False, "reason": f"network_error: {e}"}

    if resp.status_code == 201:
        msg_id = resp.json().get("id", "?")
        log.info(f"Draft created: {subject[:60]!r} -> {to_addresses}")
        return {"created": True, "message_id": msg_id}

    log.error(f"Draft creation failed HTTP {resp.status_code}: {resp.text[:300]}")
    return {"created": False, "reason": f"graph_error_{resp.status_code}",
            "response_body": resp.text[:300]}


# ============================================================================
# Template rendering
# ============================================================================

def load_email_template(config: dict) -> Template:
    """Load the Jinja2 email template from the templates folder."""
    templates_dir = Path(config.get("templates_path", _DEFAULT_TEMPLATES))
    template_path = templates_dir / "pending_llt_email.html"

    if not template_path.exists():
        log.warning(f"Email template not found at {template_path}; using built-in default.")
        return Template(_FALLBACK_TEMPLATE)

    html = template_path.read_text(encoding="utf-8")
    log.info(f"Loaded email template from {template_path}")
    return Template(html)


_FALLBACK_TEMPLATE = """\
<p>Pending LLT matters for <strong>{{ property_name }}</strong> ({{ date }}):</p>
<table border="1" cellpadding="4" cellspacing="0">
<tr><th>Tenant</th><th>Unit</th><th>Status</th><th>Ripe Date</th><th>Next Steps</th></tr>
{% for m in matters %}
<tr><td>{{ m.name }}</td><td>{{ m.unit }}</td><td>{{ m.status }}</td><td>{{ m.ripe_date }}</td><td>{{ m.next_steps }}</td></tr>
{% endfor %}
</table>
<p>Total: {{ matters | length }}</p>
"""


def render_property_email(
    template: Template,
    property_name: str,
    matters: list[dict],
    today: str,
) -> str:
    """Render the email body for a single property."""
    now = datetime.now(timezone.utc)
    ripe_count = 0
    for m in matters:
        rd = m.get("ripe_dt")
        if rd and hasattr(rd, "date"):
            if rd.date() <= now.date():
                ripe_count += 1

    return template.render(
        property_name=property_name,
        date=today,
        matters=matters,
        ripe_matters=ripe_count if ripe_count > 0 else None,
    )


# ============================================================================
# Main pipeline
# ============================================================================

def run_pending_llt(token: str, config: dict, dry_run: bool = False) -> dict:
    """Full pipeline: download spreadsheets, match, draft emails.

    Returns a summary dict with counts and any unmatched properties.
    """
    user_email = config.get("user_email", "jbragdon@gallagherllp.com")

    # --- SharePoint config ---
    sp_hostname = config.get("sharepoint_hostname", "gejlaw.sharepoint.com")
    sp_site_path = config.get("sharepoint_site_path", "/sites/MultifamilyHousing")
    llt_file = config.get("llt_file_path", LLT_FILE_PATH)
    contacts_file = config.get("contacts_file_path", CONTACTS_FILE_PATH)

    # Step 1: Resolve SharePoint site and drive.
    log.info(f"Resolving SharePoint site: {sp_hostname}{sp_site_path}")
    site_id = resolve_sharepoint_site_id(token, sp_hostname, sp_site_path)
    if not site_id:
        return {"error": "Failed to resolve SharePoint site"}

    drive_id = resolve_drive_id(token, site_id)
    if not drive_id:
        return {"error": "Failed to resolve SharePoint drive"}

    # Step 2: Download both spreadsheets.
    log.info("Downloading PENDING LLT MATTERS spreadsheet...")
    llt_bytes = download_sharepoint_file(token, drive_id, llt_file)
    if not llt_bytes:
        return {"error": f"Failed to download {llt_file}"}

    log.info("Downloading BMC Contacts spreadsheet...")
    contacts_bytes = download_sharepoint_file(token, drive_id, contacts_file)
    if not contacts_bytes:
        return {"error": f"Failed to download {contacts_file}"}

    # Step 3: Parse spreadsheets.
    matters = parse_llt_spreadsheet(llt_bytes)
    contacts_map = parse_contacts_spreadsheet(contacts_bytes)
    log.info(f"Parsed {len(matters)} matters, {len(contacts_map)} contact entries")

    if not matters:
        return {"error": "No matters found in spreadsheet"}

    # Step 4: Group matters by property.
    by_property: dict[str, list[dict]] = {}
    for m in matters:
        prop = m["property"]
        by_property.setdefault(prop, []).append(m)

    log.info(f"Grouped into {len(by_property)} properties")

    # Step 5: Load email template.
    template = load_email_template(config)
    today = datetime.now().strftime("%B %d, %Y")

    # Step 6: Match each property to contacts and create drafts.
    drafts_created = 0
    drafts_skipped = 0
    unmatched_properties = []
    results = []

    for prop_name, prop_matters in sorted(by_property.items()):
        contact_entry = match_property_to_contacts(prop_name, contacts_map)

        if not contact_entry or not contact_entry["contacts"]:
            unmatched_properties.append(prop_name)
            drafts_skipped += 1
            continue

        to_addresses = [c["email"] for c in contact_entry["contacts"] if c.get("email")]
        if not to_addresses:
            unmatched_properties.append(prop_name)
            drafts_skipped += 1
            continue

        # Use the contact sheet's property name for the email (it's the canonical name).
        display_name = contact_entry["property_name"]
        subject = f"Pending LLT Matters — {display_name}"

        html_body = render_property_email(template, display_name, prop_matters, today)

        if dry_run:
            contact_names = ", ".join(
                f"{c['name']} <{c['email']}>" for c in contact_entry["contacts"]
            )
            print(f"  [DRY RUN] {display_name}: {len(prop_matters)} matters -> {contact_names}")
            results.append({
                "property": display_name,
                "matters_count": len(prop_matters),
                "to": to_addresses,
                "status": "dry_run",
            })
            drafts_created += 1
        else:
            result = create_draft_email(token, user_email, to_addresses, subject, html_body)
            results.append({
                "property": display_name,
                "matters_count": len(prop_matters),
                "to": to_addresses,
                "status": "created" if result.get("created") else "failed",
                "detail": result,
            })
            if result.get("created"):
                drafts_created += 1
            else:
                drafts_skipped += 1

    summary = {
        "total_matters": len(matters),
        "total_properties": len(by_property),
        "drafts_created": drafts_created,
        "drafts_skipped": drafts_skipped,
        "unmatched_properties": unmatched_properties,
        "results": results,
    }

    # Print summary.
    print(f"\n{'=' * 60}")
    print(f"Pending LLT — {'DRY RUN' if dry_run else 'COMPLETE'}")
    print(f"{'=' * 60}")
    print(f"  Total matters:      {summary['total_matters']}")
    print(f"  Properties:         {summary['total_properties']}")
    print(f"  Drafts {'prepared' if dry_run else 'created'}:    {drafts_created}")
    print(f"  Skipped (no match): {drafts_skipped}")

    if unmatched_properties:
        print(f"\n  Unmatched properties ({len(unmatched_properties)}):")
        for p in sorted(unmatched_properties):
            print(f"    - {p}")
        print(f"\n  To fix: add an 'LLT Name' column in BMC Contacts with the")
        print(f"  exact property name from the LLT sheet for each unmatched property.")

    print()
    return summary
