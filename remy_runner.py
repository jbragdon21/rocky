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

CATEGORY_TO_CLI = {
    "dc_rent_complaint":   "rent-complaint",
    "dc_breach_complaint": "complaint",
    "breach_notice":       "warning-letter",
    "nonrenewal":          "warning-letter",
    "warning_letter":      "warning-letter",
    "settlement_agreement": "settlement",
    "response_letter":     "response-letter",
}

# Required attachments per CLI subcommand. Values are (form-field, fallback-keywords).
# The form-field is the paralegal's stated filename; fallback-keywords match
# against attachment filename if the explicit one isn't found.
REQUIRED_ATTACHMENTS = {
    "rent-complaint":  [("ledger", ["ledger"]),
                        ("notice", ["notice"]),
                        ("affidavit", ["affidavit", "aff"])],
    "complaint":       [("lease", ["lease"]),
                        ("notice", ["notice"]),
                        ("affidavit", ["affidavit", "aff"])],
    "warning-letter":  [("lease", ["lease"])],
    "settlement":      [("lease", ["lease"])],
    "response-letter": [("incoming", ["incoming", "letter", "ltr"])],
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
    cli_subcommand: str,
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
    required = REQUIRED_ATTACHMENTS.get(cli_subcommand, [])
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
# CLI argument builders, one per subcommand
# =============================================================================

def _args_rent_complaint(fields, atts, output_dir):
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


def _args_complaint(fields, atts, output_dir):
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


def _args_warning_letter(fields, atts, output_dir, email_body):
    """Form-type and jurisdiction are REQUIRED by Remy's CLI."""
    form_type = fields.get("form type", "").strip()
    jurisdiction = fields.get("jurisdiction", "").strip()
    if not form_type or not jurisdiction:
        raise ValueError(
            "warning-letter requires both 'Form type' and 'Jurisdiction' "
            "fields in the paralegal form email."
        )
    # Normalize jurisdiction to Remy's expected casing.
    juris_map = {"va": "Virginia", "virginia": "Virginia",
                 "dc": "DC",
                 "md": "Maryland", "maryland": "Maryland"}
    jurisdiction = juris_map.get(jurisdiction.lower(), jurisdiction)

    args = [
        "--lease",        str(atts["lease"]),
        "--violation",    email_body[:4000],  # cap to keep CLI manageable
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


def _args_settlement(fields, atts, output_dir, email_body):
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


def _args_response_letter(fields, atts, output_dir):
    args = [
        "--incoming", str(atts["incoming"]),
        "--output",   str(output_dir),
    ]
    if fields.get("attorney"):
        args += ["--attorney", fields["attorney"].strip().lower()]
    return args


SUBCOMMAND_BUILDERS = {
    "rent-complaint":  _args_rent_complaint,
    "complaint":       _args_complaint,
    "warning-letter":  _args_warning_letter,
    "settlement":      _args_settlement,
    "response-letter": _args_response_letter,
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

    builder = SUBCOMMAND_BUILDERS.get(cli_sub)
    if not builder:
        result["reason"] = f"no_builder_for_{cli_sub}"
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
            cli_sub,
            tmpdir,
        )
        if missing:
            result["reason"] = f"missing_attachments: {missing}"
            log.warning(f"Skipping Remy: {result['reason']}")
            return result

        # Build CLI args. Some builders need email body as free-text input.
        try:
            if cli_sub in ("warning-letter", "settlement"):
                args = builder(fields, atts, tmpdir, body_text)
            else:
                args = builder(fields, atts, tmpdir)
        except ValueError as e:
            result["reason"] = f"args_build_error: {e}"
            log.warning(f"Skipping Remy: {result['reason']}")
            return result

        cmd = [sys.executable, str(cli_path), cli_sub, *args]
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
