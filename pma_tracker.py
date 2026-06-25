"""
PMA NegotiationWatch — pmateam mailbox monitor + HubSpot ticket updater.

Polls pmateam@gallagherllp.com, keyword-matches each email against the PMA
ticket manifest, asks Claude to classify + propose a status/summary update, and
(when enabled) writes the update to HubSpot. Unmatched emails are logged and
sent in a daily digest to Beth Crassweller and Kyle Virtue.

Two entry points, both run as scheduled one-shot commands from rocky.py:
    rocky.exe --pma-poll   [--dry-run]   every 15 min (Task Scheduler)
    rocky.exe --pma-digest [--dry-run]   8:00 AM

SAFETY — HubSpot writes require ALL of:
    1. config["pma_hubspot_enabled"] == true   (off during the observe week)
    2. Claude's status_changed == true
    3. Claude's confidence in {high, medium}
When writes are off (or --dry-run), Rocky logs the intended payload to
pma_activity.jsonl and changes nothing in HubSpot. That log is the artifact
James reads during the observe week to tune pma_instructions.md and the
manifest keyword lists.

File locations (resolved by rocky.py and passed in):
    program_dir/pma_manifest.json       deal index (from pma_bootstrap.py; OneDrive)
    program_dir/pma_instructions.md     plain-English classifier rules (James edits)
    data_dir/state/pma_poller_state.json  last-processed timestamp (local)
    data_dir/pma_activity.jsonl         full per-email record (local)
    data_dir/pma_unmatched_log.jsonl    unmatched-only, spec shape (local)
"""

import base64
import io
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

log = logging.getLogger("rocky")

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
HUBSPOT_API_BASE = "https://api.hubapi.com"

# Keep in sync with rocky.py CLAUDE_MODEL.
CLAUDE_MODEL = "claude-sonnet-4-5"

# The six valid HubSpot ticket-status (pipeline stage) display values, confirmed
# against the Beth PMA Call View export. The classifier may only choose from
# these; never invent a new one.
VALID_STATUSES = [
    "Document Draft Pending",
    "Active Negotiations- Doc with GEJ",
    "Active Negotiations- Doc with Bozzuto",
    "Active Negotiations- Doc with Client",
    "Signatures Pending",
    "On Hold",
]

# Confidence levels that are allowed to trigger a HubSpot write.
CONFIDENCE_FOR_WRITE = {"high", "medium"}

# Runtime "awake" switch for HubSpot writes. This flag FILE (local, not synced,
# not in config) must be present in addition to config["pma_hubspot_enabled"]
# for any write to happen. Default = asleep (no file). Toggle with
# `rocky.exe --pma-arm` / `--pma-sleep`.
HUBSPOT_ARM_FLAG = "pma_hubspot.armed"


def hubspot_arm_path(data_dir: Path) -> Path:
    return data_dir / "state" / HUBSPOT_ARM_FLAG


def is_hubspot_armed(data_dir: Path) -> bool:
    return hubspot_arm_path(data_dir).exists()

# On first run (no state file), how far back to look — seeds the corpus with
# recent history. Overridable via config["pma_backfill_days"] or the
# --backfill-days CLI flag.
DEFAULT_BACKFILL_DAYS = 60

# HubSpot property internal names. The summary field is a custom property
# ("Summary Status" in the UI); its internal name MUST be confirmed at go-live
# via GET /crm/v3/properties/tickets. Overridable in config.
DEFAULT_STATUS_PROPERTY = "hs_pipeline_stage"
DEFAULT_SUMMARY_PROPERTY = "summary_status"

# --- PMA Team knowledge corpus (built during the observe week, synthesized daily) ---
RAW_EMAILS_DIR = "Raw Emails"
DRAFTS_DIR = "Drafts"
EVENTS_FILE = "events.jsonl"
NEGOTIATION_JSON = "negotiation.json"
NEGOTIATION_MD = "negotiation.md"
KNOWLEDGE_DIR = "_knowledge"
GENERAL_KNOWLEDGE_MD = "pma_general_knowledge.md"
UNMATCHED_DIR = "_unmatched"
# Caps for how much text goes into the machine-readable event record.
EVENT_BODY_CAP = 4000
EVENT_ATTACH_CAP = 4000
# Skip inline signature images at/under this size when archiving.
SIGNATURE_IMAGE_MAX = 15_000

_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# =============================================================================
# JSON extraction (duplicated from rocky.py to keep this module import-cycle free)
# =============================================================================

