"""
Pending LLT Matters — SharePoint-to-Drafts pipeline.

Downloads the PENDING LLT MATTERS spreadsheet and BMC Contacts spreadsheet
from SharePoint (MultifamilyHousing site), groups pending matters by property,
matches each property to its contacts, and creates a draft email per property
in James's Drafts folder.

Two modes:

    rocky.exe --pending-llt [--dry-run] [--limit N]
        Full status email per property — every pending matter, segmented into
        rent / breach / recert / already-in-court sections.

    rocky.exe --pending-llt --ripe [--as-of M/D/YY] [--dry-run] [--limit N]
        "Ripe check-in" email per property — ONLY matters whose Ripe Date has
        already passed and which have not been filed yet. Asks the property
        manager whether each breach has resolved (and for an updated ledger on
        the money cases). This is the follow-up sweep, not a status report.

    --dry-run   Show what would be drafted without creating any emails.
    --limit N   Only process the first N matched properties (useful for testing).
    --local     Read the spreadsheets from disk instead of SharePoint (see
                LOCAL_LLT_PATH / LOCAL_CONTACTS_PATH).
"""

import io
import logging
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import openpyxl
import requests
from jinja2 import Template

log = logging.getLogger("rocky")

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

# SharePoint file paths (relative to the document library root).
LLT_FILE_PATH = "General/PENDING LLT MATTERS.XLSX"
CONTACTS_FILE_PATH = "General/BMC Contacts.xlsx"

# --local fallbacks: the same two files as they land on James's machine
# (the LLT sheet arrives by email/download; contacts sync via OneDrive).
#
# The LLT sheet is resolved by GLOB, newest-modified wins, because Downloads
# accumulates copies — "PENDING LLT MATTERS.XLSX" alongside "PENDING LLT
# MATTERS (1).XLSX" and "(2)". A fixed filename silently picked a three-month-
# old copy once and drafted 84 client emails off it.
LOCAL_LLT_GLOB_DIR = r"C:\Users\jbragdon\Downloads"
LOCAL_LLT_GLOB = "PENDING LLT MATTERS*.XLSX"
LOCAL_CONTACTS_PATH = (
    r"C:\Users\jbragdon\OneDrive - gejlaw.com"
    r"\Multifamily Housing - Documents\General\BMC Contacts.xlsx"
)

# Warn when the newest local LLT sheet is older than this. Christina revises it
# weekly, so anything past two weeks is probably not the current one.
LOCAL_LLT_STALE_DAYS = 14


def newest_local_llt(directory: str | None = None,
                     pattern: str | None = None) -> Path | None:
    """Newest file in `directory` matching `pattern`, or None if there are none."""
    d = Path(directory or LOCAL_LLT_GLOB_DIR)
    if not d.is_dir():
        return None
    hits = sorted(d.glob(pattern or LOCAL_LLT_GLOB),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0] if hits else None

