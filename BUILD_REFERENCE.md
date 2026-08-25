# Rocky — Build Plan Reference

A condensed reference for the Rocky virtual paralegal project. This document captures the essential decisions and architecture from the design conversation, excluding the exploratory tangents (naming history, etc.). Use this to brief future Claude conversations or other developers on the project.

---

## What Rocky is

Rocky is a virtual paralegal for James Bragdon, an attorney at Gallagher LLP (`gallagherllp.com`) practicing landlord-tenant, property management, and federal civil litigation in Virginia, DC, and Maryland.

She is implemented as a Python program that authenticates to Microsoft 365, watches an Outlook inbox, classifies emails, drafts replies into the user's Drafts folder (later iterations), runs document-generation skills (including a tool called Remy that generates landlord-tenant notices), and manages a shared case-file workspace.

The project went through naming iterations: Margaret → Minotaur → Rocky. "Rocky" is the final name, chosen because it's unpretentious, warm, and reads naturally in workflow sentences ("Rocky flagged this," "ask Rocky"). The name is non-human per firm directive. The visual identity is a sturdy four-legged earthen creature with green markings.

---

## Core architectural principles

These are the load-bearing decisions that shape everything else:

**Level 0 safety architecture.** Rocky is architecturally incapable of sending mail from James's account. This is enforced at the Microsoft 365 permission level (no `Mail.Send` granted on the delegated mailbox), not in code. Rocky's code does not contain a function for sending mail from James's account. Drafts only.

**Outbound allowlist.** When Rocky has her own account and sends mail from it, she can only send to `@gallagherllp.com` addresses. Enforced both in her code and at the Exchange Online tenant level via mail-flow rule (defense in depth).

**Permissions follow validated capability, not anticipated need.** Don't grant a permission until you have working code that needs it AND you've validated that code is correct. Anticipated future use is not a reason to grant permission now.

**Skills arsenal pattern.** Rocky discovers skills by enumerating a `skills/` folder. Adding a new skill is a matter of dropping a folder in, not modifying core code. Skills include: `remy` (landlord-tenant notice generator, an .exe), and SKILL.md prompt-based skills like `lease-review`, `response-letter-generator`, `resident-settlement-agreement`, `litigation-case-setup`.

**Workshop/office split.** Rocky's private workshop (her code, skills, memory, logs) and the firm's shared case workspace are separate top-level folders. Different access patterns, different audiences.

**Incremental teaching.** Rocky's behavior is shaped by editing `instructions.md` (plain English rules) and adding examples to `examples/`. She picks up changes on the next poll cycle (within 5 minutes), no restart needed. Calibration accumulates over months; the file is the long-term value.

**Bias toward false positives over false negatives.** When classifying emails, missing a real request (false negative) is worse than flagging an extra one (false positive). The classifier is prompted accordingly.

---

## Build phases

