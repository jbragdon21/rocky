# Inbox Cleaner — operations guide

Per-user inbox triage at 200,000+ message scale that becomes a permanent,
rule-learning maintenance process. Design ground truth lives in
`BUILD_REFERENCE.md` ("Inbox Cleaner"); this file is the *operational* guide:
what IT and the user must do to start, how the process runs day to day, and
where everything is written.

First user: Matt (`--inbox-matt`). Adding a colleague later (Paul, or
anyone else) = one new `inbox_users` block in config + the same IT steps
for their mailbox. Process names sort together on purpose: `inbox-matt`,
`inbox-paul`, ...

(James's own process — `--inbox-james` small-inbox mode with the "sort
with friends" conversation-sort pass, open chat mode, and Engineer — was
REMOVED 2026-08-29 along with its dashboard button and config block; only
the multi-user deep-clean machinery documented here remains.)

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
  problem is).
- The Claude batch pass over the ambiguous residual (mail matching no
  deterministic cohort) is designed but not yet wired in — v1 leaves the
  residual untouched.
- Reminders/timeouts for unanswered Teams proposals: not yet built; the
  proposal simply stays pending until the next reply.
- Dashboard registry: Matt's stage-by-stage commands are still CLI-only.
