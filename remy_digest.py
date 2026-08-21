"""
Remy digest — read the Remy repo, write the day's plain-English digest, email it.

This replaces the old two-piece arrangement (a Claude Code scheduled task on
James's dev laptop generated + pushed the digest; Rocky only fetched and
emailed it). Rocky now does the whole job, so the digest no longer depends on
James's laptop being awake at 5:00 PM.

The primary source is the shared OneDrive activity folder
(Program Files/Rocky/Remy Activity Log): every Claude Code session on James's
or Shane's machine drops its plain-English session note there the moment it
commits (the rule lives in the REMY repo's CLAUDE.md). Reading notes from
OneDrive means the digest works with no GitHub token and captures both
developers' work without waiting on a push.

GitHub is optional enrichment: when remy_github_token is configured, Rocky
also lists the day's commits over the REST API (no clone, no git binary,
never touches a working tree) and commits the finished digest back to the
repo so the archive stays browsable there.

Flow (--remy-digest, 5:30 PM weekdays):
    1. Find the low-water mark: the newest digest date recorded in local
       state (and, with a token, the newest digest/YYYY-MM-DD.md in the
       repo). No prior digest means "the last 7 days".
    2. Read session notes newer than that from the OneDrive activity folder
       — these are the primary source. With a token, also list commits since
       then (dropping digest-only commits) and any repo session notes.
    3. Claude writes the digest for two lawyers who don't code.
    4. Save it locally and into the activity folder's Digests subfolder;
       with a write-capable token, also commit it back to the repo. Then
       email it from rocky@ to James and Shane.

Quiet days are quiet: no new notes and no new commits means no file, no
commit, no email.

Public API:
    run_cli(config, data_dir) -> None

Config:
    remy_activity_dir       The shared OneDrive activity folder. If unset,
                            Rocky looks for "Program Files\\Rocky\\Remy
                            Activity Log" under the usual OneDrive roots.
    remy_github_token       Optional. Fine-grained PAT, repo jbragdon21/remy,
                            Contents: Read and write (write is what lets Rocky
                            commit the digest; read-only still enriches with
                            commit history; absent means OneDrive notes only).
    remy_github_repo        Default "jbragdon21/remy".
    remy_digest_recipients  Who gets the email. Firm addresses only — the
                            outbound allowlist refuses anything else.
    remy_digest_mailbox     Sender. Defaults to rocky_email.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from outbound import send_mail_guarded

log = logging.getLogger("rocky.remy_digest")

# Same model rocky.py uses for its other digest writing.
CLAUDE_MODEL = "claude-sonnet-4-5"

GITHUB_API_BASE = "https://api.github.com"
DEFAULT_REPO = "jbragdon21/remy"
DEFAULT_BRANCH = "main"

# No prior digest in the repo — look back this far rather than all of history.
COLD_START_DAYS = 7

# digest/2026-08-05.md — the digest files themselves.
_DIGEST_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
# digest/sessions/2026-08-05-jb-batch-notice-fix.md — the session notes.
_SESSION_PATH_RE = re.compile(r"^digest/sessions/.+\.md$", re.IGNORECASE)
# 2026-08-05-jb-batch-notice-fix.md — a note dropped in the activity folder.
_ACTIVITY_NOTE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-.+\.md$", re.IGNORECASE)

# Default locations of the shared OneDrive activity folder.
_ACTIVITY_SUBPATH = Path("Program Files") / "Rocky" / "Remy Activity Log"


def locate_activity_dir(config: dict) -> Path | None:
    """Find the shared OneDrive activity folder, or None if it isn't here."""
    import os
    configured = config.get("remy_activity_dir")
    candidates = [Path(configured)] if configured else []
    for env in ("OneDriveCommercial", "OneDrive"):
        root = os.environ.get(env)
        if root:
            candidates.append(Path(root) / _ACTIVITY_SUBPATH)
    home = Path.home()
    candidates.append(home / "OneDrive - gejlaw.com" / _ACTIVITY_SUBPATH)
    # Shared-folder shortcuts can land the folder directly under any
    # "OneDrive*" root, without the Program Files\Rocky prefix.
    for od_root in home.glob("OneDrive*"):
        candidates.append(od_root / _ACTIVITY_SUBPATH)
        candidates.append(od_root / "Remy Activity Log")
        candidates.append(od_root / "Rocky" / "Remy Activity Log")
    for c in candidates:
        if c.is_dir():
            return c
    return None