def _extract_json_from_response(text: str) -> dict:
    """Parse JSON from a Claude response, handling code fences and preamble."""
    cleaned = text.strip()

    fence_match = re.search(r"```(?:json)?\s*\n(.*?)```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escape_next = False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = cleaned[start : i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break

    raise json.JSONDecodeError("No valid JSON object found in response", text, 0)


# =============================================================================
# State
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


# =============================================================================
# Manifest
# =============================================================================

def load_manifest(manifest_path: Path) -> list[dict]:
    if not manifest_path.exists():
        log.error(
            f"[pma] Manifest not found at {manifest_path}. "
            f"Run: python pma_bootstrap.py <hubspot-export.xls>"
        )
        return []
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        log.error(f"[pma] Could not read manifest {manifest_path}: {e}")
        return []


def save_manifest(manifest_path: Path, manifest: list[dict]) -> None:
    try:
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except OSError as e:
        log.warning(f"[pma] Could not write manifest {manifest_path}: {e}")


def load_pma_instructions(instructions_path: Path) -> str:
    if not instructions_path.exists():
        log.info(f"[pma] No instructions file at {instructions_path} (using base prompt only).")
        return ""
    try:
        return instructions_path.read_text(encoding="utf-8").strip()
    except OSError as e:
        log.warning(f"[pma] Could not read instructions: {e}")
        return ""


# =============================================================================
# Mailbox poller (Graph API, application token)
# =============================================================================

def _addressed_to(email: dict, target: str) -> bool:
    """True if `target` appears in the message's To or Cc recipients.

    pmateam@ is a Microsoft 365 Group that forwards to rocky@. The delivered copy
    in rocky's inbox keeps the group address in To/Cc, so we filter on that to
    pick out PMA mail and ignore Remy forwards / other inbox traffic."""
    t = (target or "").lower()
    for field in ("toRecipients", "ccRecipients"):
        for r in email.get(field, []) or []:
            if (r.get("emailAddress", {}).get("address") or "").lower() == t:
                return True
    return False


def fetch_pma_messages(app_token: str, mailbox: str, since: datetime,
                       recipient_filter: str | None = None,
                       extra_senders: list[str] | None = None) -> list[dict]:
    """
    Fetch Inbox messages received after `since`. If recipient_filter is set, keep
    only messages addressed (To/Cc) to that address — EXCEPT messages whose From
    address is in `extra_senders`, which are always kept (PMA emails forwarded in
    from the client, e.g. pma@bozzuto.com, won't have the team inbox in To/Cc).
    Returns [] on error (logged). Mirrors fetch_folder_emails() in rocky.py.
    """
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"{GRAPH_API_BASE}/users/{mailbox}/mailFolders/Inbox/messages"
    params = {
        "$filter": f"receivedDateTime gt {since_iso}",
        "$orderby": "receivedDateTime asc",
        "$top": "50",
        "$select": (
            "id,subject,from,toRecipients,ccRecipients,receivedDateTime,"
            "bodyPreview,body,conversationId,hasAttachments"
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
            log.error(f"[pma] Network error fetching {mailbox}: {e}")
            return messages

        if resp.status_code == 403:
            log.error(
                f"[pma] 403 reading {mailbox}. The mailbox is almost certainly "
                f"not yet in the Exchange Application Access Policy. Ask IT to add "
                f"{mailbox} to the policy that already covers rocky@/eaiken@/etc."
            )
            return messages
        if resp.status_code != 200:
            log.error(f"[pma] Graph API {resp.status_code} for {mailbox}: {resp.text[:300]}")
            return messages

        data = resp.json()
        messages.extend(data.get("value", []))
        next_url = data.get("@odata.nextLink")
        page += 1

    total = len(messages)
    allow_senders = {s.lower() for s in (extra_senders or []) if s}
    if recipient_filter or allow_senders:
        kept: list[dict] = []
        forwarded = 0
        for m in messages:
            if recipient_filter and _addressed_to(m, recipient_filter):
                kept.append(m)
            elif allow_senders and _sender_address(m).lower() in allow_senders:
                kept.append(m)
                forwarded += 1
        messages = kept
        extra = f" (incl. {forwarded} forwarded from {sorted(allow_senders)})" if forwarded else ""
        log.info(f"[pma] Fetched {total} message(s) from {mailbox} since {since_iso}; "
                 f"kept {len(messages)} for PMA review{extra}.")
    else:
        log.info(f"[pma] Fetched {total} message(s) from {mailbox} since {since_iso}")
    return messages


def _email_text(email: dict) -> str:
    return email.get("body", {}).get("content") or email.get("bodyPreview") or ""


def _sender_address(email: dict) -> str:
    return (email.get("from", {}).get("emailAddress", {}).get("address") or "")


# =============================================================================
# Keyword pre-filter
# =============================================================================

def match_candidates(email: dict, manifest: list[dict]) -> list[dict]:
    """
    Fast keyword scan: subject + sender domain + first 500 chars of body against
    each ticket's counterparty_keywords. Whole-word, case-insensitive. Returns
    the list of matching manifest entries (may be empty).
    """
    subject = email.get("subject") or ""
    sender = _sender_address(email)
    domain = sender.split("@")[-1] if "@" in sender else ""
    body_head = _email_text(email)[:500]
    haystack = f"{subject}\n{domain}\n{body_head}".lower()

    candidates: list[dict] = []
    for entry in manifest:
        for kw in entry.get("counterparty_keywords", []):
            kw = (kw or "").strip()
            if not kw:
                continue
            if re.search(rf"\b{re.escape(kw.lower())}\b", haystack):
                candidates.append(entry)
                break  # one keyword hit is enough for this ticket
    return candidates


# =============================================================================
# Claude classifier
# =============================================================================

def _build_system_prompt(instructions: str) -> str:
    statuses = "\n".join(f'- "{s}"' for s in VALID_STATUSES)
    base = f"""You are a legal agreement tracking assistant for Gallagher LLP, a law firm.
You analyze emails copied to the firm's PMA team inbox and extract structured
updates about ongoing contract negotiations.

You will be given:
1. An email (subject, sender, body)
2. One or more candidate HubSpot ticket matches
3. The current ticket status for each candidate

Your job is to return a JSON object only — no preamble, no explanation.

The valid ticket status values are:
{statuses}

Return this exact structure:
{{
  "matched_ticket_id": "<ticket_id or null if no confident match>",
  "confidence": "high | medium | low",
  "proposed_status": "<new status value, or null if no change warranted>",
  "status_changed": true | false,
  "summary_line": "<one sentence, 15-30 words, past tense, starting with the date in M/D format: e.g. '6/5: GEJ received revised draft from counterparty counsel with comments on fee schedule and termination provisions.'>",
  "action_items": ["<any deadlines or response items detected, as short strings>"],
  "reasoning": "<one sentence explaining the match and status decision>"
}}

Rules:
- Only set status_changed to true if the email clearly signals a workflow transition
  (e.g., draft sent, comments received, execution copy circulated, deal stalled).
- Routine acknowledgments, scheduling emails, and FYI forwards should not trigger
  status changes.
- If two tickets are plausible matches, pick the higher-confidence one and note the
  ambiguity in reasoning.
- proposed_status MUST be exactly one of the valid status values above, or null.
- Never invent ticket IDs. Only use the candidate IDs provided."""

    if instructions:
        base += (
            "\n\nFIRM-SPECIFIC INSTRUCTIONS (these refine the rules above; follow them):\n"
            + instructions
        )
    return base


def classify_email(
    client, email: dict, candidates: list[dict], instructions: str,
    max_body_chars: int = 8000,
) -> dict:
    """One Claude call. Returns the parsed JSON dict, or raises on unparseable
    output (caller handles)."""
    cand_lines = []
    for c in candidates:
        cand_lines.append(
            f'- ticket_id {c.get("ticket_id")}: "{c.get("deal_name")}" '
            f'[{c.get("agreement_type")}] — current status: "{c.get("current_status")}"'
        )
    body = _email_text(email)[:max_body_chars]
    sender = email.get("from", {}).get("emailAddress", {})

    user_prompt = f"""EMAIL
Subject: {email.get('subject', '')}
From: {sender.get('name', '')} <{sender.get('address', '')}>
Date: {email.get('receivedDateTime', '')}

Body:
{body}

---

CANDIDATE TICKETS:
{chr(10).join(cand_lines)}

Return ONLY the JSON object."""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        system=_build_system_prompt(instructions),
        messages=[{"role": "user", "content": user_prompt}],
    )
    text_out = response.content[0].text.strip()
    return _extract_json_from_response(text_out)


# =============================================================================
# HubSpot updater
# =============================================================================

def get_pipeline_stages(hubspot_token: str) -> list[dict]:
    """Pull ticket pipeline stages (label + internal stage id). Used at go-live
    to populate hs_pipeline_stage_id in the manifest. Returns [] on failure."""
    url = f"{HUBSPOT_API_BASE}/crm/v3/pipelines/tickets"
    headers = {"Authorization": f"Bearer {hubspot_token}"}
    try:
        resp = requests.get(url, headers=headers, timeout=30)
    except requests.RequestException as e:
        log.error(f"[pma] HubSpot pipelines fetch failed: {e}")
        return []
    if resp.status_code != 200:
        log.error(f"[pma] HubSpot pipelines HTTP {resp.status_code}: {resp.text[:300]}")
        return []
    out: list[dict] = []
    for pipeline in resp.json().get("results", []):
        for stage in pipeline.get("stages", []):
            out.append({
                "pipeline": pipeline.get("label"),
                "stage_label": stage.get("label"),
                "stage_id": stage.get("id"),
            })
    return out


def hubspot_get_ticket(hubspot_token: str, ticket_id: str, properties: list[str]) -> dict | None:
    url = f"{HUBSPOT_API_BASE}/crm/v3/objects/tickets/{ticket_id}"
    headers = {"Authorization": f"Bearer {hubspot_token}"}
    params = {"properties": ",".join(properties)}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
    except requests.RequestException as e:
        log.error(f"[pma] HubSpot read failed for ticket {ticket_id}: {e}")
        return None
    if resp.status_code != 200:
        log.error(f"[pma] HubSpot read HTTP {resp.status_code} for {ticket_id}: {resp.text[:300]}")
        return None
    return resp.json().get("properties", {})


def hubspot_patch_ticket(hubspot_token: str, ticket_id: str, properties: dict) -> bool:
    url = f"{HUBSPOT_API_BASE}/crm/v3/objects/tickets/{ticket_id}"
    headers = {
        "Authorization": f"Bearer {hubspot_token}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.patch(url, headers=headers, json={"properties": properties}, timeout=30)
    except requests.RequestException as e:
        log.error(f"[pma] HubSpot write failed for ticket {ticket_id}: {e}")
        return False
    if resp.status_code in (200, 201):
        log.info(f"[pma] HubSpot ticket {ticket_id} updated.")
        return True
    log.error(f"[pma] HubSpot write HTTP {resp.status_code} for {ticket_id}: {resp.text[:300]}")
    return False


# =============================================================================
# Logging helpers
# =============================================================================

def _append_jsonl(path: Path, obj: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj) + "\n")
    except OSError as e:
        log.warning(f"[pma] Could not append to {path}: {e}")


def _now_parts() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d"), now.strftime("%H:%M")


def log_unmatched(unmatched_path: Path, email: dict, reason: str, reasoning: str) -> None:
    date_str, time_str = _now_parts()
    _append_jsonl(unmatched_path, {
        "date": date_str,
        "time": time_str,
        "received": email.get("receivedDateTime"),
        "from": _sender_address(email),
        "subject": email.get("subject"),
        "reason": reason,
        "claude_reasoning": reasoning,
    })


# =============================================================================
# Per-email processing
# =============================================================================

def process_email(
    email: dict,
    candidates: list[dict],
    manifest: list[dict],
    manifest_by_id: dict,
    instructions: str,
    client,
    config: dict,
    activity_path: Path,
    unmatched_path: Path,
    dry_run: bool,
    armed: bool = False,
) -> str:
    """
    Process one email end to end (classification + HubSpot path). `candidates`
    is the precomputed keyword match. Returns an outcome tag:
      'unmatched_keyword' | 'unmatched_lowconf' | 'unmatched_null'
      | 'logged_no_change' | 'proposed' | 'written' | 'error'
    Always writes a pma_activity.jsonl record.
    """
    subject = email.get("subject", "")

    # No keyword hits → straight to unmatched.
    if not candidates:
        log_unmatched(unmatched_path, email, "zero_keyword_match",
                      "No counterparty keyword matched any active ticket.")
        _append_jsonl(activity_path, {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "received": email.get("receivedDateTime"),
            "from": _sender_address(email),
            "subject": subject,
            "outcome": "unmatched_keyword",
            "candidates": [],
        })
        return "unmatched_keyword"

    # Classify with Claude.
    try:
        result = classify_email(client, email, candidates, instructions)
    except Exception as e:
        log.error(f"[pma] Classification failed for {subject!r}: {e}")
        _append_jsonl(activity_path, {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "received": email.get("receivedDateTime"),
            "from": _sender_address(email),
            "subject": subject,
            "outcome": "error",
            "error": str(e),
            "candidates": [c.get("ticket_id") for c in candidates],
        })
        return "error"

    matched_id = result.get("matched_ticket_id")
    confidence = (result.get("confidence") or "").lower()
    status_changed = bool(result.get("status_changed"))
    proposed_status = result.get("proposed_status")
    summary_line = result.get("summary_line") or ""
    reasoning = result.get("reasoning") or ""

    # Validate matched_id against the candidate set (never trust an invented id).
    candidate_ids = {str(c.get("ticket_id")) for c in candidates}
    if matched_id is not None and str(matched_id) not in candidate_ids:
        log.warning(f"[pma] Claude returned non-candidate ticket id {matched_id!r}; treating as null.")
        matched_id = None

    # Decide HubSpot write eligibility (two gates + global enable).
    hubspot_enabled = bool(config.get("pma_hubspot_enabled"))
    status_prop = config.get("hubspot_status_property", DEFAULT_STATUS_PROPERTY)
    summary_prop = config.get("hubspot_summary_property", DEFAULT_SUMMARY_PROPERTY)

    write_gates_pass = (
        matched_id is not None
        and confidence in CONFIDENCE_FOR_WRITE
    )

    # Unmatched conditions per spec §5.
    if matched_id is None:
        reason = "low_confidence" if confidence == "low" else "no_ticket_match"
        log_unmatched(unmatched_path, email, reason, reasoning)
        outcome = "unmatched_null" if reason == "no_ticket_match" else "unmatched_lowconf"
        _append_jsonl(activity_path, _activity_record(email, outcome, candidates, result, None))
        return outcome
    if confidence == "low":
        log_unmatched(unmatched_path, email, "low_confidence", reasoning)
        _append_jsonl(activity_path, _activity_record(email, "unmatched_lowconf", candidates, result, None))
        return "unmatched_lowconf"

    entry = manifest_by_id.get(str(matched_id), {})
    stage_id = entry.get("hs_pipeline_stage_id", "")

    # Build the intended HubSpot payload (what we would write).
    proposed_payload: dict = {}
    if summary_line:
        # Append to existing summary, preserving Beth's manual notes.
        proposed_payload[summary_prop] = {"_append": summary_line}
    if status_changed and proposed_status in VALID_STATUSES:
        proposed_payload[status_prop] = {"stage_label": proposed_status, "stage_id": stage_id}

    # Decide action. Writes require the config master switch AND the runtime
    # arm flag (affirmative "awake") AND the two classification gates.
    should_write = (
        hubspot_enabled
        and armed
        and not dry_run
        and write_gates_pass
        and bool(proposed_payload)
        and bool(config.get("hubspot_token"))
    )

    hubspot_result = None
    if should_write:
        hubspot_result = _do_hubspot_write(
            config["hubspot_token"], str(matched_id), summary_line,
            status_changed and proposed_status in VALID_STATUSES, stage_id,
            status_prop, summary_prop,
        )
        outcome = "written" if hubspot_result.get("written") else "error"
        # Reflect new status locally so the next run sees current state.
        if hubspot_result.get("written") and status_changed and proposed_status in VALID_STATUSES:
            entry["current_status"] = proposed_status
    else:
        outcome = "proposed" if proposed_payload else "logged_no_change"
        if not proposed_payload:
            outcome = "logged_no_change"

    _append_jsonl(activity_path, _activity_record(
        email, outcome, candidates, result, {
            "matched_ticket_id": matched_id,
            "proposed_payload": _readable_payload(proposed_payload),
            "write_gates_pass": write_gates_pass,
            "hubspot_enabled": hubspot_enabled,
            "hubspot_armed": armed,
            "dry_run": dry_run,
            "hubspot_result": hubspot_result,
        },
    ))
    return outcome


def _do_hubspot_write(
    hubspot_token: str, ticket_id: str, summary_line: str,
    change_status: bool, stage_id: str, status_prop: str, summary_prop: str,
) -> dict:
    """Read-then-append the summary, optionally set the stage. Returns a result dict."""
    props_to_read = [summary_prop] + ([status_prop] if change_status else [])
    current = hubspot_get_ticket(hubspot_token, ticket_id, props_to_read)
    if current is None:
        return {"written": False, "reason": "read_failed"}

    payload: dict = {}
    if summary_line:
        existing = (current.get(summary_prop) or "").strip()
        payload[summary_prop] = f"{existing}\n{summary_line}".strip() if existing else summary_line
    if change_status:
        if not stage_id:
            log.warning(
                f"[pma] Ticket {ticket_id}: status change requested but no "
                f"hs_pipeline_stage_id in manifest — skipping the stage write "
                f"(pull stage IDs and re-bootstrap). Summary still updated."
            )
        else:
            payload[status_prop] = stage_id

    if not payload:
        return {"written": False, "reason": "empty_payload"}

    ok = hubspot_patch_ticket(hubspot_token, ticket_id, payload)
    return {"written": ok, "reason": None if ok else "patch_failed", "payload": payload}


def _readable_payload(payload: dict) -> dict:
    """Flatten the structured proposed payload for human-readable logging."""
    out = {}
    for k, v in payload.items():
        if isinstance(v, dict) and "_append" in v:
            out[k] = f"APPEND: {v['_append']}"
        elif isinstance(v, dict) and "stage_label" in v:
            out[k] = f"SET STAGE: {v['stage_label']} (id={v.get('stage_id') or 'MISSING'})"
        else:
            out[k] = v
    return out


def _activity_record(email: dict, outcome: str, candidates: list[dict],
                     result: dict, extra: dict | None) -> dict:
    rec = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "received": email.get("receivedDateTime"),
        "from": _sender_address(email),
        "subject": email.get("subject"),
        "outcome": outcome,
        "candidates": [c.get("ticket_id") for c in candidates],
        "classification": result,
    }
    if extra:
        rec.update(extra)
    return rec


# =============================================================================
# Knowledge corpus — capture (runs every poll, deterministic, no Claude)
# =============================================================================
# Every pmateam email + its draft/redline attachments are archived into a
# per-deal folder under the PMA Team root, and a structured event is appended to
# that deal's events.jsonl. This builds the machine-readable substrate that the
# daily --pma-knowledge synthesis turns into negotiation briefs.

def pma_team_root(config: dict) -> Path:
    """Resolve the PMA Team corpus root (OneDrive, sibling of Rocky Cases)."""
    root = config.get("pma_team_root")
    if root:
        return Path(root)
    cases = config.get("cases_root")
    if cases:
        return Path(cases).parent / "PMA Team"
    return Path(r"C:\Users\jbragdon\OneDrive\OneDrive - gejlaw.com\PMA Team")


def _resolve_pma_file(config: dict, program_dir: Path, filename: str) -> Path:
    """Locate a PMA knowledge/config file (manifest, instructions).

    Preferred location is the PMA Team root on OneDrive (synced + editable from
    the dev laptop, browsable, read fine as plain text). Falls back to the
    program directory (next to the .exe) if a copy lives there instead. Returns
    the OneDrive path if neither exists, so error messages point at the
    recommended spot. Robust whether the .exe runs from OneDrive or a local copy."""
    onedrive = pma_team_root(config) / filename
    if onedrive.exists():
        return onedrive
    local = program_dir / filename
    if local.exists():
        return local
    return onedrive


def _safe(name: str) -> str:
    return _UNSAFE_FILENAME.sub("_", name or "").strip(" .") or "unnamed"


def _msg_prefix(email: dict) -> str:
    received = email.get("receivedDateTime") or ""
    compact = re.sub(r"[^0-9T]", "", received)[:13] or "unknown"
    import hashlib
    mid = email.get("id") or email.get("conversationId") or ""
    h = hashlib.md5(mid.encode("utf-8")).hexdigest()[:8] if mid else "nohash"
    return f"{compact}_{h}"


def _deal_folder(root: Path, entry: dict) -> Path:
    """Per-deal folder named '<ticket_id> - <deal name>'. Matched by ticket_id
    prefix so renames don't break it."""
    tid = str(entry.get("ticket_id"))
    if root.exists():
        for c in root.iterdir():
            if c.is_dir() and c.name.startswith(tid):
                return c
    return root / _safe(f"{tid} - {entry.get('deal_name', '')}")[:120]


# Attachment download cap (16 MB). Self-contained here so the frozen .exe never
# depends on importing the entry script (rocky.py) at runtime.
ATTACHMENT_MAX_BYTES = 16 * 1024 * 1024


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


def archive_email(
    email: dict, candidates: list[dict], app_token: str, mailbox: str, root: Path,
) -> dict:
    """
    Save the email body + draft attachments and append a structured event.
    Filing key is deterministic (keyword candidates), independent of Claude:
      - exactly one candidate -> that deal's folder
      - zero or ambiguous (>1) -> _unmatched bucket (candidate ids recorded)
    Never raises.
    """
    entry = candidates[0] if len(candidates) == 1 else None
    received = email.get("receivedDateTime") or ""

    if entry is not None:
        folder = _deal_folder(root, entry)
        events_path = folder / EVENTS_FILE
        body_base = folder
    else:
        month = received[:7] or datetime.now(timezone.utc).strftime("%Y-%m")
        base = root / UNMATCHED_DIR
        folder = base / month
        events_path = base / EVENTS_FILE
        body_base = folder

    raw_dir = body_base / RAW_EMAILS_DIR
    drafts_dir = body_base / DRAFTS_DIR
    prefix = _msg_prefix(email)
    body_path = raw_dir / f"{prefix}_email.txt"

    # Idempotency: if this email is already archived (body file present), skip
    # the whole thing so re-runs / --backfill-days don't duplicate events.jsonl
    # lines or re-download attachments.
    if body_path.exists():
        return {"archived": False, "reason": "already_archived", "folder": str(folder),
                "filed_ticket_id": str(entry.get("ticket_id")) if entry else None}

    try:
        raw_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.warning(f"[pma] Could not create corpus folder {raw_dir}: {e}")
        return {"archived": False, "reason": str(e)}

    sender = email.get("from", {}).get("emailAddress", {})
    body = _email_text(email)

    # Save the email body as a .txt record.
    try:
        to_line = ", ".join(
            r.get("emailAddress", {}).get("address", "")
            for r in email.get("toRecipients", [])
        )
        body_path.write_text(
            f"Subject: {email.get('subject','')}\n"
            f"From: {sender.get('name','')} <{sender.get('address','')}>\n"
            f"To: {to_line}\n"
            f"Received: {received}\n\n---\n\n{body}",
            encoding="utf-8",
        )
    except OSError as e:
        log.warning(f"[pma] Could not write {body_path}: {e}")

    # Save draft/redline attachments + extract their text for the event.
    attach_records: list[dict] = []
    if email.get("hasAttachments"):
        try:
            atts = _fetch_attachments(app_token, mailbox, email["id"])
        except Exception as e:
            log.warning(f"[pma] Attachment fetch failed for {email.get('subject')!r}: {e}")
            atts = []
        for att in atts:
            raw = att.get("contentBytes")
            if not raw:
                continue
            ct = (att.get("contentType") or "").lower()
            if att.get("isInline") and ct.startswith("image/") and len(raw) <= SIGNATURE_IMAGE_MAX:
                continue
            try:
                drafts_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            safe_name = _safe(att.get("name") or "attachment.bin")
            att_path = drafts_dir / f"{prefix}_{safe_name}"
            if not att_path.exists():
                try:
                    att_path.write_bytes(raw)
                except OSError as e:
                    log.warning(f"[pma] Could not write draft {att_path}: {e}")
            text = _extract_attachment_text(att.get("name") or "", ct, raw)
            attach_records.append({
                "name": att.get("name"),
                "size": att.get("size"),
                "saved_as": att_path.name,
                "text_excerpt": (text or "")[:EVENT_ATTACH_CAP] or None,
            })

    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "received": received,
        "from_name": sender.get("name"),
        "from_addr": sender.get("address"),
        "to": [r.get("emailAddress", {}).get("address", "") for r in email.get("toRecipients", [])],
        "cc": [r.get("emailAddress", {}).get("address", "") for r in email.get("ccRecipients", [])],
        "subject": email.get("subject"),
        "conversation_id": email.get("conversationId"),
        "candidate_ticket_ids": [c.get("ticket_id") for c in candidates],
        "filed_ticket_id": str(entry.get("ticket_id")) if entry else None,
        "body_excerpt": body[:EVENT_BODY_CAP],
        "attachments": attach_records,
    }
    _append_jsonl(events_path, event)
    return {"archived": True, "folder": str(folder), "filed_ticket_id": event["filed_ticket_id"]}


