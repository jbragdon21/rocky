"""
CourtListener / RECAP API client
=================================

Everything CourtListener-specific lives here so pacer_monitor.py can stay
about cases instead of about someone else's REST conventions (same split
as letterstream.py vs mailing_affidavits.py).

Three surfaces, three very different cost profiles:

  1. READS (dockets, docket-entries, recap-documents, parties, attorneys)
     — free, but hard-throttled. Free Law Project cut the default
     authenticated allowance on 2026-05-07 to **5/min, 50/hour, 125/day**.
     That number is the single biggest constraint on this whole feature:
     a naive "loop the watchlist and pull entries for each" burns the
     daily budget on about forty cases. Every read here goes through
     _RateGovernor, which self-throttles BELOW the published cap and
     raises RateBudgetExhausted rather than letting Rocky earn a 429.
     A Free Law Project membership raises the cap; set
     courtlistener_rate_limits in config when that happens.

  2. RECAP FETCH (/recap-fetch/) — a separate throttle scope (30/min) and
     a separate kind of expensive: it logs into PACER with James's own
     credentials and buys the docket or the PDF. Free API, real PACER
     bill. Callers must clear the purchase with the spend governor in
     pacer_monitor.py first; nothing in this module checks the budget.

  3. STORAGE DOWNLOADS (storage.courtlistener.com) — free, unthrottled,
     no token. Once a document is in the RECAP archive, fetching the PDF
     costs nothing and does not touch the API budget at all.

Docket alerts (/docket-alerts/) are free for RECAP dockets and are what
makes the whole thing affordable: subscribe once, and CourtListener
notices the case changed so Rocky doesn't have to poll it.

API docs: https://wiki.free.law/c/courtlistener/help/api/rest/v4/
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

log = logging.getLogger("rocky.courtlistener")

API_BASE = "https://www.courtlistener.com/api/rest/v4"
STORAGE_BASE = "https://storage.courtlistener.com"

# Published defaults for an authenticated account with no membership
# (2026-05-07). Rocky budgets against these unless config overrides them.
DEFAULT_RATE_LIMITS = {"minute": 5, "hour": 50, "day": 125}
# /recap-fetch/ has its own scope and its own (much higher) ceiling.
FETCH_RATE_LIMIT_PER_MINUTE = 30

# RECAP Fetch request_type values.
FETCH_DOCKET = 1
FETCH_PDF = 2
FETCH_ATTACHMENT_PAGE = 3

# PROCESSING_QUEUE status codes returned by /recap-fetch/.
FETCH_STATUS = {
    1: "queued",
    2: "successful",
    3: "failed",
    4: "in_progress",
    5: "failed_will_retry",
    6: "invalid_content",
    7: "insufficient_metadata",
}
FETCH_TERMINAL = {2, 3, 6, 7}

# Court IDs where CourtListener and PACER disagree. CourtListener's own
# docs list these; we accept either spelling and normalize to theirs.
COURT_ID_ALIASES = {
    "azb": "arb",
    "cofc": "uscfc",
    "neb": "nebraskab",
    "nysb-mega": "nysb",
}

_TIMEOUT = 60


class CourtListenerError(RuntimeError):
    """Any non-recoverable API failure."""


class RecapFetchLoginError(CourtListenerError):
    """
    CourtListener could not log into PACER with the supplied credentials.

    Almost always MFA: Free Law Project's docs say plainly that they "do
    not currently support PACER or CM/ECF accounts with MFA enabled," and
    the AO has made MFA mandatory for CM/ECF-level accounts. The failure
    surfaces as `PacerLoginException: Did not get NextGenCSO cookie`.

    This is permanent for a given account rather than transient, so
    callers should stop trying instead of retrying on a schedule. Note
    that it says nothing about Rocky's OWN PACER integration: pacer_api
    sends `otpCode` and authenticates fine.
    """


class RateBudgetExhausted(CourtListenerError):
    """
    Rocky's self-imposed budget for the window is spent. Raised BEFORE the
    request goes out, so no 429 is earned and nothing is half-done. Callers
    should stop cleanly and leave their cursors where they are — the next
    run picks up the same work with a fresh budget.
    """

    def __init__(self, window: str, retry_after: float):
        self.window = window
        self.retry_after = retry_after
        super().__init__(
            f"CourtListener {window} budget spent; "
            f"~{int(retry_after)}s until capacity returns"
        )


def normalize_court(court_id: str) -> str:
    """PACER's court abbreviation -> CourtListener's."""
    c = (court_id or "").strip().lower()
    return COURT_ID_ALIASES.get(c, c)


# =============================================================================
# Rate governor
# =============================================================================

class _RateGovernor:
    """
    Rolling-window call ledger persisted to disk.

    CourtListener throttles on rolling windows, not clock periods, so the
    ledger keeps raw timestamps and prunes anything older than a day. It
    lives on disk because Rocky's commands are one-shot subprocesses — an
    in-memory counter would reset on every invocation and the monitor loop
    would happily blow through the daily cap by dinnertime.
    """

    def __init__(self, path: Path, limits: dict | None = None,
                 reserve: dict | None = None):
        self.path = path
        self.limits = dict(DEFAULT_RATE_LIMITS)
        if limits:
            self.limits.update({k: int(v) for k, v in limits.items() if v})
        # Headroom withheld from BULK work (the nightly sync) so interactive
        # work still has budget later in the day. Without it, a 7:00 AM sync
        # over a busy watchlist can eat all 125 calls and leave someone
        # emailing "send me the docket" at 2:00 PM with nothing to spend.
        self.reserve = {k: int(v) for k, v in (reserve or {}).items() if v}
        self._calls: list[float] = self._load()

    def _load(self) -> list[float]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [float(t) for t in data.get("calls", [])]
        except (OSError, ValueError, TypeError):
            return []

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"calls": [round(t, 3) for t in self._calls]}),
                encoding="utf-8")
        except OSError as e:
            # A ledger we can't persist is worse than useless — it would
            # under-count and invite a 429 — so say so loudly.
            log.error(f"[cl] could not write rate ledger {self.path}: {e}")

    _WINDOWS = {"minute": 60.0, "hour": 3600.0, "day": 86400.0}

    def _prune(self, now: float) -> None:
        self._calls = [t for t in self._calls if now - t < 86400.0]

    def remaining(self, effective: bool = False) -> dict:
        """
        Calls left in each window. `effective=True` subtracts the reserve,
        which is what a bulk caller is actually allowed to spend.
        """
        now = time.time()
        self._prune(now)
        out = {}
        for name, span in self._WINDOWS.items():
            used = sum(1 for t in self._calls if now - t < span)
            limit = self.limits.get(name, 0)
            if effective:
                limit -= self.reserve.get(name, 0)
            out[name] = max(0, limit - used)
        return out

    def check(self, cost: int = 1) -> None:
        """Raise RateBudgetExhausted if `cost` more calls won't fit."""
        now = time.time()
        self._prune(now)
        for name, span in self._WINDOWS.items():
            limit = self.limits.get(name)
            if not limit:
                continue
            limit -= self.reserve.get(name, 0)
            in_window = sorted(t for t in self._calls if now - t < span)
            if len(in_window) + cost > limit:
                # When capacity returns: the oldest call in the window has
                # to age out before the next slot opens.
                oldest = in_window[0] if in_window else now
                raise RateBudgetExhausted(name, max(1.0, span - (now - oldest)))

    def spend(self, cost: int = 1) -> None:
        now = time.time()
        self._calls.extend([now] * cost)
        self._prune(now)
        self._save()

    def wait_for_slot(self, max_wait: float = 70.0) -> bool:
        """
        Sleep until a per-minute slot frees up. Only the minute window is
        worth waiting on; hour and day exhaustion means come back later,
        not block a scheduled job for an hour.
        """
        try:
            self.check()
            return True
        except RateBudgetExhausted as e:
            if e.window != "minute" or e.retry_after > max_wait:
                raise
            log.info(f"[cl] minute budget spent — waiting "
                     f"{int(e.retry_after) + 1}s")
            time.sleep(e.retry_after + 1)
            self.check()
            return True


