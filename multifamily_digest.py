"""
Multifamily Digest — one daily email for the multifamily practice
=================================================================

    python rocky.py --multifamily-digest [--hours N] [--dry-run]  (5:30 PM daily)

One DRAFT in James's Drafts folder (changed 2026-08-29 — Rocky used to
email it from rocky@) covering the day across the multifamily
processes, in four sections (empty sections are omitted):

    CERTIFIED MAIL   — LetterStream activity from the LetterStream
                       process: requests preauth'd, released (with cost
                       and who approved), mailed (with tracking),
                       declined/failed.
    AFFIDAVITS       — Certified Mailing Affidavits sent for approval,
                       filed in The Vault, declined; proof-of-mailing
                       email submissions.
    THE VAULT        — the day's Vault additions (same content the
                       standalone Vault Digest showed — this digest
                       SUBSUMES it; don't schedule both).
    REMY             — the day's Remy software-development digest, read
                       from the local copy --remy-digest wrote (schedule
                       remy-digest EARLIER, e.g. 17:15 with --no-email;
                       Shane is in the default draft recipients — this
                       digest subsumes the standalone Remy digest email;
                       the GitHub digest/ archive commit still happens).
    STILL PENDING    — snapshot of everything awaiting a human: mailings
                       awaiting a release YES, mailings in flight,
                       affidavits awaiting approval.

Quiet window (nothing in the first three sections) = no draft at all,
even if items are pending — the reminder pass nags about those.

Delivery model (same as the Maple Digest): the digest is created as a
DRAFT in James's Drafts folder, pre-addressed to the multifamily group
(_DEFAULT_DRAFT_RECIPIENTS below; override with config
multifamily_digest_draft_recipients). James reviews and hits send from
his own account — Rocky never sends this email. The old
multifamily_digest_recipients / vault_digest_recipients keys applied
only to the retired send-from-rocky@ model and are now ignored.
Window is the last N hours (default 24), matching the Vault Digest's
model.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

log = logging.getLogger("rocky.mfdigest")

# The multifamily group the draft is pre-addressed to (set 2026-08-29).
# These go on a draft in James's mailbox — he reviews and sends — so the
# outbound allowlist doesn't apply here (though all are firm-internal
# anyway). Override with config "multifamily_digest_draft_recipients".
# (New key on purpose: the retired send-from-rocky@ model used
# "multifamily_digest_recipients" for a different audience — reusing it
# could silently mis-address the draft if an old config.json lingers.)
_DEFAULT_DRAFT_RECIPIENTS = [
    "hmondragon@gallagherllp.com",   # Hailey Mondragon
    "swenger@gallagherllp.com",      # Sarah Wenger
    "sronan@gallagherllp.com",       # Shane Ronan
    "mbrown@gallagherllp.com",       # Michael L. Brown
    "afrantzis@gallagherllp.com",    # Adamandia Frantzis
    "mkobylski@gallagherllp.com",    # Mia R. Kobylski
    "gomara@gallagherllp.com",       # Gina O'Mara
    "caraviakis@gallagherllp.com",   # Christina Araviakis
    "pgoranin@gallagherllp.com",     # Paul O. Goranin
]

_STYLE_H3 = "margin:18px 0 6px 0;"
_STYLE_UL = "margin:0;padding-left:20px;font-size:13px;"
_STYLE_LI = "margin:2px 0;"
_MUTED = "color:#666;"


# =============================================================================
# Data gathering
# =============================================================================

def _read_activity(path: Path, since: datetime) -> list[dict]:
    events: list[dict] = []
    if not path.exists():
        return events
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("dry_run"):
                continue
            try:
                ts = datetime.fromisoformat(
                    (e.get("ts") or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts >= since:
                events.append(e)
    return events


def _mail_info(state: dict, tag: str) -> dict:
    """Best-known details for a [CM-####] across the state queues."""
    for queue in ("mail_pending", "in_flight", "mail_done"):
        entry = (state.get(queue) or {}).get(tag)
        if entry:
            return entry
    return {}


def _pretty(iso: str | None) -> str:
    try:
        dt = datetime.strptime((iso or "")[:10], "%Y-%m-%d")
        return f"{dt.month}/{dt.day}/{dt.year}"
    except ValueError:
        return iso or ""


# =============================================================================
# HTML sections
# =============================================================================

def _ul(items: list[str]) -> str:
    lis = "".join(f"<li style='{_STYLE_LI}'>{i}</li>" for i in items)
    return f"<ul style='{_STYLE_UL}'>{lis}</ul>"


def _h3(title: str, count: int | None = None) -> str:
    suffix = f" ({count})" if count is not None else ""
    return f"<h3 style='{_STYLE_H3}'>{escape(title)}{suffix}</h3>"


def _muted(text: str) -> str:
    return f"<span style='{_MUTED}'>{escape(text)}</span>"


def certified_mail_section(events: list[dict], state: dict) -> str:
    items: list[str] = []
    for e in events:
        kind = e.get("event")
        tag = e.get("tag") or ""
        info = _mail_info(state, tag)
        label = info.get("label") or e.get("file") or ""
        if kind == "mail_preauth":
            cost = e.get("cost")
            items.append(
                f"<b>Requested</b> [{escape(tag)}] {escape(label)} to "
                f"{escape(str(e.get('recipient') or '?'))} — "
                f"{e.get('pages')} pages"
                + (f", ${cost:.2f}" if isinstance(cost, (int, float)) else "")
                + " " + _muted(f"by {e.get('from') or '?'}"))
        elif kind == "mail_released":
            cost = e.get("cost")
            aff = ("with affidavit" if e.get("want_affidavit", True)
                   else "no affidavit")
            items.append(
                f"<b>Released</b> [{escape(tag)}] {escape(label)}"
                + (f" — ${cost:.2f}" if isinstance(cost, (int, float)) else "")
                + f", {aff} " + _muted(f"by {e.get('by') or '?'}"))
        elif kind == "mail_mailed":
            aff = e.get("affidavit_tag")
            items.append(
                f"<b>Mailed</b> [{escape(tag)}] {escape(label)} — tracking "
                f"{escape(str(e.get('tracking') or '?'))} "
                + _muted(f"affidavit [{aff}] sent for approval" if aff
                         else "no affidavit requested"))
        elif kind == "mail_declined":
            items.append(f"<b>Cancelled</b> [{escape(tag)}] {escape(label)} "
                         + _muted(f"by {e.get('by') or '?'}"))
        elif kind == "mail_release_failed":
            items.append(f"<b>RELEASE FAILED</b> [{escape(tag)}] "
                         + _muted(str(e.get("reason") or "")[:120]))
        elif kind == "mail_request_failed":
            items.append(f"<b>Request failed</b> {escape(str(e.get('file') or ''))} "
                         + _muted(f"from {e.get('from') or '?'}: "
                                  f"{str(e.get('reason') or '')[:120]}"))
    if not items:
        return ""
    return _h3("Certified mail (LetterStream)", len(items)) + _ul(items)


def affidavits_section(events: list[dict]) -> str:
    items: list[str] = []
    for e in events:
        kind = e.get("event")
        tag = e.get("tag") or ""
        if kind == "affidavit_proposed":
            items.append(
                f"<b>Sent for approval</b> [{escape(tag)}] "
                f"{escape(str(e.get('tenant') or '?'))} — "
                f"{escape(str(e.get('property') or 'unknown property'))}, "
                f"mailed {_pretty(e.get('mail_date'))}")
        elif kind == "affidavit_approved":
            paths = e.get("vault_paths") or []
            items.append(
                f"<b>Filed in The Vault</b> [{escape(tag)}] "
                + _muted(f"approved by {e.get('approved_by') or '?'} — "
                         + "; ".join(str(p) for p in paths)))
        elif kind == "affidavit_declined":
            items.append(f"<b>Declined</b> [{escape(tag)}] "
                         + _muted(f"by {e.get('declined_by') or '?'} — "
                                  f"{str(e.get('reply') or '')[:120]}"))
        elif kind == "proof_submission":
            pdfs = e.get("pdfs") or []
            items.append(
                f"<b>Proof of mailing emailed in</b> "
                + _muted(f"by {e.get('from') or '?'} — "
                         f"{', '.join(str(p) for p in pdfs)} "
                         f"({e.get('proposed')} affidavit(s) proposed)"))
    if not items:
        return ""
    return _h3("Affidavits", len(items)) + _ul(items)


def vault_section(config: dict, data_dir: Path, since: datetime) -> str:
    import vault
    paths = vault.get_paths(config, data_dir)
    added = []
    for e in vault.load_catalog(paths):
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
        return ""
    return (f"<h2 style='margin:22px 0 4px 0;font-size:16px;'>The Vault</h2>"
            + vault.digest_body_html(filed, review))


def remy_section(data_dir: Path, since: datetime) -> str:
    """The day's Remy development digest, if --remy-digest wrote one.

    Reads the local copy --remy-digest keeps under data_dir\\remy_digests\\
    (schedule remy-digest BEFORE this digest — e.g. 17:15 vs 17:45).
    Strictly newer than the window-start DATE, so yesterday's digest never
    repeats in today's email."""
    import remy_digest
    out_dir = data_dir / "remy_digests"
    if not out_dir.exists():
        return ""
    # Digest filenames carry LOCAL dates — compare against the window
    # start's local date, or an evening run (UTC already on tomorrow's
    # date) would wrongly exclude today's digest. ALL digests in the
    # window are included (one for the daily run; several for a wide
    # lookback like --hours 336), oldest first.
    since_local_date = since.astimezone().date()
    matches: list[Path] = []
    for p in out_dir.glob("????-??-??.md"):
        try:
            d = datetime.strptime(p.stem, "%Y-%m-%d")
        except ValueError:
            continue
        if d.date() > since_local_date:
            matches.append(p)
    if not matches:
        return ""
    fragments: list[str] = []
    for p in sorted(matches):
        try:
            md = p.read_text(encoding="utf-8")
        except OSError as e:
            log.warning(f"[mf-digest] Could not read Remy digest {p}: {e}")
            continue
        fragments.append(remy_digest.digest_fragment_html(md))
    if not fragments:
        return ""
    return ("<h2 style='margin:22px 0 4px 0;font-size:16px;'>Remy — "
            "software development</h2>"
            + "\n".join(fragments))


def pending_section(state: dict) -> str:
    items: list[str] = []
    for tag, e in sorted((state.get("mail_pending") or {}).items()):
        cost = e.get("cost")
        items.append(
            f"<b>Awaiting release</b> [{escape(tag)}] {escape(e.get('label') or '')} "
            f"to {escape(str((e.get('recipient') or {}).get('name1') or '?'))}"
            + (f", ${cost:.2f}" if isinstance(cost, (int, float)) else "")
            + " " + _muted(f"asked {(e.get('created') or '')[:10]}, "
                           f"waiting on {e.get('requester') or '?'}"))
    for tag, e in sorted((state.get("in_flight") or {}).items()):
        items.append(
            f"<b>In the mail stream</b> [{escape(tag)}] "
            f"{escape(e.get('label') or '')} "
            + _muted(f"released {(e.get('released') or '')[:10]}"
                     + ("" if e.get("want_affidavit", True)
                        else ", no affidavit")))
    for tag, e in sorted((state.get("pending") or {}).items()):
        f = e.get("fields") or {}
        items.append(
            f"<b>Affidavit awaiting approval</b> [{escape(tag)}] "
            f"{escape(str(f.get('tenant') or '?'))} — "
            f"{escape(str(f.get('property') or '?'))} "
            + _muted(f"sent {(e.get('created') or '')[:10]}"))
    if not items:
        return ""
    return _h3("Still pending", len(items)) + _ul(items)


# =============================================================================
# Assembly + send
# =============================================================================

def _banner(config: dict) -> tuple[str, list[dict]]:
    """The masthead <img> plus its inline attachment, when the banner
    image exists. Default: Icon\\multifamily_banner.png (bundled with
    the exe — drop the image in the repo's Icon folder and rebuild);
    override with config multifamily_digest_banner (a path on the
    runtime machine; "" disables)."""
    from rocky import _BUNDLE_DIR  # lazy
    raw = config.get("multifamily_digest_banner")
    if raw == "":
        return "", []
    path = Path(raw) if raw else (_BUNDLE_DIR / "Icon"
                                  / "multifamily_banner.png")
    if not path.exists():
        return "", []
    html = ("<img src='cid:mf_banner' alt=\"Gallagher's Daily Multifamily "
            "Group Digest\" style='display:block;width:100%;"
            "max-width:860px;border-radius:6px;margin:0 0 12px 0;'>")
    return html, [{"path": str(path), "name": path.name,
                   "contentId": "mf_banner"}]


def build_digest(config: dict, data_dir: Path,
                 hours: int) -> tuple[str | None, list[dict]]:
    """(digest HTML, inline attachments) — HTML is None when the window
    was quiet."""
    import mailing_affidavits as ma

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    aff_paths = ma.get_paths(config, data_dir)
    events = _read_activity(aff_paths["activity"], since)
    state = ma.load_state(aff_paths)

    mail_html = certified_mail_section(events, state)
    aff_html = affidavits_section(events)
    vault_html = vault_section(config, data_dir, since)
    remy_html = remy_section(data_dir, since)
    if not (mail_html or aff_html or vault_html or remy_html):
        return None, []
    pending_html = pending_section(state)

    # The banner carries the title when present; the plain <h2> is the
    # fallback so a missing image never leaves the email headless.
    banner_html, banner_att = _banner(config)
    title_html = ("" if banner_html else
                  "<h2 style='margin:0 0 4px 0;'>Multifamily — Daily "
                  "Digest</h2>")

    return (
        "<div style='font-family:Segoe UI,Arial,sans-serif;color:#222;"
        "max-width:860px;'>"
        f"{banner_html}{title_html}"
        f"<p style='margin:0 0 10px 0;{_MUTED}font-size:13px;'>"
        f"Certified mail, affidavits, Vault activity, and Remy "
        f"development in the last {hours} hours.</p>"
        f"{mail_html}{aff_html}{vault_html}{remy_html}{pending_html}"
        f"<p style='margin:18px 0 0 0;font-size:12px;color:#999;'>"
        f"Rocky — LetterStream ({escape(str(aff_paths['root']))}), "
        f"The Vault, and the Remy repo digest. This digest replaces the "
        f"standalone Vault and Remy digest emails.</p>"
        "</div>"
    ), banner_att


def run_cli(config: dict, data_dir: Path) -> None:
    hours_raw = _argv_value("--hours")
    hours = int(hours_raw) if hours_raw else 24
    dry_run = "--dry-run" in sys.argv

    html_body, attachments = build_digest(config, data_dir, hours)
    if html_body is None:
        log.info(f"[mf-digest] nothing happened in the last {hours}h — "
                 f"no draft")
        return

    recipients = (config.get("multifamily_digest_draft_recipients")
                  or _DEFAULT_DRAFT_RECIPIENTS)
    mailbox = config.get("multifamily_digest_mailbox",
                         config.get("user_email", "jbragdon@gallagherllp.com"))
    subject = (f"Multifamily Digest "
               f"({datetime.now().strftime('%B %d, %Y')})")

    if dry_run:
        log.info(f"[mf-digest] DRY-RUN — would draft into {mailbox}'s "
                 f"Drafts, addressed to {recipients} "
                 f"(subject: {subject!r}, {len(html_body)} chars)")
        print(html_body)
        return

    import pending_llt
    from rocky import acquire_token, audit_token_scopes, get_msal_app  # lazy
    token = acquire_token(get_msal_app(config))
    audit_token_scopes(token)
    result = pending_llt.create_draft_email(
        token=token,
        user_email=mailbox,
        to_addresses=recipients,
        subject=subject,
        html_body=html_body,
        attachments=attachments or None,
    )
    if result.get("created"):
        log.info(f"[mf-digest] drafted into {mailbox}'s Drafts, addressed "
                 f"to {recipients} (message {result.get('message_id')}). "
                 f"James reviews and sends.")
    else:
        log.warning(f"[mf-digest] draft FAILED: {result.get('reason')}")


def _argv_value(flag: str) -> str | None:
    if flag in sys.argv:
        idx = sys.argv.index(flag)
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    return None
