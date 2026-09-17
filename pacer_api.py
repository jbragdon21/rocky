"""
PACER direct API client — authentication + the PACER Case Locator (PCL)
=======================================================================

Two services, both run by the Administrative Office of the U.S. Courts:

  * **Authentication** (`/services/cso-auth` on pacer.login.uscourts.gov)
    trades a PACER username and password for a 128-character nextGenCSO
    token. The token is good for an extended period and is periodically
    re-issued in the `X-NEXT-GEN-CSO` response header of later calls, so
    this client caches it on disk and picks up re-issues automatically.
    Calling cso-auth on every search is explicitly discouraged by the AO.

  * **PCL** (`/pcl-public-api/rest/...` on pcl.uscourts.gov) is the
    nationwide *index* of federal cases — case metadata and party rows
    across district, bankruptcy, appellate, and JPML. It is NOT docket
    contents: there are no docket entries and no documents here. What it
    is good for is discovery, which is exactly the thing RECAP cannot do:
    "tell me every federal case filed anywhere that names this client."

**PCL searches are billable in production.** Every response carries a
`receipt` with `billablePages` and `searchFee`, and results come back 54
to a page with each page billed as it is retrieved. This client returns
the receipt untouched so pacer_monitor.py can hold it against the daily
spend cap. There is a free QA environment (qa-pcl.uscourts.gov) with test
data and a separate QA account — set pacer_environment to "qa" to use it.

There is no public API for CM/ECF docket contents. Getting an actual
docket out of PACER programmatically means RECAP Fetch (see
courtlistener.py), which drives PACER with these same credentials.

Docs: PCL API User Guide (Dec 2022) and PACER Authentication API User
Guide (Nov 2021), both at pacer.uscourts.gov/file-case/developer-resources.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import struct
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

log = logging.getLogger("rocky.pacer_api")

ENVIRONMENTS = {
    "production": {
        "auth": "https://pacer.login.uscourts.gov",
        "pcl": "https://pcl.uscourts.gov",
        "billable": True,
    },
    "qa": {
        "auth": "https://qa-login.uscourts.gov",
        "pcl": "https://qa-pcl.uscourts.gov",
        "billable": False,
    },
}

PCL_PATH = "/pcl-public-api/rest"
PAGE_SIZE = 54          # fixed by the API; also the billing unit
_TIMEOUT = 90

# The token survives a long time but not forever, and the guide is vague
# about how long. Re-authenticate after this many hours rather than
# discovering expiry mid-sweep.
TOKEN_MAX_AGE_HOURS = 8

JURISDICTION_TYPES = {"ap", "bk", "cr", "cv", "mdl"}


TOTP_PERIOD = 30
TOTP_DIGITS = 6


class PacerError(RuntimeError):
    """Any PACER/PCL failure that isn't worth retrying in this run."""


class PacerAuthError(PacerError):
    """Bad credentials, locked account, or a client code the account needs."""


# =============================================================================
# Multifactor authentication
# =============================================================================
# PACER made MFA mandatory for CM/ECF-level access through 2025, and the
# auth API grew an `otpCode` field to match (guide v.2, April 2025). The
# codes are ordinary TOTP: 6 digits, 30-second window, from a base32 secret
# PACER shows in Manage My Account when you enrol. An authenticator app is
# one holder of that secret; this is another. Rocky can't read a code off
# somebody's phone, so unattended access needs the secret itself.
#
# Implemented on the standard library rather than pulling in pyotp — it's
# twenty lines of RFC 6238, and it keeps the PyInstaller bundle unchanged.

