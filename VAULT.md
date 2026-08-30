# The Vault — shared document library for Remy filings

The Vault is a shared OneDrive folder ("The Vault", beside Rocky Cases)
where Rocky gathers the documents that support Remy drafting and other
filings — leases, ledgers, affidavits of service, notices — organized so
the whole team can grab what they need without asking who has the file.

Built 2026-07-29. Code: `vault.py`, dispatched as `rocky.py --vault`.

---

## What the team sees

```
The Vault\
├── README.md                    # conventions (auto-created)
├── Vault Index.xlsx             # searchable index, refreshed every run
├── _Needs Review\               # docs Rocky couldn't confidently identify
├── _vault\
│   ├── catalog.jsonl            # machine catalog — one line per stored doc
│   └── activity.jsonl           # ingest audit trail
└── <Property>\
    └── <Tenant, Name>\
        ├── Lease - Smith, John - 2026-05-01.pdf
        ├── Ledger - Smith, John - 2026-07-28.xlsx
        └── Affidavit of Service - Smith, John - 2026-07-15.pdf
```

- **Property/tenant folders** are created by Rocky from Claude's read of
  each document (plus the email body / Dropbox path for hints). Property
  names are grounded (2026-08-24) against Remy's property_table.csv AND
  (2026-08-29) `_vault\property_aliases.json` — a human-editable JSON on
  the share mapping variant spellings to the canonical folder ("Alula" →
  "Alula at Bridge District"); it also carries `known_properties` for
  communities missing from Remy's table. Teach new variants there — every
  run rereads it. Before creating folders, Rocky reuses an existing
  property folder that matches case-insensitively and an existing tenant
  folder for the same person ("Holland, D" arriving when "Holland,
  Deleona" exists files into the existing folder; two plausible matches =
  ambiguous = no reuse).
