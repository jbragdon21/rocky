"""
Allowlist-guarded outbound mail.

ALL Rocky-originated outbound mail must go through send_mail_guarded(). The
function refuses to send to any recipient outside the configured allowed
domain (default: gallagherllp.com), and refuses to send from any non-firm
mailbox.

This is the IN-CODE guard. The other layer is the Exchange Online mail-flow
rule that blocks Rocky's outbound to non-firm addresses at the tenant level.
Both must agree.

Rocky sends from rocky@gallagherllp.com (her own mailbox). All Rocky-originated
outbound mail MUST go through send_mail_guarded(). Do NOT add a non-guarded
send path elsewhere.
"""

import logging
import re

import requests

log = logging.getLogger("rocky")

ALLOWED_DOMAIN = "gallagherllp.com"
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

_DOMAIN_PATTERN = re.compile(r"@([^@\s]+)\s*$")


def is_allowed_recipient(email: str) -> bool:
    """True iff the email address ends in @gallagherllp.com (case-insensitive)."""
    if not email:
        return False
    match = _DOMAIN_PATTERN.search(email.strip().lower())
    if not match:
        return False
    return match.group(1) == ALLOWED_DOMAIN.lower()


def is_firm_sender(mailbox: str) -> bool:
    """True iff the sending mailbox is on the firm domain."""
    return is_allowed_recipient(mailbox)


def send_mail_guarded(
    token: str,
    sender_mailbox: str,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    body_type: str = "Text",
    attachments: list[dict] | None = None,
) -> dict:
    """
    Send mail FROM sender_mailbox TO `to` (cc optional). Refuses if any
    recipient or the sender is outside the firm domain.

    attachments: list of {"name": str, "path": str} dicts. Each file is
    base64-encoded and sent as a Graph API file attachment.

    Returns:
        {sent: True, "message_id": ...} on success
        {sent: False, reason: <str>, blocked: <list>} on guard failure
        {sent: False, reason: "graph_error_<status>"} on Graph API failure
    """
    cc = cc or []
    all_recipients = list(to) + list(cc)

    if not is_firm_sender(sender_mailbox):
        log.error(
            f"OUTBOUND BLOCKED: sender mailbox {sender_mailbox!r} is not on "
            f"the firm domain @{ALLOWED_DOMAIN}."
        )
        return {"sent": False, "reason": "sender_not_firm", "blocked": [sender_mailbox]}

    blocked = [r for r in all_recipients if not is_allowed_recipient(r)]
    if blocked:
        log.error(
            f"OUTBOUND BLOCKED: recipient(s) {blocked} not in allowed domain "
            f"@{ALLOWED_DOMAIN}. Send refused."
        )
        return {"sent": False, "reason": "recipient_not_allowed", "blocked": blocked}

    if not to:
        return {"sent": False, "reason": "no_recipients", "blocked": []}

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": body_type, "content": body},
            "toRecipients": [{"emailAddress": {"address": r}} for r in to],
        },
        "saveToSentItems": True,
    }
    if cc:
        payload["message"]["ccRecipients"] = [{"emailAddress": {"address": r}} for r in cc]

    if attachments:
        import base64
        from pathlib import Path
        graph_atts = []
        for att in attachments:
            file_path = Path(att["path"])
            if not file_path.exists():
                log.warning(f"Attachment not found, skipping: {file_path}")
                continue
            content_bytes = base64.b64encode(file_path.read_bytes()).decode("ascii")
            att_obj: dict = {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": att.get("name") or file_path.name,
                "contentBytes": content_bytes,
            }
            if att.get("contentId"):
                att_obj["contentId"] = att["contentId"]
                att_obj["isInline"] = True
            graph_atts.append(att_obj)
        if graph_atts:
            payload["message"]["attachments"] = graph_atts

    url = f"{GRAPH_API_BASE}/users/{sender_mailbox}/sendMail"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
    except requests.RequestException as e:
        log.error(f"Outbound network error: {e}")
        return {"sent": False, "reason": f"network_error: {e}"}

    if response.status_code in (200, 202):
        log.info(f"Sent mail from {sender_mailbox} to {to}: {subject[:60]!r}")
        return {"sent": True, "status_code": response.status_code}

    log.error(
        f"Outbound Graph API error {response.status_code}: "
        f"{response.text[:300]}"
    )
    return {
        "sent": False,
        "reason": f"graph_error_{response.status_code}",
        "response_body": response.text[:300],
    }
