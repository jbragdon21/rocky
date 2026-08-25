# Litigation Updater — operational guide

Rocky tracks litigation for Bozzuto Management Company and affiliates
(BMC, B&A, BHI, BCC, BDC) for audit disclosures on Smartsheet: **two
open claims sheets** — the BMC master ("BMC Litigation Report -
MASTER", for BMC/B&A) and the BCC/BDC master ("BCC_BDC Litigation
Report - MASTER", for the other Bozzuto entities) — sharing one
**closed claims** sheet. `litigation_open_sheets` in config routes each
entity to its home sheet; correlation searches both. Rocky watches the
mail,
classifies incoming legal documents, and proposes every sheet change —
one at a time — over a Teams group chat titled **"Litigation Updates"**.
Nothing is written to the Smartsheet without James replying YES.

Module: `litigation_updater.py`, dispatched as `rocky.py --litigation ...`.

---

## Commands

| Command | When | What it does |
|---|---|---|
| `--litigation --poll [--dry-run] [--backfill-days N]` | scheduled (10:00 suggested; more often is fine) | Inbox intake pass + Teams chat cycle |
| `--litigation --chat` | as needed | Teams chat cycle only (replies, proposals, doc/report requests) |
| `--litigation --chat --follow [--minutes N]` | cleanup sessions | Live session: polls the chat every 15s so each YES/NO gets an immediate ack + the next proposal; exits after N minutes (default 30) or once the queue drains and the chat goes quiet |
| `--litigation --digest [--date YYYY-MM-DD]` | 18:30 daily | Drafts the day's claims activity into James's **Drafts** (quiet days draft nothing) |
| `--litigation --report <BMC\|B&A\|BHI\|BCC>` | on demand | Entity audit report through the Jinja template; saved to `Reports\` and emailed from rocky@ to James |
| `--litigation --cleanup [--limit N]` | once, initial learning phase | Reviews every open-sheet entry; queues fixes as one-at-a-time chat proposals; writes `missing_info.md` |
| `--litigation --learn [--days N]` | weekly | Distills the Teams chat into standing rules appended to `brain.md`; new rules appear in the next digest |
| `--litigation --voice-rebuild` | initially + occasionally | Rebuilds the four drafting voices from the sheets, past audit reports, and the `voices\seeds\` exemplar documents |
| `--litigation --strip-formatting [--apply]` | one-time | Clears all bolding/highlighting/column-default formats from all three sheets (values and formulas kept). Dry-run without `--apply` |
| `--litigation --status` | anytime | Config readiness, cursors, pending asks, vault counts |

Dashboard buttons: **Litigation Updater** (`--litigation --poll`),
**Litigation Digest** (`--litigation-digest` alias), **Litigation Learn**
(`--litigation-learn` alias). All litigation commands share one instance
lock (they share `state.json`).

---

## What Rocky watches for (intake, rocky@'s inbox)

1. **New legal notices** — mail from `legalnotices@bozzuto.com`
   (auto-forwarded from James's mailbox), or any forward whose text
   carries that address. The attachment goes to Claude to identify the
   document type (complaint, demand letter, suggestion of bankruptcy,
   garnishment, subpoena, ...) and Rocky proposes a new claims entry
   over Teams, applying the brain's standing rules ("garnishment
   notices never get entries") when recommending.
2. **"Add to the claims/litigation smartsheet"** forwards — same
   proposal path.
3. **"Move the *X* claim to Closed claims"** forwards — Rocky
   correlates the chain with an open-sheet row (Claude-assisted; below
   0.7 confidence she asks *which claim?* in chat), drafts a **closure
   note** in the closure voice, and proposes the move. On YES: the full
   row is snapshotted to the activity log, added to the closed sheet
   with the note in the `litigation_closure_column`, then deleted from
   the open sheet.
4. **"Update the claims smartsheet"** forwards — same correlation, but
   the drafted text updates the open row in place (proposal shows the
   exact new cell values; YES applies exactly those).

Unrelated rocky@ mail is ignored (the intent match requires the notice
sender or claims/smartsheet language). A Claude/API failure **holds the
mail cursor** so nothing is lost — the same mail is retried next run.

**After a YES executes** (entry added / row updated / claim closed), the
source mail is moved out of rocky@'s inbox into the **"Litigation
Updater"** subfolder (`litigation_processed_folder`; created under the
Inbox if missing; set to `""` to disable). Declined asks leave the mail
in place. The move is best-effort — a failure is logged and never
un-does the sheet write — and runs *after* execution because a Graph
move changes the message id the executor re-fetches attachments by.
Auth: `litigation_mail_move_via` — `"delegated"` (default; rocky@'s
cached sign-in with Mail.ReadWrite.Shared, already consented 2026-07-05
for the Inbox Cleaner — no new permissions) or `"app"` (requires
Mail.ReadWrite Application).

## The Teams chat

A persistent **group chat** ("Litigation Updates") between rocky@ and
James (group, because 1:1 chats can't carry a topic; the id is persisted
because Graph creates a new group chat on every create call).
`litigation_observers` may be added to watch; **only James's replies
decide anything**.

- One live ask at a time, tagged `[L####]`. A clean YES executes exactly
  the stored effects; NO declines. New-entry asks draft the full row
  *after* the YES (per design) and the ack shows every cell written.
- **Revisions**: a direction ("yes but add the settlement amount",
  "shorten the note", "mention that owner's counsel is handling")
  revises the pending proposal's drafted content and Rocky re-proposes
  the revised version — nothing executes until a clean YES on what's
  shown. A yes *with conditions* is treated as a revision, never an
  approval. For new-entry asks (drafted after the YES) the instruction
  is stored and honored at drafting time.
- **"Do you have the settlement agreement in the Johnson case?"** —
  Rocky searches the Litigation Update Vault catalog and emails the
  document from rocky@ to James (guarded outbound; internal only).
- **"Run a litigation update for BMC"** — generates the entity report.
- Anything else is answered briefly and logged as feedback; the weekly
  `--learn` pass turns durable feedback into brain rules.

## Voices and the brain

- `_litigation\voices\voice_updates.md` — learned from the sheets' own
  update-column entries (`litigation_update_columns`).
- `voice_summary.md` — learned from the internal claim-summary column
  (`litigation_summary_column`, default "Summary of Claim"); used when
  drafting the summary narrative of a new entry.
- `voice_closure.md` — learned from the closed sheet's closure notes.
- `voice_disclosure.md` — learned from the third-party-disclosure column
  **plus past audit reports** in `litigation_audit_reports_dir`.
- **Seeds** — drop gold-standard exemplar documents (.docx/.pdf/.txt/
  .md/.html) into `_litigation\voices\seeds\<updates|summary|closure|
  disclosure>\`; every `--voice-rebuild` folds them into that voice,
  weighted above the sheet samples, so hand-picked references are never
  lost to a rebuild. Seeded 2026-08-05: the 7/1/21 BMC litigation report
  (summary voice) and the 6/19/26 BMC litigation disclosure (disclosure
  voice).
- `_litigation\brain.md` — plain-English standing rules, included in
  every classification and drafting call. Human-editable; the weekly
  learn pass appends with `[learned YYYY-MM-DD]` provenance and the next
  digest lists the new rules in plain English.

## Litigation Update Vault

`<litigation_root>\Litigation Update Vault\<Claim>\<Type> - <Claim> -
<Date>.<ext>` — every document processed on an approved ask is filed
under its claim, SHA-256-deduped, and cataloged in
`_litigation\catalog.jsonl`. Chat requests are answered from the
catalog.

## Audit reports (strict format via Jinja)

`_litigation\report_template.j2` is created as a placeholder on first
run — **replace it with the required audit-report format**. The renderer
provides: `entity_key`, `entity_name`, `generated`, `columns`,
`closed_columns`, `open_rows`, `closed_rows` (row dicts keyed by column
title, values exactly as they appear on the Smartsheet). Point
`litigation_report_template` at a different file to keep the template
elsewhere. Output goes to `Reports\` and is emailed from rocky@ to James.

---

## Setup

1. **Smartsheet token** — Smartsheet → Account → Apps & Integrations →
   API Access → Generate Access Token, from an account with access to
   both sheets → `smartsheet_token` in config.json.
2. **Sheet IDs** — on each sheet: File → Properties → Sheet ID. The two
   open sheets go in `litigation_open_sheets` (each entry: `key`,
   `sheet_id`, `entities` it hosts, its `claim_column`, and any
   per-sheet `column_map` additions — the BCC/BDC master uses `Project`
   where the BMC master uses `Property Name (State)`); the closed sheet
   is `litigation_closed_sheet_id`. New claims land on the sheet whose
   `entities` list contains the classified entity (first sheet is the
   fallback); closures from either sheet go to the one closed sheet.
3. **Column names** — Rocky matches columns by TITLE. The
   config.example values were taken from the real master sheet
   ("BMC Litigation Report - MASTER", verified 2026-08-02):
   `litigation_entity_column: Entity`,
   `litigation_claim_column: Combined Claimant (Project)` (the
   "Claimant (Property)" identifier used for correlation, vault folders,
   and chat), `litigation_update_columns: Status/Action Items + Overall
   Status`, `litigation_disclosure_column: Third Party Disclosure
   Summary`, `litigation_correlate_columns: Entity / Property Name
   (State) / Type of Case / Case No.` (shown to Claude when matching a
   forwarded chain to a row).

   The CLOSED sheet ("Closed Claims - MASTER", verified 2026-08-02)
   mirrors the open sheet plus `Date Closed` and `Closure Notes`
   (`litigation_closure_column`; `litigation_date_closed_column` is
   auto-set to the move date). Three titles drifted between the sheets,
   so `litigation_column_map` translates them when a claim moves:
   `Date of Loss/Filing → Date of Loss / Filing`,
   `Property Name (State) → Property`,
   `Combined Claimant (Project) → Combined Claimant/Project`. If either
   sheet's columns are ever renamed, update the map — unmapped titles
   that don't match by name are dropped from the moved row (the full
   snapshot always survives in the activity log).
4. **Entities** — `litigation_entities` maps report keys to full names
   (defaults cover BMC / B&A / BHI / BCC; adjust the names to the real
   affiliate names).
5. **Share folder** — `litigation_root` (standard:
   `OneDrive Program Files\Rocky\Litigation Updates`). Pin **"Always
   keep on this device"** on the Rocky laptop.
6. **Mail routing** — set up the auto-forward of
   `legalnotices@bozzuto.com` mail from James's mailbox to
   rocky@gallagherllp.com (Outlook rule / Exchange redirect — either
   works; Rocky recognizes both the original sender and forwarded
   copies).
7. **Past audit reports** — drop a few into a folder and point
   `litigation_audit_reports_dir` at it (teaches the disclosure voice).
8. **Report template** — replace the placeholder
   `_litigation\report_template.j2` with the strict format.

**No new IT/Graph permissions.** Mail reads use the app token
(Application Access Policy already covers rocky@ and jbragdon@); Teams
scopes were consented 2026-07-05; document/report email uses the guarded
internal-only outbound; the digest is a *draft* in James's mailbox
(Level 0 holds — Rocky never sends from James's account).

## Learning-phase runbook (first weeks)

1. Fill in config → `--litigation --status` until nothing says MISSING.
2. `--litigation --voice-rebuild` — build the voices from the existing
   sheets + past reports.
3. `--litigation --cleanup` — Rocky reviews every entry, writes
   `_litigation\conventions.md` and `missing_info.md`, and queues fixes.
4. Run `--litigation --poll` (or wait for the schedule) — the cleanup
   fixes arrive in the Teams chat one at a time; approve/decline each.
   Declines and corrections are learning material.
5. Weekly `--litigation --learn` — chat feedback becomes brain rules.
6. Schedule for production: poll 10:00 (add a second afternoon run via
   Task Scheduler if desired), digest 18:30, learn weekly (e.g. Friday
   17:00).

## Troubleshooting

- **"Teams not enabled"** — rocky@'s device-code consent lapsed; run any
  Teams-using command interactively once.
- **Smartsheet 4xx errors** — token lacks access to the sheet, or a
  sheet id is wrong. `--status` shows what's configured.
- **Ask executed but Smartsheet balked** — the ask is re-queued and Rocky
  says so in chat; YES again retries.
- **Closure note missing from the closed sheet** — the closed sheet has
  no `litigation_closure_column`; the note is preserved in the activity
  log (`claim_closed` / `claim_closed_snapshot` events). Add the column.
- **Wrong/stale chat** — delete `chat_id` from
  `C:\Rocky\litigation\state.json` and the next cycle creates a fresh
  group chat.
- Everything Rocky does lands in `_litigation\activity.jsonl` (and chat
  traffic in `communications.jsonl`) on the share.