# =============================================================================
# Client
# =============================================================================

class CourtListenerClient:
    """
    Read/fetch client. `state_dir` is where the rate ledger lives
    (C:\\Rocky\\pacer\\ in production).
    """

    def __init__(self, token: str, state_dir: Path,
                 rate_limits: dict | None = None,
                 reserve: dict | None = None,
                 wait_for_minute_slot: bool = True):
        if not token:
            raise CourtListenerError(
                "No CourtListener API token. Put courtlistener_token in "
                "config.json (free at courtlistener.com/profile/api/).")
        self.token = token.strip()
        self.governor = _RateGovernor(state_dir / "cl_usage.json", rate_limits,
                                      reserve)
        self.wait_for_minute_slot = wait_for_minute_slot
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Token {self.token}",
            "Accept": "application/json",
            "User-Agent": "Rocky/PACER-Monitor (Gallagher LLP)",
        })
        self._fetch_calls: list[float] = []

    # -- plumbing ----------------------------------------------------------

    def _request(self, method: str, url: str, *, params: dict | None = None,
                 payload: dict | None = None, budgeted: bool = True) -> dict:
        if budgeted:
            if self.wait_for_minute_slot:
                self.governor.wait_for_slot()
            else:
                self.governor.check()

        try:
            resp = self._session.request(
                method, url, params=params, json=payload, timeout=_TIMEOUT)
        except requests.RequestException as e:
            raise CourtListenerError(f"network error calling {url}: {e}") from e
        finally:
            if budgeted:
                # Count the attempt, not the success. A failed call still
                # consumed a slot on their side.
                self.governor.spend()

        if resp.status_code == 429:
            retry = float(resp.headers.get("Retry-After") or 60)
            # Our ledger thought there was room and there wasn't — usually
            # means another client (a browser session, a second machine) is
            # spending the same account's budget. Burn the rest of the
            # window in the ledger so we stop guessing.
            self.governor.spend(cost=max(1, self.governor.remaining()["minute"]))
            raise RateBudgetExhausted("server", retry)
        if resp.status_code == 401:
            raise CourtListenerError(
                "CourtListener rejected the token (401). Check "
                "courtlistener_token in config.json.")
        if resp.status_code == 403:
            raise CourtListenerError(
                f"CourtListener refused the request (403): {resp.text[:300]}")
        if resp.status_code >= 400:
            body = resp.text[:400]
            if "PacerLoginException" in body or "NextGenCSO" in body:
                raise RecapFetchLoginError(
                    "CourtListener could not log into PACER with these "
                    "credentials. Free Law Project does not support "
                    "MFA-enabled PACER accounts, and MFA is mandatory for "
                    "CM/ECF-level accounts, so RECAP Fetch purchases are "
                    "unavailable for this login. Rocky's own PACER "
                    "integration is unaffected (it sends otpCode). "
                    f"PACER said: {body}")
            raise CourtListenerError(
                f"CourtListener {resp.status_code} on {url}: {body[:300]}")
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as e:
            raise CourtListenerError(
                f"CourtListener returned non-JSON from {url}") from e

    def get(self, endpoint: str, params: dict | None = None) -> dict:
        return self._request("GET", f"{API_BASE}/{endpoint.strip('/')}/",
                             params=params)

    def paginate(self, endpoint: str, params: dict | None = None,
                 max_pages: int = 3) -> list[dict]:
        """
        Walk cursor pages, capped. The cap is deliberate: with 125 calls a
        day, an unbounded walk over a long docket is an outage. Callers
        that need more should filter harder instead.
        """
        results: list[dict] = []
        page = self.get(endpoint, params)
        results.extend(page.get("results") or [])
        pages = 1
        while page.get("next") and pages < max_pages:
            page = self._request("GET", page["next"])
            results.extend(page.get("results") or [])
            pages += 1
        if page.get("next"):
            log.info(f"[cl] {endpoint}: stopped at {max_pages} pages "
                     f"({len(results)} rows) — more available")
        return results

    def remaining_budget(self, effective: bool = False) -> dict:
        return self.governor.remaining(effective=effective)

    def api_usage(self) -> dict:
        """Their view of our consumption. Cheap (separate throttle scope)."""
        return self._request("GET", f"{API_BASE}/api-usage/", budgeted=False)

    # -- reads -------------------------------------------------------------

    # Field lists keep responses small. `fields=` is not just politeness:
    # docket and entry payloads carry large text blobs that make each call
    # slow, and a slow call inside a 5/min budget hurts.
    DOCKET_FIELDS = (
        "id,court_id,case_name,case_name_short,docket_number,date_filed,"
        "date_terminated,date_last_filing,date_modified,nature_of_suit,"
        "cause,jury_demand,assigned_to_str,referred_to_str,pacer_case_id,"
        "absolute_url,source"
    )
    ENTRY_FIELDS = (
        "id,docket,date_filed,entry_number,description,pacer_sequence_number,"
        "recap_documents"
    )

    def find_docket(self, court_id: str, docket_number: str) -> dict | None:
        """One docket by court + docket number. Returns None if RECAP has
        never seen the case (which is normal for anything nobody bought)."""
        rows = self.paginate("dockets", {
            "court": normalize_court(court_id),
            "docket_number": docket_number.strip(),
            "fields": self.DOCKET_FIELDS,
        }, max_pages=1)
        return rows[0] if rows else None

    def get_docket(self, docket_id: int) -> dict:
        return self.get(f"dockets/{int(docket_id)}",
                        {"fields": self.DOCKET_FIELDS})

    def dockets_by_id(self, docket_ids: list[int]) -> list[dict]:
        """
        Refresh several dockets. ONE CALL EACH — there is no batch.

        The obvious `?id__in=` does not exist on this endpoint: `id` is a
        number-range filter (exact/gt/gte/lt/lte/range), and CourtListener
        rejects unknown filter parameters outright rather than ignoring
        them. `id__range` technically groups ids but would drag in every
        unrelated docket in the span, so it's worse than useless here.

        Callers must therefore treat this as expensive and let
        RateBudgetExhausted propagate: whatever was fetched before the
        budget ran out is still good, and the rest belongs in the deferral
        queue. Use `dockets_by_id_progressive` when you need that partial
        result rather than an all-or-nothing list.
        """
        return [d for _, d in
                self.dockets_by_id_progressive(docket_ids, stop_on_budget=False)]

    def dockets_by_id_progressive(self, docket_ids: list[int],
                                  stop_on_budget: bool = True):
        """
        Yield (docket_id, docket) as each is fetched, so a caller keeps
        everything retrieved before the budget ran out. With
        stop_on_budget the generator simply ends; otherwise the exception
        propagates.
        """
        for docket_id in docket_ids:
            try:
                yield docket_id, self.get_docket(docket_id)
            except RateBudgetExhausted:
                if stop_on_budget:
                    return
                raise
            except CourtListenerError as e:
                log.warning(f"[cl] docket {docket_id} refresh failed: "
                            f"{str(e)[:200]}")

    def options(self, endpoint: str) -> dict:
        """
        What this endpoint actually supports. An OPTIONS request returns
        the filter and ordering metadata, which is the difference between
        knowing a filter exists and assuming it does — `id__in` was an
        assumption, and it cost a live run.
        """
        return self._request("OPTIONS", f"{API_BASE}/{endpoint.strip('/')}/")

    def docket_entries(self, docket_id: int, since: str | None = None,
                       max_pages: int = 3) -> list[dict]:
        """
        Entries for one docket, newest first. `since` is a YYYY-MM-DD floor
        on date_filed — pass the last entry date Rocky already stored so a
        long-running case doesn't re-download its whole history.
        """
        params = {
            "docket": int(docket_id),
            "order_by": "-date_filed",
            "fields": self.ENTRY_FIELDS,
            "page_size": 100,
        }
        if since:
            params["date_filed__gte"] = since
        return self.paginate("docket-entries", params, max_pages=max_pages)

    def parties(self, docket_id: int, max_pages: int = 2) -> list[dict]:
        return self.paginate("parties", {
            "docket": int(docket_id),
            "filter_nested_results": "true",
            "page_size": 100,
        }, max_pages=max_pages)

    def search_recap(self, query: str, extra: dict | None = None,
                     result_type: str = "r") -> list[dict]:
        """
        RECAP search. type=r returns dockets with up to three nested
        documents; type=d is dockets only; type=rd is documents only.
        """
        params = {"type": result_type, "q": query}
        if extra:
            params.update(extra)
        return self.paginate("search", params, max_pages=2)

    # -- docket alerts -----------------------------------------------------

    def create_docket_alert(self, docket_id: int) -> dict:
        """
        Subscribe to a docket. Free for RECAP dockets, and the reason this
        feature is affordable: an alerted docket tells us it changed
        instead of being polled.
        """
        return self._request("POST", f"{API_BASE}/docket-alerts/",
                             payload={"docket": int(docket_id)})

    def list_docket_alerts(self, max_pages: int = 3) -> list[dict]:
        return self.paginate("docket-alerts", {"page_size": 100},
                             max_pages=max_pages)

    def delete_docket_alert(self, alert_id: int) -> None:
        self._request("DELETE", f"{API_BASE}/docket-alerts/{int(alert_id)}/")

    # -- RECAP Fetch (SPENDS PACER MONEY) ----------------------------------

    def _fetch_throttle(self) -> None:
        now = time.time()
        self._fetch_calls = [t for t in self._fetch_calls if now - t < 60]
        if len(self._fetch_calls) >= FETCH_RATE_LIMIT_PER_MINUTE:
            wait = 61 - (now - self._fetch_calls[0])
            log.info(f"[cl] recap-fetch minute budget spent — waiting "
                     f"{int(wait) + 1}s")
            time.sleep(max(1.0, wait))
        self._fetch_calls.append(time.time())

    def recap_fetch(self, payload: dict) -> dict:
        """
        Queue a PACER purchase. THIS COSTS MONEY on James's PACER account.
        Never call it without clearing pacer_monitor's spend governor first.
        Returns the PROCESSING_QUEUE row; poll it with fetch_status().
        """
        self._fetch_throttle()
        row = self._request("POST", f"{API_BASE}/recap-fetch/",
                            payload=payload, budgeted=False)
        log.info(f"[cl] recap-fetch queued id={row.get('id')} "
                 f"type={payload.get('request_type')}")
        return row

    def fetch_docket(self, pacer_username: str, pacer_password: str, *,
                     court: str | None = None,
                     docket_number: str | None = None,
                     docket_id: int | None = None,
                     pacer_case_id: str | None = None,
                     show_parties_and_counsel: bool = True,
                     date_start: str | None = None,
                     client_code: str | None = None) -> dict:
        """
        Buy a docket report. `date_start` (YYYY-MM-DD) limits the report to
        entries on or after that date, which is both cheaper and kinder —
        PACER bills the docket report by page.
        """
        payload: dict = {
            "request_type": FETCH_DOCKET,
            "pacer_username": pacer_username,
            "pacer_password": pacer_password,
            "show_parties_and_counsel": bool(show_parties_and_counsel),
        }
        if docket_id:
            payload["docket"] = int(docket_id)
        elif pacer_case_id and court:
            payload["pacer_case_id"] = str(pacer_case_id)
            payload["court"] = normalize_court(court)
        elif docket_number and court:
            payload["docket_number"] = docket_number
            payload["court"] = normalize_court(court)
        else:
            raise CourtListenerError(
                "fetch_docket needs docket_id, or court + docket_number, or "
                "court + pacer_case_id")
        if date_start:
            payload["de_date_start"] = date_start
        if client_code:
            payload["client_code"] = client_code
        return self.recap_fetch(payload)

    def fetch_pdf(self, pacer_username: str, pacer_password: str,
                  recap_document_id: int,
                  client_code: str | None = None) -> dict:
        """Buy one PDF (a RECAPDocument that has no file yet)."""
        payload = {
            "request_type": FETCH_PDF,
            "pacer_username": pacer_username,
            "pacer_password": pacer_password,
            "recap_document": int(recap_document_id),
        }
        if client_code:
            payload["client_code"] = client_code
        return self.recap_fetch(payload)

    def fetch_status(self, fetch_id: int) -> dict:
        return self._request("GET", f"{API_BASE}/recap-fetch/{int(fetch_id)}/",
                             budgeted=False)

    def wait_for_fetch(self, fetch_id: int, timeout: int = 600,
                       poll_seconds: int = 15) -> dict:
        """Poll a queued purchase to a terminal status."""
        deadline = time.time() + timeout
        row: dict = {}
        while time.time() < deadline:
            row = self.fetch_status(fetch_id)
            status = row.get("status")
            if status in FETCH_TERMINAL:
                label = FETCH_STATUS.get(status, str(status))
                if status == 2:
                    log.info(f"[cl] recap-fetch {fetch_id} {label}")
                else:
                    log.warning(f"[cl] recap-fetch {fetch_id} {label}: "
                                f"{(row.get('error_message') or '')[:200]}")
                return row
            time.sleep(poll_seconds)
        log.warning(f"[cl] recap-fetch {fetch_id} still running after "
                    f"{timeout}s — leaving it queued")
        return row

    # -- documents ---------------------------------------------------------

    def recap_documents(self, entry_ids: list[int]) -> list[dict]:
        if not entry_ids:
            return []
        out: list[dict] = []
        for i in range(0, len(entry_ids), 50):
            chunk = entry_ids[i:i + 50]
            out.extend(self.paginate("recap-documents", {
                "docket_entry__id__in": ",".join(str(e) for e in chunk),
                "fields": ("id,docket_entry,document_number,attachment_number,"
                           "description,is_available,page_count,filepath_local,"
                           "pacer_doc_id,is_free_on_pacer"),
                "page_size": 100,
            }, max_pages=3))
        return out

    def download_document(self, filepath_local: str, dest: Path) -> bool:
        """
        Pull a PDF out of the RECAP archive. Free, unauthenticated, and
        outside the API budget — storage.courtlistener.com is plain object
        storage, so this never eats into the 125/day.
        """
        if not filepath_local:
            return False
        url = (filepath_local if filepath_local.startswith("http")
               else f"{STORAGE_BASE}/{filepath_local.lstrip('/')}")
        try:
            resp = requests.get(url, timeout=120, stream=True)
        except requests.RequestException as e:
            log.warning(f"[cl] document download failed {url}: {e}")
            return False
        if resp.status_code != 200:
            log.warning(f"[cl] document download {resp.status_code}: {url}")
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(65536):
                fh.write(chunk)
        return True


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def days_ago_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