- **Filename dates are the document's OWN date** (service date, lease
  execution, ledger through-date) — never the email/Dropbox timestamp.
  When no date is extractable the name says `undated` (revised
  2026-08-29; the old source-date fallback stamped half the vault with
  the client's 8/6 Dropbox upload date). Scanned PDFs with no text layer
  are attached to the classify call as PDF pages (first 4), so Claude
  reads the scan itself — dates and parties now come from the image.
- **`_Needs Review`** holds anything below the confidence floor (0.75) or
  missing a property/tenant. Files keep their original name prefixed
  `YYYY-MM-DD_<hash8>_`. Humans may move these into place by hand; the
  index marks catalog entries whose file has moved as
  "Missing — moved or deleted" rather than breaking.
- **`Vault Index.xlsx`** has a "Vault Index" sheet (Property, Tenant,
  Type, Date, File, Folder, Source, Added, Status) and a "Needs Review"
  sheet. Regenerated from the catalog at the end of every run and via
  `--vault --reindex`. If someone has it open in Excel the rebuild is
  skipped that run (logged), not an error.

Team access is ordinary OneDrive sharing on "The Vault" folder. On the
Rocky laptop the folder must be pinned **"Always keep on this device"**
(same Files On-Demand constraint as Rocky Cases).

## The three sources

Each `--vault` run walks all configured sources (or one, with
`--source inbox|vault-mail|dropbox`):

1. **James's inbox (`inbox`).** Fetches messages with attachments since
   the last cursor (first run backfills `vault_backfill_days`, default 7).
   Attachments are prefiltered (pdf/doc/docx/xls/xlsx/xlsm/csv, signature
   images dropped), then one Claude call per email classifies each
   attachment. Only the core types are taken from this opportunistic
   source: **lease, ledger, affidavit_of_service** — notices and misc
   docs ride in via the other two channels.
2. **rocky@ "Vault" submissions (`vault-mail`).** Any email in rocky@'s
   inbox that says "vault" (config `vault_subject_keyword`) in the
   subject **or in the forwarding note** — the body text above the
   quoted `From:` header, so "please add to vault" typed atop an
   unchanged-subject forward works (added 2026-08-23; capped at 10 note
   lines, or the first 3 body lines of a non-forward, so deep quoted
   mentions never trigger) — is a team submission: **every** substantive
   attachment is ingested (unknown types get doc_type "other" with a
   short label; unidentifiable ones go to _Needs Review — explicit
   submissions are never silently dropped, even if the Claude API is
   down). Filing hints in the body ("this is the Smith lease at Maple
   Gardens") are honored. Rocky replies from rocky@ confirming what was
   filed where (`vault_confirmation_replies`, default true; the outbound
   allowlist keeps this internal-only, and replies aren't needed for the
   filing itself).

   **Processed mail leaves ROCKY'S inbox only (revised 2026-08-24).** A
   handled vault-mail submission moves to `Inbox\The Vault` in rocky@'s
   mailbox (`vault_processed_folder`; "" disables). James's own inbox
   is NEVER touched by the Vault sweep — it files copies of his
   attachments and leaves his mail exactly where it is (his decision
   2026-08-24; a briefly-shipped James-inbox move was removed same
   day). Moves use rocky@'s delegated Mail.ReadWrite.Shared (no new
   permissions), are best-effort (failure logs, filing stands), and run
   LAST because a Graph move changes the message id. Same pattern as
   the Litigation Updater's `Inbox\Litigation Updater` and Mailing
   Affidavits' `Inbox\Letterstream` (`letterstream_processed_folder`).

3. **Dropbox (`dropbox`).** Each entry in `vault_dropbox_accounts` is
   one Dropbox *login*, which can watch two kinds of things:
   - **Its own `folders`** — incremental via persisted Dropbox delta
     cursors; first pull per folder is a full recursive listing. Folder
     files also carry skip-before-download marks (like links), so cursor
     resets and catch-up runs never re-download processed files. **This
     is the mode to use for frequent (e.g. hourly) runs**: a client's
     shared folder can be mounted into James's own Dropbox via the
     link's "Add to my Dropbox" button (read-only mounts work; nothing
     changes on the client side) and watched as a folder — an idle run
     is then one "what changed?" call instead of a full re-walk.
     Requires a Full-Dropbox-access app (App-folder apps can't see
     mounts).
   - **`shared_links`** — shared-folder links received from clients
     (`https://www.dropbox.com/scl/fo/...` with the `rlkey` kept
     intact). The Dropbox API reads shared links with *any* authorized
     token, so one app under James's own Dropbox account covers every
     client's link — no client-side setup at all. Shared-link listing
     has no delta cursors, so Rocky re-lists each run and keeps a
     per-file processed mark (`server_modified|size`) in state; only
     new or changed files are downloaded and classified. Each link
     takes an optional `description` that feeds the classifier context
     (e.g. "DC RAD-stamped notices").

   Both paths honor `max_age_days` (per link, or account-wide; CLI
   `--max-age-days N` overrides) — files whose Dropbox modified date is
   older are skipped entirely, e.g. 183 keeps only the last ~6 months.
   Both paths cap at `max_files_per_run` (default 200) per run — the
   remainder arrives on subsequent runs. Files are classified in
   batches of 8 (the Dropbox path is shown to Claude — it often names
   the property). Unconfigured/unauthorized accounts are skipped with a
   log line, never an error.

**Dedup:** every stored document's SHA-256 lives in the catalog; the same
bytes arriving again from any source are skipped (logged as
`duplicate_skipped`). This also makes cursor re-reads harmless.

**Cursor safety:** if classification fails (API outage), the inbox pass
stops and holds its cursor, and a Dropbox folder's cursor isn't advanced —
nothing is silently lost; the next run retries.

## CLI

