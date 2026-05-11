# Rocky — Task List

Ordered to-do for getting Rocky into production. Top-down: do these in order. Each task notes its blocker (what has to be true before you can start it).

When you finish a task, replace `[ ]` with `[x]` and move on. When a task spawns sub-tasks or surfaces a decision, note it inline below the task.

---

## Architectural decision (2026-05-02)

**Rocky runs only on the dedicated Rocky laptop.** James's primary laptop is the dev workstation, never a runtime. Code edits flow: edit on primary laptop → `git push` → Rocky laptop pulls within 30 minutes and restarts. This collapses what was originally Phase 0 (validate on James's laptop) and the front-end of Phase A (migrate to production) into a single phase.

## Who does what (2026-05-03)

Tasks below are split by owner. **🧑‍⚖️ JAMES** = developer / decision-maker, owns code, GitHub, secrets, ongoing operation. **🛠️ JEFF** = colleague handling Rocky-laptop setup once. After initial setup, Jeff is only involved if something on the laptop physically breaks; everything else flows through `git push` automatically.

## Code status as of 2026-05-02

All code is in `C:\Users\jbragdon\Desktop\Rocky\` and tested offline. Pushed to GitHub 2026-05-03. Done:

- ✅ Iteration 1 classifier (Mail.Read, RRID matching, case-index lookup)
- ✅ Iteration 2 attachment text extraction (PDF / DOCX / XLSX / TXT)
- ✅ Phase D Stage 1 — case-folder ingestion (saves matched emails to OneDrive)
- ✅ Phase D Stage 2 — daily folder-update skill (`--folder-update`)
- ✅ Phase D Stage 3 — daily case digest skill (`--daily-digest`)
- ✅ Phase A safety — `permissions.py`, `outbound.py`, `kill_switch.py`
- ✅ `run_rocky.py` supervisor wrapper for the Rocky laptop
- ✅ `.gitignore` covering all secrets and runtime state

Pending: IT pre-reqs (Conditional Access exception), Rocky-laptop install, live validation.

---

## Phase 1 — Get Rocky onto the production laptop

### 1. Pre-requisites (IT-dependent)

- [x] **Provision `rocky@gallagherllp.com`** (M365 mailbox, standard license) — done 2026-05-02
- [x] **Confirm Rocky laptop is provisioned** (Windows 10/11, on the firm domain, hardware ready) — done 2026-05-02
- [x] **Azure AD app registration "Rocky"** — done 2026-05-03. Delegated Microsoft Graph permissions granted with admin consent: `Mail.ReadWrite` and `Calendars.ReadWrite` (covers full build: reading mail, saving drafts, calendar read/create). `Mail.Send` intentionally NOT granted — Rocky is drafts-only.
- [x] **Grant `rocky@gallagherllp.com` delegation on James's mailbox + calendar** — done 2026-05-03. Full Access on mailbox (covers Drafts) and Calendar delegate access granted via Exchange admin.
- [ ] **Conditional Access exception** for device code flow on `rocky@gallagherllp.com` — Jeff to add, see §3 below (firm policy blocks device code flow by default; without this exception, Rocky's first login will fail with AADSTS53003)

### 2. Set up the git deployment pipeline

This is the FIRST technical step before any Rocky code runs in production. Tasks below are split by owner — **🧑‍⚖️ JAMES** (developer laptop, GitHub, secrets) vs. **🛠️ JEFF** (production Rocky laptop setup).

---

#### 🧑‍⚖️ JAMES — push code to GitHub (on his own dev laptop)

- [x] ~~Create GitHub account `jbragdon21`~~ — done 2026-05-03
- [x] ~~Install Git for Windows + `git config --global user.name` / `user.email`~~ — done 2026-05-03
- [x] ~~Create private GitHub repo `jbragdon21/rocky`~~ — done 2026-05-03
- [x] ~~`git init` + `.gitignore` + first push of Rocky code~~ — done 2026-05-03
- [x] ~~**Push the latest Rocky changes**~~ — done 2026-05-03. Verified: `config.json`, `state/`, logs all gitignored.
- [x] ~~**Push Remy to GitHub**~~ (private repo `jbragdon21/remy`) — done 2026-05-03. API key removed from batch scripts (commit 8f0104a), `config.json` gitignored, no secrets in tracked files.
  - ~~**Rotate the leaked Anthropic API key**~~ — done 2026-05-03.

#### 🧑‍⚖️ JAMES → 🛠️ JEFF — credentials handoff

- [x] ~~**Azure AD `client_id` and `tenant_id`**~~ — sent securely 2026-05-03
- [x] ~~**Anthropic API key**~~ — sent securely 2026-05-03 (used by both Rocky and Remy `config.json` files)
- [x] ~~**`rocky@gallagherllp.com` password**~~ — Jeff has it securely 2026-05-03
- [ ] **Get Jeff's GitHub username** — Jeff to create a free GitHub account and send the username (see Jeff's task list, step 1)
- [ ] **Add Jeff as a collaborator** on both private repos (Settings → Collaborators on `jbragdon21/rocky` and `jbragdon21/remy`) once he sends his username

---

#### 🛠️ JEFF — pre-reqs (do these FIRST, before laptop setup)

- [ ] **Create a GitHub account** (free tier is fine) and send the username to James. James will add it as a collaborator on the two private repos so you can clone them.
- [ ] **Add Conditional Access exception for device code flow on `rocky@gallagherllp.com`.** Firm's default Conditional Access policy blocks device code flow tenant-wide. Rocky's Python program authenticates via device code flow (no human at the keyboard most of the time), so we need a narrowly-scoped exception for this one account only. In the Entra/Azure admin portal: add `rocky@gallagherllp.com` to the exclusion list of the policy that blocks `authenticationFlows / deviceCodeFlow`, OR create a new CA policy that explicitly grants device code flow to this single account. Without this, the first `python rocky.py` run will fail with `AADSTS53003` ("Access has been blocked by Conditional Access policies").

---

#### 🛠️ JEFF — Rocky laptop setup (everything below)

Note: log into the Rocky laptop with whatever credentials IT gave you. The `rocky@gallagherllp.com` identity is only used at the device-code-login step — it's an authentication event for the Python program, not a Windows login.

**Install prerequisites:**

- [ ] Install Git for Windows (defaults are fine)
- [ ] Install Python 3.12 (NOT 3.13 — MSAL libraries lag). Check "Add Python to PATH" during install.

**Clone Rocky:**

- [ ] In Git Bash: `cd /c/` then `git clone https://github.com/jbragdon21/rocky.git Rocky`. The folder `C:\Rocky` should now exist with all the code.
- [ ] `cd /c/Rocky` then `pip install -r requirements.txt`
- [ ] Copy `config.example.json` → `config.json`. Fill in the values James gives you:
  - `client_id` — Azure AD value James provides
  - `tenant_id` — Azure AD value James provides
  - `user_emails` — leave the default `["jbragdon@gallagherllp.com", "rocky@gallagherllp.com"]`
  - `anthropic_api_key` — value James provides
  - `remy_cli_path` — leave the default `C:\\Remy\\remy_cli.py`
  - `remy_outputs_path` — leave the default OneDrive path
  - `enable_remy_invocation` — leave `false`. James will flip this to `true` later, after testing.