def totp_now(secret_base32: str, at: float | None = None,
             digits: int = TOTP_DIGITS, period: int = TOTP_PERIOD) -> str:
    """Current TOTP code for a base32 secret (RFC 6238, SHA-1)."""
    cleaned = re.sub(r"[\s-]+", "", secret_base32 or "").upper()
    if not cleaned:
        raise PacerAuthError("empty PACER OTP secret")
    cleaned += "=" * (-len(cleaned) % 8)      # base32 wants a multiple of 8
    try:
        key = base64.b32decode(cleaned, casefold=True)
    except (ValueError, TypeError) as e:
        raise PacerAuthError(
            "pacer_otp_secret is not valid base32. Copy the secret key "
            "string PACER shows beside the QR code in Manage My Account, "
            "not the QR image or a generated 6-digit code.") from e
    counter = int((at if at is not None else time.time()) // period)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def seconds_until_next_totp(period: int = TOTP_PERIOD) -> float:
    """How long until the current code rolls over."""
    return period - (time.time() % period)


def _looks_like_otp_failure(text: str) -> bool:
    lowered = (text or "").lower()
    return "passcode" in lowered or "one-time" in lowered or "otp" in lowered


def _looks_like_redaction_failure(text: str) -> bool:
    lowered = (text or "").lower()
    return "redact" in lowered


class PacerClient:
    """
    PACER authentication + PCL search.

    `state_dir` holds the cached token (C:\\Rocky\\pacer\\ in production).
    The cache file contains a live PACER session token, so it stays on the
    local disk and never goes near OneDrive.
    """

    def __init__(self, username: str, password: str, state_dir: Path,
                 environment: str = "production",
                 client_code: str | None = None,
                 otp_secret: str | None = None,
                 redact_flag: bool = False):
        if not username or not password:
            raise PacerAuthError(
                "No PACER credentials. Set pacer_username / pacer_password "
                "in config.json.")
        env = (environment or "production").strip().lower()
        if env not in ENVIRONMENTS:
            raise PacerError(f"Unknown pacer_environment {environment!r} — "
                             f"use 'production' or 'qa'")
        self.env = env
        self.urls = ENVIRONMENTS[env]
        self.username = username
        self.password = password
        self.client_code = (client_code or "").strip() or None
        self.otp_secret = (otp_secret or "").strip() or None
        # Accounts with e-filing privileges cannot authenticate without
        # this. It is the filer's certification that they comply with the
        # redaction rules (Fed. R. App. P. 25(a)(5), Fed. R. Civ. P. 5.2,
        # Fed. R. Crim. P. 49.1, Fed. R. Bankr. P. 9037) — the same
        # attestation CM/ECF collects at every interactive login. It is
        # deliberately NOT defaulted on: setting pacer_redact_flag is the
        # account holder's act, not Rocky's.
        self.redact_flag = bool(redact_flag)
        self.token_path = state_dir / f"pacer_token_{env}.json"
        self._token: str | None = None
        self._token_issued: float = 0.0
        self._session = requests.Session()
        self._load_token()

    @property
    def billable(self) -> bool:
        return bool(self.urls["billable"])

    # -- token cache -------------------------------------------------------

    def _load_token(self) -> None:
        try:
            data = json.loads(self.token_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.get("username") != self.username:
            return  # cached token belongs to a different login
        issued = float(data.get("issued") or 0)
        if time.time() - issued > TOKEN_MAX_AGE_HOURS * 3600:
            return
        self._token = data.get("token") or None
        self._token_issued = issued

    def _store_token(self, token: str) -> None:
        self._token = token
        self._token_issued = time.time()
        try:
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(json.dumps({
                "username": self.username,
                "token": token,
                "issued": self._token_issued,
                "environment": self.env,
            }), encoding="utf-8")
        except OSError as e:
            log.warning(f"[pacer] could not cache token: {e}")

    # -- authentication ----------------------------------------------------

    def _auth_once(self, otp: str | None) -> dict:
        body: dict = {"loginId": self.username, "password": self.password}
        if self.client_code:
            body["clientCode"] = self.client_code
        if otp:
            body["otpCode"] = otp
        if self.redact_flag:
            body["redactFlag"] = "1"

        url = f"{self.urls['auth']}/services/cso-auth"
        try:
            resp = self._session.post(
                url, json=body, timeout=_TIMEOUT,
                headers={"Content-Type": "application/json",
                         "Accept": "application/json"})
        except requests.RequestException as e:
            raise PacerError(f"PACER auth network error: {e}") from e

        if resp.status_code != 200:
            raise PacerAuthError(
                f"PACER auth HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            return resp.json()
        except ValueError as e:
            raise PacerAuthError("PACER auth returned non-JSON") from e

    def authenticate(self, force: bool = False) -> str:
        if self._token and not force:
            return self._token

        otp = totp_now(self.otp_secret) if self.otp_secret else None
        data = self._auth_once(otp)

        # A code can be refused because it just rolled over mid-request, or
        # because PACER won't accept the same code twice in one window. One
        # retry on the next window separates that from a genuinely wrong
        # secret, which is worth doing in a scheduled job that nobody is
        # watching.
        if (self.otp_secret and str(data.get("loginResult")) != "0"
                and _looks_like_otp_failure(data.get("errorDescription", ""))):
            wait = seconds_until_next_totp() + 1
            log.info(f"[pacer] one-time passcode refused — retrying in "
                     f"{wait:.0f}s on the next TOTP window")
            time.sleep(wait)
            data = self._auth_once(totp_now(self.otp_secret))

        # loginResult "0" is success; anything else carries a description
        # worth surfacing verbatim (locked account, client code required,
        # password expiry — PACER forces a change every 180 days).
        if str(data.get("loginResult")) != "0" or not data.get("nextGenCSO"):
            detail = data.get("errorDescription") or data
            hint = ""
            if _looks_like_redaction_failure(str(detail)) \
                    and not self.redact_flag:
                hint = (" — this account has e-filing privileges, so PACER "
                        "requires the filer redaction certification on every "
                        "authentication. Set pacer_redact_flag true in "
                        "config.json to send it; that setting is your "
                        "attestation, so read the rule text above first.")
            elif _looks_like_otp_failure(str(detail)):
                if not self.otp_secret:
                    hint = (" — this account has MFA enabled. Put the base32 "
                            "secret from PACER's Manage My Account into "
                            "pacer_otp_secret so Rocky can generate the code "
                            "herself.")
                else:
                    hint = (" — the secret is set, so check that this "
                            "machine's clock is accurate (TOTP breaks on "
                            "clock drift) and that the secret belongs to "
                            f"{self.username}.")
            raise PacerAuthError(f"PACER login refused: {detail}{hint}")

        self._store_token(data["nextGenCSO"])
        log.info(f"[pacer] authenticated as {self.username} ({self.env})"
                 + (" with MFA" if otp else ""))
        return self._token

    def logout(self) -> None:
        if not self._token:
            return
        try:
            self._session.post(
                f"{self.urls['auth']}/services/cso-logout",
                json={"nextGenCSO": self._token}, timeout=30,
                headers={"Content-Type": "application/json"})
        except requests.RequestException:
            pass
        self._token = None
        try:
            self.token_path.unlink(missing_ok=True)
        except OSError:
            pass

    # -- PCL plumbing ------------------------------------------------------

    def _pcl(self, method: str, path: str, *, body: dict | None = None,
             params: dict | None = None, _retried: bool = False) -> dict:
        token = self.authenticate()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-NEXT-GEN-CSO": token,
        }
        if self.client_code:
            headers["X-CLIENT-CODE"] = self.client_code

        url = f"{self.urls['pcl']}{PCL_PATH}{path}"
        try:
            resp = self._session.request(
                method, url, json=body, params=params, headers=headers,
                timeout=_TIMEOUT)
        except requests.RequestException as e:
            raise PacerError(f"PCL network error on {path}: {e}") from e

        # PCL re-issues the token periodically in the response header.
        reissued = resp.headers.get("X-NEXT-GEN-CSO")
        if reissued and reissued != token:
            log.info("[pacer] PCL re-issued the session token")
            self._store_token(reissued)

        if resp.status_code == 401:
            if _retried:
                raise PacerAuthError(
                    "PCL rejected the session token twice — check the PACER "
                    "account (password expiry? locked?)")
            log.info("[pacer] PCL token expired — re-authenticating")
            self.authenticate(force=True)
            return self._pcl(method, path, body=body, params=params,
                             _retried=True)
        if resp.status_code == 406:
            raise PacerError(
                f"PCL rejected a search parameter (406): {resp.text[:300]}")
        if resp.status_code >= 400:
            raise PacerError(
                f"PCL {resp.status_code} on {path}: {resp.text[:300]}")
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as e:
            raise PacerError(f"PCL returned non-JSON from {path}") from e

    # -- searches ----------------------------------------------------------

    def find_cases(self, criteria: dict, page: int = 0,
                   sort: list[str] | None = None) -> dict:
        """
        One page (up to 54) of case results. In production this page is
        billed on retrieval. Returns the raw response — `receipt`,
        `pageInfo`, `content`.
        """
        params: dict = {"page": int(page)}
        if sort:
            params["sort"] = sort  # requests repeats the key per element
        return self._pcl("POST", "/cases/find",
                         body=_clean(criteria), params=params)

    def find_parties(self, criteria: dict, page: int = 0,
                     sort: list[str] | None = None) -> dict:
        """
        Party search. A minimally valid search needs at least a last name
        (which also matches entity names), an SSN (bankruptcy debtors only),
        or a filing date bound.
        """
        params: dict = {"page": int(page)}
        if sort:
            params["sort"] = sort
        return self._pcl("POST", "/parties/find",
                         body=_clean(criteria), params=params)

    def search_parties_all_pages(self, criteria: dict,
                                 max_pages: int = 3) -> tuple[list[dict], list[dict]]:
        """
        Party search across pages, capped. Returns (rows, receipts).

        max_pages is a money control, not a politeness control: each page
        past the first is another billable page, and a loose name against
        the nationwide index can run to hundreds.
        """
        rows: list[dict] = []
        receipts: list[dict] = []
        page = 0
        while page < max_pages:
            data = self.find_parties(criteria, page=page)
            receipt = data.get("receipt") or {}
            if receipt:
                receipts.append(receipt)
            rows.extend(data.get("content") or [])
            info = data.get("pageInfo") or {}
            if info.get("last") is True or not data.get("content"):
                break
            total_pages = int(info.get("totalPages") or 0)
            page += 1
            if total_pages and page >= total_pages:
                break
        return rows, receipts

    def find_case_by_number(self, case_number_full: str,
                            court_ids: list[str] | None = None) -> dict:
        """
        Look up one case by number, e.g. "1:2025cv01234". Court IDs here are
        PACER's (vaedc, dcdc, mddc), not CourtListener's.
        """
        criteria: dict = {"caseNumberFull": case_number_full}
        if court_ids:
            criteria["courtId"] = court_ids
        return self.find_cases(criteria)


# =============================================================================
# Court ID translation
# =============================================================================
# PCL spells a court as region + type: "vaedc" (Virginia Eastern District),
# "vaebk" (Virginia Eastern Bankruptcy), "04ca" (Fourth Circuit).
# CourtListener spells the same three "vaed", "vaeb", "ca4". Getting this
# wrong is expensive rather than merely wrong: a case-number search with no
# courtId hits every district in the country and bills the pages.

_PCL_TO_CL_SPECIAL = {
    "cofc": "uscfc",       # Federal Claims
    "citdc": "cit",        # International Trade
    "dcca": "cadc",        # D.C. Circuit
    "cafc": "cafc",        # Federal Circuit
}
_CL_TO_PCL_SPECIAL = {v: k for k, v in _PCL_TO_CL_SPECIAL.items()}

_CL_CIRCUIT = re.compile(r"^ca(\d{1,2})$")
_PCL_CIRCUIT = re.compile(r"^(\d{2})(ca|bap)$")


def cl_court_to_pcl(cl_id: str) -> str | None:
    """
    CourtListener court id -> PCL court id. None when we can't be sure,
    which callers must treat as "don't scope the search" rather than
    guessing a court and searching the wrong one.
    """
    court = (cl_id or "").strip().lower()
    if not court:
        return None
    if court in _CL_TO_PCL_SPECIAL:
        return _CL_TO_PCL_SPECIAL[court]
    m = _CL_CIRCUIT.match(court)
    if m:
        return f"{int(m.group(1)):02d}ca"
    if court.endswith("d") and len(court) >= 3:
        return f"{court[:-1]}dc"
    if court.endswith("b") and len(court) >= 3:
        return f"{court[:-1]}bk"
    return None


def pcl_court_to_cl(pcl_id: str) -> str:
    """PCL court id -> CourtListener court id. Falls back to the input."""
    court = (pcl_id or "").strip().lower()
    if not court:
        return ""
    if court in _PCL_TO_CL_SPECIAL:
        return _PCL_TO_CL_SPECIAL[court]
    m = _PCL_CIRCUIT.match(court)
    if m:
        return f"ca{int(m.group(1))}" if m.group(2) == "ca" \
            else f"bap{int(m.group(1))}"
    if court.endswith("bk"):
        return f"{court[:-2]}b"
    if court.endswith("dc"):
        return f"{court[:-1]}"
    return court


def _clean(criteria: dict) -> dict:
    """Drop empties — PCL 406s on blank strings where it wants a value."""
    out = {}
    for key, value in (criteria or {}).items():
        if value in (None, "", [], {}):
            continue
        out[key] = value
    return out


def receipt_fee(receipt: dict) -> float:
    """searchFee comes back as a string like '.10'."""
    try:
        return float(receipt.get("searchFee") or 0)
    except (TypeError, ValueError):
        return 0.0


def receipt_pages(receipt: dict) -> int:
    try:
        return int(receipt.get("billablePages") or 0)
    except (TypeError, ValueError):
        return 0


def case_key(row: dict) -> str:
    """
    Stable identity for a PCL case row: court + full case number. PCL's own
    caseId is only unique within a court.
    """
    court = (row.get("courtId") or "").strip().lower()
    number = (row.get("caseNumberFull") or "").strip()
    if not number:
        number = f"{row.get('caseYear', '')}-{row.get('caseNumber', '')}"
    return f"{court}:{number}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
