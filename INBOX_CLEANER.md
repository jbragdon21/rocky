# Inbox Cleaner — operations guide

Per-user inbox triage at 200,000+ message scale that becomes a permanent,
rule-learning maintenance process. Design ground truth lives in
`BUILD_REFERENCE.md` ("Inbox Cleaner"); this file is the *operational* guide:
what IT and the user must do to start, how the process runs day to day, and
where everything is written.

First user: Matt (`--inbox-matt`). Second user: James himself
(`--inbox-james`, added 2026-07-12) — a small-inbox maintenance process with
the "sort with friends" conversation-sort pass; see its own section below.
Adding a colleague later (Paul, or anyone else) = one new `inbox_users`
block in config + the same IT steps for their mailbox. Process names sort
together on purpose: `inbox-matt`, `inbox-james`, `inbox-paul`, ...

---

## Getting started — the first steps, in order

### Step 1 (IT): add the mailbox to the Application Access Policy — *read-only*

Rocky reads mailboxes with an app-level token (client credentials) restricted
by an Exchange **Application Access Policy** (currently: rocky@, eaiken@,
jbragdon@, smetzger@). IT adds Matt's mailbox to that policy's mail-enabled
security group. No new Graph permission — `Mail.Read` (application) is
already consented. This unlocks `--snapshot` / `--analyze` and nothing else:
Rocky can *read* Matt's mail metadata, not move or send anything.

### Step 2 (IT): Teams chat scopes on the Rocky app registration

For the approval chat, add **delegated** permissions to the "Rocky" Azure AD
app registration: `Chat.Create`, `Chat.ReadWrite`, `ChatMessage.Send`
(+ admin consent). These ride Rocky's existing device-code sign-in as
rocky@ — one new consent prompt on the next `--chat` run, then cached.
(Same gate as the planned human-in-the-loop Remy Teams feature — granting it
once serves both.)

### Step 3 (James): config + first pull

1. Put Matt's real mailbox address in `config.json` → `inbox_users.matt.mailbox`
   (the CLI refuses to run against the PASTE placeholder).
2. `rocky.exe --inbox-matt --snapshot` — folder tree + inbox metadata into a
   local JSONL. Hours-long on 200k messages; checkpointed, re-run to resume.
3. `rocky.exe --inbox-matt --analyze` — ledgers + draft cohorts + the review
   workbook (`Rocky Inboxes\inbox-matt\inbox-matt review.xlsx`). Sanity-check
   the cohorts before any chat goes out.

   Two optional, human-editable JSON files in the user's share folder drive
   the user-specific passes (seed them from the questionnaire; templates in
   `_templates\inbox_matters.example.json` / `inbox_sender_routes.example.json`):

   - **`matters.json`** — the user's deals/cases by client. The matter pass
     matches message subjects against each matter's keywords (whole-word,
     case-insensitive) and claims the entire conversation, so file-share and
     DocuSign notices about a deal ride along into `Client\Matter` folders.
     Closed matters archive wholesale (no age floor). Matters earlier in the
     file win keyword overlaps. This is the transactional-practice answer to
     the litigation "big thread family" heuristic.
   - **`sender_routes.json`** — standing sender→folder routes (e.g. SEIA →
     "SEIA Folder"), each its own approval cohort, plus `never_bulk_domains`:
     senders the newsletters pass must never sweep to Deleted Items.

   Everything Rocky says to the user (cohort descriptions, Teams proposals,
   digests) uses the user's own vocabulary: `"noun"` in matters.json (or
   `matter_noun` in the config block) — `"deal"` for Matt's transactional
   practice, `"case"` (the default) for litigation users.

   Deal contacts are protected twice: senders who write on known matters are
   excluded from the newsletters pass, and at execute time cohorts claim
   messages in priority order (matters → big cases → routes → court →
   newsletters → internal sweep), so an approved internal sweep can never
   take mail a matter cohort matches — even one still awaiting approval, and
   even a declined one ("leave those alone" means nobody else takes them).

   Editing either file (or the config) and re-running `--analyze` regenerates
   cleanly: undecided drafts the current rules no longer produce are pruned;
   proposed/decided cohorts are never touched.

   The internal-office sweep drafts as **subgroups**, not one blob (added
   2026-08-02 after Matt balked at approving 51k internal emails blind):
   ops mailboxes (billing/AP/admin/no-reply), firm-wide broadcasts (8+
   visible recipients), one batch per colleague (≥ `internal_person_min`,
   default 150, with sample subjects so misfiled deal/case mail can be
   spotted and re-routed), and a small-volumes remainder. Chat proposals
   follow a curated order — most obviously non-work first, per-person
   internal mail last. Run `rocky.exe --inbox-<user> --internal-report`
   (on the laptop, needs the snapshot) to email the owner + observers the
   full breakdown (counts, top senders, sample subjects) before the chat
   starts proposing them; a copy lands in the share as
   `internal_report.md`.

