"""
Rocky kill switch.

If Rocky's behavior goes sideways and James can't get to the production
laptop, he can email the magic phrase from any device and Rocky goes
dormant within one poll cycle.

Magic phrases (in subject line):
    "ROCKY STOP"   → enter dormant mode
    "ROCKY START"  → exit dormant mode

Authorized senders are configurable via config.json key
'kill_switch_authorized' (list of email addresses). Default: just James's
mailbox. Emails from anyone else are silently ignored — never let an
external sender control Rocky's state.

When dormant, Rocky still POLLS the inbox (so she can wake on ROCKY START),
but she:
- Skips classification
- Skips drafting
- Skips case-folder ingestion (Phase D Stage 1)
- Skips folder-update and digest skills if invoked

The dormant flag is a file at state/dormant.flag. Manual delete also wakes
her — so if email auth is broken too, James can RDP into the production
laptop and remove the file by hand.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("rocky")

STOP_PATTERN = "ROCKY STOP"
START_PATTERN = "ROCKY START"


def dormant_flag_path(state_dir: Path) -> Path:
    return state_dir / "dormant.flag"


def is_dormant(state_dir: Path) -> bool:
    return dormant_flag_path(state_dir).exists()


def set_dormant(state_dir: Path, reason: str) -> None:
    state_dir.mkdir(exist_ok=True)
    dormant_flag_path(state_dir).write_text(
        json.dumps(
            {"reason": reason, "timestamp": datetime.now(timezone.utc).isoformat()},
            indent=2,
        ),
        encoding="utf-8",
    )
    log.warning(f"DORMANT MODE ACTIVATED. Reason: {reason}")


def clear_dormant(state_dir: Path) -> None:
    flag = dormant_flag_path(state_dir)
    if flag.exists():
        flag.unlink()
        log.info("Dormant mode cleared. Rocky resuming normal operation.")


def check_emails_for_kill_switch(
    state_dir: Path,
    emails: list[dict],
    authorized_senders: list[str],
) -> bool:
    """
    Scan a batch of emails for STOP/START commands.

    Returns True if dormant state changed (caller may want to log/alert).
    Returns False if no state change (either no command or command from
    unauthorized sender).

    Both STOP and START are case-insensitive substring matches on the subject.
    Sender must match (case-insensitive) one of the authorized addresses.
    """
    auth_lower = {(s or "").lower() for s in authorized_senders if s}
    if not auth_lower:
        log.warning("Kill switch has no authorized senders configured. Disabled.")
        return False

    state_changed = False
    for email in emails:
        subject = (email.get("subject") or "").upper()
        sender = (
            (email.get("from") or {}).get("emailAddress", {}).get("address") or ""
        ).lower()
        if sender not in auth_lower:
            continue

        if STOP_PATTERN in subject:
            if not is_dormant(state_dir):
                set_dormant(
                    state_dir,
                    f"ROCKY STOP from {sender} at "
                    f"{email.get('receivedDateTime', '?')}",
                )
                state_changed = True
        elif START_PATTERN in subject:
            if is_dormant(state_dir):
                clear_dormant(state_dir)
                state_changed = True

    return state_changed