def gather_local_notes(activity_dir: Path, after_date: str | None,
                       cold_start_floor: str) -> list[dict]:
    """Session notes in the activity folder newer than the last digest.

    A note counts if the YYYY-MM-DD in its filename is strictly after the
    last digest date (notes ride with same-day digests already sent), or on
    a cold start, on/after the look-back floor. Digests themselves and
    non-note files are ignored.
    """
    floor = after_date or cold_start_floor
    notes: list[dict] = []
    for f in sorted(activity_dir.glob("*.md")):
        m = _ACTIVITY_NOTE_RE.match(f.name)
        if not m:
            continue
        note_date = m.group(1)
        if after_date is not None:
            if note_date <= after_date:
                continue
        elif note_date < cold_start_floor:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError as e:
            log.warning(f"[remy-digest] Could not read {f.name}: {e}")
            continue
        notes.append({"path": f.name, "text": text})
    log.info(f"[remy-digest] {len(notes)} activity note(s) in {activity_dir} "
             f"newer than {floor}.")
    return notes


# =============================================================================
# GitHub REST helpers
# =============================================================================

def _gh_headers(token: str, raw: bool = False) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _gh_get(repo: str, token: str, path: str, params: dict | None = None,
            raw: bool = False) -> requests.Response:
    url = f"{GITHUB_API_BASE}/repos/{repo}/{path.lstrip('/')}"
    return requests.get(url, headers=_gh_headers(token, raw=raw),
                        params=params or {}, timeout=30)


def list_digest_dates(repo: str, token: str) -> dict[str, str]:
    """Map YYYY-MM-DD -> blob sha for every digest/<date>.md already committed."""
    resp = _gh_get(repo, token, "contents/digest")
    if resp.status_code == 404:
        log.warning(f"[remy-digest] No digest/ folder in {repo}.")
        return {}
    resp.raise_for_status()
    dates: dict[str, str] = {}
    for entry in resp.json():
        if entry.get("type") != "file":
            continue
        m = _DIGEST_FILE_RE.match(entry.get("name", ""))
        if m:
            dates[m.group(1)] = entry.get("sha", "")
    return dates


def list_commits_since(repo: str, token: str, since_utc: datetime,
                       branch: str) -> list[dict]:
    """Commits on `branch` newer than since_utc, oldest first."""
    resp = _gh_get(repo, token, "commits", params={
        "sha": branch,
        "since": since_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "per_page": 100,
    })
    resp.raise_for_status()
    return list(reversed(resp.json()))


def commit_files(repo: str, token: str, sha: str) -> list[str]:
    """Paths changed by one commit. Empty list if GitHub won't say."""
    resp = _gh_get(repo, token, f"commits/{sha}")
    if resp.status_code != 200:
        log.warning(f"[remy-digest] Could not read commit {sha[:7]}: "
                    f"{resp.status_code}. Treating as a code commit.")
        return []
    return [f.get("filename", "") for f in resp.json().get("files", [])]


def get_file_text(repo: str, token: str, path: str) -> str | None:
    """Current text of a file on the default branch, or None if it's gone."""
    resp = _gh_get(repo, token, f"contents/{path}", raw=True)
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        log.warning(f"[remy-digest] Could not read {path}: {resp.status_code}")
        return None
    return resp.text