# =============================================================================
# Knowledge corpus — daily synthesis (--pma-knowledge, one Claude call per
# active deal + one for the cross-deal general knowledge)
# =============================================================================

DEAL_SYNTH_SYSTEM_PROMPT = """You maintain a structured knowledge file about ONE property management agreement (PMA) negotiation for Gallagher LLP.

You receive the EXISTING brief (JSON, may be empty on first run) and NEW activity (emails + draft/redline excerpts) since it was last updated. Integrate the new activity into an updated brief.

Return ONLY a JSON object with these fields:
{
  "ticket_id": "<unchanged>",
  "deal_name": "<unchanged>",
  "agreement_type": "<e.g. Lease-up PMA, Amendment, Renewal>",
  "parties": [{"name": "", "role": "<GEJ counsel | BMC/Bozzuto | owner/client | other>", "org": "", "email": ""}],
  "what_is_being_negotiated": "<1-3 sentences: the agreement and the property/deal>",
  "key_terms": [{"term": "", "status": "<agreed | open | proposed>", "positions": "<who wants what>"}],
  "open_issues": ["<unresolved points>"],
  "document_versions": [{"date": "M/D", "filename": "", "from": "", "notes": "<what changed / redline summary>"}],
  "chronology": [{"date": "M/D", "event": "<what happened>"}],
  "current_posture": "<one sentence: where the negotiation stands now>",
  "status_evidence": "<compare the emails to the CURRENT HUBSPOT RECORD provided: which email(s) appear to justify the recorded status and summary line, and note any gap, staleness, or activity the recorded status hasn't caught up to>"
}

Rules:
- You are also given the CURRENT HUBSPOT RECORD (the team's human-authored status + summary line). Treat it as ground truth: use it to anchor the brief, and populate status_evidence by correlating it with the emails. Do NOT alter or second-guess it.
- Preserve and extend prior content; don't drop facts established earlier unless superseded.
- Be specific about negotiated terms (fees, term length, termination, departure fees, indemnities, etc.).
- Keep it factual; do not speculate beyond the materials."""