- [x] ~~Save `run_rocky.py` to `C:\Rocky\`~~ — already in the repo, arrives via `git clone`.

**Clone Remy (separate repo, lives at `C:\Remy\`):**

- [ ] In Git Bash: `cd /c/` then `git clone https://github.com/jbragdon21/remy.git Remy`. The folder `C:\Remy` should now exist.
- [ ] `cd /c/Remy` then `pip install -r requirements.txt` (Remy has its own dependency set — anthropic, python-docx, pdfminer.six, jinja2, etc.).
- [ ] Copy Remy's `config.example.json` → `config.json` and paste in the same Anthropic API key James gave you. Remy uses its own copy of the key.
- [ ] Smoke-test: `python C:\Remy\remy_cli.py --help` should print the subcommand list (`rent-complaint`, `complaint`, `va-ud`, `warning-letter`, `settlement`, `response-letter`, `lease-review`, `notice-summary`). If that works, Remy is ready.
- [ ] Verify the Remy Outputs folder exists at `C:\Users\jbragdon\OneDrive\OneDrive - gejlaw.com\Remy Outputs\`. Create it if not. Right-click the folder in File Explorer → "Always keep on this device" so OneDrive doesn't make it cloud-only.

**First-time login + wrapper test:**

- [ ] **First-time device code login.** Run `python rocky.py` once manually. It will print a code and a URL. On any browser, go to that URL, enter the code, and sign in as `rocky@gallagherllp.com`. (Have James type the password if you don't have it.) Confirm the token is cached at `state/token_cache.json`. Watch the console for a poll cycle or two to confirm classifications are flowing into `classifications.jsonl`. Then Ctrl+C to stop.
- [ ] Test the wrapper manually: `python run_rocky.py`. Verify it logs "Wrapper starting up" → "Starting Rocky..." in `wrapper.log`. The wrapper pulls BOTH the Rocky AND Remy repos every 30 minutes — a change in either triggers a Rocky restart. James will push a trivial change from his laptop to confirm; within 30 minutes the Rocky laptop's `wrapper.log` should show `git pull on C:\Rocky pulled changes...` followed by "Code updated. Restarting Rocky." Ctrl+C to stop the wrapper.

**Auto-start at boot:**

- [ ] Set up Task Scheduler entry "Rocky Wrapper":
  - General → "Run whether user is logged on or not", "Run with highest privileges"
  - Triggers → At startup, delay 1 minute
  - Actions → Start a program: full path to `python.exe`, arguments: `C:\Rocky\run_rocky.py`, start in: `C:\Rocky`
  - Conditions → uncheck "Start the task only if the computer is on AC power"
  - Settings → restart on failure every 5 min, up to 3 attempts
- [ ] Reboot the laptop. Confirm Rocky comes back up automatically (`wrapper.log` shows startup within ~1 minute of login screen appearing; `classifications.jsonl` resumes being appended on the next email).
- [ ] Hand the laptop back to James / report success.

#### 🛠️ JEFF reference — when Rocky's permissions change later

- [ ] **After ANY change that broadens Rocky's Graph scopes or mailbox list, force a fresh login.** Delete `C:\Rocky\state\token_cache.json` and re-run `python rocky.py` once manually. Sign in again as `rocky@gallagherllp.com` so the new consent prompt reflects the broader scopes / new mailboxes. Cached tokens carry the OLD scope set and will 403 on anything new. (James will tell you when this is needed; the most recent case was the 2026-05-03 multi-mailbox migration.)

### 3. Ongoing classifier observation (🧑‍⚖️ JAMES — not a gate)

Decision 2026-05-03: skip the original ≥90%-accuracy validation gate. Drafts are reversible, so the cost of a misclassification is low (a deleted Word file). Faster to learn from real production mistakes than to grade in vitro. This is a James task — no Jeff involvement.

- [ ] Spot-check `classifications.jsonl` weekly to catch **false negatives** — Remy requests Rocky misclassified. Since 2026-05-11, Rocky only classifies emails sent to her own mailbox (`rocky_email` in config); James's inbox emails are case-matched only. False positives still surface as bad drafts you'll see anyway.
- [ ] When you spot misclassifications, edit `instructions.md` on your dev laptop, commit, push. The Rocky laptop picks up the change within 30 minutes — Jeff doesn't need to touch anything.
- [ ] No accuracy bar to clear before moving to Phase 2 — Phase 2 activation can begin whenever you're ready.

### 4. Case-management Claude call (🧑‍⚖️ JAMES — future work)

Currently case-matched emails are saved to the case folder + logged but skip Claude entirely (`skip_reason=case_matched`). The next iteration adds a dedicated Claude call for this bucket, focused on case management — not Remy classification.

- [ ] Design the call: distinct system prompt (case posture, what changed, suggested next steps, deadline extraction). Likely takes the matched case context + email body + most-recent Case Status Memorandum text as input.
- [ ] Decide what the output drives: just enrich the case digest? Append a per-case "pending items" list? Generate a draft response in James's Drafts folder (Phase B territory — gated on `Mail.ReadWrite`)?
- [ ] Implement and wire into the main loop's bucket-1 branch, replacing the current `claude_called=False, skip_reason='case_matched'` skip.

---

## Phase 2 — Remy invocation + draft generation

Decoupled from case management on purpose. The two pipelines never overlap: Remy invocation writes to its own flat output folder; case-management filing is independent.

Pre-requisites:
- ✅ Azure: `Mail.ReadWrite` and `Calendars.ReadWrite` granted (done 2026-05-03)
- ✅ `remy_cli.py` headless entry point built and tested standalone (done 2026-05-03)
- [x] ~~Remy code on GitHub (private repo, secrets stripped, leaked key rotated)~~ — done 2026-05-03
- [ ] Remy cloned to `C:\Remy\` on the Rocky laptop, deps installed (`pip install -r requirements.txt` in `C:\Remy`)
- ✅ `run_rocky.py` extended to also `git pull` the Remy repo every cycle (done 2026-05-03)

Implementation (done 2026-05-03, dormant):
- ✅ Built `remy_runner.py` — parses paralegal "Run Remy:" form-email body, stages attachments to a temp dir, invokes the right `remy_cli.py` subcommand, copies output to the configured Remy Outputs folder with a descriptive filename.
- ✅ Classifier prompt updated to recognize seven categories (added `dc_rent_complaint`, `dc_breach_complaint`) and to bias toward is_remy_request=true on `Run Remy:` subjects.
- ✅ Wired into rocky.py main loop, behind `config.enable_remy_invocation` (default `false`).
- ✅ Created `paralegal_remy_request_template.md` — the form email paralegals copy/paste.

To activate (🧑‍⚖️ JAMES — after Phase 1 deployment is stable):

These are James-driven tasks. Jeff already verified the Remy clone and folder during Phase 1 setup; flipping the activation flag and distributing the template is on James.

- [x] Verify `Remy Outputs` folder exists at the configured path on James's OneDrive — Jeff confirmed this during Phase 1 setup.
- [x] Confirm Remy is cloned to `C:\Remy\` on the Rocky laptop and `--help` works — Jeff confirmed this during Phase 1 setup.
- [ ] Send a test "Run Remy:" email to `rocky@gallagherllp.com` from `jbragdon@` (use `paralegal_remy_request_template.md` as a starting point). Watch `rocky.log` (RDP or have Jeff send you a copy) for `Remy skipped: missing_attachments: [...]` etc. — iterate on the form template wording until it works end-to-end.
- [ ] Flip `enable_remy_invocation` from `false` to `true`. Two ways to do this:
  - **Option A (preferred):** RDP into the Rocky laptop yourself, edit `C:\Rocky\config.json`, save. Rocky restarts at the next git-pull cycle (within 30 min) and picks up the change.
  - **Option B:** ask Jeff to do it on the laptop — quick edit-and-save in any text editor.
- [ ] Distribute `paralegal_remy_request_template.md` to paralegals (or convert to PDF first using the same approach as the git-push cheatsheet).

---

## Phase 3+ — see BUILD_REFERENCE.md

Calendar handling, morning digest, multi-skill expansion (lease review, response letter, settlement, etc.). Follow the phase progression in BUILD_REFERENCE.md, all running on the Rocky laptop, all updated via git push.

---

## Operational reminders

- **Never push secrets.** Always `git status` before `git commit`. If you see `config.json`, `state/`, or any `.log`/`.jsonl` in the list, STOP and fix `.gitignore` first.
- **If config.json was accidentally pushed:** rotate the Anthropic API key immediately; force-push history rewrite is a last resort and not safe in shared repos.
- **Cloud-only OneDrive files** can't be read by Rocky if she's running on a machine where the Rocky Cases folder isn't pinned "Always keep on this device." Make sure the production laptop has it pinned.
- **For occasional manual access** to the Rocky laptop (logs, debugging): Tailscale + RDP. Set this up alongside Phase 1.
