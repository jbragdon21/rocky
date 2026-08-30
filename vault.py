"""
The Vault — shared document library for Remy-related filings
=============================================================

Rocky gathers the documents the team needs for Remy drafting and other
filings (leases, ledgers, affidavits of service, notices) into a shared
OneDrive folder called "The Vault", organized by property and tenant so
anyone at the firm can grab what they need.

    python rocky.py --vault [--dry-run] [--source inbox|vault-mail|dropbox]
                            [--backfill-days N] [--limit N] [--max-age-days N]
    python rocky.py --vault-dropbox | --vault-mail | --vault-inbox [same flags]
        The split flags run ONE source each, with independent instance
        locks and per-source state files (state_inbox.json /
        state_vault_mail.json / state_dropbox.json under C:\\Rocky\\vault\\),
        so they can be scheduled separately (e.g. hourly) and overlap
        safely. --vault runs every source in one pass — fine manually,
        but don't schedule it alongside the split tasks.
        Run the ingestion passes (all configured sources by default):
          inbox      — scan James's inbox for emails carrying leases,
                       ledgers, or affidavits of service and pull the
                       attachments into the Vault.
          vault-mail — scan rocky@'s inbox for any email with "vault" in
                       the subject; everything attached is submitted to
                       the Vault (the team's submission channel). Rocky
                       replies with a confirmation of what was filed.
          dropbox    — pull new files from the Dropbox accounts configured
                       in vault_dropbox_accounts: the account's own folders
                       (incremental via persisted delta cursors) and/or
                       shared-folder LINKS (a "shared_links" entry per
                       account — any authorized app token can read a link,
                       so one app under James's own Dropbox covers every
                       client's shared link; per-file processed marks
                       instead of cursors). Skipped when unconfigured.
        Every document is classified by Claude (type / property / tenant),
        deduplicated by SHA-256, filed to
        The Vault\\<Property>\\<Tenant>\\<Type> - <Tenant> - <Date>.<ext>,
        and logged to _vault\\catalog.jsonl. Low-confidence items land in
        _Needs Review\\ instead of being guessed at. The human-facing
        "Vault Index.xlsx" is regenerated at the end of each run.

    python rocky.py --vault --status
        Print catalog counts, cursor positions, and configured sources.

    python rocky.py --vault --reindex
        Rebuild Vault Index.xlsx from the catalog without ingesting.

    python rocky.py --vault --dropbox-auth <account-name>
        Interactive one-time OAuth for a Dropbox account from
        vault_dropbox_accounts (needs app_key/app_secret already in
        config). Prints the refresh token to paste into config.json.

    python rocky.py --vault --cleanup [--execute] [--force] [--vault-root <path>]
        Merge fragmented property/tenant folders (alias map + known
        names + same-person tenant matching), normalize catalog casing,
        and remove byte-identical duplicate files. Dry-run by default —
        writes the full plan to _vault\\cleanup_plan.txt for review;
        --execute applies it (catalog is backed up first).

    python rocky.py --vault --reclassify-review [--limit N] [--dry-run]
        Re-run classification over the _Needs Review backlog — scanned
        PDFs now go to Claude as attached pages — and file whatever
        comes back confident.

Reads use the app-level token (Application Access Policy already covers
jbragdon@ and rocky@ — no new Graph permission). Confirmation replies go
out from rocky@ via outbound.send_mail_guarded (internal-only allowlist
holds). Writing to the Vault is plain filesystem I/O against the local
OneDrive sync folder — pin "The Vault" "Always keep on this device" on
the Rocky laptop.

State (cursors) lives locally in C:\\Rocky\\vault\\state.json. The
catalog and activity log live on the share in The Vault\\_vault\\ so the
audit trail travels with the documents.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

log = logging.getLogger("rocky.vault")

CLAUDE_MODEL = "claude-sonnet-4-5"
CLASSIFY_MAX_TOKENS = 8000
# Never classify more documents than this in one Claude call — a response
# describing dozens of attachments overruns max_tokens and truncates the
# JSON mid-array (seen live 2026-08-22: an email carrying a stack of
# ledgers failed both attempts). Bigger inputs are chunked transparently.
CLASSIFY_BATCH_MAX = 10

# Extensions worth pulling into the Vault. Everything else (images, ics,
# eml, zip) is ignored — signature logos and calendar noise, mostly.
VAULT_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".xlsm", ".csv"}

# Below this, Rocky won't guess at property/tenant filing — the document
# goes to _Needs Review with its original name instead.
CONFIDENCE_FLOOR = 0.75

# Per-document text cap in classification prompts.
DOC_TEXT_CAP = 4000
BODY_TEXT_CAP = 1500

# Scanned PDFs (no text layer) ride into the classify call as attached
# PDF pages so Claude can read the scan itself — the 2026-08-17 bulk
# ingest sent 600+ scans to _Needs Review for want of this. Caps keep a
# batch of scans from ballooning the request.
SCAN_PDF_PAGES = 4
SCAN_PDF_MAX_BYTES = 20 * 1024 * 1024   # bigger source PDFs: name-only
SCAN_PDF_EXCERPT_MAX = 10 * 1024 * 1024  # excerpt still huge: skip attach

# Dropbox: files bigger than this are skipped (logged), batch size for
# classification calls, and default per-run file cap per account.
DROPBOX_MAX_FILE_BYTES = 30 * 1024 * 1024
DROPBOX_CLASSIFY_BATCH = 8
DROPBOX_DEFAULT_MAX_FILES = 200
# Shared-link walks re-list the whole tree every run (no delta support), so
# budget the API calls: ~500 entries per page-call, one call per subfolder.
# (The RAD-notices tree measured ~900+ calls on 2026-08-06 — keep headroom.)
DROPBOX_LINK_MAX_CALLS = 2500

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
_MAX_FETCH_PAGES = 40  # 40 pages x 50 = 2,000 messages per source per run

DOC_TYPE_LABELS = {
    "lease": "Lease",
    "ledger": "Ledger",
    "affidavit_of_service": "Affidavit of Service",
    "notice": "Notice",
    "other": "Document",
}

# Doc types the *inbox* pass is allowed to file. James's inbox is scanned
# opportunistically, so only the core Vault material is taken; explicit
# vault-mail submissions and curated Dropbox folders accept everything.
INBOX_DOC_TYPES = {"lease", "ledger", "affidavit_of_service"}

_DEFAULT_CASES_ROOT = r"C:\Users\jbragdon\OneDrive\OneDrive - gejlaw.com\Rocky Cases"

VAULT_README = """# The Vault

Documents gathered to support Remy drafting and other filings — leases,
ledgers, affidavits of service, and notices — collected here by Rocky so
the whole team can grab what they need.

## How it's organized

    The Vault\\
      <Property>\\
        <Tenant, Name>\\
          Lease - Tenant, Name - 2026-05-01.pdf
          Ledger - Tenant, Name - 2026-07-28.xlsx
      _Needs Review\\      <- documents Rocky couldn't confidently identify
      Vault Index.xlsx     <- searchable index of everything, refreshed daily

## How documents get here

1. Rocky watches James's inbox for leases, ledgers, and affidavits of
   service and files them automatically.
2. **Email (or forward) to rocky@gallagherllp.com and say "Vault"** —
   in the subject, or just in your note at the top of the email
   ("please add to vault" works). Rocky files every attachment and
   replies with where they went. Mention the property and tenant if the
   documents don't say.
3. Rocky pulls new files from connected Dropbox folders daily.

## House rules

- Copy files out freely — that's what the Vault is for.
- If something in _Needs Review belongs to a property/tenant, feel free
  to move it into the right folder (the index will note the move).
- Don't rename or delete filed documents; if something is wrong, tell
  James and Rocky will be taught accordingly.
"""


# =============================================================================
# Paths, config, state
# =============================================================================

def get_paths(config: dict, data_dir: Path) -> dict:
    """Resolve every Vault location from config (with house defaults).
    A CLI --vault-root <path> overrides config — the share mounts under a
    different local path on each machine (e.g. the dev laptop sees it as
    a shared-with-me folder), and maintenance commands like --cleanup
    need to run against it from wherever it's synced."""
    explicit = (_argv_value("--vault-root")
                or config.get("vault_root") or "").strip()
    if explicit:
        root = Path(explicit)
    else:
        cases = (config.get("cases_root") or "").strip() or _DEFAULT_CASES_ROOT
        root = Path(cases).parent / "The Vault"

    meta = root / "_vault"
    local = data_dir / "vault"
    return {
        "root": root,
        "meta": meta,
        "catalog": meta / "catalog.jsonl",
        "activity": meta / "activity.jsonl",
        "needs_review": root / "_Needs Review",
        "index_xlsx": root / "Vault Index.xlsx",
        "readme": root / "README.md",
        "local": local,
        # One state file per source, so the split tasks (--vault-inbox /
        # --vault-mail / --vault-dropbox) can run concurrently without
        # overwriting each other's cursors/marks.
        "state_inbox": local / "state_inbox.json",
        "state_vault_mail": local / "state_vault_mail.json",
        "state_dropbox": local / "state_dropbox.json",
        "state_legacy": local / "state.json",
    }


def ensure_vault_dirs(paths: dict) -> None:
    paths["meta"].mkdir(parents=True, exist_ok=True)
    paths["needs_review"].mkdir(parents=True, exist_ok=True)
    paths["local"].mkdir(parents=True, exist_ok=True)
    # README is Rocky-generated documentation — keep it current with the
    # template rather than freezing whatever version first shipped.
    try:
        if (not paths["readme"].exists()
                or paths["readme"].read_text(encoding="utf-8") != VAULT_README):
            paths["readme"].write_text(VAULT_README, encoding="utf-8")
    except OSError as e:
        log.warning(f"[vault] Could not refresh README.md: {e}")


def load_state(state_path: Path) -> dict:
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


# Which legacy state.json keys belong to which per-source file.
_LEGACY_STATE_SPLIT = {
    "state_inbox": ("inbox_cursor",),
    "state_vault_mail": ("vault_mail_cursor",),
    "state_dropbox": ("dropbox", "dropbox_links", "dropbox_folder_marks"),
}