def put_digest_file(repo: str, token: str, branch: str, date_str: str,
                    text: str, existing_sha: str | None = None) -> dict:
    """Commit digest/<date>.md via the contents API."""
    url = f"{GITHUB_API_BASE}/repos/{repo}/contents/digest/{date_str}.md"
    payload = {
        "message": (f"Daily digest {date_str}\n\n"
                    f"Written by Rocky (rocky@gallagherllp.com)."),
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "branch": branch,
        "committer": {"name": "Rocky", "email": "rocky@gallagherllp.com"},
    }
    if existing_sha:
        payload["sha"] = existing_sha

    try:
        resp = requests.put(url, headers=_gh_headers(token), json=payload,
                            timeout=30)
    except requests.RequestException as e:
        return {"committed": False, "reason": f"network_error: {e}"}

    if resp.status_code in (200, 201):
        sha = (resp.json().get("commit") or {}).get("sha", "")
        return {"committed": True, "commit_sha": sha}
    if resp.status_code in (403, 404):
        # Read-only token, or the PAT lost access to the repo.
        return {"committed": False, "reason": f"no_write_access_{resp.status_code}"}
    return {"committed": False,
            "reason": f"github_error_{resp.status_code}: {resp.text[:200]}"}


# =============================================================================
# Gathering material
# =============================================================================

def _is_digest_only(paths: list[str]) -> bool:
    """True if every file the commit touched lives under digest/."""
    return bool(paths) and all(p.startswith("digest/") for p in paths)


def gather_material(repo: str, token: str, branch: str,
                    since_utc: datetime) -> dict:
    """Collect code commits and the session notes they touched."""
    all_commits = list_commits_since(repo, token, since_utc, branch)
    log.info(f"[remy-digest] {len(all_commits)} commit(s) since "
             f"{since_utc:%Y-%m-%d %H:%M} UTC.")

    code_commits: list[dict] = []
    note_paths: list[str] = []

    for c in all_commits:
        sha = c.get("sha", "")
        paths = commit_files(repo, token, sha)
        # Session notes ride along with code commits, but a later commit may
        # amend one — collect from every commit, digest-only ones included.
        for p in paths:
            if _SESSION_PATH_RE.match(p) and p not in note_paths:
                note_paths.append(p)
        if _is_digest_only(paths):
            continue
        commit_meta = c.get("commit") or {}
        author = (commit_meta.get("author") or {})
        code_commits.append({
            "sha": sha[:7],
            "author": author.get("name") or "unknown",
            "date": (author.get("date") or "")[:10],
            "subject": (commit_meta.get("message") or "").split("\n")[0],
            "files": paths,
        })

    notes: list[dict] = []
    for p in note_paths:
        text = get_file_text(repo, token, p)
        if text:
            notes.append({"path": p, "text": text})

    log.info(f"[remy-digest] {len(code_commits)} code commit(s), "
             f"{len(notes)} session note(s).")
    return {"code_commits": code_commits, "session_notes": notes}


# =============================================================================
# Writing the digest
# =============================================================================

DIGEST_SYSTEM_PROMPT = """\
You are Rocky, writing the daily digest of changes to Remy, an internal legal
document-drafting app. Your readers are James Bragdon and Shane — two lawyers
who do not code. They want to know what the app does differently today, in
language they'd use themselves.

FORMAT — follow exactly:
- First line: `# Remy digest — <Month D, YYYY>`
- Then one short intro sentence saying how many changes there were and who made
  them.
- Then one `## ` section per author (James, Shane), each with bullets in plain
  English: which tab or feature changed, and what a user would notice.
- Under roughly 300 words total.

RULES:
- No file paths, no function names, no commit hashes, no programming jargon.
- Session notes are your primary source — they were written in plain English by
  the person doing the work. Commit subjects are a fallback.
- If a commit has no session note and its purpose is unclear from the subject,
  say so honestly at the level of detail you actually have ("James made an
  update to the batch rent notice feature"). Never invent a reason or a
  benefit.
- Group related commits into one bullet rather than listing each separately.

PRIVACY — absolute, overrides everything above:
Never include client names, tenant names, resident names, unit or property
addresses, ledger amounts, dollar figures tied to a matter, case numbers, or
the contents of any legal document — even if a commit message or session note
contains them. Describe changes generically ("fixed how late fees are excluded
from rent complaints"), never by the matter that prompted them.

Output only the digest markdown. No preamble, no code fences."""