GENERAL_SYNTH_SYSTEM_PROMPT = """You maintain a general knowledge base about how Property Management Agreement (PMA) negotiations work at Gallagher LLP, learned from real deal activity.

You receive the PRIOR general-knowledge markdown (may be empty) and SUMMARIES of recent deal activity. Return an UPDATED markdown document that captures GENERAL, cross-deal knowledge — not deal-specific detail. Cover, as the evidence supports:
- Typical workflow / stages of a PMA negotiation and who drives each
- Common negotiated terms and where parties usually land
- Recurring sticking points and how they get resolved
- Useful patterns for classifying/triaging PMA emails

Return ONLY the markdown document (no preamble). Preserve prior insights; refine and add as new evidence arrives."""


STATUS_CALIBRATION_MD = "status_calibration.md"

STATUS_CALIBRATION_SYSTEM_PROMPT = """You are learning HOW Gallagher LLP's team writes PMA ticket STATUS values and SUMMARY lines, by comparing real negotiation emails against the team's own human-authored HubSpot record.

You receive, for several deals: the recent email activity, the CURRENT human-written status, and the CURRENT human-written summary line (which is usually dated, e.g. "6/4: Sam sent an updated draft to BMC for review.").

From this supervised signal, infer and maintain GENERAL, reusable rules:
1. Email-event -> status mapping: what kind of email activity corresponds to each status value (e.g. "Active Negotiations- Doc with Bozzuto", "Signatures Pending", "On Hold").
2. Summary-line style: the voice, length, tense, date format, and what detail the team includes/omits when writing a summary line.
3. Common status transitions and the email triggers that cause them.
4. Cases where the recorded status appears stale vs. the emails (useful caution flags).

Return ONLY the updated markdown document (no preamble). Preserve prior rules; refine and add as new evidence arrives. This document will later be folded into Rocky's classifier instructions, so be concrete and prescriptive."""


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        log.warning(f"[pma] Could not read {path}: {e}")
    return out


