"""
Teams chat transport — Rocky talks to firm users over Microsoft Teams.

Rocky signs in as rocky@gallagherllp.com (the same delegated device-code
identity used for mail) and chats 1:1 with a firm user. Microsoft does not
support sending Teams chat messages with application (client-credential)
tokens, so this module is delegated-only.

Required delegated Graph scopes (Azure app registration "Rocky" + one-time
device-code consent as rocky@): Chat.Create, Chat.ReadWrite, ChatMessage.Send.
Until IT adds those permissions, acquire_teams_token() will fail with a
consent error — every caller should treat that as "Teams not enabled yet".

First consumer: the Inbox Cleaner (inbox_cleaner.py) cohort-approval loop.
The design rules from BUILD_REFERENCE apply to every consumer:
  - propose one thing at a time so a bare "yes" is unambiguous;
  - only the counterparty's replies count;
  - a chat reply can approve/decline what Rocky proposed, never command
    new actions.

This module is transport only — no interpretation. Callers log every
message sent/received via their own communications log; helpers here
return full Graph message objects so nothing is lost.
"""

from __future__ import annotations

import logging
import re
import time

import requests

log = logging.getLogger("rocky.teams")

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

# Delegated scopes for Teams chat. Kept separate from rocky.py's GRAPH_SCOPES
# so mail commands never trigger a consent prompt for chat permissions.
TEAMS_SCOPES = ["Chat.Create", "Chat.ReadWrite", "ChatMessage.Send"]


class TeamsNotEnabled(RuntimeError):
    """Teams scopes not yet consented / granted on the app registration."""


# =============================================================================
# Token
# =============================================================================

def acquire_teams_token(app) -> str:
    """Get a delegated token carrying the Teams chat scopes.

    `app` is the MSAL PublicClientApplication built by rocky.get_msal_app()
    (shared token cache — one rocky@ sign-in covers mail and chat once the
    scopes are consented). Raises TeamsNotEnabled when consent is missing so
    callers can degrade gracefully instead of crashing a scheduled run.
    """
    accounts = app.get_accounts()
    result = None
    if accounts:
        result = app.acquire_token_silent(TEAMS_SCOPES, account=accounts[0])

    if not result:
        flow = app.initiate_device_flow(scopes=TEAMS_SCOPES)
        if "user_code" not in flow:
            raise TeamsNotEnabled(
                f"Could not start device flow for Teams scopes: "
                f"{flow.get('error_description', flow)}"
            )
        print("\n" + "=" * 60)
        print("Teams chat needs a one-time consent as rocky@:")
        print(flow["message"])
        print("=" * 60 + "\n")
        result = app.acquire_token_by_device_flow(flow)

    if hasattr(app, "_save_cache"):
        app._save_cache()

    if "access_token" not in result:
        raise TeamsNotEnabled(
            f"Teams token acquisition failed (scopes likely not granted "
            f"on the app registration yet): "
            f"{result.get('error_description', result)}"
        )
    return result["access_token"]


# =============================================================================
# Throttle-aware Graph call
# =============================================================================

