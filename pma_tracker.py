"""
PMA Activity feed — raw email export for the Maple Updater Agent.

Single entry point, run as a scheduled one-shot command from rocky.py:
    rocky.exe --pma-activity [--dry-run] [--backfill-days N]

A deliberately simple, Claude-free exporter: it pulls new emails from rocky@'s
"Inbox\\PMA emails" folder and appends each one (full body + extracted
attachment text) as a single JSONL line to a feed file in Maple's folder on
OneDrive. Rocky does NOT classify, match tickets, or recommend changes — the
Maple Updater Agent reads the feed and does all of that itself.

History: the HubSpot poller (--pma-poll/--pma-digest/--pma-arm/--pma-sleep/
--pma-test) was retired 2026-06-26, and the corpus/knowledge-synthesis path
(--pma-knowledge) was retired the same day. Maple now owns everything downstream
of this feed.
"""

import base64
import io
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

log = logging.getLogger("rocky")

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

# Skip inline signature images at/under this size when exporting attachments.
SIGNATURE_IMAGE_MAX = 15_000
ATTACHMENT_MAX_BYTES = 16 * 1024 * 1024


# =============================================================================
# State + small helpers
# =============================================================================

def load_pma_state(state_path: Path) -> dict:
    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"[pma] Could not read state {state_path}: {e}")
    return {}


def save_pma_state(state_path: Path, state: dict) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as e:
        log.warning(f"[pma] Could not write state {state_path}: {e}")


def _email_text(email: dict) -> str:
    return email.get("body", {}).get("content") or email.get("bodyPreview") or ""


def _append_jsonl(path: Path, obj: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj) + "\n")
    except OSError as e:
        log.warning(f"[pma] Could not append to {path}: {e}")


def _fetch_attachments(token: str, user_email: str, message_id: str) -> list[dict]:
    """List + download a message's attachments. Returns dicts with contentBytes
    (decoded) or None. Mirrors rocky.fetch_attachments; kept local for the exe."""
    url = f"{GRAPH_API_BASE}/users/{user_email}/messages/{message_id}/attachments"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        resp = requests.get(url, headers=headers,
                            params={"$select": "id,name,contentType,size,isInline"}, timeout=30)
    except requests.RequestException as e:
        log.warning(f"[pma] Could not list attachments for {message_id}: {e}")
        return []
    if resp.status_code != 200:
        log.warning(f"[pma] Could not list attachments for {message_id}: {resp.status_code}")
        return []

    out: list[dict] = []
    for meta in resp.json().get("value", []):
        size = meta.get("size") or 0
        rec = {"id": meta.get("id"), "name": meta.get("name"),
               "contentType": meta.get("contentType"), "size": size,
               "isInline": meta.get("isInline", False), "contentBytes": None}
        if size > ATTACHMENT_MAX_BYTES:
            out.append(rec)
            continue
        att_url = f"{GRAPH_API_BASE}/users/{user_email}/messages/{message_id}/attachments/{meta['id']}"
        try:
            ar = requests.get(att_url, headers=headers, timeout=60)
        except requests.RequestException as e:
            log.warning(f"[pma] Network error fetching attachment {meta.get('name')!r}: {e}")
            out.append(rec)
            continue
        if ar.status_code == 200:
            b64 = ar.json().get("contentBytes")
            if b64:
                try:
                    rec["contentBytes"] = base64.b64decode(b64)
                except Exception:
                    pass
        out.append(rec)
    return out


def _extract_attachment_text(name: str, content_type: str, raw: bytes | None) -> str | None:
    """Best-effort text extraction for drafts (PDF/DOCX/XLSX/text)."""
    if not raw:
        return None
    nl = (name or "").lower()
    ct = (content_type or "").lower()
    try:
        if nl.endswith(".pdf") or "pdf" in ct:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            return "\n".join((p.extract_text() or "") for p in reader.pages).strip() or None
        if nl.endswith(".docx") or "wordprocessingml" in ct:
            from docx import Document
            doc = Document(io.BytesIO(raw))
            return "\n".join(p.text for p in doc.paragraphs if p.text).strip() or None
        if nl.endswith((".xlsx", ".xlsm")) or "spreadsheetml" in ct:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
            lines = []
            for ws in wb.worksheets:
                lines.append(f"[Sheet: {ws.title}]")
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) for c in row if c is not None]
                    if cells:
                        lines.append("\t".join(cells))
            wb.close()
            return "\n".join(lines).strip() or None
        if nl.endswith((".txt", ".md", ".csv", ".log")) or ct.startswith("text/"):
            return raw.decode("utf-8", errors="replace").strip() or None
    except Exception as e:
        log.debug(f"[pma] Extraction failed for {name!r}: {e}")
    return None