### Step 4 (Matt, ~10 minutes): the questionnaire — by email

James tells Matt to expect an email from rocky@, then runs
`rocky.exe --inbox-matt --questionnaire`. Rocky emails the ten questions
(template: `_templates\inbox_questionnaire.md`; the per-user copy in
`Rocky Inboxes\inbox-matt\questionnaire.md` can be customized first) as
HTML with an **answer box under each question**. Matt types his answers in
the boxes and hits Reply. Re-running `--questionnaire` (schedule it with
`--chat`, or run it manually) watches rocky@'s inbox for the reply, parses
the answers out of the boxes (`Answer [iq-N]:` markers — the same
reply-parsing pattern as the Maple digest), saves them to
`questionnaire_answers.md`, sends Matt a short confirmation, and goes
quiet. Answers are folded into his `rules.md` by the nightly
`--rules-update`.

Note: this step needs **no new permissions** (rocky@ already sends internal
mail), so the questionnaire can go out any time — even before IT finishes
steps 1–2.

### Step 5: the approval loop

Schedule `--inbox-matt --chat` every 15 minutes (dashboard/Task Scheduler).
Each cycle: log Matt's replies → resolve the pending proposal → propose the
next cohort, one at a time ("5,012 'Angelos' emails → which folder?" /
"1,842 court notices older than 6 months → Court Notices Archive, YES/NO?").
Also schedule `--inbox-matt --rules-update` once daily (evening) — folds the
day's decisions and chat into `rules.md`, then **tunes `matters.json` from
the chat**: if the user named a new deal, said a batch caught the wrong
mail, or said a deal closed, a second guarded Claude call proposes
structured edits (add matter / update keywords / set closed — never
delete), the code validates and applies them, backs up the old file to
`rules_history\`, logs a `matters_updated` event (reported in the digest),
and re-runs analyze so the next chat cycle proposes the updated batches.
Self-tuning is safe by construction: a changed rule only produces a fresh
DRAFT cohort — nothing moves until the user approves it on Teams. Quiet
day = no API calls.

And schedule `--inbox-matt --digest` once daily (e.g. right after the rules
update) — a plain-English summary of everything the process did in the last
24 hours (`--hours N` to widen the window), emailed from rocky@ to the
mailbox owner and the observers (James), styled like Rocky's other digests.
Built deterministically from the activity/communications/move logs, phrased
by one Claude call (falls back to a plain deterministic summary if the API
is unavailable). A quiet day sends nothing — no email, no API call.

### Step 6 (IT, only when cohorts are approved): write permission

Two options — pick per user via config `write_via` ("app" default,
"delegated"). This step is deliberately last: the whole snapshot → analyze →
approve cycle runs read-only.

**Option A — `"write_via": "delegated"` (chosen for Matt, 2026-07-09; the
lighter IT ask).** IT grants rocky@ Exchange **Full Access** on the target
mailbox — the same mechanism already in place for James's mailbox. One
Exchange action, no Azure/app-registration change, no admin consent (the
delegated `Mail.ReadWrite.Shared` scope was verified consented 2026-07-05):

    Add-MailboxPermission -Identity Mpirnot@gallagherllp.com `
        -User rocky@gallagherllp.com -AccessRights FullAccess -AutoMapping $false