```
rocky.exe --vault-dropbox                  # ONE source: Dropbox pulls
rocky.exe --vault-mail                     # ONE source: rocky@ "Vault" submissions
rocky.exe --vault-inbox                    # ONE source: James's inbox sweep
rocky.exe --vault                          # all sources in one run (manual use)
rocky.exe --vault --dry-run                # classify + log, write nothing
rocky.exe --vault --source dropbox         # legacy spelling of one-source runs
rocky.exe --vault --backfill-days 30       # REWIND the mail windows 30 days
                                           # (past the cursors; dedup makes
                                           # re-covered ground harmless —
                                           # use to catch a missed email)
rocky.exe --vault --limit 10               # cap emails/files per source
rocky.exe --vault --max-age-days 183       # skip Dropbox files older than ~6mo
rocky.exe --vault-digest [--hours N]       # email the day's additions from rocky@
rocky.exe --vault --status                 # catalog counts + cursor state
rocky.exe --vault --reindex                # rebuild Vault Index.xlsx only
rocky.exe --vault --dropbox-auth <name>    # one-time Dropbox OAuth
rocky.exe --vault --cleanup [--execute]    # merge fragmented property/tenant
                                           # folders + casing + exact-dup files;
                                           # dry-run writes _vault\cleanup_plan.txt
rocky.exe --vault --reclassify-review [--limit N] [--dry-run]
                                           # retry _Needs Review with scanned-PDF
                                           # vision; files what comes back confident
```

Both maintenance commands accept `--vault-root <path>` (the share mounts
at a different local path on each machine). **Catalog-rewrite caution:**
--cleanup --execute and --reclassify-review rewrite catalog.jsonl
(backed up to `_vault\catalog.backup-*.jsonl` first). A vault ingest on
ANOTHER machine appending to the catalog while the rewrite syncs makes
OneDrive fork a conflict copy (this happened live 2026-08-29 and had to
be merged by hand). Run them right AFTER an hourly ingest finishes, not
around :49 when the Rocky laptop's tasks fire — or pause the Vault
schedules first. An in-progress check (activity.jsonl) aborts the
rewrite when a live run looks unfinished; `--force` overrides.

All per-run flags (--dry-run / --limit / --backfill-days /
--max-age-days) work on the split flags too.

**Split tasks (2026-08-22).** The three sources are independently
schedulable commands with independent instance locks and per-source
state files (`C:\Rocky\vault\state_inbox.json` / `state_vault_mail.json`
/ `state_dropbox.json`; a pre-split `state.json` is auto-migrated on
first run), so they can overlap safely — a multi-hour Dropbox ingest
never blocks mail filing. Don't schedule the all-sources `--vault`
alongside the split tasks (same state files). The shared catalog is
append-only and the index write is atomic (temp + swap), so concurrent
finishes are safe.

**Vault Digest — SUPERSEDED 2026-08-23 by the Multifamily Digest**
(`rocky.exe --multifamily-digest`, same 5:30 PM slot), which carries
this same Vault section plus certified mail and affidavit activity from
the Mailing Affidavits process (see LETTERSTREAM.md). Since 2026-08-29
the Multifamily Digest lands as a DRAFT in James's Drafts folder
(pre-addressed to the multifamily group; James sends) rather than an
email from rocky@. The standalone command below still works for manual
use.

**Vault Digest (`--vault-digest`, suggested 5:30 PM daily).** Emails
the window's additions (default 24h) from rocky@ to
`vault_digest_recipients` (default James): a Filed table grouped by
property (Property / Tenant / Document / Date / From — the From column
names the Dropbox link, the rocky@ submitter, or the inbox sender) and
a Needs Review list prompting someone to file the unidentified items.
Built from the catalog, so dry-run ingests never appear. A quiet day
sends no email at all.

Dashboard: a "Vault" group with "Vault Dropbox", "Vault Rocky Inbox",
and "Vault James Inbox" (each defaulting to an hourly schedule),
"Vault Digest" (17:30 daily), plus "The Vault" full run for manual use.

## Setup

**Mail sources — nothing new.** Reads use the app-level token
(`client_secret` in config + the Application Access Policy, which already
covers jbragdon@ and rocky@).
Confirmation replies use rocky@'s existing delegated Mail.Send with the
outbound allowlist. No IT steps.

**Vault folder.** Defaults to `<cases_root parent>\The Vault` (i.e.
beside Rocky Cases); override with `vault_root`. Pin it on the Rocky
laptop; share it with the team from OneDrive.

