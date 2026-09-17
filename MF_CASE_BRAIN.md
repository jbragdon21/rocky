# MF Case Brain — design

**Status: Stage 0 BUILT and running against live data (2026-09-13).**
`mf_brain.py` + `mf_digest.py` + `rocky.py --mf-brain`. Everything from
"Phase 2" down is still design. Every number here is measured.

## Stage 0 as built — observe, predict, ask

```
rocky.exe --mf-brain --digest [--dry-run] [--max-questions N]   (7:00 AM daily)
rocky.exe --mf-brain --scan [--local] [--dry-run] | --status
```

Writes nothing outside its own ledger: no spreadsheet edits, no mail
moves, no folder changes. `--digest` runs the scan itself, so only one
of the two belongs on a schedule.

**First full run against live data:**

| | |
|---|---|
| Cases | **2,270** (a case is a resident at a unit) |
| Matter lines | **1,026** open, matching the sheet exactly, 0 wrongly closed |
| Observations | 2,750 |
| Mail read | 479 messages across 26 matter folders (26 Claude calls) |
| Predictions open | 820 |
| Folders needing review | **11** |

Matter types: 1,048 other · 870 eviction · 231 insured_litigation ·
36 incident · 29 habitability · 25 agency_complaint · 12 discrimination ·
8 demand · 7 bankruptcy · 4 lease_exit.

**Three bugs the live data caught, in order:**

1. **Surname-only identity.** `case_signature` keyed on the surname
   while `llt_watch` keys on the full name, so roommates and a parent
   with an adult child merged into one case — 986 lines out of 1,026
   matters, with lines "closing" on a first scan. Fixed by adding a
   first initial that *vetoes* a surname match when both sides have one
   (separates John from Jane, still tolerates "Jasmin" from "Jasmine").
2. **Single-pass line pairing.** Two resident groups can legitimately
   resolve to one case, and processing them independently made the
   second pair against the first's lines and close them. Fixed by
   collecting rows per case first and pairing once.
3. **A review queue full of coincidences.** Any surname hit went to the
   queue, so the first digest asked James whether "Adams, Deborah" was
   "Adams, Durrel". In a 2,000-resident population a bare surname match
   is noise. Raising the bar to property+unit or name+unit took the
   queue from 568 to 11 and left the questions worth asking.

With the queue clean, the questions the first digest actually produced
were real: *"Classic / Modern on M" vs "Modern on M"*, *"70 Cap Yards"
vs "70 Capitol Yards"*, and two folders under *Bridge District*
matching sheet rows at *Alula* and *Stratos* — which is exactly what
the Pending LLT notes already record about that leasing office.

### The daily email

Sections: what I saw · **scorecard** · what I think is happening (one
Claude call) · what I would have done · questions with fill-in boxes.
James types into a box and replies; the next run reads the reply out of
rocky@'s inbox and folds it into `learning.md`.

Question budget defaults to five a day, ranked by how many ambiguities
each answer would clear — an email with forty questions is an email
nobody answers. Unanswered questions come back after a week.

The answer parser drops quoted lines outright and stops at the first
blank line after content, so a quoted question between two boxes cannot
bleed into the previous answer (`inbox_cleaner`'s version un-quotes and
keeps, which absorbs it).

### Predictions — the part that grades itself

Three rules today, each with a horizon the world resolves on its own:
a ripe unfiled matter **gets filed within 21 days**; a scheduled court
date **holds**; a matter ripe more than 90 days and still unfiled
**leaves the sheet without a filing** within 60 days.

Outcomes are `right`, `wrong`, or **`moot`** — the matter resolved for a
reason the prediction was not about. Moot is excluded from the accuracy
rate rather than counted as a win, which keeps the number honest. That
rate is the thing that eventually earns write access.

---

## Original design

Reconnaissance against live data was done 2026-09-12; what follows was
written before Stage 0 existed and remains the plan for everything
after it.

A per-matter view of the multifamily landlord-tenant practice, assembled
from four sources that each know part of the truth:

| | Source | Knows | State |
|---|---|---|---|
| (a) | `PENDING LLT MATTERS.XLSX` | stage, ripe date, court date — **evictions only** | **readable now** via `llt_watch` |
| (b) | The Teams Notes OneNote notebook | who owes what this week, the C/M number | blocked on delegated `Notes.Read` |
| (c) | James's inbox (later Christina's) | what actually happened, first | to build |
| (d) | James's Outlook folder tree | **which matters exist at all**, and which folder is which | to build |

**(d) is a source of case existence, not just an index.** A non-eviction
resident dispute gets an email folder and never appears on the eviction
spreadsheet or the task list. Measured: of 1,564 matter folders in
James's mailbox, **391 are live** (mail in the last 90 days) and **201 of
those are absent from the LLT sheet**. Roughly half the active resident
work is invisible to the spreadsheet.

So the brain's case population is the **union of the sources**, not the
spreadsheet with folders attached to it. An earlier draft of this design
had that backwards and treated the 940 non-matching folders as a closed
archive; they are not.

---

## Why this needs to be "elastic"

**There is no join key.** `Client.Matter` on the spreadsheet is populated
on **6 of 1,060 rows (1%)**, so the C/M number that looks like a spine
from the task-list side does not exist on the spreadsheet side. Nothing
in any source is an ID.

What identity has to be built from instead, strongest first:

1. **Outlook folder ID**, once mapped. Deterministic.
2. **Property + unit + surname.** 910 distinct property+unit pairs across
   1,060 matters.
