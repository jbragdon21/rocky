"""
Rocky → Remy bridge. Parses a paralegal "Run Remy" form-email into Remy CLI
arguments and shells out to remy_cli.py to produce a Word draft.

Decoupled from case management on purpose: this module never reads the case
index, never touches Rocky Cases\\, never looks at case_match. It writes its
output to a single flat folder (config.remy_outputs_path) with descriptive
filenames. Case-folder filing happens elsewhere in Rocky's pipeline.

Public API:
    run(email, classification, config) -> dict

Returns a dict with keys:
    invoked      bool — whether remy_cli.py was actually called
    output_path  str | None — path to generated draft, if any
    project_type str | None — which CLI subcommand we picked
    reason       str | None — why we skipped (if invoked is False)
    error        str | None — captured error message (if invocation failed)
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("rocky.remy_runner")


# =============================================================================
# Mapping: classifier output → Remy CLI subcommand
# =============================================================================

# Maps classifier category → Remy CLI subcommand name.
CATEGORY_TO_CLI = {
    "dc_rent_complaint":    "rent-complaint",
    "dc_breach_complaint":  "complaint",
    "breach_notice":        "warning-letter",
    "nonrenewal":           "warning-letter",
    "warning_letter":       "warning-letter",
    "settlement_agreement": "settlement",
    "response_letter":      "response-letter",
}

# Required attachments per classifier category. Each entry is
# (form-field-name, fallback-filename-keywords).
REQUIRED_ATTACHMENTS = {
    "dc_rent_complaint":    [("ledger", ["ledger"]),
                             ("notice", ["notice"]),
                             ("affidavit", ["affidavit", "aff"])],
    "dc_breach_complaint":  [("lease", ["lease"]),
                             ("notice", ["notice"]),
                             ("affidavit", ["affidavit", "aff"])],
    "breach_notice":        [("lease", ["lease"])],
    "nonrenewal":           [("lease", ["lease"])],
    "warning_letter":       [("lease", ["lease"])],
    "settlement_agreement": [("lease", ["lease"])],
    "response_letter":      [("incoming", ["incoming", "letter", "ltr"])],
}

# Default form-type string per (category, jurisdiction). Used when the
# paralegal form doesn't include an explicit "Form type:" field.
_DEFAULT_FORM_TYPE = {
    ("nonrenewal", "VA"):     "VA Nonrenewal",
    ("nonrenewal", "MD"):     "MD Nonrenewal",
    ("breach_notice", "VA"):  "VA 21/30 (Breach)",
    ("breach_notice", "DC"):  "DC Rent (Breach)",
    ("breach_notice", "MD"):  "MD 14-Day (Breach)",
    ("warning_letter", None): "Warning Letter",
}

_JURIS_NORMALIZE = {
    "va": "Virginia", "virginia": "Virginia",
    "dc": "DC",
    "md": "Maryland", "maryland": "Maryland",
}


# =============================================================================
# Form-email parsing
# =============================================================================

# Keys we recognize in the paralegal form. Lowercase.
KNOWN_KEYS = {
    "project", "resident", "property",
    "lease", "ledger", "notice", "affidavit", "incoming",
    "subsidized", "tenant rent portion", "subsidy portion",
    "rent over tenant portion", "subsidy failed to pay",
    "subsidy terminated", "other pending case", "pending case info",
    "stay dc status", "has stay dc email",
    "dangerous conduct", "non-rent charges",
    "form type", "jurisdiction", "agreement type",
    "special provisions", "attorney",
}


def parse_form_fields(body: str) -> dict[str, str]:
    """
    Parse 'Key: value' lines from the email body. Forgiving:
      - case-insensitive keys, whitespace trimmed
      - lines starting with '#' are comments
      - HTML tags stripped if Outlook gave us HTML
      - unrecognized keys silently ignored
    """
    if not body:
        return {}

    body = re.sub(r"<[^>]+>", "\n", body)
    body = re.sub(r"&nbsp;", " ", body)
    body = body.replace("\r\n", "\n")

    fields: dict[str, str] = {}
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z][A-Za-z \-/]+?):\s*(.*)$", line)
        if not m:
            continue
        key = m.group(1).strip().lower()
        val = m.group(2).strip()
        if key in KNOWN_KEYS:
            fields[key] = val
    return fields


def _yesno(val: str) -> bool:
    return (val or "").strip().lower() in ("yes", "y", "true", "1", "on")


def _money(val: str) -> str:
    """Strip $ and commas. Remy CLI takes strings, not numbers."""
    if not val:
        return ""
    return re.sub(r"[\$,]", "", val).strip()


# =============================================================================
# Attachment handling — write to a temp dir for Remy's CLI to read
# =============================================================================

def _stage_attachment(att: dict, tmpdir: Path) -> Path | None:
    """Write an attachment's bytes to disk under tmpdir. Returns the path."""
    name = (att.get("name") or "").strip()
    bytes_ = att.get("contentBytes")
    if not name or not bytes_:
        return None
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    path = tmpdir / safe
    try:
        path.write_bytes(bytes_)
    except OSError as e:
        log.warning(f"Could not stage attachment {name!r}: {e}")
        return None
    return path


