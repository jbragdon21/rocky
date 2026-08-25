# Rocky — Task List

Status tracking for Rocky features and deployment. Code is the source of truth; this file tracks what's done, what's active, and what's next.

Last updated: 2026-05-15

---

## Deployment model

Rocky is deployed as a single-file .exe built with PyInstaller:

1. James edits source on dev laptop (`C:\Users\jbragdon\Desktop\Rocky\`)
2. `python build_exe.py` builds `dist/rocky.exe` and copies it to `OneDrive - gejlaw.com\Program Files\Rocky\rocky.exe`
3. OneDrive syncs the .exe to the Rocky laptop
4. Rocky laptop runs the new .exe on next scheduled invocation

The Rocky laptop is **fully operational** as of 2026-05-15. `run_rocky.py` is a crash-recovery wrapper (restart on crash, no git operations).

---

## Completed — Phase 1 (production deployment)

All done. Rocky laptop is live and running.

- [x] Provision `rocky@gallagherllp.com` (M365 mailbox)
- [x] Rocky laptop provisioned (Windows 11, firm domain)
- [x] Azure AD app registration "Rocky" — `Mail.ReadWrite`, `Calendars.ReadWrite` (admin consent granted). `Mail.Send` on Rocky's own mailbox granted for digest emails.
- [x] Delegated access: `rocky@gallagherllp.com` → James's mailbox + calendar
- [x] Conditional Access exception for device code flow
- [x] Python + dependencies installed on Rocky laptop
- [x] `config.json` created with real credentials
- [x] First-time device code login completed, token cached
- [x] Task Scheduler entries configured
- [x] .exe build/deploy pipeline working via OneDrive

---

## Completed — implemented features

### Core pipeline
- [x] Email classifier (iteration 1 + 2) — reads Rocky's inbox, classifies Remy requests
- [x] Attachment text extraction (PDF / DOCX / XLSX / TXT)
- [x] `--daily-cases` — fetches today's emails from per-case Outlook folders, summarizes via Claude, saves to case folders
- [x] `--daily-run` — reads each case's `_project/instructions.md`, runs Claude-driven file actions
- [x] `--daily-digest` — consolidated daily case digest (markdown + HTML email)
- [x] ECF download — detects `uscourts.gov` court notification emails, downloads pleading PDFs to case folders

### Digest broadening (2026-05-15)
- [x] Digest reads Cowork activity from `_spine_text/_activity.json` in addition to `activity.jsonl`
- [x] Digest reads `Master Case Summary/*.docx` as fallback for status memo
- [x] Digest reads `## Rocky Digest` section from per-case `CLAUDE.md` for case-specific instructions
- [x] Naive timestamps in `_activity.json` treated as UTC (no comparison crash)
- [x] Session bookends filtered out of digest; spine events formatted with summary + actor

### Additional daily commands
- [x] `--steve-todo` — generates Steve Metzger's daily to-do list from his inbox (7:30 AM)
- [x] `--ella-digest` — Ella Aiken's daily case digest from her delegated mailbox (5:00 PM)
- [x] `--ella-auth` — one-time auth setup for Ella's mailbox access

### Safety modules
- [x] `permissions.py` — blocks `Mail.Send` on James's delegated mailbox at token level
- [x] `outbound.py` — `send_mail_guarded()` refuses non-`@gallagherllp.com` recipients
- [x] `kill_switch.py` — ROCKY STOP / ROCKY START via email from authorized senders

### Pending LLT Matters (code complete 2026-06-03)
- [x] `pending_llt.py` — SharePoint download, spreadsheet parsing, property matching, draft creation
- [x] Jinja2 email template at `OneDrive/.../Rocky reference files/templates/pending_llt_email.html`
- [x] `--pending-llt [--dry-run]` CLI command wired into `rocky.py`
- [x] Graph API: SharePoint site/drive resolution, file download
- [x] Property matching: normalized names + "LLT Name" column support in contacts sheet
- [x] `GRAPH_SCOPES` updated to include `Sites.Read.All`

### Remy integration (code complete, activation pending)
- [x] `remy_runner.py` — parses "Run Remy:" form-email, invokes `remy_cli.py`, delivers output
- [x] Classifier recognizes seven Remy categories
- [x] Wired into main loop behind `config.enable_remy_invocation` (default: `false`)
- [x] `paralegal_remy_request_template.md` created

---

## Scheduled commands reference

| Command | Schedule | What it does |
|---------|----------|-------------|
| `--daily-cases [RRID-XXXX]` | 4:00 PM | Fetch emails from Outlook folders, summarize, save to case folders |
| `--daily-run [RRID-XXXX]` | 4:30 PM | Run per-case instruction-driven folder skills |
| `--daily-digest [RRID-XXXX] [--hours N]` | 5:00 PM | Generate consolidated case digest (file + email) |
| `--steve-todo` | 7:30 AM | Steve's daily to-do list from inbox |
| `--ella-digest [--hours N]` | 5:00 PM | Ella's daily case digest |
| `--monitor-remy` | 24/7 (via wrapper) | Poll Rocky's inbox for Remy requests |
| `--pending-llt [--dry-run]` | On demand | Download LLT + contacts from SharePoint, draft status emails by property |
| `--remy-digest [--date YYYY-MM-DD] [--dry-run]` | 5:30 PM weekdays | Summarize the day's Remy app changes from GitHub, commit the digest, email James + Shane |

---

## Active — open items

### Pending LLT Matters pipeline (code complete, activation pending)
- [ ] IT: Add `Sites.Read.All` (delegated) to Rocky app registration + admin consent
- [ ] Re-authenticate Rocky on laptop (device code flow — new scope consent)
- [ ] Add pending LLT config fields to `config.json` on Rocky laptop (see `config.example.json`)
- [ ] Verify contacts file path on SharePoint matches config (`General/BMC Contacts (1).xlsx`)
- [ ] Run `--pending-llt --dry-run` to validate property matching
- [ ] Add "LLT Name" column to BMC Contacts spreadsheet for unmatched properties
- [ ] Run `--pending-llt` live to create drafts, review in Outlook

### Remy digest (code complete 2026-08-05, activation pending)
- [x] `remy_digest.py` — reads jbragdon21/remy over the GitHub API, writes the
      plain-English digest via Claude, commits it to `digest/`, emails it
- [x] `--remy-digest` wired into `rocky.py`, dashboard registry, and the build
- [ ] Mint a fine-grained GitHub PAT: repo `jbragdon21/remy`, **Contents: Read
      and write**, long expiration → `remy_github_token` in config.json
- [ ] Add Shane's email address to `remy_digest_recipients`
- [ ] Rebuild/deploy `rocky.exe`, add the 5:30 PM weekday scheduled task
- [ ] Verify with `--remy-digest --dry-run`, then a live run
- [ ] After a clean live run: delete the `remy-daily-digest` Claude Code
      scheduled task on the dev laptop and update `digest/README.md` +
      `digest/ROCKY.md` in the REMY repo (both still describe the old
      generate-on-dev-laptop / Rocky-only-emails split)

### Remy activation
- [ ] Send test "Run Remy:" email to `rocky@gallagherllp.com`, verify end-to-end
- [ ] Flip `enable_remy_invocation` to `true` in `config.json` on Rocky laptop
- [ ] Distribute `paralegal_remy_request_template.md` to paralegals

### Digest setup across case folders
- [ ] Run Cowork digest setup instructions in each case folder (see `Rocky Cases/Rocky Digest Setup Instructions.md`)
- [ ] Verify digest picks up Cowork activity from `_spine_text/_activity.json`

### Classifier tuning (ongoing)
- [ ] Spot-check `classifications.jsonl` periodically for false negatives
- [ ] Update `instructions.md` as needed (push via .exe rebuild)

---

## Future — Paul Inbox Review (standalone)

See BUILD_REFERENCE.md for full design. Helps Paul triage thousands of inbox messages via two passes: conversation-based sorting and Claude-assisted archive triage. Output is an Excel for Paul to review before anything moves.

### Prerequisites
- [ ] Get `Mail.ReadWrite` delegated access for Paul's mailbox (same pattern as Ella delegation)
- [ ] `--paul-auth` one-time auth setup for Paul's mailbox access
- [ ] Create "Inbox Archive" folder in Paul's mailbox

### Pass 1: Conversation Sort
- [ ] Fetch Paul's inbox metadata via Graph API
- [ ] Match inbox messages to conversation threads with newer siblings already in named folders
- [ ] Normalized-subject fallback (port `SortByConversation` VBA logic to Python — strip `[EXTERNAL]`, `RE:`, `FW:`)
- [ ] Generate Sheet 1 of Excel output (Subject, From, Date, Read?, Proposed Folder, Reason, Approve Y/N)

### Pass 2: Archive Triage
- [ ] Batch remaining inbox metadata to Claude for classification (archive / needs-review / keep)
- [ ] Conservative threshold — only propose high-confidence disposables (read newsletters, automated alerts, old read correspondence)
- [ ] Generate Sheet 2 of Excel output (Subject, From, Date, Read?, Category, Confidence, Approve Y/N)

### Execution & Safety
- [ ] `--paul-inbox --dry-run` generates Excel only (default mode)
- [ ] `--paul-inbox --execute <approved.xlsx>` reads approved Excel, moves only Y rows
- [ ] Log every move to `paul_inbox_activity.jsonl` with original folder ID (undo capability)
- [ ] Nothing deleted — only moved to named folders or Inbox Archive

---

## Future — Phase E and beyond

See BUILD_REFERENCE.md for full roadmap. Key items:

- [ ] Case-management Claude call — dedicated prompt for case-matched emails (currently logged but skip Claude)
- [ ] Tracked-client capability — running communications log + on-demand call agendas for 5-15 high-volume clients
- [ ] Merge morning digest (Phase B, inbox triage) with case digest (Phase D) into single consolidated report
- [ ] Schema versioning for `case.json`
- [ ] Multi-attorney support beyond James
- [ ] SharePoint migration for case files (eliminates OneDrive Files On-Demand issues)

---

## Configuration reference

**Rocky laptop runtime data:** `C:\Rocky\` (config.json, state/, logs)

**Deployed .exe:** `OneDrive - gejlaw.com\Program Files\Rocky\rocky.exe`

**Case files:** `OneDrive - gejlaw.com\Rocky Cases\`

**Remy:** `C:\Users\jbragdon\Desktop\REMY\remy_cli.py` (config default)

**Key config fields** (in `config.json`, see `config.example.json` for schema):
- `user_email` — James's mailbox (read target)
- `rocky_email` — Rocky's own mailbox (sender for digests)
- `anthropic_api_key` — Claude API key
- `enable_remy_invocation` — `false` until Remy is validated end-to-end
- `remy_delivery_recipients` — who gets Remy draft emails
- `kill_switch_authorized` — who can send ROCKY STOP/START