def migrate_legacy_state(paths: dict) -> None:
    """One-time split of the pre-split state.json into per-source files."""
    legacy = paths["state_legacy"]
    if not legacy.exists():
        return
    try:
        old = json.loads(legacy.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for paths_key, fields in _LEGACY_STATE_SPLIT.items():
        target = paths[paths_key]
        if target.exists():
            continue
        part = {f: old[f] for f in fields if f in old}
        if part:
            save_state(target, part)
    try:
        legacy.rename(legacy.with_name("state.json.migrated"))
        log.info("[vault] Migrated legacy state.json into per-source "
                 "state files")
    except OSError as e:
        log.warning(f"[vault] Could not rename legacy state.json after "
                    f"migration: {e}")


def append_activity(paths: dict, event: dict) -> None:
    event = {"ts": datetime.now(timezone.utc).isoformat(), **event}
    try:
        with open(paths["activity"], "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning(f"[vault] Could not write activity event: {e}")


def append_catalog(paths: dict, entry: dict) -> None:
    entry = {"ts": datetime.now(timezone.utc).isoformat(), **entry}
    with open(paths["catalog"], "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_catalog(paths: dict) -> list[dict]:
    entries: list[dict] = []
    if not paths["catalog"].exists():
        return entries
    with open(paths["catalog"], "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
    return entries


def known_hashes(catalog: list[dict]) -> set[str]:
    return {e["sha256"] for e in catalog if e.get("sha256")}


# =============================================================================
# Filing
# =============================================================================

def _clean_component(value: str) -> str:
    """Sanitize a property/tenant name into a folder-safe component."""
    from rocky import _sanitize_filename  # lazy
    cleaned = re.sub(r"\s+", " ", (value or "").strip())
    return _sanitize_filename(cleaned)[:80]


def _doc_date(meta: dict) -> str | None:
    """The document's OWN date from the classifier, or None. Never borrows
    the source timestamp: a Dropbox upload date on a scanned affidavit is
    not a service date (the 2026-08-17 bulk ingest stamped half the vault
    2026-08-06 that way, colliding distinct documents into _(2) names)."""
    candidate = str(meta.get("document_date") or "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
        return candidate
    return None


def _ingest_date(fallback_iso: str | None) -> str:
    """Source date (else today) — only for _Needs Review name prefixes,
    where it marks when the file arrived, not what the document says."""
    candidate = (fallback_iso or "")[:10]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
        return candidate
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _filed_filename(meta: dict, original_name: str) -> str:
    from rocky import _sanitize_filename  # lazy
    ext = Path(original_name).suffix.lower() or ".pdf"
    doc_type = meta.get("doc_type") or "other"
    if doc_type == "other":
        label = (meta.get("label") or "").strip() or Path(original_name).stem
        label = _sanitize_filename(re.sub(r"\s+", " ", label))[:40] or "Document"
    else:
        label = DOC_TYPE_LABELS.get(doc_type, "Document")
    tenant = _clean_component(meta.get("tenant") or "")
    date = _doc_date(meta) or "undated"
    return f"{label} - {tenant} - {date}{ext}"


def _review_filename(original_name: str, sha256: str, fallback_iso: str | None) -> str:
    from rocky import _sanitize_filename  # lazy
    date = _ingest_date(fallback_iso)
    return f"{date}_{sha256[:8]}_{_sanitize_filename(original_name)[:100]}"


def _match_existing_property_folder(root: Path, prop: str) -> str | None:
    """An existing top-level folder that matches case-insensitively —
    reuse its exact casing so 'PARC RIVERSIDE' and 'Parc Riverside' never
    split the index."""
    if not prop:
        return None
    try:
        for child in root.iterdir():
            if (child.is_dir() and not child.name.startswith("_")
                    and child.name.casefold() == prop.casefold()):
                return child.name
    except OSError:
        pass
    return None


def _split_tenant(name: str) -> tuple[str, list[str]] | None:
    """'Last, First Middle' -> ('last', ['first', 'middle']); None if the
    name has no comma (corporate tenants stay as-is)."""
    if "," not in (name or ""):
        return None
    last, given = name.split(",", 1)
    tokens = [t for t in re.split(r"[\s.]+", given.strip()) if t]
    return re.sub(r"\s+", " ", last.strip()).casefold(), [t.casefold() for t in tokens]


def _same_person(a: str, b: str, allow_typo: bool = False) -> bool:
    """Same last name and compatible given names: equal, an initial of
    the other ('Holland, D' ~ 'Holland, Deleona'), or one extending the
    other ('Banks, Calvin' ~ 'Banks, Calvin Abram'). allow_typo also
    accepts a one-letter slip or short form in the first name
    ('Timeca'/'Timeka', 'Shantel'/'Shantell') — cleanup-only, too fuzzy
    for live filing."""
    pa, pb = _split_tenant(a), _split_tenant(b)
    if not pa or not pb or pa[0] != pb[0]:
        return False
    ga, gb = pa[1], pb[1]
    if not ga or not gb:
        return False
    short, long_ = (ga, gb) if len(ga) <= len(gb) else (gb, ga)
    if all(s == l or (len(s) == 1 and l.startswith(s))
           or (len(l) == 1 and s.startswith(l))
           for s, l in zip(short, long_)):
        return True
    if allow_typo:
        fa, fb = ga[0], gb[0]
        rest_match = ga[1:] == gb[1:] or not (ga[1:] and gb[1:])
        if len(fa) >= 4 and len(fb) >= 4 and rest_match:
            if len(fa) == len(fb) and sum(1 for x, y in zip(fa, fb) if x != y) == 1:
                return True
            if abs(len(fa) - len(fb)) <= 2 and (fa.startswith(fb) or fb.startswith(fa)):
                return True
    return False


def _match_existing_tenant(prop_dir: Path, tenant: str) -> str | None:
    """Reuse an existing tenant folder when the classified name is the
    same person (exact case-insensitive, or an initial/extension
    variant). Two DIFFERENT plausible folders = ambiguous = reuse
    nothing — a wrong merge is worse than a split."""
    if not tenant:
        return None
    try:
        existing = [c.name for c in prop_dir.iterdir() if c.is_dir()]
    except OSError:
        return None
    for name in existing:
        if name.casefold() == tenant.casefold():
            return name
    matches = [n for n in existing if _same_person(n, tenant)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        log.info(f"[vault] tenant {tenant!r} matches multiple existing "
                 f"folders {matches} — keeping its own folder")
    return None


def file_document(
    paths: dict,
    meta: dict,
    content: bytes,
    original_name: str,
    source: str,
    source_detail: dict,
    fallback_iso: str | None,
    dry_run: bool,
) -> dict:
    """
    Decide where a classified document belongs, write it, and log it.
    Returns the catalog entry (with disposition filed | needs_review).
    """
    from rocky import _dedup_path  # lazy

    sha256 = hashlib.sha256(content).hexdigest()
    # Gate on the RAW values — _clean_component's "unnamed" fallback must
    # never turn a missing property/tenant into a real-looking folder.
    prop_raw = (meta.get("property") or "").strip()
    tenant_raw = (meta.get("tenant") or "").strip()
    prop = _clean_component(prop_raw)
    tenant = _clean_component(tenant_raw)
    confidence = float(meta.get("confidence") or 0.0)

    confident = bool(prop_raw and tenant_raw and confidence >= CONFIDENCE_FLOOR)
    if confident:
        # Reuse existing folders rather than forking near-duplicates: an
        # exact-casing property match, then a same-person tenant match.
        existing_prop = _match_existing_property_folder(paths["root"], prop)
        if existing_prop and existing_prop != prop:
            prop = existing_prop
            meta = {**meta, "property": existing_prop}
        existing_tenant = _match_existing_tenant(paths["root"] / prop, tenant)
        if existing_tenant and existing_tenant != tenant:
            log.info(f"[vault] tenant folder reuse under {prop!r}: "
                     f"{tenant!r} -> {existing_tenant!r}")
            meta = {**meta, "tenant": existing_tenant}
            tenant = _clean_component(existing_tenant)
        dest_dir = paths["root"] / prop / tenant
        filename = _filed_filename(meta, original_name)
        disposition = "filed"
    else:
        dest_dir = paths["needs_review"]
        filename = _review_filename(original_name, sha256, fallback_iso)
        disposition = "needs_review"

    dest = _dedup_path(dest_dir / filename)
    if not dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)

    entry = {
        "sha256": sha256,
        "source": source,
        "source_detail": source_detail,
        "original_name": original_name,
        "doc_type": meta.get("doc_type"),
        "property": meta.get("property"),
        "tenant": meta.get("tenant"),
        "document_date": _doc_date(meta),
        "confidence": round(confidence, 2),
        "reasoning": meta.get("reasoning"),
        "disposition": disposition,
        "path": str(dest.relative_to(paths["root"])),
        "dry_run": dry_run,
    }
    if not dry_run:
        append_catalog(paths, entry)
    append_activity(paths, {"event": f"document_{disposition}", **{
        k: entry[k] for k in ("sha256", "source", "original_name", "doc_type",
                              "property", "tenant", "path", "dry_run")
    }})
    log.info(
        f"[vault] {'DRY-RUN ' if dry_run else ''}{disposition}: "
        f"{original_name!r} -> {entry['path']}"
    )
    return entry


# =============================================================================
# Claude classification
# =============================================================================

# Known property/community names (loaded once per run from Remy's
# property_table.csv). Grounds classification: the list rides in the
# prompt, and returned property names snap to canonical spellings.
_KNOWN_PROPERTIES: list[str] = []

# Property-name aliases (loaded once per run from the share's
# _vault\property_aliases.json). Keys are normalized variants, values the
# canonical folder name. This is the teaching surface for property
# naming: when a variant fragments a folder, add an alias line — no
# rebuild needed, every vault run rereads the file.
_PROPERTY_ALIASES: dict[str, str] = {}


def _norm_prop(name: str | None) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


def load_property_aliases(paths: dict) -> tuple[dict[str, str], list[str]]:
    """Read _vault\\property_aliases.json: {"aliases": {variant:
    canonical}, "known_properties": [names missing from Remy's table]}.
    Missing or unparseable file just disables aliasing."""
    path = paths["meta"] / "property_aliases.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return {}, []
    except ValueError as e:
        log.warning(f"[vault] {path.name} is not valid JSON ({e}) — "
                    f"aliases disabled this run")
        return {}, []
    aliases = {_norm_prop(k): (v or "").strip()
               for k, v in (data.get("aliases") or {}).items()
               if (v or "").strip()}
    known = [n.strip() for n in (data.get("known_properties") or [])
             if (n or "").strip()]
    log.info(f"[vault] property aliases: {len(aliases)} alias(es), "
             f"{len(known)} supplemental known propert(ies)")
    return aliases, known


def _load_grounding(config: dict, paths: dict) -> None:
    """Populate _KNOWN_PROPERTIES (Remy table + share supplements + alias
    canonicals) and _PROPERTY_ALIASES for this run."""
    _KNOWN_PROPERTIES[:] = load_known_properties(config)
    aliases, extra_known = load_property_aliases(paths)
    _PROPERTY_ALIASES.clear()
    _PROPERTY_ALIASES.update(aliases)
    have = {_norm_prop(n) for n in _KNOWN_PROPERTIES}
    for name in list(extra_known) + sorted(set(aliases.values())):
        if _norm_prop(name) not in have:
            _KNOWN_PROPERTIES.append(name)
            have.add(_norm_prop(name))


def load_known_properties(config: dict) -> list[str]:
    """Property names from Remy's property table. Default location is
    data\\property_table.csv beside remy_cli_path; override with
    vault_property_table. Missing file = empty list (grounding off)."""
    import csv
    explicit = (config.get("vault_property_table") or "").strip()
    if explicit:
        path = Path(explicit)
    else:
        remy_cli = (config.get("remy_cli_path") or "").strip()
        if not remy_cli:
            return []
        path = Path(remy_cli).parent / "data" / "property_table.csv"
    if not path.exists():
        log.info(f"[vault] property table not found at {path} — "
                 f"classification runs ungrounded")
        return []
    try:
        with open(path, "r", newline="", encoding="utf-8-sig") as f:
            names = [(r.get("Property Name") or "").strip()
                     for r in csv.DictReader(f)]
    except (OSError, csv.Error) as e:
        log.warning(f"[vault] could not read property table {path}: {e}")
        return []
    out = sorted({n for n in names if n})
    log.info(f"[vault] property grounding: {len(out)} known properties "
             f"loaded from {path.name}")
    return out


# Generic words that may legitimately pad a property-name variant
# ("The Kelvin Apartments" ~ "The Kelvin", "77 H Street NW" ~
# "77 H Street") without meaning a different community.
_PROP_STOPWORDS = {"the", "at", "on", "of", "a", "an", "and", "apartment",
                   "apartments", "apts", "nw", "ne", "sw", "se", "dc"}


def _extra_words_generic(raw_l: str, name_l: str) -> bool:
    """True when raw minus name leaves only stopwords."""
    leftover = raw_l.replace(name_l, " ")
    return all(w in _PROP_STOPWORDS
               for w in re.findall(r"[a-z0-9']+", leftover))


def _ground_property(meta: dict) -> dict:
    """Snap a classified property to its canonical spelling — the alias
    map first (exact, case-insensitive), then fuzzy against the known
    list — and, on a match with a tenant present, floor the confidence at
    filing level: a validated property removes the main doubt."""
    raw = (meta.get("property") or "").strip()
    if not raw:
        return meta
    snapped = _PROPERTY_ALIASES.get(_norm_prop(raw))
    if snapped is None and _KNOWN_PROPERTIES:
        from difflib import SequenceMatcher
        raw_l = _norm_prop(raw)
        best, best_score = None, 0.0
        containment_hits: set[str] = set()
        for name in _KNOWN_PROPERTIES:
            name_l = _norm_prop(name)
            if raw_l == name_l:
                best, best_score = name, 1.0
                break
            score = SequenceMatcher(None, raw_l, name_l).ratio()
            # Containment also counts for short-vs-long variants ("Kelvin"
            # vs "The Kelvin") — but a LONGER raw containing a known name
            # only matches when its extra words are generic: "Classic @
            # Modern on M" must never snap to "Modern on M".
            contained = (len(raw_l) >= 5 and raw_l in name_l) or \
                (name_l in raw_l and _extra_words_generic(raw_l, name_l))
            if contained:
                containment_hits.add(name)
                score = max(score, 0.9)
            if score > best_score:
                best, best_score = name, score
        if best is not None and best_score >= 0.85:
            if best_score < 0.95 and len(containment_hits) > 1:
                log.info(f"[vault] property {raw!r} matches several known "
                         f"names {sorted(containment_hits)} — not snapping")
            else:
                snapped = best
    if snapped is None:
        return meta
    # A table spelling may itself be aliased (e.g. the three Flats 130
    # phase entries all collapse to one vault folder).
    snapped = _PROPERTY_ALIASES.get(_norm_prop(snapped)) or snapped
    if snapped != raw:
        log.info(f"[vault] property snapped to canonical: {raw!r} -> {snapped!r}")
    meta["property"] = snapped
    if ((meta.get("tenant") or "").strip()
            and float(meta.get("confidence") or 0) >= 0.5):
        meta["confidence"] = max(float(meta.get("confidence") or 0), 0.8)
    return meta


_CLASSIFY_RULES = """For each document, return an object with:
  "filename": the filename exactly as given
  "is_vault_document": true if it is a residential lease (or lease renewal/
      addendum packet), a resident ledger / statement of deposit account /
      account statement, an affidavit or certificate of service, or a
      landlord-tenant notice. Correspondence, pleadings, invoices, W-9s,
      marketing, and internal memos are NOT vault documents.
  "doc_type": one of "lease", "ledger", "affidavit_of_service", "notice",
      "other" (null when is_vault_document is false). "notice" includes
      DC RAD-stamped notices (notice to vacate / notice to correct or
      vacate bearing a Rental Accommodations Division date stamp).
  "property": the apartment community / property name (null if unknown)
  "tenant": the resident's name as "Last, First" (null if unknown; for
      multiple residents use the first-listed, e.g. "Smith, John")
  "label": for doc_type "other" only, a 2-5 word label of what it is
  "document_date": the document's OWN date as YYYY-MM-DD (lease start or
      execution date; ledger through-date; affidavit service date; the
      date printed or stamped on a notice), or null when the document
      shows none — NEVER today's date and NEVER the file's upload date
  "confidence": 0.0-1.0 that doc_type, property, AND tenant are all right
  "reasoning": one short sentence

Respond with ONLY a JSON array, one object per document, same order."""


def _scan_pdf_excerpt(filename: str, content: bytes,
                      text: str | None) -> bytes | None:
    """First pages of a text-free PDF, to attach to the classify call so
    Claude reads the scan itself. Returns None when the file has a text
    layer (the text path is cheaper) or isn't a workable PDF."""
    if text and text.strip():
        return None
    if not (filename or "").lower().endswith(".pdf") or not content:
        return None
    if len(content) > SCAN_PDF_MAX_BYTES:
        log.info(f"[vault] {filename!r}: scanned PDF too large to attach "
                 f"({len(content) // (1024 * 1024)} MB) — classifying by "
                 f"name/path only")
        return None
    excerpt = content
    try:
        from pypdf import PdfReader, PdfWriter
        reader = PdfReader(io.BytesIO(content))
        n_pages = len(reader.pages)
    except Exception as e:
        # A PDF pypdf can't even open (truncated scan, no EOF marker)
        # would 400 the WHOLE classify batch if attached — seen live
        # 2026-08-29. Classify it by name/path only.
        log.info(f"[vault] {filename!r}: unreadable as PDF ({e}) — "
                 f"not attaching, classifying by name/path only")
        return None
    try:
        if n_pages > SCAN_PDF_PAGES:
            writer = PdfWriter()
            for page in reader.pages[:SCAN_PDF_PAGES]:
                writer.add_page(page)
            buf = io.BytesIO()
            writer.write(buf)
            excerpt = buf.getvalue()
    except Exception as e:
        log.debug(f"[vault] could not trim {filename!r} to "
                  f"{SCAN_PDF_PAGES} pages: {e}")
    if len(excerpt) > SCAN_PDF_EXCERPT_MAX:
        return None
    return excerpt


def _extract_json_array(text: str) -> list[dict]:
    """Pull the first JSON array out of a Claude response, tolerantly."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON array in response")
    parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, list):
        raise ValueError("response JSON is not an array")
    return [p for p in parsed if isinstance(p, dict)]


def classify_documents(
    client,
    docs: list[dict],
    context: str,
    explicit_submission: bool,
) -> list[dict]:
    """
    One Claude call classifying a batch of documents.
    docs: [{"filename": ..., "text": ... or None, "pdf_bytes": optional
    bytes of a text-free scan (see _scan_pdf_excerpt) attached so Claude
    reads the pages}]. Returns one meta dict per doc (aligned by
    filename, falling back to order). Inputs larger than
    CLASSIFY_BATCH_MAX are split across multiple calls.
    """
    if len(docs) > CLASSIFY_BATCH_MAX:
        results: list[dict] = []
        for i in range(0, len(docs), CLASSIFY_BATCH_MAX):
            results.extend(classify_documents(
                client, docs[i:i + CLASSIFY_BATCH_MAX], context,
                explicit_submission))
        return results

    doc_blocks = []
    for d in docs:
        text = (d.get("text") or "").strip()
        if text:
            snippet = text[:DOC_TEXT_CAP]
        elif d.get("pdf_bytes"):
            snippet = "(scanned document — its pages are attached below)"
        else:
            snippet = "(no text could be extracted)"
        doc_blocks.append(f"--- Document: {d['filename']} ---\n{snippet}")

    scan_note = ""
    if any(d.get("pdf_bytes") for d in docs):
        scan_note = (
            "\nSome documents are scans with no machine-readable text — "
            "their first pages are attached as PDFs after this message, "
            "each preceded by its filename. Read the scans themselves to "
            "classify them and to find their true document dates.\n"
        )

    intent = ""
    if explicit_submission:
        intent = (
            "\nThe sender explicitly submitted these documents to The Vault, "
            "so treat every substantive document as a vault document "
            "(is_vault_document true) even if it doesn't match the usual "
            "types — use doc_type \"other\" with a label. Honor any filing "
            "instructions in the email body (e.g. stated property or "
            "tenant names).\n"
        )

    known = ""
    if _KNOWN_PROPERTIES:
        known = (
            "\nKnown firm properties — when a document belongs to one of "
            "these, use the EXACT name as listed (email subjects, notes, "
            "and file paths often name the property, e.g. a subject like "
            "'The Kelvin | July Suit List' means these documents are for "
            "The Kelvin):\n" + "; ".join(_KNOWN_PROPERTIES) + "\n"
        )

    prompt = (
        "You are Rocky, a virtual paralegal at Gallagher LLP, filing "
        "documents into \"The Vault\" — a shared library of documents "
        "supporting landlord-tenant filings in VA, DC, and MD.\n\n"
        f"{context}\n{intent}{known}{scan_note}\n"
        f"{_CLASSIFY_RULES}\n\n"
        + "\n\n".join(doc_blocks)
    )

    content: list[dict] = [{"type": "text", "text": prompt}]
    for d in docs:
        pdf = d.get("pdf_bytes")
        if not pdf:
            continue
        content.append({"type": "text",
                        "text": f"Scanned document: {d['filename']}"})
        content.append({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf",
                       "data": base64.standard_b64encode(pdf).decode("ascii")},
        })

    results: list[dict] | None = None
    for attempt in (1, 2):
        response_text = None
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=CLASSIFY_MAX_TOKENS,
                messages=[{"role": "user", "content": content}],
            )
            response_text = response.content[0].text
            results = _extract_json_array(response_text)
            break
        except Exception as e:  # API error or bad JSON — one retry
            tail = (f" (response ended: ...{response_text[-150:]!r})"
                    if response_text else "")
            log.warning(f"[vault] classify attempt {attempt} failed: "
                        f"{e}{tail}")
    if results is None:
        log.error("[vault] classification failed for batch — explicit "
                  "submissions go to Needs Review, inbox docs are skipped")
        # classification_failed lets callers hold their cursor so nothing
        # is silently lost to an API outage: explicit submissions still
        # land in Needs Review; inbox/dropbox passes retry next run.
        return [
            {"filename": d["filename"], "is_vault_document": explicit_submission,
             "doc_type": "other" if explicit_submission else None,
             "confidence": 0.0, "reasoning": "classification failed",
             "classification_failed": True}
            for d in docs
        ]

    by_name = {(r.get("filename") or "").strip(): r for r in results}
    aligned: list[dict] = []
    for i, d in enumerate(docs):
        meta = by_name.get(d["filename"]) or (results[i] if i < len(results) else None)
        if meta is None:
            meta = {"filename": d["filename"],
                    "is_vault_document": explicit_submission,
                    "doc_type": "other" if explicit_submission else None,
                    "confidence": 0.0, "reasoning": "missing from response"}
        aligned.append(_ground_property(meta))
    return aligned


# =============================================================================
# Graph mail fetch (paged; shared by both mail sources)
# =============================================================================

def fetch_inbox_messages(
    token: str, mailbox: str, since: datetime, attachments_only: bool,
) -> list[dict]:
    """Fetch Inbox messages received after `since`, paging through all results."""
    since_iso = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    filt = f"receivedDateTime gt {since_iso}"
    if attachments_only:
        filt += " and hasAttachments eq true"
    url = f"{GRAPH_API_BASE}/users/{mailbox}/mailFolders/Inbox/messages"
    # No $orderby: combined with a compound $filter it can trip Graph's
    # InefficientFilter error. The cursor logic needs ascending order, so
    # we sort client-side below instead.
    params: dict | None = {
        "$filter": filt,
        "$top": "50",
        "$select": ("id,subject,from,receivedDateTime,body,bodyPreview,"
                    "hasAttachments,internetMessageId"),
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Prefer": 'outlook.body-content-type="text"',
    }

    messages: list[dict] = []
    pages = 0
    while url and pages < _MAX_FETCH_PAGES:
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=60)
        except requests.RequestException as e:
            log.error(f"[vault] Graph fetch failed for {mailbox}: {e}")
            break
        if resp.status_code != 200:
            log.error(f"[vault] Graph error {resp.status_code} for {mailbox}: "
                      f"{resp.text[:300]}")
            break
        data = resp.json()
        messages.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
        params = None  # nextLink already carries the query
        pages += 1
    messages.sort(key=lambda m: m.get("receivedDateTime") or "")
    return messages


def _candidate_attachments(token: str, mailbox: str, message: dict) -> list[dict]:
    """Download a message's attachments and keep the Vault-eligible ones."""
    from rocky import fetch_attachments, is_signature_image  # lazy

    keep: list[dict] = []
    for att in fetch_attachments(token, mailbox, message["id"]):
        name = att.get("name") or ""
        if Path(name).suffix.lower() not in VAULT_EXTENSIONS:
            continue
        if is_signature_image(name, att.get("contentType"),
                              att.get("size") or 0, att.get("isInline", False)):
            continue
        if not att.get("contentBytes"):
            log.info(f"[vault] Skipping attachment with no content: {name!r}")
            continue
        keep.append(att)
    return keep


def _email_context(message: dict, mailbox_role: str) -> str:
    sender = ((message.get("from") or {}).get("emailAddress") or {})
    body = (message.get("body") or {}).get("content") or message.get("bodyPreview") or ""
    body = re.sub(r"\s+\n", "\n", body).strip()[:BODY_TEXT_CAP]
    return (
        f"These documents arrived attached to {mailbox_role}:\n"
        f"From: {sender.get('name') or ''} <{sender.get('address') or ''}>\n"
        f"Subject: {message.get('subject') or '(no subject)'}\n"
        f"Received: {message.get('receivedDateTime') or ''}\n"
        f"Body:\n{body or '(empty)'}\n"
    )


# =============================================================================
# Source 1 — James's inbox scan
# =============================================================================

def scan_inbox_source(
    client, token: str, config: dict, paths: dict, state: dict,
    seen: set[str], backfill_days: int, limit: int | None, dry_run: bool,
    force_window: bool = False,
) -> dict:
    """Scan James's inbox for emails carrying leases/ledgers/affidavits.
    force_window=True (CLI --backfill-days) rewinds past the cursor —
    dedup makes re-covered ground harmless."""
    from rocky import extract_text_from_attachment  # lazy

    mailbox = config.get("vault_inbox_mailbox") or config.get("user_email")
    cursor_key = "inbox_cursor"
    since = _cursor_datetime(
        None if force_window else state.get(cursor_key), backfill_days)
    run_start = datetime.now(timezone.utc)

    messages = fetch_inbox_messages(token, mailbox, since, attachments_only=True)
    log.info(f"[vault] inbox: {len(messages)} message(s) with attachments in "
             f"{mailbox} since {since:%Y-%m-%d %H:%M}")

    counts = {"messages": 0, "filed": 0, "needs_review": 0, "duplicates": 0}
    for message in messages:
        if limit is not None and counts["messages"] >= limit:
            log.info(f"[vault] inbox: --limit {limit} reached; the rest picks "
                     f"up next run")
            break
        candidates = _candidate_attachments(token, mailbox, message)
        if not candidates:
            _advance_cursor(state, cursor_key, message)
            continue
        counts["messages"] += 1

        docs = []
        for a in candidates:
            text = extract_text_from_attachment(
                a["name"], a.get("contentType") or "", a["contentBytes"])
            docs.append({"filename": a["name"], "text": text,
                         "pdf_bytes": _scan_pdf_excerpt(
                             a["name"], a["contentBytes"], text)})
        metas = classify_documents(
            client, docs, _email_context(message, "James's inbox"),
            explicit_submission=False,
        )
        if any(m.get("classification_failed") for m in metas):
            log.warning("[vault] inbox: classification failed — stopping this "
                        "pass so the cursor holds and these emails retry "
                        "next run")
            break

        for att, meta in zip(candidates, metas):
            if not meta.get("is_vault_document"):
                log.info(f"[vault] inbox: {att['name']!r} not vault material "
                         f"({meta.get('reasoning') or 'no reason given'}) — "
                         f"skipping")
                continue
            if meta.get("doc_type") not in INBOX_DOC_TYPES:
                log.info(f"[vault] inbox: {att['name']!r} is "
                         f"{meta.get('doc_type')!r} — outside inbox scope, skipping")
                continue
            sha256 = hashlib.sha256(att["contentBytes"]).hexdigest()
            if sha256 in seen:
                counts["duplicates"] += 1
                append_activity(paths, {"event": "duplicate_skipped",
                                        "source": "inbox",
                                        "original_name": att["name"],
                                        "sha256": sha256})
                continue
            entry = file_document(
                paths, meta, att["contentBytes"], att["name"],
                source="inbox",
                source_detail=_mail_source_detail(message, mailbox),
                fallback_iso=message.get("receivedDateTime"),
                dry_run=dry_run,
            )
            seen.add(sha256)
            counts["filed" if entry["disposition"] == "filed" else "needs_review"] += 1

        # NOTE: James's inbox is deliberately left untouched — only
        # rocky@'s vault-mail submissions get moved to Inbox\The Vault
        # (his decision 2026-08-24; his own inbox is his workspace).
        _advance_cursor(state, cursor_key, message)

    if not dry_run and not messages:
        state[cursor_key] = run_start.isoformat()
    return counts


# =============================================================================
# Source 2 — rocky@ "Vault" submissions
# =============================================================================

def scan_vault_mailbox(
    client, token: str, config: dict, paths: dict, state: dict,
    seen: set[str], backfill_days: int, limit: int | None, dry_run: bool,
    force_window: bool = False,
) -> dict:
    """Scan rocky@'s inbox for emails with the vault keyword in the subject."""
    from rocky import extract_text_from_attachment  # lazy

    mailbox = config.get("vault_mailbox") or config.get("rocky_email") \
        or "rocky@gallagherllp.com"
    keyword = (config.get("vault_subject_keyword") or "vault").lower()
    cursor_key = "vault_mail_cursor"
    since = _cursor_datetime(
        None if force_window else state.get(cursor_key), backfill_days)
    run_start = datetime.now(timezone.utc)

    messages = fetch_inbox_messages(token, mailbox, since, attachments_only=False)
    submissions = [m for m in messages if _is_vault_submission(m, keyword)]
    log.info(f"[vault] vault-mail: {len(submissions)} submission(s) of "
             f"{len(messages)} new message(s) in {mailbox}")

    counts = {"messages": 0, "filed": 0, "needs_review": 0, "duplicates": 0}
    ignored_logged = 0
    for message in messages:
        if message not in submissions:
            # Say WHY mail was ignored — "I forwarded it, where is it?"
            # should be answerable from this log (capped to stay sane on
            # wide backfills).
            if ignored_logged < 10:
                sender = ((message.get("from") or {}).get("emailAddress")
                          or {}).get("address") or "?"
                log.info(f"[vault] vault-mail: no '{keyword}' in subject or "
                         f"first lines of body — ignored: "
                         f"{(message.get('subject') or '')[:80]!r} "
                         f"(from {sender})")
                ignored_logged += 1
            _advance_cursor(state, cursor_key, message)
            continue
        if limit is not None and counts["messages"] >= limit:
            break
        counts["messages"] += 1
        results: list[dict] = []

        candidates = _candidate_attachments(token, mailbox, message)
        if candidates:
            docs = []
            for a in candidates:
                text = extract_text_from_attachment(
                    a["name"], a.get("contentType") or "", a["contentBytes"])
                docs.append({"filename": a["name"], "text": text,
                             "pdf_bytes": _scan_pdf_excerpt(
                                 a["name"], a["contentBytes"], text)})
            metas = classify_documents(
                client, docs,
                _email_context(message, "Rocky's mailbox as a Vault submission"),
                explicit_submission=True,
            )
            for att, meta in zip(candidates, metas):
                sha256 = hashlib.sha256(att["contentBytes"]).hexdigest()
                if sha256 in seen:
                    counts["duplicates"] += 1
                    results.append({"original_name": att["name"],
                                    "disposition": "duplicate",
                                    "path": "(already in the Vault)"})
                    append_activity(paths, {"event": "duplicate_skipped",
                                            "source": "vault-mail",
                                            "original_name": att["name"],
                                            "sha256": sha256})
                    continue
                entry = file_document(
                    paths, meta, att["contentBytes"], att["name"],
                    source="vault-mail",
                    source_detail=_mail_source_detail(message, mailbox),
                    fallback_iso=message.get("receivedDateTime"),
                    dry_run=dry_run,
                )
                seen.add(sha256)
                results.append(entry)
                counts["filed" if entry["disposition"] == "filed"
                       else "needs_review"] += 1
        else:
            log.info(f"[vault] vault-mail: submission with no eligible "
                     f"attachments: {message.get('subject')!r}")

        if not dry_run and config.get("vault_confirmation_replies", True):
            _send_confirmation(config, message, results)
        if not dry_run:
            # Move LAST — a Graph move changes the message id.
            _file_processed_mail(config, paths, state, mailbox, message,
                                 "vault_processed_folder", "The Vault")
        _advance_cursor(state, cursor_key, message)

    if not dry_run and not messages:
        state[cursor_key] = run_start.isoformat()
    return counts


def _send_confirmation(config: dict, message: dict, results: list[dict]) -> None:
    """Reply from rocky@ telling the submitter what was filed where."""
    sender = ((message.get("from") or {}).get("emailAddress") or {}).get("address")
    if not sender:
        return
    try:
        import outbound
        from rocky import get_msal_app, acquire_token  # lazy
        token = acquire_token(get_msal_app(config))
        rocky_email = config.get("rocky_email", "rocky@gallagherllp.com")

        if results:
            lines = []
            for r in results:
                where = r.get("path", "?")
                if r.get("disposition") == "filed":
                    lines.append(f"  FILED: {r.get('original_name')} -> {where}")
                elif r.get("disposition") == "needs_review":
                    lines.append(f"  NEEDS REVIEW: {r.get('original_name')} -> {where} "
                                 f"(couldn't confirm property/tenant)")
                else:
                    lines.append(f"  ALREADY IN VAULT: {r.get('original_name')}")
            body = (
                "Rocky filed your Vault submission:\n\n"
                + "\n".join(lines)
                + "\n\nAll paths are inside the shared \"The Vault\" folder "
                  "on OneDrive. Anything under _Needs Review just needs the "
                  "property/tenant confirmed — feel free to move it, or "
                  "resend with the property and tenant named in the body."
            )
        else:
            body = (
                "Rocky received your Vault email but found no documents to "
                "file (no PDF/Word/Excel attachments). Reattach and resend "
                "with \"Vault\" in the subject."
            )

        outbound.send_mail_guarded(
            token=token,
            sender_mailbox=rocky_email,
            to=[sender],
            subject=f"Rocky — Vault: {message.get('subject') or ''}"[:150],
            body=body,
        )
    except Exception as e:
        log.warning(f"[vault] Confirmation reply to {sender} failed: {e}")


# Per-process cache for the delegated mail-move token (never persisted).
_MOVE_TOKEN: dict[str, str | None] = {}


def _file_processed_mail(config: dict, paths: dict, state: dict,
                         mailbox: str, message: dict,
                         config_key: str, default_name: str) -> None:
    """Move a consumed email into Inbox\\<name> so processed mail leaves
    the inbox. Best-effort — failures log and never break the run. Set
    the config key to "" to disable."""
    name = config.get(config_key) if config_key in config else default_name
    if not (name or "").strip():
        return
    if "token" not in _MOVE_TOKEN:
        from rocky import acquire_mail_move_token  # lazy
        _MOVE_TOKEN["token"] = acquire_mail_move_token(config)
    token = _MOVE_TOKEN["token"]
    if not token:
        return
    from rocky import file_message_to_inbox_subfolder  # lazy
    cache = state.setdefault("mail_folders", {})
    if file_message_to_inbox_subfolder(token, mailbox, message["id"],
                                       name, cache):
        append_activity(paths, {"event": "mail_filed", "mailbox": mailbox,
                                "subject": message.get("subject"),
                                "folder": name})
        log.info(f"[vault] moved processed mail to Inbox\\{name}: "
                 f"{(message.get('subject') or '')[:60]!r}")


_QUOTED_HEADER_RE = re.compile(
    r"^\s*(from:|-{3,}\s*original message|_{5,})", re.IGNORECASE)


def _is_vault_submission(message: dict, keyword: str) -> bool:
    """A vault submission has the keyword in the subject OR in the
    forwarding note — the body text ABOVE the quoted 'From:' header
    (people forward without touching the subject and just type 'please
    add to vault'). Capped at 10 note lines, and only the first 3 lines
    when there's no quoted section, so a stray mention deep in a regular
    email never triggers filing."""
    if keyword in (message.get("subject") or "").lower():
        return True
    body = ((message.get("body") or {}).get("content")
            or message.get("bodyPreview") or "")
    note_lines: list[str] = []
    saw_quote = False
    for ln in body.splitlines():
        if _QUOTED_HEADER_RE.match(ln):
            saw_quote = True
            break
        if ln.strip():
            note_lines.append(ln)
        if len(note_lines) >= 10:
            break
    if not saw_quote:
        note_lines = note_lines[:3]
    return any(keyword in ln.lower() for ln in note_lines)


def _mail_source_detail(message: dict, mailbox: str) -> dict:
    sender = ((message.get("from") or {}).get("emailAddress") or {})
    return {
        "mailbox": mailbox,
        "from": sender.get("address"),
        "subject": message.get("subject"),
        "received": message.get("receivedDateTime"),
        "internet_message_id": message.get("internetMessageId"),
    }


def _cursor_datetime(cursor_iso: str | None, backfill_days: int) -> datetime:
    if cursor_iso:
        try:
            return datetime.fromisoformat(cursor_iso.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc) - timedelta(days=backfill_days)


def _advance_cursor(state: dict, key: str, message: dict) -> None:
    received = message.get("receivedDateTime")
    if received:
        state[key] = received


# =============================================================================
# Source 3 — Dropbox accounts
# =============================================================================

def _dropbox_access_token(account: dict) -> str | None:
    """Redeem the account's long-lived refresh token for an access token."""
    try:
        resp = requests.post(
            "https://api.dropbox.com/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": account["refresh_token"],
                "client_id": account["app_key"],
                "client_secret": account["app_secret"],
            },
            timeout=30,
        )
    except requests.RequestException as e:
        log.error(f"[vault] Dropbox token request failed for "
                  f"{account.get('name')}: {e}")
        return None
    if resp.status_code != 200:
        log.error(f"[vault] Dropbox token error for {account.get('name')}: "
                  f"{resp.status_code} {resp.text[:200]}")
        return None
    return resp.json().get("access_token")


def _dropbox_list_new(token: str, folder: str, cursor: str | None) -> tuple[list[dict], str | None]:
    """
    List new file entries for a folder. With a cursor, continues the delta;
    without, does a full recursive listing. Returns (entries, new_cursor).
    A stale/reset cursor falls back to a full listing (dedup absorbs re-sees).
    """
    headers = {"Authorization": f"Bearer {token}",
               "Content-Type": "application/json"}
    entries: list[dict] = []

    def _call(url: str, payload: dict) -> dict | None:
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
        except requests.RequestException as e:
            log.error(f"[vault] Dropbox list failed: {e}")
            return None
        if resp.status_code == 409 and cursor and "reset" in resp.text:
            log.warning(f"[vault] Dropbox cursor reset for {folder!r}; relisting")
            return {"__reset__": True}
        if resp.status_code != 200:
            log.error(f"[vault] Dropbox list error {resp.status_code}: "
                      f"{resp.text[:200]}")
            return None
        return resp.json()

    if cursor:
        data = _call("https://api.dropboxapi.com/2/files/list_folder/continue",
                     {"cursor": cursor})
        if data and data.get("__reset__"):
            return _dropbox_list_new(token, folder, None)
    else:
        data = _call("https://api.dropboxapi.com/2/files/list_folder",
                     {"path": folder, "recursive": True,
                      "include_deleted": False, "limit": 500})
    if data is None:
        return [], cursor

    while True:
        entries.extend(e for e in data.get("entries", [])
                       if e.get(".tag") == "file")
        new_cursor = data.get("cursor") or cursor
        if not data.get("has_more"):
            return entries, new_cursor
        data = _call("https://api.dropboxapi.com/2/files/list_folder/continue",
                     {"cursor": new_cursor})
        if data is None or data.get("__reset__"):
            return entries, new_cursor


def _dropbox_download(token: str, path_lower: str) -> bytes | None:
    try:
        resp = requests.post(
            "https://content.dropboxapi.com/2/files/download",
            headers={
                "Authorization": f"Bearer {token}",
                "Dropbox-API-Arg": json.dumps({"path": path_lower}),
            },
            timeout=120,
        )
    except requests.RequestException as e:
        log.error(f"[vault] Dropbox download failed for {path_lower}: {e}")
        return None
    if resp.status_code != 200:
        log.error(f"[vault] Dropbox download error {resp.status_code} for "
                  f"{path_lower}: {resp.text[:200]}")
        return None
    return resp.content


def _dropbox_list_shared_link(token: str, url: str) -> list[dict] | None:
    """
    List every file under a Dropbox shared-folder link (no access to the
    owner's account needed — any authorized app token can read a link).
    Shared-link listing supports neither recursive=true nor persistent
    delta cursors, so this walks subfolders itself and callers track
    per-file processed marks instead of a cursor. Returns None on error
    (distinct from an empty folder) so callers hold their marks.
    """
    headers = {"Authorization": f"Bearer {token}",
               "Content-Type": "application/json"}
    files: list[dict] = []
    calls = 0
    stack = [""]
    while stack:
        rel = stack.pop()
        api_url = "https://api.dropboxapi.com/2/files/list_folder"
        payload: dict = {"path": rel, "shared_link": {"url": url}}
        while True:
            if calls >= DROPBOX_LINK_MAX_CALLS:
                log.warning(
                    f"[vault] Shared-link walk hit the "
                    f"{DROPBOX_LINK_MAX_CALLS}-call budget — proceeding with "
                    f"the {len(files)} files listed so far "
                    f"({len(stack)} subfolder(s) unvisited). If this folder "
                    f"is really that big, consider 'Add to my Dropbox' + a "
                    f"folders entry instead of the link (delta cursors).")
                return files
            calls += 1
            # Transient failures (network blips, Dropbox 5xx — seen live
            # 2026-08-16: a 500 at page ~1900 killed a 25-minute walk) get
            # 3 attempts with backoff; 429s wait out Retry-After.
            resp = None
            for attempt in (1, 2, 3):
                try:
                    r = requests.post(api_url, headers=headers, json=payload,
                                      timeout=60)
                except requests.RequestException as e:
                    log.warning(f"[vault] Dropbox listing network error at "
                                f"{rel!r} (attempt {attempt}/3): {e}")
                    r = None
                if r is not None and r.status_code == 429:
                    try:
                        delay = min(int(r.headers.get("Retry-After") or 5), 60)
                    except ValueError:
                        delay = 5
                    log.info(f"[vault] Dropbox throttled the listing — "
                             f"waiting {delay}s before retrying...")
                    time.sleep(delay)
                    r = None
                elif r is not None and r.status_code >= 500:
                    log.warning(f"[vault] Dropbox server error "
                                f"{r.status_code} at {rel!r} "
                                f"(attempt {attempt}/3): {r.text[:120]}")
                    r = None
                if r is not None:
                    resp = r
                    break
                if attempt < 3:
                    time.sleep(5 * attempt)
            if resp is None:
                if rel:
                    log.warning(f"[vault] Giving up on subfolder {rel!r} "
                                f"after 3 attempts — skipping it this run")
                    break
                log.error("[vault] Shared-link root listing failed after "
                          "3 attempts — aborting this link for this run")
                return None
            if resp.status_code == 409 and rel:
                # One bad subfolder (renamed mid-walk, odd characters, a
                # restricted sub-share) must not torpedo the whole listing:
                # skip it and keep walking. Seen live 2026-08-06:
                # path/not_found at ~800 calls killed a 12-minute walk.
                log.warning(f"[vault] Shared-link subfolder {rel!r} could "
                            f"not be listed ({resp.text[:120]}) — "
                            f"skipping it")
                break
            if resp.status_code != 200:
                log.error(f"[vault] Dropbox shared-link list error "
                          f"{resp.status_code} at path {rel!r}: "
                          f"{resp.text[:300]}")
                return None
            data = resp.json()
            for e in data.get("entries", []):
                child = f"{rel}/{e.get('name')}"
                if e.get(".tag") == "file":
                    files.append({"name": e.get("name"), "rel_path": child,
                                  "size": e.get("size") or 0,
                                  "server_modified": e.get("server_modified")})
                elif e.get(".tag") == "folder":
                    stack.append(child)
            if calls % 25 == 0:
                log.info(f"[vault] ...walked {calls} listing pages: "
                         f"{len(files)} files found, {len(stack)} "
                         f"subfolder(s) still queued")
            if not data.get("has_more"):
                break
            api_url = "https://api.dropboxapi.com/2/files/list_folder/continue"
            payload = {"cursor": data.get("cursor")}
    return files


def _dropbox_download_shared(token: str, url: str, rel_path: str) -> bytes | None:
    try:
        resp = requests.post(
            "https://content.dropboxapi.com/2/sharing/get_shared_link_file",
            headers={
                "Authorization": f"Bearer {token}",
                "Dropbox-API-Arg": json.dumps({"url": url, "path": rel_path}),
            },
            timeout=120,
        )
    except requests.RequestException as e:
        log.error(f"[vault] Dropbox shared-link download failed for "
                  f"{rel_path}: {e}")
        return None
    if resp.status_code != 200:
        log.error(f"[vault] Dropbox shared-link download error "
                  f"{resp.status_code} for {rel_path}: {resp.text[:200]}")
        return None
    return resp.content


def _link_mark(f: dict) -> str:
    """Processed-mark for a shared-link file: re-process only if it changes."""
    return f"{f.get('server_modified')}|{f.get('size')}"


def _apply_age_floor(entries: list[dict], max_age_days: int | None,
                     label: str) -> list[dict]:
    """Drop files whose Dropbox modified date is older than the floor.
    Files with no parseable date are kept (conservative)."""
    if not max_age_days:
        return entries
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    kept: list[dict] = []
    for e in entries:
        modified = e.get("server_modified")
        try:
            dt = datetime.fromisoformat(
                (modified or "").replace("Z", "+00:00"))
        except ValueError:
            kept.append(e)
            continue
        if dt >= cutoff:
            kept.append(e)
    skipped = len(entries) - len(kept)
    if skipped:
        log.info(f"[vault] {label}: {skipped} file(s) older than "
                 f"{max_age_days} days skipped")
    return kept


def _classify_and_file_batch(
    client, paths: dict, batch: list[dict], context: str,
    source_name: str, counts: dict, seen: set[str], dry_run: bool,
) -> bool:
    """
    Classify one downloaded-file batch and file the vault documents.
    Returns False when classification failed — the caller must then hold
    its cursor / not mark these files processed, so they retry next run.
    """
    if not batch:
        return True
    metas = classify_documents(
        client,
        [{"filename": b["prompt_name"], "text": b["text"],
          "pdf_bytes": b.get("pdf_bytes")} for b in batch],
        context, explicit_submission=False,
    )
    if any(m.get("classification_failed") for m in metas):
        return False
    for b, meta in zip(batch, metas):
        if not meta.get("is_vault_document"):
            continue
        if b["sha256"] in seen:
            # Identical bytes twice in one run (two client-side copies
            # downloaded before either was filed) — the download-time
            # check can't see them, this one can.
            counts["duplicates"] += 1
            append_activity(paths, {"event": "duplicate_skipped",
                                    "source": source_name,
                                    "original_name": b["file_name"],
                                    "sha256": b["sha256"]})
            continue
        entry = file_document(
            paths, meta, b["content"], b["file_name"],
            source=source_name,
            source_detail=b["detail"],
            fallback_iso=b["modified"],
            dry_run=dry_run,
        )
        seen.add(b["sha256"])
        counts["filed" if entry["disposition"] == "filed"
               else "needs_review"] += 1
    return True


def scan_dropbox(
    client, config: dict, paths: dict, state: dict,
    seen: set[str], limit: int | None, dry_run: bool,
    max_age_override: int | None = None,
) -> dict:
    """Pull new files from every configured Dropbox account: its own
    folders (delta cursors) and/or shared-folder links (per-file marks)."""
    from rocky import extract_text_from_attachment  # lazy

    accounts = config.get("vault_dropbox_accounts") or []
    counts = {"files": 0, "filed": 0, "needs_review": 0, "duplicates": 0}
    if not accounts:
        log.info("[vault] dropbox: no accounts configured — skipping")
        return counts

    dropbox_state = state.setdefault("dropbox", {})
    link_states = state.setdefault("dropbox_links", {})
    for account in accounts:
        name = account.get("name") or "unnamed"
        if not all(account.get(k) for k in ("app_key", "app_secret", "refresh_token")):
            log.warning(f"[vault] dropbox '{name}': missing app_key/app_secret/"
                        f"refresh_token — run --vault --dropbox-auth {name}")
            continue
        token = _dropbox_access_token(account)
        if not token:
            continue

        max_files = limit if limit is not None else \
            int(account.get("max_files_per_run") or DROPBOX_DEFAULT_MAX_FILES)
        account_cursors = dropbox_state.setdefault(name, {})

        # A link-only account shouldn't scan its (probably empty) own root.
        folders = account.get("folders")
        shared_links = account.get("shared_links") or []
        if folders is None:
            folders = [] if shared_links else [""]

        account_age = max_age_override or account.get("max_age_days")
        folder_marks_root = state.setdefault("dropbox_folder_marks", {})

        for folder in folders:
            fmarks = folder_marks_root.setdefault(f"{name}:{folder}", {})
            entries, new_cursor = _dropbox_list_new(
                token, folder, account_cursors.get(folder))
            eligible = [
                e for e in entries
                if Path(e.get("name") or "").suffix.lower() in VAULT_EXTENSIONS
                and (e.get("size") or 0) <= DROPBOX_MAX_FILE_BYTES
            ]
            eligible = _apply_age_floor(eligible, account_age,
                                        f"dropbox '{name}' {folder!r}")
            # Same skip-before-download marks as the link pass, so cursor
            # resets, truncated catch-up runs, and a first pass over a
            # freshly mounted folder never re-download processed files.
            fresh = [e for e in eligible
                     if fmarks.get(e.get("path_lower")) != _link_mark(e)]
            truncated = len(fresh) > max_files
            if truncated:
                log.info(f"[vault] dropbox '{name}' {folder!r}: {len(fresh)} "
                         f"new files, capping at {max_files} this run")
                fresh = fresh[:max_files]
            log.info(f"[vault] dropbox '{name}' {folder!r}: "
                     f"{len(fresh)} new of {len(eligible)} eligible file(s)")

            context = (
                f"These files were pulled from the Dropbox account "
                f"'{name}', folder '{folder or '/'}' — a source of "
                f"client property-management documents. The Dropbox "
                f"path of each file may name the property or tenant."
            )

            def _flush_folder(batch: list[dict]) -> bool:
                ok = _classify_and_file_batch(
                    client, paths, batch, context, f"dropbox:{name}",
                    counts, seen, dry_run)
                if ok and not dry_run:
                    for b in batch:
                        fmarks[b["path_lower"]] = b["mark"]
                    save_state(paths["state_dropbox"], state)
                return ok

            batch: list[dict] = []
            ok_all = True
            for e in fresh:
                content = _dropbox_download(token, e.get("path_lower") or "")
                if content is None:
                    continue
                sha256 = hashlib.sha256(content).hexdigest()
                if sha256 in seen:
                    counts["duplicates"] += 1
                    append_activity(paths, {"event": "duplicate_skipped",
                                            "source": f"dropbox:{name}",
                                            "original_name": e.get("name"),
                                            "sha256": sha256})
                    if not dry_run:
                        fmarks[e.get("path_lower")] = _link_mark(e)
                    continue
                counts["files"] += 1
                text = extract_text_from_attachment(
                    e.get("name") or "", "", content)
                batch.append({
                    "content": content,
                    "sha256": sha256,
                    "file_name": e.get("name"),
                    # Include the folder path in the name Claude sees — it
                    # often carries the property/tenant.
                    "prompt_name": e.get("path_display") or e.get("name"),
                    "text": text,
                    "pdf_bytes": _scan_pdf_excerpt(
                        e.get("name") or "", content, text),
                    "detail": {"account": name,
                               "path": e.get("path_display"),
                               "server_modified": e.get("server_modified")},
                    "modified": e.get("server_modified"),
                    "path_lower": e.get("path_lower"),
                    "mark": _link_mark(e),
                })
                if len(batch) >= DROPBOX_CLASSIFY_BATCH:
                    ok_all &= _flush_folder(batch)
                    batch = []
            ok_all &= _flush_folder(batch)

            # A truncated or failure-marred pass must not skip past the
            # unprocessed remainder: keep the old cursor so next run
            # re-lists (marks + dedup absorb re-sees).
            if not dry_run and not truncated and ok_all and new_cursor:
                account_cursors[folder] = new_cursor
                save_state(paths["state_dropbox"], state)

        for link in shared_links:
            lname = link.get("name") or "link"
            link_url = (link.get("url") or "").strip()
            if not link_url:
                log.warning(f"[vault] dropbox '{name}' shared link "
                            f"'{lname}': no url configured — skipping")
                continue
            marks = link_states.setdefault(f"{name}:{lname}", {})
            log.info(f"[vault] dropbox '{name}' link '{lname}': listing "
                     f"shared folder (no output until the walk finishes — "
                     f"large folders can take minutes)...")
            listed = _dropbox_list_shared_link(token, link_url)
            if listed is None:
                continue
            eligible = [
                f for f in listed
                if Path(f["name"] or "").suffix.lower() in VAULT_EXTENSIONS
                and (f["size"] or 0) <= DROPBOX_MAX_FILE_BYTES
            ]
            eligible = _apply_age_floor(
                eligible, max_age_override or link.get("max_age_days")
                or account_age, f"dropbox '{name}' link '{lname}'")
            fresh = [f for f in eligible
                     if marks.get(f["rel_path"]) != _link_mark(f)]
            if len(fresh) > max_files:
                log.info(f"[vault] dropbox '{name}' link '{lname}': "
                         f"{len(fresh)} new files, capping at {max_files} "
                         f"this run")
                fresh = fresh[:max_files]
            log.info(f"[vault] dropbox '{name}' link '{lname}': "
                     f"{len(fresh)} new of {len(eligible)} eligible file(s)")

            desc = link.get("description") or "client documents"
            context = (
                f"These files were pulled from a shared Dropbox folder "
                f"('{lname}': {desc}). The file path may name the "
                f"property or tenant."
            )

            def _flush_link(batch: list[dict]) -> None:
                ok = _classify_and_file_batch(
                    client, paths, batch, context, f"dropbox:{name}",
                    counts, seen, dry_run)
                # Mark files processed only after a clean classification —
                # a failed batch stays unmarked and retries next run. State
                # is persisted per batch so an interrupted multi-hour ingest
                # resumes without re-downloading what it already processed.
                if ok and not dry_run:
                    for b in batch:
                        marks[b["rel_path"]] = b["mark"]
                    save_state(paths["state_dropbox"], state)

            batch = []
            for f in fresh:
                content = _dropbox_download_shared(token, link_url,
                                                   f["rel_path"])
                if content is None:
                    continue
                sha256 = hashlib.sha256(content).hexdigest()
                if sha256 in seen:
                    counts["duplicates"] += 1
                    append_activity(paths, {"event": "duplicate_skipped",
                                            "source": f"dropbox:{name}",
                                            "original_name": f["name"],
                                            "sha256": sha256})
                    if not dry_run:
                        marks[f["rel_path"]] = _link_mark(f)
                    continue
                counts["files"] += 1
                text = extract_text_from_attachment(
                    f["name"] or "", "", content)
                batch.append({
                    "content": content,
                    "sha256": sha256,
                    "file_name": f["name"],
                    "prompt_name": f["rel_path"],
                    "text": text,
                    "pdf_bytes": _scan_pdf_excerpt(
                        f["name"] or "", content, text),
                    "detail": {"account": name, "shared_link": lname,
                               "path": f["rel_path"],
                               "server_modified": f["server_modified"]},
                    "modified": f["server_modified"],
                    "rel_path": f["rel_path"],
                    "mark": _link_mark(f),
                })
                if len(batch) >= DROPBOX_CLASSIFY_BATCH:
                    _flush_link(batch)
                    batch = []
            _flush_link(batch)
    return counts


def dropbox_auth_cli(config: dict, account_name: str) -> None:
    """Interactive one-time OAuth: prints the refresh token to paste into config."""
    accounts = config.get("vault_dropbox_accounts") or []
    account = next((a for a in accounts if a.get("name") == account_name), None)
    if account is None:
        print(f"No account named {account_name!r} in vault_dropbox_accounts. "
              f"Add it to config.json first (name, app_key, app_secret).")
        sys.exit(1)
    app_key = account.get("app_key")
    app_secret = account.get("app_secret")
    if not app_key or not app_secret:
        print(f"Account {account_name!r} needs app_key and app_secret from "
              f"the Dropbox App Console (https://www.dropbox.com/developers/apps).\n"
              f"The app needs scopes: files.metadata.read, files.content.read.")
        sys.exit(1)

    print(
        "\n1. Open this URL in a browser SIGNED IN to the target Dropbox "
        "account:\n\n"
        f"   https://www.dropbox.com/oauth2/authorize?client_id={app_key}"
        f"&response_type=code&token_access_type=offline\n\n"
        "2. Approve access and copy the code Dropbox shows you.\n"
    )
    code = input("Paste the code here: ").strip()
    resp = requests.post(
        "https://api.dropbox.com/oauth2/token",
        data={"grant_type": "authorization_code", "code": code,
              "client_id": app_key, "client_secret": app_secret},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"Token exchange failed: {resp.status_code} {resp.text[:300]}")
        sys.exit(1)
    refresh = resp.json().get("refresh_token")
    print(
        f"\nSuccess. Add this to the {account_name!r} entry in config.json:\n\n"
        f'    "refresh_token": "{refresh}"\n'
    )


# =============================================================================
# Vault Index.xlsx
# =============================================================================

def rebuild_index(paths: dict) -> None:
    """Regenerate Vault Index.xlsx from the catalog (filed + needs-review)."""
    try:
        import openpyxl
        from openpyxl.styles import Font
    except ImportError:
        log.warning("[vault] openpyxl not available — skipping index rebuild")
        return

    catalog = load_catalog(paths)
    wb = openpyxl.Workbook()

    ws = wb.active
    ws.title = "Vault Index"
    headers = ["Property", "Tenant", "Document Type", "Document Date",
               "File", "Folder", "Source", "Added", "Status"]
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True)

    review_rows: list[list] = []
    for e in catalog:
        if e.get("dry_run"):
            continue
        rel = e.get("path") or ""
        exists = (paths["root"] / rel).exists() if rel else False
        status = "OK" if exists else "Missing — moved or deleted"
        added = (e.get("ts") or "")[:10]
        if e.get("disposition") == "filed":
            ws.append([
                e.get("property") or "", e.get("tenant") or "",
                DOC_TYPE_LABELS.get(e.get("doc_type") or "other", "Document"),
                e.get("document_date") or "", Path(rel).name,
                str(Path(rel).parent), e.get("source") or "", added, status,
            ])
        elif e.get("disposition") == "needs_review":
            review_rows.append([
                Path(rel).name, e.get("original_name") or "",
                e.get("doc_type") or "?", e.get("source") or "", added,
                e.get("reasoning") or "", status,
            ])

    ws2 = wb.create_sheet("Needs Review")
    ws2.append(["File", "Original Name", "Best Guess Type", "Source",
                "Added", "Why It Needs Review", "Status"])
    for c in ws2[1]:
        c.font = Font(bold=True)
    for row in review_rows:
        ws2.append(row)

    for sheet in (ws, ws2):
        for col in sheet.columns:
            width = max((len(str(c.value or "")) for c in col), default=8)
            sheet.column_dimensions[col[0].column_letter].width = min(width + 2, 60)

    # Write to a temp file then swap, so a reader (or another vault task
    # finishing at the same moment) never sees a half-written workbook.
    tmp_path = paths["index_xlsx"].with_name("Vault Index.xlsx.tmp")
    try:
        wb.save(tmp_path)
        tmp_path.replace(paths["index_xlsx"])
        log.info(f"[vault] Index rebuilt: {paths['index_xlsx']}")
    except OSError:
        log.warning("[vault] Vault Index.xlsx is open/locked (or another "
                    "vault task is writing it) — index not updated this "
                    "run (will refresh next run)")
        try:
            tmp_path.unlink()
        except OSError:
            pass


# =============================================================================
# Vault digest — daily email of what was added and from where
# =============================================================================

def _digest_source_label(entry: dict) -> str:
    """Human-readable 'from where' for a catalog entry."""
    src = entry.get("source") or ""
    d = entry.get("source_detail") or {}
    if src == "inbox":
        return (f"James's inbox — {d.get('from') or 'unknown sender'}: "
                f"“{d.get('subject') or ''}”")
    if src == "vault-mail":
        return (f"emailed to rocky@ by {d.get('from') or 'unknown sender'}: "
                f"“{d.get('subject') or ''}”")
    if src.startswith("dropbox:"):
        where = d.get("shared_link") or d.get("account") or src.split(":", 1)[1]
        path = d.get("path") or ""
        return f"Dropbox ({where}){' — ' + path if path else ''}"
    return src or "unknown source"


# Wide-lookback digests (e.g. --hours 336 covering a bulk ingest) must
# not become a several-thousand-row email — overflow is summarized and
# the reader pointed at Vault Index.xlsx.
DIGEST_MAX_FILED_ROWS = 300
DIGEST_MAX_REVIEW_ROWS = 150


def digest_body_html(filed: list[dict], review: list[dict]) -> str:
    """The Filed table + Needs Review list as an HTML fragment — used by
    the Vault Digest and by the Multifamily Digest's Vault section."""
    from html import escape

    def td(text, extra=""):
        return (f'<td style="padding:4px 10px 4px 0;vertical-align:top;'
                f'{extra}">{escape(str(text))}</td>')

    filed_total = len(filed)
    review_total = len(review)
    filed = sorted(filed, key=lambda x: ((x.get("property") or "").lower(),
                                         (x.get("tenant") or "").lower()))[
        :DIGEST_MAX_FILED_ROWS]
    review = review[:DIGEST_MAX_REVIEW_ROWS]

    filed_rows = []
    for e in filed:
        filed_rows.append(
            "<tr>"
            + td(e.get("property") or "?")
            + td(e.get("tenant") or "?")
            + td(DOC_TYPE_LABELS.get(e.get("doc_type") or "other", "Document"))
            + td(e.get("document_date") or "")
            + td(_digest_source_label(e), extra="color:#666;")
            + "</tr>"
        )
    filed_html = ""
    if filed_rows:
        overflow = ""
        if filed_total > len(filed_rows):
            overflow = (f"<p style='margin:4px 0 0 0;font-size:13px;"
                        f"color:#666;'>…and {filed_total - len(filed_rows)} "
                        f"more — the full inventory is in Vault Index.xlsx."
                        f"</p>")
        filed_html = (
            f"<h3 style='margin:18px 0 6px 0;'>Filed ({filed_total})</h3>"
            "<table style='border-collapse:collapse;font-size:13px;'>"
            "<tr style='text-align:left;color:#888;'>"
            "<th style='padding:4px 10px 4px 0;'>Property</th>"
            "<th style='padding:4px 10px 4px 0;'>Tenant</th>"
            "<th style='padding:4px 10px 4px 0;'>Document</th>"
            "<th style='padding:4px 10px 4px 0;'>Date</th>"
            "<th style='padding:4px 10px 4px 0;'>From</th></tr>"
            + "".join(filed_rows) + "</table>" + overflow
        )

    review_html = ""
    if review:
        items = "".join(
            f"<li style='margin:2px 0;'>{escape(e.get('original_name') or '?')}"
            f" <span style='color:#666;'>— {escape(_digest_source_label(e))}"
            f"</span></li>"
            for e in review
        )
        overflow = ""
        if review_total > len(review):
            overflow = (f"<p style='margin:4px 0 0 0;font-size:13px;"
                        f"color:#666;'>…and {review_total - len(review)} "
                        f"more in the _Needs Review folder.</p>")
        review_html = (
            f"<h3 style='margin:18px 0 6px 0;'>Needs review "
            f"({review_total})</h3>"
            "<p style='margin:0 0 6px 0;font-size:13px;color:#666;'>"
            "Rocky couldn't confirm the property/tenant for these — they're "
            "in <b>_Needs Review</b> under their original names. Move them "
            "into place when you get a minute.</p>"
            f"<ul style='margin:0;padding-left:20px;font-size:13px;'>{items}</ul>"
            + overflow
        )

    return filed_html + review_html


def _build_digest_html(filed: list[dict], review: list[dict],
                       hours: int, vault_root: Path) -> str:
    from html import escape
    return (
        "<div style='font-family:Segoe UI,Arial,sans-serif;color:#222;"
        "max-width:820px;'>"
        "<h2 style='margin:0 0 4px 0;'>The Vault — Daily Digest</h2>"
        f"<p style='margin:0 0 10px 0;color:#666;font-size:13px;'>"
        f"{len(filed) + len(review)} document(s) added in the last "
        f"{hours} hours.</p>"
        f"{digest_body_html(filed, review)}"
        f"<p style='margin:18px 0 0 0;font-size:12px;color:#999;'>"
        f"Shared folder: {escape(str(vault_root))}</p>"
        "</div>"
    )


def vault_digest(config: dict, paths: dict, hours: int, dry_run: bool) -> None:
    """Email the window's Vault additions (and their sources) from rocky@.
    Quiet window = no email at all."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    added: list[dict] = []
    for e in load_catalog(paths):
        if e.get("dry_run"):
            continue
        try:
            ts = datetime.fromisoformat((e.get("ts") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts >= since:
            added.append(e)
    filed = [e for e in added if e.get("disposition") == "filed"]
    review = [e for e in added if e.get("disposition") == "needs_review"]

    if not filed and not review:
        log.info(f"[vault] digest: nothing added in the last {hours}h — "
                 f"no email")
        return

    html_body = _build_digest_html(filed, review, hours, paths["root"])
    recipients = config.get("vault_digest_recipients") or \
        [config.get("user_email", "jbragdon@gallagherllp.com")]
    subject = (f"Rocky — Vault Digest: {len(filed) + len(review)} added "
               f"({datetime.now().strftime('%B %d, %Y')})")

    if dry_run:
        log.info(f"[vault] digest DRY-RUN — would email {recipients}: "
                 f"{len(filed)} filed, {len(review)} needs review "
                 f"(subject: {subject!r})")
        return

    import outbound
    from rocky import get_msal_app, acquire_token  # lazy
    token = acquire_token(get_msal_app(config))
    result = outbound.send_mail_guarded(
        token=token,
        sender_mailbox=config.get("rocky_email", "rocky@gallagherllp.com"),
        to=recipients,
        subject=subject,
        body=html_body,
        body_type="HTML",
    )
    if result.get("sent"):
        log.info(f"[vault] digest sent to {recipients}: {len(filed)} filed, "
                 f"{len(review)} needs review")
        append_activity(paths, {"event": "digest_sent",
                                "recipients": recipients,
                                "filed": len(filed), "needs_review": len(review),
                                "hours": hours})
    else:
        log.warning(f"[vault] digest send FAILED: {result.get('reason')}")


def run_digest_cli(config: dict, data_dir: Path) -> None:
    """Entry point for `rocky.py --vault-digest [--hours N] [--dry-run]`."""
    paths = get_paths(config, data_dir)
    hours_raw = _argv_value("--hours")
    hours = int(hours_raw) if hours_raw else 24
    vault_digest(config, paths, hours, "--dry-run" in sys.argv)


# =============================================================================
# Status
# =============================================================================

def print_status(config: dict, paths: dict) -> None:
    migrate_legacy_state(paths)
    inbox_state = load_state(paths["state_inbox"])
    mail_state = load_state(paths["state_vault_mail"])
    state = load_state(paths["state_dropbox"])
    catalog = [e for e in load_catalog(paths) if not e.get("dry_run")]
    filed = [e for e in catalog if e.get("disposition") == "filed"]
    review = [e for e in catalog if e.get("disposition") == "needs_review"]
    by_type: dict[str, int] = {}
    for e in filed:
        t = e.get("doc_type") or "other"
        by_type[t] = by_type.get(t, 0) + 1

    print(f"The Vault: {paths['root']}")
    print(f"  Documents filed: {len(filed)}"
          + (f"  ({', '.join(f'{DOC_TYPE_LABELS.get(k, k)}: {v}' for k, v in sorted(by_type.items()))})"
             if by_type else ""))
    print(f"  Needs review:    {len(review)}")
    print(f"  Inbox cursor:      {inbox_state.get('inbox_cursor') or '(none — will backfill)'}")
    print(f"  Vault-mail cursor: {mail_state.get('vault_mail_cursor') or '(none — will backfill)'}")
    accounts = config.get("vault_dropbox_accounts") or []
    if accounts:
        for a in accounts:
            name = a.get("name") or "unnamed"
            ready = all(a.get(k) for k in ("app_key", "app_secret", "refresh_token"))
            cursors = (state.get("dropbox") or {}).get(name) or {}
            line = (f"  Dropbox '{name}': "
                    + ("ready" if ready else
                       "NOT AUTHORIZED — run --vault --dropbox-auth " + name)
                    + f", {len(cursors)} folder cursor(s)")
            for link in (a.get("shared_links") or []):
                lname = link.get("name") or "link"
                seen_marks = (state.get("dropbox_links") or {}).get(
                    f"{name}:{lname}") or {}
                line += (f"\n      link '{lname}': "
                         f"{len(seen_marks)} file(s) processed")
            print(line)
    else:
        print("  Dropbox: no accounts configured")


# =============================================================================
# Maintenance — folder cleanup & Needs-Review reclassification
# =============================================================================

def _recent_unfinished_run(paths: dict) -> str | None:
    """Timestamp of a live vault run started in the last 2h with no
    run_summary yet — catalog rewrites must not race an active ingest."""
    try:
        lines = paths["activity"].read_text(
            encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    pending: list[str] = []
    for line in lines[-500:]:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("event") == "run_started" and not ev.get("dry_run"):
            pending.append(ev.get("ts") or "")
        elif ev.get("event") == "run_summary" and pending:
            pending.pop(0)
    for ts in reversed(pending):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if datetime.now(timezone.utc) - dt < timedelta(hours=2):
            return ts
    return None


def _rewrite_catalog(paths: dict, updates: dict[int, dict],
                     drop: set[int]) -> Path:
    """Back up catalog.jsonl, then atomically rewrite it applying per-index
    updates/drops (indexes = load_catalog() order). The catalog is
    append-only in normal operation, so lines appended between read and
    rewrite sit at the tail and are carried over untouched."""
    raw = paths["catalog"].read_text(encoding="utf-8").splitlines()
    backup = paths["meta"] / (
        f"catalog.backup-{datetime.now():%Y%m%d-%H%M%S}.jsonl")
    backup.write_text("\n".join(raw) + "\n", encoding="utf-8")
    out: list[str] = []
    parsed_i = 0
    for line in raw:
        s = line.strip()
        if not s:
            continue
        try:
            json.loads(s)
        except ValueError:
            out.append(line)
            continue
        if parsed_i not in drop:
            out.append(json.dumps(updates[parsed_i], ensure_ascii=False)
                       if parsed_i in updates else line)
        parsed_i += 1
    tmp = paths["catalog"].with_name("catalog.jsonl.tmp")
    tmp.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    tmp.replace(paths["catalog"])
    return backup


def _cluster_tenants(names: list[str], counts: dict[str, int],
                     ) -> tuple[dict[str, str], list[list[str]]]:
    """Group same-person tenant-folder variants. Returns (variant ->
    canonical, ambiguous clusters left alone). A cluster only merges when
    EVERY pair matches — 'Smith, J' must not chain 'Smith, John' to
    'Smith, Jane'."""
    clusters: list[list[str]] = []
    for n in sorted(names):
        for cl in clusters:
            if any(_same_person(n, m, allow_typo=True) for m in cl):
                cl.append(n)
                break
        else:
            clusters.append([n])

    def given_len(n: str) -> int:
        p = _split_tenant(n)
        return len(" ".join(p[1])) if p else 0

    mapping: dict[str, str] = {}
    ambiguous: list[list[str]] = []
    for cl in clusters:
        if len(cl) < 2:
            continue
        if not all(_same_person(a, b, allow_typo=True)
                   for i, a in enumerate(cl) for b in cl[i + 1:]):
            ambiguous.append(sorted(cl))
            continue
        canonical = max(cl, key=lambda n: (given_len(n), counts.get(n, 0), n))
        for n in cl:
            if n != canonical:
                mapping[n] = canonical
    return mapping, ambiguous


def run_cleanup(paths: dict, execute: bool, force: bool) -> None:
    """
    Merge fragmented property/tenant folders and normalize the catalog:
    alias/known-name property merges ('Alula' -> 'Alula at Bridge
    District'), catalog casing normalization ('PARC RIVERSIDE' ->
    'Parc Riverside'), same-person tenant-folder merges ('Holland, D' ->
    'Holland, Deleona'), and removal of byte-identical duplicate files.

    Default is a DRY-RUN that writes the full plan to
    _vault\\cleanup_plan.txt for review; --execute applies it (moves
    files, rewrites the catalog after backing it up, rebuilds the index).
    Re-runnable: a second pass finds nothing left to do.
    """
    from collections import defaultdict
    from rocky import _dedup_path  # lazy

    root = paths["root"]
    catalog = load_catalog(paths)
    filed = [e for e in catalog
             if not e.get("dry_run") and e.get("disposition") == "filed"]

    doc_counts: dict[str, int] = defaultdict(int)
    for e in filed:
        doc_counts[(e.get("property") or "").strip()] += 1

    disk_props = [d.name for d in root.iterdir()
                  if d.is_dir() and not d.name.startswith("_")]

    # --- property resolution: alias -> known spelling -> preferred casing
    known_by_norm: dict[str, str] = {}
    for n in _KNOWN_PROPERTIES:
        known_by_norm.setdefault(_norm_prop(n), n)
    names = set(disk_props) | {p for p in doc_counts if p}
    groups: dict[str, list[str]] = defaultdict(list)
    for n in names:
        groups[_norm_prop(n)].append(n)
    prop_map: dict[str, str] = {}
    for n in names:
        canon = _PROPERTY_ALIASES.get(_norm_prop(n)) \
            or known_by_norm.get(_norm_prop(n))
        if canon is None:
            # Casing-only variants: prefer a non-ALL-CAPS spelling, then
            # the one with the most filed documents.
            canon = sorted(groups[_norm_prop(n)],
                           key=lambda g: (g.isupper(),
                                          -doc_counts.get(g, 0), g))[0]
        prop_map[n] = canon

    # --- gather tenant folders per canonical property
    by_canon: dict[str, dict[str, list[Path]]] = defaultdict(dict)
    for pname in disk_props:
        for tdir in (root / pname).iterdir():
            if tdir.is_dir():
                by_canon[prop_map[pname]].setdefault(tdir.name, []).append(tdir)

    # --- plan: tenant clusters and file moves
    moves: list[tuple[Path, str, str, str]] = []  # (src, prop, tenant, name)
    tenant_map: dict[tuple[str, str], str] = {}
    ambiguous_all: list[tuple[str, list[str]]] = []
    for canon_prop, tenants in sorted(by_canon.items()):
        counts_t = {t: sum(1 for d in dirs for f in d.iterdir() if f.is_file())
                    for t, dirs in tenants.items()}
        cluster, ambiguous = _cluster_tenants(list(tenants), counts_t)
        ambiguous_all += [(canon_prop, a) for a in ambiguous]
        for variant, canonical in cluster.items():
            tenant_map[(canon_prop, variant)] = canonical
        for tname, dirs in sorted(tenants.items()):
            dest_tenant = cluster.get(tname, tname)
            for d in dirs:
                if (_norm_prop(d.parent.name) == _norm_prop(canon_prop)
                        and tname == dest_tenant):
                    continue
                for f in sorted(d.iterdir()):
                    if not f.is_file():
                        continue
                    newname = f.name
                    if dest_tenant != tname:
                        newname = newname.replace(f" - {tname} - ",
                                                  f" - {dest_tenant} - ")
                    moves.append((f, canon_prop, dest_tenant, newname))

    case_renames = sorted(
        (p, prop_map[p]) for p in disk_props
        if prop_map[p] != p and _norm_prop(prop_map[p]) == _norm_prop(p))
    case_map = dict(case_renames)

    # --- plan: byte-identical duplicates (keep the earliest entry)
    by_sha: dict[str, list[int]] = defaultdict(list)
    for i, e in enumerate(catalog):
        if (not e.get("dry_run") and e.get("disposition") == "filed"
                and e.get("sha256")):
            by_sha[e["sha256"]].append(i)
    dup_drop: dict[int, str] = {}
    for sha, idxs in by_sha.items():
        if len(idxs) < 2:
            continue
        keep_rel = catalog[idxs[0]].get("path") or ""
        if not keep_rel or not (root / keep_rel).exists():
            continue
        for i in idxs[1:]:
            rel = catalog[i].get("path") or ""
            if rel and rel != keep_rel and (root / rel).exists():
                dup_drop[i] = rel

    # Catalog-string-only fixes (casing etc.) for entries whose file
    # doesn't move.
    catalog_only = sum(
        1 for e in filed
        if prop_map.get((e.get("property") or "").strip(),
                        (e.get("property") or "").strip())
        != (e.get("property") or "").strip())

    # --- write the plan
    now = datetime.now(timezone.utc)
    plan_lines = [
        f"The Vault — cleanup plan ({now:%Y-%m-%d %H:%M} UTC)",
        f"Mode: {'EXECUTE' if execute else 'DRY-RUN (nothing changed)'}",
        "",
        f"File moves:               {len(moves)}",
        f"Property casing renames:  {len(case_renames)}",
        f"Duplicate files removed:  {len(dup_drop)}",
        f"Catalog property fixes:   {catalog_only} entr(ies)",
        f"Ambiguous tenant groups (LEFT ALONE): {len(ambiguous_all)}",
        "",
        "== Property merges/renames ==",
    ]
    merge_pairs = sorted({(p, c) for p, c in prop_map.items() if p != c})
    for old, new in merge_pairs:
        kind = "casing" if _norm_prop(old) == _norm_prop(new) else "MERGE"
        plan_lines.append(f"  [{kind}] {old}  ->  {new} "
                          f"({doc_counts.get(old, 0)} catalog doc(s))")
    plan_lines += ["", "== Tenant folder merges =="]
    for (cp, variant), canonical in sorted(tenant_map.items()):
        plan_lines.append(f"  {cp}: {variant}  ->  {canonical}")
    plan_lines += ["", "== Ambiguous tenant groups (no action) =="]
    for cp, group in ambiguous_all:
        plan_lines.append(f"  {cp}: {group}")
    plan_lines += ["", "== Duplicate files to remove =="]
    for i, rel in sorted(dup_drop.items()):
        plan_lines.append(f"  {rel}")
    plan_lines += ["", "== File moves =="]
    for f, cp, ct, nm in moves:
        plan_lines.append(f"  {f.relative_to(root)}  ->  {cp}\\{ct}\\{nm}")
    plan_path = paths["meta"] / "cleanup_plan.txt"
    plan_path.write_text("\n".join(plan_lines) + "\n", encoding="utf-8")
    log.info(f"[vault] cleanup plan: {len(moves)} move(s), "
             f"{len(case_renames)} casing rename(s), {len(dup_drop)} "
             f"duplicate(s), {len(ambiguous_all)} ambiguous group(s) — "
             f"written to {plan_path}")
    if not execute:
        log.info("[vault] DRY-RUN — review the plan, then rerun with "
                 "--cleanup --execute")
        return

    busy = _recent_unfinished_run(paths)
    if busy and not force:
        log.error(f"[vault] cleanup aborted: a vault run started {busy} "
                  f"has no run_summary yet. Wait for it (or pass --force "
                  f"if it's a stale crash).")
        return

    # --- execute: file moves
    move_map: dict[str, str] = {}
    source_dirs: set[Path] = set()
    for f, canon_prop, dest_tenant, newname in moves:
        src_rel = str(f.relative_to(root))
        dest_dir = root / canon_prop / dest_tenant
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = _dedup_path(dest_dir / newname)
            f.rename(dest)
        except OSError as err:
            log.warning(f"[vault] cleanup: could not move {src_rel}: {err}")
            continue
        # Keyed casefolded: catalog paths often differ from disk casing
        # ('PARC RIVERSIDE\...' vs 'Parc Riverside\...').
        move_map[src_rel.casefold()] = str(dest.relative_to(root))
        source_dirs.add(f.parent)

    # --- execute: duplicate removals
    deleted: set[int] = set()
    for i, rel in dup_drop.items():
        target = root / move_map.get(rel.casefold(), rel)
        try:
            target.unlink()
            deleted.add(i)
            append_activity(paths, {"event": "duplicate_removed",
                                    "sha256": catalog[i].get("sha256"),
                                    "path": rel})
        except OSError as err:
            log.warning(f"[vault] cleanup: could not delete duplicate "
                        f"{rel}: {err}")

    # --- execute: property-folder casing renames (two-step — Windows
    # won't rename a folder to its own name in a different case directly)
    for old, new in case_renames:
        src, tmp = root / old, root / (new + ".casefix-tmp")
        try:
            src.rename(tmp)
            tmp.rename(root / new)
        except OSError as err:
            log.warning(f"[vault] cleanup: casing rename {old!r} -> "
                        f"{new!r} failed: {err}")

    # --- execute: remove emptied source folders (never untouched ones)
    for d in sorted(source_dirs, key=lambda p: -len(str(p))):
        try:
            d.rmdir()
        except OSError:
            continue
        try:
            d.parent.rmdir()
        except OSError:
            pass

    # --- execute: catalog rewrite
    now_iso = now.isoformat()
    case_map_ci = {k.casefold(): v for k, v in case_map.items()}
    tenant_map_ci = {(cp, v.casefold()): c for (cp, v), c in tenant_map.items()}
    updates: dict[int, dict] = {}
    for i, e in enumerate(catalog):
        if e.get("dry_run") or i in deleted or e.get("disposition") != "filed":
            continue
        new_e = dict(e)
        changed = False
        rel = e.get("path") or ""
        if rel.casefold() in move_map:
            new_e["path"] = move_map[rel.casefold()]
            changed = True
        else:
            parts = rel.split("\\", 1)
            if len(parts) == 2 and parts[0].casefold() in case_map_ci:
                new_e["path"] = case_map_ci[parts[0].casefold()] + "\\" + parts[1]
                changed = True
        p = (e.get("property") or "").strip()
        canon_p = prop_map.get(p, p)
        if canon_p != p:
            new_e["property"] = canon_p
            changed = True
        t = (e.get("tenant") or "").strip()
        canon_t = tenant_map_ci.get((canon_p, t.casefold()))
        if canon_t and canon_t != t:
            new_e["tenant"] = canon_t
            changed = True
        if changed:
            new_e["cleanup_ts"] = now_iso
            updates[i] = new_e

    backup = _rewrite_catalog(paths, updates, drop=deleted)
    append_activity(paths, {"event": "vault_cleanup",
                            "moves": len(move_map),
                            "casing_renames": len(case_renames),
                            "duplicates_removed": len(deleted),
                            "catalog_updates": len(updates),
                            "backup": backup.name})
    rebuild_index(paths)
    log.info(f"[vault] cleanup complete: {len(move_map)} file(s) moved, "
             f"{len(deleted)} duplicate(s) removed, {len(updates)} catalog "
             f"entr(ies) updated (backup: {backup.name})")


def run_reclassify_review(client, paths: dict, limit: int | None,
                          dry_run: bool) -> None:
    """
    Re-run classification over the _Needs Review backlog — most of it is
    scanned PDFs that predate scanned-page support — and file whatever
    now comes back confident. Updates each refiled entry in place (the
    catalog is rewritten once, backed up first) and rebuilds the index.
    Files a human already moved out of _Needs Review are skipped.
    """
    from rocky import extract_text_from_attachment, _dedup_path  # lazy

    catalog = load_catalog(paths)
    targets: list[tuple[int, dict, Path]] = []
    for idx, e in enumerate(catalog):
        if e.get("dry_run") or e.get("disposition") != "needs_review":
            continue
        rel = e.get("path") or ""
        file = paths["root"] / rel
        if rel and file.exists():
            targets.append((idx, e, file))
    if limit is not None:
        targets = targets[:limit]
    log.info(f"[vault] reclassify-review: retrying {len(targets)} file(s)"
             f"{' (capped by --limit)' if limit is not None else ''}")

    context = (
        "These documents were previously placed in the Vault's _Needs "
        "Review folder because they couldn't be confidently identified. "
        "They are being re-examined. The filename shown for each is its "
        "original source path, which often names the property."
    )
    updates: dict[int, dict] = {}
    refiled = 0
    for start in range(0, len(targets), DROPBOX_CLASSIFY_BATCH):
        chunk = targets[start:start + DROPBOX_CLASSIFY_BATCH]
        docs, inputs = [], []
        for idx, e, file in chunk:
            try:
                content = file.read_bytes()
            except OSError as err:
                log.warning(f"[vault] could not read {file.name!r}: {err} — "
                            f"skipping (OneDrive placeholder? run this on "
                            f"the Rocky laptop)")
                continue
            original = e.get("original_name") or file.name
            detail = e.get("source_detail") or {}
            prompt_name = detail.get("path") or detail.get("subject") or original
            text = extract_text_from_attachment(original, "", content)
            docs.append({"filename": prompt_name, "text": text,
                         "pdf_bytes": _scan_pdf_excerpt(original, content,
                                                        text)})
            inputs.append((idx, e, file, original))
        if not docs:
            continue
        metas = classify_documents(client, docs, context,
                                   explicit_submission=False)
        if any(m.get("classification_failed") for m in metas):
            log.warning("[vault] reclassify-review: classification failed — "
                        "stopping; what's left retries on the next "
                        "invocation")
            break
        for (idx, e, file, original), meta in zip(inputs, metas):
            if not meta.get("is_vault_document"):
                continue
            meta = dict(meta)
            prop_raw = (meta.get("property") or "").strip()
            tenant_raw = (meta.get("tenant") or "").strip()
            confidence = float(meta.get("confidence") or 0.0)
            if not (prop_raw and tenant_raw
                    and confidence >= CONFIDENCE_FLOOR):
                continue
            prop = _clean_component(prop_raw)
            existing_prop = _match_existing_property_folder(paths["root"], prop)
            if existing_prop:
                prop = existing_prop
                meta["property"] = existing_prop
            tenant = _clean_component(tenant_raw)
            existing_tenant = _match_existing_tenant(paths["root"] / prop,
                                                     tenant)
            if existing_tenant:
                meta["tenant"] = existing_tenant
                tenant = _clean_component(existing_tenant)
            dest_dir = paths["root"] / prop / tenant
            dest = _dedup_path(dest_dir / _filed_filename(meta, original))
            if not dry_run:
                try:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    file.rename(dest)
                except OSError as err:
                    log.warning(f"[vault] could not refile {file.name!r}: "
                                f"{err}")
                    continue
            updates[idx] = {
                **e,
                "doc_type": meta.get("doc_type"),
                "property": meta.get("property"),
                "tenant": meta.get("tenant"),
                "document_date": _doc_date(meta),
                "confidence": round(confidence, 2),
                "reasoning": meta.get("reasoning"),
                "disposition": "filed",
                "path": str(dest.relative_to(paths["root"])),
                "reclassified_ts": datetime.now(timezone.utc).isoformat(),
            }
            refiled += 1
            append_activity(paths, {"event": "document_refiled",
                                    "sha256": e.get("sha256"),
                                    "original_name": original,
                                    "from": e.get("path"),
                                    "path": updates[idx]["path"],
                                    "dry_run": dry_run})
            log.info(f"[vault] {'DRY-RUN ' if dry_run else ''}refiled: "
                     f"{original!r} -> {updates[idx]['path']}")

    if updates and not dry_run:
        _rewrite_catalog(paths, updates, drop=set())
        rebuild_index(paths)
    log.info(f"[vault] reclassify-review {'DRY-RUN ' if dry_run else ''}"
             f"complete: {refiled} of {len(targets)} refiled")


# =============================================================================
# CLI glue
# =============================================================================

def run_cli(config: dict, data_dir: Path,
            forced_source: str | None = None) -> None:
    """Entry point, called from rocky.py's dispatch. --vault runs every
    source; the split flags (--vault-inbox / --vault-mail /
    --vault-dropbox) pass forced_source so each can be scheduled and
    locked independently."""
    paths = get_paths(config, data_dir)

    if "--dropbox-auth" in sys.argv:
        idx = sys.argv.index("--dropbox-auth")
        if idx + 1 >= len(sys.argv):
            print("Usage: rocky.py --vault --dropbox-auth <account-name>")
            sys.exit(1)
        dropbox_auth_cli(config, sys.argv[idx + 1])
        return

    if "--status" in sys.argv:
        print_status(config, paths)
        return

    ensure_vault_dirs(paths)

    if "--reindex" in sys.argv:
        rebuild_index(paths)
        return

    migrate_legacy_state(paths)
    dry_run = "--dry-run" in sys.argv
    source = forced_source or _argv_value("--source")
    backfill_days = int(_argv_value("--backfill-days")
                        or config.get("vault_backfill_days") or 7)
    limit_raw = _argv_value("--limit")
    limit = int(limit_raw) if limit_raw else None
    max_age_raw = _argv_value("--max-age-days")
    max_age = int(max_age_raw) if max_age_raw else None
    # An explicit CLI --backfill-days rewinds the mail windows past their
    # cursors (config vault_backfill_days still only seeds first runs).
    force_window = _argv_value("--backfill-days") is not None

    _load_grounding(config, paths)

    if "--cleanup" in sys.argv:
        run_cleanup(paths, execute="--execute" in sys.argv,
                    force="--force" in sys.argv)
        return

    from anthropic import Anthropic  # lazy
    from rocky import acquire_app_token  # lazy
    client = Anthropic(api_key=config["anthropic_api_key"])

    if "--reclassify-review" in sys.argv:
        run_reclassify_review(client, paths, limit, dry_run)
        return

    seen = known_hashes(load_catalog(paths))
    totals: dict[str, dict] = {}
    append_activity(paths, {"event": "run_started", "source": source or "all",
                            "dry_run": dry_run})

    graph_token = None
    if source in (None, "inbox", "vault-mail"):
        graph_token = acquire_app_token(config)

    if source in (None, "inbox"):
        inbox_state = load_state(paths["state_inbox"])
        totals["inbox"] = scan_inbox_source(
            client, graph_token, config, paths, inbox_state, seen,
            backfill_days, limit, dry_run, force_window)
        if not dry_run:
            save_state(paths["state_inbox"], inbox_state)

    if source in (None, "vault-mail"):
        mail_state = load_state(paths["state_vault_mail"])
        totals["vault-mail"] = scan_vault_mailbox(
            client, graph_token, config, paths, mail_state, seen,
            backfill_days, limit, dry_run, force_window)
        if not dry_run:
            save_state(paths["state_vault_mail"], mail_state)

    if source in (None, "dropbox"):
        dropbox_state = load_state(paths["state_dropbox"])
        totals["dropbox"] = scan_dropbox(
            client, config, paths, dropbox_state, seen, limit, dry_run,
            max_age)
        if not dry_run:
            save_state(paths["state_dropbox"], dropbox_state)

    if not dry_run:
        rebuild_index(paths)

    summary = {k: v for k, v in totals.items()}
    append_activity(paths, {"event": "run_summary", "dry_run": dry_run,
                            "totals": summary})
    for src, c in totals.items():
        log.info(f"[vault] {src}: {c}")
    log.info(f"[vault] {'DRY-RUN ' if dry_run else ''}run complete.")


def _argv_value(flag: str) -> str | None:
    if flag in sys.argv:
        idx = sys.argv.index(flag)
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    return None