def _resolve_attachments(
    attachments: list[dict],
    fields: dict[str, str],
    category: str,
    tmpdir: Path,
) -> tuple[dict[str, Path], list[str]]:
    """
    Stage all attachments to tmpdir, then resolve each required role
    (lease/ledger/notice/etc.) to a file path. Returns (resolved, missing).
    """
    staged: list[tuple[dict, Path]] = []
    for att in attachments:
        p = _stage_attachment(att, tmpdir)
        if p:
            staged.append((att, p))

    resolved: dict[str, Path] = {}
    required = REQUIRED_ATTACHMENTS.get(category, [])
    for role, fallback_keywords in required:
        # 1. Paralegal explicitly named a file in the form.
        hint = fields.get(role, "").strip().lower()
        if hint:
            for att, path in staged:
                if (att.get("name") or "").strip().lower() == hint:
                    resolved[role] = path
                    break
        if role in resolved:
            continue

        # 2. Filename keyword fallback.
        for kw in fallback_keywords:
            for att, path in staged:
                if kw in (att.get("name") or "").lower():
                    resolved[role] = path
                    break
            if role in resolved:
                break

    missing = [role for role, _ in required if role not in resolved]
    return resolved, missing


# =============================================================================
# Output filename
# =============================================================================

def _safe_slug(text: str, max_len: int = 30) -> str:
    if not text:
        return ""
    s = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return s[:max_len]


def _build_output_filename(fields: dict, project_type: str) -> str:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    resident = _safe_slug(fields.get("resident", ""))
    prop = _safe_slug(fields.get("property", ""))
    parts = [date]
    if resident:
        parts.append(resident)
    if prop:
        parts.append(prop)
    parts.append(project_type)
    if not resident and not prop:
        parts.append(secrets.token_hex(2))
    return "_".join(parts) + ".docx"


# =============================================================================
# CLI argument builders — one per classifier category
# =============================================================================
# Each builder receives (fields, atts, output_dir, email_body, classification)
# and returns a list of CLI args for the appropriate remy_cli.py subcommand.


def _resolve_form_type_and_jurisdiction(
    fields: dict[str, str],
    classification: dict,
) -> tuple[str, str]:
    """Derive --form-type and --jurisdiction for the warning-letter subcommand.
    Prefers explicit paralegal form fields; falls back to classifier output."""
    form_type = fields.get("form type", "").strip()
    jurisdiction = fields.get("jurisdiction", "").strip()

    cat = classification.get("project_category")
    juris_code = classification.get("jurisdiction")

    if not form_type:
        form_type = _DEFAULT_FORM_TYPE.get((cat, juris_code), "")
    if not jurisdiction and juris_code:
        jurisdiction = _JURIS_NORMALIZE.get(juris_code.lower(), juris_code)
    elif jurisdiction:
        jurisdiction = _JURIS_NORMALIZE.get(jurisdiction.lower(), jurisdiction)

    if not form_type or not jurisdiction:
        raise ValueError(
            f"Could not determine form_type/jurisdiction for {cat}/{juris_code}. "
            f"Resolved: form_type={form_type!r}, jurisdiction={jurisdiction!r}."
        )

    log.info(f"Resolved form_type={form_type!r}, jurisdiction={jurisdiction!r}")
    return form_type, jurisdiction