# =============================================================================
# PMA Activity feed — raw email export for the Maple Updater Agent
# =============================================================================

# Default = the Rocky-laptop PRODUCTION path (where the exe runs, as user "rocky",
# with Maple shared into rocky@'s OneDrive — same base as rocky.py's
# _DEFAULT_MAPLE_LOGS_DIR). Other machines (e.g. the dev laptop, where the folder
# mounts under the jbragdon profile) override this with "maple_activity_dir" in
# config.json.
DEFAULT_MAPLE_ACTIVITY_DIR = (
    r"C:\Users\rocky\OneDrive - gejlaw.com"
    r"\James D. Bragdon's files - Program Files\Maple\Maple updater agent\PMA Activity"
)
ACTIVITY_FEED_FILE = "pma_activity_feed.jsonl"
DEFAULT_ACTIVITY_BACKFILL_DAYS = 30
# How many recently-exported message ids to remember for idempotent re-runs.
ACTIVITY_SEEN_CAP = 2000


def maple_activity_dir(config: dict) -> Path:
    """Folder where the PMA Activity JSONL feed is written (the Maple Updater
    Agent folder on OneDrive). Overridable via config['maple_activity_dir']."""
    return Path(config.get("maple_activity_dir") or DEFAULT_MAPLE_ACTIVITY_DIR)


def fetch_folder_messages(app_token: str, mailbox: str, folder_id: str,
                          since: datetime) -> list[dict]:
    """Fetch all messages in a specific Outlook folder received after `since`,
    paginated, with plain-text bodies. No recipient filter — the folder itself is
    the scope. Returns [] on error."""
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"{GRAPH_API_BASE}/users/{mailbox}/mailFolders/{folder_id}/messages"
    params = {
        "$filter": f"receivedDateTime gt {since_iso}",
        "$orderby": "receivedDateTime asc",
        "$top": "50",
        "$select": (
            "id,subject,from,toRecipients,ccRecipients,receivedDateTime,"
            "bodyPreview,body,conversationId,internetMessageId,hasAttachments"
        ),
    }
    headers = {
        "Authorization": f"Bearer {app_token}",
        "Accept": "application/json",
        "Prefer": 'outlook.body-content-type="text"',
    }

    messages: list[dict] = []
    next_url: str | None = None
    page = 0
    while page == 0 or next_url:
        try:
            if next_url:
                resp = requests.get(next_url, headers=headers, timeout=30)
            else:
                resp = requests.get(url, headers=headers, params=params, timeout=30)
        except requests.RequestException as e:
            log.error(f"[pma-activity] Network error fetching folder in {mailbox}: {e}")
            return messages
        if resp.status_code == 403:
            log.error(
                f"[pma-activity] 403 reading {mailbox}. The mailbox is likely not in "
                f"the Exchange Application Access Policy. Ask IT to add {mailbox}."
            )
            return messages
        if resp.status_code != 200:
            log.error(f"[pma-activity] Graph API {resp.status_code} for {mailbox}: {resp.text[:300]}")
            return messages
        data = resp.json()
        messages.extend(data.get("value", []))
        next_url = data.get("@odata.nextLink")
        page += 1

    log.info(f"[pma-activity] Fetched {len(messages)} message(s) from folder since {since_iso}.")
    return messages


def _recipients(email: dict, field: str) -> list[dict]:
    out = []
    for r in email.get(field, []) or []:
        ea = r.get("emailAddress", {}) or {}
        out.append({"name": ea.get("name"), "address": ea.get("address")})
    return out


def build_activity_record(email: dict, app_token: str, mailbox: str,
                          source_folder: str, extract_attachments: bool) -> dict:
    """Build one JSONL feed record for an email: full body + (optionally)
    extracted attachment text. Never raises on attachment failures."""
    sender = email.get("from", {}).get("emailAddress", {}) or {}
    record = {
        "id": email.get("id"),
        "internet_message_id": email.get("internetMessageId"),
        "conversation_id": email.get("conversationId"),
        "received": email.get("receivedDateTime"),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source_folder": source_folder,
        "from": {"name": sender.get("name"), "address": sender.get("address")},
        "to": _recipients(email, "toRecipients"),
        "cc": _recipients(email, "ccRecipients"),
        "subject": email.get("subject"),
        "body": _email_text(email),
        "has_attachments": bool(email.get("hasAttachments")),
        "attachments": [],
    }

    if email.get("hasAttachments"):
        try:
            atts = _fetch_attachments(app_token, mailbox, email["id"])
        except Exception as e:
            log.warning(f"[pma-activity] Attachment fetch failed for {email.get('subject')!r}: {e}")
            atts = []
        for att in atts:
            raw = att.get("contentBytes")
            ct = (att.get("contentType") or "").lower()
            # Skip small inline signature images, matching the corpus archiver.
            if att.get("isInline") and ct.startswith("image/") and raw and len(raw) <= SIGNATURE_IMAGE_MAX:
                continue
            text = None
            if extract_attachments and raw:
                text = _extract_attachment_text(att.get("name") or "", ct, raw)
            record["attachments"].append({
                "name": att.get("name"),
                "content_type": att.get("contentType"),
                "size": att.get("size") or 0,
                "is_inline": att.get("isInline", False),
                "text": text,
            })
    return record


