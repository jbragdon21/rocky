"""
Startup permissions self-audit.

Decodes Rocky's Microsoft Graph access token (without verifying the signature
— we just want to inspect the scope claim) and halts the program if any
forbidden scope is present.

Rocky is allowed Mail.Send (she sends digests from rocky@gallagherllp.com),
but must never hold Mail.Send.Shared or Mail.Send.All (which would let her
send as other people). The outbound.py guardrail additionally restricts all
recipients to @gallagherllp.com.

This is defense in depth alongside:
- The Azure AD app registration (delegated permissions deliberately limited)
- The Exchange Online mail-flow rule (rejects outbound from Rocky to non-firm
  addresses)
- outbound.py send_mail_guarded() — refuses non-firm recipients in code
"""

import base64
import json
import logging
import sys

log = logging.getLogger("rocky")

# Scopes Rocky must NEVER hold. If any of these appear in the token, halt.
# Mail.Send is allowed (Rocky sends from her own mailbox).
# Mail.Send.Shared / Mail.Send.All are forbidden (would let Rocky send as
# other users).
FORBIDDEN_SCOPES: frozenset[str] = frozenset({
    "Mail.Send.Shared",
    "Mail.Send.All",
})


def _decode_jwt_payload(token: str) -> dict:
    """Decode the unvalidated payload of a JWT. Caller must not trust the
    return value for security decisions other than 'is X in this list' — we
    don't verify the signature. Microsoft signed it; we just want the claims.
    """
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError("token is not a JWT")
    payload_b64 = parts[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)  # pad to multiple of 4
    return json.loads(base64.urlsafe_b64decode(payload_b64))


def audit_token_scopes(access_token: str, halt_on_failure: bool = True) -> dict:
    """
    Inspect the access token's scope claim. Halt the program if forbidden
    scopes are present.

    Returns a dict: {audited, scopes, forbidden_present, user}.

    Set halt_on_failure=False for testing (callers that want to inspect the
    result without sys.exit).
    """
    try:
        payload = _decode_jwt_payload(access_token)
    except Exception as e:
        log.warning(f"Permissions audit could not decode token: {e}. Skipping check.")
        return {"audited": False, "reason": str(e)}

    scope_str = payload.get("scp") or ""
    scopes = set(scope_str.split())
    forbidden_present = sorted(scopes & FORBIDDEN_SCOPES)

    result = {
        "audited": True,
        "scopes": sorted(scopes),
        "forbidden_present": forbidden_present,
        "user": payload.get("upn") or payload.get("preferred_username"),
    }

    if forbidden_present:
        log.error(
            f"PERMISSIONS AUDIT FAILED. Token contains forbidden scope(s): "
            f"{forbidden_present}. Rocky must never hold these. Halting."
        )
        if halt_on_failure:
            sys.exit(2)
        return result

    log.info(
        f"Permissions audit passed. User: {result['user']}. "
        f"Scopes: {result['scopes']}"
    )
    return result
