# Rocky — Session Log

A running record of every working session on Rocky. Each session prepends a new dated entry at the top so the most recent state is always at the start of the file.

---

## How to use this log

**At the START of every session,** read in this order:
1. `BUILD_REFERENCE.md` — the project's architectural ground truth (rarely changes)
2. The first 1–2 entries below in this file — what changed most recently and what was open
3. `instructions.md` — current plain-English rules for the classifier
4. The current state of the code (`Icon/rocky.py`, `review.py`) only when the planned work touches it

**At the END of every session,** add a new entry at the top of the log (above the previous one). Use the template at the bottom of this file. Keep entries tight: bullets, not prose. Include:
- **What changed** — files touched and the *why*, not the diff
- **Decisions made** — anything the future session would otherwise re-debate
- **Open items** — things deferred or blocked
- **Watch-outs** — surprises, bugs found, things future sessions should know

This log is append-only history. Don't rewrite past entries. If a past decision was overturned, write a new entry that says so and links back ("supersedes 2026-04-30 entry on X").

Naming entries: `## Session YYYY-MM-DD — short title`. If multiple sessions in one day, add `(2)`, `(3)`, etc.

---

## Session 2026-05-02 — Big build: case mgmt (Phase D Stages 1/2/3), Phase A safety, deployment plan, Phase 0/A merge

**What changed**

