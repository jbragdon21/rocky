# LLT Watch — hourly change tracking on PENDING LLT MATTERS

`llt_watch.py` + `rocky.py --llt-watch`. Built 2026-09-12.

Christina's `PENDING LLT MATTERS.XLSX`, on the Multifamily Housing Teams
site, is the system of record for the landlord-tenant docket — about
1,030 pending matters across ~920 residents — and it is edited by hand
all day by several people. Nothing was recorded when a court date landed,
a matter was deleted, or a resident was added; the sheet just quietly
differed from what it said an hour ago.

LLT Watch keeps that history and feeds it to the Multifamily Digest.

---

## What it does

Once an hour, 8 AM to 7 PM:

1. **Stat, don't download.** One small Graph GET for the file's
   `driveItem` metadata. If its `cTag` matches the last snapshot, the
   sheet has not changed and the run ends — no download, nothing written.
   (`cTag` and not `eTag`: `eTag` also moves on metadata-only changes,
   which would make every run look like an edit.)
2. **Download and read every column.** `pending_llt` projects the sheet
   down to the nine fields its email templates need; LLT Watch reads them
   all, so a court-date column — or any column added to the sheet next
   month — is tracked without a code change.
3. **Diff against the last snapshot** and append each change to the
   record of account.

**Cost: zero Claude tokens.** A spreadsheet diff is arithmetic, not
judgment, and the digest phrasing is rendered from templates in
`llt_watch.py`. Twelve runs a day cost twelve metadata requests plus a
~1.3 MB download on the hours somebody actually edited the sheet.

---

## Commands

```
rocky.exe --llt-watch                     One pass (the scheduled call)
rocky.exe --llt-watch --force             Run even outside 8 AM-7 PM
rocky.exe --llt-watch --dry-run           Report changes, write nothing
rocky.exe --llt-watch --status            Snapshot age + the last 24h of changes
rocky.exe --llt-watch --local             Read the newest Downloads copy
                                          instead of SharePoint
rocky.exe --llt-watch --sheet 9.12.26     Read a named sheet, not the active one
rocky.exe --llt-watch --llt-watch-root D  Point at a different share mount
```

`--dry-run` is genuinely read-only: it logs and returns the change list
but leaves both the snapshot and the change log alone, so the next real
run still sees the same edits.

---

## Where things live

LLT Watch is source (a) of the Multifamily Tracker, so its data sits
under that subsystem's roof rather than beside it:

```
Program Files\Rocky\Multifamily Tracker and Dashboard\   <- mf_tracker_root
└── LLT Watch\
    ├── snapshot.json          # last known state of the sheet (~460 KB)
    ├── llt_changes.jsonl      # the record of account, append-only
    └── Daily Changes\
        └── YYYY-MM-DD.md      # the same changes, readable, for humans
```

Resolution order: `--llt-watch-root`, then config `llt_watch_root`,
then `<mf_tracker_root>\LLT Watch`, then the built-in default. Repointing
`mf_tracker_root` alone moves every component of the tracker, which is
the one change needed on the Rocky laptop (the share mounts there under
the `rocky` profile). `llt_watch_root` stays available for the case where
this one piece has to live somewhere else.

**The snapshot is on the share, not in `C:\Rocky`, and that is
deliberate.** The snapshot IS the dedup key for the change log. A local
snapshot on two machines means both machines report the same edit and the
record of account stops being a record — the same reasoning that put
PACER's spend ledger on the share. Writes are atomic (temp +
`os.replace`).

SharePoint location comes from the keys `pending_llt` already uses:
`sharepoint_hostname`, `sharepoint_site_path`, `llt_file_path`. No new
Graph permissions — the existing grant already carries `Sites.Read.All`.

---

## Scheduling

Task Scheduler, **hourly around the clock**, created by the dashboard's
LLT Watch entry (it is in the recommended schedule). The 8 AM-7 PM window
is enforced **in code**, not by the trigger: `schtasks` cannot express
"hourly, but only during the workday" without a `/du` duration, and
`dashboard.create_task` deliberately keeps its schtasks surface small.
An off-hours run exits before any network call.

Change the window with config `llt_watch_hours` (inclusive local hours,
default `[8, 19]`).

---

## Reading the change log

Each line of `llt_changes.jsonl` carries `ts`, `sheet`, `edited_by`,
`edited_at`, the matter's `label`, and one of:

| `change` | Means |
|---|---|
| `added` | A row that wasn't there an hour ago |
| `removed` | A row that's gone — usually the matter resolved |
| `modified` | Same matter, edited cells (`fields`: column, old, new) |
| `renamed` | A removal and an addition that are plainly one matter re-typed |
| `columns_changed` | The header row gained or lost a column |
| `sheet_rollover` | The active sheet changed — a new weekly revision |
| `touched` | The file was re-saved but no tracked value changed |

`touched` exists so a quiet digest can distinguish "nobody opened the
sheet" from "someone worked in it and nothing material moved." It is
logged but never rendered as a digest bullet.

---

## Row identity — the part that took two tries

The sheet has no ID column, so a row is keyed on
**property + tenant + unit**, normalized (the sheet is hand-typed).

