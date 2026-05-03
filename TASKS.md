# Rocky — Task List

Ordered to-do for getting Rocky into production. Top-down: do these in order. Each task notes its blocker (what has to be true before you can start it).

When you finish a task, replace `[ ]` with `[x]` and move on. When a task spawns sub-tasks or surfaces a decision, note it inline below the task.

---

## Architectural decision (2026-05-02)

**Rocky runs only on the dedicated Rocky laptop.** James's primary laptop is the dev workstation, never a runtime. Code edits flow: edit on primary laptop → `git push` → Rocky laptop pulls within 5 minutes and restarts. This collapses what was originally Phase 0 (validate on James's laptop) and the front-end of Phase A (migrate to production) into a single phase.

## Code status as of 2026-05-02

All code is in `C:\Users\jbragdon\Desktop\Rocky\` and tested offline. NOT YET pushed to GitHub. Done:

- ✅ Iteration 1 classifier (Mail.Read, RRID matching, case-index lookup)
- ✅ Iteration 2 attachment text extraction (PDF / DOCX / XLSX / TXT)
- ✅ Phase D Stage 1 — case-folder ingestion (saves matched emails to OneDrive)
- ✅ Phase D Stage 2 — daily folder-update skill (`--folder-update`)
- ✅ Phase D Stage 3 — daily case digest skill (`--daily-digest`)
- ✅ Phase A safety — `permissions.py`, `outbound.py`, `kill_switch.py`
- ✅ `run_rocky.py` supervisor wrapper for the Rocky laptop
- ✅ `.gitignore` covering all secrets and runtime state

Pending: GitHub push, IT pre-reqs, Rocky-laptop install, live validation.

---

## Phase 1 — Get Rocky onto the production laptop

### 1. Pre-requisites (IT-dependent)

- [x] **Provision `rocky@gallagherllp.com`** (M365 mailbox, standard license) — done 2026-05-02
- [x] **Confirm Rocky laptop is provisioned** (Windows 10/11, on the firm domain, hardware ready) — done 2026-05-02
- [ ] **Azure AD app registration "Rocky"** — confirm it exists and has delegated `Mail.Read` permission, with admin consent granted. (If not yet created: Azure portal → App registrations → New registration → name "Rocky", supported account types: single tenant. Then API permissions → Add → Microsoft Graph → Delegated → `Mail.Read` → Grant admin consent.)
- [ ] **Grant `rocky@gallagherllp.com` delegated read on James's mailbox** via Exchange admin (Recipients → Mailboxes → james → Mailbox delegation → Read permissions → add `rocky@gallagherllp.com`)
- [ ] **Conditional Access exception** for device code flow on `rocky@gallagherllp.com` (firm policy blocks it by default — confirm exception is in place before attempting first device code login)

### 2. Set up the git deployment pipeline

This is the FIRST technical step on both laptops, before any Rocky code runs in production. See the detailed walkthrough in chat (or BUILD_REFERENCE.md § Git deployment when filed) for layperson step-by-step.

**On James's laptop:**

- [ ] Create GitHub account (free, private repos included)
- [ ] Install Git for Windows (defaults are fine)
- [ ] Run `git config --global user.name` and `user.email` once
- [ ] Create a private GitHub repo named `rocky`
- [ ] Add `.gitignore` to the local Rocky folder — must list `config.json`, `state/`, `*.log`, `*.jsonl`, `__pycache__/`
- [ ] `git init`, `git add .`, `git status` — VERIFY config.json and state/ are NOT in the list before committing
- [ ] `git commit`, `git remote add origin`, `git push -u origin main`
- [ ] Verify on github.com that secrets did NOT get pushed

**On the Rocky laptop:**

Note: log into the Rocky laptop with your normal credentials for installation work. The `rocky@gallagherllp.com` identity is used only at the device-code-login step — that's an authentication event for the Python program, not a Windows login.

- [ ] Install Git for Windows (defaults are fine)
- [ ] Install Python 3.12 (NOT 3.13 — MSAL libraries lag). Check "Add Python to PATH" during install.
- [ ] In Git Bash: `cd /c/` then `git clone https://github.com/YOUR-USERNAME/rocky.git Rocky`. The folder `C:\Rocky` should now exist with all the code.
- [ ] `cd /c/Rocky` then `pip install -r requirements.txt`
- [ ] Copy `config.example.json` → `config.json`. Fill in real values:
  - `client_id` — from the Azure AD app registration "Rocky" (Overview tab)
  - `tenant_id` — from the Azure AD app registration (Overview tab)
  - `user_email` — `jbragdon@gallagherllp.com` (the *target* mailbox, not Rocky's)
  - `anthropic_api_key` — your Anthropic API key
- [x] ~~Save `run_rocky.py` to `C:\Rocky\`~~ — done; lives in repo root, will arrive automatically when you `git clone`.
- [ ] **First-time device code login.** Run `python rocky.py` once manually. It will print a code and a URL. On any browser, go to that URL, enter the code, and sign in as `rocky@gallagherllp.com`. (This is the moment Conditional Access exception matters — if device code flow is blocked, login will fail here.) Confirm the token is cached at `state/token_cache.json`. Watch the console for a poll cycle or two to confirm classifications are flowing into `classifications.jsonl`. Then Ctrl+C to stop.
- [ ] Test the wrapper manually: `python run_rocky.py`. Verify it logs "Wrapper starting up" → "Starting Rocky..." in `wrapper.log`. From James's laptop, push a trivial change (add a comment to `instructions.md`, commit, push). Within 5 minutes, the Rocky laptop's `wrapper.log` should show "Code updated. Restarting Rocky." Ctrl+C to stop the wrapper.
- [ ] Set up Task Scheduler entry "Rocky Wrapper":
  - General → "Run whether user is logged on or not", "Run with highest privileges"
  - Triggers → At startup, delay 1 minute
  - Actions → Start a program: full path to `python.exe`, arguments: `C:\Rocky\run_rocky.py`, start in: `C:\Rocky`
  - Conditions → uncheck "Start the task only if the computer is on AC power"
  - Settings → restart on failure every 5 min, up to 3 attempts
- [ ] Reboot the laptop. Confirm Rocky comes back up automatically (`wrapper.log` shows startup within ~1 minute of login screen appearing; `classifications.jsonl` resumes being appended on the next email).

### 3. Validate iteration 1 in production

This is the original Phase 0 validation, but running on the Rocky laptop instead of James's laptop. Code edits flow via git push.

- [ ] Let Rocky run for ~1 week. Process real inbox traffic.
- [ ] Periodically run `python review.py` (RDP into Rocky laptop or run on James's laptop pointing at a synced copy of `classifications.jsonl`) to grade her classifications.
- [ ] When you spot misclassifications, edit `instructions.md` on James's laptop, commit, push. Production picks it up.
- [ ] **Decision criteria before moving to iteration 2:**
  - Classifier reliably correct on ≥90% of routine emails
  - Recall (catches actual Remy requests) is the metric that matters most
  - Instructions.md has accumulated meaningful calibration content

---

## Phase 2 — Iteration 2: read attachment contents (deferred)

Tasks for the next iteration. Don't start these until Phase 1 is validated.

- [ ] Add attachment-content reading (still `Mail.Read` permission — covers attachments)
- [ ] Update classifier to consider attachment text (lease, ledger contents)
- [ ] Re-run validation against same decision criteria

---

## Phase 3+ — see BUILD_REFERENCE.md

Drafting, Remy invocation, case management (Phase D detailed design), morning digest, etc. Follow the phase progression in BUILD_REFERENCE.md, all running on the Rocky laptop, all updated via git push.

---

## Operational reminders

- **Never push secrets.** Always `git status` before `git commit`. If you see `config.json`, `state/`, or any `.log`/`.jsonl` in the list, STOP and fix `.gitignore` first.
- **If config.json was accidentally pushed:** rotate the Anthropic API key immediately; force-push history rewrite is a last resort and not safe in shared repos.
- **Cloud-only OneDrive files** can't be read by Rocky if she's running on a machine where the Rocky Cases folder isn't pinned "Always keep on this device." Make sure the production laptop has it pinned.
- **For occasional manual access** to the Rocky laptop (logs, debugging): Tailscale + RDP. Set this up alongside Phase 1.