- **Iteration 1.1 (RRID matching).** Added `load_case_index`, `find_rrids_in_text`, `match_email_to_case` to `rocky.py`. Reads `Rocky Case Index.xlsx` from OneDrive each poll. Three-tier matcher: RRID > case number > sender identifier. Surfaces matched case to classifier prompt as context. Logs `rrids_found_in_email`, `matched_rrid`, `match_method` per classification. Added `openpyxl` to `requirements.txt`.
- **Iteration 2 (attachment text extraction).** Replaced metadata-only `fetch_attachment_metadata` with `fetch_attachments` that downloads bytes (16 MB cap). Added `extract_text_from_attachment` (PDF via pypdf, DOCX via python-docx, XLSX via openpyxl, plain text). Classifier prompt now includes extracted text under per-file (5000) and total (20000) char caps. System prompt updated with security note that extracted text is untrusted. Added `pypdf`, `python-docx` to requirements.
- **Phase D Stage 1 (case-folder ingestion).** When RRID-matched, Rocky writes email body (as `.txt` with header) + attachments to `<case>/Raw Documents/` with a `{YYYYMMDDTHHMM}_{8charhash}` prefix (idempotent). Appends `email_ingested` event to per-case `activity.jsonl`. Functions: `find_case_folder` (globs by RRID substring — folders named "Last, First (RRID-XXXX)"), `save_email_to_case`, `append_case_activity`, `_sanitize_filename`. Smoke-tested live against RRID-0001.
- **Phase D Stage 2 (daily folder-update).** New skill: `python rocky.py --folder-update [RRID-XXXX]`. Walks each case folder, classifies new files in `Raw Documents/` via Claude using DYNAMICALLY DISCOVERED subfolders (the case's actual structure — works whether case has BUILD_REFERENCE schema or `litigation-case-setup` schema). Copies (not moves) raw → target subfolder, records to `master_file_index.json`, appends to `activity.jsonl`. Tracks `source_raw` for idempotency on re-run.
- **Phase D Stage 3 (daily case digest).** New skill: `python rocky.py --daily-digest [RRID-XXXX] [--hours N]`. Reads each case's `activity.jsonl` and `master_file_index.json` for the last 24h, plus the latest `Case Status Memorandum*.docx`. One Claude call per active case generates a markdown section (What happened / Recommended next steps / Upcoming dates). Consolidated output at `Rocky Cases/Daily Digests/YYYY-MM-DD.md`. Skips writing entirely if no case had activity.
- **Phase A safety code (three new modules).**
  - `permissions.py` — decodes JWT scope claim at startup; halts with sys.exit(2) if `Mail.Send`/`Mail.Send.Shared`/`Mail.Send.All` is present. Wired in immediately after first token acquisition.
  - `outbound.py` — `send_mail_guarded()` refuses non-`@gallagherllp.com` recipients OR senders. Scaffold; no callers yet (Rocky has no Mail.Send).
  - `kill_switch.py` — scans inbound subjects for "ROCKY STOP" / "ROCKY START" from authorized senders (config-driven, defaults to `user_email`). Writes/clears `state/dormant.flag`. Main loop checks `is_dormant()` each poll; if dormant, advances cursor but skips classification/ingestion. Manual flag deletion also wakes Rocky.
- **Deployment architecture.** Decided: Rocky runs ONLY on the dedicated Rocky laptop. Personal laptop is the dev workstation. Code flows via private GitHub repo + a wrapper script (`run_rocky.py`) on the Rocky laptop that does `git pull` every 5 minutes and restarts Rocky on code change or crash. Wrapper uses Ctrl+Break for graceful shutdown on Windows, force-kills after 20s grace. Launched at boot via Task Scheduler. New section "Git deployment pipeline" added to BUILD_REFERENCE.md.
- **Phase 0 / Phase A merge.** Original plan ran Phase 0 on James's primary laptop before migrating to a dedicated machine in Phase A. Collapsed: Rocky runs on the dedicated laptop from day one. Validation feedback loop is unchanged because git push gives identical dev iteration regardless of runtime location.
- **New files.** `run_rocky.py` (wrapper), `permissions.py`, `outbound.py`, `kill_switch.py`, `.gitignore` (covers `config.json`, `state/`, `*.log`, `*.jsonl`, `Azure ID Info.txt`, `__pycache__/`, OS junk, with `!Icon/*.png` and `!Icon/*.ico` exceptions), `TASKS.md` (ordered Phase 1 checklist).
- **Memory updates.** Saved `onedrive_legacy_path.md` (folder is "OneDrive - gejlaw.com" not gallagherllp — DO NOT "fix") and `onedrive_files_on_demand.md` (placeholder files unreadable to agents — pin folder locally).
- **Handoff doc.** `C:\Users\jbragdon\Desktop\Sunday Building Plans 2.md` written for tomorrow's session in plain English.
- **`config.example.json`** got `kill_switch_authorized` field with comment.

**Decisions made**

- Rocky runs only on the production laptop; primary laptop is dev only.
- Git pipeline over OneDrive code-sync. Reasons: atomic file copy (no mid-execution sync corruption), `git diff` review step, clean rollback, secrets stay out of cloud.
- Stage 2 dynamically discovers each case's subfolders rather than hardcoding a schema. Adapts to whatever structure exists (RRID-0001 has `Drafts/Fact Research/Legal Research/Miscellaneous/Pleadings`).
- Stage 1 saves use `_filename_prefix` = `{receivedYYYYMMDDTHHMM}_{md5(messageId)[:8]}`. Idempotent on re-run.
- Stage 2 COPIES raw → target subfolder rather than moving. Raw stays as immutable record; can be re-classified if rules change.
- Daily digest is a file (`Daily Digests/YYYY-MM-DD.md`) not an email until `Mail.Send` arrives on Rocky's account.
- Co-counsel digest routing deferred — case index has no co-counsel column yet.
- Phase A safety code split into separate modules per BUILD_REFERENCE.md (`permissions.py`, `outbound.py`, `kill_switch.py`), not bundled into `rocky.py`. rocky.py was already ~1300 lines.
- Forbidden-scope check is a blocklist (`Mail.Send` family) not an allowlist — keeps the audit non-brittle as iterations 3+ add `Mail.ReadWrite`.

**Open items**

- **GitHub setup** still to do tomorrow (Sunday). Not yet pushed. See TASKS.md Phase 1 step 2.
- **IT pre-reqs (3 left).** Azure AD app permissions + admin consent; Exchange mailbox delegation for `rocky@`; Conditional Access exception for device code flow on `rocky@`. Mailbox itself and Rocky laptop hardware are done.
- **`--folder-update` has not been run on real data.** RRID-0001 has 42 existing PDFs in `Raw Documents/`; running it would file all of them via 42 Claude API calls. Decision deferred to user. Recommended: try RRID-0002 first.
- **`--daily-digest` has not been run on real data.** No reason it shouldn't work, but live test deferred until at least one case has activity.
- **Daily-log-file rotation** for `rocky.log` not implemented. Easy to add when the log gets unwieldy.

**Watch-outs**

- **OneDrive Files On-Demand will bite.** The Rocky Cases folder MUST be pinned "Always keep on this device" on the Rocky laptop, or all the Phase D writes silently fail (the `xlsx` permission denied we hit during this session is the symptom). Memory file `onedrive_files_on_demand.md` documents this; flagged in Sunday Building Plans 2.md.
- **OneDrive folder name uses legacy `gejlaw.com`** even though the firm is now `gallagherllp.com`. Local sync folder name is a one-time artifact from before the rename. `CASE_INDEX_PATH` and `ROCKY_CASES_ROOT` in `rocky.py` use `gejlaw.com` deliberately. Memory `onedrive_legacy_path.md` documents.
- **42 PDFs in RRID-0001's `Raw Documents/`** are pre-existing case files (from before Stage 1 was wired). They're not from email ingestion. Running `--folder-update` will process them; that's a real $$$ + filesystem-mutation event.
- **Test coverage is offline-only for tonight's additions.** Functions tested in isolation against synthetic data and the live RRID-0001 folder. No end-to-end test of the email→classify→save→file→digest pipeline because that requires a live token + real inbound traffic.
- **Case index columns "Case No. Identifier" and "Sender Identifiers"** are blank for both current cases. Until populated, only RRID-tagged emails will match. Worth populating when convenient (e.g., DC court automated-notice email address for RRID-0001).
- **`outbound.py` has no callers yet.** When future code adds drafting/sending, USE THIS FUNCTION. Do not add a parallel non-guarded send path.
- **Smoke test scripts** were deliberately deleted after each test. They were one-off; leaving them in the repo would clutter and potentially leak debugging artifacts.

---

## Session 2026-04-30 — Folder rename to Rocky, build references filed, Setup B confirmed, rocky.py moved to root

**What changed**
- Folder renamed Minotaur → Rocky on James's Desktop. Code, docs, and configs verified clean of "minotaur"; only historical strings remain in `Icon/rocky.log` (old log entries, intentionally preserved) and one Bash permission entry in `.claude/settings.local.json` (harmless — record of the `mv` command itself).
- `Rocky_Build_Plan.docx` saved to the folder. Source: `C:\Users\jbragdon\Desktop\Minotaur_Build_Plan_Revised.docx`. All "Minotaur"/"minotaur" replaced with "Rocky"/"rocky" inside the document XML; formatting preserved (renamed via direct ZIP rewrite of the docx, not a Word round-trip, so styles/headers/footers survived). Verified: docx already used `gallagherllp.com` correctly, so no domain edits needed.
- `BUILD_REFERENCE.md` saved to the folder — condensed architectural reference covering Phase 0 → Phase E, permission progression, classifier schema, and Phase A production target. **Read this first in any future session.**
- `BUILD_REFERENCE.md` updated: all `gejlaw.com` references replaced with `gallagherllp.com` to match the firm's actual domain.
- `rocky.py` moved from `Icon/` subfolder to the project root. Icon image assets (`rocky.ico`, `rocky.png`, etc.) remain in `Icon/` where they belong.
- This `SESSIONS.md` created.

**Decisions made**
- **Firm domain is `gallagherllp.com`.** All references in BUILD_REFERENCE.md updated. (Build plan docx and current config.json were already correct.)
- **Inbox source for Rocky-specific requests: Setup B (separate M365 account).** Rocky will get her own account `rocky@gallagherllp.com` with delegated read access to James's mailbox. Device login uses Rocky's identity; `user_email` in `config.json` is the *target* mailbox (James's).
- **`rocky.py` lives at the project root**, not in `Icon/`. The earlier `Icon/` location was non-standard and would have caused `Path(__file__).parent` to resolve incorrectly relative to `instructions.md`, `state/`, `config.json`, etc. — all of which are at root.
- The build plan + reference are the source of truth for architecture going forward; ad-hoc decisions in conversation should be reflected back into one of those files (or this log) before the session ends.

**Open items**
- IT setup for `rocky@gallagherllp.com` is not yet done. Required: provision the account, grant delegated read on James's mailbox via Exchange, Conditional Access exception for device code flow, Azure AD app registration "Rocky" with `Mail.Read`. See `BUILD_REFERENCE.md` § Authentication setup (Setup B).
- **Code for Rocky to read her own inbox is NOT yet drafted.** Original ask in this session, deferred pending the IT setup above. When ready: extend `fetch_new_emails` to poll *both* James's inbox (current behavior) and Rocky's own inbox; tag each classification record with `source_mailbox`; same classifier, same schema. Forward-to-Rocky pattern (per BUILD_REFERENCE.md § Key UX patterns) will be the primary use case for Rocky's own inbox.

**Watch-outs**
- `Icon/rocky.log` is the orphaned log from when `rocky.py` was running from `Icon/` (Minotaur era). When `rocky.py` is next run from root, a fresh `rocky.log` will be created at root via `LOG_PATH = ROOT / "rocky.log"`. The orphan is harmless but can be deleted any time.
- The `anthropic_api_key` is stored in plaintext in `config.json`. Acceptable for Phase 0 on James's personal laptop. Must be addressed before Phase A (dedicated machine, broader access).
- The Python sandbox on this machine cannot execute scripts from `C:\Users\jbragdon\AppData\Roaming\Claude\local-agent-mode-sessions\...`. Future sessions doing docx/pptx/xlsx work via the Anthropic skills will need to copy script files locally or use stdlib alternatives (zipfile, etc.). This is what we did to rename the build plan.
- `classifications.jsonl` and `review.jsonl` do not yet exist in the folder — Rocky has not been run end-to-end yet.

---

## Template for future entries

```
## Session YYYY-MM-DD — short title

**What changed**
- file path — what + why (not the diff)

**Decisions made**
- the decision and the reason

**Open items**
- thing deferred + what future session needs to do it

**Watch-outs**
- surprise, gotcha, or non-obvious constraint future-you should know
```