# Default template path — overridden by config["templates_path"] if present.
_DEFAULT_TEMPLATES = (
    r"C:\Users\jbragdon\OneDrive - gejlaw.com"
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

_RIPE_DATE_RE = re.compile(r"(\d{1,2})[./\-](\d{1,2})[./\-](\d{2,4})")


def parse_ripe_date(raw) -> date | None:
    """Parse a Ripe Date cell into a date.

    The column is free text in practice — real values seen include a true
    datetime, "9.9.26", "8.6.26 " (trailing space), and "Ripe on9.3.26"
    (the whole next-step sentence typed into the date cell). Two-digit years
    are 2000s. Returns None when no date can be found.
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    m = _RIPE_DATE_RE.search(str(raw).strip())
    if not m:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_llt_spreadsheet(file_bytes: bytes, sheet: str | None = None) -> list[dict]:
    """Parse the PENDING LLT MATTERS spreadsheet into a list of matter dicts.

    Each dict has: name, property, unit, status, ripe_date, next_steps,
    subsidized, vawa_notice, vawa_complies, client_matter, property_group.

    sheet: sheet name to read. Defaults to the workbook's active sheet — the
    workbook keeps one dated sheet per weekly revision (e.g. "7.25.26") and the
    active one is the current revision.
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb[sheet] if sheet else wb.active

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

        # Column 0 holds the client/portfolio header (e.g. "Bozzuto MD",
        # "Horning"), written once above its block of rows. Read it BEFORE the
        # name check so a header on a nameless row still carries forward.
        col0 = row[0]
        if col0 is not None and str(col0).strip():
            current_group = str(col0).strip()

        name = get(row, "Name")
        if not name:
            continue

        prop = get(row, "Property") or get(row, "Property ")
        if not prop:
            prop = current_group or "Unknown"

        ripe_raw = get(row, "Ripe Date")
        ripe_d = parse_ripe_date(ripe_raw)
        if ripe_d:
            ripe_date = f"{ripe_d.month}/{ripe_d.day}/{ripe_d.year}"
            ripe_dt = datetime(ripe_d.year, ripe_d.month, ripe_d.day)
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
            "ripe_d": ripe_d,
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

# Hand-curated bridges between the LLT sheet's property spelling and the BMC
# Contacts sheet's. Every entry here is a confirmed same-property pair that the
# automatic matcher misses because of a typo or an "at"/"on" swap; keys and
# values are both run through _normalize_property, so write them naturally.
# Add to this list rather than loosening the fuzzy matcher.
CONTACT_ALIASES = {
    "450 k": "450K",
    "city martket at o": "City Market at O",          # sheet typo
    "millstone at kingsview": "Milstone at Kingsview",  # contacts-sheet typo
    "liberty at harbor east": "Liberty Harbor East",
    "residences at the avenue": "Residences on the Avenue",
}

# BMC Contacts is the *Bozzuto Management Company* contact sheet. Properties
# belonging to any other client on the LLT sheet must never be matched against
# it — their names collide (Silver Tree's "Burton Manor" vs Bozzuto's "The
# Burton"; Horning's "Chesapeake" vs Bozzuto's "Chesapeake Ridge") and the
# containment matcher happily pairs them, which would send one client's
# residents to another client's manager. Matched on prefix, so the several
# "Horning - ..." portfolio headers are all covered.
NON_BOZZUTO_PORTFOLIOS = (
    "Horning",
    "Hanover",
    "Franklin Group",
    "Towner Management Company",
    "WPC",
    "MRP Realty",
    "Silver Tree",
    "Associated Catholic Charities",
    "Stella Maris",
    "CHAI",
)


def is_bozzuto_portfolio(portfolio: str | None) -> bool:
    """False when the LLT row's client is someone other than Bozzuto."""
    p = (portfolio or "").strip()
    return not any(p.startswith(x) for x in NON_BOZZUTO_PORTFOLIOS)


def match_property_to_contacts(
    property_name: str,
    contacts_map: dict[str, dict],
    portfolio: str | None = None,
) -> dict | None:
    """Try to match an LLT property name to a contacts entry.

    Match strategy (in order):
    1. Exact normalized match
    2. Curated CONTACT_ALIASES bridge
    3. One name contains the other (for abbreviation cases)

    portfolio: the row's column-A client. When supplied and it names a
    non-Bozzuto client, no match is returned at all — see
    NON_BOZZUTO_PORTFOLIOS.
    """
    if portfolio is not None and not is_bozzuto_portfolio(portfolio):
        return None

    norm = _normalize_property(property_name)

    # Exact normalized match.
    if norm in contacts_map:
        return contacts_map[norm]

    # Curated alias.
    alias = CONTACT_ALIASES.get(norm)
    if alias:
        entry = contacts_map.get(_normalize_property(alias))
        if entry:
            return entry

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
    cc_addresses: list[str] | None = None,
    attachments: list[dict] | None = None,
) -> dict:
    """Create a draft email in the user's Drafts folder.

    Uses POST /users/{email}/messages which creates a message in Drafts
    (NOT sendMail — no mail is sent).

    attachments: list of {"name": str, "path": str, "contentId": str?}
    dicts (same shape outbound.send_mail_guarded takes). A contentId
    marks the file as an inline image referenced by cid: in the body.

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
    if cc_addresses:
        payload["ccRecipients"] = [
            {"emailAddress": {"address": addr}} for addr in cc_addresses
        ]
    if attachments:
        import base64
        graph_atts = []
        for att in attachments:
            file_path = Path(att["path"])
            if not file_path.exists():
                log.warning(f"Draft attachment not found, skipping: {file_path}")
                continue
            att_obj: dict = {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": att.get("name") or file_path.name,
                "contentBytes": base64.b64encode(file_path.read_bytes()).decode("ascii"),
            }
            if att.get("contentId"):
                att_obj["contentId"] = att["contentId"]
                att_obj["isInline"] = True
            graph_atts.append(att_obj)
        if graph_atts:
            payload["attachments"] = graph_atts

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


# Matter classification — mirrors how Christina Araviakis segments her
# property update emails: nonpayment (resolve via ledger), breach/conduct
# (resolve or proceed to filing), recertification, and a read-only court bucket.
#
# A matter is classified from its "Status" (case type) and "Next Steps" cells:
#   1. court  — already in litigation; surfaced as a status section, not an ask.
#   2. recert — recertification / LIHTC notices.
#   3. rent   — nonpayment of rent and non-rent *monetary* charges (utilities).
#   4. breach — everything else (smoking, noise, conduct, occupant, NTCV, etc.).
_COURT_RE = re.compile(
    r"(?i)\b(hearing|trial|mediation|writ|judgment awarded|possession awarded|"
    r"unlawful detainer|wrongful detainer|warrant in debt|tenant'?s assertion|"
    r"small claims|civil action|civil complaint|appeal|motion|filed)\b"
)
_RECERT_RE = re.compile(r"(?i)(recert|lihtc)")
# Monetary cases: rent, non-rent charges, utilities. "non-renew(al)" is NOT
# matched because the substring is "non-ren", not "non-rent".
_RENT_RE = re.compile(r"(?i)(^\s*rent\b|non-?rent|water bill|electric|utilit)")

# Per-category lead-in sentences (the call-to-action differs by category).
LEAD_IN_RENT = (
    "We can file on the following nonpayment cases — could you send updated "
    "ledgers if they still have a balance?"
)
LEAD_IN_BREACH = (
    "We also have the following breach/conduct notices pending. Please let us "
    "know whether these issues have continued, or if the violations have resolved."
)
LEAD_IN_RECERT = (
    "We have the following recertification notices that are ripe for filing. "
    "Please let us know if the residents have completed their recertifications, "
    "or if we should proceed with filing."
)
LEAD_IN_COURT = (
    "For your reference, the matters below are already in court — no action is "
    "needed from you on these at this time."
)


def _classify_matter(status: str, next_steps: str) -> str:
    """Return one of: 'court', 'recert', 'rent', 'breach'."""
    blob = f"{status} {next_steps}"
    if _COURT_RE.search(blob):
        return "court"
    if _RECERT_RE.search(status):
        return "recert"
    if _RENT_RE.search(status):
        return "rent"
    return "breach"


def _ripe_note(ripe_dt, today) -> str:
    """Bare 'M/D' when the notice is not yet ripe; '' when already ripe.

    Templates add the 'after '/'ripe ' prefix so the wording can vary by section.
    """
    if ripe_dt and hasattr(ripe_dt, "date") and ripe_dt.date() > today:
        return f"{ripe_dt.month}/{ripe_dt.day}"
    return ""


def _build_greeting(contacts: list[dict] | None) -> str:
    """Address recipients by first name, Christina-style ('Hi Angel and Bertha,')."""
    firsts: list[str] = []
    for c in contacts or []:
        nm = (c.get("name") or "").strip()
        if not nm:
            continue
        first = nm.split()[0].strip().rstrip(",")
        if first and first.lower() not in [f.lower() for f in firsts]:
            firsts.append(first)
    if not firsts:
        return "Good afternoon,"
    if len(firsts) == 1:
        names = firsts[0]
    elif len(firsts) == 2:
        names = f"{firsts[0]} and {firsts[1]}"
    else:
        names = ", ".join(firsts[:-1]) + f", and {firsts[-1]}"
    return f"Hi {names},"


def _group_matters_for_template(matters: list[dict]) -> dict[str, list[dict]]:
    """Split matters into category buckets with display fields composed.

    Returns {'rent': [...], 'breach': [...], 'recert': [...], 'court': [...]}.
    Each bullet dict carries: unit, name, issue (case type), ripe_note, detail.
    """
    today = datetime.now().date()
    buckets: dict[str, list[dict]] = {"rent": [], "breach": [], "recert": [], "court": []}

    for m in matters:
        status = (m.get("status") or "").strip()
        next_steps = (m.get("next_steps") or "").strip()
        cat = _classify_matter(status, next_steps)
        buckets[cat].append({
            "unit": m.get("unit", ""),
            "name": m.get("name", ""),
            "issue": status,
            "ripe_note": _ripe_note(m.get("ripe_dt"), today),
            "detail": next_steps,
        })

    return buckets


_FALLBACK_TEMPLATE = """\
<p>{{ greeting }}</p>
<p>Below is a summary of the pending landlord-tenant matters for
<strong>{{ property_name }}</strong> as of {{ date }}.</p>
{% if rent %}<p>{{ lead_in_rent }}</p>
<ul>{% for m in rent %}<li>{{ m.unit }}{% if m.ripe_note %} (after {{ m.ripe_note }}){% endif %}</li>{% endfor %}</ul>{% endif %}
{% if breach %}<p>{{ lead_in_breach }}</p>
<ul>{% for m in breach %}<li>{{ m.unit }}{% if m.issue %} ({{ m.issue }}){% endif %}{% if m.ripe_note %} — ripe {{ m.ripe_note }}{% endif %}</li>{% endfor %}</ul>{% endif %}
{% if recert %}<p>{{ lead_in_recert }}</p>
<ul>{% for m in recert %}<li>{{ m.unit }}{% if m.ripe_note %} (after {{ m.ripe_note }}){% endif %}</li>{% endfor %}</ul>{% endif %}
{% if court %}<p>{{ lead_in_court }}</p>
<table border="1" cellpadding="4" cellspacing="0">
<tr><th>Tenant</th><th>Unit</th><th>Type</th><th>Next Court Event</th></tr>
{% for m in court %}<tr><td>{{ m.name }}</td><td>{{ m.unit }}</td><td>{{ m.issue }}</td><td>{{ m.detail }}</td></tr>{% endfor %}
</table>{% endif %}
"""


def render_property_email(
    template: Template,
    property_name: str,
    matters: list[dict],
    today: str,
    contacts: list[dict] | None = None,
) -> str:
    """Render the email body for a single property, segmented by category."""
    buckets = _group_matters_for_template(matters)

    return template.render(
        greeting=_build_greeting(contacts),
        property_name=property_name,
        date=today,
        rent=buckets["rent"],
        breach=buckets["breach"],
        recert=buckets["recert"],
        court=buckets["court"],
        lead_in_rent=LEAD_IN_RENT,
        lead_in_breach=LEAD_IN_BREACH,
        lead_in_recert=LEAD_IN_RECERT,
        lead_in_court=LEAD_IN_COURT,
    )


# ============================================================================
# Main pipeline
# ============================================================================

def run_pending_llt(token: str, config: dict, dry_run: bool = False,
                    limit: int | None = None) -> dict:
    """Full pipeline: download spreadsheets, match, draft emails.

    limit: if set, only process the first N matched properties (useful for testing).
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

        # Respect --limit: stop after N matched properties.
        if limit is not None and drafts_created >= limit:
            drafts_skipped += 1
            continue

        # Use the contact sheet's property name for the email (it's the canonical name).
        display_name = contact_entry["property_name"]
        subject = f"Pending LLT Matters — {display_name}"

        html_body = render_property_email(
            template, display_name, prop_matters, today,
            contacts=contact_entry["contacts"],
        )

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
            cc = config.get("pending_llt_cc", ["caraviakis@gallagherllp.com"])
            result = create_draft_email(token, user_email, to_addresses, subject, html_body, cc_addresses=cc)
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
    if limit is not None:
        print(f"  Limit:              {limit}")
    print(f"  Skipped (no match): {drafts_skipped}")

    if unmatched_properties:
        print(f"\n  Unmatched properties ({len(unmatched_properties)}):")
        for p in sorted(unmatched_properties):
            print(f"    - {p}")
        print(f"\n  To fix: add an 'LLT Name' column in BMC Contacts with the")
        print(f"  exact property name from the LLT sheet for each unmatched property.")

    print()
    return summary