3. **Property (from the folder's ancestor path) + surname.**
4. **Surname + client.**
5. **C/M number** where present. A bridge between the task list and any
   matter that happens to carry one, never the spine.

So resolution is scored, not keyed; corrections are persisted rather than
re-derived; and a source can be absent without the structure changing.

The folder tree already encodes most of what is needed. Of 2,962 folders
in James's mailbox:

- **582 containers** (leading `_`), nesting client → jurisdiction →
  property: `__Bozzuto Management \ __DC \ __5333`
- **1,564 matter-shaped** (`Last, First`), holding **27,592 emails**
- **799 topic or commercial** (`77H - Smoothie King`)

624 of the matter folders surname-match a current LLT matter; 160 of
those also have an ancestor folder naming the property. Folder names
frequently carry the unit (`Bynum, John 502`, `Cherry, Delante (726)`),
which should resolve most of the remaining 464. 940 match no open matter,
which is the closed-matter archive and worth keeping: a matter that
closed and reopened is a thing that happens.

The parenthetical in a folder name is **not** reliably the unit.
`Negron, Damon (5333)` is the property; the unit is 704.
`Pachter, Wendy (5333 - RA request)` is property plus issue.
`Robinson, Johniece (HAP issues 518)` is issue plus unit. Parse it as
"any of unit, property, or issue" and score accordingly.

---

## What the brain owns, and what it must not take

The folder tree spans four different systems of record. Of the 201 live
off-sheet matters:

| | count | system of record | owner |
|---|---|---|---|
| Evictions | (the 190 live on-sheet matters) | `PENDING LLT MATTERS.XLSX` | **this brain** |
| Insured / monitored claims | **37** | Bozzuto Smartsheet | `litigation_updater` — leave alone |
| Federal civil | (subset of the above) | RECAP / PACER | `pacer_monitor` — leave alone |
| **Everything else** | **164** | **none** | **this brain** |

The 37 under `__Bozzuto Insured/Monitored Litigation` already have a home
(the BMC / BCC master sheets, proposed over the Litigation Updates Teams
chat), and several are federal cases `pacer_monitor` watches —
`Hettinger, Laura (utility charges)` carries 1,691 messages on
`1:23-cv-…`. The brain should recognize those folders and record that
they belong elsewhere rather than tracking them twice.

**The 164 are the real finding.** They have no system of record anywhere;
today the only evidence they exist is that a folder exists. From the
sample they sort into:

- Agency complaints — DC OHR (`Pittman, Amira`, Docket 26-199 H(N)), DC
  OAG consumer and tenant inquiries (`Hardy, Rosell`)
- Demand letters from tenant counsel (`Li, Jing 535`)
- Lease-exit negotiation — release from lease, roommate release,
  move-out disputes (`LoCascio, Adam`, `Grove, Matthew`, `Boutla, Mrim`)
- Discrimination and harassment claims (`Berstein, Daniel`,
  `Eden, Aretmus`)
- Habitability, repairs, bed bugs, storage and property claims
- Safety incidents (`Ploessel, Matthew PH3` — stalker)
- Data breach and privacy (`Awoliyi, Tinuola`)
- Resident bankruptcy (`Zuckerman, Debbie`)

137 of the 164 sit under `__Bozzuto Management`, the same client whose
evictions do reach the sheet. Within one client's branch, evictions are
tracked and everything else is not.

Every case therefore carries a **`matter_type`** — `eviction` ·
`agency_complaint` · `demand` · `lease_exit` · `discrimination` ·
`habitability` · `incident` · `bankruptcy` · `insured_litigation`
(not ours) · `other` — inferred from the folder's branch, its
parenthetical, and the mail, and correctable by hand. `matter_type`
decides which divergence rules apply: a `lease_exit` matter absent from
the eviction spreadsheet is correct, and firing rule 6 on it would bury
the report in false positives.

---

## Two phases, and the gate between them

**Phase 1 — read and report disagreements.** The brain reads all four
sources and reports where they contradict each other. It writes nothing
outside its own ledger.

**Phase 2 — write.** Once the brain demonstrably understands the cases,
it updates the spreadsheet and the task list and takes administrative
work off the team.

**The gate is the accuracy record, and Phase 1 produces it.** Every
divergence the brain reports carries an ID and gets a disposition from
James or Christina: *right*, *wrong*, or *already handled*. That
disposition log is the evidence that the brain knows what is going on,
and it is the only thing that should unlock write access. Same shape as
the Pending LLT calibration loop, where the delta between what Rocky
drafted and what James sent became the spec for the next iteration, and
the same house rule that permissions follow validated capability rather
than anticipated need.

Nothing about Phase 2 needs to be decided now beyond building Phase 1 so
that it generates the record honestly.

---

## Storage

Everything lives under one roof on the share:
`Program Files\Rocky\Multifamily Tracker and Dashboard\` (config
**`mf_tracker_root`** — one key to repoint per machine, and every
component follows).

```
Multifamily Tracker and Dashboard\
├── LLT Watch\              source (a) — the hourly spreadsheet watch
│   ├── snapshot.json
│   ├── llt_changes.jsonl
│   └── Daily Changes\
├── Task Lists\             source (b) — the OneNote task lists
├── Case Brain\             the ledger reconciling all four sources
│   ├── cases.jsonl             registry: MF-##### + every alias per source
│   ├── observations.jsonl      append-only facts, idempotent by obs_id
│   ├── divergences.jsonl       what was reported + how it was dispositioned
│   ├── folder_map.json         Outlook folder id -> MF-#####  (hand-editable)
│   ├── aliases.md              human identity corrections  (hand-editable)
│   └── _Needs Review\          observations no rule could resolve
└── Folder Operations\      logs for mailbox changes Rocky made
    └── folder_renames.jsonl
```

**Data only.** Program code ships in `rocky.exe` and these design
documents stay in the repo, per the house deployment model: OneDrive is
a shared filesystem, not a deployment mechanism. Only `folder_map.json`
and `aliases.md` are hand-editable; everything else is append-only, so
correcting a mistake means adding a line rather than editing one.

Per-component overrides still exist (`llt_watch_root` and friends) for
the case where one piece has to sit somewhere else, but the default is
to derive from `mf_tracker_root`.

**Case IDs are `MF-#####`**, assigned on first observation, never reused,
separate from the RRID series (RRID means a Phase D litigation case
folder with a `case.json`; the ~1,060 open LLT matters are not that).

**Observations are idempotent by `obs_id`** — a hash of source, source
key, and fact. A cursor that gets re-read cannot double-write. That
removes the whole question of where cursors live and sidesteps the
sync-conflict trap that put `llt_watch`'s snapshot on the share.

```json
{"obs_id": "9f2a…", "ts": "2026-09-12T18:03:11Z",
 "source": "llt_sheet", "case": "MF-00412",
 "confidence": 0.94, "resolved_by": "property+unit+surname",
 "fact": {"kind": "stage", "value": "Term letter sent (Rent)",
          "as_of": "2026-09-12"},
 "evidence": {"sheet": "7.25.26", "row": {"…": "…"}}}
```

Fact kinds: `stage` · `ripe_date` · `court_date` · `task` · `owner` ·
`correspondence` · `payment` · `resolution`.

Nothing is ever overwritten. Current state per case is a **projection**
computed from that case's observations with per-source precedence, so a
wrong precedence rule is a one-line fix rather than a data migration.

---

## Identity resolution

Reuse what already works rather than writing a fourth matcher.
`pending_llt` solved this exact problem for property names and carries
the scars: `_property_signature()` (numeric and roman-numeral tokens must
match exactly, so "Cloisters I" never merges with "Cloisters II"),
`CONTACT_ALIASES` for curated same-property bridges, and
`NON_BOZZUTO_PORTFOLIOS` to stop one client's building matching
another's.

Confidence floor **0.75**, matching the Vault. Above it, attach. Below
it, park the observation in `_needs_review\` and ask a human; the answer
persists in `aliases.md` so the same question is never asked twice.

---

## Divergence rules — the Phase 1 product

Rules live in a plain-English file included in every run, the way
`litigation_updater`'s brain file does, so they can be added without a
code change. Seven the measured data already supports:

Every rule is gated on `matter_type`. A rule that assumes the eviction
pipeline must not fire on the 164 matters that legitimately never reach
the spreadsheet.

| # | Divergence | Applies to | Needs |
|---|---|---|---|
| 1 | **Stale ripe date** — sheet says ripe and unfiled, later mail suggests it resolved | eviction | (a)+(c) |
| 2 | **Court date drift** — mail names a hearing date the sheet's Next Steps does not | eviction | (a)+(c) |
| 3 | **Task/sheet contradiction** — task list says Filed, sheet is pre-filing | eviction | (a)+(b) |
| 4 | **Silent matter** — active, no mail in N days, no task entry | any | (a)+(c)+(d) |
| 5 | **Ghost matter** — mail still arriving for a matter that left the sheet | eviction | (a)+(c)+(d) |
| 6 | **Unregistered matter** — a live folder with no MF-##### at all | any | (d) |
| 7 | **Off-vocabulary stage** — Status outside `LLT_SHEET_STYLE_GUIDE.md` | eviction | (a) |
| 8 | **Should be on the sheet** — an `eviction`-typed matter with a live folder and no sheet row | eviction | (a)+(d) |
| 9 | **Untracked matter aging** — a non-eviction matter with no system of record and no activity in N days | the 164 | (c)+(d) |

Rule 6 originally read "a matter folder taking mail with no sheet entry,"
which would have fired on all 201 off-sheet matters every run. It now
means *unknown to the brain*, which is a genuine intake queue that drains
to zero. Rule 8 is the narrow version of the original intent: an eviction
that never got entered on the sheet is a real miss, and `matter_type` is
what tells the two apart.

Rule 9 is the one that only exists because of the 164. Those matters have
no weekly review anywhere, so "nobody has touched this in six weeks" is
information no current process can produce.

Rules 3, 6 and 7 need no inbox monitor, so the first divergence report
can ship before (c) exists.

Each divergence is reported once, tagged `[MF-D####]`, and stays open in
the ledger until dispositioned.

---

## Source adapters

**(a) LLT spreadsheet — nearly free.** `llt_watch`'s change records are
already observations in all but name; the adapter is a translation. It
also needs a full-state read for the initial population, which
`llt_watch.snapshot_rows` already returns.

**(b) Task list — write it now, run it later.** Blocked on delegated
`Notes.Read`. Build the parser against the OneNote page-HTML shape, whose
columns are known: *Property | Resident (Unit) | C/M | Project | Lead |
Priority/Notes*. Ship a degraded mode off the Search API, which today
returns page titles plus about 180 characters of text without any new
permission, so the adapter is testable before the gate opens.

**(c) Inbox monitor — mostly retargeting.** For each mapped folder, fetch
mail since that folder's cursor and have Claude turn it into typed facts.
This is what `--daily-cases` already does for RRID cases, against a
different folder list. One Claude call per folder with new mail per day,
never one per email, which is the standing house pattern. The Inbox root
holds only 50 items against 27,592 in matter folders, so Outlook rules
are already filing nearly everything and reading mapped folders alone is
close to complete coverage.

**(d) Folder map — the recon is written.** The script that produced the
numbers above becomes `--mf-brain --map`: propose folder → MF-#####
mappings with confidence, auto-apply the strong ones, list the rest for
review. `folder_map.json` stays hand-editable.

**Christina's mailbox** is a config block plus one IT step (adding her
mailbox to the Application Access Policy), which is how `inbox_cleaner`
onboards a user. Architect for several mailboxes from the first commit;
onboarding a second one should not be a refactor.

---

## Folder naming — measured, then a standard

Folder names are how (d) resolves, so uniformity is worth real money
here. Measured across the 2,945 folders under James's Inbox, the core
convention is **already uniform** and the damage is at the edges.

**What is already right:** 1,563 of 1,564 matter folders use
`Last, First` with a comma and one space. That is a convention, not an
accident, and nothing should touch it.

**What is not:**

| Defect | Count | Example |
|---|---|---|
| Trailing space | **123** | `Distance, Michael ` |
| Unbalanced parenthesis | 37 | `Evans, Paris (` |
| Trailing punctuation | 16 | `Sarah's House - ` |
| Double space inside | 12 | `Chevy Chase Retail  - SCK Mechanic's Lien` |
| Container prefix drift | — | `_` ×52, `__` ×517, `___` ×9, `_____` ×2, `______` ×2 |
| Unit placement split | — | in parens ×263, bare after the name ×28 |

The trailing spaces are the expensive ones: invisible on screen, and they
break every exact match. `__Allegro  ` and `__The Barrett  ` already
carry two.

### Proposed standard

```
Containers    __<Name>              two underscores, no more
Matters       Last, First (unit — issue)
```

- **Unit in parentheses**, never bare: `Cherry, Delante (726)`, not
  `Bynum, John 502`. Parens already win 263 to 28.
- **Issue after the unit**, same parenthetical, separated by a dash:
  `McKelvey, Camillia (211 — bed bugs)`. Where there is no unit, the
  parenthetical is just the issue: `Hardy, Rosell (DC OAG)`.
- **No trailing space, no trailing punctuation, no double spaces, parens
  balanced.**
- **Two underscores for containers.** The deeper runs (`_____CUVY`,
  `______BASA FUTURA`) are sort hacks to pin a folder to the top; those
  are a deliberate choice and should be left alone if James wants them,
  but they should be a short named list rather than an accident.

A caution on the parenthetical: today it is not reliably the unit.
`Negron, Damon (5333)` is the *property*. Standardizing it to mean
"unit first, then issue" is what makes it parseable, and it is the single
change that would most improve (d)'s match rate.

### How Rocky should do the renaming

Rocky's grant already carries `Mail.ReadWrite` on James's mailbox, so she
can rename folders today. That is precisely why it needs a gate: this is
a bulk, hard-to-reverse change to the thing James works in all day.

**The mechanic that makes it safe, now verified rather than assumed:** a
Graph mail folder's `id` does not change when its `displayName` changes.
Confirmed live 2026-09-13 by renaming one folder and re-fetching it by
its original id — HTTP 200, new name, message and subfolder counts
intact. `folder_map.json` is keyed on `id`, so renaming cannot break the
brain's own mapping, and every rename is reversible by PATCHing the old
name back. Outlook rules store a folder id too, so they are unaffected.

**Already done (2026-09-13):** the `__` prefix was stripped from all 205
property folders inside `__Bozzuto Management\{__DC, __Maryland,
__Virginia}` at James's request — those levels are all property now, so
the sort prefix had no job. Log and undo instructions at
`Multifamily Tracker and Dashboard\Folder Operations\folder_renames.jsonl`.
The flow used
there is the one `--mf-brain --rename` should implement: dry-run plan →
collision check → single-folder canary → log-before-acting → `$batch`
PATCH in twenties → re-read verification.

Checked on the Rocky side: nothing depends on James's *matter* folder
names. `--daily-cases` reads folder IDs from the case index, and the
name-based `resolve_folder_path()` (`rocky.py:414`) is used only for
Rocky's own operational folders in rocky@'s mailbox
(`Inbox\PMA emails`, `Inbox\Letterstream`).

Proposed as `--mf-brain --rename`:

1. **Propose, never act.** Write `rename_plan.md` to the share: current
   name, proposed name, and the rule that fired, grouped by defect class.
2. **Tiered approval.** The 123 trailing spaces and 12 double spaces are
   mechanical and lossless; James approves that tier as a batch. Anything
   that changes a *word* — moving a bare unit into parens, completing an
   unbalanced paren, reordering a parenthetical — goes one at a time over
   Teams with a YES, the `litigation_updater` pattern.
3. **Log before renaming.** Folder id, old name, new name, timestamp to
   `folder_renames.jsonl`, so the whole run is reversible by replay.
4. **Never delete or move a folder.** Rename only.

The mechanical tier alone (135 folders, no word changes) is worth doing
on its own and carries almost no risk.

---

## The staff dashboard — what the brain is actually for

The brain is the backend. The product is an intranet page where the
landlord-tenant team does its daily tracking instead of opening four
shared spreadsheets, and it is **read and write**: a user archives a
resolved matter on the page and the spreadsheet in Teams updates.

Hosting and authentication are a separate decision going to IT — see
`MF_DASHBOARD_IT_BRIEFING.md`. One constraint is settled regardless:
**this must be a new application, not a page on the existing
`dashboard.py`.** That app binds `0.0.0.0:5001` with no authentication
and its `/api/run` executes Rocky commands; it is safe only because
Tailscale is the perimeter. Whatever staff can reach must be fenced off
from it by ACL.

### The six panels

| Panel | Source | Available |
|---|---|---|
| **Court calendar** | hearing dates parsed from the sheet's Next Steps (`Initial Hearing: 9/18/26 at 9:00am (26-7159)`; 382 such lines today) | now — and Rocky already holds `Calendars.ReadWrite`, so these can also publish to a real shared M365 calendar |
| **Task list** | the OneNote weekly pages | blocked on `Notes.Read` |
| **Pending LLT matters** | the spreadsheet, sortable by property and ripe date | now |
| **New matters** | rules 6 and 8 — live folders with no `MF-#####`, evictions with a folder but no sheet row | needs (d) |
| **Untracked matters** | the 164 with no system of record | needs (c)+(d); **this panel exists nowhere today** |
| **Needs attention** | the divergence report | Phase 1 output |

The court calendar is nearly free: the grammar is already documented in
`LLT_SHEET_STYLE_GUIDE.md` and it needs no permission Rocky lacks.

### Writing to the spreadsheet

**Use the Graph Excel API** (workbook session plus range endpoints),
which edits individual cells and coexists with people working in Excel
Online. Downloading the workbook, editing it with openpyxl and uploading
it back would silently destroy a colleague's concurrent edits and strip
formatting. That must never be the mechanism.

Validate the Excel API against the real file before committing: 1.3 MB,
33 worksheets. If it cannot handle the workbook, that changes the plan,
not the safety rule.

Every dashboard edit becomes an observation in the ledger, and
`llt_watch` independently sees the resulting cell change on its next
hourly pass. The loop closes with no special-casing.

### Archive is a verb per matter type

Asked what archiving should do to the spreadsheet row, James: *"it will
depend on what is being archived."* Right — archive is not one
operation. Proposed defaults, to be corrected:

| Archiving a… | Spreadsheet | Folder | Notes |
|---|---|---|---|
| **Eviction** on the sheet | cut the row from the active revision tab, append to a `Closed` sheet with the resolution date and who archived it | flatten and delete | the outcome is worth keeping queryable |
| **Non-eviction matter** (the 164) | nothing — never on the sheet | flatten and delete | closure recorded in the brain only |
| **Task-list item** | n/a | none | needs `Notes.ReadWrite`; Phase 2 |
| **Insured/monitored claim** | **hand off, do not archive** | none | `litigation_updater` owns closure here, with its own snapshot-then-close machinery over the Litigation Updates chat |
| **Unregistered matter** (rule 6) | nothing | none | "archive" means *not a matter* — record a negative rule so it is never proposed again, the declined-cohort pattern from `inbox_cleaner` |

**One integration hazard if a `Closed` sheet is added:** `llt_watch`
reads `wb.active`. If somebody leaves the Closed tab selected when they
save, the watcher reads the wrong sheet and reports the entire docket as
removed. Before any Closed tab exists, `llt_watch` must pin the current
revision by its `M.D.YY` name pattern instead of trusting `wb.active`.

### Folder disposition on archive — flatten and delete

**Decided by James 2026-09-13**, after I proposed moving the intact
folder into a `_Closed` container instead. His call, and the flatter
tree is the point. Implemented with an interlock rather than an
argument:

1. Enumerate every message in the matter subfolder, and every
   **subfolder** of it (some matter folders have children — handle them
   or refuse to proceed).
2. Move each message to the parent property folder, logging
   `{message_id, from_folder_id, to_folder_id}` per message.
3. **Re-read the subfolder. The delete proceeds only if
   `totalItemCount == 0` and `childFolderCount == 0`.** A partial move
   aborts the delete and reports. This interlock keeps the failure mode
   at "folder still there" instead of "mail gone."
4. Log the folder's id, name and parent before deleting.
5. Delete.

Reversal replays the move log backwards and recreates the folder by
name. Confirm on one test folder whether a Graph `DELETE` on a mail
folder soft-deletes to Deleted Items or removes it outright — the same
single-folder canary discipline that verified id-stability before the
205 renames.

The residual risk James accepted: a reopened matter's mail ends up mixed
in with every other resident's at that property, and matters do reopen.

---

## Build order

1. Ledger, resolver, adapter (a), adapter (d), `matter_type`
   classification. Resolves and populates, produces no output.
   Reviewable on its own.
2. Divergence rules 3, 6, 7, 8 and the digest section. First real
   output, no inbox needed.
3. Adapter (c). Turns on rules 1, 2, 4, 5, 9.
4. Adapter (b) when `Notes.Read` lands.
5. Christina's mailbox.
6. Phase 2 writes, gated on the accuracy record from step 2 onward.

The dashboard tracks alongside, read-only at first: panels 1 and 3 (court
calendar, pending matters) can be built against step 1's data and are
worth standing up early, because a read-only page the team actually opens
is how the resolver's mistakes get found. Write actions land with step 6.

Folder renaming is independent of all six and can run whenever James
wants it; the mechanical tier does not even need the brain to exist.

---

## Notes

**Cost.** Steps 1, 2 and 4 are arithmetic and cost no tokens. Step 3 is
the only Claude spend: one call per active folder per day, so tens of
calls daily rather than thousands.

**Naming.** "Brain" already means two other things in this codebase —
`litigation_updater`'s plain-English rules file, and the Email Brain
corpus that moved to Minotaur in August. This is a third. Calling the
module `mf_brain.py` and the artifact the MF Case Brain keeps it
distinguishable; a future session reading "brain" should check which one.

**What Phase 2 will need when it comes.** **`Sites.Selected`** for the
spreadsheet — not `Sites.ReadWrite.All`; it grants write access to named
sites only, here just MultifamilyHousing, and is the version to ask IT
for. Plus `Notes.ReadWrite` for the task list. Both requested only once
write code exists and the accuracy bar is met. Where the dashboard can
run writes **as the signed-in user** (OAuth on-behalf-of), do so:
SharePoint's own version history then shows the actual person rather
than attributing every edit to `rocky@`. Writes should follow the
`litigation_updater` pattern: one proposal at a time over Teams carrying
its stored effects, executed only on a YES, with a full row snapshot
logged first so any change is reversible by replay. No batch writes, and
no row deletions.

**The thing to protect.** The value here is the lag between sources. An
email arrives days before the spreadsheet catches up, and that gap is the
product. Any future temptation to "reconcile" sources into one clean
number silently destroys the signal the brain exists to find.

**The second thing to protect.** The spreadsheet is not the case
population. 201 of the 391 live matter folders are absent from it by
design, and 164 of those have no system of record anywhere. Any rule,
report or schema that starts from "for each row on the sheet" re-creates
the blind spot this brain exists to remove.