**A key holds a LIST of rows, not one row.** 106 of the ~1,030 live rows
are a *second* matter for a resident who already has one — a rent case
and a smoking case at the same unit. Between snapshots, a resident's rows
are paired by how many cells they still agree on, so a status update on
one matter doesn't get attributed to the other. Change lines for these
residents name the matter in brackets: `Stratos — Boykins, Gary (1001)
[Rent sent]`.

**Do not fold the matter type into the key.** The type lives in the
Status cell, and Status is also where progress is recorded (`Rent` →
`Rent sent` → `Rent sent (re-issue/not properly served)`), so a status
update would read as a deletion plus a new entry — the exact noise this
process exists to remove. `pending_llt._classify_matter` is no help
either: it reclassifies a matter as `court` once a hearing appears, which
is a change we very much want reported *as* a change.

**Renames are paired on purpose.** Fixing a spelling — `Nicols, Christa`
→ `Nichols, Christa` — would otherwise read as a deletion plus a new
entry. Deletions here usually mean a matter resolved, so they must not be
diluted with typo noise. A pair requires the same property plus either
the same unit or the same name.

---

## Two refusals worth keeping

Both are cases where the honest failure is to report *nothing*:

- **A snapshot that won't parse is treated as a FIRST RUN** — baseline
  only, no change events. The sheet carries ~1,030 rows, so mistaking a
  corrupt snapshot for "the sheet was empty an hour ago" would file a
  thousand phantom "new entry" lines and bury the real changes. Losing
  one hour of history is the cheaper failure. A OneDrive sync conflict is
  the realistic way this happens. Same for a snapshot whose
  `version` doesn't match this build.
- **A parse that yields zero matters is a parse failure, not a cleared
  docket.** The snapshot is left untouched and the run reports an error.

---

## Watch-outs

- **The first run after deployment only baselines.** No changes are
  reported and no digest section appears; the *second* run produces the
  first real diff. This is logged plainly.
- **66 of the sheet's 77 columns are unnamed empty spacers** and are
  dropped from the snapshot (named columns are kept even when empty —
  their appearance in the header row is itself a layout change). An
  unnamed column that later gains a value is reported by its spreadsheet
  letter: "column AS added: ...".
- **Column A is unlabeled and carries the client** ("Bozzuto MD",
  "Horning", "Towner"), written once above its block of rows. Diffing it
  as an ordinary column turns one inserted row into "column A cleared"
  on one matter and "column A added" on another, so it's replaced with a
  synthetic `Client` column carrying the value on every row. A matter
  moving between clients is therefore reported; a row insertion is not.
- **The workbook holds 33 dated sheets**, one per weekly revision, and
  they are NOT in chronological order. LLT Watch always reads the
  *active* sheet, which is the current revision. When that changes, a
  `sheet_rollover` record explains the larger-than-usual change count —
  without it a future session would read a rollover day as data loss.
- **Ripe dates are free text** (`9.9.26`, `8.6.26 `, `Ripe on9.3.26`).
  LLT Watch compares the raw cell text, so it reports what the sheet
  literally says. Real date cells are rendered `M/D/YYYY` before
  comparison so an Excel reformat isn't an edit.
- **Two of a resident's matters edited in the same hour** can cross in
  the pairing, misattributing one old value. Strictly better than the
  alternative, but worth knowing if a line reads oddly.
- **`edited_by` is whoever saved last, not necessarily whoever made each
  change.** Graph reports one `lastModifiedBy` per file, so if two people
  edit the sheet within the same hour every change in that batch is
  credited to the second one. Inherent to hourly polling; the only fix
  would be per-cell version history, which Graph does not expose for a
  workbook. Read the attribution as "who had the sheet last", not as
  authorship.
- **The digest is drafted at 5:45 PM**, so the 6 PM and 7 PM runs' changes
  appear in the *next* day's digest. Nothing is lost — the digest's 24-hour
  window picks them up — they just arrive a day later.

---

## How the sheet is written

`LLT_SHEET_STYLE_GUIDE.md` is the entry style guide, derived from all 31
revision tabs: the eight process stages, the Next Steps grammar, and the
date / case-number conventions. Two things there bear on this code:

- **Status is free text — 1,761 distinct values for ~40 real states**, and
  163 stages have several spellings. So `Non-renewal sent → Non-Renewal
  sent` will show up as a real change in the digest. That is correct
  behavior. **Do not normalize Status inside `llt_watch`**; a record of
  account should say what the sheet said. The style guide is the fix.
- **Writ and eviction live in Next Steps, not Status** (`WRIT FILED`,
  `EVICTION: <date>`), and the stage order is not monotonic: a sent
  notice cycles back to a drafted-reissue state often enough that no
  code should assume forward-only movement.

---

## Consumer

The Multifamily Digest's **LLT Spreadsheet** section
(`multifamily_digest.llt_section`), drafted into James's Drafts at 5:45
PM addressed to the multifamily group. LLT changes count toward the
digest's quiet-window test: a day whose only activity was Christina
working the docket still earns the digest, because those edits are what
the group needs to see.

Nothing else reads `llt_changes.jsonl`, so its shape is free to grow.