# ============================================================================
# Ripe check-in mode (--pending-llt --ripe)
# ============================================================================
#
# The weekly LLT sheet records a Ripe Date on every matter where a notice has
# run but nothing has been filed — the date the termination period expires and
# the case becomes filable. Once that date passes, the matter needs a decision
# from the property manager: did the violation resolve, or do we sue?
#
# This mode selects exactly those matters (ripe date passed, not yet filed) and
# drafts one short check-in email per property asking that question.

# Next Steps text that means the matter is already in litigation, so it needs
# no check-in even though the row still carries a ripe date. Christina records
# case numbers as "(25-2293)" / "(D-06-CV-26-014601)" / "(GV26018057)".
_FILED_RE = re.compile(
    r"(?i)\b(filed|efiled|hearing|trial|writ|judgment|possession awarded|"
    r"mediation|dismissed|abandonment)\b|\(\d\d-\d+\)|\([A-Z]{1,2}-?\d\d-"
)

# Next Steps text meaning the matter is deliberately parked (settlement talks,
# waiting on an inspection, waiting on the client). Still worth a check-in, but
# flagged in the run report so James can pull it out of a draft before sending.
_HOLD_RE = re.compile(
    r"(?i)\b(hold|working on agreement|move.?out agreement|"
    r"pending client response|settlement discussion)"
)