def _render_negotiation_md(brief: dict) -> str:
    def bullets(items, fmt):
        return "\n".join(fmt(i) for i in items) if items else "_None recorded yet._"
    parties = bullets(brief.get("parties", []),
                      lambda p: f"- **{p.get('name','?')}** — {p.get('role','')} "
                                f"({p.get('org','')}) {p.get('email','')}".strip())
    terms = bullets(brief.get("key_terms", []),
                   lambda t: f"- **{t.get('term','?')}** [{t.get('status','')}]: {t.get('positions','')}")
    issues = bullets(brief.get("open_issues", []), lambda s: f"- {s}")
    versions = bullets(brief.get("document_versions", []),
                      lambda d: f"- {d.get('date','')} — {d.get('filename','')} "
                                f"(from {d.get('from','')}): {d.get('notes','')}")
    chrono = bullets(brief.get("chronology", []),
                    lambda c: f"- **{c.get('date','')}** — {c.get('event','')}")
    gt_status = brief.get("hubspot_status", "")
    gt_summary = brief.get("hubspot_summary_status", "")
    gt_block = ""
    if gt_status or gt_summary:
        gt_block = (
            f"\n## Current HubSpot record (human-authored — ground truth)\n"
            f"- **Status:** {gt_status or '_(none)_'}\n"
            f"- **Summary line:** {gt_summary or '_(none)_'}\n"
            f"- **How the emails support it:** {brief.get('status_evidence','_Not assessed._')}\n"
        )
    return f"""# {brief.get('deal_name','(unknown deal)')}

**Ticket:** {brief.get('ticket_id','')}  |  **Agreement type:** {brief.get('agreement_type','')}
**Last synthesized:** {brief.get('last_synthesized','')}
{gt_block}
## What is being negotiated
{brief.get('what_is_being_negotiated','_Not yet determined._')}

## Current posture
{brief.get('current_posture','_Not yet determined._')}

## Parties
{parties}

## Key terms
{terms}

## Open issues
{issues}

## Document versions
{versions}

## Chronology
{chrono}
"""