def run_pma_activity(
    app_token: str, config: dict, program_dir: Path, data_dir: Path,
    folder_id: str, source_folder: str = "Inbox/PMA emails",
    dry_run: bool = False, backfill_days: int | None = None,
) -> dict:
    """Export new emails from the PMA emails folder to a JSONL feed for Maple.

    Reads only emails newer than the saved cursor (or a backfill window on the
    first run / when backfill_days is given), appends one JSONL record per email
    to the feed in Maple's folder, and advances the cursor. Idempotent: messages
    already exported (tracked by id) are skipped, so re-runs and forced backfills
    don't duplicate lines. One-shot."""
    state_path = data_dir / "state" / "pma_activity_state.json"
    state = load_pma_state(state_path)
    seen_ids = set(state.get("seen_ids", []))

    if backfill_days is not None:
        since = datetime.now(timezone.utc) - timedelta(days=backfill_days)
        log.info(f"[pma-activity] Forced backfill: last {backfill_days} day(s) (ignoring cursor).")
    elif state.get("last_received"):
        since = datetime.fromisoformat(state["last_received"].replace("Z", "+00:00"))
    else:
        days = int(config.get("pma_activity_backfill_days", DEFAULT_ACTIVITY_BACKFILL_DAYS))
        since = datetime.now(timezone.utc) - timedelta(days=days)
        log.info(f"[pma-activity] No state — first run, backfilling last {days} day(s).")

    mailbox = config.get("pma_activity_mailbox") or config.get("rocky_email", "rocky@gallagherllp.com")
    extract_attachments = config.get("pma_activity_extract_attachments", True)

    messages = fetch_folder_messages(app_token, mailbox, folder_id, since)
    if not messages:
        return {"exported": 0, "skipped_seen": 0, "since": since.isoformat(), "dry_run": dry_run}

    feed_path = (data_dir / "pma_activity_feed.dryrun.jsonl") if dry_run \
        else (maple_activity_dir(config) / ACTIVITY_FEED_FILE)

    # Pre-flight: confirm we can actually create the dir AND write the feed
    # BEFORE exporting. If this fails we must NOT advance the cursor, or the
    # fetched emails would be marked "seen" and skipped forever despite never
    # being written. (Append failures inside the loop are otherwise swallowed.)
    try:
        feed_path.parent.mkdir(parents=True, exist_ok=True)
        with open(feed_path, "a", encoding="utf-8"):
            pass
    except OSError as e:
        log.error(
            f"[pma-activity] Cannot write feed at {feed_path}: {e}. "
            f"Cursor NOT advanced (no emails lost). Point maple_activity_dir at a "
            f"path this process can write to."
        )
        return {"error": "feed_not_writable", "feed": str(feed_path),
                "detail": str(e), "fetched": len(messages), "dry_run": dry_run}

    exported = 0
    skipped = 0
    newest = since
    new_ids: list[str] = []
    for email in messages:
        mid = email.get("id")
        if mid and mid in seen_ids:
            skipped += 1
        else:
            record = build_activity_record(email, app_token, mailbox, source_folder, extract_attachments)
            _append_jsonl(feed_path, record)
            exported += 1
            if mid:
                new_ids.append(mid)
        rdt = email.get("receivedDateTime")
        if rdt:
            try:
                dt = datetime.fromisoformat(rdt.replace("Z", "+00:00"))
                if dt > newest:
                    newest = dt
            except ValueError:
                pass

    # Advance the cursor and remember exported ids (capped) for idempotency.
    state["last_received"] = newest.strftime("%Y-%m-%dT%H:%M:%SZ")
    state["seen_ids"] = (list(seen_ids) + new_ids)[-ACTIVITY_SEEN_CAP:]
    if not dry_run:
        save_pma_state(state_path, state)
    else:
        log.info(f"[pma-activity] DRY RUN — cursor NOT advanced; wrote to {feed_path}.")

    result = {"exported": exported, "skipped_seen": skipped, "feed": str(feed_path),
              "since": since.isoformat(), "dry_run": dry_run}
    log.info(f"[pma-activity] Done: {result}")
    return result