def _common_notice_args(fields, atts, output_dir, email_body, form_type, jurisdiction):
    """Shared arg construction for all notice types that use warning-letter."""
    args = [
        "--lease",        str(atts["lease"]),
        "--violation",    email_body[:4000],
        "--form-type",    form_type,
        "--jurisdiction", jurisdiction,
        "--output",       str(output_dir),
    ]
    if "ledger" in atts:
        args += ["--ledger", str(atts["ledger"])]
    if fields.get("attorney"):
        args += ["--attorney", fields["attorney"].strip().lower()]
    if _yesno(fields.get("subsidized")):
        args.append("--subsidy")
    return args


def _args_nonrenewal(fields, atts, output_dir, email_body, classification):
    form_type, jurisdiction = _resolve_form_type_and_jurisdiction(fields, classification)
    return _common_notice_args(fields, atts, output_dir, email_body, form_type, jurisdiction)


def _args_breach_notice(fields, atts, output_dir, email_body, classification):
    form_type, jurisdiction = _resolve_form_type_and_jurisdiction(fields, classification)
    return _common_notice_args(fields, atts, output_dir, email_body, form_type, jurisdiction)


def _args_warning_letter(fields, atts, output_dir, email_body, classification):
    form_type, jurisdiction = _resolve_form_type_and_jurisdiction(fields, classification)
    return _common_notice_args(fields, atts, output_dir, email_body, form_type, jurisdiction)


def _args_rent_complaint(fields, atts, output_dir, email_body, classification):
    args = [
        "--ledger",     str(atts["ledger"]),
        "--notice",     str(atts["notice"]),
        "--affidavit",  str(atts["affidavit"]),
        "--output",     str(output_dir),
    ]
    if _yesno(fields.get("subsidized")):
        args.append("--subsidized")
    if fields.get("tenant rent portion"):
        args += ["--tenant-rent-portion", _money(fields["tenant rent portion"])]
    if fields.get("subsidy portion"):
        args += ["--subsidy-portion", _money(fields["subsidy portion"])]
    if _yesno(fields.get("rent over tenant portion")):
        args.append("--rent-over-tenant-portion")
    if _yesno(fields.get("subsidy failed to pay")):
        args.append("--subsidy-failed-to-pay")
    if _yesno(fields.get("subsidy terminated")):
        args.append("--subsidy-terminated")
    if _yesno(fields.get("other pending case")):
        args.append("--other-pending")
    if fields.get("pending case info"):
        args += ["--pending-case-info", fields["pending case info"]]
    if fields.get("stay dc status"):
        args += ["--stay-dc-status", fields["stay dc status"].strip().lower()]
    if _yesno(fields.get("has stay dc email")):
        args.append("--has-stay-dc-email")
    return args


def _args_complaint(fields, atts, output_dir, email_body, classification):
    args = [
        "--lease",      str(atts["lease"]),
        "--notice",     str(atts["notice"]),
        "--affidavit",  str(atts["affidavit"]),
        "--output",     str(output_dir),
    ]
    if _yesno(fields.get("subsidized")):
        args.append("--subsidized")
    if fields.get("tenant rent portion"):
        args += ["--tenant-rent-portion", _money(fields["tenant rent portion"])]
    if fields.get("subsidy portion"):
        args += ["--subsidy-portion", _money(fields["subsidy portion"])]
    if _yesno(fields.get("dangerous conduct")):
        args.append("--dangerous-conduct")
    if _yesno(fields.get("other pending case")):
        args.append("--other-pending")
    if fields.get("pending case info"):
        args += ["--pending-case-info", fields["pending case info"]]
    if _yesno(fields.get("non-rent charges")):
        args.append("--non-rent-charges")
    return args


def _args_settlement(fields, atts, output_dir, email_body, classification):
    agreement_type = fields.get("agreement type", "general").strip().lower()
    args = [
        "--lease",          str(atts["lease"]),
        "--terms",          email_body[:4000],
        "--agreement-type", agreement_type,
        "--output",         str(output_dir),
    ]
    if fields.get("special provisions"):
        args += ["--special-provisions", fields["special provisions"]]
    return args


def _args_response_letter(fields, atts, output_dir, email_body, classification):
    args = [
        "--incoming", str(atts["incoming"]),
        "--output",   str(output_dir),
    ]
    if fields.get("attorney"):
        args += ["--attorney", fields["attorney"].strip().lower()]
    return args