def _build_synth_user_prompt(prior: dict, new_events: list[dict],
                            ground_truth: dict | None = None) -> str:
    ev_lines = []
    total = 0
    for e in new_events[-30:]:
        att = ""
        for a in e.get("attachments", []):
            snippet = (a.get("text_excerpt") or "")[:2000]
            att += f"\n    [draft: {a.get('name')}]\n    {snippet}"
        block = (
            f"- {e.get('received','')} | from {e.get('from_name','')} "
            f"<{e.get('from_addr','')}>\n  Subject: {e.get('subject','')}\n"
            f"  {(e.get('body_excerpt') or '')[:2000]}{att}"
        )
        ev_lines.append(block)
        total += len(block)
        if total > 24000:
            ev_lines.append("[older new events omitted — cap reached]")
            break
    gt_block = ""
    if ground_truth and (ground_truth.get("status") or ground_truth.get("summary")):
        gt_block = (
            "\n\nCURRENT HUBSPOT RECORD (human-authored, ground truth):\n"
            f"  Status: {ground_truth.get('status') or '(none)'}\n"
            f"  Summary line: {ground_truth.get('summary') or '(none)'}"
        )
    return (
        "EXISTING BRIEF (JSON):\n"
        + json.dumps({k: v for k, v in prior.items() if not k.startswith("last_")}, indent=2)
        + gt_block
        + "\n\nNEW ACTIVITY since last update:\n"
        + "\n\n".join(ev_lines)
        + "\n\nReturn ONLY the updated JSON brief."
    )


def synthesize_deal(client, prior: dict, new_events: list[dict], ticket_id: str,
                    deal_name: str, ground_truth: dict | None = None) -> dict:
    """One Claude call to produce an updated structured brief for a deal.
    ground_truth = {"status","summary"} from the HubSpot manifest snapshot."""
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        system=DEAL_SYNTH_SYSTEM_PROMPT,
        messages=[{"role": "user",
                   "content": _build_synth_user_prompt(prior, new_events, ground_truth)}],
    )
    brief = _extract_json_from_response(response.content[0].text.strip())
    brief.setdefault("ticket_id", ticket_id)
    brief.setdefault("deal_name", deal_name)
    # Record the ground truth verbatim (not model-generated) for later training.
    if ground_truth:
        brief["hubspot_status"] = ground_truth.get("status") or ""
        brief["hubspot_summary_status"] = ground_truth.get("summary") or ""
    return brief


def update_general_knowledge(client, root: Path, touched: list[tuple[str, dict]]) -> bool:
    """One Claude call to refresh the cross-deal general PMA knowledge."""
    kdir = root / KNOWLEDGE_DIR
    kpath = kdir / GENERAL_KNOWLEDGE_MD
    prior = kpath.read_text(encoding="utf-8") if kpath.exists() else ""

    summaries = []
    for name, brief in touched:
        summaries.append(
            f"### {brief.get('deal_name', name)} ({brief.get('agreement_type','')})\n"
            f"- What: {brief.get('what_is_being_negotiated','')}\n"
            f"- Posture: {brief.get('current_posture','')}\n"
            f"- Open issues: {', '.join(brief.get('open_issues', []) or [])}\n"
            f"- Key terms: "
            + "; ".join(f"{t.get('term')} [{t.get('status')}]" for t in brief.get('key_terms', []))
        )
    user = (
        "PRIOR GENERAL KNOWLEDGE (markdown):\n" + (prior or "(empty)")
        + "\n\nRECENT DEAL ACTIVITY SUMMARIES:\n" + "\n\n".join(summaries)
        + "\n\nReturn ONLY the updated markdown document."
    )
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=3000,
            system=GENERAL_SYNTH_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
        md = response.content[0].text.strip()
        # Strip a stray code fence if present.
        md = re.sub(r"^```(?:markdown)?\s*\n(.*)\n```$", r"\1", md, flags=re.DOTALL).strip()
        kdir.mkdir(parents=True, exist_ok=True)
        kpath.write_text(md, encoding="utf-8")
        log.info(f"[pma] Updated general knowledge ({len(md)} chars).")
        return True
    except Exception as e:
        log.error(f"[pma] General knowledge synthesis failed: {e}")
        return False


def update_status_calibration(client, root: Path,
                              touched: list[tuple[str, dict]]) -> bool:
    """One Claude call to learn how the team writes status values + summary lines,
    by comparing each deal's emails to its human-authored HubSpot record. Only
    uses deals that actually have a recorded status/summary (the supervised set).
    Writes _knowledge/status_calibration.md."""
    kdir = root / KNOWLEDGE_DIR
    kpath = kdir / STATUS_CALIBRATION_MD
    prior = kpath.read_text(encoding="utf-8") if kpath.exists() else ""

    blocks = []
    for name, brief in touched:
        status = brief.get("hubspot_status") or ""
        summary = brief.get("hubspot_summary_status") or ""
        if not status and not summary:
            continue  # no ground truth to learn from
        chrono = "; ".join(
            f"{c.get('date','')} {c.get('event','')}" for c in brief.get("chronology", [])
        )
        blocks.append(
            f"### {brief.get('deal_name', name)} ({brief.get('agreement_type','')})\n"
            f"- HUMAN STATUS: {status}\n"
            f"- HUMAN SUMMARY LINE: {summary}\n"
            f"- Emails/chronology Rocky observed: {chrono or '(little/no recent email)'}\n"
            f"- Rocky's status_evidence: {brief.get('status_evidence','')}"
        )
    if not blocks:
        log.info("[pma] No deals with HubSpot ground truth this run; skipping calibration.")
        return False

    user = (
        "PRIOR CALIBRATION RULES (markdown):\n" + (prior or "(empty)")
        + "\n\nNEW SUPERVISED EXAMPLES (emails vs. the team's human-written record):\n"
        + "\n\n".join(blocks)
        + "\n\nReturn ONLY the updated markdown document."
    )
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=3000,
            system=STATUS_CALIBRATION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
        md = response.content[0].text.strip()
        md = re.sub(r"^```(?:markdown)?\s*\n(.*)\n```$", r"\1", md, flags=re.DOTALL).strip()
        kdir.mkdir(parents=True, exist_ok=True)
        kpath.write_text(md, encoding="utf-8")
        log.info(f"[pma] Updated status calibration ({len(blocks)} example(s), {len(md)} chars).")
        return True
    except Exception as e:
        log.error(f"[pma] Status calibration synthesis failed: {e}")
        return False


