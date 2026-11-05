"""Graph access to Joe's mailbox: tokens, paging and MIME downloads.

Read only. Nothing here POSTs, PATCHes or DELETEs.

Two grants can reach a colleague's mailbox (JOE_ARCHIVE_DESIGN.md section 1),
so there are three ways to try for a token, safest first:

    app             client credentials, Mail.Read (application). Needs the
                    mailbox in the Exchange Application Access Policy group.
    delegated-read  rocky@'s cached sign-in with Mail.Read.Shared. Needs
                    rocky@ Full Access on the mailbox, plus that scope's
                    consent.
    delegated       rocky@'s cached sign-in with Mail.ReadWrite.Shared
                    (consented 2026-07-05; Minotaur and Inbox Cleaner use
                    it). Same Full Access grant. Rocky still only reads.
"""

import logging
import os
import threading
import time
from pathlib import Path

import requests

log = logging.getLogger("rocky")

GRAPH = "https://graph.microsoft.com/v1.0"
PAGE_SIZE = 50
REFRESH_AFTER = 45 * 60          # tokens live ~60-75 min; a backfill runs for hours
MESSAGE_CLASS = "String 0x001A"  # PR_MESSAGE_CLASS: IPM.Note, REPORT.IPM.Note.IPNRN, ...

LIST_SELECT = ("id,internetMessageId,conversationId,subject,from,toRecipients,"
               "ccRecipients,bccRecipients,sentDateTime,hasAttachments,body")
LIST_EXPAND = f"singleValueExtendedProperties($filter=id eq '{MESSAGE_CLASS}')"
TEXT_BODY = {"Prefer": 'outlook.body-content-type="text"'}

AUTH_ORDER = ("app", "delegated-read", "delegated")
_DELEGATED_SCOPES = {"delegated-read": ["Mail.Read.Shared"],
                     "delegated": ["Mail.ReadWrite.Shared"]}


class GraphError(RuntimeError):
    def __init__(self, status: int | None, detail: str):
        super().__init__(f"Graph {status}: {detail}" if status else detail)
        self.status = status
        self.detail = detail


# =============================================================================
# Tokens
# =============================================================================

def token_for(config: dict, path: str) -> tuple[str | None, str]:
    """(token, note) for one auth path. A None token comes with the reason."""
    if path == "app":
        secret = config.get("client_secret")
        if not secret:
            return None, "no client_secret in config"
        import msal  # lazy
        app = msal.ConfidentialClientApplication(
            client_id=config["client_id"], client_credential=secret,
            authority=f"https://login.microsoftonline.com/{config['tenant_id']}")
        result = app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"])
    elif path in _DELEGATED_SCOPES:
        from rocky import get_msal_app  # lazy, as inbox_cleaner does
        app = get_msal_app(config)
        accounts = app.get_accounts()
        if not accounts:
            return None, "no cached rocky@ sign-in on this machine"
        result = app.acquire_token_silent(_DELEGATED_SCOPES[path],
                                          account=accounts[0])
        if hasattr(app, "_save_cache"):
            app._save_cache()
    else:
        return None, f"unknown auth path {path!r}"
    if result and result.get("access_token"):
        return result["access_token"], "token acquired"
    detail = (result or {}).get("error_description") or (result or {}).get("error") \
        or "no token returned"
    return None, str(detail).splitlines()[0][:200]


class TokenKeeper:
    """Hands out a token for one auth path and refreshes it before it expires
    (or right away after a 401)."""

    def __init__(self, config: dict, path: str, token: str | None = None):
        self.config = config
        self.path = path
        self._token = token
        self._acquired = time.monotonic() if token else 0.0
        self._lock = threading.Lock()   # the MIME downloads share one keeper

    def get(self) -> str:
        with self._lock:
            if not self._token or time.monotonic() - self._acquired > REFRESH_AFTER:
                self._refresh()
            return self._token

    def refresh(self) -> None:
        with self._lock:
            self._refresh()

    def _refresh(self) -> None:
        token, note = token_for(self.config, self.path)
        if not token:
            raise GraphError(None, f"could not get a {self.path} token: {note}")
        if self._token:
            log.info(f"[joe-archive] refreshed the {self.path} token")
        self._token = token
        self._acquired = time.monotonic()


# =============================================================================
# Requests
# =============================================================================

def get(keeper: TokenKeeper, url: str, params: dict | None = None,
        headers: dict | None = None, stream: bool = False,
        max_retries: int = 6) -> requests.Response:
    """GET with backoff on throttling, server errors and network drops, and
    one token refresh on a 401. Returns the last response (any status);
    raises GraphError only when no response came back at all."""
    delay = 2.0
    refreshed = False
    for attempt in range(max_retries):
        h = {"Authorization": f"Bearer {keeper.get()}", "Accept": "application/json"}
        h.update(headers or {})
        try:
            resp = requests.get(url, headers=h, params=params, timeout=120,
                                stream=stream)
        except requests.RequestException as e:
            log.warning(f"[joe-archive] network error ({attempt + 1}/{max_retries}): {e}")
            time.sleep(delay)
            delay = min(delay * 2, 60)
            continue
        if resp.status_code == 401 and not refreshed:
            refreshed = True
            keeper.refresh()
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            wait = resp.headers.get("Retry-After")
            wait = float(wait) if wait else delay
            log.info(f"[joe-archive] throttled ({resp.status_code}); sleeping {wait:.0f}s")
            resp.close()
            time.sleep(wait)
            delay = min(delay * 2, 60)
            continue
        return resp
    raise GraphError(None, f"no response after {max_retries} tries: {url}")