# Keyed by classifier category (not CLI subcommand).
CATEGORY_BUILDERS = {
    "dc_rent_complaint":    _args_rent_complaint,
    "dc_breach_complaint":  _args_complaint,
    "breach_notice":        _args_breach_notice,
    "nonrenewal":           _args_nonrenewal,
    "warning_letter":       _args_warning_letter,
    "settlement_agreement": _args_settlement,
    "response_letter":      _args_response_letter,
}


# =============================================================================
# Public entry point
# =============================================================================

def run(email: dict, classification: dict, config: dict) -> dict:
    """
    Decide whether to invoke Remy for this email; if so, do it.
    Always returns a result dict. Never raises.
    """
    result: dict = {
        "invoked": False,
        "output_path": None,
        "project_type": None,
        "reason": None,
        "error": None,
    }

    if not classification.get("is_remy_request"):
        result["reason"] = "not_a_remy_request"
        return result

    category = classification.get("project_category")
    cli_sub = CATEGORY_TO_CLI.get(category)
    if not cli_sub:
        result["reason"] = f"no_cli_mapping_for_{category}"
        return result

    builder = CATEGORY_BUILDERS.get(category)
    if not builder:
        result["reason"] = f"no_builder_for_{category}"
        return result

    cli_path = config.get("remy_cli_path")
    outputs_path = config.get("remy_outputs_path")
    if not cli_path or not outputs_path:
        result["reason"] = "remy_paths_not_configured"
        return result

    if not Path(cli_path).exists():
        result["reason"] = f"remy_cli_not_found: {cli_path}"
        return result

    # Parse paralegal form. Falls back to empty dict if not present.
    body_text = (email.get("body") or {}).get("content") or email.get("bodyPreview") or ""
    fields = parse_form_fields(body_text)
    log.info(f"Parsed {len(fields)} form field(s) from email body.")

    Path(outputs_path).mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="rocky_remy_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)

        atts, missing = _resolve_attachments(
            email.get("attachments", []) or [],
            fields,
            category,
            tmpdir,
        )
        if missing:
            result["reason"] = f"missing_attachments: {missing}"
            log.warning(f"Skipping Remy: {result['reason']}")
            return result

        # All builders share the same signature now.
        try:
            args = builder(fields, atts, tmpdir, body_text, classification)
        except ValueError as e:
            result["reason"] = f"args_build_error: {e}"
            log.warning(f"Skipping Remy: {result['reason']}")
            return result

        # When Rocky is a frozen .exe, sys.executable is rocky.exe — not Python.
        # Use config.remy_python_path if set, otherwise find python on PATH.
        if getattr(sys, "frozen", False):
            python = config.get("remy_python_path", "python")
        else:
            python = sys.executable
        cmd = [python, str(cli_path), cli_sub, *args]
        log.info(f"Invoking Remy: {cli_sub} with {len(atts)} attachment(s)")
        log.debug(f"Full command: {cmd}")

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            result["reason"] = "remy_timeout_300s"
            log.error(result["reason"])
            return result
        except Exception as e:
            result["error"] = f"subprocess_failed: {e!r}"
            log.exception("Remy subprocess failed")
            return result

        if proc.returncode != 0:
            result["error"] = (
                f"remy_returncode_{proc.returncode}: "
                f"stderr={proc.stderr.strip()[:500]}"
            )
            log.error(result["error"])
            return result

        produced = (proc.stdout or "").strip().splitlines()
        produced_path_str = produced[-1] if produced else ""
        if not produced_path_str or not Path(produced_path_str).exists():
            result["error"] = f"remy_did_not_print_valid_output_path: {produced_path_str!r}"
            log.error(result["error"])
            return result

        produced_path = Path(produced_path_str)
        # Move to the final outputs folder with our descriptive filename.
        final_name = _build_output_filename(fields, cli_sub)
        final_path = Path(outputs_path) / final_name
        try:
            shutil.copy2(produced_path, final_path)
        except OSError as e:
            result["error"] = f"copy_to_outputs_failed: {e!r}"
            log.error(result["error"])
            return result

        result["invoked"] = True
        result["project_type"] = cli_sub
        result["output_path"] = str(final_path)
        log.info(f"Remy draft written: {final_path}")
        return result