(or Exchange admin center → Recipients → Mailboxes → the user → Delegation →
"Read and manage (Full Access)" → add rocky@. `-AutoMapping $false` just
keeps the mailbox from auto-appearing in rocky@'s Outlook.)

**Option B — `"write_via": "app"`.** Add `Mail.ReadWrite` (application) to
the app registration + admin consent (the Application Access Policy already
limits which mailboxes it can touch).

Then, either way:

- `rocky.exe --inbox-matt --execute` — **dry-run**, logs the full move plan.
- `rocky.exe --inbox-matt --execute --live --limit 500` — first real batch,
  capped; spot-check in Outlook; then run uncapped (it re-runs safely —
  moves are checkpointed, a 200k cleanup spans days).

---

## James's own process (`--inbox-james`) — small-inbox mode

James runs the same machinery on his own inbox (`jbragdon@gallagherllp.com`),
but tuned for the opposite problem: an inbox he keeps near zero, where the
work is *filing the trickle*, not draining a 200k backlog.

**No IT steps.** Everything is already in place: jbragdon@ is in the
Application Access Policy (read), rocky@ has Exchange Full Access on James's
mailbox (write via `write_via: "delegated"`), and the Teams chat scopes were
consented 2026-07-05.

**One command runs the whole loop:**

    rocky.exe --inbox-james --cycle

snapshot → analyze → chat → execute-approved, in one shot. Dashboard button
"James Inbox" (suggested schedule 08:00 daily; also fine on demand, any
time). Because the config sets `cycle_execute: true`, cohorts James has
approved on Teams are moved *live* at the end of the cycle — a YES on Teams
is acted on the next time the cycle runs (or immediately, if he clicks the
button after replying). Everything else about the safety model is unchanged:
only approved cohorts move, every move is logged to `moves.jsonl`, nothing
is ever deleted.

Chat replies are only *read* when a cycle runs, so conversational latency
follows the schedule. On a near-empty inbox a full cycle is a handful of
Graph calls (and a Claude call only when James actually wrote something),
so it's fine to schedule `--inbox-james --cycle` every 15–30 minutes for a
responsive chat, or keep it daily and click the dashboard button when a
conversation is going.

**The "sort with friends" pass** (`conversation_sort: true` in the config;
James's first standing rule, 2026-07-12). His SortByConversation Outlook
VBA macro, ported to Graph: for each message still in the Inbox, Rocky looks
mailbox-wide for OTHER messages in the same conversation, and if they're
already filed in a folder, proposes moving the inbox message there.

- Strategy 1: mailbox-wide `$filter` on `conversationId` (Graph's id is
  computed from thread headers, so it survives the `[EXTERNAL]`-style
  gateway subject rewrites that broke Outlook's native matching — this one
  filter covers the macro's strategies 1 and 2).
- Strategy 2 (fallback): mailbox-wide `$search` on the normalized subject
  (RE:/FW:/[EXTERNAL] stripped), post-filtered to exact normalized-subject
  equality — the macro's strategy 3.
- Sent Items / Deleted Items / Drafts / Junk / Outbox and the Inbox root
  never count as "filed" (the macro's IsExcludedFolder); the folder holding
  the most conversation siblings wins (GetBestFolder).

The pass reads the **current** inbox live — never the snapshot — so a
message James already filed by hand is never proposed. It drafts one cohort
per target folder ("2 inbox emails in conversations you've already filed in
'Litigation\\Smith v Jones' — move them there? YES/NO"), proposed over the
normal 1:1 Teams chat. It is skipped whenever the inbox holds more than
`conversation_sort_max` messages (default 200): per-message sibling lookups
are only sane on small inboxes, and this pass must never run against a
deep-clean-scale mailbox like Matt's.

**Open chat mode** (`chat_mode: "open"`; Matt stays on the strict YES/NO
protocol). James talks to Rocky in plain English and one guarded Claude
call per chat cycle turns the conversation into structured effects:

- *Decide the pending proposal* — "yes", "no thanks", "actually put those
  under Litigation\Court" all work; free text still can't command a move
  that wasn't proposed.
- *Open-ended requests become concrete proposals* (the propose→confirm
  loop). When James's request needs a judgment call to operationalize
  ("stop bugging me about bar association stuff"), Rocky doesn't apply
  anything: it comes back with a specific proposal — `[PROPOSAL A0001]`
  plus the exact effects, rendered by the code, not paraphrased — and
  waits for YES/NO. On YES the *stored* effects apply exactly as shown
  (rules.md lines get `[A0001 approved YYYY-MM-DD]` provenance); on NO it's
  dropped; "make it cover X too" gets a revised proposal that replaces the
  old one. One ask at a time: while a proposal is pending, no new cohort
  proposal goes out, and a bare "yes" always refers to the most recent
  ask. A YES even works when the Claude API is down — the stored effects
  apply deterministically. Crisp instructions ("skip newsletters@x.com")
  still apply immediately with no extra round-trip.
- *Standing rules* — "skip emails from x because y" is appended to
  `rules.md` immediately (`[chat YYYY-MM-DD]` provenance) AND becomes a
  machine-executable exclusion in `sender_routes.json`
  (`exclude_senders` / `exclude_domains`), honored by every proposal pass
  and at execute time. Named routes ("SEIA mail goes to the SEIA folder")
  are appended as standing routes, which surface as normal approval
  cohorts on the next analyze.
- *Code-change backlog* — anything James asks for that the current code
  can't do is appended to `Rocky Inboxes\inbox-james\code_changes.md`
  (title + developer-ready detail + the chat quote). That file is the
  dev to-do list; delete entries as they ship.
- Every applied effect is logged to `activity.jsonl`;
  `sender_routes.json` is backed up to `rules_history\` before each edit.
  If the Claude call fails, the strict parser still catches a YES/NO and
  the messages sit in `communications.jsonl` for the nightly rules update.

**Engineer** — full workup of one email. From Teams, start a message with
the word *engineer* ("engineer", "engineer the one from Behroozi"); from the
CLI, `rocky.exe --inbox-james --engineer [--query "behroozi"]`. Rocky pulls
the newest matching inbox email, downloads its attachments and the whole
conversation history, runs one deep Claude call, and delivers four things:

1. `report.md` (Summary / Timeline / Analysis / Recommended response) +
   the raw attachments → `Rocky Inboxes\inbox-james\engineer\<stamp>_<slug>\`
2. the full report emailed from rocky@ to James
3. a **draft reply** in James's Drafts folder (createReply with the
   recommended response — Level 0 holds: Rocky drafts, never sends)
4. a Teams ack with the summary

**Adding more rules over time** works exactly like every other user:
`matters.json` and `sender_routes.json` in `Rocky Inboxes\inbox-james\`
drive the matter and standing-route passes, chat decisions accumulate in
`rules.md`, and the generic passes (court notices, newsletters, internal)
are all active — they just rarely trip their volume thresholds on an inbox
this small.

---

## Safety properties

- **Nothing is ever deleted.** Newsletters go to Deleted Items (recoverable
  until retention clears them); everything else moves to named folders.
- **Every move is logged** to `moves.jsonl` with cohort + source folder —
  undo is a replay of that log in reverse.
- **Nothing moves without approval.** Deterministic passes only *draft*
  cohorts; Matt (or the workbook) approves each one. Unmatched mail is left
  alone.
- **A chat reply can only approve or decline what Rocky proposed** — never
  command a new action. The approval chat is a small group chat (Rocky +
  Matt + James as observer, per the `observers` config key) so James can
  watch and comment — but **only Matt's replies count as approvals**;
  Rocky resolves the mailbox owner's Teams identity from the chat member
  list and ignores everyone else for decisions. An unreadable reply parks
  the cohort as `needs_review` for James; nothing moves.
- **One proposal at a time**, so a bare "yes" is never ambiguous.
- **Permissions follow validated capability**: read-only until cohorts are
  approved and the execute plan has been dry-run.

## Where everything lives

| What | Where |
|---|---|
| Snapshot, folder tree, move log (machine data) | `C:\Rocky\inbox_cleaner\matt\` |
| Rules file, cohorts, review workbook, activity + communications logs (human-facing) | OneDrive `Program Files\Rocky\Rocky Inboxes\inbox-matt\` (config `inbox_cleaner_dir`; set each machine's own path to the SAME shared folder — dev: `C:\Users\jbragdon\OneDrive - gejlaw.com\Program Files\Rocky\Rocky Inboxes`, laptop: `C:\Users\rocky\OneDrive - gejlaw.com\James D. Bragdon's files - Program Files\Rocky\Rocky Inboxes` — note the laptop mounts James's share under "James D. Bragdon's files - Program Files", NOT plain "Program Files", which is rocky@'s own unshared OneDrive) |
| Cursors / chat state | `C:\Rocky\state\inbox_cleaner_matt.json` |

The share folder rides the same OneDrive tree as the deployed executables, which the laptop already pins "always keep on this device" — so the binary review workbook never hits the Files-On-Demand placeholder problem, and Matt's inbox data stays out of the shared Rocky Cases tree.

`rules.md` is the long-term value: every approved cohort becomes a rule with
provenance (`[C0007] [approved 2026-07-12] Move ... → "..."`), every declined
one a never-propose-again rule, and questionnaire answers become standing
preferences. It's plain English — James or Matt can edit it directly.

## After the deep clean: maintenance mode

Re-run the same loop on a weekly schedule: `--snapshot` (incremental — only
new mail since the cursor), `--analyze` (existing decisions are never
clobbered; only genuinely new patterns become draft cohorts), `--chat`
(proposes only those new patterns), `--execute --live`. Mature rules dispose
of matching mail with no Claude involvement at all; the process gets cheaper
and more personalized every cycle.

## Current limitations / open items

- Executed moves take messages *from the Inbox only* (that's where the
  problem is). The inbox-side "sort with friends" conversation-sort pass
  exists for small inboxes (James, 2026-07-12); the mailbox-wide backfill
  variant (file OLDER messages wherever a newer reply already lives) is
  still a future enhancement, as is running the pass at deep-clean scale
  (it is deliberately capped at `conversation_sort_max` messages).
- The Claude batch pass over the ambiguous residual (mail matching no
  deterministic cohort) is designed but not yet wired in — v1 leaves the
  residual untouched.
- Reminders/timeouts for unanswered Teams proposals: not yet built; the
  proposal simply stays pending until the next reply.
- Dashboard registry: `inbox-james --cycle` added ("James Inbox" button,
  2026-07-12); Matt's stage-by-stage commands are still CLI-only.