def error_detail(resp: requests.Response) -> str:
    """'ErrorAccessDenied: Access is denied...' from a Graph error body."""
    try:
        err = resp.json().get("error") or {}
        return f"{err.get('code', '')}: {err.get('message', '')}".strip(": ")[:300]
    except ValueError:
        return (resp.text or "")[:300]


def get_json(keeper: TokenKeeper, url: str, params: dict | None = None,
             headers: dict | None = None) -> dict:
    resp = get(keeper, url, params=params, headers=headers)
    if resp.status_code != 200:
        raise GraphError(resp.status_code, error_detail(resp))
    return resp.json()


def folder(keeper: TokenKeeper, mailbox: str, folder_id: str) -> dict:
    return get_json(keeper, f"{GRAPH}/users/{mailbox}/mailFolders/{folder_id}")


def iter_pages(keeper: TokenKeeper, mailbox: str, folder_id: str = "sentitems",
               since: str | None = None, top: int = PAGE_SIZE, max_pages: int | None = None):
    """Yield pages (lists) of messages in a folder, oldest first, with the
    text body and the message class. `since` (ISO UTC) keeps sends at or
    after it; reruns overlap on purpose and the archive dedups."""
    url = f"{GRAPH}/users/{mailbox}/mailFolders/{folder_id}/messages"
    params = {"$top": str(top), "$select": LIST_SELECT, "$expand": LIST_EXPAND,
              "$orderby": "sentDateTime asc"}
    if since:
        params["$filter"] = f"sentDateTime ge {since}"
    next_url, pages = None, 0
    while pages == 0 or next_url:
        data = get_json(keeper, next_url or url, None if next_url else params, TEXT_BODY)
        batch = data.get("value") or []
        if batch:
            yield batch
        next_url = data.get("@odata.nextLink")
        pages += 1
        if max_pages and pages >= max_pages:
            return


INDEX_SELECT = "id,internetMessageId,sentDateTime"
INDEX_PAGE = 500


def iter_index(keeper: TokenKeeper, mailbox: str, folder_id: str,
               from_addrs: list[str] | None = None, top: int = INDEX_PAGE):
    """Yield pages of one folder's messages with only what the census needs:
    ids, sent date and message class, no body. from_addrs keeps only mail
    from those addresses (Joe's, in folders other than Sent Items)."""
    url = f"{GRAPH}/users/{mailbox}/mailFolders/{folder_id}/messages"
    params = {"$top": str(top), "$select": INDEX_SELECT, "$expand": LIST_EXPAND}
    if from_addrs:
        params["$filter"] = " or ".join(f"from/emailAddress/address eq '{a}'"
                                        for a in from_addrs)
    next_url = None
    while True:
        data = get_json(keeper, next_url or url, None if next_url else params)
        batch = data.get("value") or []
        if batch:
            yield batch
        next_url = data.get("@odata.nextLink")
        if not next_url:
            return


def message(keeper: TokenKeeper, mailbox: str, graph_id: str) -> dict | None:
    """One message in the listing's shape, or None if it's gone (404)."""
    resp = get(keeper, f"{GRAPH}/users/{mailbox}/messages/{graph_id}",
               params={"$select": LIST_SELECT, "$expand": LIST_EXPAND},
               headers=TEXT_BODY)
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise GraphError(resp.status_code, error_detail(resp))
    return resp.json()


def prop_tag(prop_id: str | None) -> tuple[str, int] | None:
    """('string', 0x1a) for 'String 0x001A' or 'String 0x1a', else None.

    Graph answers a tagged property in its own short form ('String 0x1a')
    whatever form the request used, so compare the parsed tag, not the text.
    The 11/5 --check read every class as blank on the text compare."""
    parts = (prop_id or "").split()
    if len(parts) != 2 or not parts[1].lower().startswith("0x"):
        return None
    try:
        return parts[0].lower(), int(parts[1], 16)
    except ValueError:
        return None


_CLASS_TAG = prop_tag(MESSAGE_CLASS)


def message_class(msg: dict) -> str:
    for prop in msg.get("singleValueExtendedProperties") or []:
        if prop_tag(prop.get("id")) == _CLASS_TAG:
            return prop.get("value") or ""
    return ""


def download_mime(keeper: TokenKeeper, mailbox: str, graph_id: str, dest: Path) -> int:
    """Stream one message's full MIME (.eml, attachments included) to dest.
    Writes to a .part file first so a crash never leaves a short .eml."""
    resp = get(keeper, f"{GRAPH}/users/{mailbox}/messages/{graph_id}/$value",
               stream=True)
    if resp.status_code != 200:
        detail = error_detail(resp)
        resp.close()
        raise GraphError(resp.status_code, detail)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    size = 0
    try:
        with open(part, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=256 * 1024):
                if chunk:
                    fh.write(chunk)
                    size += len(chunk)
        os.replace(part, dest)
    finally:
        resp.close()
        if part.exists():
            part.unlink()
    return size