def _pretty_date(date_str: str) -> str:
    """2026-08-05 -> August 5, 2026 (no platform-specific strftime flags)."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt:%B} {dt.day}, {dt.year}"


def write_digest(client, date_str: str, material: dict) -> str:
    """Claude call: turn commits + session notes into the digest markdown."""
    pretty_date = _pretty_date(date_str)

    commit_lines = [
        f"- [{c['date']}] {c['author']}: {c['subject']}\n"
        f"  (areas touched: {', '.join(c['files'][:8]) or 'unknown'})"
        for c in material["code_commits"]
    ]
    note_blocks = [
        f"--- session note: {n['path'].rsplit('/', 1)[-1]} ---\n{n['text']}"
        for n in material["session_notes"]
    ]

    user_prompt = f"""TODAY: {pretty_date}

CODE COMMITS SINCE THE LAST DIGEST ({len(material['code_commits'])}):
{chr(10).join(commit_lines) if commit_lines else '(none)'}

SESSION NOTES ({len(material['session_notes'])}) — your primary source:
{chr(10).join(note_blocks) if note_blocks else '(none — work from the commit subjects above)'}

Write the digest."""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,
        system=DIGEST_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = response.content[0].text.strip()
    # Claude occasionally wraps markdown in a fence despite the instruction.
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n|\n```$", "", text)
    return text.strip() + "\n"


# =============================================================================
# Email rendering
# =============================================================================

def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _inline(text: str) -> str:
    """Escape, then honor **bold** and `code`."""
    out = _escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"`(.+?)`", r"<code>\1</code>", out)
    return out


def digest_to_html(md: str) -> str:
    """Render the digest markdown as a simple, readable HTML email."""
    parts: list[str] = []
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            parts.append("</ul>")
            in_list = False

    for raw_line in md.split("\n"):
        line = raw_line.strip()
        if not line:
            close_list()
            continue
        if line.startswith("# "):
            close_list()
            parts.append(
                f'<h1 style="margin:0 0 4px 0;font-size:20px;font-weight:600;'
                f'color:#1a1a1a;">{_inline(line[2:])}</h1>')
        elif line.startswith("## "):
            close_list()
            parts.append(
                f'<h2 style="margin:18px 0 6px 0;font-size:15px;font-weight:600;'
                f'color:#1a1a1a;border-bottom:1px solid #e5e5e5;'
                f'padding-bottom:4px;">{_inline(line[3:])}</h2>')
        elif line.startswith("### "):
            close_list()
            parts.append(
                f'<h3 style="margin:12px 0 4px 0;font-size:14px;font-weight:600;'
                f'color:#1a1a1a;">{_inline(line[4:])}</h3>')
        elif line.startswith("- ") or line.startswith("* "):
            if not in_list:
                parts.append('<ul style="margin:0 0 8px 0;padding-left:20px;">')
                in_list = True
            parts.append(
                f'<li style="margin-bottom:6px;color:#333333;font-size:14px;'
                f'line-height:1.5;">{_inline(line[2:])}</li>')
        else:
            close_list()
            parts.append(
                f'<p style="margin:6px 0;color:#333333;font-size:14px;'
                f'line-height:1.5;">{_inline(line)}</p>')
    close_list()

    body = "\n".join(parts)
    return f"""<html><body style="margin:0;padding:0;background-color:#f5f5f5;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="background-color:#f5f5f5;padding:24px 0;">
  <tr><td align="center">
    <table role="presentation" width="640" cellpadding="0" cellspacing="0"
           style="background-color:#ffffff;border:1px solid #e5e5e5;
                  border-radius:6px;padding:28px 32px;font-family:
                  -apple-system,Segoe UI,Helvetica,Arial,sans-serif;">
      <tr><td>
{body}
        <p style="margin:24px 0 0 0;padding-top:12px;border-top:1px solid #e5e5e5;
                  color:#888888;font-size:12px;line-height:1.5;">
          Written by Rocky from the day's commits and session notes. Every
          digest is also kept in the Remy repository under digest/.
        </p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""