def run_pma_knowledge(client, config: dict, program_dir: Path,
                      dry_run: bool = False) -> dict:
    """Daily synthesis. For each deal with new events, update its structured
    brief; then refresh the cross-deal general knowledge. One-shot."""
    root = pma_team_root(config)
    if not root.exists():
        log.info(f"[pma] No corpus yet at {root}; nothing to synthesize.")
        return {"updated": 0, "reason": "no_corpus"}

    # HubSpot ground truth (status + summary line) for supervised calibration.
    manifest = load_manifest(_resolve_pma_file(config, program_dir, "pma_manifest.json"))
    manifest_by_id = {str(e.get("ticket_id")): e for e in manifest}

    updated: list[str] = []
    touched: list[tuple[str, dict]] = []
    skipped = 0

    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name == KNOWLEDGE_DIR or d.name == UNMATCHED_DIR:
            continue
        events_path = d / EVENTS_FILE
        events = _read_jsonl(events_path)
        if not events:
            continue

        json_path = d / NEGOTIATION_JSON
        prior = {}
        if json_path.exists():
            try:
                prior = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                prior = {}
        last_synth = prior.get("last_event_synthesized") or ""
        new_events = [e for e in events if (e.get("received") or "") > last_synth]
        if not new_events:
            skipped += 1
            continue

        tid = (events[0].get("filed_ticket_id")
               or (d.name.split(" - ")[0] if " - " in d.name else d.name))
        deal_name = prior.get("deal_name") or d.name
        gt = manifest_by_id.get(str(tid), {})
        ground_truth = {"status": gt.get("current_status"), "summary": gt.get("summary_status")}

        if dry_run:
            log.info(f"[pma] (dry-run) {d.name}: {len(new_events)} new event(s) would be synthesized.")
            updated.append(d.name)
            continue

        try:
            brief = synthesize_deal(client, prior, new_events, str(tid), deal_name, ground_truth)
        except Exception as e:
            log.error(f"[pma] Synthesis failed for {d.name}: {e}")
            continue

        brief["last_synthesized"] = datetime.now(timezone.utc).isoformat()
        brief["last_event_synthesized"] = max((e.get("received") or "") for e in events)
        try:
            json_path.write_text(json.dumps(brief, indent=2), encoding="utf-8")
            (d / NEGOTIATION_MD).write_text(_render_negotiation_md(brief), encoding="utf-8")
        except OSError as e:
            log.warning(f"[pma] Could not write brief for {d.name}: {e}")
        updated.append(d.name)
        touched.append((d.name, brief))
        log.info(f"[pma] Synthesized {d.name} ({len(new_events)} new event(s)).")

    general_updated = False
    calibration_updated = False
    if touched and not dry_run:
        general_updated = update_general_knowledge(client, root, touched)
        # Learn how the team writes status/summary by comparing to ground truth.
        calibration_updated = update_status_calibration(client, root, touched)

    result = {"updated": len(updated), "deals": updated, "skipped_no_new": skipped,
              "general_knowledge_updated": general_updated,
              "status_calibration_updated": calibration_updated, "dry_run": dry_run}
    log.info(f"[pma] Knowledge synthesis complete: {result}")
    return result


# =============================================================================
# Entry point: poll
# =============================================================================

def run_pma_poll(
    client, app_token: str, config: dict,
    program_dir: Path, data_dir: Path, dry_run: bool = False,
    backfill_days: int | None = None,
) -> dict:
    """Poll pmateam, classify, log (and write to HubSpot if enabled). One-shot.

    backfill_days: if given, look back that many days regardless of the saved
    cursor (forces a re-pull). On a first run with no cursor and no override,
    the lookback defaults to config['pma_backfill_days'] (60)."""
    mailbox = config.get("pma_mailbox", "pmateam@gallagherllp.com")
    manifest_path = _resolve_pma_file(config, program_dir, "pma_manifest.json")
    instructions_path = _resolve_pma_file(config, program_dir, "pma_instructions.md")
    state_path = data_dir / "state" / "pma_poller_state.json"
    activity_path = data_dir / "pma_activity.jsonl"
    unmatched_path = data_dir / "pma_unmatched_log.jsonl"

    manifest = load_manifest(manifest_path)
    if not manifest:
        return {"error": "no_manifest"}
    manifest_by_id = {str(e.get("ticket_id")): e for e in manifest}
    instructions = load_pma_instructions(instructions_path)

    state = load_pma_state(state_path)
    last = state.get("last_received")
    if backfill_days is not None:
        since = datetime.now(timezone.utc) - timedelta(days=backfill_days)
        log.info(f"[pma] Forced backfill: last {backfill_days} day(s) (ignoring saved cursor).")
    elif last:
        since = datetime.fromisoformat(last.replace("Z", "+00:00"))
    else:
        days = int(config.get("pma_backfill_days", DEFAULT_BACKFILL_DAYS))
        since = datetime.now(timezone.utc) - timedelta(days=days)
        log.info(f"[pma] No state — first run, backfilling last {days} day(s).")

    # HubSpot write posture: master switch (config) AND arm flag (runtime).
    hubspot_enabled = bool(config.get("pma_hubspot_enabled"))
    armed = is_hubspot_armed(data_dir)
    if dry_run:
        log.info("[pma] DRY RUN — no HubSpot writes regardless of config/arm.")
    elif hubspot_enabled and armed:
        log.info("[pma] HubSpot writes ARMED — live writes enabled (master ON + arm flag present).")
    elif hubspot_enabled and not armed:
        log.info("[pma] ASLEEP — pma_hubspot_enabled is true but NOT armed. Run --pma-arm to go live. Logging proposals only.")
    else:
        log.info("[pma] OBSERVE MODE — pma_hubspot_enabled is false; logging proposals only.")

    # Corpus capture (independent of HubSpot dry_run — always builds the brain).
    archive_enabled = config.get("pma_archive_enabled", True)
    root = pma_team_root(config) if archive_enabled else None

    recipient_filter = config.get("pma_recipient_filter", "pmateam@gallagherllp.com")
    # Senders whose forwarded mail counts as PMA review even when the team inbox
    # isn't in To/Cc (e.g. the client forwarding negotiation emails in).
    forwarder_senders = config.get("pma_forwarder_senders", ["pma@bozzuto.com"])
    messages = fetch_pma_messages(app_token, mailbox, since, recipient_filter,
                                  forwarder_senders)
    if not messages:
        return {"processed": 0, "since": since.isoformat()}

    # Volume guard (spec §1): batch large backlogs to avoid Claude rate issues.
    counters = {
        "processed": 0, "unmatched_keyword": 0, "unmatched_lowconf": 0,
        "unmatched_null": 0, "logged_no_change": 0, "proposed": 0,
        "written": 0, "error": 0, "archived": 0,
    }
    BATCH = 10
    newest = since
    for i in range(0, len(messages), BATCH):
        batch = messages[i:i + BATCH]
        for email in batch:
            candidates = match_candidates(email, manifest)
            # Capture into the PMA Team corpus first (deterministic, no Claude).
            if archive_enabled:
                arch = archive_email(email, candidates, app_token, mailbox, root)
                if arch.get("archived"):
                    counters["archived"] += 1
            outcome = process_email(
                email, candidates, manifest, manifest_by_id, instructions, client,
                config, activity_path, unmatched_path, dry_run, armed,
            )
            counters[outcome] = counters.get(outcome, 0) + 1
            counters["processed"] += 1
            # Track newest receivedDateTime for the cursor.
            rdt = email.get("receivedDateTime")
            if rdt:
                try:
                    dt = datetime.fromisoformat(rdt.replace("Z", "+00:00"))
                    if dt > newest:
                        newest = dt
                except ValueError:
                    pass
        if i + BATCH < len(messages):
            time.sleep(2)

    # Advance the cursor and persist any manifest status changes.
    state["last_received"] = newest.strftime("%Y-%m-%dT%H:%M:%SZ")
    save_pma_state(state_path, state)
    if counters["written"]:
        save_manifest(manifest_path, manifest)

    log.info(f"[pma] Poll complete: {counters}")
    return counters


