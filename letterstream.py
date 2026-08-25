"""
LetterStream API client — the mailing-house side of the Mailing Affidavits
process (mailing_affidavits.py).

Calibrated 2026-08-06 against LetterStream's real API documentation
("Mail Fulfillment by LetterStream — Integration API", February 3, 2023
PDF, downloaded from My Account > API Information). Everything
LetterStream-specific lives in THIS file.

What the documented API offers (single endpoint, command via POST fields):
  - Authentication on every request: a (API_ID), t (unique numeric id,
    10-18 digits, ACCEPTED ONLY ONCE EVER), h = md5(base64(last-6-of-t +
    API_KEY + first-6-of-t)). Verification responses: AUTHOK / IDOK (id
    found, hash failed) / DUP (t reused) / BAD (id invalid).
  - accountstatus=1                      account balance (used by --probe)
  - jobstatus= / docstatus= / batchstatus=   status for KNOWN ids (csv lists)
  - cert=<tracking#> or doc_id=<id> + getinfo=track|trackx|sig|proof
    trackx + responseformat=json returns JSON; proof returns the
    proof-of-mailing PDF (raw %PDF or base64-encoded stream).

What it does NOT offer (as of the Feb 2023 doc): a call that LISTS recent
jobs. The dashboard shows them, but the API only answers about ids you
already know. Until LetterStream provides an enumeration call (ask
support; the API PUSH callback exists but needs a public web endpoint,
which doesn't fit Rocky's architecture), discovery of new mailings is
manual: `--letterstream --fetch <tracking#>` (proof pulled via this API) or
`--letterstream --ingest <proof.pdf>` (proof downloaded by hand). If
support reveals a list call, put its POST params in config
`letterstream_list_params` and list_recent_mailings() will use them —
raw responses land in C:\\Rocky\\affidavits\\letterstream_raw.jsonl so
_normalize_job() can be extended from real data.

The client never submits or modifies mail jobs — status + download only.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

log = logging.getLogger("rocky.letterstream")

# The Feb 2023 doc uses www.letterstream.com throughout ("Updated all
# urls" changelog entry, Dec 2020). secure.letterstream.com also answers.
DEFAULT_BASE_URL = "https://www.letterstream.com/apis/index.php"

# Candidate key names for normalizing job JSON if/when a list call
# exists. The first key present wins; extend from letterstream_raw.jsonl.
_JOB_KEYS = {
    "job_id": ("job_id", "jobid", "id", "job", "order_id", "uniqueid",
               "unique_id", "ls_id"),
    "recipient_name": ("recipient_name", "to_name", "recipient", "name",
                       "to", "addressee"),
    "recipient_address": ("recipient_address", "to_address", "address",
                          "full_address"),
    "mail_date": ("mail_date", "mailed_date", "mailed", "date_mailed",
                  "mailing_date", "send_date", "processed_date"),
    "status": ("status", "job_status", "state"),
    "tracking": ("tracking", "tracking_number", "certified_number",
                 "usps_tracking", "article_number", "barcode", "cert"),
    "proof_url": ("proof_url", "proof", "pdf_url", "proof_link",
                  "pom_url", "proof_of_mailing"),
    "mail_type": ("mail_type", "type", "product", "mail_class"),
}

_last_unique_id = 0


class LetterStreamError(Exception):
    pass


class LetterStreamClient:
    def __init__(self, config: dict, local_dir: Path):
        self.api_id = (config.get("letterstream_api_id") or "").strip()
        self.api_key = (config.get("letterstream_api_key") or "").strip()
        self.base_url = (config.get("letterstream_base_url")
                         or DEFAULT_BASE_URL).strip()
        self.list_params = config.get("letterstream_list_params") or {}
        self.raw_log = local_dir / "letterstream_raw.jsonl"

    @property
    def configured(self) -> bool:
        return bool(self.api_id and self.api_key
                    and "PASTE" not in self.api_id.upper()
                    and "PASTE" not in self.api_key.upper())

    # -------------------------------------------------------------------
    # Auth — API doc Table 3.1
    # -------------------------------------------------------------------

    def _auth_fields(self) -> dict:
        """
        h = md5( base64( last6(t) + API_KEY + first6(t) ) ), hex digest.
        t is numeric, 10-18 digits, and LetterStream accepts each value
        only once ever — milliseconds (13 digits) with a monotonic bump
        guarantees uniqueness across rapid calls in one run.
        """
        global _last_unique_id
        t_val = max(int(time.time() * 1000), _last_unique_id + 1)
        _last_unique_id = t_val
        t = str(t_val)
        string_to_hash = t[-6:] + self.api_key + t[:6]
        h = hashlib.md5(base64.b64encode(string_to_hash.encode())).hexdigest()
        return {"a": self.api_id, "t": t, "h": h,
                "responseformat": "json"}

    # -------------------------------------------------------------------
    # Requests
    # -------------------------------------------------------------------

    def _post(self, params: dict, timeout: int = 60) -> requests.Response:
        if not self.configured:
            raise LetterStreamError(
                "LetterStream API credentials not configured "
                "(letterstream_api_id / letterstream_api_key)")
        data = {**self._auth_fields(), **params}
        try:
            return requests.post(self.base_url, data=data, timeout=timeout)
        except requests.RequestException as e:
            raise LetterStreamError(f"LetterStream request failed: {e}") from e

    def probe(self) -> None:
        """Account-status request (accountstatus=1) with verbose debug —
        verifies auth end to end and shows the prepay balance."""
        print(f"POST {self.base_url}")
        print("  params: accountstatus=1, debug=3 (+ auth fields a/t/h)")
        try:
            resp = self._post({"accountstatus": "1", "debug": "3"})
        except LetterStreamError as e:
            print(f"  ERROR: {e}")
            return
        print(f"  HTTP {resp.status_code}")
        body = resp.text or "(empty body)"
        print(body[:3000])
        if len(body) > 3000:
            print(f"  ... ({len(body)} chars total)")
        if "AUTHOK" in body or '"-100"' in body or "Success" in body:
            print("\n  Authentication looks GOOD (AUTHOK / success code).")
        elif "IDOK" in body:
            print("\n  API ID recognized but the hash failed — check "
                  "letterstream_api_key.")

    def _log_raw(self, obj) -> None:
        try:
            self.raw_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.raw_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(
                    {"ts": datetime.now(timezone.utc).isoformat(), "raw": obj},
                    ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass

    def _json(self, resp: requests.Response, what: str) -> dict | list:
        if resp.status_code != 200:
            raise LetterStreamError(
                f"LetterStream {what} error {resp.status_code}: "
                f"{resp.text[:300]}")
        try:
            data = resp.json()
        except ValueError as e:
            self._log_raw({what: resp.text[:5000]})
            raise LetterStreamError(
                f"LetterStream {what} returned non-JSON (logged raw): "
                f"{resp.text[:200]}") from e
        self._log_raw({what: data})
        return data

    # -------------------------------------------------------------------
    # Status + tracking (API doc sections VI and VII)
    # -------------------------------------------------------------------

    def account_status(self) -> dict | list:
        return self._json(self._post({"accountstatus": "1"}), "accountstatus")

    def job_status(self, job_ids: list[str]) -> dict | list:
        return self._json(
            self._post({"jobstatus": ",".join(map(str, job_ids))}),
            "jobstatus")

    def doc_status(self, doc_ids: list[str]) -> dict | list:
        return self._json(
            self._post({"docstatus": ",".join(map(str, doc_ids))}),
            "docstatus")

    def tracking(self, ref: str) -> dict | list:
        """Certified-mail tracking data (getinfo=trackx, JSON) for a USPS
        tracking number (20/22 digits) or a unique doc id."""
        return self._json(
            self._post({**_ref_field(ref), "getinfo": "trackx"}), "trackx")

    # -------------------------------------------------------------------
    # Proof of mailing (API doc Table 6.3 + appendix sample)
    # -------------------------------------------------------------------

    def download_proof_by_ref(self, ref: str) -> bytes | None:
        """
        Download a proof-of-mailing PDF by USPS certified tracking number
        or unique doc id. Per the docs the stream comes back either as
        raw PDF or base64-encoded PDF. Returns None (logged) on failure.
        """
        try:
            resp = self._post({**_ref_field(ref), "getinfo": "proof"},
                              timeout=120)
        except LetterStreamError as e:
            log.error(f"[letterstream] proof download failed for {ref}: {e}")
            return None
        if resp.status_code != 200:
            log.error(f"[letterstream] proof download error "
                      f"{resp.status_code} for {ref}: {resp.text[:200]}")
            return None
        content = resp.content
        if content[:8].lstrip().startswith(b"%PDF"):
            return content
        try:
            decoded = base64.b64decode(content, validate=False)
            if decoded[:8].lstrip().startswith(b"%PDF"):
                return decoded
        except (binascii.Error, ValueError):
            pass
        self._log_raw({"proof_ref": ref, "proof_response": resp.text[:2000]})
        log.error(f"[letterstream] proof response for {ref} is not a PDF "
                  f"(raw response logged) — check the reference and that "
                  f"the mailing has been produced")
        return None

    def download_proof(self, job: dict) -> bytes | None:
        """Proof for a normalized job dict (tracking number preferred)."""
        ref = job.get("tracking") or job.get("job_id")
        if not ref:
            log.error("[letterstream] job has no tracking number or id — "
                      "cannot fetch proof")
            return None
        return self.download_proof_by_ref(str(ref))

    # -------------------------------------------------------------------
    # Job submission — API doc method 2 (HTTP POST, 50 jobs/day)
    # -------------------------------------------------------------------

    def submit_single(
        self, pdf_bytes: bytes, filename: str, job_name: str,
        to: str, sender: str, pages: int,
        mailtype: str = "certified", coversheet: bool = True,
        preauth: bool = True, extra_fields: dict | None = None,
    ) -> dict:
        """
        Submit one PDF to one recipient (Table 4.2.1/4.2.3). `to` is
        "doc_id:name1:name2:addr1:addr2:city:ST:zip", `sender` the same
        without doc_id. With preauth=True (ALWAYS, in Rocky's flow) the
        job is NOT released: LetterStream returns the cost + an authcode,
        and nothing prints or bills until authorize() confirms it.
        extra_fields (config letterstream_extra_fields) is merged into
        the POST last — the hook for options the Feb 2023 doc doesn't
        cover (e.g. expedited production) once support names the field.
        Returns the parsed submission response (see _parse_submission).
        """
        data = {
            **self._auth_fields(),
            "job": job_name,
            "to[]": to,
            "from": sender,
            "pages": str(pages),
            "mailtype": mailtype,
            "coversheet": "Y" if coversheet else "N",
            **{str(k): str(v) for k, v in (extra_fields or {}).items()},
        }
        if preauth:
            data["preauth"] = "1"
        try:
            resp = requests.post(
                self.base_url, data=data,
                files={"single_file": (filename, pdf_bytes,
                                       "application/pdf")},
                timeout=300)
        except requests.RequestException as e:
            raise LetterStreamError(f"LetterStream submit failed: {e}") from e
        return self._parse_submission(self._json(resp, "submit"))

    def authorize(self, authcode: str) -> dict:
        """Release a preauth'd job into production (doauth). THIS is the
        step that spends money — call it only on a human YES."""
        return self._parse_submission(
            self._json(self._post({"doauth": authcode}), "doauth"))

    @staticmethod
    def _parse_submission(data) -> dict:
        """
        Normalize a submission/doauth response. Success codes: -100
        (submitted) and -200 (preauth ok / auth ok). Returns
        {ok, code, details, authcode, cost, quantity, docs, errors, raw}.
        """
        msgs = data.get("message") if isinstance(data, dict) else None
        if msgs is None:
            msgs = []
        if isinstance(msgs, dict):
            msgs = [msgs]
        infos = [m for m in msgs if isinstance(m, dict)
                 and (m.get("@attributes") or {}).get("type") != "error"]
        errors = [m for m in msgs if isinstance(m, dict)
                  and (m.get("@attributes") or {}).get("type") == "error"]
        out: dict = {
            "ok": False, "code": None, "details": None, "authcode": None,
            "cost": None, "quantity": None, "docs": [],
            "errors": [f"{m.get('code')}: {(m.get('details') or '').strip()}"
                       for m in errors],
            "raw": data,
        }
        if infos:
            info = infos[0]
            try:
                out["code"] = int(str(info.get("code")))
            except (TypeError, ValueError):
                pass
            out["details"] = (info.get("details") or "").strip()
            out["authcode"] = info.get("authcode")
            try:
                out["cost"] = float(info.get("cost"))
            except (TypeError, ValueError):
                pass
            out["quantity"] = info.get("quantity")
            docs = info.get("doc") or []
            if isinstance(docs, dict):
                docs = [docs]
            out["docs"] = [{"id": str(d.get("id")), "job": d.get("job"),
                            "cost": d.get("cost")}
                           for d in docs if isinstance(d, dict)]
            out["ok"] = out["code"] in (-100, -200) and not errors
        return out

    # -------------------------------------------------------------------
    # Job listing — NOT in the documented API (see module docstring)
    # -------------------------------------------------------------------

    def list_recent_mailings(self) -> list[dict]:
        """
        The Feb 2023 API doc has no job-enumeration call. If LetterStream
        support supplies one, put its POST params in config
        letterstream_list_params and this normalizes the result; until
        then it returns [] and the morning pull is a no-op (use
        --fetch <tracking#> / --ingest <proof.pdf> for discovery).
        """
        if not self.list_params:
            log.info("[letterstream] no job-list call in the documented "
                     "API — skipping automatic discovery (use --fetch or "
                     "--ingest; ask LetterStream support about a list call)")
            return []
        data = self._json(self._post(dict(self.list_params)), "joblist")

        jobs = data
        if isinstance(data, dict):
            for key in ("jobs", "data", "result", "results", "orders",
                        "message"):
                if isinstance(data.get(key), list):
                    jobs = data[key]
                    break
            else:
                jobs = [data]
        if not isinstance(jobs, list):
            raise LetterStreamError("LetterStream job list has an "
                                    "unrecognized shape (logged raw)")
        return [self._normalize_job(j) for j in jobs if isinstance(j, dict)]

    @staticmethod
    def _normalize_job(job: dict) -> dict:
        lowered = {str(k).lower(): v for k, v in job.items()}
        out: dict = {"raw": job}
        for field, candidates in _JOB_KEYS.items():
            out[field] = next(
                (lowered[c] for c in candidates
                 if c in lowered and lowered[c] not in (None, "")), None)
        out["job_id"] = str(out["job_id"]) if out["job_id"] is not None else None
        out["mail_date"], out["mail_time"] = _split_datetime(out.get("mail_date"))
        return out


def mail_date_from_trackx(data) -> str | None:
    """
    The mailing date from a trackx response: the earliest event date in
    the USPS detail strings (observed shape 2026-08-16: message.item.
    detail = ["SHIPMENT RECEIVED ACCEPTANCE PENDING 2026-05-29 22:31:00
    PHOENIX,AZ, 85026", ...]). Event times are facility-local, so only
    the date is trusted.
    """
    dates = re.findall(r"\b(20\d{2}-[01]\d-[0-3]\d)\b",
                       json.dumps(data, default=str))
    return min(dates) if dates else None


def _clean_addr_field(value) -> str:
    """Address-string fields are colon-delimited — colons/pipes inside a
    field would corrupt the record, so they become spaces."""
    return re.sub(r"[:|]+", " ", str(value or "")).strip()


def format_recipient(doc_id: str, parts: dict) -> str:
    """doc_id:name_1:name_2:address_1:address_2:city:state:zip (Table 4.2.2)."""
    keys = ("name1", "name2", "addr1", "addr2", "city", "state", "zip")
    return ":".join([_clean_addr_field(doc_id)]
                    + [_clean_addr_field(parts.get(k)) for k in keys])


def format_sender(parts: dict) -> str:
    """Same as format_recipient but without the doc_id."""
    keys = ("name1", "name2", "addr1", "addr2", "city", "state", "zip")
    return ":".join(_clean_addr_field(parts.get(k)) for k in keys)


def _ref_field(ref: str) -> dict:
    """cert= for USPS tracking numbers (20/22 digits), doc_id= otherwise."""
    digits = "".join(ch for ch in str(ref) if ch.isdigit())
    if len(digits) in (20, 22) and digits == str(ref).replace(" ", ""):
        return {"cert": digits}
    if len(digits) >= 20:
        return {"cert": digits}
    return {"doc_id": str(ref).strip()}


def _split_datetime(value) -> tuple[str | None, str | None]:
    """Split a vendor date/datetime into (YYYY-MM-DD, 'H:MM a.m.') parts."""
    if not value:
        return None, None
    text = str(value).strip()
    dt = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
                "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M",
                "%Y-%m-%d", "%m/%d/%Y"):
        try:
            dt = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None, None
    date_part = dt.strftime("%Y-%m-%d")
    if dt.hour or dt.minute:
        hour12 = dt.hour % 12 or 12
        ampm = "a.m." if dt.hour < 12 else "p.m."
        return date_part, f"{hour12}:{dt.minute:02d} {ampm}"
    return date_part, None