def select_ripe_matters(
    matters: list[dict],
    as_of: date,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Split matters into (ripe_unfiled, ripe_but_filed, no_usable_ripe_date).

    A matter is "ripe" when its Ripe Date parsed and is on or before `as_of`.
    Rows whose Ripe Date cell is blank or unparseable are returned in the third
    bucket rather than silently dropped, so a mangled cell shows up in the run
    report instead of quietly costing a resident a check-in.
    """
    ripe_unfiled, ripe_filed, unusable = [], [], []
    for m in matters:
        ripe = m.get("ripe_d")
        if not ripe:
            if (m.get("ripe_date") or "").strip():
                unusable.append(m)
            continue
        if ripe > as_of:
            continue
        if _FILED_RE.search(m.get("next_steps") or ""):
            ripe_filed.append(m)
        else:
            ripe_unfiled.append(m)
    return ripe_unfiled, ripe_filed, unusable


def _unit_sort_key(unit: str) -> tuple:
    """Natural sort for unit numbers, so 252 precedes 1259 and N411 groups with
    the N units. Units are free text: "605", "S516", "E-216", "813-2", "PH-E".
    """
    return tuple(
        (1, int(part), "") if part.isdigit() else (0, 0, part.lower())
        for part in re.findall(r"\d+|[^\d]+", unit or "")
    )


_ROMAN_RE = re.compile(r"^(?:i{1,3}|iv|v|vi{1,3}|ix|x)$")


def _property_signature(norm_name: str) -> frozenset[str]:
    """The tokens of a property name that must match EXACTLY to be the same
    property: street numbers, building numbers, and roman numerals.

    This is the guard on fuzzy spelling merges. "Cloisters I" and "Clositers I"
    are one property typed two ways; "Cloisters I" and "Cloisters II" are two
    buildings, and their edit distance is smaller than the typo's. Likewise
    "Solaire 8200" vs "Solaire 8250" and "565 Penn" vs "566 Penn".
    """
    return frozenset(
        t for t in norm_name.split()
        if t.isdigit() or _ROMAN_RE.match(t)
    )


def group_ripe_by_property(matters: list[dict]) -> list[dict]:
    """Group matters by property, collapsing spelling variants.

    Grouping key is the `Property` column, NOT the column-A portfolio header —
    column A is the client ("Bozzuto MD", "Horning", "Towner Management
    Company") and spans dozens of properties, each with its own manager.

    The sheet is hand-typed, so the same property shows up as "City Ridge" and
    "City RIdge", "Cloisters I" and "Clositers I". Near-identical normalized
    names are merged into one group — but only when their numeric/roman-numeral
    tokens match exactly (see _property_signature). Each merge is logged.
    """
    import difflib

    groups: dict[str, dict] = {}
    for m in matters:
        key = _normalize_property(m["property"])
        if key not in groups:
            sig = _property_signature(key)
            eligible = [k for k in groups if _property_signature(k) == sig]
            near = difflib.get_close_matches(key, eligible, n=1, cutoff=0.88)
            if near:
                log.info(f"[pending-llt] Merged property spelling "
                         f"{m['property']!r} into {groups[near[0]]['display']!r}")
                key = near[0]
        gr = groups.setdefault(key, {"display": m["property"], "spellings": Counter(),
                                     "portfolios": Counter(), "matters": []})
        gr["spellings"][m["property"]] += 1
        gr["portfolios"][m.get("property_group") or m["property"]] += 1
        gr["matters"].append(m)

    out = []
    for gr in groups.values():
        gr["display"] = gr["spellings"].most_common(1)[0][0]
        gr["portfolio"] = gr["portfolios"].most_common(1)[0][0]
        gr["matters"].sort(key=lambda x: (_unit_sort_key(x["unit"]),
                                          x["ripe_d"] or date.min))
        out.append(gr)
    out.sort(key=lambda g: g["display"].lower())
    return out


# Ripe-mode buckets. 'court' can't occur here (filed matters are filtered out
# before grouping), so _classify_matter's four categories collapse to three.
RIPE_SECTIONS = [
    ("breach", "Breach cases (non-rent):",
     "has this issue resolved? If not, would you like us to prepare a lawsuit "
     "for breach of lease?"),
    ("rent", "Rent and non-rent charge cases:",
     "if a balance remains outstanding, can you please provide an updated ledger?"),
    ("recert", "Recertification:",
     "has the resident completed the recertification? If not, would you like us "
     "to proceed with filing?"),
]

RIPE_INTRO = (
    "I hope you had a great holiday weekend. We wanted to check in on the "
    "following matters whose termination dates have now passed."
)

_RIPE_TEMPLATE = Template("""\
<p>Good morning!</p>
<p>{{ intro }}</p>
{% for section in sections %}<p><strong>{{ section.heading }}</strong></p>
<ul>
{% for m in section.matters %}<li>{{ m.name }} ({{ m.unit }}) &ndash; {{ m.issue }} &ndash; {{ section.ask }}</li>
{% endfor %}</ul>
{% endfor %}<p>Thank you,</p>
""")


def ripe_bullets(matters: list[dict],
                 label_buildings: bool = False) -> list[dict]:
    """Build one bullet record per matter, in render order.

    Each bullet carries both the rendered text fields AND the source
    spreadsheet row it came from. The source is what makes the draft-vs-sent
    comparison useful: when James cuts or rewords a bullet, the rule behind the
    edit has to be inferrable from what Rocky knew, so the row travels with the
    bullet into the run record.
    """
    buckets: dict[str, list[dict]] = {"breach": [], "rent": [], "recert": []}
    for m in matters:
        cat = _classify_matter(m.get("status") or "", m.get("next_steps") or "")
        # Filed matters are already excluded, so 'court' shouldn't appear; if a
        # status still trips the court regex, treat it as a breach so the matter
        # is asked about rather than dropped.
        if cat not in buckets:
            cat = "breach"
        unit = m.get("unit", "")
        if label_buildings and m.get("property"):
            unit = f"{m['property']}, {unit}" if unit else m["property"]
        buckets[cat].append({
            "section": cat,
            "name": m.get("name", ""),
            "unit": unit,
            "issue": (m.get("status") or "").strip(),
            "source": {
                "property": m.get("property", ""),
                "portfolio": m.get("property_group", ""),
                "name": m.get("name", ""),
                "unit": m.get("unit", ""),
                "status": m.get("status", ""),
                "ripe_date": m.get("ripe_date", ""),
                "next_steps": m.get("next_steps", ""),
                "subsidized": m.get("subsidized", ""),
                "vawa_notice": m.get("vawa_notice", ""),
                "vawa_complies": m.get("vawa_complies", ""),
                "client_matter": m.get("client_matter", ""),
                "on_hold": bool(_HOLD_RE.search(m.get("next_steps") or "")),
            },
        })

    out = []
    for key, _heading, _ask in RIPE_SECTIONS:
        out.extend(buckets[key])
    return out


def render_ripe_email(property_name: str, matters: list[dict],
                      intro: str = RIPE_INTRO,
                      label_buildings: bool = False) -> str:
    """Render the ripe check-in body for one property.

    label_buildings: when one manager covers several buildings (Bridge
    District's Alula / Poplar House / Stratos share a team), the unit alone is
    ambiguous, so name the building alongside it.
    """
    bullets = ripe_bullets(matters, label_buildings=label_buildings)
    sections = [
        {"heading": heading, "ask": ask,
         "matters": [b for b in bullets if b["section"] == key]}
        for key, heading, ask in RIPE_SECTIONS
        if any(b["section"] == key for b in bullets)
    ]
    return _RIPE_TEMPLATE.render(property_name=property_name, intro=intro,
                                 sections=sections)


def display_property_name(contacts_property_name: str,
                         sheet_property_name: str | None = None) -> str:
    """The name to put in the subject line.

    Contacts-sheet property cells are sometimes multi-line, listing the
    buildings a team covers under a district name ("Bridge District\\nAlula,
    Poplar House, Stratos"). The first line is the name to address. The sheet
    also files some properties inverted ("Clark, The"), which un-inverts here.

    When the LLT sheet's spelling normalizes to the same name, prefer the LLT
    spelling — it is how James's team writes it, and it keeps the article that
    the contacts sheet drops ("The Burton", not "Burton"). A spelling that
    normalizes differently is an abbreviation or a typo, so the contacts sheet
    wins ("Elevation" -> "Elevation at Washington Gateway").
    """
    raw = (contacts_property_name or "")
    first = raw.splitlines()[0] if raw else ""
    name = re.sub(r"\s+", " ", first).strip()
    m = re.match(r"^(.*),\s*The$", name, re.IGNORECASE)
    if m:
        name = f"The {m.group(1)}"
    if sheet_property_name:
        sheet = re.sub(r"\s+", " ", sheet_property_name).strip()
        if sheet and _normalize_property(sheet) == _normalize_property(name):
            return sheet
    return name


def ripe_subject(property_name: str) -> str:
    return f"{property_name} - pending resident issues"


def load_llt_and_contacts(
    token: str,
    config: dict,
    local: bool = False,
    llt_path: str | None = None,
    contacts_path: str | None = None,
) -> tuple[bytes | None, bytes | None, str]:
    """Fetch both spreadsheets, from SharePoint or (with --local) from disk.

    Third element names the LLT source that was actually read — it goes into the
    run record, because "which spreadsheet was this" is the one question a
    stale-data incident turns on.
    """
    if local or llt_path or contacts_path:
        explicit = llt_path or config.get("local_llt_path")
        lp = Path(explicit) if explicit else newest_local_llt(
            config.get("local_llt_dir"), config.get("local_llt_glob"))
        if lp is None:
            log.error(f"[pending-llt] No local LLT spreadsheet matching "
                      f"{LOCAL_LLT_GLOB!r} in "
                      f"{config.get('local_llt_dir', LOCAL_LLT_GLOB_DIR)}")
            return None, None, ""
        cp = Path(contacts_path or config.get("local_contacts_path",
                                              LOCAL_CONTACTS_PATH))
        for p in (lp, cp):
            if not p.exists():
                log.error(f"[pending-llt] Local spreadsheet not found: {p}")
                return None, None, ""

        modified = datetime.fromtimestamp(lp.stat().st_mtime)
        age_days = (datetime.now() - modified).days
        # This line is the guard against drafting off a stale sheet — it names
        # the exact file and its date, and prints (not just logs) when old.
        msg = (f"[pending-llt] LLT sheet: {lp.name} "
               f"(modified {modified:%Y-%m-%d %H:%M}, {age_days}d old)")
        log.info(msg)
        print(f"  {msg}")
        if age_days > LOCAL_LLT_STALE_DAYS:
            warn = (f"[pending-llt] WARNING: that sheet is {age_days} days old. "
                    f"Download the current PENDING LLT MATTERS before drafting.")
            log.warning(warn)
            print(f"  {warn}")
        log.info(f"[pending-llt] Contacts sheet: {cp.name}")
        return lp.read_bytes(), cp.read_bytes(), f"{lp.name} (local, {modified:%Y-%m-%d})"

    sp_hostname = config.get("sharepoint_hostname", "gejlaw.sharepoint.com")
    sp_site_path = config.get("sharepoint_site_path", "/sites/MultifamilyHousing")
    site_id = resolve_sharepoint_site_id(token, sp_hostname, sp_site_path)
    if not site_id:
        return None, None, ""
    drive_id = resolve_drive_id(token, site_id)
    if not drive_id:
        return None, None, ""
    llt_file = config.get("llt_file_path", LLT_FILE_PATH)
    llt_bytes = download_sharepoint_file(token, drive_id, llt_file)
    contacts_bytes = download_sharepoint_file(
        token, drive_id, config.get("contacts_file_path", CONTACTS_FILE_PATH))
    if not llt_bytes or not contacts_bytes:
        return None, None, ""
    return llt_bytes, contacts_bytes, f"{llt_file} (SharePoint)"


# ============================================================================
# Run record — the draft-vs-sent calibration corpus
# ============================================================================
#
# Every live ripe run writes an append-only record of exactly what Rocky
# drafted: subject, recipients, body, and the source spreadsheet row behind
# each bullet. James then edits and sends the drafts from Outlook. Because a
# draft keeps its Graph message id when it is sent, `--ripe --compare` can pull
# the sent version back and diff it against the record — and the difference
# between what Rocky wrote and what James sent IS the ruleset Rocky is missing.
# That is the whole point of the record; don't reduce it to a summary.

_DEFAULT_RECORD_DIR = (
    r"C:\Users\jbragdon\OneDrive - gejlaw.com"
    r"\Program Files\Rocky\Pending LLT Records"
)


def record_dir(config: dict) -> Path:
    d = Path(config.get("pending_llt_record_dir", _DEFAULT_RECORD_DIR))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _strip_html(html: str) -> str:
    """Body HTML -> canonical plain text, for readable records and diffing.

    Must be CANONICAL, not merely readable: the same body is stripped once from
    Rocky's own HTML and again from what Graph hands back, and Graph rewrites
    the markup on the way through (entities decoded, inter-tag newlines
    dropped). Anything not normalized here shows up as a phantom edit in
    `--compare` and buries the real ones. Hence: entities decoded to their
    characters, dashes folded to one form, and no blank lines inside a bullet
    run.
    """
    text = re.sub(r"(?i)<br\s*/?>", "\n", html or "")
    text = re.sub(r"(?i)</li\s*>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"(?i)<li\s*[^>]*>", "* ", text)
    text = re.sub(r"<[^>]+>", "", text)
    for entity, char in (("&ndash;", "–"), ("&mdash;", "—"),
                         ("&nbsp;", " "), ("&rsquo;", "’"),
                         ("&lsquo;", "‘"), ("&quot;", '"'),
                         ("&#39;", "'"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&amp;", "&")):
        text = text.replace(entity, char)
    # Fold dash variants so an en-dash typed by Outlook doesn't read as an edit.
    text = text.replace("–", "-").replace("—", "-")

    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines()]
    # Blank lines are normalized structurally rather than preserved, because
    # Rocky's markup and Graph's rewrite of it disagree about where they fall.
    # Canonical form: none inside a bullet run, exactly one everywhere else.
    out = []
    for ln in lines:
        if not ln:
            continue
        if out and not (ln.startswith("* ") and out[-1].startswith("* ")):
            out.append("")
        out.append(ln)
    return "\n".join(out)


def write_ripe_record(config: dict, run: dict) -> dict:
    """Append the run to the JSONL of record and write a readable snapshot.

    Returns {"jsonl": path, "markdown": path}.
    """
    import json

    d = record_dir(config)
    jsonl_path = d / "pending_llt_ripe_drafts.jsonl"
    with jsonl_path.open("a", encoding="utf-8") as f:
        for draft in run["drafts"]:
            f.write(json.dumps({
                "run_id": run["run_id"],
                "drafted_at": run["drafted_at"],
                "as_of": run["as_of"],
                "source_file": run["source_file"],
                "source_sheet": run["source_sheet"],
                **draft,
            }, ensure_ascii=False) + "\n")

    md_path = d / f"{run['run_id']} — drafts.md"
    L = [
        f"# Rocky ripe check-in drafts — run {run['run_id']}",
        "",
        f"- Drafted: {run['drafted_at']}",
        f"- Ripe cutoff (`--as-of`): {run['as_of']}",
        f"- Source: `{run['source_file']}`, sheet `{run['source_sheet']}`",
        f"- Drafts: {len(run['drafts'])}",
        "",
        "This is the baseline. Edit and send the drafts from Outlook, then run",
        "",
        f'    rocky.exe --pending-llt --ripe --compare "{run["run_id"]}"',
        "",
        "to diff what was actually sent against what is recorded here. Omit the",
        "run id to compare the most recent run. Quote it — it contains spaces.",
        "",
        "---",
        "",
    ]
    for draft in run["drafts"]:
        L.append(f"## {draft['property']}")
        L.append("")
        L.append(f"- **Subject:** {draft['subject']}")
        L.append(f"- **To:** {'; '.join(draft['to'])}")
        if draft.get("cc"):
            L.append(f"- **Cc:** {'; '.join(draft['cc'])}")
        L.append(f"- **Message id:** `{draft.get('message_id') or 'n/a'}`")
        if len(draft.get("sheet_properties", [])) > 1:
            L.append(f"- **Buildings covered:** "
                     f"{', '.join(draft['sheet_properties'])}")
        L.append("")
        L.append("### Body as drafted")
        L.append("")
        for line in draft["body_text"].splitlines():
            L.append(f"> {line}" if line else ">")
        L.append("")
        L.append("### Bullets and their source rows")
        L.append("")
        L.append("| Section | Resident | Unit | Status (as printed) | Ripe | "
                 "Next steps | Hold? |")
        L.append("|---|---|---|---|---|---|---|")
        for b in draft["bullets"]:
            s = b["source"]
            L.append(
                f"| {b['section']} | {s['name']} | {s['unit']} | {s['status']} | "
                f"{s['ripe_date']} | {(s['next_steps'] or '').replace('|', '/')} | "
                f"{'yes' if s['on_hold'] else ''} |"
            )
        L.append("")
    md_path.write_text("\n".join(L) + "\n", encoding="utf-8")

    log.info(f"[pending-llt] Run record written: {jsonl_path.name} "
             f"(+{len(run['drafts'])} drafts), {md_path.name}")
    return {"jsonl": str(jsonl_path), "markdown": str(md_path)}


def load_ripe_record(config: dict, run_id: str | None = None) -> list[dict]:
    """Read back recorded drafts. run_id=None returns the most recent run."""
    import json

    path = record_dir(config) / "pending_llt_ripe_drafts.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip()]
    if not rows:
        return []
    if run_id is None:
        run_id = max(r["run_id"] for r in rows)
    return [r for r in rows if r["run_id"] == run_id]


def fetch_message(token: str, user_email: str, message_id: str) -> dict | None:
    """Fetch one message by id, wherever it now lives (Drafts or Sent Items)."""
    url = (f"{GRAPH_API_BASE}/users/{user_email}/messages/{message_id}"
           "?$select=id,subject,toRecipients,ccRecipients,body,"
           "isDraft,sentDateTime,parentFolderId")
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                        timeout=30)
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        log.error(f"Message fetch failed HTTP {resp.status_code}: {resp.text[:200]}")
        return None
    return resp.json()


def compare_ripe_run(token: str, config: dict, run_id: str | None = None) -> dict:
    """Diff each recorded draft against the message as it stands now.

    A draft James edited and sent reports as sent, with a line-level diff of
    the body and any recipient changes. A draft still sitting unsent reports as
    pending. One he deleted outright reports as deleted — itself a rule
    ("don't email this property at all").
    """
    import difflib

    user_email = config.get("user_email") or (
        config.get("user_emails") or ["jbragdon@gallagherllp.com"])[0]

    recorded = load_ripe_record(config, run_id)
    if not recorded:
        return {"error": f"No recorded drafts found"
                         f"{f' for run {run_id}' if run_id else ''}"}
    run_id = recorded[0]["run_id"]

    results = []
    for rec in recorded:
        msg = fetch_message(token, user_email, rec.get("message_id") or "")
        if msg is None:
            results.append({"property": rec["property"], "state": "deleted",
                            "subject_drafted": rec["subject"]})
            continue

        sent = not msg.get("isDraft", True)
        now_text = _strip_html((msg.get("body") or {}).get("content", ""))
        was_text = rec["body_text"]
        diff = [ln for ln in difflib.unified_diff(
            was_text.splitlines(), now_text.splitlines(),
            fromfile="rocky_draft", tofile="as_sent" if sent else "current_draft",
            lineterm="", n=1)]

        def addrs(key):
            return [r["emailAddress"]["address"]
                    for r in (msg.get(key) or [])]

        results.append({
            "property": rec["property"],
            "state": "sent" if sent else "pending",
            "sent_at": msg.get("sentDateTime") if sent else None,
            "subject_drafted": rec["subject"],
            "subject_now": msg.get("subject"),
            "subject_changed": (msg.get("subject") or "") != rec["subject"],
            "to_added": sorted(set(addrs("toRecipients")) - set(rec["to"])),
            "to_removed": sorted(set(rec["to"]) - set(addrs("toRecipients"))),
            "cc_added": sorted(set(addrs("ccRecipients")) - set(rec.get("cc") or [])),
            "cc_removed": sorted(set(rec.get("cc") or []) - set(addrs("ccRecipients"))),
            "body_changed": bool(diff),
            "diff": diff,
            "bullets_drafted": len(rec["bullets"]),
        })

    sent_n = sum(1 for r in results if r["state"] == "sent")
    edited = [r for r in results if r["state"] == "sent" and
              (r["body_changed"] or r["subject_changed"] or r["to_added"] or
               r["to_removed"])]

    print(f"\n{'=' * 68}")
    print(f"Ripe check-in — DRAFT vs SENT  (run {run_id})")
    print(f"{'=' * 68}")
    print(f"  Recorded drafts:  {len(results)}")
    print(f"  Sent:             {sent_n}")
    print(f"  Still pending:    {sum(1 for r in results if r['state'] == 'pending')}")
    print(f"  Deleted unsent:   {sum(1 for r in results if r['state'] == 'deleted')}")
    print(f"  Sent with edits:  {len(edited)}")

    for r in edited:
        print(f"\n--- {r['property']} ---")
        if r["subject_changed"]:
            print(f"  subject: {r['subject_drafted']!r} -> {r['subject_now']!r}")
        for label, key in (("to +", "to_added"), ("to -", "to_removed"),
                           ("cc +", "cc_added"), ("cc -", "cc_removed")):
            if r[key]:
                print(f"  {label} {', '.join(r[key])}")
        for line in r["diff"]:
            print(f"  {line}")

    unsent = [r for r in results if r["state"] != "sent"]
    if unsent:
        print(f"\n  Not sent ({len(unsent)}):")
        for r in unsent:
            print(f"    - {r['property']} ({r['state']})")

    print()
    return {"run_id": run_id, "total": len(results), "sent": sent_n,
            "edited": len(edited), "results": results}


def build_ripe_drafts(
    token: str,
    config: dict,
    as_of: date,
    local: bool = False,
    sheet: str | None = None,
) -> dict:
    """Derive the ripe check-in drafts without creating or sending anything.

    Pure derivation, shared by every caller (draft, dry-run, snapshot) so the
    three can't drift. Returns {"drafts": [...], "unmatched": [...],
    "filed": [...], "unusable": [...], "matters": N, "source_file": str}.
    Each draft carries its subject, recipients, rendered body, bullets (with
    source rows) and hold flags — everything the run record needs.
    """
    llt_bytes, contacts_bytes, source_file = load_llt_and_contacts(
        token, config, local=local)
    if not llt_bytes:
        return {"error": "Failed to load spreadsheets"}

    matters = parse_llt_spreadsheet(llt_bytes, sheet=sheet)
    contacts_map = parse_contacts_spreadsheet(contacts_bytes)
    log.info(f"[pending-llt] Parsed {len(matters)} matters, "
             f"{len(contacts_map)} contact entries")
    if not matters:
        return {"error": "No matters found in spreadsheet"}

    ripe, filed, unusable = select_ripe_matters(matters, as_of)
    log.info(f"[pending-llt] Ripe on/before {as_of:%m/%d/%y}: {len(ripe)} unfiled, "
             f"{len(filed)} already in court, {len(unusable)} unparseable ripe dates")

    groups = group_ripe_by_property(ripe)

    # Resolve each property group to a contacts entry, then collapse groups that
    # resolve to the SAME entry into one draft. Several sheet spellings can name
    # one contacts row ("450 K"/"450K", "Novel 14th"/"Novel 14th Street"), and
    # one contacts row can legitimately cover several buildings under one
    # management team (Bridge District). Either way the manager should get one
    # email, not one per spelling.
    unmatched, by_contact = [], {}

    for gr in groups:
        entry = match_property_to_contacts(gr["display"], contacts_map,
                                           portfolio=gr["portfolio"])
        if not entry:
            for alt in gr["spellings"]:
                entry = match_property_to_contacts(alt, contacts_map,
                                                   portfolio=gr["portfolio"])
                if entry:
                    break
        emails = [c["email"] for c in (entry or {}).get("contacts", [])
                  if c.get("email")]
        if not emails:
            unmatched.append({
                "property": gr["display"],
                "portfolio": gr["portfolio"],
                "matters_count": len(gr["matters"]),
                "contacts_row": (entry or {}).get("property_name"),
            })
            continue

        key = entry["property_name"]
        d = by_contact.setdefault(key, {
            "contacts_name": key,
            "sheet_properties": [],
            "portfolios": [],
            "to": emails,
            "matters": [],
        })
        d["sheet_properties"].append(gr["display"])
        if gr["portfolio"] not in d["portfolios"]:
            d["portfolios"].append(gr["portfolio"])
        d["matters"].extend(gr["matters"])

    cc = config.get("pending_llt_cc", ["caraviakis@gallagherllp.com"])
    drafts = []
    for d in by_contact.values():
        multi = len(d["sheet_properties"]) > 1
        # A draft covering one building can carry that building's own spelling;
        # one covering several has to use the contacts sheet's district name.
        prop = display_property_name(
            d["contacts_name"],
            None if multi else d["sheet_properties"][0],
        ) or d["sheet_properties"][0]
        d["matters"].sort(key=lambda m: (m["property"].lower(),
                                         _unit_sort_key(m["unit"])))
        html_body = render_ripe_email(prop, d["matters"], label_buildings=multi)
        drafts.append({
            "property": prop,
            "sheet_properties": d["sheet_properties"],
            "portfolio": ", ".join(display_property_name(p)
                                   for p in d["portfolios"]),
            "subject": ripe_subject(prop),
            "to": d["to"],
            "cc": cc,
            "body_html": html_body,
            "body_text": _strip_html(html_body),
            "bullets": ripe_bullets(d["matters"], label_buildings=multi),
            "holds": [f"{m['name']} ({m['unit']}) — {m['next_steps']}"
                      for m in d["matters"]
                      if _HOLD_RE.search(m.get("next_steps") or "")],
            "matters_count": len(d["matters"]),
        })
    drafts.sort(key=lambda x: x["property"].lower())

    return {"drafts": drafts, "unmatched": unmatched, "filed": filed,
            "unusable": unusable, "matters": len(matters),
            "ripe": ripe, "source_file": source_file}


def snapshot_ripe_drafts(
    token: str,
    config: dict,
    as_of: date | None = None,
    local: bool = False,
    sheet: str | None = None,
) -> dict:
    """Write a run record for ripe drafts ALREADY sitting in the Drafts folder.

    Recovers the calibration record when drafts were created before the
    recording step existed (or a run died after creating them). Re-derives the
    same drafts, pairs each to the live message by subject to pick up its real
    message id, and records the body as it actually stands in the mailbox — not
    the re-rendered one — so the baseline is what James will really edit.
    Creates nothing and sends nothing.
    """
    as_of = as_of or date.today()
    user_email = config.get("user_email") or (
        config.get("user_emails") or ["jbragdon@gallagherllp.com"])[0]

    built = build_ripe_drafts(token, config, as_of, local=local, sheet=sheet)
    if built.get("error"):
        return built

    # Index the Drafts folder by subject.
    live: dict[str, dict] = {}
    url = (f"{GRAPH_API_BASE}/users/{user_email}/mailFolders/drafts/messages"
           "?$select=id,subject,body,toRecipients,ccRecipients,createdDateTime"
           "&$top=100&$orderby=createdDateTime desc")
    while url:
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                            timeout=60)
        if resp.status_code != 200:
            log.error(f"Drafts listing failed HTTP {resp.status_code}: "
                      f"{resp.text[:200]}")
            return {"error": f"Drafts listing failed ({resp.status_code})"}
        data = resp.json()
        for m in data.get("value", []):
            # Newest wins: the listing is newest-first, so only set once.
            live.setdefault((m.get("subject") or "").strip(), m)
        url = data.get("@odata.nextLink")

    recorded, missing, drifted = [], [], []
    for d in built["drafts"]:
        msg = live.get(d["subject"].strip())
        if not msg:
            missing.append(d["property"])
            continue
        live_text = _strip_html((msg.get("body") or {}).get("content", ""))
        if live_text != d["body_text"]:
            drifted.append(d["property"])
        recorded.append({
            "property": d["property"],
            "sheet_properties": d["sheet_properties"],
            "portfolio": d["portfolio"],
            "subject": msg.get("subject") or d["subject"],
            "to": [r["emailAddress"]["address"]
                   for r in (msg.get("toRecipients") or [])] or d["to"],
            "cc": [r["emailAddress"]["address"]
                   for r in (msg.get("ccRecipients") or [])] or d["cc"],
            "message_id": msg.get("id"),
            "body_html": (msg.get("body") or {}).get("content", d["body_html"]),
            "body_text": live_text or d["body_text"],
            "bullets": d["bullets"],
        })

    if not recorded:
        return {"error": "No matching drafts found in the Drafts folder"}

    now = datetime.now()
    paths = write_ripe_record(config, {
        "run_id": now.strftime("%Y-%m-%d %H%M") + " (snapshot)",
        "drafted_at": now.isoformat(timespec="seconds"),
        "as_of": as_of.isoformat(),
        "source_file": built["source_file"],
        "source_sheet": sheet or "(active)",
        "drafts": recorded,
    })

    print(f"\n{'=' * 68}")
    print("Pending LLT — RIPE DRAFT SNAPSHOT")
    print(f"{'=' * 68}")
    print(f"  Drafts derived:   {len(built['drafts'])}")
    print(f"  Matched in inbox: {len(recorded)}")
    print(f"  Not found:        {len(missing)}")
    if missing:
        for p in missing:
            print(f"    - {p}")
    print(f"  Body differs from re-render: {len(drifted)}")
    if drifted:
        print("    (already edited, or drafted from different data — the "
              "mailbox version is what got recorded)")
        for p in drifted:
            print(f"    - {p}")
    print(f"\n  Record: {Path(paths['markdown']).name}")
    print(f"          {Path(paths['markdown']).parent}\n")

    return {"recorded": len(recorded), "missing": missing, "drifted": drifted,
            "record": paths}


def run_pending_llt_ripe(
    token: str,
    config: dict,
    as_of: date | None = None,
    dry_run: bool = False,
    limit: int | None = None,
    local: bool = False,
    sheet: str | None = None,
) -> dict:
    """Draft one ripe check-in email per property.

    as_of: only matters ripe on or before this date are included. Defaults to
    today. Pass an explicit date to sweep up to a cutoff other than today.
    """
    as_of = as_of or date.today()
    user_email = config.get("user_email") or (
        config.get("user_emails") or ["jbragdon@gallagherllp.com"])[0]

    built = build_ripe_drafts(token, config, as_of, local=local, sheet=sheet)
    if built.get("error"):
        return built

    filed, unusable = built["filed"], built["unusable"]
    ripe, unmatched = built["ripe"], built["unmatched"]
    matters_total, source_file = built["matters"], built["source_file"]

    drafts_created = 0
    matched, results, recorded = [], [], []

    for draft in built["drafts"]:
        rec = {
            "property": draft["property"],
            "sheet_properties": draft["sheet_properties"],
            "portfolio": draft["portfolio"],
            "matters_count": draft["matters_count"],
            "to": draft["to"],
            "holds": draft["holds"],
            "subject": draft["subject"],
        }

        if limit is not None and drafts_created >= limit:
            rec["status"] = "skipped_limit"
            results.append(rec)
            matched.append(rec)
            continue

        message_id = None
        if dry_run:
            rec["status"] = "dry_run"
            rec["body"] = draft["body_html"]
            drafts_created += 1
        else:
            result = create_draft_email(token, user_email, draft["to"],
                                        draft["subject"], draft["body_html"],
                                        cc_addresses=draft["cc"])
            rec["status"] = "created" if result.get("created") else "failed"
            rec["detail"] = result
            if result.get("created"):
                message_id = result.get("message_id")
                drafts_created += 1

        if rec["status"] == "created":
            recorded.append({
                "property": draft["property"],
                "sheet_properties": draft["sheet_properties"],
                "portfolio": draft["portfolio"],
                "subject": draft["subject"],
                "to": draft["to"],
                "cc": draft["cc"],
                "message_id": message_id,
                "body_html": draft["body_html"],
                "body_text": draft["body_text"],
                "bullets": draft["bullets"],
            })

        results.append(rec)
        matched.append(rec)

    record_paths = None
    if recorded:
        now = datetime.now()
        record_paths = write_ripe_record(config, {
            "run_id": now.strftime("%Y-%m-%d %H%M"),
            "drafted_at": now.isoformat(timespec="seconds"),
            "as_of": as_of.isoformat(),
            "source_file": source_file,
            "source_sheet": sheet or "(active)",
            "drafts": recorded,
        })

    summary = {
        "as_of": as_of.isoformat(),
        "source_file": source_file,
        "record": record_paths,
        "total_matters": matters_total,
        "ripe_unfiled": len(ripe),
        "ripe_filed_excluded": len(filed),
        "unparseable_ripe_dates": len(unusable),
        "properties_matched": len(matched),
        "properties_unmatched": len(unmatched),
        "drafts_created": drafts_created,
        "unmatched": unmatched,
        "results": results,
        "filed_excluded": [
            {"property": m["property"], "name": m["name"], "unit": m["unit"],
             "status": m["status"], "ripe_date": m["ripe_date"],
             "next_steps": m["next_steps"]}
            for m in filed
        ],
        "unusable": [
            {"property": m["property"], "name": m["name"],
             "ripe_date": m["ripe_date"]}
            for m in unusable
        ],
    }

    print(f"\n{'=' * 68}")
    print(f"Pending LLT — RIPE CHECK-IN {'(DRY RUN)' if dry_run else ''}"
          f"  as of {as_of:%m/%d/%Y}")
    print(f"{'=' * 68}")
    print(f"  Matters on the sheet:        {summary['total_matters']}")
    print(f"  Ripe & unfiled:              {summary['ripe_unfiled']}")
    print(f"  Ripe but already in court:   {summary['ripe_filed_excluded']} (excluded)")
    if unusable:
        print(f"  Unparseable ripe dates:      {len(unusable)} (excluded — review)")
    print(f"  Properties with a PM email:  {summary['properties_matched']}")
    print(f"  Properties with no email:    {summary['properties_unmatched']}")
    print(f"  Drafts {'prepared' if dry_run else 'created'}:            {drafts_created}")
    if record_paths:
        print(f"\n  Run record: {Path(record_paths['markdown']).name}")
        print(f"              {Path(record_paths['markdown']).parent}")

    all_holds = [(r["property"], h) for r in matched for h in r.get("holds", [])]
    if all_holds:
        print(f"\n  On hold but included ({len(all_holds)}) — pull before sending "
              f"if the hold still stands:")
        for prop, h in all_holds:
            print(f"    - {prop}: {h}")

    if unmatched:
        print(f"\n  No PM email found ({len(unmatched)} properties, "
              f"{sum(u['matters_count'] for u in unmatched)} matters):")
        for u in sorted(unmatched, key=lambda x: x["property"].lower()):
            print(f"    - {u['property']} [{u['portfolio']}] "
                  f"— {u['matters_count']} matters")
        print("\n  These are mostly non-Bozzuto clients (Horning, Franklin Group,")
        print("  Towner, WPC, MRP, Silver Tree). BMC Contacts only covers Bozzuto")
        print("  properties — they need their own contact sheet, or an 'LLT Name'")
        print("  column in BMC Contacts.")

    print()
    return summary