# =============================================================================
# Entry point: daily digest
# =============================================================================

def _read_today_jsonl(path: Path, today: str) -> list[dict]:
    if not path.exists():
        return []
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Match by date field (unmatched log) or timestamp prefix (activity).
            d = obj.get("date") or (obj.get("timestamp") or "")[:10]
            if d == today:
                out.append(obj)
    except OSError as e:
        log.warning(f"[pma] Could not read {path}: {e}")
    return out


def build_digest(unmatched: list[dict], counters: dict, today: str) -> tuple[str, str]:
    """Return (plain_text, html) digest bodies (spec §5 format)."""
    lines = [
        "The following emails were received at pmateam@gallagherllp.com and could "
        "not be confidently matched to an active HubSpot ticket. No HubSpot updates "
        "were made for these items.",
        "",
        "-" * 60,
    ]
    for i, u in enumerate(unmatched, 1):
        lines.append(f"[{i}] {u.get('received') or u.get('date','')} {u.get('time','')}")
        lines.append(f"From: {u.get('from','')}")
        lines.append(f"Subject: {u.get('subject','')}")
        lines.append(f"Reason: {u.get('reason','')} — {u.get('claude_reasoning','')}")
        lines.append("")
    lines.append("-" * 60)
    lines.append(f"Total unmatched today: {len(unmatched)}")
    lines.append(f"Total emails processed today: {counters.get('processed', 0)}")
    lines.append(f"Total HubSpot updates made today: {counters.get('written', 0)}")
    text = "\n".join(lines)

    # Simple HTML version.
    html_items = ""
    for i, u in enumerate(unmatched, 1):
        html_items += (
            f"<p style='margin:0 0 12px'><b>[{i}]</b> {u.get('received') or u.get('date','')} "
            f"{u.get('time','')}<br>"
            f"<b>From:</b> {u.get('from','')}<br>"
            f"<b>Subject:</b> {u.get('subject','')}<br>"
            f"<b>Reason:</b> {u.get('reason','')} — {u.get('claude_reasoning','')}</p>"
        )
    html = f"""<html><body style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#222">
<p>The following emails were received at <b>pmateam@gallagherllp.com</b> and could not be
confidently matched to an active HubSpot ticket. No HubSpot updates were made for these items.</p>
<hr>{html_items}<hr>
<p><b>Total unmatched today:</b> {len(unmatched)}<br>
<b>Total emails processed today:</b> {counters.get('processed', 0)}<br>
<b>Total HubSpot updates made today:</b> {counters.get('written', 0)}</p>
</body></html>"""
    return text, html


def run_pma_digest(
    send_token: str | None, config: dict, data_dir: Path, dry_run: bool = False,
) -> dict:
    """Build the daily unmatched-email digest and email it to Beth + Kyle."""
    from outbound import send_mail_guarded

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    unmatched = _read_today_jsonl(data_dir / "pma_unmatched_log.jsonl", today)
    activity = _read_today_jsonl(data_dir / "pma_activity.jsonl", today)
    counters = {
        "processed": len(activity),
        "written": sum(1 for a in activity if a.get("outcome") == "written"),
    }

    text, html = build_digest(unmatched, counters, today)
    recipients = config.get(
        "pma_digest_recipients",
        ["bcrassweller@gallagherllp.com", "kvirtue@gallagherllp.com"],
    )
    rocky_email = config.get("rocky_email", "rocky@gallagherllp.com")
    subject = f"PMA Monitor — Daily Unmatched Email Digest {today}"

    # Always write a copy to disk for the record / dry-run inspection.
    out_path = data_dir / f"pma_digest_{today}.txt"
    try:
        out_path.write_text(text, encoding="utf-8")
    except OSError as e:
        log.warning(f"[pma] Could not write digest file: {e}")

    if dry_run or not send_token:
        log.info(f"[pma] Digest NOT sent ({'dry-run' if dry_run else 'no token'}); "
                 f"wrote {out_path}. {len(unmatched)} unmatched, {counters['processed']} processed.")
        return {"sent": False, "reason": "dry_run" if dry_run else "no_token",
                "unmatched": len(unmatched), "path": str(out_path)}

    result = send_mail_guarded(
        token=send_token, sender_mailbox=rocky_email, to=recipients,
        subject=subject, body=html, body_type="HTML",
    )
    if result.get("sent"):
        log.info(f"[pma] Digest emailed to {recipients} ({len(unmatched)} unmatched).")
    else:
        log.warning(f"[pma] Digest send failed: {result.get('reason')}")
    return {"sent": result.get("sent", False), "unmatched": len(unmatched),
            "recipients": recipients, **result}


# =============================================================================
# PMA Activity feed — raw email export for the Maple Updater Agent
# =============================================================================
# A deliberately simple, Claude-free exporter. It pulls new emails from rocky@'s
# "Inbox\PMA emails" folder and appends each one (full body + extracted
# attachment text) as a single JSONL line to a feed file in Maple's folder on
# OneDrive. Rocky does NOT classify, match tickets, or recommend changes here —
# the Maple Updater Agent reads the feed and does all of that itself.

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
    the scope. Mirrors fetch_pma_messages but folder-scoped. Returns [] on error."""
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