def _graph_call(method: str, url: str, token: str, *, params: dict | None = None,
                payload: dict | None = None, max_retries: int = 5):
    """requests call with 429/5xx backoff. Returns Response or None."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    delay = 2.0
    for attempt in range(max_retries):
        try:
            resp = requests.request(method, url, headers=headers, params=params,
                                    json=payload, timeout=30)
        except requests.RequestException as e:
            log.warning(f"[teams] network error ({attempt + 1}/{max_retries}): {e}")
            time.sleep(delay)
            delay = min(delay * 2, 60)
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after else delay
            log.info(f"[teams] throttled ({resp.status_code}); sleeping {wait:.0f}s")
            time.sleep(wait)
            delay = min(delay * 2, 60)
            continue
        return resp
    return None


# =============================================================================
# Identity / chat helpers
# =============================================================================

def get_self_user_id(token: str) -> str | None:
    """AAD object id of the signed-in identity (rocky@). Used to tell
    Rocky's own chat messages apart from the counterparty's."""
    resp = _graph_call("GET", f"{GRAPH_API_BASE}/me", token,
                       params={"$select": "id,userPrincipalName"})
    if resp is None or resp.status_code != 200:
        log.warning(f"[teams] /me failed: "
                    f"{resp.status_code if resp is not None else 'no response'}")
        return None
    return resp.json().get("id")


# The firm renamed gejlaw.com -> gallagherllp.com, but Azure UPNs (sign-in
# names) still carry the legacy domain while mail flows on the new one
# (rocky@'s own UPN is rocky@gejlaw.com — verified 2026-07-11). Chat member
# binds need UPNs, so when Graph reports users not found we retry once with
# the legacy domain swapped for exactly the members it named. Works in both
# directions, so nothing breaks if IT later flips UPNs to the new domain.
_ALT_DOMAINS = {"gallagherllp.com": "gejlaw.com",
                "gejlaw.com": "gallagherllp.com"}


def swap_legacy_domain(upn: str) -> str:
    """rocky@gallagherllp.com <-> rocky@gejlaw.com (other domains as-is)."""
    local, _, dom = (upn or "").partition("@")
    alt = _ALT_DOMAINS.get(dom.lower())
    return f"{local}@{alt}" if alt else upn


def _failed_upns(resp) -> set[str]:
    """UPNs Graph aggregated into a 'Failed to find users ...' message."""
    try:
        msg = resp.json()["error"]["message"]
    except Exception:
        return set()
    m = re.search(r"user principal name '([^']*)'", msg)
    if not m:
        return set()
    return {u.strip().lower() for u in m.group(1).split(",") if u.strip()}


def _chat_members(upns: list[str]) -> list[dict]:
    return [
        {
            "@odata.type": "#microsoft.graph.aadUserConversationMember",
            "roles": ["owner"],
            "user@odata.bind": f"{GRAPH_API_BASE}/users('{upn}')",
        }
        for upn in upns
    ]


def _create_chat(token: str, payload: dict, upns: list[str],
                 what: str) -> str | None:
    """POST /chats; on a members-not-found 404, retry once with the legacy
    domain swapped on exactly the members Graph named."""
    resp = _graph_call("POST", f"{GRAPH_API_BASE}/chats", token,
                       payload={**payload, "members": _chat_members(upns)})
    if resp is not None and resp.status_code == 404:
        failed = _failed_upns(resp)
        retry = [swap_legacy_domain(u) if u.lower() in failed else u
                 for u in upns]
        if failed and retry != list(upns):
            log.info(f"[teams] {what}: users not found, retrying with "
                     f"legacy-domain UPNs for {sorted(failed)}")
            resp = _graph_call(
                "POST", f"{GRAPH_API_BASE}/chats", token,
                payload={**payload, "members": _chat_members(retry)})
    if resp is None or resp.status_code not in (200, 201):
        log.error(f"[teams] {what} failed: "
                  f"{resp.status_code if resp is not None else 'no response'} "
                  f"{resp.text[:300] if resp is not None else ''}")
        return None
    return resp.json().get("id")


def ensure_one_on_one_chat(token: str, self_upn: str, other_upn: str) -> str | None:
    """Create (or fetch — Graph returns the existing chat for the same pair)
    the 1:1 chat between rocky@ and `other_upn`. Returns the chat id."""
    return _create_chat(token, {"chatType": "oneOnOne"},
                        [self_upn, other_upn],
                        f"create/fetch chat with {other_upn}")


def ensure_group_chat(token: str, topic: str, member_upns: list[str]) -> str | None:
    """Create a group chat (Rocky + counterparty + observers). UNLIKE
    oneOnOne, POSTing a group chat creates a NEW chat every time — callers
    MUST persist the returned id and never call this twice for the same
    process."""
    return _create_chat(token, {"chatType": "group", "topic": topic},
                        member_upns, f"create group chat {topic!r}")


def list_chat_members(token: str, chat_id: str) -> list[dict]:
    """Members of a chat: [{userId, email, displayName}]. Lets callers map
    message sender ids to people (e.g. 'only the mailbox owner's replies
    count as approvals')."""
    resp = _graph_call("GET", f"{GRAPH_API_BASE}/chats/{chat_id}/members", token)
    if resp is None or resp.status_code != 200:
        log.warning(f"[teams] list members for chat {chat_id} failed: "
                    f"{resp.status_code if resp is not None else 'no response'}")
        return []
    return [{"userId": m.get("userId"),
             "email": (m.get("email") or "").lower(),
             "displayName": m.get("displayName")}
            for m in resp.json().get("value", [])]


def send_chat_message(token: str, chat_id: str, content: str) -> dict | None:
    """Send a plain-text message. Returns the created Graph message object
    (callers log it + keep the id for reply correlation), or None."""
    payload = {"body": {"contentType": "text", "content": content}}
    resp = _graph_call("POST", f"{GRAPH_API_BASE}/chats/{chat_id}/messages",
                       token, payload=payload)
    if resp is None or resp.status_code not in (200, 201):
        log.error(f"[teams] send to chat {chat_id} failed: "
                  f"{resp.status_code if resp is not None else 'no response'} "
                  f"{resp.text[:300] if resp is not None else ''}")
        return None
    return resp.json()


def fetch_chat_messages(token: str, chat_id: str, since_iso: str | None = None,
                        page_cap: int = 4) -> list[dict]:
    """Recent messages in the chat, oldest-first, optionally only those
    created after `since_iso`. Pages newest-first from Graph (50/page,
    capped) and filters client-side — chat volume is tiny, so a few pages
    always covers the window between poll cycles."""
    url = f"{GRAPH_API_BASE}/chats/{chat_id}/messages"
    params: dict | None = {"$top": "50"}
    out: list[dict] = []
    pages = 0
    next_url: str | None = None
    while pages == 0 or (next_url and pages < page_cap):
        resp = _graph_call("GET", next_url or url, token,
                           params=None if next_url else params)
        if resp is None or resp.status_code != 200:
            log.warning(f"[teams] fetch messages for chat {chat_id} failed: "
                        f"{resp.status_code if resp is not None else 'no response'}")
            break
        data = resp.json()
        batch = data.get("value", [])
        out.extend(batch)
        # Graph returns newest-first; stop paging once we're past the cursor.
        if since_iso and batch and (batch[-1].get("createdDateTime") or "") <= since_iso:
            break
        next_url = data.get("@odata.nextLink")
        pages += 1

    if since_iso:
        out = [m for m in out if (m.get("createdDateTime") or "") > since_iso]
    out.sort(key=lambda m: m.get("createdDateTime") or "")
    return out


def message_text(msg: dict) -> str:
    """Plain text of a chat message body (strips the HTML Teams wraps
    around even 'text' replies typed in the client)."""
    body = (msg.get("body") or {}).get("content") or ""
    if (msg.get("body") or {}).get("contentType") == "html":
        import re
        body = re.sub(r"<[^>]+>", " ", body)
    return " ".join(body.split()).strip()


def message_sender_id(msg: dict) -> str | None:
    """AAD user id of the sender (None for system/app messages)."""
    return (((msg.get("from") or {}).get("user")) or {}).get("id")