**Dropbox — one-time app setup:**

1. Create an app at https://www.dropbox.com/developers/apps under the
   Dropbox account Rocky will authenticate as. **For shared links from
   clients, this is simply James's own Dropbox account** — the API reads
   any shared link with any authorized token. Choose "Scoped access";
   "App folder" is fine for link-only use ("Full Dropbox" only if Rocky
   should also scan the account's own existing folders). Permissions
   tab: enable `files.metadata.read`, `files.content.read`, and
   `sharing.read` (needed for shared links) — then re-run step 3 if the
   scopes change later, since tokens carry the scopes from consent time.
2. Add the account to `vault_dropbox_accounts` in config.json: `name`,
   `app_key`, `app_secret`, plus what to watch — `shared_links` entries
   (name + full URL including `rlkey`, optional `description`) and/or
   `folders` in the account's own Dropbox (`""` = whole visible root).
3. Run `rocky.exe --vault --dropbox-auth <name>` on the Rocky laptop:
   open the printed URL while signed in to that account, approve, paste
   the code back, then paste the printed `refresh_token` into that
   account's config entry.
4. `rocky.exe --vault --source dropbox --dry-run` to sanity-check the
   first pull before going live.

If a client ever revokes or rotates a shared link, update the `url` in
config; the per-file marks are keyed by account+link *name*, so keeping
the same `name` avoids re-processing (dedup would absorb it anyway).

## State & logs

- Cursors: `C:\Rocky\vault\state.json` (local; per-mail-source ISO
  timestamps + per-account/folder Dropbox cursors).
- Catalog + activity: on the share under `The Vault\_vault\` so the audit
  trail travels with the documents.
- Operational logging to `rocky.log` with a `[vault]` tag, instance lock
  `state/rocky_vault.lock` (via main()'s standard lock).

## 2026-08-29 folder cleanup (one-time, executed)

The 8/17 bulk ingest predated property grounding and scanned-PDF
support, leaving ~400 files split across variant folders. A cleanup pass
(`--vault --cleanup --execute`) merged them: 433 files moved, 10 folder
casings fixed, 1 byte-identical duplicate removed, ~700 catalog entries
normalized. Same-person tenant variants merged on strict rules
(initial/extension always; one-letter first-name slips only in cleanup,
never live filing); genuinely ambiguous groups were left alone and are
listed in `_vault\cleanup_plan.txt`. Deliberately NOT merged: "Cloisters
I/II" vs "The Cloisters" and "Solstice I/II" vs the table's
"Solstice - 3500/3534 E Capitol" (distinct phases — needs a human call,
then alias lines).

## Design notes / future

- Classification confidence floor is a constant (`CONFIDENCE_FLOOR` =
  0.75 in vault.py); doc-type vocabulary: lease, ledger,
  affidavit_of_service, notice, other.
- **Dropbox double-coverage:** the `rad-notices` shared link and the
  mounted `RAD CASES` folder watch the same client tree — the same
  document arrived through both routes with different bytes (dedup can't
  catch that). Remove the `rad-notices` entry from `shared_links` in the
  Rocky laptop's config.json; the mounted folder (delta cursors) is the
  keeper per the hourly-runs guidance above.
- Level 0 holds: the only outbound mail is the guarded confirmation
  reply from rocky@ (internal-only allowlist, both in code and tenant
  mail-flow rule).
- Not built (deliberately): watching case folders / Rocky Cases for
  vault material (Remy requests already carry their documents through
  rocky@'s inbox); a Teams channel for submissions (email is the
  established pattern); OCR for scanned leases with no text layer —
  those land in _Needs Review today. Possible later: reuse
  `extract_image_text_via_vision` for scanned PDFs.
- Teaching: property-name misfilings go in
  `_vault\property_aliases.json` (built 2026-08-29 — no rebuild needed);
  other misfilings become prompt refinements in `_CLASSIFY_RULES`
  (vault.py) — or, if this recurs enough, a `vault_instructions.md`
  following the classifier's incremental-teaching pattern.