# =============================================================================
# Local copies and one-email-per-day state
# =============================================================================

def _state_path(data_dir: Path) -> Path:
    return data_dir / "state" / "remy_digest.json"


def _load_state(data_dir: Path) -> dict:
    path = _state_path(data_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning(f"[remy-digest] Could not read {path}; starting fresh.")
        return {}


def _save_state(data_dir: Path, state: dict) -> None:
    path = _state_path(data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as e:
        log.warning(f"[remy-digest] Could not save state: {e}")


def _save_local_copy(data_dir: Path, date_str: str, text: str) -> Path | None:
    """Keep a local copy so a failed push never loses the day's digest."""
    out_dir = data_dir / "remy_digests"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{date_str}.md"
        path.write_text(text, encoding="utf-8")
        return path
    except OSError as e:
        log.warning(f"[remy-digest] Could not write local copy: {e}")
        return None


# =============================================================================
# Main job
# =============================================================================

def remy_digest(
    client,
    token: str | None,
    config: dict,
    data_dir: Path,
    date_str: str,
    dry_run: bool = False,
    push: bool = True,
    email: bool = True,
    force: bool = False,
) -> dict:
    """Generate, commit, and email today's Remy digest. Never raises."""
    repo = config.get("remy_github_repo", DEFAULT_REPO)
    branch = config.get("remy_github_branch", DEFAULT_BRANCH)
    gh_token = config.get("remy_github_token")

    activity_dir = locate_activity_dir(config)
    if not activity_dir and not gh_token:
        log.error("[remy-digest] Neither the OneDrive activity folder nor a "
                  "remy_github_token is available — nothing to read. Set "
                  "remy_activity_dir in config.json (the shared 'Remy "
                  "Activity Log' folder) or add a GitHub token.")
        return {"written": False, "reason": "no_sources"}
    if not activity_dir:
        log.warning("[remy-digest] Activity folder not found — working from "
                    "GitHub only. Notes dropped on other machines won't be "
                    "seen until they're pushed.")
    if not gh_token:
        log.info("[remy-digest] No remy_github_token — working from the "
                 "activity folder only. Commit history won't enrich the "
                 "digest and the repo's digest archive won't be updated.")

    state = _load_state(data_dir)
    state_last = state.get("last_digest_date")

    existing: dict[str, str] = {}
    if gh_token:
        try:
            existing = list_digest_dates(repo, gh_token)
        except (requests.RequestException, requests.HTTPError) as e:
            if not activity_dir:
                log.error(f"[remy-digest] GitHub unreachable: {e}")
                return {"written": False, "reason": f"github_unreachable: {e}"}
            log.warning(f"[remy-digest] GitHub unreachable ({e}) — continuing "
                        f"from the activity folder only.")
            gh_token = None

    if not force and (date_str in existing or state_last == date_str):
        log.info(f"[remy-digest] A {date_str} digest was already written — "
                 f"nothing to do. (--force to rewrite it.)")
        return {"written": False, "reason": "already_exists"}

    prior = sorted(d for d in existing if d < date_str)
    repo_last = prior[-1] if prior else None
    known = [d for d in (repo_last, state_last) if d and d < date_str]
    last_date = max(known) if known else None
    cold_start_floor = (datetime.now()
                        - timedelta(days=COLD_START_DAYS)).strftime("%Y-%m-%d")
    if last_date:
        # Everything after the end of the last digest's day.
        since_local = datetime.strptime(last_date, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59)
    else:
        since_local = datetime.now() - timedelta(days=COLD_START_DAYS)
        log.info(f"[remy-digest] No earlier digest found — looking back "
                 f"{COLD_START_DAYS} days.")
    since_utc = since_local.astimezone(timezone.utc)
    log.info(f"[remy-digest] Last digest: {last_date or 'none'} | window "
             f"starts {since_utc:%Y-%m-%d %H:%M} UTC | activity folder: "
             f"{activity_dir or 'none'} | GitHub: "
             f"{repo if gh_token else 'off'}")

    material: dict = {"code_commits": [], "session_notes": []}
    if gh_token:
        try:
            material = gather_material(repo, gh_token, branch, since_utc)
        except (requests.RequestException, requests.HTTPError) as e:
            if not activity_dir:
                log.error(f"[remy-digest] Could not read repo history: {e}")
                return {"written": False,
                        "reason": f"history_read_failed: {e}"}
            log.warning(f"[remy-digest] Could not read repo history ({e}) — "
                        f"continuing from the activity folder only.")
            material = {"code_commits": [], "session_notes": []}

    if activity_dir:
        local_notes = gather_local_notes(activity_dir, last_date,
                                         cold_start_floor)
        # Merge by filename; the OneDrive copy wins — it can carry edits
        # that haven't been pushed to GitHub yet.
        by_name = {n["path"].rsplit("/", 1)[-1]: n
                   for n in material["session_notes"]}
        for n in local_notes:
            by_name[n["path"]] = n
        material["session_notes"] = [by_name[k] for k in sorted(by_name)]

    if not material["code_commits"] and not material["session_notes"]:
        log.info("[remy-digest] No new session notes or code commits since "
                 "the last digest — quiet day. Nothing written, nothing "
                 "emailed.")
        return {"written": False, "reason": "no_activity"}

    if dry_run:
        log.info(f"[remy-digest] DRY RUN — would summarize "
                 f"{len(material['code_commits'])} commit(s) and "
                 f"{len(material['session_notes'])} session note(s) into "
                 f"the {date_str} digest, then email it. No Claude call, no "
                 f"commit, no mail.")
        for c in material["code_commits"]:
            log.info(f"    {c['date']} {c['author']}: {c['subject']}")
        for n in material["session_notes"]:
            log.info(f"    note: {n['path']}")
        return {"written": False, "reason": "dry_run",
                "commits": len(material["code_commits"]),
                "notes": len(material["session_notes"])}

    try:
        text = write_digest(client, date_str, material)
    except Exception as e:
        log.error(f"[remy-digest] Claude could not write the digest: {e}")
        return {"written": False, "reason": f"generation_failed: {e}"}

    local_path = _save_local_copy(data_dir, date_str, text)
    result: dict = {
        "written": True,
        "date": date_str,
        "commits": len(material["code_commits"]),
        "notes": len(material["session_notes"]),
        "local_path": str(local_path) if local_path else None,
        "committed": False,
        "emailed": False,
    }

    # Remember the low-water mark so the next run (with or without GitHub)
    # picks up where this one left off.
    state["last_digest_date"] = date_str
    _save_state(data_dir, state)

    # Drop a copy in the activity folder so James and Shane can browse the
    # archive from OneDrive without GitHub.
    if activity_dir:
        try:
            digests_dir = activity_dir / "Digests"
            digests_dir.mkdir(exist_ok=True)
            (digests_dir / f"{date_str}.md").write_text(text,
                                                        encoding="utf-8")
            log.info(f"[remy-digest] Copied the digest to {digests_dir}.")
        except OSError as e:
            log.warning(f"[remy-digest] Could not copy the digest to the "
                        f"activity folder: {e}")

    # Commit it back so the archive on GitHub stays complete.
    if push and not gh_token:
        log.info("[remy-digest] No GitHub token — digest not committed to "
                 "the repo archive.")
    elif push:
        put = put_digest_file(repo, gh_token, branch, date_str, text,
                              existing_sha=existing.get(date_str))
        result["committed"] = put.get("committed", False)
        result["commit_reason"] = put.get("reason")
        if put.get("committed"):
            log.info(f"[remy-digest] Committed digest/{date_str}.md to {repo} "
                     f"({put.get('commit_sha', '')[:7]}).")
        elif str(put.get("reason", "")).startswith("no_write_access"):
            log.warning("[remy-digest] GitHub token is read-only — the digest "
                        "was NOT added to the repo. Emailing it anyway. Give "
                        "the token 'Contents: Read and write' to restore the "
                        "on-GitHub archive.")
        else:
            log.warning(f"[remy-digest] Could not commit the digest: "
                        f"{put.get('reason')}. Emailing it anyway; the local "
                        f"copy is at {local_path}.")
    else:
        log.info("[remy-digest] --no-push — digest not committed to GitHub.")

    # Email it.
    state = _load_state(data_dir)
    if email:
        if state.get("last_emailed") == date_str and not force:
            log.info(f"[remy-digest] A {date_str} digest was already emailed — "
                     f"not sending a second one. (--force to override.)")
            result["email_reason"] = "already_emailed_today"
        else:
            recipients = config.get("remy_digest_recipients") or [
                config.get("user_email", "jbragdon@gallagherllp.com")
            ]
            if len(recipients) < 2:
                log.warning(f"[remy-digest] Only {len(recipients)} recipient(s) "
                            f"configured ({recipients}). Add Shane's address to "
                            f"remy_digest_recipients in config.json.")
            sender = config.get("remy_digest_mailbox") or config.get(
                "rocky_email", "rocky@gallagherllp.com")
            pretty = _pretty_date(date_str)
            sent = send_mail_guarded(
                token=token,
                sender_mailbox=sender,
                to=recipients,
                subject=f"Remy update digest — {pretty}",
                body=digest_to_html(text),
                body_type="HTML",
            )
            result["emailed"] = sent.get("sent", False)
            result["email_reason"] = sent.get("reason")
            if sent.get("sent"):
                log.info(f"[remy-digest] Emailed to {recipients} from {sender}.")
                state["last_emailed"] = date_str
                _save_state(data_dir, state)
            else:
                log.warning(f"[remy-digest] Email failed: {sent.get('reason')}. "
                            f"The digest is in the repo and at {local_path}.")
    else:
        log.info("[remy-digest] --no-email — digest not sent.")

    return result


def _argv_value(flag: str) -> str | None:
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            return args[i + 1]
    return None


def run_cli(config: dict, data_dir: Path) -> None:
    """Entry point, called from rocky.py's dispatch for --remy-digest."""
    log.info("=" * 60)
    log.info("Rocky — Remy digest (read the activity log, write it, send it)")
    log.info("=" * 60)

    date_str = _argv_value("--date")
    if not date_str and "--yesterday" in sys.argv:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        log.error(f"Invalid --date {date_str!r}; expected YYYY-MM-DD.")
        sys.exit(1)

    dry_run = "--dry-run" in sys.argv
    push = "--no-push" not in sys.argv
    email = "--no-email" not in sys.argv and not dry_run
    force = "--force" in sys.argv

    log.info(f"Date: {date_str} | Dry run: {dry_run} | Push: {push} | "
             f"Email: {email} | Force: {force}")

    from anthropic import Anthropic  # lazy — dry runs never call Claude
    client = None if dry_run else Anthropic(api_key=config["anthropic_api_key"])

    # Only the send step needs Graph.
    token = None
    if email:
        from rocky import acquire_token, get_msal_app  # lazy — avoids a cycle
        from permissions import audit_token_scopes
        token = acquire_token(get_msal_app(config))
        audit_token_scopes(token)

    result = remy_digest(
        client=client, token=token, config=config, data_dir=data_dir,
        date_str=date_str, dry_run=dry_run, push=push, email=email, force=force,
    )

    if result.get("written"):
        log.info(f"Remy digest {date_str}: {result['commits']} change(s) "
                 f"summarized | committed: {result['committed']} | "
                 f"emailed: {result['emailed']}")
    else:
        log.info(f"No Remy digest written. Reason: {result.get('reason')}")
    log.info("=" * 60)