| Phase | Stage | Where it runs | Status |
|---|---|---|---|
| 0+A (merged) | Production foundation + classifier validation — Rocky laptop, git deployment pipeline, classifier iteration in production | Rocky laptop, 24/7 (dev edits from James's primary laptop via git push) | **Currently here**; code complete (iter 1 + iter 2 + Phase A safety + Phase D Stages 1/2/3); awaiting GitHub push, IT permissions, and Rocky-laptop install |
| B | Inbox triage & email assistance — morning digest, expanded drafting, email-based teaching | Office laptop, 24/7 | Pending Mail.ReadWrite + Mail.Send permissions |
| C | Skills arsenal — wrap existing skills, versioning, smoke tests | Office laptop, 24/7 | Future |
| D | Case workspace — RRID-indexed case folders, daily folder-update skill, inbox-to-case ingestion, daily case digest, co-counsel routing | Office laptop, 24/7 | **Stages 1/2/3 code complete (2026-05-02)**; co-counsel routing + email-delivered digest deferred (need case-index column + Mail.Send) |
| E | Polish — refined digest, schema versioning, multi-user audit, tracked-client agendas | Office laptop, 24/7 | Future |

Phases A–C give a production-ready Rocky in ~5–6 weekends. Full vision through E is 9–12 weekends. Case management (D) infrastructure is parallel to the case-management *skills* themselves, which develop on a separate track.

**Phase 0 / Phase A merge (decided 2026-05-02):** The original plan ran Phase 0 (validate classifier) on James's primary laptop before migrating to a dedicated production machine in Phase A. That sequencing has been collapsed: Rocky runs only on the dedicated Rocky laptop from day one, with code edits flowing via `git push` from James's primary laptop. The dev loop is identical to a local-laptop setup; only the runtime location changes. See `TASKS.md` for the ordered task list and `Git deployment pipeline` section below for the architecture.

---

## Current architecture: scheduled batch commands

Rocky runs as a set of scheduled one-shot commands (Task Scheduler), not a polling loop:

1. **`--daily-cases`** (4:00 PM) — for each case with an Outlook Folder path in the spreadsheet, fetches today's emails from that folder via Graph API, summarizes them via Claude, saves documents to the case folder's `Raw Documents/`.
2. **`--daily-run`** (4:30 PM) — reads each case's `_project/instructions.md` and executes Claude-driven file actions.
3. **`--daily-digest`** (5:00 PM) — generates a consolidated markdown digest of the day's case activity.

**Remy requests** are handled separately: James forwards emails to `rocky@gallagherllp.com`. Remy processing from Rocky's mailbox will be a separate scheduled command (future work).

**Case-to-folder mapping** is handled by Outlook Rules, not Rocky's code. James sets up an Outlook Rule for each case to sort incoming mail into a per-case folder. Rocky reads from those folders using the folder ID stored in the case index spreadsheet.

---

## File layout

**App files (local on Rocky laptop, `C:\Rocky\`):**

```
C:\Rocky\
├── rocky.py                  # Main program — scheduled batch commands
├── config.json               # Tenant/client IDs, user email, Anthropic key
├── examples/                 # Few-shot examples (starts empty)
├── classifications.jsonl     # Append-only log of every classification
├── state/
│   └── token_cache.json      # MSAL refresh token (auto-managed)
├── rocky.log                 # Operational log
└── requirements.txt
```

**Shared data (OneDrive, synced to Rocky laptop):**

```
OneDrive - gejlaw.com\Rocky Cases\
├── Rocky Case Index.xlsx     # Case-to-folder mapping, RRIDs. Sheet 1 = open cases;
│                             #   any sheet named like "Closed" = closed cases (skipped
│                             #   by digest/daily-run/daily-cases)
├── instructions.md           # Plain-English classifier rules (James edits)
├── Closed Cases/             # Retired case folders — invisible to Rocky's scans
├── [Case folders]/           # Per-case folders with Raw Documents, drafts, etc.
```

**Key constants in `rocky.py`:**
- `GRAPH_SCOPES = ["Mail.Read"]` — explicitly read-only, no Mail.ReadWrite, no Mail.Send
- `CLAUDE_MODEL = "claude-sonnet-4-5"`

**Authentication setup (Setup B):**

Rocky authenticates as `rocky@gallagherllp.com` (her own M365 account, set up by IT) and uses delegated permissions to read James's (`jbragdon@gallagherllp.com`) inbox. The `user_email` field in `config.json` is the *target mailbox* (James's), while the device code login uses *Rocky's identity*.

IT setup required:
1. Provision `rocky@gallagherllp.com` with a standard license
2. Grant `rocky@gallagherllp.com` delegated read access to James's mailbox at the Exchange level (Recipients → Mailboxes → james → Mailbox delegation → Read permissions → add rocky@gallagherllp.com)
3. Conditional Access policy exception: allow device code flow for `rocky@gallagherllp.com` (firm policy blocks it by default)
4. Azure AD app registration "Rocky" with delegated `Mail.Read` permission

**Classifier output schema:**

```json
{
  "is_remy_request": true,
  "confidence": 0.92,
  "reasoning": "Property manager forwarded with lease and ledger; describes unauthorized occupant violation.",
  "documents_referenced": ["smith-lease.pdf", "smith-ledger-march.xlsx"],
  "project_category": "breach_notice",
  "jurisdiction": "DC",
  "subtype": null
}
```

`project_category` is one of: `breach_notice`, `nonrenewal`, `warning_letter`, `settlement_agreement`, `response_letter`, or `null`. `jurisdiction` is `VA`/`DC`/`MD`/`null` (meaningful only for breach_notice and nonrenewal). `subtype` is populated only for settlement_agreement (`move-out` | `early-termination` | `concession` | `transfer` | `combination`). When `is_remy_request` is false, the three categorization fields are all null.

**What Remy is:** Remy (`C:\Users\jbragdon\Desktop\REMY`) is a Python/Tkinter desktop tool, packaged via PyInstaller to `Remy.exe`, that generates landlord-tenant documents for VA, DC, and MD. Its full output catalog includes 12 notice form types, 3 complaint/filing packets (DC Form 1-A, DC Form 1-B, VA UD), warning letters, response letters, settlement agreements, and a batch DC rent notice mode. **Rocky's iteration-1 classifier scope is narrower:** it only identifies the five "letters & agreements" categories listed above — breach notices, nonrenewals, warning letters, settlement agreements, and response letters. Complaints and batch notices are explicitly out of scope and classified as `is_remy_request: false`. Rocky does not invoke Remy in iteration 1; she only classifies and identifies the requested project type so accuracy can be reviewed before automation.

**Deferred:** for `breach_notice`, picking the specific Remy form (e.g., VA 21/30 vs. VA Nonremediable vs. VA Immediate) is a legal judgment call. The classifier deliberately stops at category + jurisdiction; the eventual Phase-0 iteration-3 design is for Rocky to chat with James to pick the form before invoking Remy.

---

## Permission progression (when to add what)

| Iteration | Capability | Graph permission | Added when |
|---|---|---|---|
| 1 (current) | Classify emails | `Mail.Read` only | At app registration |
| 2 | Read attachment contents | `Mail.Read` (same — covers attachments) | Same as iteration 1 |
| 3 | Create drafts in James's Drafts folder | `Mail.ReadWrite` (NOT `Mail.Send`) | When drafting code is written and tested |
| 4 | Move emails between folders / file emails | `Mail.ReadWrite` (covers it) + `MailboxSettings.Read` (to enumerate folders) | When folder-management code is written |
| 5 | Create new folders | No new permission needed | Same as iteration 4 |
| Phase A | Send mail from Rocky's own account | `Mail.Send` on Rocky's mailbox only | Phase A transition |
| Phase D | Write case files to OneDrive | `Files.ReadWrite` (scoped to Rocky Cases folder) | When document ingestion code is written and tested |
| Never | Send mail from James's account | NEVER granted on James's delegated mailbox | Never |

---

## Code design notes

**Authentication:** Uses Microsoft Authentication Library (MSAL) with device code flow. Token cache in `state/token_cache.json` (file-based serializable cache). Refresh token auto-renews on each successful API call; valid for ~90 days as long as Rocky runs at least once in that window.

**Folder-based email fetch (simplified 2026-05-11):** Rocky no longer polls the inbox in a loop. Instead, `--daily-cases` reads the case index for Outlook Folder IDs and fetches today's emails from each folder via Graph API. Outlook Rules (configured by James) sort incoming mail into per-case folders. Rocky reads from those folders using `/users/{email}/mailFolders/{folderId}/messages` with a `receivedDateTime` filter for today.

**Email summary call:** One Claude API call per case (not per email). All of a case's daily emails are batched into a single prompt. The response includes a summary, key documents, and action items — logged to `activity.jsonl`.

**Logging:** Per-case activity → `activity.jsonl` in each case folder. Operational events → `rocky.log`.

**Error handling:** Malformed JSON from Claude handled gracefully. API errors logged and skipped. Each case is processed independently — one failure doesn't block others.

---

## Deployment model

**The app lives on the Rocky laptop.** Rocky is installed and runs locally at `C:\Rocky\` on the dedicated Rocky laptop. The app code, config, state, and logs all live on that machine — not on OneDrive.

**OneDrive is a shared data filesystem, not a deployment mechanism.** Rocky reads from and writes to OneDrive for case-related I/O:
- Reading `Rocky Case Index.xlsx` (case-to-folder mapping)
- Reading `instructions.md` (classifier rules, plain-English)
- Saving classified documents and drafts into per-case folders
- Reading case folder contents for daily runs

**Code updates flow via .exe rebuild.** James edits source on his primary laptop, runs `python build_exe.py` to produce a single-file `rocky.exe` via PyInstaller, which is automatically copied to `OneDrive - gejlaw.com\Program Files\rocky.exe`. The Rocky laptop picks it up via OneDrive sync — no git pull or manual file copy required.

**Build and deploy workflow:**
1. Edit source code on dev laptop (this repo)
2. Run `python build_exe.py` — builds `dist/rocky.exe` and copies to OneDrive `Program Files\`
3. OneDrive syncs the .exe to the Rocky laptop automatically
4. Rocky laptop runs the new .exe on next scheduled invocation (or restart)

```
Rocky laptop (C:\Rocky\, local — runtime data only):
  C:\Rocky\config.json           ← API keys, tenant/client IDs, mailbox list
  C:\Rocky\state\                ← MSAL token cache, last_check, conversation cache, dormant flag
  C:\Rocky\rocky.log             ← operational log
  C:\Rocky\classifications.jsonl ← email classification audit trail

OneDrive (synced — program + shared case data):
  OneDrive - gejlaw.com\Program Files\rocky.exe  ← the deployed executable
  OneDrive - gejlaw.com\Rocky Cases\             ← per-case folders, case index, instructions
```

**First-time setup on the Rocky laptop:**
1. Ensure OneDrive syncs `Program Files\` and `Rocky Cases\` locally ("Always keep on this device")
2. Create `C:\Rocky\config.json` (copy from `config.example.json`, fill in real values)
3. Create a Task Scheduler entry that runs the OneDrive `.exe` at boot / on schedule
4. First run will prompt for device-code auth (one time)

**Wrapper script (`run_rocky.py`):** Optional crash-recovery wrapper. If used, it launches `rocky.exe` and restarts it after any crash with a 30-second delay. Can also be built as an .exe. Task Scheduler's built-in restart-on-failure works as a simpler alternative.

**Manual access to Rocky laptop:** Tailscale + RDP. Used for occasional debugging, log inspection, force-restart.

---

## Production architecture (Phase A target)

When iteration 1 is validated and James moves to Phase A, the production architecture adds:

**Hardware:**
- Dedicated office laptop or Mac mini, UPS-protected, never sleeps
- Ethernet, auto-launch on boot
- Tailscale for remote management
- Healthchecks.io for liveness monitoring
- OneDrive sync for backup

**M365 changes:**
- App registration permission split:
  - On Rocky's mailbox: `Mail.ReadWrite`, `Mail.Send`, `Calendars.Read`
  - On James's delegated mailbox: `Mail.ReadWrite`, `Calendars.Read`, NEVER `Mail.Send`
- Exchange Online mail-flow rule: reject Rocky's outbound to non-`@gallagherllp.com` addresses
- Confirm Purview audit logging covers both mailboxes

**Code additions:**
- `permissions.py` — **drafted 2026-05-02.** Decodes JWT scope claim at startup; halts (sys.exit(2)) if `Mail.Send`, `Mail.Send.Shared`, or `Mail.Send.All` is present. Wired into rocky.py main() immediately after first token acquisition. Forbidden list is a blocklist (not allowlist) so it doesn't need updating as Mail.ReadWrite is added.
- `outbound.py` — **drafted 2026-05-02.** `send_mail_guarded(token, sender_mailbox, to, subject, body, cc=None)` refuses non-`@gallagherllp.com` recipients OR senders. No callers yet (Mail.Send not granted). When future drafting/sending code is added, USE THIS FUNCTION — do not add a parallel non-guarded send path.
- `kill_switch.py` — **drafted 2026-05-02.** Scans inbound subjects for "ROCKY STOP" / "ROCKY START" from authorized senders (config-driven; default `user_email`). Writes/clears `state/dormant.flag`. Main loop checks `is_dormant()` each poll; if dormant, skips all classification + ingestion but continues polling to wake on START. Manual flag deletion also wakes Rocky.
- Improved audit logging — partial. `classifications.jsonl` and per-case `activity.jsonl` are JSONL. Daily-file rotation deferred until log volume warrants it.

---

## Future skills and capabilities

**SKILL.md skills (already exist, will be wrapped into Rocky's arsenal in Phase C):**
- `lease-review` — residential lease review, lease termination notices for VA/DC/MD
- `response-letter-generator` — Gallagher letterhead response letters from incoming PDFs
- `resident-settlement-agreement` — settlement agreements (move-out, early termination, concession, transfer)
- `litigation-case-setup` — sets up case folder structure from a complaint PDF (DC Superior Court focused)

**Case management skills (Phase D, parallel development):**
- `case-create` — new case from a complaint
- `case-add-pleading` — add pleading to existing case
- `case-process-correspondence` — process incoming email into case
- `case-summary-update` — refresh case.json from current state
- `case-deadline-extraction` — pull deadlines from new filings
- `case-archive` — close out a case

**Minotaur (sibling program, created 2026-07-29):** A second program at `OneDrive - gejlaw.com\Program Files\Minotaur\` (data at `C:\Minotaur`) housing *interactive* email-management processes driven by a live Claude Code session James directs from his phone — the inverse of Rocky's scheduled batch model (the session is the intelligence; Minotaur's Python only fetches/formats/drafts). v1 is inbox review & summarize + draft replies via `createReply` (delegated rocky@, Level 0 holds — no send function exists there either). **The Email Brain moved ENTIRELY to Minotaur 2026-08-02** (migration verified: 29,699 pairs, all embedded, at `C:\Minotaur\email_brain\brain.db`; Rocky's `--email-brain`, `email_brain.py`, and the numpy dependency were removed the same day — see the Email Brain note below). The name knowingly recycles Rocky's old naming history — post-2026-07 "Minotaur" references mean the sibling program, not Rocky. Ground truth lives in `MINOTAUR.md` in that folder.

**Email Brain (built under Rocky 2026-06-22, phase 1 live 2026-07-13, MOVED to Minotaur 2026-08-02 — no longer in this codebase):** the sent-mail corpus + retrieval index (SQLite `brain.db`: FTS5 + Voyage `voyage-3-large` embeddings over inbound→reply pairs) now lives in Minotaur — commands `minotaur.py brain stats|query|ingest|migrate`, data at `C:\Minotaur\email_brain\`, roadmap in `MINOTAUR.md`. Migration verified 2026-08-02 (29,699 pairs, all embedded); phase 2 ("respond like James" drafting) shipped as Minotaur's CLAUDE.md drafting instruction. Facts that remain relevant to Rocky's history: **Voyage data-policy gate CLEARED 2026-07-12** (account opted out — zero-day retention, no training; evidence `docs/voyage_opt_out_2026-07-12.png`, details `DATA_SECURITY.md`). Still open, now Minotaur's: James moving Online Archive sends into rocky@ `Inbox\James Older Sent` (then re-adding that folder to `sent_brain_folders` in `C:\Minotaur\config.json`); *phase 3 — timesheet correlation* (cross-reference the corpus against billing time entries; brain.db already keeps dates/participants/conversationId; needs a matter/client tag + timesheet ingest, billing system TBD).

**Inbox Cleaner (standalone process — designed 2026-07-04, code complete 2026-07-05; supersedes the earlier "Paul Inbox Review" two-pass design):** Triages colleagues' unorganized inboxes at 200,000+ message scale, then becomes a permanent, per-user recurring process. First user: Matt (`--inbox-matt`; switched from Paul 2026-07-05 — Paul becomes a later config block); second user: James himself (`--inbox-james`, added 2026-07-12 — small-inbox maintenance mode, see "Sort with friends" below); designed multi-user from day one — each user is a config block plus IT adding their mailbox to the Application Access Policy (app-token read, same mechanism as `--email-brain`; no Exchange delegation needed). **Implementation:** `inbox_cleaner.py` (all five stages + daily Claude rules update) and `teams.py` (delegated Teams chat transport, shared with the future Remy-via-Teams feature), dispatched as `rocky.py --inbox-<user> --snapshot|--analyze|--chat|--rules-update|--execute|--status`. Operational guide, IT steps, and the onboarding questionnaire: `INBOX_CLEANER.md` + `_templates/inbox_questionnaire.md`.

**Lifecycle: deep clean bootstraps a permanent process.** The initial 200k-message cleanup is phase one of a per-user process named for its owner ("paul-inbox", "matt-inbox", ...). Every rule created during the cleanup — questionnaire answers, labeled case clusters, approved cohorts — is logged to that user's rules file and learned permanently. Thereafter, recurring maintenance runs apply the accumulated rules to new mail automatically; only novel patterns (a new big case, an unfamiliar high-volume sender) generate fresh cohort proposals needing approval. Same incremental-teaching principle as the classifier's `instructions.md`: the rules file is the long-term value.

*(A Claude managed-agent architecture was considered 2026-07-04 and declined: the recurring scheduled runs and permanent per-user rule accumulation fit Rocky's architecture, and the 200k-scale bulk mechanics need script-driven Graph paging/moves regardless — an agent session is amnesiac between runs.)*

**Core architecture — a funnel.** Cheap deterministic passes dispose of the bulk; Claude classifies only the ambiguous residual (metadata-only: sender + subject, batched). One-Claude-call-per-email is off the table at this scale. Humans approve *cohorts* (rules covering thousands of messages), never individual messages.

**Stage 1 — Metadata snapshot (read-only).** Paged Graph fetch over the target inbox pulling lightweight fields only (`id`, `conversationId`, `subject`, `from`, `receivedDateTime`, `isRead`, `hasAttachments`, `importance`) — no bodies, no attachments. Written to a local JSONL snapshot (~50–100 MB at 200k messages); checkpointed and resumable across throttling. **Requires only `Mail.Read` delegated to the target mailbox** — the entire snapshot/analyze/approve cycle happens before any write permission is granted (permissions follow validated capability).

**Stage 2 — Ledgers.** Two derived files, and these are what a human reviews:
- *Sender ledger* — one row per sender: message count, date range, read %, internal/external. 200k emails collapses to a few thousand senders; the top few hundred cover most of the volume.
- *Conversation ledger* — one row per thread family (`conversationId` primary, normalized-subject fallback stripping `[EXTERNAL]`/`RE:`/`FW:` — ported from James's `SortByConversation` VBA macro): message count, participants, first/last date.

**Stage 3 — Cohort proposals.** Categories, each with its own age floor:
1. **Big cases** (age floor: 6 months). Multi-year matters appear as the top conversation clusters; court notices are pure sender-pattern matches (CM/ECF NEF addresses from `uscourts.gov`, e-filing notification senders). Human labels each big cluster *once* ("that's Smith v. Whatever → folder X") and the label disposes of thousands of messages. **Flat case folders — no date subfolders.** All of a case's mail goes in one folder for later searching.
2. **Internal office traffic** (age floor: 6 months). Sender domain `gallagherllp.com` or legacy `gejlaw.com`, older than 6 months, **and not part of a labeled case cluster** (internal mail includes co-counsel traffic on real matters) → "Office Misc. Archive" folder. Obvious office senders (office manager, billing, announcements) come straight from the sender ledger; ambiguous internal residue goes to the Claude batch pass.
3. **Newsletters/spam** (no age floor). Sender-ledger signals: high volume, no-reply-style addresses, low read rate; confirmed by fetching full headers on a *sample* per candidate sender and checking `List-Unsubscribe`. Proposed disposition: **move to Deleted Items** (Exchange retention auto-purges eventually; recoverable in the interim). Never hard-deleted — the nothing-is-ever-deleted rule holds.
4. **Claude batch pass** on the remaining residual — conservative, metadata-only, classifying `archive` / `needs-review` / `keep`. For a lawyer's inbox, under-archiving beats over-archiving.

**Approval workbook:** cohort rows, not message rows — a few hundred rows like "1,842 messages from 6 court-notice senders older than 6 months → Smith v. Jones", each with count, rule description, target folder, and Approve (Y/N), plus a drill-down sheet for spot-checking sample messages per cohort.

**Teams approval loop.** For execution approvals, Rocky chats with the inbox owner on Teams instead of requiring them to work the spreadsheet: *"5,012 messages in threads titled 'Angelos' → Angelos case folder. OK to move?"* — owner replies "yes" → cohort queued for execution; anything else → cohort marked declined / needs-review. Uses the Teams chat capability specced below (delegated `Chat.Create` / `Chat.ReadWrite` / `ChatMessage.Send` — the same IT-gated dependency as the human-in-the-loop Remy proposal, and the same pending-job state machine + reply-polling loop; whichever feature builds first creates the skeleton the other reuses). Design rules: propose cohorts **one at a time** (or as Adaptive Cards carrying a `cohort_id`) so a bare "yes" is unambiguous; **only the mailbox owner's replies count as approval** — the chat is actually a small *group* chat (Rocky + owner + `observers` from config, default James) so James can watch and comment without his messages deciding anything (owner identity resolved from the chat member list; unresolved owner = no reply decides anything, fail-safe); **a chat reply can only approve or decline a cohort Rocky proposed — it can never define a new move.** The workbook remains the audit artifact and the bulk-review option; Teams is the day-to-day approval channel.

**Stage 4 — Execution.** Requires `Mail.ReadWrite` delegated to the target mailbox (granted only once execute code exists and cohorts are approved). Executes approved cohorts (from Teams approvals or the workbook); performs moves in batches, throttling-aware and checkpointed — a 200k-message cleanup runs across multiple days, not one afternoon. Every move logged to a per-user `inbox_cleaner_activity.jsonl` with original folder ID for undo-by-replay.

**Onboarding questionnaire.** Before the analysis pass, Rocky interviews the new user (Teams chat, or a form James walks them through): what are your big active/legacy cases and what should their folders be called; what internal traffic do you want archived; how aggressive on newsletters; anything that must never be touched. Answers seed the initial rules file and pre-label the biggest conversation clusters, so the first cohort-proposal round is already half-customized.

**Rule learning.** Every approved decision persists as a rule in the user's rules file — e.g. *"threads matching 'Angelos' → Angelos case folder"*, *"sender X → Deleted Items"*, *"office-manager announcements older than 6 months → Office Misc. Archive"*. Each rule records its provenance (questionnaire answer, or cohort approved via Teams on date Y). Declined cohorts are recorded too, as never-propose-again negative rules. Format follows the `instructions.md` pattern: plain-English and human-editable, so the user (or James) can adjust rules directly, with enough structure (match criteria, target folder, age floor) that maintenance runs execute them deterministically without a Claude call.

**Recurring maintenance mode.** Once the deep clean completes, the per-user process moves to a scheduled cadence (weekly, initially): apply the learned rules to mail that has accumulated since the last run — still move-only, still logged. Mail matching no rule is left alone until enough of a pattern accumulates to propose a new cohort via Teams, which on approval becomes the newest rule. The process gets more automatic and more personalized with every cycle.

**"Sort with friends" — James's small-inbox mode (built 2026-07-12).** James's own process (`--inbox-james`) skips the deep clean entirely: his inbox stays near zero, so the job is filing the trickle. A per-user opt-in pass (`conversation_sort: true`) ports his SortByConversation Outlook VBA macro to Graph — for each message still in the Inbox, find mailbox-wide where OTHER messages in the same conversation are already filed (strategy 1: `$filter` on `conversationId`, which survives `[EXTERNAL]`-style gateway subject rewrites; fallback: `$search` on the normalized subject, post-filtered to exact normalized equality; Sent/Deleted/Drafts/Junk/Outbox and the Inbox root never count as "filed"; most-siblings folder wins) and draft one cohort per target folder, proposed over the normal Teams loop. The pass reads the CURRENT inbox live (never the snapshot — hand-filed mail is never proposed) and is skipped above `conversation_sort_max` messages (default 200), so it can never run against a deep-clean-scale mailbox. A new `--cycle` subcommand runs snapshot → analyze → chat → execute-approved in one shot (dashboard "James Inbox" button, suggested 08:00 daily); `cycle_execute: true` in James's config makes approved cohorts move live at the end of the cycle. No IT steps were needed: jbragdon@ was already in the Application Access Policy, rocky@ already holds Full Access on his mailbox (`write_via: "delegated"`), and Teams scopes were consented 2026-07-05. Cohort model additions: kind `conversation_sort` (outranks `matter` at execute-time claim order — filing history is stronger evidence than a keyword match), `match.message_ids` resolved live at analyze time, and `target_folder_id` (destination is an existing folder anywhere in the mailbox, used directly instead of `_ensure_folder`'s under-Inbox path creation); analyze prunes stale conversation-sort drafts only when the pass actually ran, so a skipped/failed pass can't wipe pending drafts.

**Open chat + Engineer — James's conversational layer (built 2026-07-12, same session).** Two per-user additions on top of the small-inbox mode, both opt-in via config so Matt's strict protocol is untouched:

- *Open chat mode* (`chat_mode: "open"`). Every message James sends in the Teams chat goes through one guarded Claude call that returns structured effects: a decision on the pending proposal (free text still can't command an unproposed move); plain-English standing rules appended to `rules.md` immediately with `[chat YYYY-MM-DD]` provenance; additive `sender_routes.json` ops — `exclude_sender` / `exclude_domain` ("skip emails from x because y" → a durable exclusion honored by every proposal pass AND at execute time, via `load_chat_exclusions`/`_drop_excluded`) and `add_route` (surfaces as a normal approval cohort next analyze); and `code_changes` — feature requests the current code can't satisfy, appended to the user's `code_changes.md` as a developer backlog (title + detail + chat quote). Effects are logged to `activity.jsonl`; `sender_routes.json` is backed up to `rules_history\` before every edit; on Claude failure the strict YES/NO parser still resolves the pending proposal and the messages remain in `communications.jsonl` for the nightly rules update (whose substantive-event list now includes `rule_from_chat`, `routes_updated`, `code_change_logged`, `action_proposed`/`action_confirmed`/`action_declined`, `engineer_done`).
- *Action proposals — the propose→confirm loop (added 2026-07-13).* Direct application is reserved for instructions with exactly one reasonable operationalization; anything open-ended ("stop bugging me about court stuff") is instead translated into a stored ACTION PROPOSAL: `{id: A####, description, standing_rules, route_ops, code_changes}` in the user's state file, one at a time. The Teams message renders the exact effects deterministically (`_render_action` — James approves what the code says it will do, not a paraphrase) and asks YES/NO. On approval the STORED effects apply exactly as proposed (`_apply_action_effects`, `[A#### approved date]` provenance in rules.md) — not re-generated, so approval works deterministically even if the Claude API is down (the fallback resolves a bare yes/no against whichever pending ask — cohort or action — was proposed most recently). Revisions replace the pending proposal under a new id; declines drop it without applying. While an action proposal is pending, `chat_cycle` holds new cohort proposals so a bare "yes" stays unambiguous.
- *Engineer* (`--engineer [--query "..."]`, or a Teams message starting with the word "engineer" — detected deterministically before the Claude call). Full workup of one inbox email (newest, or best subject/sender match): downloads the full body (text preference), attachments (`fetch_attachments`), and the whole conversation history mailbox-wide (conversationId, with folder locations and the last 5 prior bodies); one deep Claude call produces a fixed-structure report (Summary / Timeline / Analysis / Recommended response); deliverables are the report + raw attachments saved under the share folder's `engineer\` directory, the report emailed from rocky@, a DRAFT reply in James's Drafts via `createReply` with the recommended response (Level 0 holds — Rocky drafts, never sends; this is the first shipped use of the iteration-3 "create drafts" capability, via rocky@'s Full Access), and a Teams ack carrying the summary. Failures at any stage degrade gracefully and are logged (`engineer_failed` / `engineer_draft_failed` / `engineer_email_failed`).

**Open items (post-build, 2026-07-05):** Claude batch pass over the ambiguous residual (designed, not wired — v1 leaves unmatched mail alone); reminders/timeouts for unanswered Teams proposals; mailbox-wide conversation-sort *backfill* (file OLDER filed-nowhere messages where a newer reply already lives — the inbox-side pass shipped 2026-07-12 for small inboxes, see "Sort with friends" above); dashboard registry entries for Matt's stage-by-stage `inbox-*` commands (`inbox-james --cycle` was added 2026-07-12); Adaptive Cards (v1 is plain-text one-at-a-time proposals, which keeps "yes" unambiguous); maintenance cadence per user (default weekly). Resolved during build: CLI is `--inbox-<user>` (processes sort together: inbox-matt, inbox-paul); rules file is plain-English markdown with `[C####] [approved/declined YYYY-MM-DD]` provenance tags, machine-executable via cohorts.json; questionnaire is 10 questions in `_templates/inbox_questionnaire.md`, sent **by email** from rocky@ as HTML with an answer box under each question (`Answer [iq-N]:` markers; Rocky watches her inbox for the reply and parses the boxes, Maple-digest style) — Teams carries only the one-at-a-time cohort proposals.

**The Vault (built 2026-07-29):** `vault.py` + `rocky.py --vault` (suggested 3:00 PM daily) — a shared OneDrive folder ("The Vault", beside Rocky Cases; config `vault_root`) holding the documents that support Remy drafting and other filings: leases, ledgers, affidavits of service, notices. Organized `<Property>\<Tenant, Name>\<Type> - <Tenant> - <Date>.<ext>` with a regenerated `Vault Index.xlsx` for the team and a `_Needs Review\` pen for anything below the 0.75 confidence floor. Three ingestion sources per run: (1) James's inbox — attachment-bearing mail since cursor, Claude-classified, only lease/ledger/affidavit taken from this opportunistic source; (2) rocky@'s inbox — any subject containing "vault" is a team submission channel: everything attached is ingested (never silently dropped) and Rocky replies from rocky@ with what was filed where (guarded, internal-only); (3) Dropbox — two shapes per configured account: the account's own folders (persisted delta cursors) and **shared-folder links** (added 2026-08-06 for the client RAD-stamped-notices folder): any authorized token can read a shared link, so ONE app under James's own Dropbox covers every client link, with per-file processed marks (`server_modified|size`) instead of cursors since shared-link listing has no delta support. One-time `--vault --dropbox-auth <name>` helper; unconfigured accounts skip cleanly. SHA-256 dedup across all sources; classification failures hold cursors so an API outage loses nothing. Mail reads use the existing app token (Application Access Policy) — no new Graph permissions; catalog + activity JSONL live on the share under `_vault\`, cursors local in `C:\Rocky\vault\`. Full guide: `VAULT.md`.

**LetterStream (built 2026-08-06; renamed from "Mailing Affidavits" 2026-08-23 — it also sends mailings without affidavits):** `mailing_affidavits.py` + `letterstream.py` + `rocky.py --letterstream` (suggested 8:00 AM daily) — the certified-mail affidavit loop. The firm mails notices via LetterStream; Rocky pulls newly mailed jobs from the LetterStream API, downloads each proof-of-mailing PDF (LetterStream cover page + the full mailed packet), Claude-extracts tenant/address/property and a documents-mailed clause in the house style, generates the Certified Mailing Affidavit .docx (conformed /s/ signature + preparation date, matching the Brathwaite exemplar), and emails affidavit + proof from rocky@ to the approver (Hailey Mondragon, config `affidavit_approver`) tagged `[AM-####]`. Her YES reply (or James's) is the recorded authorization for that exact document: both files are then vaulted through vault.py's normal machinery (`The Vault\<Property>\<Tenant>\Certified Mailing Affidavit - ... .pdf` + `Proof of Mailing - ... .pdf`, indexed in Vault Index.xlsx, catalog notes who approved). NO sets the pair aside in `Declined\` and emails James the note; unclear replies get a "reply YES or NO" nudge; unanswered items re-send every 3 days. Approvals are polled before the pull, so an API outage never blocks filing. **Gating dependency:** LetterStream activates API access per account (email support@letterstream.com, "Automation" mode) and the docs unlock in-account afterward — `letterstream.py` isolates every vendor-specific detail and `--letterstream --probe` + `letterstream_raw.jsonl` support a one-session calibration; until then `--letterstream --ingest <proof.pdf>` runs the identical pipeline on manually downloaded proofs (tested end-to-end against the Brathwaite proof). No new Graph permissions (app-token reads + guarded outbound; Level 0 holds). Full guide: `LETTERSTREAM.md`.

**Litigation Updater (built 2026-08-02):** `litigation_updater.py` + `rocky.py --litigation` — Bozzuto claims tracking (BMC/B&A/BHI/BCC/BDC, audit disclosures) on Smartsheet: TWO open claims sheets (the BMC master for BMC/B&A and the BCC/BDC master for the other entities; config `litigation_open_sheets` routes each entity to its home sheet, correlation searches both, per-sheet claim-column and column-map quirks are spec'd per sheet) sharing one closed sheet (raw REST, columns matched by title). `--poll` scans rocky@'s inbox for legal notices from `legalnotices@bozzuto.com` (or forwards of them) and for forwards saying add-to / update / move-to-closed the claims smartsheet; attachments are Claude-classified (complaint, demand letter, suggestion of bankruptcy, ...), closures/updates are correlated to a row (below 0.7 confidence Rocky asks *which claim?*), and every sheet change is proposed one-at-a-time (`[L####]`) over a persistent **"Litigation Updates" Teams group chat** — YES executes exactly the stored effects, nothing is written without it. Closures log a full row snapshot to `activity.jsonl` before add-to-closed + delete-from-open (reconstructible by replay); the closure note is drafted in a learned voice. Three **voices** (`--voice-rebuild`): updates (from the sheets' own entries), closure notes (from the closed sheet), third-party disclosure (from the disclosure column + past audit reports dir). A **brain file** (plain-English standing rules, included in every call) grows via the weekly `--learn` pass over the chat log; new rules are surfaced in the next digest. Also: daily `--digest` drafted into James's Drafts on active days (Level 0 holds); on-demand `--report <entity>` rendered STRICTLY through a Jinja template on the share (placeholder auto-created; James supplies the real format), saved to `Reports\` and emailed internally; a one-time `--cleanup` compliance review of existing entries (conventions memo, one-by-one chat fixes, `missing_info.md`); and the **Litigation Update Vault** (`<root>\Litigation Update Vault\<Claim>\`, SHA-256-deduped catalog) answering chat requests like "do you have the settlement agreement in the Johnson case" by emailing the document to James. No new Graph permissions (app-token reads + already-consented Teams scopes + guarded outbound). Claude/API failures hold the mail cursor. Config: `smartsheet_token`, sheet ids, column titles, `litigation_root` (standard: OneDrive `Program Files\Rocky\Litigation Updates`, pinned). Full guide: `LITIGATION_UPDATER.md`.

**Teams chat capability:** Rocky communicates with firm users via Microsoft Teams through the `rocky@gallagherllp.com` Teams account. Uses delegated permissions (Rocky's existing device code flow), not application permissions — Microsoft doesn't support sending Teams chat messages via client credentials.

**Required delegated permissions:** `Chat.Create`, `Chat.ReadWrite`, `ChatMessage.Send`. **Verified already consented 2026-07-05** (rocky@'s refresh-token grant redeems for them — probed from the Rocky laptop; the grant also carries delegated `Mail.ReadWrite`/`.Shared` and `Calendars.ReadWrite`). Kept in a separate `TEAMS_SCOPES` list in `teams.py` rather than `GRAPH_SCOPES`, so mail commands never prompt for chat consent. The transport itself is built (`teams.py`, 2026-07-05, first consumer: Inbox Cleaner) — the Remy-via-Teams "external dependency" gate below is therefore already cleared.

**Graph API pattern:** Create a 1:1 chat with `POST /chats` (members: Rocky + target user), then send messages with `POST /chats/{chat-id}/messages`. Rocky can receive replies by polling `GET /chats/{chat-id}/messages`.

**Tracked-client capability (Phase E):** Specific clients can be flagged for high-touch tracking. For each tracked client, Rocky maintains a running communications log, a pending-items file, and can generate on-demand call agendas. This handles 5-15 high-volume or strategically-important clients.

---

## Proposed: human-in-the-loop Remy via Teams (form selection + extraction review)

**Status: idea, not built. Documented here for a future design pass.**

**The problem this solves.** Rocky's headless Remy path (`remy_runner.py` → `remy_cli.py`) reuses the *exact same* workflow classes the Remy GUI uses, so for identical inputs the generated document is identical. But going headless drops two things a GUI user does by hand:

1. **Form selection.** In the GUI the attorney explicitly picks the notice form from a dropdown (VA has 5 forms, DC 3, MD 3) — a legal-judgment call. Headless, `remy_runner` either reads a `Form type:` line from the paralegal form-email or falls back to a coarse `_DEFAULT_FORM_TYPE` table (e.g. `breach_notice + VA` → always `VA 21/30 (Breach)`; `breach_notice + DC` → `DC Rent (Breach)` even for a non-rent breach). This silently re-introduces an automated form pick that the original design deliberately deferred (see the "Deferred" note under **What Remy is** — picking the specific form is a legal judgment the classifier was meant to stop short of).
2. **Extraction review.** The GUI flow is *extract → editable review panel → human corrects → generate*. Headless skips the review panel: whatever Claude extracted (party names, address, dates, balances) flows straight into the document with no human correction.

**The idea.** Rocky uses Microsoft Teams (delegated chat, already specced under **Teams chat capability**) to pull the attorney back into the loop at the two points the GUI does — messaging James or Christina (routed by the existing `attorney` field, `bragdon`/`araviakis`) to (a) choose the form before generation, and (b) review and correct the extracted fields before generation. This restores GUI-level fidelity to the headless path and finally implements the deferred "chat with James to pick the form" design intent.

**Why it's feasible with today's architecture:**
- A long-running host loop already exists (`--monitor-remy` → `remy_poll_cycle`), a natural place to also poll for Teams replies.
- The Remy engine already separates `extract_*` from `generate_*` on every workflow, and the CLI already has a `lease-review` subcommand that emits extracted `lease_data` as JSON — so "extract, pause for a human, then generate from corrected data" fits existing seams.
- Form lists are bounded (`JURISDICTION_FORMS` in `modules/lease_review.py`) — a clean fit for Adaptive Card buttons.
- Attorney routing already exists in the args.

**What would have to be built (none of this exists yet):**
1. **Teams transport** (`teams.py`): create 1:1 chat, send Adaptive Card, poll `GET /chats/{id}/messages`. Requires adding delegated scopes `Chat.Create` / `Chat.ReadWrite` / `ChatMessage.Send` to `GRAPH_SCOPES` plus an Azure app-registration change and IT consent. **This is an external dependency that gates the whole feature.**
2. **Two-phase Remy split**: an `--extract-only` CLI mode that emits the intermediate JSON, and a `--generate-from <json>` mode that skips re-extraction. The workflow methods support this; `remy_cli.py` currently fuses the two phases in one process. This is the one piece with *no* Azure dependency and is the foundation for everything else.
3. **Durable pending-job state machine**: a queue (e.g. `state/pending_remy/<job_id>.json`) plus resumption logic in the monitor loop. This is the genuinely new architecture — Rocky moves from fire-and-forget `subprocess.run()` to a job that pauses and resumes across poll cycles.
4. **Reply interpretation**: button click → form choice (carry the `job_id` in the card action); field corrections → a pre-filled card form the attorney edits (preferred over free-text chat, which is fragile to parse).

**Design decisions to make in the design pass:**
- **Latency / abandonment.** A notice now blocks on a human reply that may take hours or days. Needs timeouts, reminders, and a fallback (after N hours, deliver as a flagged draft-for-review, or escalate) so requests don't silently stall.
- **Correlation.** Multiple pending jobs in one chat means replies must carry a `job_id`. Adaptive Card actions do this cleanly; free-text replies are ambiguous. **Favor cards over chat text.**
- **Two interaction shapes.** (a) form selection = bounded buttons; (b) review/correction = a pre-filled structured form. Don't attempt (b) as free-text.

**Suggested phasing:**
- **Phase 1 — form selection only.** Adaptive Card with the jurisdiction's valid forms as buttons; Rocky waits, then generates. Smaller, bounded, no JSON-patch complexity, and closes the biggest risk (the auto-defaulted legal-judgment call).
- **Phase 2 — field review/correction.** Pre-filled card of the extracted values → attorney edits → generate. Restores the GUI's review panel.

Both phases share the same prerequisites (Teams scopes + the extract/generate split + the pending-job state machine), so Phase 1 builds the skeleton Phase 2 reuses.

---

## Case workspace structure (Phase D target)

**Root location:** `C:\Users\jbragdon\OneDrive\OneDrive - gejlaw.com\Rocky Cases`

Note: the OneDrive folder name on disk retains `gejlaw.com` from before the firm renamed to `gallagherllp.com`. The folder path is correct as-is — do not "fix" it. (Email/account domain is `gallagherllp.com`; OneDrive sync folder name is a legacy artifact.)

**Case identification:** Each case has a Rocky Reference ID (RRID) in the format `RRID-XXXX`. The master case index at the root lists all cases with their RRIDs.

```
Rocky Cases\
├── _index.json                   # Master case index (RRID → matter name, status, folder path)
├── _schema\                      # Versioned case-file schema
│   ├── case_v1.schema.json
├── RRID-0001-Smith\              # RRID-based folder naming
│   ├── case.json                 # System of record (parties, posture, status, deadlines)
│   ├── activity.jsonl            # Multi-user activity log (Rocky, James, Cowork users)
│   ├── master_file_index.json    # Index of all documents in this case folder
│   ├── case_status_memo.md       # Master case status memorandum (deadlines, upcoming dates)
│   ├── _project\                 # Claude project context
│   │   ├── instructions.md
│   │   ├── knowledge\
│   │   └── history.jsonl
│   ├── Pleadings\
│   ├── Correspondence\           # from-opposing, from-court, from-client subfolders
│   ├── Court Documents\
│   ├── Client Documents\
│   ├── Drafts\
│   ├── Generated\
│   ├── Research\
│   ├── Raw Documents\            # Unprocessed incoming — staging area for daily skill
│   └── Archive\
└── _archived\                    # Closed/superseded matters
```

Case workspace is browsable by Cowork users without needing Rocky-specific knowledge. `case.json` is the self-describing summary. `activity.jsonl` is the unified audit trail across all actors (Rocky, humans, Cowork sessions).

---

## Phase D: Case management — detailed operational design

**Three stages**, all CLI-triggered skills run once daily via Task Scheduler: Stage 1 (`--daily-cases`) at 4:00 PM, Stage 2 (`--daily-run`) at 4:30 PM, Stage 3 (`--daily-digest`) at 5:00 PM.

**All three stages: code complete as of 2026-05-02.** Filesystem-only — no `Files.ReadWrite` Graph permission needed. Rocky writes to the local OneDrive sync folder; OneDrive uploads to the cloud. (Caveat: requires `Rocky Cases` folder to be pinned "Always keep on this device" on the production machine.)

### Stage 1: Document ingestion — IMPLEMENTED (via `--daily-cases`)

**Folder-based approach (simplified 2026-05-11).** Outlook Rules sort incoming mail into per-case folders. James adds the Outlook Folder ID to the case index spreadsheet (`Outlook Folder ID` column). Rocky's `--daily-cases` command:

1. Reads the case index for all cases with an Outlook Folder ID
2. Fetches today's emails from each folder via Graph API (`/mailFolders/{folderId}/messages`)
3. Sends all emails for a case to Claude in one call → summary, key documents, action items
4. Saves email bodies (as `.txt` with header) and attachments into `<case>/Raw Documents/`
5. Logs a `daily_cases_email_summary` event to `activity.jsonl`
6. Runs per-case folder skills (`daily_run`) on cases that received new files

Filenames are prefixed `{receivedYYYYMMDDTHHMM}_{md5(messageId)[:8]}_` so re-runs are idempotent. Case folder lookup is by RRID-substring match in folder name (e.g., `Mackey, Karen (RRID-0001)`).

### Stage 2: Daily run — IMPLEMENTED (`python rocky.py --daily-run [RRID-XXXX]`)

Instruction-driven: each case folder's `_project/instructions.md` tells Claude what to do. Rocky's code is pure plumbing — no hardcoded classification logic. Adding new behaviors means editing the case's instructions, not modifying `rocky.py`.

For each case folder with `_project/instructions.md`:

1. Reads the case-specific instructions
2. Gathers context: new unprocessed files in `Raw Documents/` (with extracted text), available subfolders, recent activity (last 48h)
3. **One Claude call per case** with the instructions + context → JSON response: `{analysis, file_actions[], recommendations[]}`
4. Executes `file_actions`: **copies** raw → target subfolder (preserves raw as immutable record). Filename collision handling appends `(1)`, `(2)`, etc.
5. Logs a `daily_run` event (analysis + recommendations) to `activity.jsonl`
6. Logs each `document_filed` event to `activity.jsonl` and updates `master_file_index.json`
7. **Seen-but-unfiled guard:** a raw file analyzed but not successfully filed/discarded increments a miss counter (`unfiled_attempts` in `master_file_index.json`). After 3 missed runs the file is **parked**: indexed with `"disposition": "parked_unfiled"` (so it stops surfacing as "new" and stops burning a Claude call daily), a `document_parked_unfiled` event is logged, and a standing "Rocky Unfiled Documents" section is added to the case's `CLAUDE.md` telling the next Cowork/project session to ask James what to do with it. `source_raw` matching is whitespace-tolerant (`_normalize_name_for_match`) — exotic whitespace in filenames (e.g. Outlook's non-breaking-space `Re_ ` artifact) previously made a file unfileable forever.

Cases without `_project/instructions.md` are skipped. A default template is at `_templates/instructions.md` — copy it into a case's `_project/` folder to enable daily runs for that case.

### Stage 3: Daily case digest — IMPLEMENTED (`python rocky.py --daily-digest [RRID-XXXX] [--hours N]`)

Rolling N-hour window (default 24h). For each case with `activity.jsonl` events or `master_file_index.json` entries timestamped within the window:

1. Reads the window's activity events + filed documents
2. Globs for the most-recent `*Case Status*.docx`, extracts text (capped at 8000 chars)
3. One Claude call per case → markdown section with three subsections (What happened / Recommended next steps / Upcoming dates)

Consolidated output: `Rocky Cases/Daily Digests/YYYY-MM-DD.md`. Skips writing entirely when no case had activity in the window.

**Digest delivery (current):** file written to disk; James reads manually.
**Digest delivery (future, when Mail.Send is granted on Rocky's account):** swap `digest_path.write_text(...)` for an emailed message body. The rest of the pipeline is unchanged.

**Co-counsel routing (deferred):** Plan calls for separate digests filtered to shared cases per attorney. Blocked on (a) adding a co-counsel column to the case index, (b) Mail.Send.

### Relationship to Phase B morning digest

The Phase B morning digest (7:30 AM, email triage summary) and the Phase D case digest (5:00 PM, case file activity summary) are separate products. Phase B covers what arrived in the inbox; Phase D covers what happened in the case files. In Phase E, these may merge into a single consolidated daily report.

### Operational constraint: OneDrive Files On-Demand vs. agent access

**The problem:** With OneDrive Files On-Demand enabled (the default), files in the Rocky Cases folder appear in directory listings but are stored as cloud-only placeholders until accessed in File Explorer. Sandboxed agents (Cowork sessions, Rocky's Python process running on a different machine, scheduled scripts) cannot trigger OneDrive's on-demand download — when they try to read a placeholder `.docx` or `.pdf`, the OS returns "Invalid argument" and the read fails. Plain-text files (`.md`, `.txt`, `.ini`) generally read fine; binary office files do not. This was confirmed in production: a Cowork user attempting to update a shared folder hit "Invalid argument" on every `.docx` and `.pdf`, including the master `File Index.docx` and all 14 files in `Raw Documents/`.

**Implications for Phase D:**

- Rocky's daily folder-update skill (Stage 2) reads from `Raw Documents/` to classify and file documents. If Rocky runs on a different machine than the one syncing OneDrive, those files will be cloud-only placeholders and the skill will fail.
- Any Cowork user who opens a case folder will hit this on every binary file until they manually download.
- The plan to have co-counsel access shared cases via OneDrive assumes their local OneDrive is configured to keep these files local.

**Mitigation options (decide in Phase D design pass, not now):**

1. **Pin Rocky Cases folder "Always keep on this device"** on the production machine running Rocky and on every Cowork user's machine. Eliminates placeholders for that folder. Costs disk space proportional to total case load.
2. **Programmatic hydration before read** — call `attrib -P +U <file>` (or the Python `ctypes` equivalent invoking `SetFileAttributes`) to force-download a placeholder before reading. Brittle; fails silently in some edge cases.
3. **Don't use OneDrive for shared case files** — switch to SharePoint document library with proper Graph API access (`Sites.ReadWrite.All`), which avoids the on-demand issue entirely because Rocky reads via API, not the local filesystem. Heavier setup, cleaner long-term.

The current build plan tacitly assumes option 1. Cowork users updating shared folders need to be told to pin the case folder locally before working in it.

---

## Key user-experience patterns

**Morning digest (Phase B):** Sent at ~7:30 AM from Rocky to James, grouped by matter:
> Smith v. Jones (3 items): 2 drafts ready in your Drafts folder, 1 flagged for review.
> Doe eviction (1 item): notice to cure drafted, in matter folder.
> General/unmatched (4 items): scheduling drafts, FYI items.
> Rocky needs input (1 item): unfamiliar sender, unclear matter.

**Forward-to-Rocky pattern:** James forwards an email to `rocky@gallagherllp.com` with a one-line instruction ("Run a settlement agreement on this — early termination, $1,500 concession"). Rocky processes the forward, runs the appropriate skill, returns the document by email reply.

**Email-based teaching loop (Phase B):** James can update Rocky's instructions or add examples by emailing her. Subject patterns trigger the teaching handler instead of normal classification.

**Tracked-client agendas (Phase E):** James can request "generate a call agenda for [tracked client]"; Rocky pulls from the running communications log and pending-items file.

---

## Technology stack

- **Python 3.11 or 3.12** (not 3.13 — some MSAL libraries lag)
- **Microsoft Graph API** for M365 access
- **MSAL (Microsoft Authentication Library)** for auth
- **Anthropic Claude API** (model: claude-sonnet-4-5) for classification and drafting
- **OneDrive** for backup and shared file access (Phase A+)
- **Tailscale** for remote access (Phase A+)
- **Healthchecks.io** for liveness monitoring (Phase A+)

No databases, no servers, no message queues — Rocky uses files (JSONL logs, JSON state, markdown instructions) for everything. The simplicity is intentional and load-bearing.

---

## Things to know about James's practice (relevant for prompts and skills)

- Practice areas: landlord-tenant, property management, federal civil litigation
- Jurisdictions: Virginia, DC, Maryland
- Property management clients are major Remy users; property managers forward emails for notice generation
- Common notice types: notices to cure, notices to vacate, NCV (DC), non-rent breach (unauthorized occupants/pets, late rent)
- Has worked extensively on multi-state lease addenda compliance, federal opposition briefs, DC property matter timelines
- Existing tools: Remy.exe (notice generator), Outlook add-in for lease termination notices, ledger analysis Python tool, multiple master prompts for DC lease termination letters and Property Management Agreement abstracts

---

## Open decisions to revisit later

These were flagged in the design conversation but deferred:

- Whether to disclose Rocky's existence to clients in engagement letters
- Whether to add browser automation for systems without APIs (declined for v1 — credential storage risk)
- Time-entry system / DMS integration (deferred; system-by-system, API-first)
- Multi-attorney support beyond James (architecture supports it, deployment scoped to James only initially)
- Conflict resolution between Rocky and Cowork users in case workspace (v1: last-writer-wins with OneDrive version history fallback)
- Schema versioning strategy for case.json (versioned from day one, migrations on first-open of old cases)
- RRID numbering scheme — auto-increment vs. prefix-based (e.g., by year or matter type); whether RRID maps 1:1 to firm matter numbers or is Rocky's own parallel index
- Whether the daily folder-update skill should also run on-demand (e.g., when James forwards a batch of documents and wants immediate processing)
- Co-counsel digest routing — how to associate attorneys with specific cases (field in case.json vs. separate config)
- **Shared storage backend for case files: OneDrive (with mandatory "always keep on this device" pinning) vs. SharePoint document library (Graph API access, no Files On-Demand issue).** Current plan assumes OneDrive + pinning; SharePoint is the cleaner architecture if Cowork users routinely hit the placeholder problem.
- Specific 5-15 clients to flag for tracked-client capability
- Whether tracked-client log is per-client or per-matter (instinct: per-client, cross-references matters)

---

## Resources from the design conversation

The following deliverables were produced and may exist in James's working folders:

1. **`Margaret_IT_Briefing.docx`** — IT admin briefing document (still uses old name "Margaret"; technical content current)
2. **`Margaret_Proposal_Analysis.docx`** — 28 open questions in fillable boxes (uses old name)
3. **`Minotaur_Build_Plan_Revised.docx`** — workshop-first build plan (uses old name "Minotaur")
4. **Rocky logo files** — `.ico` (multi-resolution), `.png` (multiple sizes), with and without drop shadow
5. **`rocky.py`, `instructions.md`, `requirements.txt`, `config.example.json`** — iteration 1 working code
6. **`Rocky.docx`** — case management feature plan (source for Phase D detailed design in this document)
7. **`run_rocky.py`** (added 2026-05-02, simplified 2026-05-06) — optional crash-recovery wrapper for the production laptop. Restarts Rocky on crash. Launched at boot via Task Scheduler. No longer does git pull (OneDrive handles sync).
8. **`permissions.py`, `outbound.py`, `kill_switch.py`** (added 2026-05-02) — Phase A safety modules. See "Production architecture (Phase A target)" section.
9. **`.gitignore`** (added 2026-05-02) — keeps `config.json`, `state/`, `*.log`, `*.jsonl`, `Azure ID Info.txt`, OS junk, and `__pycache__/` out of version control. `!Icon/*.png` and `!Icon/*.ico` are explicit allowlist exceptions for the legitimate logo files.
10. **`TASKS.md`** (added 2026-05-02) — ordered Phase 1 task list (IT pre-reqs, GitHub setup, Rocky-laptop install, validation). The active to-do list as of late 2026-05-02.

When updating these, the project name in code/docs should be "Rocky"; older references to "Margaret" or "Minotaur" should be replaced.
