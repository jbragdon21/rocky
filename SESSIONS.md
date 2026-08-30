# Rocky — Session Log

A running record of every working session on Rocky. Each session prepends a new dated entry at the top so the most recent state is always at the start of the file.

---

## How to use this log

**At the START of every session,** read in this order:
1. `BUILD_REFERENCE.md` — the project's architectural ground truth (rarely changes)
2. The first 1–2 entries below in this file — what changed most recently and what was open
3. `instructions.md` — current plain-English rules for the classifier
4. The current state of the code (`Icon/rocky.py`, `review.py`) only when the planned work touches it

**At the END of every session,** add a new entry at the top of the log (above the previous one). Use the template at the bottom of this file. Keep entries tight: bullets, not prose. Include:
- **What changed** — files touched and the *why*, not the diff
- **Decisions made** — anything the future session would otherwise re-debate
- **Open items** — things deferred or blocked
- **Watch-outs** — surprises, bugs found, things future sessions should know

This log is append-only history. Don't rewrite past entries. If a past decision was overturned, write a new entry that says so and links back ("supersedes 2026-04-30 entry on X").

Naming entries: `## Session YYYY-MM-DD — short title`. If multiple sessions in one day, add `(2)`, `(3)`, etc.

---

## Session 2026-08-29 (3) — Vault audit + overhaul: aliases, tenant reuse, honest dates, scanned-PDF vision, folder cleanup

**What changed**

- **Audit findings (the trigger):** the 8/17 bulk ingest (pre-grounding,
  pre-vision) had fragmented the Vault — 129 property spellings for ~85
  real properties, 145 same-person tenant-folder variants, half the
  filed docs (1,254) date-stamped `2026-08-06` (the client's Dropbox
  upload date, not the document date), 236 `_(n)` collision files, a
  618-file _Needs Review backlog (scanned PDFs, no text layer), and one
  intra-batch SHA-dedup miss.
- **`vault.py` — five fixes:**
  1. `_vault\property_aliases.json` on the share (NEW teaching file):
     variant→canonical aliases + supplemental `known_properties`;
     `_ground_property` checks aliases first, and the containment
     fuzzy-match no longer lets a longer name snap onto a contained one
     ("Classic @ Modern on M" ≠ "Modern on M") unless the extra words
     are generic stopwords.
  2. Tenant folder reuse at filing: exact case-insensitive property
     folder reuse + same-person tenant matching (`_same_person`:
     initials/extensions always; one-letter typos only in cleanup).
     Ambiguous matches (two plausible folders) reuse nothing.
  3. Honest dates: `_doc_date` returns the classifier's date or None
     ("undated" in filenames) — the source-timestamp fallback is gone.
     Scanned PDFs (no text layer) now ride into the classify call as
     attached PDF pages (first 4, `_scan_pdf_excerpt`); PDFs pypdf
     can't open are NOT attached (an invalid PDF 400s the whole batch —
     hit live).
  4. Intra-run dedup gap closed: `_classify_and_file_batch` re-checks
     `seen` at filing time (two identical files in one batch both
     passed the download-time check — the Williams pair, 6ms apart).
  5. Maintenance commands: `--vault --cleanup [--execute] [--force]`
     (merge fragmented folders + casing + exact-dup files; dry-run
     writes `_vault\cleanup_plan.txt`) and `--vault --reclassify-review
     [--limit N]` (retry _Needs Review with vision, refile confident
     results, catalog rewritten once with backup). Both take
     `--vault-root <path>` (share mounts differently per machine).
- **Cleanup EXECUTED** (from the dev laptop against the share): 433
  files merged to canonical folders, 10 casing renames, 1 byte-dup
  removed, ~700 catalog entries normalized, index rebuilt. Idempotent —
  re-run finds nothing. Ambiguous groups left alone (listed in the plan
  file).
- **Reclassify-review EXECUTED:** 286/300 + 239/332 + final pass —
  _Needs Review drained from 618 to under ~100, with REAL service dates
  read off the scans.
- **Docs:** VAULT.md (aliases, dates, maintenance commands, cleanup
  record, Dropbox double-coverage note), config.example.json comment,
  rocky.py help text. New exe built + deployed to OneDrive.

**Decisions made**

- Canonical property names: Remy's table wins, EXCEPT where its
  stylization loses to established usage via alias ("Insignia on M",
  "Novel South Capitol", "AME at Meridian Hill", "70 Capitol Yards" for
  the table's "Seventy1Hundred Capitol Yards").
- NOT merged (needs a human call): "The Cloisters" vs table's
  "Cloisters I/II"; "Solstice I/II" vs table's "Solstice - 3500/3534 E
  Capitol" — distinct phases; add alias lines once James confirms.
- Typo-level tenant merges (Timeca/Timeka) allowed in cleanup only,
  never live filing; the canonical spelling was picked by doc count and
  is worth a skim in `cleanup_plan.txt`.

**Open items**

- **Rocky laptop config.json:** remove the `rad-notices` entry from
  `shared_links` — the mounted `RAD CASES` folder covers the same tree
  (same doc arrived via both routes with different bytes; dedup can't
  see that).
- Confirm Cloisters/Solstice phase mapping; add aliases.
- `_Needs Review` residue (~dozens): items Claude still can't
  confidently place — rerun `--vault --reclassify-review` anytime, or
  file by hand.

**Watch-outs**

- **Catalog rewrites race OneDrive.** The first `--cleanup --execute`
  collided with the Rocky laptop's :49 hourly ingest → OneDrive forked
  `catalog.jsonl` (merged by hand same session; move-map matching also
  made case-insensitive, the root cause of 22 stale paths). Run
  maintenance commands right after an hourly ingest, never around :49;
  an activity-log in-progress check aborts obvious overlaps
  (`--force` overrides).

**What changed**

- **`rocky.py`:** removed the 24/7 Remy inbox monitor as a runnable process —
  `run_monitor_remy_cli`, `remy_poll_cycle`, `_deliver_remy_draft`, the
  `remy_last_check` state helpers/constants, the `--monitor-remy` dispatch,
  and its docstring/help lines. A retirement comment marks where the section
  lived. KEPT: `classify_email` + `CLASSIFIER_SYSTEM_PROMPT` and
  `remy_runner.py` (the headless Remy engine) for a future re-wire.
- **`dashboard.py`:** dropped the "Monitor Remy" button.
- **`TASKS.md`:** dropped the `--monitor-remy` row from the schedule table.
- **`config.example.json` / `BUILD_REFERENCE.md`:** scrubbed/annotated the
  `--monitor-remy` mentions (the Teams-in-the-loop idea now points at
  `--monitor` as the host loop).

**Decisions made**

- Retired because it's unused AND structurally stale-prone: a boot-launched
  forever-process keeps running whatever rocky.exe it started with, so every
  exe rebuild leaves it on an outdated model of the world. If headless Remy
  returns, run it as an entry in `monitor_commands` — the monitor launches
  fresh subprocesses each cycle, so it always runs the current exe.
- `--vault-inbox` (James's inbox sweep) is already in the monitor defaults —
  no monitor_commands change needed; question came up this session.

**Open items**

- **Rocky laptop:** delete the `--monitor-remy` Task Scheduler entry (and its
  wrapper, if any) and kill the running process if one is still up. The stale
  `state/remy_last_check.json` can be deleted or ignored.

**Watch-outs**

- Old SESSIONS.md entries still mention `--monitor-remy` — historical,
  left as-is (append-only log).

---

## Session 2026-08-29 (2) — Remove James's Inbox Cleaner process (--inbox-james)

**What changed**

- **`inbox_cleaner.py`:** removed everything that existed only for
  `--inbox-james` (supersedes the 2026-07-12/13 build entries): the
  `--cycle` one-shot, the "sort with friends" conversation-sort pass
  (`propose_conversation_sort` + helpers, the `conversation_sort` cohort
  kind, its `message_ids`/`target_folder_id` handling in
  `_cohort_message_ids`/`execute`), open chat mode (`_open_chat_handle`,
  action proposals, `_apply_route_ops`, code_changes backlog), and the
  Engineer workup. ~1,070 lines gone (3,784 → 2,718). Matt's strict-
  protocol machinery is untouched; `load_chat_exclusions`/`_drop_excluded`
  stay (exclude_senders/exclude_domains in sender_routes.json are now
  hand-edited only).
- **`dashboard.py`:** "James Inbox" button removed from `ROCKY_COMMANDS`;
  comments updated. ("Vault James Inbox" is a different feature —
  untouched.)
- **`config.json` / `config.example.json`:** `inbox_users.james` block and
  `_inbox_james_comment` removed. Matt's block stays.
- **`rocky.py`:** header docstring + `--help` lose `--cycle`/`--engineer`.
- **Docs:** `INBOX_CLEANER.md` (James section deleted, removal note at
  top), `BUILD_REFERENCE.md` ("Sort with friends" + "Open chat +
  Engineer" paragraphs replaced with a removal note), `VAULT.md`
  (`--inbox-james` mention dropped).

**Open items**

- On the Rocky laptop: delete the `\Rocky\James Inbox` scheduled task if
  one was created (none exists on the dev machine), remove the
  `inbox_users.james` block from `C:\Rocky\config.json`, pull, rebuild
  the exe.
- Data left in place on purpose: `Rocky Inboxes\inbox-james\` on the
  share (rules.md, cohorts, logs, engineer reports, code_changes.md) and
  `C:\Rocky\inbox_cleaner\james\` + `state\inbox_cleaner_james.json` on
  the laptop. Delete manually if wanted.

**Watch-outs**

- A stale worktree (`.claude/worktrees/compassionate-goldberg-c55cbf`)
  holds an older, unrelated "James Inbox" pull/annotate/act experiment
  that never landed on main — ignore it or prune the worktree.

---

## Session 2026-08-29 — Multifamily Digest: draft into James's Drafts (Maple model)

**What changed**

- **`multifamily_digest.py`:** `--multifamily-digest` no longer emails from
  rocky@. It now creates a DRAFT in James's Drafts folder (same model as
  `--maple-digest`), pre-addressed to the multifamily group — James reviews
  and sends. Default To: list hardcoded as `_DEFAULT_DRAFT_RECIPIENTS`
  (Mondragon, Wenger, Ronan, Brown, Frantzis, Kobylski, O'Mara, Araviakis,
  Goranin — set by James 2026-08-29); override with NEW config key
  `multifamily_digest_draft_recipients`, mailbox with
  `multifamily_digest_mailbox` (default `user_email`). The old
  `multifamily_digest_recipients` / `vault_digest_recipients` fallback chain
  is ignored (new key on purpose — same reasoning as the Maple client-digest
  keys: a lingering old config must not silently mis-address the draft).
  Subject dropped the "Rocky — " prefix (it's James's outgoing email now):
  `Multifamily Digest (Month D, YYYY)`. Quiet day = no draft (unchanged).
- **`pending_llt.py`:** `create_draft_email()` grew an optional
  `attachments` param (same `{"name","path","contentId"}` shape as
  `outbound.send_mail_guarded`) so the digest's inline banner image
  survives the move to a draft. Inline images get `contentId`/`isInline`.
- **`dashboard.py`, `config.example.json`, `LETTERSTREAM.md`, `VAULT.md`:**
  descriptions updated to the draft model.

**Decisions made**

- Token path mirrors `run_maple_digest_cli`: `acquire_token(get_msal_app())`
  + `audit_token_scopes` — proven in production for drafting into James's
  mailbox, so no new permissions needed.

**Open items**

- Rebuild the exe and pull on the Rocky laptop for the change to take
  effect on the scheduled 17:45 run.

**Watch-outs**

- Draft recipients live on a draft in James's mailbox, so the outbound
  allowlist doesn't gate them — the To: list itself is the safety here.

---

## Session 2026-08-25 — Dashboard: LetterStream (Certified Mail card, fetch box, registry)

**What changed**

- **`dashboard.py`:**
  - Registry: `letterstream` added (new **"Mail"** group; dry-run capable;
    📅 default 08:00 but deliberately NOT `recommended` — the `--monitor`
    loop already sweeps it every 10 min, same reasoning as the hourly
    vault-mail/vault-inbox jobs, so ⚡ Auto-setup must not install a
    double-runner). `monitor` also added (group "Other", stays-running,
    like monitor-remy) so the fast loop can be started from the dashboard.
  - `letterstream_summary()` reads `<DATA_DIR>\affidavits\state.json`
    directly (cheap file read, no API calls) and rides along on every
    `/api/status` poll as a new `letterstream` key: affidavits pending
    approval, mailings awaiting release, jobs in flight, API-configured.
  - New `POST /api/letterstream/fetch`: validates a tracking#/doc id
    (`[A-Za-z0-9._-]{3,64}`, whitespace stripped) and launches
    `--letterstream --fetch <ref>`. First flag in argv is `--letterstream`,
    so it shares the letterstream instance lock and the running-now
    indicator works unchanged. `launch_command`/`command_argv` grew an
    `extra_args` param for this (never raw user input — callers validate).
- **`templates/dashboard.html`:** "Certified Mail" sidebar card (three
  sections with `[AM-####]`/`[CM-####]` tag + who + detail rows; an
  "API not set up — email fetch only" header note when keys are missing;
  Fetch-proof input + button, Enter submits). Plain-English mode:
  `[affidavits]` tag → "Certified mail" process name, plus ~14 rules for
  the AM/CM lifecycle lines (sent for approval, approved/declined,
  preauth'd, released with/WITHOUT affidavit, cancelled, not-mailed-yet,
  mailed, proof-not-ready, tracking failed, API-not-configured, run
  complete incl. dry-run variant).
- **Docs:** DASHBOARD.md feature bullet; LETTERSTREAM.md note pointing at
  the dashboard card/fetch box + the monitor-overlap caveat.

**Decisions made**

- `--probe` / `--status` got no Run buttons: they print to stdout, which
  the dashboard discards (DEVNULL). The status card *replaces* `--status`;
  probe stays CLI-only.
- `--mail <packet.pdf>` / `--ingest <proof.pdf>` not surfaced — they need
  a file path/upload; the email channels already cover them.

**Watch-outs**

- Ships with the next **dashboard.exe** rebuild/deploy (rocky.py is
  untouched, so rocky.exe needs no rebuild).
- The Certified Mail card reads the machine-local state file — on any box
  other than the Rocky laptop it will just say "Nothing waiting."
- Verified locally end-to-end with a seeded sample state.json (card
  rendering, empty state, fetch validation 400 + toast, plain-English
  replacements incl. the `$$$4` cost escape); the sample file was removed
  after testing.

---

## Session 2026-08-29 — Maple digest: add five CCs (Bozzuto + Gallagher)

**What changed**

- **`rocky.py` (`_DEFAULT_MAPLE_CLIENT_DIGEST_CC`) + `config.example.json`:**
  added rprice@bozzuto.com, ccooley@bozzuto.com, mbarry@bozzuto.com,
  cesmeir@gallagherllp.com, sstephey@gallagherllp.com to the Maple client
  digest CC list (per James). pma@bozzuto.com stays first and remains
  load-bearing (reply-all routing into rocky@'s watched folder); the five
  individuals are courtesy copies.

**Watch-outs**

- Takes effect on the Rocky laptop only after the exe is rebuilt/deployed.
  If that machine's config.json sets `maple_client_digest_cc`, it overrides
  the new default — update it there too.

---

## Session 2026-08-08 — Case CLAUDE.md audit + new case scaffolds + index skeleton-row bug

**What changed**

- **Audited every case CLAUDE.md in Rocky Cases for daily-run compatibility.**
  All litigation cases share the standard sections (Activity Logging, Folder
  Structure, Classification Rules, upload workflow, Rocky Digest); fixed the
  outliers:
  - Willingham (0023): file was TRUNCATED mid-word ("## Tec") — completed
    Technical Notes + added the missing Rocky Digest section.
  - Whalen (0015): instructions pointed at legacy `Raw Data` as staging, but
    the daily run reads `Raw Documents` only — made Raw Documents the staging
    area (Raw Data = legacy archive) and added rules for folders 09–11.
  - Ogunnupe (0008), Phillips-Moore (0002), DC AG Fees (0014): folder tables/
    classification rules referenced folders that don't exist on disk —
    rewrote to the actual folder sets (numbered spine convention for 0008;
    custom folders for 0002; pre-litigation folders for 0014).
  - Eden (0003): created missing top-level folders (Email Correspondence,
    Legal Research, Trial and Hearing Documents) and updated the Pleadings
    sub-case table to the reorganized two-level layout (active 2025-CAB-005811
    + "Pleadings (Prior cases)"). NOTE: overview/digest still call
    2026-LTB-002283 "active" but its folder says "settled" — James to confirm.
  - Palma (0020): created the 4 missing standard subfolders; Novel Vouchers
    (0013): created Hot Documents + Related Cases.
- **New case scaffolds** (standard six-folder template + mirror CLAUDE.md +
  master_file_index.json + activity.jsonl; seeds moved to Raw Documents with
  organized copies filed): Dannucci (0026, slip-and-fall, Bridge Mgmt, empty),
  Fields (0027, Fields v. Humphrey Mgmt late-fee class action, Howard County),
  Ismael (0028, Ismael v. DC First & M Owner + Bozzuto, 2026-SCB-000434,
  motion hearing 9/11/2026). Seeds pre-indexed so tonight's run won't re-file.
- **BUG FOUND + FIXED: case-index skeleton rows shadowed real cases.**
  Pre-numbered rows (RRID with all other cells blank) on the "Closed" sheet
  load as Closed and, because callers key by RRID last-row-wins, shadowed the
  Sheet1 rows — RRID-0024+ (incl. all new cases) were treated CLOSED and
  skipped by daily-run/digest/daily-cases. Fixed both ends: `load_case_index`
  now skips skeleton rows (rocky.py), and the empty skeletons were removed
  from the Closed sheet of Rocky Case Index.xlsx. **Needs `python
  build_exe.py` to deploy the code fix**; the spreadsheet fix works today.
- Mt. St. Joseph (0029) appeared mid-session; scaffold was built then fully
  reverted — James is writing a different CLAUDE.md for it separately.

**Open items**

- Rebuild + deploy rocky.exe (index-loader fix).
- James: confirm Eden 2026-LTB-002283 settled → refresh Eden's overview/digest
  sections (stale May/June 2026 deadlines).
- James: Mt. St. Joseph CLAUDE.md (his own version).
- Fields (0027): case number/filed date TBD from docket; Ismael (0028):
  obtain the original 3/13/2026 Statement of Claim (becomes PLD-001; PLD
  numbering deferred until docket reconciled).

**Watch-outs**

- James edits Rocky Case Index.xlsx live in Excel (file was locked during the
  session; his edits landed mid-session). Any script touching it should
  copy-edit-copy-back and re-read immediately before writing.
- Rocky's daily run only offers TOP-LEVEL subfolders as filing targets —
  nested targets (Eden's pleadings sub-cases) rely on the CLAUDE.md prose and
  a project session, not the daily run.

## Session 2026-08-26 — Affidavit format: submission time via API + VAWA abbreviation

- **Submission time on the affidavit** (Garo exemplar: "That on
  7/22/2026 at 4:27 p.m., ..."). James first said pull it from the
  proof, then corrected mid-build: **through the API**. Source order:
  (1) Rocky-released jobs already carry the communicated-to-LetterStream
  stamp (unchanged, takes precedence, no lookup); (2) website-submitted
  mailings: extraction now pulls the LetterStream job number off the
  proof cover ("14102628.1.1fc-21" -> 14102628) and process_mailing
  calls `jobstatus` — `letterstream.earliest_datetime_from` scans the
  response for timestamps (both ISO and M/D/YYYY h:mm am forms) and
  takes the EARLIEST as the submission stamp; (3) no job number / no
  configured API / lookup fails -> affidavit shows the date alone
  (never a guessed time).
- **CALIBRATION CAVEAT:** the jobstatus response shape is undocumented
  and unverified live — first real ingest logs it raw
  (letterstream_raw.jsonl); if its earliest timestamp turns out not to
  be the submission stamp, adjust earliest_datetime_from. Hailey
  reviews every affidavit meanwhile.
- **VAWA:** "Violence Against Women Act" is abbreviated "VAWA" in the
  documents-mailed clause — prompt example/instruction updated plus a
  deterministic post-replace so it holds even if Claude spells it out.
- Tests: timestamp parsing (both forms, earliest wins), jobstatus
  lookup wiring, VAWA replace, rendered affidavit matches the Garo
  exemplar sentence, release-stamp precedence (no lookup on API jobs).

## Session 2026-08-24 — The Monitor: fast loop for letterstream + vault mail

- **NEW: `--monitor [--once]`** (24/7 at boot, like --monitor-remy but
  SEPARATE from it — James weighed folding it in; keeping the Remy
  loop untouched preserves its isolation). Every
  `monitor_interval_minutes` (default 10) it runs `monitor_commands`
  (default --letterstream, --vault-mail, --vault-inbox) as
  SUBPROCESSES of itself — each keeps its own instance lock, cursors,
  and failure policy; a held lock exits 0 quietly so overlap with
  scheduled runs is safe; 15-min timeout per subprocess; ROCKY STOP
  (kill_switch.is_dormant) pauses cycles. Frozen mode spawns
  sys.executable (the exe re-extracts per spawn, seconds — fine at
  this cadence). --once = single cycle for testing.
- Config-extensible: adding e.g. "--litigation" to monitor_commands
  folds another sweep in with no rebuild. Heavier jobs stay scheduled
  (vault-dropbox hourly, digests, litigation --poll 10:00 unless
  folded in).
- Rocky-laptop setup: Task Scheduler at-boot entry `rocky.exe
  --monitor`; DISABLE the 8:00 AM letterstream entry and the hourly
  vault-mail/vault-inbox schedules (redundant under the monitor).
- Tested (mocked subprocesses): spawn order/timeout, dormant skip,
  failing subprocess doesn't kill the cycle.

## Session 2026-08-23 (3) — Rename: --affidavits → --letterstream

- **James: rename the process "letterstream"** (it also sends mailings
  without affidavits). CLI is now `--letterstream`; `--affidavits`
  stays as a working legacy alias (pma-activity pattern: both normalize
  to one "letterstream" instance lock, existing Task Scheduler entries
  keep working). Docs: MAILING_AFFIDAVITS.md renamed **LETTERSTREAM.md**
  (all references updated), BUILD_REFERENCE paragraph retitled, config
  comments updated. UNCHANGED on purpose: module filenames
  (mailing_affidavits.py — like pma_tracker.py after its rename),
  affidavit_* config keys, the "Mailing Affidavits" share folder name,
  and the [AM]/[CM] tags.
- **Unified-monitor question:** recommended (not yet built) a thin
  `--monitor` orchestrator that runs the existing sweeps in sequence
  (letterstream sweep + litigation --poll + vault inbox sources) every
  15-30 min for latency — keeping per-process cursors/locks/failure
  isolation (load-bearing per BUILD_REFERENCE error-handling
  principle). Remy's 5-min loop and the daily Dropbox/index vault run
  stay separate. Awaiting James's go-ahead.

## Session 2026-08-23 (2) — Multifamily Digest subsumes the Vault Digest

- **NEW: `multifamily_digest.py` + `--multifamily-digest [--hours N]
  [--dry-run]`** (5:30 PM daily — takes the Vault Digest's dashboard
  slot). ONE email from rocky@ with four sections (empty ones omitted):
  Certified mail (preauth'd/released with cost+approver/mailed with
  tracking/declined/failures), Affidavits (proposed/filed with vault
  paths/declined/proof-of-mailing submissions), The Vault (the day's
  additions — same content as the standalone digest, via the new
  shared `vault.digest_body_html` fragment), and Still Pending
  (mailings awaiting release, in flight, affidavits awaiting
  approval). Quiet window across the first three sections = no email.
  Recipients: `multifamily_digest_recipients` → falls back to
  `vault_digest_recipients` → James.
- **Vault Digest superseded:** `--vault-digest` still works for manual
  use but left the dashboard (its 17:30 slot now runs
  `--multifamily-digest`); help/config marked. vault.py refactor:
  `_build_digest_html` split so `digest_body_html(filed, review)` is
  reusable.
- Tested: all sections render from seeded activity/state (labels
  resolved from CM state queues), quiet window returns None.

## Session 2026-08-23 — Affidavit ask in the release reply + mailing-date policy

- **Multi-user confirmed (no code change):** any firm sender can email
  a certified-mail request, and the REQUESTER is the [CM] release
  approver (requester or James only) — already how it was built.
- **Affidavit ask:** the [CM-####] release email now asks whether to
  prepare the Certified Mailing Affidavit in the same reply — plain YES
  / "Yes, with affidavit" queues it; "Yes, no affidavit" (negation
  within two words of affidavit/certificate) releases without one;
  ambiguous → WITH (safe: Hailey still gates the affidavit itself).
  `want_affidavit` stored at release; no-affidavit jobs are tracked to
  done with no proof pull ([mail_mailed with affidavit_tag null]).
- **Mailing-date policy (James):** the affidavit swears to the date AND
  time the mailing was communicated to LetterStream = the [CM] release
  moment (`communicated_date`/`communicated_time`, laptop-local),
  NOT USPS acceptance. The release confirmation email states the exact
  date/time that will appear. Website-submitted mailings via the
  proof-of-mailing channel keep using the notice's own date (Rocky
  can't see their submission time; Hailey reviews).
- Tests: opt-out parsing (10 cases), release records policy fields,
  no-affidavit completion path, affidavit uses communicated date/time.

## Session 2026-08-17 (2) — Job naming convention + expedite hook

- **Naming convention (James):** uploads + LetterStream job names now
  read "LastName Matter#" (`Brathwaite 1234.001.pdf`; job gets a
  6-char unique suffix since job names must be unique account-wide).
  `extract_mail_request` gains last_name + matter_number (stated in
  the request email body; never invented); missing matter -> approval
  email says "NOT GIVEN — reply NO and resend with it". Outbound copy:
  `CM-#### LastName Matter#.pdf`.
- **Expedite (James wants it default):** no expedite field in the Feb
  2023 API doc — support question OPEN ("what's the API argument for
  the website's expedite option?"). Plumbing ready:
  `letterstream_extra_fields` (config dict) merges into every
  submission POST; when support answers, e.g. `{"expedite": "1"}` in
  config makes it the default with no rebuild.
- Mocked tests: filename/job actually sent to LetterStream, extra
  fields passthrough, no-matter fallback (`Donadio.pdf`), approval
  email renders. Rebuilt/redeployed.

## Session 2026-08-17 — Outbound validated LIVE end to end (through release)

- Prepay funded ($100). Live test with real API calls, all clean:
  Hailey emailed rocky@ ("certified mail" + PDF) → [CM-0001] "Parking
  License Revocation - Mondragon" preauth'd at $11.01 (live response
  parsed fine) → her YES released it (doauth OK, confirmation sent) →
  in-flight poll correctly holds it ("not mailed yet", status unknown
  right after release). AM decline path also validated live (James NO'd
  the old [AM-0001] Donadio ingest test → Declined\ + flag email). The
  unclear-reply nudge fired once and worked.
- Remaining to observe (no action needed): USPS acceptance in ~1-2 days
  → run flips to mailed:1 → proof pulled by doc_id → [AM-0002] to
  Hailey → YES → Vault. If tracking still shows 'unknown' after
  production (~2 days), check letterstream_raw.jsonl — by-doc_id trackx
  shape may need a tweak.
- Next: daily 8:00 AM Task Scheduler entry for `rocky.exe --affidavits`
  (James; copy the Vault task). affidavit_root on the Rocky laptop still
  derives to rocky@'s own OneDrive — set explicitly if the team should
  see Pending/Approved/Outbound (see 2026-08-16 (2) watch-out).

## Session 2026-08-16 (4) — Outbound: Rocky submits the certified mail (preauth → CM YES → track → affidavit)

**What changed**

- **James's call: build the fully-automatic path** — Rocky submits
  certified mailings through the LetterStream API (API-submitted jobs
  DO have queryable proofs/tracking, unlike website ones).
- `letterstream.py`: `submit_single` (method-2 POST, always
  `preauth=1` — LetterStream returns cost + authcode, nothing bills),
  `authorize` (doauth — THE billing step), `_parse_submission`
  (-100/-200 info vs error messages, docs list), `format_recipient` /
  `format_sender` (colon-delimited address strings, delimiter chars
  scrubbed).
- `mailing_affidavits.py` outbound half: request channel = firm sender
  emails rocky@ with "certified mail" (`mail_request_keyword`) +
  exactly ONE PDF (the packet, in mailing order; body overrides the
  document's addressee); `extract_mail_request` (Claude) → validate →
  preauth → `[CM-####]` release email to the REQUESTER quoting exact
  recipient/pages/cost. Only requester or James can YES (releases via
  doauth); NO cancels (unreleased job = $0). `poll_in_flight` tracks
  released jobs each run; status containing mailed/delivered → proof
  via `getinfo=proof` by doc_id → the normal affidavit pipeline
  (tracking number + mail date wired from trackx). CLI `--mail <pdf>`.
- **Safety rails:** preauth-always (human YES is the only billing
  trigger); `mail_max_cost` cap (default $50) refuses runaway quotes;
  same-doc-same-recipient dedup (email channel; deliberate re-mail via
  --mail); firm senders only; doauth failure keeps the job pending and
  notifies James; reminders cover CM queue too.
- Offline-tested with mocked LetterStream: preauth, duplicate refusal
  (no API call), cost cap, release, doauth failure held, mailed→
  affidavit handoff (tracking + mail date), unmailed held. Docs:
  MAILING_AFFIDAVITS.md "Outbound" section, config keys
  (mail_request_keyword, mail_max_cost, mail_from,
  letterstream_mailtype, letterstream_coversheet), rocky.py help.

**Open items — live validation (costs one real stamp)**

- Fund check: LetterStream prepay balance (`--probe` shows it).
- End-to-end test: email rocky@ ("certified mail" + a 1-2 page PDF)
  addressed TO THE FIRM's own office → YES the [CM] → wait for USPS
  acceptance (1-2 days) → confirm proof pull + affidavit + vault.
- Response-shape risk: submission/doauth JSON shapes were built from
  the API doc's XML examples — first live preauth may need a parse
  tweak (raw responses land in letterstream_raw.jsonl as always).

## Session 2026-08-16 (3) — Email is the discovery channel; LetterStream confirms API limits

**What changed**

- **LetterStream support confirmed:** the API cannot retrieve proofs for
  website-submitted jobs (-999 explained) and has no list call for them
  — "must be located through My Jobs." So automatic API discovery is
  off the table while the firm mails via the website.
- **NEW: proof-of-mailing email channel** (the Vault-submission
  pattern): any FIRM sender emails rocky@ with "proof of mailing" in
  the subject (`affidavit_subject_keyword`) + the proof PDF(s) from the
  LetterStream job page attached. The same inbox sweep that reads
  approval replies turns each PDF into an affidavit sent for approval
  (SHA-256 dedup; non-PDF/non-firm ignored) and replies to the
  submitter with the [AM-####] tags. `process_mailing` now returns the
  tag; `poll_approvals` takes the Anthropic client and counts
  `submitted`. This is Hailey's whole workflow: mail → download proof →
  email rocky@ → reply YES.
- Docs updated (module docstring, MAILING_AFFIDAVITS.md "Discover"
  step, config comment + `affidavit_subject_keyword`).

**Decisions made**

- Full automation (no human discovery step) would mean the firm
  SUBMITTING certified mail through the API (API-submitted jobs do get
  ids + proofs). Workflow/billing decision for James + Hailey — parked,
  not built.

## Session 2026-08-16 (2) — Affidavit rendered from James's .docx template

**What changed**

- James reformatted the generated Donadio affidavit (signature blocks in
  a borderless 4-col table with bottom-border signature lines, single
  spacing with blank-line separators, 1.25" side margins) and asked that
  his layout govern. Instead of re-hand-coding it, **his document IS now
  the template**: tokenized to `_templates/affidavit_template.docx`
  ({{TENANT}}, {{ADDRESS_LINE1}}/{{ADDRESS_LINE2}} (split city/ST/zip),
  {{ADDRESS_FULL}}, {{WHEN_MAILED}}, {{DOCUMENTS_MAILED}}, {{AFFIANT}},
  {{AFFIANT_TITLE}}, {{SIGN_DATE}}; the /s/ run keeps Edwardian Script).
- `build_affidavit_docx` rewritten to render from the template. Live
  copy on the share: `<affidavit_root>\_affidavits\template.docx`,
  auto-seeded from the bundled default — James edits formatting there
  with NO rebuild. Token split across runs by Word edits handled
  (paragraph-level fallback). Bundled via build_exe --add-data.
- Verified: template render of the Donadio fields matches James's
  document EXACTLY (text + table + script font + margins); Brathwaite
  render exercises the time clause + address split. Rebuilt/redeployed.

**Watch-out**

- On the Rocky laptop `--status` showed the derived affidavit root
  `C:\Users\rocky\OneDrive - gejlaw.com\Mailing Affidavits` — that's
  rocky@'s OWN OneDrive, not James's shared folders. If the team should
  see Pending/Approved/template, set `affidavit_root` explicitly to a
  location under the James-share (like vault_root/litigation_root are).

## Session 2026-08-16 — LetterStream live testing: auth + trackx work; proof call can't serve web-submitted jobs

**What changed**

- Live tests from the Rocky laptop with real credentials: **auth passes**
  and `trackx` returns full USPS tracking data by certified number
  (shape confirmed: message.item.detail = list of "EVENT YYYY-MM-DD
  HH:MM:SS CITY,ST, ZIP" strings; times are facility-local).
- **`mail_date_from_trackx`** (letterstream.py) + fetch wiring: the
  affidavit's mailing date now comes from the earliest USPS event date
  (verified 2026-05-29 for Brathwaite) instead of the notice date.
  Rebuilt/redeployed.
- **FINDING: `getinfo=proof` returns -999 "could not locate proof
  (job-piece)" for the firm's mailings** — tested against a 5/29 mailing
  AND a fresh 8/14 mailing (so not retention). The firm submits via the
  LetterStream website; the API's stored "proof" apparently exists only
  for API-submitted jobs, and the dashboard's "Proof of Mailing" PDF is
  not exposed by this call. Doc-id lookups (-924 invalid) confirmed
  internal piece ids aren't queryable; the USPS certified tracking
  number is the only working handle.

**Open items**

- James emailing LetterStream support (API ID y4s3vk19): how to retrieve
  the Proof of Mailing PDF for website-submitted jobs via API, and
  whether a job-list call exists. Depending on the answer, the long-term
  fully-automatic path may be submitting certified mail THROUGH the API
  (API-submitted jobs get doc ids + proofs + the -100 response data).
- Interim workflow is unaffected: `--affidavits --ingest <proof.pdf>`
  (hand-downloaded from the dashboard) runs the identical pipeline —
  finish the end-to-end approval/vault test with it.

## Session 2026-08-06 (2) — LetterStream client calibrated from the real API docs

**What changed**

- James obtained API credentials and the real API documentation ("Mail
  Fulfillment by LetterStream — Integration API", Feb 3 2023 PDF).
  First --probe returned IDOK (id recognized, hash wrong), as expected.
- **`letterstream.py` rewritten to the documented API:** auth is
  `h = md5(base64(last6(t) + API_KEY + first6(t)))` with `t` a unique
  numeric id accepted ONLY ONCE ever (ms timestamp + monotonic bump);
  base URL www.letterstream.com/apis/index.php; calls implemented:
  accountstatus (probe), jobstatus/docstatus/batchstatus (known ids),
  trackx (JSON tracking), and getinfo=proof by cert=<tracking#> or
  doc_id (handles raw-%PDF and base64 streams per their sample code).
- **KEY FINDING: the documented API has NO job-enumeration call** — it
  only answers about ids you already know. So: NEW
  `--affidavits --fetch <tracking#>` pulls one proof via the API by the
  USPS certified tracking number (from the LetterStream dashboard) and
  runs the pipeline — the day-to-day discovery path. The 8 AM pull
  stays a no-op unless LetterStream support supplies a list call, whose
  POST params drop into config `letterstream_list_params` with no code
  change. Docs/help updated (MAILING_AFFIDAVITS.md "API reality",
  config comment, rocky.py help).

**Open items**

- Ask LetterStream support whether a job-list API call exists (the
  dashboard has the data). If yes → letterstream_list_params.
- Re-run `--affidavits --probe` (expect AUTHOK + prepay balance), then
  test `--affidavits --fetch 9214890142980480892752 --dry-run` (the
  Brathwaite mailing's tracking number).
- trackx response shape unknown until first real call (logged to
  letterstream_raw.jsonl); wire its mail date into the affidavit later —
  until then the affidavit uses the notice's own date.

## Session 2026-08-06 — Mailing Affidavits: LetterStream → affidavit → Hailey's YES → Vault

**What changed**

- **NEW: `mailing_affidavits.py` + `letterstream.py` + `--affidavits`**
  (suggested 8:00 AM daily) — automates the certified-mailing affidavit
  loop: pull newly mailed jobs from the LetterStream API, download each
  proof-of-mailing PDF, Claude-extract tenant/address/property and the
  documents-mailed clause, generate the Certified Mailing Affidavit
  .docx (conformed /s/ + prep date, matching the Brathwaite NTPR
  exemplar James provided), email affidavit + proof from rocky@ to
  Hailey tagged `[AM-####]`. YES reply → both files vaulted via
  vault.py's normal filing (doc_type "other" with labels "Certified
  Mailing Affidavit" / "Proof of Mailing", confidence 1.0, catalog
  records approver + tag); NO → `Declined\` + James emailed the note;
  unclear → "reply YES or NO" nudge; reminders every 3 days. Approvals
  poll BEFORE the pull so an API outage never blocks filing. PDF
  conversion of the approved affidavit via Word COM when available,
  .docx fallback.
- Wiring: rocky.py dispatch/help/docstring, config.example.json
  (`_affidavit_comment` block: letterstream keys + affidavit_* keys),
  build_exe.py bundles the two new modules. New guide
  `MAILING_AFFIDAVITS.md`; BUILD_REFERENCE.md capability paragraph.

**Decisions made**

- **LetterStream API is account-gated** (docs + credentials unlock only
  after emailing support@letterstream.com for activation, "Automation"
  mode). Every vendor-specific detail (auth hash recipe, request
  params, response key names) is isolated in `letterstream.py` with
  `--probe` + `letterstream_raw.jsonl` for a one-session calibration
  once credentials arrive; list/proof params are also config-overridable
  without a rebuild. The client is read-only against LetterStream.
- `--affidavits --ingest <proof.pdf>` runs the identical pipeline on a
  manually downloaded proof — the bridge until API activation and the
  test harness.
- The /s/ signature + date go on BEFORE Hailey sees it, so her YES
  approves the exact bytes that get filed (nothing altered
  post-approval); the email says her reply is the authorization record.
- Approval decisions parse first-word-with-boundary only ("Now that I
  look..." never reads as "no"); only `affidavit_approver` or James can
  decide; unmatched replies get a nudge, not a guess.
- Affidavit signature date = preparation date (exemplar shows prep date
  7/30 vs mail date 5/29, so a later-than-mailing date is the norm).

**Open items**

- James: email support@letterstream.com to request API access; paste
  `letterstream_api_id`/`letterstream_api_key` into config; run
  `--affidavits --probe` and calibrate letterstream.py against the
  in-account docs (30-min session).
- Config on the Rocky laptop: `affidavit_approver` = Hailey's address.
- Task Scheduler: daily 8:00 AM `rocky.exe --affidavits`.
- Not added to the dashboard (James: "will not necessarily need to be
  on the dashboard") — add a registry entry later if wanted.

**Watch-outs**

- Extraction validated live against the real Brathwaite proof PDF
  (claude-sonnet-4-5, confidence 0.98 — output matched Hailey's
  hand-drafted affidavit essentially verbatim, including the certified
  article number). Generation validated against the exemplar text.
- Word must be installed on the Rocky laptop for the vaulted affidavit
  to be a PDF; otherwise the .docx is filed (both fine for the Vault).

## Session 2026-08-05 (2) — Litigation voices: seeds, summary voice, voice-aware cleanup

**What changed**

- **Voice seeds** (`litigation_updater.py`) — new
  `_litigation\voices\seeds\<updates|summary|closure|disclosure>\`
  folders on the share (auto-created by `ensure_dirs`). Documents
  dropped there (.docx/.pdf/.txt/.md/.html, first 5 per voice) are
  extracted and folded into that voice's `--voice-rebuild` as
  gold-standard references, weighted above the sheet samples — so
  hand-picked exemplars survive every rebuild.
- **NEW summary voice** — `voice_summary.md`, learned from the internal
  claim-summary column (`litigation_summary_column`, default "Summary
  of Claim") across open + closed sheets plus its seeds. `draft_entry`
  now passes it as the voice guide for that column; `--status` lists all
  four voices with seed counts.
- **Voice-aware cleanup** — `run_cleanup`'s compliance pass now also
  reviews the summary + disclosure columns against their voice guides
  and may propose rewording-only rewrites (prompt forbids changing any
  fact/date/figure). Suggestions arrive as the usual one-at-a-time
  `[L####]` Teams asks; the existing dedup still blocks re-proposing
  declined fixes.
- **Seeded from James's two exemplar docs** (copied to the share):
  "BMC Litigation Report - 7-1-21.docx" → `seeds\summary\` (internal
  report voice) and "BMC Litigation Disclosure - 6-19-26.docx" →
  `seeds\disclosure\`. Hand-built `voice_summary.md` and appended a
  seeded-exemplar section to `voice_disclosure.md` on the share so
  drafting improves immediately, before the next rebuild.
- Docs/config: `LITIGATION_UPDATER.md` (voices section, command table),
  `config.example.json` (`litigation_summary_column` + comment).

- **Mail filing after YES** — when an approved ask actually writes
  (`ask["applied"]` set by the new_entry/update/closure paths), the
  source mail is moved from rocky@'s inbox to the "Litigation Updater"
  subfolder (`litigation_processed_folder`, `""` disables;
  `litigation_mail_move_via` "delegated" default = Mail.ReadWrite.Shared
  from rocky@'s cached sign-in, consented 2026-07-05 — no new
  permissions). Best-effort: failures log `mail_filed`-less warnings and
  never affect the ask. Runs post-execution because a Graph move changes
  the message id used for attachment re-fetch. Folder id cached in
  state (`mail_folders`), stale-id 404 retried once.
- **Cleanup truncation fix** — voice review made chunk responses long;
  chunk size 8 (was 20) when voice guides are loaded, review max_tokens
  8000 (was 4000), `_claude_text` now logs when a response hits the cap.

**Decisions made**

- Key voice distinction the two docs teach: the internal summary voice
  is candid, first-person-plural ("We believe the claim meritless",
  retention/settlement figures included); the disclosure voice is
  third-person-only "BMC", set merit phrases, no dollar figures, no
  strategy.
- Voice-alignment suggestions flow through the Teams chat (cleanup
  asks), not a new mechanism — dev laptop has no Smartsheet token, and
  one-at-a-time YES/NO is the designed approval path.

**Open items**

- On the Rocky laptop, after pulling: `--litigation --voice-rebuild`
  (rebuilds all four voices with seeds), then `--litigation --cleanup`
  (queues voice-alignment proposals into the Teams chat; consider
  `--limit` for the first run).

## Session 2026-08-05 — Remy digest becomes a Rocky command

**What changed**

- **NEW: `remy_digest.py` + `--remy-digest`** — the whole Remy daily-digest
  job now lives in Rocky. Previously it was split: a Claude Code scheduled
  task on James's *dev laptop* generated and pushed `digest/YYYY-MM-DD.md`
  at 5:00 PM, and Rocky (via pasted plain-English instructions, not code)
  fetched it at 5:30 PM and emailed it. Rocky now does both halves.
- Flow: list `digest/*.md` on GitHub → newest date is the low-water mark
  (none = 7-day cold start) → list commits since → drop commits touching
  only `digest/` → collect the `digest/sessions/*.md` notes those commits
  carried → Claude writes the digest → PUT it back via the contents API →
  email from rocky@ through `send_mail_guarded`.
- **All GitHub REST, no clone and no git binary.** The Rocky laptop has
  neither a REMY clone nor git credentials, and James's REMY working tree
  usually holds uncommitted work — nothing here goes near it.
- `rocky.py` — docstring, dispatch, help text. `dashboard.py` — registry
  entry (Other group, 17:30, recommended, dry-run capable).
  `build_exe.py` — bundles `remy_digest.py`. `config.example.json` —
  `remy_github_token` / `_repo` / `_branch`, `remy_digest_recipients`,
  `remy_digest_mailbox`.
- Offline suite (GitHub + Claude stubbed, 7 scenarios): already-exists
  short-circuit, dry run, full generate/commit/email, no-duplicate-email,
  quiet day, read-only-token degradation, HTML escaping. 7/7 PASS.

**Decisions made**

- **Rocky still commits the digest back to the repo** rather than only
  emailing it. The archive under `digest/` is how James and Shane read
  past digests without email, and a file already present is the natural
  "did today already run?" check — cheaper and more honest than local
  state. Local state (`state/remy_digest.json`) only guards the *email*,
  for the case where the push failed.
- **A read-only token degrades instead of failing.** If the PAT can't
  write, Rocky logs a loud warning, skips the commit, and still sends the
  email — a missing archive entry shouldn't cost James the day's digest.
  Local copy always lands in `<data_dir>\remy_digests\` first.
- **Privacy is enforced in the system prompt, not by filtering.** Session
  notes are supposed to be scrubbed already (the rule is in REMY's
  CLAUDE.md), but they're written by a different agent, so the digest
  prompt independently forbids client/tenant names, addresses, ledger
  amounts, and document contents. Belt and suspenders.
- Session notes are collected from *every* in-range commit including
  digest-only ones — a note can be amended after the code commit lands —
  then re-fetched at branch HEAD so the latest text wins.

**Open items**

- **Not activated.** Needs, on the Rocky laptop's `config.json`:
  (1) `remy_github_token` — fine-grained PAT, repo `jbragdon21/remy`,
  **Contents: Read and write** (the old ROCKY.md said read-only; write is
  what lets Rocky commit), (2) Shane's address in
  `remy_digest_recipients` — it has never been recorded anywhere, and
  Rocky warns when fewer than two recipients are configured.
- Rebuild + deploy `rocky.exe`, add the 5:30 PM weekday Task Scheduler
  entry (or click Setup on the dashboard).
- **Then retire the old path:** delete the `remy-daily-digest` Claude Code
  scheduled task on the dev laptop, and update `digest/README.md` +
  `digest/ROCKY.md` in the REMY repo, which still describe the two-piece
  arrangement. Left in place deliberately until Rocky's version runs
  clean — otherwise there'd be a window with no digest at all.

**Watch-outs**

- Both jobs will double-run if the old scheduled task isn't removed after
  activation. The damage is limited (whoever writes first wins; the second
  sees the file and exits `already_exists`, and the email guard is
  per-day), but the digest could be written by whichever fires first.
- `--dry-run` implies no email and skips the Claude call entirely, so it
  costs nothing and needs no Graph token — but it *does* need the GitHub
  token, since it reads real repo history.
- The REMY repo's `CLAUDE.md` requires a session note per code-changing
  commit. That rule is what makes this digest readable — if notes stop
  appearing, digests silently degrade to commit-subject paraphrase.

---

## Session 2026-08-02 (2) — inbox-matt: internal sweep split into subgroups

**What changed**

- **C0003 (51,655 internal → Office Misc.) WITHDRAWN per Matt's concern**
  (a deal email with a colleague could hide in it — e.g. Mike Henigan
  mail about a deal never named in the subject). Removed from
  cohorts.json; Rocky posted a withdrawal explanation to the Teams chat
  (sent from dev via Graph; appended to communications.jsonl manually).
  Matt had NOT replied to the proposal.
- **Internal sweep now drafts SUBGROUPS instead of one blob** (all →
  Office Misc. Archive, all still per-batch approval): `internal_ops`
  (functional mailboxes — billing/AP/admin/switchboard/no-reply...),
  `internal_broadcast` (8+ visible To/Cc recipients), `internal_person`
  (one batch per colleague ≥150 msgs, localpart merges gejlaw/gallagher
  domains, sample subjects included in proposal + report),
  `internal_misc` (small-volume senders). Tunables:
  internal_person_min, broadcast_min_recipients. Match rules carry
  min/max_recipients, honored in _cohort_message_ids.
- **Chat proposal ORDER is now curated** (_PROPOSE_PRIORITY): ops →
  broadcasts → newsletters → court → routes → matters → per-person →
  misc. Most-obviously-non-work first, per James; per-person internal
  mail (the mislabel risk) proposes last.
- **NEW: --inbox-<user> --internal-report** — emails the owner +
  observers a detailed plain-English breakdown of the internal subgroups
  (counts, top senders, sample subjects) BEFORE anything is proposed;
  deterministic, saved to share as internal_report.md. James runs it on
  the laptop after --analyze.
- Synthetic suite recreated + extended: 14/14 PASS (subgroups, order,
  recipient-bound claims, no overlap).
- **Laptop sequence:** disable chat task → new exe → --analyze
  (regenerates internal subgroups; C0003 gone) → --internal-report →
  re-enable chat task.

## Session 2026-08-02 — Litigation Updater (Bozzuto claims Smartsheet)

**What changed**

- **Litigation Updater built** — `litigation_updater.py` + `rocky.py
  --litigation --poll|--chat|--digest|--report <entity>|--cleanup|
  --learn|--voice-rebuild|--status`. Bozzuto claims tracking
  (BMC/B&A/BHI/BCC) on Smartsheet: watches rocky@'s inbox for
  legalnotices@bozzuto.com mail and add/update/move-to-closed forwards,
  Claude-classifies the documents, and proposes every sheet change
  one-at-a-time ([L####]) over a persistent "Litigation Updates" Teams
  group chat — YES executes exactly the stored effects. Closure = row
  snapshot logged, add to closed sheet with a voice-drafted closure
  note, delete from open. Voices (updates/closure/disclosure — the
  disclosure voice learns from past audit reports), brain.md (weekly
  --learn over the chat log, additions surfaced in the next digest),
  daily digest drafted into James's Drafts, strict-Jinja entity audit
  reports (placeholder template auto-created), one-time --cleanup
  compliance review (conventions.md, one-by-one fixes, missing_info.md),
  and the Litigation Update Vault (per-claim key documents; chat "do you
  have the X in the Y case" -> Rocky emails it to James).
- **Master sheet reviewed** (same session, "BMC Litigation Report -
  MASTER (9).xlsx", 100 rows x 20 cols) and the real schema baked into
  config.example.json: claim identity = "Combined Claimant (Project)"
  (new litigation_claim_column key, used for correlation/vault/chat;
  falls back to the sheet's primary column), disclosure column is
  "Third Party Disclosure Summary" (98/100 filled — draft_entry now
  passes the disclosure voice for it), update columns are
  "Status/Action Items" + "Overall Status", new
  litigation_correlate_columns key (Entity / Property Name (State) /
  Type of Case / Case No.), entity match lists handle the sheet's
  variants ("B&A d/b/a The Bozzuto Group", "Bozzuto Group"), and the
  classifier prompt teaches the "Claimant (Property)" naming
  convention.
- **Closed sheet reviewed too** ("Closed Claims - MASTER (2).xlsx",
  321 rows): closure column is "Closure Notes" (new default; many
  legacy notes are just "Closed"/"Settled" — the voice builder samples
  only the longer narrative ones), plus a "Date Closed" column Rocky
  now auto-sets to the move date (litigation_date_closed_column).
  Three column titles DRIFTED between the sheets (Date of Loss/Filing
  vs 'Date of Loss / Filing'; Property Name (State) vs Property;
  Combined Claimant (Project) vs Combined Claimant/Project) — new
  litigation_column_map config translates titles on the move so those
  values aren't dropped; unmapped/unmatched titles fall back to the
  logged snapshot. Closed-sheet Entity values are messier still (BDC,
  BCC/BAA, multi-entity rows) — reports match on substring lists, and
  the odd ones are cleanup-pass material. Smoke test extended to 53
  checks (column map + Date Closed covered).
- **Third sheet reviewed -> MULTI-SHEET refactor** ("BCC_BDC Litigation
  Report - MASTER.xlsx", 11 rows — the open sheet for the other
  Bozzuto entities; BCC + one "BBC" typo row). Replaced the single
  litigation_open_sheet_id with a litigation_open_sheets LIST (legacy
  key still works): each spec = {key, sheet_id, entities, claim_column,
  column_map}. New entries route by classified entity to their home
  sheet (BMC/B&A -> BMC master; BCC/BDC/BHI -> BCC_BDC master;
  first sheet = fallback); correlation lists ALL open sheets and
  locates the matched row's sheet; asks carry sheet_key so closures/
  updates/cleanup fixes execute against the right sheet; reports filter
  across every open sheet (mis-filed rows still surface) with a
  first-seen column-title union; cleanup and voices iterate all sheets
  (per-sheet conventions memos). BCC/BDC sheet quirks: "Project"
  (not "Property Name (State)") -> per-sheet column_map {"Project":
  "Property"} for closures; claim column "Combined Claimant/Project"
  (only 3/11 filled — cleanup material); "Claimant" and "Project"
  added to litigation_correlate_columns so sparse claim cells don't
  starve correlation. BDC added to litigation_entities. Smoke test now
  64 checks (spec helpers, entity routing, BCC report isolation,
  BCC-sheet closure with mapped columns) — all pass.
- Wiring: rocky.py dispatch + docstring/help (all --litigation* flags
  share one instance lock), dashboard registry group "Litigation"
  (poll 10:00 / digest 18:30 / learn button; --litigation-digest and
  --litigation-learn are dashboard aliases), litigation_* keys in
  config.example.json, module bundled in build_exe.py. New
  LITIGATION_UPDATER.md; BUILD_REFERENCE.md section added.

- **Go-live afternoon (same session):** exes built + deployed (rocky
  48MB / dashboard 16MB; dashboard was running from dist\ and had to be
  stopped for the rebuild). First live run hit a Smartsheet 401 (bad
  token paste) which ALSO exposed an unhandled-traceback path — run_cli
  now catches SmartsheetError and exits with a clean config-hint
  message (rebuilt/redeployed). Fresh token worked; voices built from
  the live sheets. **--chat --follow [--minutes N] added** (James asked
  for immediate next-proposal after a YES): a live session polling the
  chat every 15s, exiting at the minute cap (default 30) or once the
  ask queue drains and the chat goes ~2 min quiet; idle cycles cost no
  Claude calls. Smoke test now 66 checks.
- **Revision loop added** (James: "can I give it directions on what to
  change, then have it come back with a final confirmation"): a
  non-yes/no reply to a pending ask routes through a Claude classifier
  (approve/decline/revise/unrelated); "revise" redrafts the STORED
  content (closure note / update cells / cleanup value; new-entry asks
  store the instruction and honor it at draft time), re-proposes, and
  only a clean YES executes. Guard fixed in the same pass: "yes but
  shorten it" previously matched the YES regex and would have applied
  the proposal UNCHANGED — a conditional yes/no (_COND_RE) now always
  routes to the revision path. Approve/decline extracted to shared
  helpers. Smoke test 74 checks; rebuilt + redeployed.
- **Today's-date bug caught live** (cleanup proposed "fixing" a
  2026-04-16 Date of Loss to 2024 because "2026 is in the future" —
  the model had no current date): _preamble now states today's date,
  which flows into EVERY classification/drafting/cleanup call. Also
  added cleanup re-run dedup on (row_id, column, proposed value) so
  declined fixes are never re-proposed and pending ones never
  double-queue — James should re-run --cleanup after the exe syncs to
  re-review with date awareness (dedup absorbs the overlap). 76 checks.
- **--strip-formatting [--apply] added** (James: remove all bolding/
  highlighting and keep it out of future additions): clears row, cell,
  AND column-default formats on all three sheets — dry-run by default.
  Values kept; formula cells re-sent as formulas (never flattened to
  values); cells with inbound cell-links skipped (a value write severs
  the link). Column defaults are the piece that stops Smartsheet
  auto-filling formats onto future rows; Rocky's own writes never
  carried formatting. 82 checks; rebuilt + redeployed.

**Decisions made**

- Everything that writes to the Smartsheet goes through the Teams
  YES/NO loop — including cleanup fixes. New-entry asks draft the full
  row AFTER the YES (per James's spec; the ack lists every cell
  written); closure/update asks carry the drafted text IN the proposal
  so James approves the exact words.
- "Move to closed" is snapshot -> add -> delete (Smartsheet's move-row
  API not used: the closed sheet's columns differ, and the logged
  snapshot makes every move reconstructible).
- Columns matched by TITLE via config (litigation_entity_column,
  litigation_closure_column, litigation_update_columns...) — no
  hardcoded column ids; Claude-invented column names are skipped with
  a warning.
- Correlation below 0.7 confidence never guesses — an "identify" ask
  asks which claim; the reply re-correlates and queues the real ask.
- Claude/API failure during intake HOLDS the mail cursor (vault
  pattern); Smartsheet failure during execution re-queues the ask and
  says so in chat (YES again retries).
- Teams chat is a GROUP chat (topic "Litigation Updates" — 1:1 chats
  can't carry a topic); id persisted forever (Graph creates a new group
  chat per create call). Only James's replies decide.
- Reports are pure data-through-Jinja (no Claude call) so the "strictly
  followed" format is deterministic; James replaces the placeholder
  template with the real format.
- No new Graph permissions: app-token mail reads, consented Teams
  scopes, guarded internal-only outbound, digest as a Drafts draft.

**Open items**

- James: create the Smartsheet API token + paste sheet ids/column
  titles into config; set up the legalnotices@bozzuto.com auto-forward
  to rocky@; supply the strict audit-report format (replace
  _litigation\report_template.j2); point litigation_audit_reports_dir
  at past reports; pin the "Litigation Updates" share folder.
- First live runs: --status, --voice-rebuild, --cleanup, then schedule
  poll 10:00 / digest 18:30 / learn weekly. Consider a second daily
  poll or hourly polls once trusted.
- Rebuild + deploy (`python build_exe.py`).
- v2 candidates: Adaptive Cards for asks, reminders for unanswered
  asks, richer report variables (per-claim disclosure narratives in the
  disclosure voice) once the real template arrives.

**Watch-outs**

- Offline smoke test (scratchpad test_litigation.py, 51 checks) covers
  intent detection, intake->propose->YES->row-add + vault filing,
  closure move with snapshot, identify fallback, update flow, doc
  requests, entity reports, cleanup, learn, digest, voices — all pass
  with stubbed Smartsheet/Graph/Teams/Claude. No live Smartsheet or
  Teams run yet.
- Entity matching checks the abbreviation AND the full name in both
  directions ("BMC" cell matches "Bozzuto Management Company" config
  and vice versa).
- The closed sheet needs a closure-note column (config
  litigation_closure_column, default "Closure Note") — without it the
  note survives only in the activity log (warned in chat ack).

---

## Session 2026-08-06 — Vault: Dropbox shared-link source (RAD notices)

**What changed**

- **Shared-folder links as a Vault Dropbox source** (vault.py). James's
  first concrete Dropbox need is a client-shared link (DC RAD-stamped
  notices — key inputs for Remy DC complaint drafting), not an account
  Rocky owns. Each `vault_dropbox_accounts` entry now takes
  `shared_links: [{name, url, description}]` alongside (or instead of)
  `folders`: `files/list_folder` with the `shared_link` param (manual
  subfolder walk — recursive listing isn't supported for links) +
  `sharing/get_shared_link_file` for downloads. `description` feeds the
  classifier context. `--status` shows per-link processed counts.
- Classifier hint: "notice" explicitly includes DC RAD-stamped notices.
- Refactor: folder pass and link pass share `_classify_and_file_batch`.
- config.example.json example is now a link-first account
  ("james-dropbox" + "rad-notices"); VAULT.md setup rewritten (app under
  James's own Dropbox; scopes now include `sharing.read`).
- rocky.exe rebuilt + deployed (dashboard.exe unchanged).

**Decisions made**

- Shared-link listing has no delta cursors → re-list each run and keep
  per-file processed marks (`server_modified|size`) in state under
  `dropbox_links["<account>:<link-name>"]`; marks are written only after
  a CLEAN classification batch (failure = unmarked = retry next run),
  and duplicates re-mark immediately. Marks are keyed by link *name* so
  a rotated URL doesn't re-process.
- A link-only account (no `folders` key) does NOT scan the account's own
  root — `folders` defaults to `[]` when `shared_links` present.
- The auth account for client links is James's own Dropbox: any
  authorized token can read any shared link, so one app + one
  `--dropbox-auth` covers all client links. App scopes:
  files.metadata.read, files.content.read, **sharing.read**.

**Open items**

- James: create the Dropbox app on his own account, add the RAD-notices
  link entry to config.json on the Rocky laptop, run
  `--vault --dropbox-auth james-dropbox`, then
  `--vault --source dropbox --dry-run` before going live.
- RAD notices are often scans — no-text-layer PDFs land in _Needs Review
  until the vision-extraction idea (prior entry) is built.

**Watch-outs**

- Offline suite (scratchpad test_vault.py, now 38 checks) covers the
  link pass: mark save/skip/re-mark, dedup on changed-but-identical
  files, failure holds marks, link-only account skips own-root scan.

**Live shakeout (same day, on the Rocky laptop):**

- **Stale-exe gotcha:** James runs `C:\Rocky\rocky.exe` manually — that
  local copy is NOT auto-updated (only OneDrive Program Files is). Two
  confusing runs happened on the pre-shared-link build, which scanned
  his own Dropbox root (old `folders or [""]` default). After any
  deploy, refresh the local copy (or run the OneDrive exe / check what
  Task Scheduler points at).
- **The RAD-notices link tree is BIG:** ~1,900+ files, hundreds of
  subfolders, ~900+ API calls ≈ 13+ min per walk at ~1 call/sec — every
  run (links have no delta). Added: "listing..." announcement, progress
  line every 25 calls, 429 Retry-After handling, call budget (2500).
- **409-skip fix:** one subfolder returning `409 path/not_found` at
  ~800 calls aborted the whole 12-minute listing (0 files processed).
  Non-root 409s now warn + skip that subfolder; root/other errors stay
  fatal.
- **If the daily walk proves too slow:** "Add to my Dropbox" (mount the
  client folder into James's account) + a `folders` entry = delta
  cursors, one cheap call/day. Caveat: an App-folder app can't see the
  mount unless it's moved under Apps\<app>\; may need a Full-Dropbox
  app + re-auth.
- **Full dry-run succeeded** (~28-min walk, 5,258 files listed, 2,955
  eligible, 200 classified: 157 filed / 38 needs-review — review items
  are mostly scanned returns of service with no text layer). James then
  asked for a 6-month age floor: added `max_age_days` (per link /
  account-wide) + `--max-age-days` CLI override, filtering on Dropbox
  `server_modified` before download (undated files kept). James chose
  to pull the ENTIRE database on first pass (no floor; the floor stays
  available for later).
- **(2026-08-16) 5xx resilience:** the first live full-pull walk died
  at page ~1900 on a transient Dropbox `500` (one subfolder) — any
  non-409 error was fatal to the listing. Now: 3 attempts with backoff
  per page (network errors + 5xx; 429 waits Retry-After), and a
  subfolder that still fails is skipped with a warning while the walk
  continues; only root-listing failure aborts the link. Verified with a
  mocked-HTTP test (transient root 500 recovers; permanent subfolder
  500 gets exactly 3 attempts then skip; permanent root 500 aborts).
- **Incremental state saves:** link marks / folder cursors now persist
  per completed batch (not just end-of-run), so an interrupted
  multi-hour ingest resumes without re-downloading processed files
  (hash dedup always prevented re-FILING; this saves the bandwidth).
- **(2026-08-21) Hourly-runs question — decided AGAINST archive-move.**
  James asked whether Rocky should create "Archive" subfolders in the
  client Dropboxes and move ingested docs there to speed the walk.
  Declined: the API can't write via shared links anyway (needs edit
  membership + mount + write scopes), and Rocky should not reorganize a
  client's live working folder — read-only posture on client data
  holds. The hourly path is instead: mount the link ("Add to my
  Dropbox", works read-only, invisible to the client) + a `folders`
  entry = delta cursors (idle run ≈ 2 API calls). Needs one
  Full-Dropbox app + re-auth. Supporting change shipped: the FOLDER
  pass now has the same skip-before-download marks as links
  (`dropbox_folder_marks` state), so cursor resets / catch-ups / first
  pass over a mounted folder never re-download processed files
  (verified: scratchpad test_folder_marks.py, 9 checks). A second
  shared link was also queued for config ("second-link" example given
  to James; contents TBD).
- **(2026-08-22) Vault split into per-source tasks** (James's request,
  aiming at hourly cadence): new top-level flags `--vault-dropbox`,
  `--vault-mail` (rocky@ submissions), `--vault-inbox` (James's inbox
  sweep) — each dispatches vault.run_cli with a forced source, gets its
  OWN instance lock (state/rocky_vault-<x>.lock), and its OWN state
  file (`C:\Rocky\vault\state_inbox.json` / `state_vault_mail.json` /
  `state_dropbox.json`; legacy state.json auto-migrates + renames to
  .migrated). Concurrency hardening: Vault Index.xlsx now writes
  temp-then-swap (catches OSError, not just PermissionError); the
  append-only catalog tolerates the (tiny) concurrent-append risk.
  Dashboard: new "Vault" group — the three split commands default to
  HOURLY/1 schedules; the all-sources `--vault` stays for manual runs
  (do NOT schedule it beside the split tasks — same state files).
  Verified: test_folder_marks.py now 14 checks incl. migration split /
  rename / idempotency; live `--vault --status` still works. James's
  plan: mount the big RAD folder (delta cursors) and keep smaller links
  walked hourly via --vault-dropbox.
- **(2026-08-22, same session) Vault Digest** (`--vault-digest
  [--hours N] [--dry-run]`, dashboard "Vault Digest" @ 17:30): emails
  the window's Vault additions from rocky@ to
  `vault_digest_recipients` (default James) — Filed table (Property /
  Tenant / Document / Date / From, where From names the Dropbox link,
  rocky@ submitter, or inbox sender) + Needs Review list. Built from
  catalog.jsonl (dry-run entries excluded); quiet day = no email;
  guarded outbound (body_type HTML). Verified: scratchpad
  test_vault_digest.py, 8 checks (labels, escaping, window filter,
  quiet-day skip, dry-run no-send).
- **(2026-08-22, same session) Classification truncation fix.** First
  live `--vault-inbox` run failed on a Burton email carrying a stack of
  ledgers: one Claude call covered ALL of an email's attachments, and
  the response overran CLASSIFY_MAX_TOKENS (3000) → JSON truncated
  mid-array → "no JSON array in response" ×2 → email skipped (cursor
  correctly held). Fix: classify_documents now transparently chunks
  inputs at CLASSIFY_BATCH_MAX (10 docs/call), max_tokens raised to
  8000, and parse failures log the response tail for diagnosis.
  Verified: scratchpad test_classify_chunk.py (27 docs → calls of
  10/10/7, alignment preserved; truncated response → failure sentinel).
- **(2026-08-23) Cursor-rewind + rejection logging.** A Wednesday email
  (Sarah Wenger) sat before the inbox cursor (2026-08-20 19:39) and was
  unreachable: --backfill-days only applied on cursorless first runs.
  Now an EXPLICIT CLI --backfill-days rewinds both mail windows past
  their cursors (config vault_backfill_days still seeds first runs
  only); dedup makes re-covered ground harmless. Also fixed silent
  drops: inbox attachments the classifier rejects as non-vault material
  now log filename + reasoning (previously no trace, making "where's my
  email" undiagnosable). Verified: scratchpad test_rewind.py. Interim
  workaround told to James: forward any specific email to rocky@ with
  "Vault" in the subject.
- **(2026-08-23) Remy digest folded into the Multifamily Digest**
  (James: "squarely in the multifamily world"). New REMY section in
  multifamily_digest.py reads the local copy --remy-digest keeps under
  C:\Rocky\remy_digests\ and embeds it via the new
  remy_digest.digest_fragment_html() (digest_to_html refactored to wrap
  that fragment — same email output for manual runs). Date gate
  compares LOCAL dates (a UTC compare wrongly dropped today's digest on
  evening runs — caught by test) and is strictly newer-than-window-
  start, so a digest never repeats the next day. A Remy-only day still
  sends. Choreography: remy-digest 17:15 with --no-email (now in the
  registry args — the GitHub digest/ commit + local copy still happen;
  the standalone email retires), multifamily-digest 17:45 (now
  recommended). James must add Shane to multifamily_digest_recipients
  and recreate both scheduled tasks. Verified: scratchpad
  test_mf_remy.py, 7 checks.
- **(2026-08-24) James-inbox move REMOVED (supersedes part of the
  2026-08-23 "processed mail leaves the inbox" entry).** James: the
  move-to-Inbox\The Vault behavior should run ONLY on rocky@'s inbox,
  never his own. scan_inbox_source no longer moves anything (config key
  vault_inbox_processed_folder retired); vault-mail /
  litigation / letterstream moves (all rocky@) unchanged.
- **(2026-08-24) Property grounding from Remy's table.** Five Kelvin
  lease/ledger PDFs (forwarded by Sarah Wenger, subject "FW: The Kelvin
  | July Suit List") landed in _Needs Review because the classifier
  didn't recognize "The Kelvin" as a property. Fix: the Vault now loads
  Remy's `data\property_table.csv` (240 canonical property names,
  resolved beside remy_cli_path; `vault_property_table` overrides;
  missing = grounding off) once per run. The full name list rides in
  every classification prompt (with an explicit subject-names-the-
  property example), and `_ground_property` snaps returned names to
  canonical spellings (exact / SequenceMatcher ≥0.85 / containment for
  short-vs-long variants, Remy-asset style) and floors confidence at
  0.8 when a strong match coincides with a present tenant and original
  confidence ≥0.5. Also normalizes property-folder naming variants.
  Verified: scratchpad test_grounding.py, 11 checks (loads the real
  240-row table). The five stranded Kelvin files: team drags them from
  _Needs Review into place (re-forwarding would dedup-skip).
- **(2026-08-23) Multifamily Digest banner.** James supplied a
  "Gallagher's Daily Multifamily Group Digest" banner image, saved to
  Icon\multifamily_banner.png (bundled into rocky.exe like the Rocky
  icon). multifamily_digest embeds it as the email masthead via an
  inline cid attachment (`_banner()`; build_digest now returns
  (html, attachments)); the plain-text title is suppressed when the
  banner renders and returns if the image is missing. Config
  `multifamily_digest_banner`: omit = bundled default, path = override,
  "" = disable.
- **(2026-08-23) Processed mail leaves the inbox.** James's ask, all
  three processes: shared helpers added to rocky.py
  (`acquire_mail_move_token` — delegated Mail.ReadWrite.Shared,
  best-effort; `ensure_inbox_subfolder`;
  `file_message_to_inbox_subfolder` — 404-tolerant, stale-cache retry,
  modeled on litigation_updater._file_source_mail, which already did
  this and stays as-is → Inbox\Litigation Updater after a YES sheet
  write). New: Vault vault-mail submissions → Inbox\The Vault (rocky@);
  James-inbox mail the Vault TOOK documents from (incl. duplicates) →
  Inbox\The Vault (his mailbox; examined-but-nothing-taken stays put);
  Mailing Affidavits request/proof emails → Inbox\Letterstream once
  handled. Config: vault_processed_folder / vault_inbox_processed_folder
  / letterstream_processed_folder ("" disables each). Moves always run
  LAST (a Graph move changes the message id); folder ids cached in each
  process's state. Also confirmed for James: NO pipeline filters to
  unread — all fetches are date-window only. Verified: scratchpad
  test_mail_move.py, 9 checks.
- **(2026-08-23) Vault-mail detection widened to the forwarding note.**
  People forward to rocky@ without changing the subject and just type
  "please add to vault" — subject-only keyword matching missed 2 of 2
  real submissions in the first live vault-mail run. Now a submission =
  keyword in the subject OR anywhere in the note ABOVE the quoted
  `From:`/`-----Original Message-----` header (max 10 note lines; plain
  non-forward mail checks only its first 3 lines so a deep quoted
  mention never triggers filing). Ignored mail is now logged with
  subject + sender (capped 10/run) so "I forwarded it, where is it?" is
  answerable from rocky.log. The Vault's README.md now self-refreshes
  from the template whenever it drifts (it had frozen at first write)
  and tells the team the body-note option. Verified: scratchpad
  test_submission.py, 9 checks.
- **(2026-08-23) Wide-lookback support** (James wants e.g. a two-week
  pull: `--multifamily-digest --hours 336`): the Remy section now
  embeds ALL digests in the window chronologically (was newest-only —
  right daily, wrong for lookbacks), and vault.digest_body_html caps
  rendering at 300 filed / 150 review rows with "…and N more" overflow
  notes (headers show true totals) so a window covering a bulk ingest
  can't produce a several-thousand-row email. test_mf_remy.py now 11
  checks.

---

## Session 2026-07-29 (2) — The Vault (shared Remy-filing document library)

**What changed**

- **The Vault built** — `vault.py` + `rocky.py --vault`: a shared OneDrive
  folder ("The Vault", beside Rocky Cases) of documents supporting Remy
  drafting and other filings (leases, ledgers, affidavits of service,
  notices), organized `<Property>\<Tenant>\` with a regenerated
  `Vault Index.xlsx`, `_Needs Review\` for low-confidence items, and
  catalog/activity JSONL on the share under `_vault\`. Three sources per
  run: James's inbox (lease/ledger/affidavit attachments only), rocky@'s
  inbox (any subject containing "vault" = team submission — everything
  ingested, confirmation reply sent from rocky@), and Dropbox accounts
  (incremental via persisted cursors; per-account OAuth app +
  `--vault --dropbox-auth <name>` one-time helper).
- Wiring: `--vault` dispatch + docstring/help in rocky.py, dashboard
  registry entry ("The Vault", Other group, suggested 15:00, dry-run
  button), vault keys in config.example.json, vault.py bundled in
  build_exe.py. New `VAULT.md` operational guide; BUILD_REFERENCE.md
  section added.

**Decisions made**

- Filing gate: property + tenant + confidence >= 0.75 (raw values, not
  sanitized — the "unnamed" sanitizer fallback must never become a
  folder); everything else -> `_Needs Review` with original filename.
- Inbox pass takes ONLY lease/ledger/affidavit (opportunistic source);
  vault-mail takes everything (explicit submission, never dropped —
  even on Claude API failure it files to _Needs Review); Dropbox takes
  all vault types (curated source).
- SHA-256 dedup across all sources (catalog is the dedup index), so
  cursor re-reads are harmless; classification failures HOLD cursors
  (inbox pass stops; Dropbox folder cursor not advanced).
- Mail reads use the app token (Application Access Policy already covers
  jbragdon@ + rocky@) — zero new Graph permissions. Confirmation replies
  use rocky@'s guarded outbound (Level 0 holds).
- Graph fetch: no `$orderby` with the compound filter (InefficientFilter
  risk) — sort client-side, cursor = last processed receivedDateTime.

**Open items**

- Dropbox accounts: James to identify which client accounts, create the
  per-account Dropbox apps, run `--dropbox-auth`, and fill
  `vault_dropbox_accounts` (until then the pass skips cleanly).
- First live runs: `--vault --dry-run` on the Rocky laptop, then pin
  "The Vault" folder, share it with the team, schedule 15:00 daily.
- Scanned (no-text-layer) leases land in _Needs Review — consider wiring
  `extract_image_text_via_vision` for PDFs with no extractable text.
- Rebuild + deploy rocky.exe (`python build_exe.py`).

**Watch-outs**

- Offline test (scratchpad `test_vault.py`, 32 checks) covers filing,
  dedup, cursor-hold on failure, index rebuild, vault-mail end-to-end
  with stubbed Graph/Claude — all pass. No live-mailbox run yet.
- `Vault Index.xlsx` open in Excel during a run = index rebuild skipped
  that run (logged, harmless).

---

## Session 2026-08-02 — Email brain moved to Minotaur; Rocky side RETIRED

**What changed**

- **`email_brain.py` copied to Minotaur** (`Program Files\Minotaur\`) with
  its one rocky import swapped to Minotaur's `graph_mail`; Minotaur gained
  `brain stats|query|ingest|migrate` subcommands and validated the moved
  pipeline live (small no-embed ingest + FTS retrieval).
- **Migration executed and verified on the Rocky laptop** (same day, via
  Minotaur's `setup` → `login` → `brain migrate`): 29,699 pairs, all
  embedded (dim 1024), at `C:\Minotaur\email_brain\brain.db`.
- **Rocky retirement completed:** removed `--email-brain` dispatch, help
  text, and `run_email_brain_cli` from rocky.py (pointer comment left at
  the old site); deleted this repo's email_brain.py; dropped the
  email_brain add-data + numpy hidden-import from build_exe.py; removed
  the dashboard's Email Brain registry entry; pruned the email-brain
  config block from config.example.json; updated BUILD_REFERENCE.md and
  DATA_SECURITY.md (live corpus now under C:\Minotaur; stale copy remains
  at C:\Rocky\email_brain until deliberately deleted).

**Open items**

- Rebuild + deploy rocky.exe/dashboard.exe (`python build_exe.py`) — also
  ships the 2026-07-18 closed-cases change that was already awaiting
  rebuild.
- On the Rocky laptop: disable any `--email-brain` Task Scheduler entry if
  one was ever created; schedule Minotaur's `brain ingest` (~02:00) and
  `learn` (~21:00) instead. Delete `C:\Rocky\email_brain\` once Minotaur's
  copy has run for a while.
- Online-Archive sends → rocky@ `Inbox\James Older Sent` remains open,
  now tracked in MINOTAUR.md (add the folder to sent_brain_folders in
  C:\Minotaur\config.json when done).

---

## Session 2026-07-29 — Minotaur created (sibling program, interactive email management)

**What changed**

- **Minotaur v1 built** — a sibling program at
  `OneDrive - gejlaw.com\Program Files\Minotaur\` (source; data/secrets at
  `C:\Minotaur`). It houses *interactive* email-management processes driven
  by a live Claude Code session (usually from James's phone), vs. Rocky's
  scheduled batch commands. v1 = inbox review & summarize + draft replies:
  `minotaur.py check|login|inbox|read|thread|draft|folders` over an extracted
  `graph_mail.py` (Rocky's auth/fetch/attachment plumbing, exceptions instead
  of sys.exit). No Rocky code was modified. See `MINOTAUR.md` there.

**Decisions made**

- **Email brain moves ENTIRELY to Minotaur** (its stage 2): ingestion,
  retrieval, and brain.db. A MIGRATION, not a fresh start — phase 1 has been
  live since 2026-07-13 (~29,700 pairs in `C:\Rocky\email_brain\brain.db`);
  stage 2 copies brain.db + `state\email_brain_state.json` to `C:\Minotaur`
  and moves any daily-incremental schedule. Rocky's `--email-brain` and
  email_brain.py stay untouched until that move actually lands, then retire.
  (Also fixed BUILD_REFERENCE.md, which still called the brain "not yet run
  live" — stale since the 2026-07-12 (3) session.)
- Minotaur reuses Rocky's Azure app registration and both validated auth
  paths (app-token reads of jbragdon@; delegated rocky@ Mail.ReadWrite.Shared
  for createReply drafts). Level 0 holds: no send function in Minotaur.
- The name "Minotaur" is knowingly recycled from Rocky's old naming history;
  Minotaur's docs carry a disclaimer distinguishing it from pre-"Rocky" docs.

**Open items**

- Live verification of Minotaur's commands against the real mailbox; Rocky
  laptop setup (Python + requirements, Claude Code, `minotaur.py login`).
- When Minotaur stage 2 lands: remove `--email-brain` from rocky.py, drop
  email_brain.py from build_exe.py bundling, and update BUILD_REFERENCE.md.

---

## Session 2026-07-18 — Closed cases: second worksheet + Closed Cases folder

**What changed**

- **`load_case_index()` (rocky.py) reads all worksheets**, not `wb.active`
  (which pointed at whatever sheet was selected on last save — a landmine
  once James added the closed-cases worksheet). Rows from any sheet whose
  name contains "closed" load with `Open/Closed` forced to `"Closed"`, so
  moving a row between sheets is the whole close/reopen workflow.
- **Closed cases skipped everywhere**: `--daily-digest` (no section at all,
  even with folder activity — previously only the no-activity list checked),
  `--daily-run`, and `--daily-cases` (no email fetch, result reason
  `closed`). Each skip logs at INFO.
- **`Closed Cases/` disk subfolder** (new, James-created under Rocky Cases):
  top-level scans are non-recursive so it was already invisible; added
  `CLOSED_CASES_DIRNAME` + `_find_in_closed_cases()` so `--daily-cases`
  logs a quiet "in Closed Cases — skipping" instead of a spurious
  "folder not found" warning when a folder has moved there.

**Decisions made**

- An explicitly targeted RRID (`--daily-digest RRID-XXXX` etc.) overrides
  the closed skip — a manual run on a closed case is presumed intentional.
- Sheet placement is authoritative: a row on the closed sheet is Closed even
  if its Open/Closed cell is blank or says Open.

**Open items**

- Auto-reply/OOF filtering in the email fetchers discussed and designed
  (message class `IPM.Note.Rules.OofTemplate.Microsoft` via extended
  property + `Auto-Submitted` header) but not implemented — awaiting go-ahead.

**Watch-outs**

- Offline test (scratchpad `test_closed_cases.py`) covers the two-sheet
  index saved with the closed sheet active, and the folder helpers — all pass.
- Rebuild + deploy `rocky.exe` (`python build_exe.py`) for the change to
  reach the Rocky laptop.

---

## Session 2026-07-13 — Open chat action proposals (propose→confirm loop)

**What changed**

- **Action proposals (inbox_cleaner.py, open chat mode).** Open-ended
  requests from James no longer apply directly: the open-chat Claude call
  now returns them as a `proposal` — description + exact structured
  effects (standing_rules / route_ops / code_changes) — which the code
  stores in the user's state file (`pending_action`, ids A####, one at a
  time), renders deterministically on Teams (`_render_action` — he
  approves what the code will do, not a paraphrase), and holds for
  YES/NO. On YES, `_apply_action_effects` applies the STORED effects
  exactly as proposed (`[A#### approved date]` provenance in rules.md);
  on NO it's dropped; "adjust it" returns a revised proposal that
  replaces the old one under a new id. Direct application remains for
  crisp single-interpretation instructions ("skip newsletters@x.com").
- **Ambiguity guards.** While an action proposal is pending, chat_cycle
  proposes no new cohorts (one ask at a time); the prompt instructs that
  a bare yes/no resolves whichever pending ask (cohort vs action) was
  proposed most recently, and the Claude-down fallback implements the
  same rule deterministically — a stored proposal can be approved with
  no API available.
- rules_update substantive events extended with action_proposed /
  action_confirmed / action_declined.
- New offline test suite (test_action_proposals, scratchpad): propose,
  confirm, revise, decline, deterministic-fallback confirm, suppression
  state — all passing; prior suites re-run clean.

**Decisions made**

- Approved effects apply from the STORE, never re-generated at approval
  time — what James saw is what runs.
- Proposal rendering is code-side, not Claude's prose, so the approval
  target is always the literal operational effects.

**Open items**

- Live-test the loop once deployed: give an open-ended instruction,
  confirm the [PROPOSAL A0001] message shows exact effects, reply YES,
  verify rules.md/sender_routes.json and that the next analyze honors it.

## Session 2026-07-12 (4) — Inbox Cleaner for James + "sort with friends" pass

**What changed (part 2 — open chat, code backlog, Engineer)**

- **Open chat mode (`chat_mode: "open"`, James only; Matt stays strict).**
  Every Teams message from the owner goes through one guarded Claude call
  (`_open_chat_handle`) returning structured effects: pending-proposal
  decision (free text still can't command an unproposed move); standing
  rules → rules.md immediately (`[chat YYYY-MM-DD]` tags); additive
  sender_routes.json ops — exclude_sender / exclude_domain / add_route
  ("skip emails from x because y" → durable exclusion honored by EVERY
  pass and at execute time via `load_chat_exclusions`/`_drop_excluded`);
  and a conversational reply. Routes file backed up to rules_history
  before each edit. Claude failure → strict YES/NO fallback still
  resolves the pending proposal; messages stay in communications.jsonl
  for the nightly rules update (substantive-events list extended).
- **Code-change backlog.** Chat requests the code can't satisfy are
  appended to `inbox-james\code_changes.md` (title + dev-ready detail +
  chat quote) — the dev to-do list James asked for.
- **Engineer.** Teams message starting with "engineer" (deterministic
  regex, runs before the Claude call) or `--inbox-james --engineer
  [--query "..."]`. Pulls newest/best-matching inbox email, full text
  body + attachments + mailbox-wide thread history (folder locations,
  last 5 prior bodies); one deep Claude call (8192 tokens) → fixed
  headings (Summary/Timeline/Analysis/Recommended response). Delivers:
  report.md + raw attachments to `inbox-james\engineer\<stamp>_<slug>\`,
  report emailed from rocky@, DRAFT reply in James's Drafts (createReply
  — first shipped use of the iteration-3 draft capability; never sends),
  Teams ack with the summary. All failure paths degrade gracefully.
- Offline tests (mocked Graph/Claude/Teams): route ops, exclusions
  end-to-end, code-change log, engineer regex, open-chat handler +
  fallback, full engineer run — all passing; prior tests re-run clean.

**What changed (part 1 — the process + sort-with-friends)**

- **`--inbox-james` — James's own Inbox Cleaner process** (small-inbox
  maintenance mode, not a deep clean). Config blocks added to
  config.example.json AND this machine's config.json:
  `conversation_sort: true`, `cycle_execute: true`,
  `write_via: "delegated"`, `observers: []` (true 1:1 Teams chat).
- **"Sort with friends" pass (inbox_cleaner.py)** — the SortByConversation
  Outlook VBA macro ported to Graph. Per inbox message, mailbox-wide
  sibling lookup: `$filter=conversationId eq` first (survives [EXTERNAL]
  gateway rewrites), normalized-subject `$search` fallback (post-filtered
  to exact normalized equality); Sent/Deleted/Drafts/Junk/Outbox + Inbox
  root excluded; most-siblings folder wins. Drafts one cohort per target
  folder → normal Teams YES/NO loop. Reads the CURRENT inbox live (hand-
  filed mail never proposed); skipped above `conversation_sort_max`
  (default 200) so it can't run on a Matt-scale mailbox.
- **`--cycle` subcommand** — snapshot → analyze → chat → execute-approved
  in one shot; live execution only when the user's `cycle_execute` is set,
  and only for cohorts already approved over Teams.
- **Cohort model additions:** kind `conversation_sort` (claim priority -1,
  outranks matters — filing history beats keyword match);
  `match.message_ids` (explicit ids, resolved live at analyze); cohort
  `target_folder_id` (existing folder anywhere in the mailbox — execute
  uses it directly, skipping `_ensure_folder`'s under-Inbox creation).
  Analyze prunes stale conversation-sort drafts only when the pass ran
  (skip/failure can't wipe pending drafts).
- **Dashboard:** "James Inbox" button (`--inbox-james --cycle`, suggested
  08:00, Inbox group); registry gained a generic `args` key for commands
  needing fixed extra argv.
- Docs: INBOX_CLEANER.md new "James's own process" section;
  BUILD_REFERENCE.md Inbox Cleaner section + open items updated.

**Decisions made**

- Conversation-sort proposals go through the SAME approval loop as
  everything else (no auto-move without a Teams YES), but James's cycle
  executes approved cohorts live immediately — rocky@ already has Full
  Access on his mailbox, so no IT gate applies.
- Pass reads the live inbox, not the snapshot: correctness (never propose
  hand-filed mail) over reusing Stage-1 plumbing.
- No new well-known-folder exclusions beyond macro parity (Archive is a
  legitimate filing target).

**Open items**

- **Rocky laptop:** add the `james` block to `C:\Rocky\config.json`
  (copy from config.example.json), rebuild/deploy (`python build_exe.py`
  — tree also carries the 7/11 + 7/12 uncommitted work), then first live
  run: `rocky.exe --inbox-james --cycle`. Verify the `$filter=
  conversationId eq` mailbox-wide query works against Graph (only piece
  not verifiable offline — dev config.json has no client_secret, so app-
  token commands can't run here; the fallback $search path covers a
  rejection, but confirm in rocky.log). Then schedule daily 08:00 from
  the dashboard.
- First Teams cycle will send James the intro message (open-mode wording)
  and the first proposal — expect it.
- Live-test Engineer once deployed: send "engineer" in the Teams chat (or
  `rocky.exe --inbox-james --engineer`) and confirm all four deliverables:
  report folder, rocky@ email, draft in Drafts, Teams ack.
- Open-chat live checks: give one piece of free-form feedback ("skip
  emails from X because Y") and confirm rules.md + sender_routes.json
  exclusions + next-cycle behavior; ask for something the code can't do
  and confirm it lands in code_changes.md.

**Watch-outs**

- Offline smoke test (mocked Graph) passed: strategy order, exclusions,
  best-folder pick, cap gate, explicit-id resolution, dashboard argv,
  rules wording.
- A conversation-sort cohort proposed on Teams, then hand-filed before
  approval, will 404 at move time — logged as `move_failed`, harmless.
- Cohort ids in `match.message_ids` are Exchange ids, which CHANGE when a
  message moves folders — fine here because ids are resolved live each
  analyze and executed shortly after.

## Session 2026-07-12 (3) — Email brain first live runs + streaming ingest patch

**What changed**

- **First live email-brain runs (Rocky laptop, this afternoon)** — continuing
  the morning session's Voyage work: config email-brain block added to the
  laptop's config.json (one missing-comma JSON error found/fixed); `--stats`
  smoke OK (proves exe bundling); `--backfill-days 7 --limit 25 --no-embed`
  processed 25 of 420 (7 days of sent mail): **14 Graph-paired / 2 quoted /
  9 style-only** — healthy split, pipeline works end to end.
- **email_brain.py — streaming ingest rewrite.** The live run exposed that
  `fetch_all_folder_messages` paged the ENTIRE folder (bodies included) into
  one in-memory list before the `--limit` check ever ran, and the
  cursor/seen-ids persisted only once per folder at the very end. James's
  Sent Items = 30k messages; at the measured ~1.6 s/msg that's a ~13-hour
  backfill holding ~GBs in RAM that would lose ALL progress (cursor reset →
  full refetch) on any interruption. Rewrote to `iter_folder_message_pages()`
  (generator, one Graph page at a time) + a checkpoint every 100 processed
  messages (commit + cursor + seen-ids; safe because pages arrive
  oldest-first, so everything before max_sent is stored). Bonus: `--limit N`
  now stops fetching early — the original "--limit 25 fetches ALL" surprise
  is gone.
- **email_brain.py — Graph token auto-refresh (second patch, same day).**
  The first long live run (8,450 processed in ~65 min) died on Graph 401
  "token is expired": the app token (~60-75 min lifetime) was acquired once
  at startup. Streaming checkpoints meant zero data loss (all 8,450 banked).
  New `TokenKeeper` (proactive refresh after 45 min via a `token_provider`
  callable = `acquire_app_token(config)` passed from rocky.py); page iterator
  re-stamps the Authorization header per page; per-message calls pull
  `keeper.get()`. Short runs behave exactly as before (provider never fires).
- **email_brain.py — Voyage token-budget batching (third patch, same day).**
  First embed attempt (20,648 pending pairs) failed immediately: Voyage 400
  TOO_MANY_TOKENS_IN_BATCH — the API caps a request at **120k tokens** and
  `embed_texts` batched by COUNT only (EMBED_BATCH=64 texts × up to 24k chars
  = way over; seen live: 64 texts = 145,874 tokens). Rewrote `embed_texts`
  to pack requests by estimated tokens (`len//3+1`, conservative) under
  `EMBED_TOKEN_BUDGET = 90_000`, max 64 texts; if the estimate ever runs low,
  a TOO_MANY_TOKENS 400 bisects the chunk recursively instead of failing the
  run. Embedding remains resumable by nature (only NULL-embedding pairs are
  selected; commits per outer batch).
- **Rebuilt + deployed THREE times** (`python build_exe.py --rocky` →
  OneDrive `Program Files\Rocky\`): ~15:15 streaming patch, ~17:00
  TokenKeeper, ~22:30 Voyage batching. **The laptop's C:\Rocky copy lagged
  the deploys** — the 22:18 run still 401'd at the hour mark (old exe), so
  confirm the C:\Rocky copy updates before the final passes.
- **Backfill progress at session time:** 11,949 pairs stored (~12k of 30k
  sent), 5,025 with Graph inbounds; oldest-1,000 slice was 88% paired
  (18% Graph + 70% quoted) — the quoted-history parser is doing exactly what
  it was designed for on archive-era mail. Observed pace: ~0.4 s/msg on old
  mail, ~1.6 s/msg on recent attachment-heavy mail.
- **Log noise, harmless:** pypdf "Ignoring wrong pointing object" /
  "invalid pdf header" walls = malformed PDF attachments, extraction is
  best-effort; openpyxl "Slicer List extension" similar.

**Decisions made**

- **Full backfill plan (30k sent messages): overnight
  `--email-brain --rebuild --no-embed`.** `--rebuild` is REQUIRED for the
  full pass: the smoke runs left the cursor at ~2026-07-05, which would
  otherwise permanently hide all older mail from incremental runs. If the
  overnight run is interrupted, resume with plain `--email-brain --no-embed`
  — **never repeat --rebuild on a resume** (it wipes db + cursor). Embed
  everything afterward in one `--email-brain` pass, then schedule the daily
  incremental.

**BACKFILL COMPLETE (2026-07-13 00:43).** Final run: 9,052 fetched with zero
401s (TokenKeeper refresh worked) and **all 29,699 pairs embedded** in one
pass (token-budget batching worked; no TOO_MANY_TOKENS splits needed at the
64-text/90k-est packing). Final corpus: **41,207 messages, 29,699 pairs,
14,990 (50%) with true Graph inbounds**, all embedded with voyage-3-large.
Phase 1 of the email brain is live.

**Retrieval acceptance test PASSED (2026-07-13 ~00:50).**
`--query "VAWA lease termination"` returned 5/5 on-topic results spanning
years and senders: a protective-order termination with James's substantive
VA-statute answer (30-day release; temporary vs final order distinction), the
HUD-forms-still-required-despite-duplication answer citing the DC litigation
loss, VAWA extension-request approvals, and a coverage question. Both
`inbound` and `inbound_quoted` sources contributed. Scores 0.54–0.61.
Cosmetic only: CLI snippets show minor text artifacts from quoted-history
extraction ("Jamesmes replied", URL-encoded junk) — stored text feeding
phase-2 Claude calls is unaffected in substance; not worth chasing now.

**Open items**

- `--stats` for the full source split (inbound / inbound_quoted / reply-only)
  — informational now that retrieval quality is confirmed.
- Schedule the daily incremental (dashboard 📅 on email-brain; suggested
  02:00). ~60 sends/day → seconds of runtime, pennies of embedding.
- Optional, whenever: rocky@ `Inbox\James Older Sent` archive ingest
  (create folder, drag Online Archive sends, re-add config entry) — cursors
  make it additive, and the new streaming/token/batching code handles the
  multi-hour run.
- Phase 2 design (retrieval → drafting) is now unblocked.

**Watch-outs**

- ~36% of recent sends are style-only (thread-starters/forwards with no
  inbound) — expected, not a pairing failure.
- Sent volume is ~420/week, so the daily incremental is trivially cheap.

## Session 2026-07-12 (2) — Fix Behroozi daily-run loop (NBSP filename mismatch) + seen-but-unfiled parking

**What changed**

- **Seen-but-unfiled parking (rocky.py).** Closes the general class of the
  Behroozi loop: any raw file analyzed by a daily run but not successfully
  filed or discarded (no usable file_action, refused DISCARD, copy failures)
  now increments `unfiled_attempts` in `master_file_index.json`. After
  `UNFILED_PARK_THRESHOLD` (3) missed runs the file is **parked**: indexed
  with `"disposition": "parked_unfiled"` so it stops surfacing as "new",
  a `document_parked_unfiled` activity event is logged, and a standing
  "## Rocky Unfiled Documents" section is added to the case CLAUDE.md
  telling the next Cowork session to ask James what to do with each parked
  file. Refactored the CLAUDE.md pointer logic into shared
  `_ensure_claude_md_section()` (used by both the suggestions and unfiled
  pointers). `_read_filed_since` excludes `parked_unfiled` (digest doesn't
  report parked files as "filed"); parking itself surfaces once in the
  digest via the unknown-event bias. BUILD_REFERENCE Stage 2 section
  updated. Verified end-to-end with a stubbed Claude client: 4 simulated
  runs → misses counted, parked on run 3, pointer added, run 4 sees zero
  new files.
- **Queued the Behroozi question for Cowork.** Appended an
  `internal_suggestions` activity event to RRID-0012 asking what to do with
  the manually filed `Misc/Email - Behroozi-McKenna Rent Credit Settlement
  Thread (2026-01-06).pdf` (keep in Misc / move / delete as duplicative of
  the 6/7 filing), and added the "## Rocky Suggestions" pointer to the case
  CLAUDE.md (it wasn't there yet) so the next project session asks.

- **Whitespace-tolerant file_action matching (rocky.py).** RRID-0012's daily
  run had reported the same "new pre-litigation correspondence" (the 1/6/26
  Behroozi↔McKenna rent-credit thread) every day since 6/10. Root cause: the
  raw file `Re_ Heming #316 - Rent Credit.pdf` contains a **non-breaking
  space (U+00A0)** after `Re_` (Outlook subject artifact). Claude echoed the
  name back with a plain space, the exact-match check in
  `process_case_folder`'s file-actions loop failed, the action was skipped
  every run, and the file never entered `master_file_index.json` — so it was
  "new" forever (~13 wasted Claude calls). New module-level
  `_normalize_name_for_match()` (collapse Unicode whitespace, casefold) +
  a normalized-name fallback lookup in the matching loop; exact match still
  wins, and unmatched names still warn and skip.
- **Manually filed the stuck PDF** to
  `Misc/Email - Behroozi-McKenna Rent Credit Settlement Thread (2026-01-06).pdf`
  with matching `master_file_index.json` entry and `document_filed` activity
  event, so the loop stops immediately — before the code fix is even
  deployed. (The similarly named single-space PDF filed 6/7 is a different
  capture — 181 KB vs 130 KB — both kept.)

**Decisions made**

- Filed rather than deleted the NBSP file: not byte-identical to its
  single-space sibling.
- Normalized lookup uses `setdefault` (first name wins) on collision — two
  *new* raws differing only in whitespace is rare, and raws are copied not
  moved, so a wrong pick can't lose data.

**Open items**

- ~~Code fix takes effect on the Rocky laptop only after the next
  build/deploy.~~ Shipped: rebuilt + deployed rocky.exe ~15:19 (the
  session-(3) 15:04 build had the NBSP fix but missed the parking edits by
  a minute; this build is the same source plus parking — email-brain
  streaming code unchanged, so the overnight backfill plan is unaffected).

**Watch-outs**

- A file in `Raw Documents/` that gets analyzed but never successfully
  filed re-analyzes daily with no cap — there's still no "seen but unfiled"
  state. If another skip path recurs (e.g. model returns no file_action),
  the same loop happens for a different reason.

---

## Session 2026-07-12 — Email brain roadmap, Voyage data-policy review, DATA_SECURITY.md

**What changed**

- **`BUILD_REFERENCE.md` — new "Email Brain" entry** under Future skills and
  capabilities (the brain previously existed only in session-log entries).
  Three-phase roadmap: *Phase 1* corpus + retrieval (built 6/22, never run
  live; gated on Voyage key + Voyage opt-out + archived-sends drag into
  rocky@); *Phase 2* retrieval → live drafting ("respond like James"), not
  designed; *Phase 3* (NEW, stated by James this session) **timesheet
  correlation** — cross-reference the email corpus with billing time entries
  so the brain learns email-task↔time-entry mapping, eventual goal assisted
  drafting of time entries. No phase-1 schema change needed (dates,
  participants, conversationId already preserved); future needs: per-message
  matter/client tag + timesheet ingest (firm billing system / export format
  still unidentified — ask James before designing phase 3).
- **`DATA_SECURITY.md` (new)** — comprehensive data-security reference for
  Rocky, started because James may present on Rocky's data security soon.
  Covers: data inventory, storage map (local vs OneDrive vs git-excluded),
  M365 permission model (Level 0, access policy scoping), external-processor
  table (Graph/Anthropic/Voyage/Healthchecks/Tailscale), code-level controls
  (permissions.py, outbound.py, kill_switch, dashboard allowlist, DISCARD
  guardrail), audit trail, known gaps (plaintext keys, BitLocker/Purview/
  RDP-access confirmations, Voyage SOC 2), and a dated vendor-policy
  verification log. Living document — update on any new data flow/vendor/
  permission; re-verify vendor log entries older than ~90 days.

**Decisions made**

- **Voyage AI approved for email-brain embeddings CONDITIONAL on account
  opt-out.** Verified against Voyage's published FAQ/privacy/ToS (7/12):
  default terms retain API data and permit training on it — unacceptable for
  privileged correspondence. Paid accounts can opt out (dashboard → Org
  settings → ToS toggle; needs payment method + org Admin; one-way), giving
  **zero-day retention**. **James is opting the account out**; confirming the
  dashboard shows "Opted Out" is step zero of the first-run sequence (added
  to BUILD_REFERENCE phase-1 gate). Free tier prohibited — free-tier data is
  trained on. Rationale recorded in DATA_SECURITY.md §10: opted-out Voyage is
  the same exposure category as the already-accepted Anthropic API flow
  (Anthropic Commercial Terms re-verified 7/12: no training on API content,
  ≤30-day standard retention, ZDR available via sales).

**Open items**

- ~~James: perform the Voyage opt-out~~ **DONE same session (2026-07-12)** —
  dashboard ToS page shows "Opted Out"; screenshot saved to
  `docs/voyage_opt_out_2026-07-12.png` (gitignore exception `!docs/*.png`
  added so compliance artifacts stay tracked); recorded in
  DATA_SECURITY.md §5/§9/§10 and BUILD_REFERENCE.md (gate cleared).
- DATA_SECURITY.md §8 gap list needs answers: BitLocker status on the Rocky
  laptop, Purview coverage confirmation, Tailscale/RDP access list, OneDrive
  sharing scope of Rocky Cases, whether to pursue an Anthropic ZDR agreement,
  Voyage SOC 2 inquiry (support@voyageai.com).
- Phase 3 prerequisite question for James: which billing system, and can past
  time entries be exported (CSV/Excel/report)?

**Watch-outs**

- Voyage's opt-out toggle is ONE-WAY (can't re-opt-in from the dashboard) —
  fine for us, but don't be surprised by it.
- Vendor-policy claims in DATA_SECURITY.md carry verification dates; treat
  anything older than ~90 days as stale before a presentation.

## Session 2026-07-11 — Signature-image filter fix + DISCARD file action

**What changed**

- **Signature-image ingestion filter broadened (rocky.py + pma_tracker.py).**
  The 7/10 digest reported two "organizational logo images uploaded" to the
  Palma case (RRID-0020) — Catholic Charities (image003.png, 41.9 KB) and
  Esperanza Center (image004.png, 16.9 KB) signature logos from a 7/7 Laura
  Callahan email — plus a NonProfit Times award badge (image100395.png,
  20.3 KB) misfiled into Whalen (RRID-0015). The old filter only skipped
  images that were BOTH isInline AND ≤15 KB. New module-level
  `is_signature_image()`: image content-type, ≤25 KB
  (`SIGNATURE_IMAGE_MAX_BYTES`), and (isInline OR auto-name
  `image\d{2,}.(png|jpe?g|gif|bmp)` — long digit runs like image100395 and
  image540880 occur in the wild). Deliberately attached photos keep original
  filenames / aren't inline, so they pass. pma_tracker.py mirrors the logic
  locally (self-contained module; rocky imports it, so no back-import).
- **Why 25 KB, not bigger:** RRID-0015 proves substantive PASTED screenshots
  arrive inline as image001.png at ~45 KB (real AAA arbitrator-name
  screenshots, filed 6/24 as case documents), while the largest junk logo
  seen is 41.9 KB. No size threshold separates them, so ingestion only
  auto-skips clear-cut tiny artifacts; the 25–60 KB gray zone falls through
  to the daily run's DISCARD action, which judges actual image content via
  vision.
- **NEW: DISCARD file action in the daily run.** Root cause of the misfiling:
  unfiled raws re-appear as "new" every day, so on day 2 the model filed the
  logos into Pleadings ("Catholic Charities Logo.pdf") just to dispose of
  them — it had no discard option. DAILY_RUN_SYSTEM_PROMPT now allows
  `"target_folder": "DISCARD"` for non-substantive email artifacts; executor
  deletes the raw + its image/PDF companion. **Code-level guardrail** (model
  can't override): only a small image or a PDF with an image sibling, each
  ≤200 KB (`DISCARD_IMAGE_MAX_BYTES`), can be discarded — anything else is
  refused and left in place. Discards are indexed in master_file_index.json
  as `"disposition": "discarded", "path": null` (never resurface as new) and
  logged as `document_discarded` activity events.
- **Digest gating:** `document_discarded` is non-substantive;
  `daily_run` events subtract `discards_requested` from both
  file_actions_requested and new_raw_files_seen (artifact-only days no longer
  surface a case); digest formatter skips `document_discarded`;
  `_read_filed_since` skips discarded index entries.
- **RRID-0020 cleanup:** deleted the 6 junk files (Pleadings\Catholic
  Charities Logo.pdf + Esperanza Center Logo.pdf; Raw Documents image003/004
  .png + .pdf), rewrote their two master_file_index entries as discarded,
  appended two `document_discarded` audit events (actor claude-dev-session).
- **RRID-0015 cleanup:** deleted the NonProfit Times badge pair from Raw
  Documents, AND found/removed an older mess: three byte-identical copies of
  the **Gallagher LLP signature logo** (10.3 KB) misfiled on 5/31 into
  case subfolders with hallucinated vision descriptions ("Email screenshot
  containing wire transfer mechanics", "settlement amounts", "attorney
  correspondence"). Index entries corrected to discarded with the real
  descriptions; audit events appended. The 6/22 image001.png files (45 KB
  AAA arbitrator-name screenshots) were verified substantive and left filed.
- **All-case sweep for imageNNN artifacts** found two more cases with junk,
  cleaned the same way (delete + index disposition + audit events):
  RRID-0007 Clowney — blank 823 B/1.7 KB signature-spacer images filed as
  "Unprocessed Document" PDFs in Fact Research and as "Email Signature
  Image 003.jpg" in Miscellaneous; RRID-0002 Phillips-Moore — Capstone Real
  Estate signature logo filed as "Corporate Logo.pdf" in Miscellaneous Case
  Docs, plus another Gallagher-logo copy filed 5/18 as "Unidentified Image
  001" in Raw Data. Every filed copy hash-verified as junk before deletion.
  Images verified visually via Claude vision before deleting.
- **Deployed:** `python build_exe.py` → rocky.exe (45.5 MB) + dashboard.exe
  copied to OneDrive `Program Files\Rocky\` 7/11 ~7:05 AM; Rocky laptop
  picks it up on the next scheduled run.

**Watch-outs**

- **Vision hallucination pattern:** when handed a bare logo, the daily-run
  model invented document descriptions matching the surrounding email's
  topic (the 5/31 Whalen misfilings). DAILY_RUN_SYSTEM_PROMPT now says to
  judge an image by what it shows, not the email context — but treat
  image-only "document_filed" summaries with suspicion when auditing.
- The 25 KB threshold is a judgment line with real counterexamples on both
  sides within 4 KB of each other (41.9 KB junk logo vs 45 KB substantive
  screenshot). A junk logo over 25 KB is expected to reach the DISCARD path
  rather than the ingestion skip. Ingestion skips log at INFO
  ("Skipping signature image ...").
- Legacy daily_run events lack `discards_requested`; gating defaults it to 0
  (old behavior preserved).

---

## Session 2026-07-09 — Matt's questionnaire reply received and parsed

**What changed**

- **Second --analyze on the laptop (12:46 PM) validated the new passes:**
  28 cohorts — 22 matter cohorts (all 25 seeded matters except Stag Moose
  Construction, CVE Term Refi, Syncarpha ME III Construction, whose
  specific keywords matched no subjects; generic keywords sent that mail
  to their sibling matters), 4 sender routes, court notices 37→34
  (sharefile fix confirmed), newsletters 33,073→21,241 (carve-outs
  working). Internal cohort survived un-refreshed at stale n=54,135 (its
  match rule didn't change, only exclusions) → added draft-refresh to
  analyze: same-key cohorts still in draft status get count/wording/seq
  updated in place (cohorts_refreshed event); proposed/decided stay
  frozen. Needs one more --analyze after deploy to refresh the internal
  count.
- **Chat kickoff failed 7/11 6:31 AM — legacy-UPN bug, FIXED + verified.**
  Graph 404 "Failed to find users": Azure UPNs are still @gejlaw.com
  (rocky@'s own UPN verified via /me) while mail + config addresses are
  @gallagherllp.com; chat member binds need UPNs. Directory reads are
  403 for rocky@ (no User.ReadBasic.All), so no lookup — instead teams.py
  _create_chat now parses the failed-UPN list out of the 404 and retries
  once with the legacy domain swapped on exactly those members
  (swap_legacy_domain, bidirectional — survives IT flipping UPNs later).
  Owner-resolution in chat_cycle also matches either domain. LIVE
  VERIFIED from dev: rocky↔James 1:1 chat created on the retry path;
  member emails come back @gallagherllp.com. James re-runs --inbox-matt
  --chat on the laptop after the deploy syncs.
- **Participant-corroboration guard for generic matter keywords (per
  James, 7/10).** Optional per-matter "participant_domains" in
  matters.json: a subject-keyword hit only claims the conversation if
  some message in it involves (from/to/cc) one of those domains
  (subdomain-aware). For Twelve/Dimension the hooks are seeded EMPTY (no
  guard yet) because the counterparty domains aren't knowable from dev —
  matter cohorts now carry top_senders (workbook + Teams proposal), so
  James/Matt fill the domains from that evidence and re-analyze before
  approving those two batches. Guard is stored in the cohort match (new
  match_key → clean regeneration) and honored at execute time. Tests:
  14/14 pass incl. "twelve days of CLE" rejection.
- **Subdomain matching for routes/never_bulk (bug, caught answering
  James's "is Matt's industry-newsletter rule enforced?").** Route +
  never_bulk domain matching was exact-equality, so
  contactus@emails.woodmac.com missed the woodmac.com renewable-updates
  route and sat in the Deleted Items cohort. New _domain_in() does
  suffix-safe subdomain matching (emails.woodmac.com ⊂ woodmac.com;
  woodmac.com.evil.net does NOT match), used in the route pass, bulk
  exclusions, and _cohort_message_ids. Re-analyze shifts counts.
- **Delegated write path (per James — "the lighter IT ask").** New per-user
  config write_via: "app" (default; application Mail.ReadWrite, the
  original design) or "delegated" (rocky@'s delegated token with
  Mail.ReadWrite.Shared — verified consented 7/5 — plus an Exchange Full
  Access delegation on the target mailbox, same mechanism as James's).
  Matt = delegated. --execute and its mid-run 401 refresh both go through
  _write_token(). IT ask reduces to ONE Exchange action:
  Add-MailboxPermission -Identity Mpirnot@... -User rocky@...
  -AccessRights FullAccess -AutoMapping $false. No Azure change, no admin
  consent. James must add "write_via": "delegated" to the laptop config's
  matt block. INBOX_CLEANER.md step 6 rewritten with both options.
- **NEW: matters.json self-tuning (per James).** --rules-update now has a
  phase 2: a second guarded Claude call reads the day's chat + matter-
  cohort statuses and returns STRUCTURED ops (add_matter /
  update_keywords / set_closed — no delete op exists), applied
  deterministically by _apply_matter_ops (duplicates/unknowns/no-op
  changes skipped silently). Old file backed up to
  rules_history\matters_*.json; matters_updated event logged; digest
  reports the change ("a new or revised batch proposal will follow");
  analyze re-runs immediately when anything changed (snapshot present).
  Safety: edits can never move mail — regenerated cohorts are drafts
  needing Teams approval. Guardrail tests pass (apply-ops + full suite).
- **Deal-vocabulary overhaul (per James):** everything user-facing now
  speaks the user's own language via _matter_noun() — "deal" for Matt
  (set as "noun": "deal" in his matters.json; matter_noun in config also
  works), default "case". Flows into cohort descriptions ("closed deal —
  archive wholesale", "outside any known deal thread", "long-running
  deal"), Teams proposals (which render cohort text), and the digest
  prompt (told to say deals/projects, never cases/matters). Chat intro,
  proposal scaffolding, and rules seed were already vocabulary-neutral.
  Also fixed: config.example.json had a duplicate inbox_cleaner_dir key
  (last-wins in JSON → example resolved to "").
- **NEW: matter + sender-route passes** (built after Matt's first --analyze
  produced only 3 generic cohorts). His 198,736 messages = 106,877
  conversations with ZERO thread families ≥150 — the "big case cluster"
  heuristic is litigation-shaped and never fires for a deal lawyer. Two
  per-user JSON files in the share folder now drive user-specific passes
  (templates in _templates\): matters.json (client/matter/keywords/closed —
  subject keyword match, whole-conversation claims, Client\Matter folders,
  closed deals = no age floor) and sender_routes.json (standing routes like
  SEIA → "SEIA Folder" + never_bulk_domains so Box/ShareFile/DocuSign
  notices are never swept to Deleted Items). Both seeded for Matt from his
  questionnaire (25 matters, 4 routes). Safety layers added: matter senders
  excluded from the newsletters pass (his deal counterparty was drafted as
  a "newsletter" in testing — unread external bulk-ish ledger); execute now
  claims messages in kind-priority order (matter → big_case → route →
  court → newsletters → internal), draft order breaking ties within a kind
  (seq field), with pending AND declined cohorts still shielding their
  messages from lower passes. --analyze prunes undecided drafts the current
  rules no longer produce (self-cleaning regeneration). Bug fixed:
  "efile" marker substring-matched sharefile.com → ShareFile drafted as a
  court sender. Synthetic 12-check test passed (scratchpad). Matt's
  cohorts.json drafts regenerate on next --analyze on the laptop.
- **Snapshot/execute token-refresh fix.** Matt's first laptop snapshot
  died at 139,100 messages with http_401 after ~65 min — the app token
  (client credentials) expires at ~1h and snapshot() never refreshed it.
  Both snapshot() and execute() now re-acquire the app token on a 401
  and retry (guarded: a 401 within 120s of a fresh token = real
  permission problem, still aborts/fails). Events:
  snapshot_token_refreshed / execute_token_refreshed. Cursor checkpoint
  worked as designed — re-run resumes from 2024-09-25.
- **NEW: daily digest command** (`--inbox-<user> --digest [--hours N]`,
  built this session at James's request). Plain-English summary of the
  window's inbox-cleaner activity, emailed from rocky@ to the mailbox
  owner + observers (James), styled like the daily case digest (same
  card HTML, rocky icon cid). Deterministic fact aggregation from
  activity/comms/moves logs → one Claude call to phrase it ("batch",
  never "cohort") → deterministic fallback markdown if the API call
  fails (verified: dev's broken key exercised the fallback on real
  data). Quiet window = no email AND no API call. Digest's own events
  (digest_sent etc.) are excluded from the activity test so a sent
  digest never makes the next day look active. Schedule once daily on
  the laptop after --rules-update. rocky.exe rebuilt + deployed.

- **Matt answered the questionnaire** (reply landed 2026-07-09 03:12 UTC).
  Ran `--inbox-matt --questionnaire` from dev: all **10/10 answers parsed**,
  saved to `Rocky Inboxes\inbox-matt\questionnaire_answers.md`, confirmation
  ack emailed to Matt. Dev state now has `q_answered_at` — future
  questionnaire runs are a no-op.
- **Parser bug found + fixed**: the questionnaire HTML placed the outro
  paragraph ("From here: I'll analyze...") between answer box 10 and the
  "(End of questionnaire)" sentinel, so the quoted-back outro bled into
  Matt's answer 10. Fixed `_questionnaire_html` (sentinel now emitted
  before the outro) and hand-cleaned answer 10 in the saved answers file.
  Fix only matters for the *next* user's questionnaire — needs a rebuild
  /deploy before then, no urgency.

**Open items / watch-outs**

- **Rules fold DONE on the laptop 8:39 AM** (dev config.json's
  `anthropic_api_key` is invalid — 401 — so it couldn't run from dev; fix
  the dev key eventually). rules.md now carries all 10 questionnaire
  answers as standing preferences; verified faithful. Laptop state file
  updated with q_answered_at (staged via OneDrive, James copied it), so
  `--questionnaire` runs are no-ops everywhere.
- Console logging crashed (cosmetic UnicodeEncodeError, cp1252) printing
  Matt's reply — zero-width chars in his signature. Processing unaffected;
  flagged for a separate fix.
- **Laptop share path gotcha (resolved same morning):** on the Rocky
  laptop, James's shared OneDrive folder mounts as
  `...\OneDrive - gejlaw.com\James D. Bragdon's files - Program Files\`,
  NOT plain `Program Files\` (that path is rocky@'s own unshared
  OneDrive). First laptop config used the plain path → rules-update saw
  an empty folder, skipped, and created an orphan tree (deleted).
  Correct `inbox_cleaner_dir` recorded in INBOX_CLEANER.md. Any future
  laptop config path pointing at the share must use the
  "James D. Bragdon's files -" prefix.

## Session 2026-07-05 (5) — Deploy for laptop + James joins the approval chat as observer

**What changed**

- **Approval chat is now a GROUP chat** (Rocky + mailbox owner + observers)
  so James can watch and learn as it runs. New `observers` key per
  inbox_users entry (default: [user_email] = James; [] = true 1:1).
  teams.py grew ensure_group_chat (NOT idempotent — Graph creates a new
  group chat per POST; chat_id persisted in state guards this) and
  list_chat_members. chat_cycle resolves the OWNER's Teams user id from
  the member list; **only owner replies decide cohorts** — observer
  messages are logged to communications.jsonl but never approve anything;
  unresolved owner = no decisions (fail-safe). Intro message on chat
  creation explains the rules to the user.
- **Built + deployed rocky.exe and dashboard.exe** to OneDrive Program
  Files (first deploy since the 7/04 Maple changes — dashboard rebuild
  watch-out now cleared). build_exe.py bundles inbox_cleaner.py +
  teams.py. rocky.exe REBUILT after the group-chat change (first build
  raced the edit).
- Matt had NOT yet answered the questionnaire — his "Re: Experiment"
  email to James was consent + "do I just respond to Rocky's email?"
  (answer: yes). Questionnaire state (q_sent_at 2026-07-05T17:49:43Z)
  must be copied to the laptop's state dir before running --questionnaire
  there, or it will re-send (see INBOX_CLEANER.md / session (4) notes).

- **Share folder relocated** (per James): Rocky Inboxes now lives at
  OneDrive `Program Files\Rocky\Rocky Inboxes` (config `inbox_cleaner_dir`,
  set per machine — dev jbragdon profile / laptop rocky profile). Existing
  inbox-matt data moved from the old Rocky-Cases-sibling location. Rides
  the already-pinned Program Files tree, keeps Matt's data out of the
  browsable Rocky Cases tree. Blank config = old sibling fallback.

**Open items**

- Laptop setup checklist (given to James): config inbox_users block +
  inbox_cleaner_dir (rocky-profile path), state file copy, Maple Digest
  scheduled-task time check (before 4:30 PM 7/06!), then --inbox-matt
  --snapshot on the laptop.
- First real questionnaire reply still pending; verify parsing when it
  lands.

## Session 2026-07-05 (4) — inbox-matt goes live: permissions verified, questionnaire SENT

**What changed**

- **Matt = Matt Pirnot, Mpirnot@gallagherllp.com** (config.json updated).
  He's a transactional deal lawyer — questionnaire template rewritten for
  deals/projects (big deals, closed deals, automated notices like
  DocuSign/data-room/wire confirmations instead of court e-filing,
  active-negotiation never-touch, deal-rhythm age line). No code change;
  analyzer's court-notice pass will simply stay quiet on his mailbox and
  DocuSign-type senders surface via the sender ledger.
- **Permission check ran on the Rocky laptop** (PowerShell probe against
  Graph with Rocky's own creds). Results:
  - App roles: Mail.Read only (application Mail.ReadWrite still needed at
    execute time — the ONLY outstanding permission in the project).
  - **Matt's mailbox is ALREADY covered by the Application Access Policy**
    — snapshot can run with zero IT action.
  - **Teams chat scopes ALREADY consented** (Chat.Create, Chat.ReadWrite,
    ChatMessage.Send — plus, notably, delegated Mail.ReadWrite/.Shared,
    Mail.Send, Calendars.ReadWrite all in rocky@'s grant). NO Caudill
    email needed.
- **Questionnaire SENT to Matt** 2026-07-05 1:49 PM from rocky@ via
  --inbox-matt --questionnaire on the dev laptop (device-code sign-in as
  rocky@ — token now cached on dev, so dev can run questionnaire checks
  and Teams cycles without re-auth).

**Watch-outs / open items**

- rocky@'s password reset ~6/16 revoked the laptop's OLD cached token,
  but the cache held a second, newer token that is ALIVE — scheduled jobs
  were never broken (rocky.log clean). Dead entry is harmless.
- Dev config.json has NO client_secret → --snapshot (app token) can't run
  from dev yet. Either copy client_secret from the laptop's config or run
  snapshot on the laptop — which requires a rocky.exe rebuild/deploy
  (build_exe.py), and deploy ships the 7/04 Maple changes: check the
  stale "\Rocky\Maple Digest" scheduled-task watch-out first.
- Next: re-run --inbox-matt --questionnaire periodically (manually or
  scheduled) to catch Matt's reply; verify the first real Outlook reply
  parses (raw reply lands in communications.jsonl either way).

## Session 2026-07-05 (3) — Inbox Cleaner: questionnaire moves to email with answer boxes

**What changed**

- **Questionnaire is now an EMAIL, not a Teams message** (per James — too
  many questions for chat back-and-forth). New `--inbox-<user>
  --questionnaire [--resend]` subcommand in inbox_cleaner.py:
  - Sends from rocky@ via `outbound.send_mail_guarded` (HTML, internal
    recipient — allowlist-clean, NO new permissions; can run before any
    IT steps).
  - HTML renders each question with a bordered answer box carrying an
    `Answer [iq-N]:` marker; user types in the box and hits Reply.
  - Idempotent cycle: not sent → send; sent → poll rocky@'s inbox for a
    reply (from = user, subject contains "Rocky Inbox Cleaner
    questionnaire"); parse answers with the Maple-digest marker-scan
    pattern (adapted from pma_tracker._parse_digest_answers: stop at next
    marker / "(End of questionnaire)" / next question heading / quoted
    From: block); save to share `questionnaire_answers.md`; log full
    reply to communications.jsonl; email a short confirmation; go quiet.
  - Unparseable reply → logged as questionnaire_reply_unparsed for James;
    empty boxes parse to nothing (quoted-back original is harmless).
- **Teams questionnaire path removed** from chat_cycle (`--send-
  questionnaire` flag gone) — Teams now carries only cohort proposals.
- Template `_templates/inbox_questionnaire.md` is now PARSED, not sent
  verbatim: paragraphs starting "N." are questions; intro/outro around
  them. Per-user copy in the share folder overrides the repo template.
- `--status` shows emailed/awaiting/answered; rules_update event filter
  updated to the new questionnaire event names.

**Decisions made**

- Reply detection: rocky@'s own inbox, delegated token (Mail.Read +
  Mail.Send already in GRAPH_SCOPES) — the questionnaire leg has zero IT
  dependencies.
- Answers reach rules.md through the existing nightly --rules-update
  (comms + questionnaire events already in its input) — no new Claude
  call.

**Watch-outs**

- Smoke test extended: template split (10 questions), HTML build (all 10
  markers + sentinel), reply parsing (answered boxes 1/5/6 extracted,
  empty boxes skipped, ">"-quoted answer text cleaned, quoted headings and
  From: block don't bleed into answers). py_compile clean. NOT tested
  live: actual Outlook reply rendering — verify the first real reply
  parses before relying on it (raw reply is always in
  communications.jsonl as a fallback).

## Session 2026-07-05 (2) — Inbox Cleaner: first user is Matt, not Paul

**What changed**

- Per James: the process launches with **Matt** (`--inbox-matt`), not Paul.
  No code-logic change — the build was per-user generic. Renamed the
  config block (config.json + config.example.json: inbox_users.matt,
  placeholder mailbox still to fill), rewrote INBOX_CLEANER.md's
  operational steps around Matt, and swapped the paul→matt examples in
  rocky.py / inbox_cleaner.py docstrings + help text.

**Open items**

- Need Matt's real mailbox address in config (and his full name for
  display_name, currently just "Matt").
- Paul is unstarted, not cancelled: adding him later = one inbox_users
  block + the same IT steps (BUILD_REFERENCE notes the switch).

## Session 2026-07-05 — Inbox Cleaner: build (inbox_cleaner.py, teams.py, --inbox-<user>)

**What changed**

- **`inbox_cleaner.py` (new, ~900 lines):** the whole per-user process.
  `--snapshot` (folder tree + inbox metadata → local JSONL, cursor-
  checkpointed per page, resumable, app-token Mail.Read); `--analyze`
  (sender/conversation ledgers, draft cohorts — big_case / court_notices /
  newsletters / internal_office — review workbook via openpyxl; re-runs
  never clobber decided cohorts, match_key dedupe); `--chat` (one Teams
  poll cycle: log all replies, resolve pending proposal YES/NO/folder-name,
  send questionnaire once with --send-questionnaire, propose next cohort —
  ONE at a time); `--rules-update` (the daily Claude call: folds decisions +
  chat into rules.md with rules_history/ backups; quiet day = no API call);
  `--execute` (dry-run default; --live moves via POST /move, checkpointed
  to moves.jsonl with source folder for undo; --limit N for first batches);
  `--status`.
- **`teams.py` (new):** delegated Teams transport (rocky@ device-code,
  TEAMS_SCOPES separate from GRAPH_SCOPES so mail runs never prompt for
  chat consent; TeamsNotEnabled degrades gracefully). ensure 1:1 chat,
  send, fetch-since-cursor, throttle-aware. Transport only — shared with
  the future Remy-via-Teams feature.
- **rocky.py:** dispatch any `--inbox-<user>` flag (lock normalizes to the
  process name regardless of subflag order); docstring + help text.
- **config:** `inbox_users` block (paul, placeholder mailbox — CLI refuses
  PASTE values) + `inbox_cleaner_dir` in config.example.json and dev
  config.json.
- **`INBOX_CLEANER.md` (new):** operational guide — ordered start steps
  (IT: add Paul's mailbox to the Application Access Policy → Teams
  delegated scopes on the app registration → James: config + snapshot +
  analyze → Paul: questionnaire → 15-min --chat schedule + nightly
  --rules-update → Mail.ReadWrite application permission LAST), safety
  properties, storage map, maintenance mode.
- **`_templates/inbox_questionnaire.md` (new):** 10-question onboarding,
  written to Paul, sent verbatim over Teams.

**Decisions made**

- Mailbox access = app token + Application Access Policy (email-brain
  pattern), NOT Exchange delegation — lighter IT lift per colleague.
- Storage split: machine data local (C:\Rocky\inbox_cleaner\<user>\
  snapshot/moves), human-facing on OneDrive (Rocky Inboxes\inbox-<user>\
  rules.md, cohorts.json, activity.jsonl, communications.jsonl, workbook).
- Unclear Teams reply → cohort parked as needs_review (nothing moves,
  James adjudicates); bare "yes" on a label-needed cohort is unclear.
- Newsletters cohort dispositions to well-known folder "deleteditems";
  case/internal folders are created under the user's Inbox at execute time.

**Open items**

- Fill Paul's real mailbox in config; IT steps 1 (access policy) and
  2 (Chat.Create/Chat.ReadWrite/ChatMessage.Send + admin consent) before
  first snapshot/chat. Mail.ReadWrite application permission only after
  cohorts approved + dry-run reviewed.
- Not built (see BUILD_REFERENCE open items): Claude residual pass,
  proposal reminders/timeouts, conversation-sort pass, dashboard entries.
- Deploy needs a rocky.exe rebuild (build_exe.py) when this goes live.

**Watch-outs**

- Verified: py_compile on rocky/inbox_cleaner/teams; offline smoke test
  (synthetic 2,750-msg snapshot → 4 cohorts with exact expected counts,
  age floors, purely-internal exclusion of external-cc mail, dedupe,
  re-analyze idempotence, reply interpretation, rules provenance,
  execute id-matching); CLI guards for missing/placeholder users. NOT
  verified (needs IT): live Graph snapshot, Teams round-trip, live moves.
- fetch_folder_tree recurses in Python — very deep folder trees would be
  slow but fine at law-firm scale.

## Session 2026-07-04 (4) — Inbox Cleaner: process design (supersedes Paul Inbox Review)

**What changed**

- **BUILD_REFERENCE.md only** (no code): replaced the "Paul Inbox Review"
  two-pass design with the new **Inbox Cleaner** process spec, generalized
  for any colleague with a 200k+ message inbox (Paul is first user).
- **Lifecycle framing:** the deep clean is phase one of a permanent
  per-user process ("paul-inbox", "matt-inbox"). Onboarding questionnaire
  seeds a per-user rules file; every approved cohort persists as a rule
  (declined ones as negative rules); recurring maintenance runs apply
  learned rules to new mail, proposing only novel cohorts. Rules file is
  plain-English + structured match fields, per the instructions.md
  incremental-teaching pattern.
- Design shape: funnel — metadata snapshot (JSONL, `Mail.Read` only,
  checkpointed paged Graph fetch, no bodies) → sender + conversation
  ledgers → cohort-approval workbook (rules covering thousands of
  messages, not per-message rows) → checkpointed execution under
  `Mail.ReadWrite`, every move logged for undo-by-replay.
- Categories: big-case clusters (human labels each cluster once),
  internal office traffic → Office Misc. Archive, newsletters/spam →
  Deleted Items, Claude batch pass on ambiguous residual (metadata-only).
- **Teams approval loop:** Rocky proposes each cohort to the inbox owner
  in a 1:1 Teams chat ("5,012 'Angelos' messages → Angelos folder, OK?");
  a "yes" queues it for execution. Shares prerequisites with the
  human-in-the-loop Remy proposal (Teams scopes + pending-job state
  machine) — whichever builds first creates the skeleton for the other.
  Workbook stays as audit artifact / bulk-review option.

**Decisions made**

- Age floors are **per category**: 6 months for case mail and internal
  office traffic; **none** for newsletters/spam.
- **Flat case folders** — no date-based subfolders; all of a case's mail
  in one folder for later searching.
- Newsletters are **moved to Deleted Items**, not hard-deleted (James
  initially said delete; kept the nothing-is-ever-deleted rule — same
  clean-inbox outcome, recoverable until retention purges).
- Whole snapshot/analyze/approve cycle runs under `Mail.Read`;
  `Mail.ReadWrite` granted only when execute code exists and a workbook
  is approved.
- Per-user config from day one (multi-colleague is the point).
- **Stays in Rocky** — a Claude managed-agent architecture was considered
  and declined: recurring scheduled runs + permanent rule accumulation
  fit Rocky; agent sessions are amnesiac between runs; the 200k bulk
  mechanics need script-driven Graph paging/moves regardless.
- Teams safety rules: cohorts proposed one at a time (or Adaptive Cards
  with cohort_id) so "yes" is unambiguous; only the mailbox owner's
  replies count; a reply can only approve/decline a proposed cohort,
  never define a new move.

**Open items**

- CLI flag naming (suggested `--inbox-clean --snapshot|--analyze|--execute
  --user <mailbox>`), workbook schema, Claude batch-pass prompt, per-user
  config format, Teams approval UX (plain-text vs Adaptive Cards;
  reminders/timeouts for unanswered proposals).
- IT prerequisites when building: Exchange-level delegation of Paul's
  mailbox to rocky@ (same pattern as the existing mailbox delegations);
  Teams chat scopes (Chat.Create / Chat.ReadWrite / ChatMessage.Send) +
  app-registration change — same gate as the Remy-via-Teams proposal.

## Session 2026-07-04 (3) — Email brain: drop rocky@ "James Older Sent" folder for now

**What changed**

- **config.example.json only:** removed the
  `{"mailbox": "rocky@gallagherllp.com", "path": "Inbox\\James Older Sent"}`
  entry from `sent_brain_folders` — James hasn't moved the archived sends
  into rocky@ yet, so the brain starts from jbragdon@'s Sent Items alone.
  The `_sent_brain_folders_comment` now carries the full "add this entry
  later" recipe (folder to create, exact JSON to paste back).
- No code change: rocky.py already resolves folders from config and skips
  unresolvable ones; per-folder cursors mean the rocky@ folder can be added
  later and will backfill on its own without re-pulling Sent Items.

**Open items**

- When the older sent emails are dragged into rocky@ `Inbox\James Older Sent`,
  re-add the entry to `sent_brain_folders` (in the deployed config.json on
  the Rocky laptop too, if it was copied there with the old two-folder list).

## Session 2026-07-04 (2) — Dashboard: Maple Updater trigger, last/next-run display, plainer plain-English

**What changed**

- **Dashboard "Maple Updater" command (external).** New registry entry between
  maple-pma-activity and maple-digest, so the Maple group reads as the 3-step
  daily loop (15:30 collect → 16:00 updater → 19:00 digest). It runs the
  Maple repo's `run-daily-update.ps1` via
  `powershell.exe -NoProfile -ExecutionPolicy Bypass -File` — not a rocky.py
  flag. New config key `maple_updater_script` (default IN CODE = production
  rocky-profile path, same pattern as `_DEFAULT_MAPLE_OUTBOX_DIR`; dev
  config.json + config.example.json set the jbragdon path). Registry entries
  now support `external: True`: argv from `external_argv()`, running-state
  from a tracked Popen (no rocky lock file), **left out of ⚡ Auto-setup on
  purpose** — the Maple side may already have its own Task Scheduler entry;
  creating a second would double-run it (updater is idempotent via its
  processed-cursor, but still).
- **Last ran / next run everywhere.** Each Run-a-Command row now shows
  "Last ran: 2h ago (today 7:50 AM) · Next run: today 4:00 PM". Sources:
  rocky commands' lock files are truncate-rewritten at every start, so lock
  mtime = last start (covers manual AND scheduled runs); the updater uses the
  newest `logs\scheduled_run_*.log` mtime; schtasks "Last Run Time" is the
  fallback. Next run = earliest enabled matched task. Matching: parse
  `--<flag>` (or run-daily-update.ps1) out of the task's "Task To Run".
- **schtasks query rewritten:** one full `/query /fo CSV /v` (the \Rocky\
  -folder-only fast path missed the Maple-side task) with a 20 s TTL cache
  (it's polled every 10 s); mutations invalidate; ↻ button forces fresh.
  Keeps tasks named rocky/maple or running run-daily-update.ps1; skips /v's
  repeated header rows; dedupes multi-trigger tasks; each task carries
  `flag`/`group`. Scheduled Tasks card now renders grouped in registry order
  (Maple together) with friendly dates ("today 4:00 PM"); schtasks'
  11/30/1999 never-ran sentinel → "never" (guarded server + client side).
  delete_task guard now allows "maple" names too.
- **Plainer plain-English mode:** rules rewritten against real rocky.log
  lines (maple-digest drafting, dry-run outcomes by reason code,
  client_secret error, stale-outbox warning); command descs de-jargoned
  (Maple group is "Step 1/2/3 — ..."); post-cleanup strips quotes around
  emails/filenames and turns "update(s)" into "updates".

**Watch-outs**

- Deploy needs a dashboard.exe rebuild (`build_exe.py`) — template + code.
- On the laptop, check whether the Maple updater already has its own
  scheduled task before 📅-scheduling it from the dashboard: it now SHOWS UP
  in the Scheduled Tasks card (query matches maple/run-daily-update), so if
  it's there, manage that one rather than adding a \Rocky\ twin.
- "Running now" for the updater only tracks dashboard-launched runs (no lock
  file); a Task-Scheduler-launched updater run won't light the dot.
- Verified: py_compile; live dashboard on dev — Maple group order, last/next
  lines, task grouping, create + delete of a \Rocky\Maple Updater task via
  the API round-trip (created 16:00 daily, matched flag/group/next-run,
  deleted; dev machine left with no Rocky tasks). Updater itself NOT run
  (would write HubSpot).

---

## Session 2026-07-04 — Maple digest goes daily + 3-stream: code edits, HubSpot updater record, abstracts

**What changed (continuation 5 — internal digest DELETED; client digest renamed "Maple Digest")**

- **The internal Maple digest is gone** (per James — one digest is enough).
  Removed from rocky.py: the whole 3-stream email subsystem built earlier
  today (`maple_daily_digest`, `run_maple_digest_cli` (old), all
  `_read_*`/`_build_*` digest helpers, `_read_jsonl_records`,
  `_DEFAULT_MAPLE_LOGS_DIR` / `_UPDATER_LOGS_DIR` / `_ABSTRACTS_DIR`,
  `_DEFAULT_MAPLE_DIGEST_RECIPIENTS`, `MAPLE_DIGESTS_DIR`,
  `MAPLE_LOGO_PATH`). Config keys `maple_logs_dir`, `maple_updater_logs_dir`,
  `maple_abstracts_dir`, `maple_digest_recipients`,
  `maple_digest_skip_if_empty` retired (removed from config.example + dev
  config). "daily activity digest" subject marker dropped from
  `_DIGEST_SUBJECT_MARKERS` (smoke test flipped to assert non-match).
- **`--maple-client-digest` renamed `--maple-digest`** (flag, label "Maple
  Digest", functions `maple_digest`/`run_maple_digest_cli`, log tag
  `[maple-digest]`). It was never deployed, so no alias. **Config keys keep
  the `maple_client_digest_*` prefix on purpose** — the retired internal
  digest used `maple_digest_recipients` for a firm-internal list; reusing
  that key for the client draft could silently repoint the audience if an
  old config.json lingers (comment in code says so).
- Dashboard: Maple group is now maple-pma-activity (15:30) + maple-digest
  (19:00, recommended). PROCESS_NAMES: `maple-digest` primary; `maple` /
  `maple-client` kept as legacy mappings for old log lines.
- **Left in place, now consumer-less (kept deliberately):** Maple's
  `src/abstractLog.js` abstract audit log (standalone value, tiny);
  Maple's `logs/activity` session logging (a Maple-side feature, not
  Rocky's); `make_maple_logo.py` + Icon/maple_logo.png (unused).
- **WATCH-OUT for deploy:** if a "\Rocky\Maple Digest" scheduled task
  already exists on the laptop (auto-setup used to create one at 16:30), it
  now runs the NEW --maple-digest (client drafting) at the WRONG time —
  move it to 19:00 or delete/recreate via the dashboard before relying on it.
- Verified: py_compile + smoke tests pass; dry-run `--maple-digest --date
  2026-07-03` drafts-to-list correctly under the new name.

**What changed (continuation 4 — client digest drafted to James's Drafts + reply sweep)**

- **New command `--maple-client-digest` (7:00 PM daily, dashboard Maple
  group).** Takes the Maple Updater's `outbox\client_digest_<today>.html` and
  creates a **DRAFT** in jbragdon@'s Drafts folder (reuses
  `pending_llt.create_draft_email`, delegated token) addressed to
  bcrassweller@/kwoelper@ (Bozzuto) + jbragdon@/kvirtue@ — James reviews and
  sends manually, since Rocky's outbound allowlist forbids external
  addresses (Level 0 preserved: drafting ≠ sending). Subject
  `Maple — PMA ticket updates M/D/YYYY` (matches the reply-sweep detector).
  On success the file moves to `outbox\sent\` (idempotency per the outbox
  contract); stale earlier-dated files are warned about, never drafted; no
  file = quiet day. Config: `maple_outbox_dir`,
  `maple_client_digest_recipients`, `maple_client_digest_mailbox`.
- **Client digest always CCs pma@bozzuto.com** (`maple_client_digest_cc`,
  revised same session per James). The cc is load-bearing: pma@bozzuto.com's
  existing routing delivers Beth's reply-all into rocky@'s watched
  "Inbox\PMA emails" folder, so client replies ride the NORMAL export path
  (answer boxes parsed by build_activity_record there too). Caveat: a plain
  Reply (not reply-all) goes only to James and skips the route — forward
  those to the PMA folder by hand.
- **No inbox sweep at all** (final revision, same session — supersedes the
  two intermediate sweep designs, which were built then removed). Client
  replies reach the feed via the pma@bozzuto.com cc → rocky@'s watched
  "Inbox\PMA emails" folder → the NORMAL export path, where
  `build_activity_record` already stamps `digest_reply` +
  `question_answers` on every record. `run_pma_activity` is back to its
  original single-source structure. Consequence: replies to the INTERNAL
  digest (they go to rocky@'s Inbox root) reach the feed only if a rocky@
  Outlook rule moves "Maple —" replies into the PMA folder — optional, the
  internal loop was a bonus.
- **Maple's judgment now consumes the structured answers** (Maple repo):
  `normalize_record` passes `digest_reply` / `question_answers` through to
  the agent payload (previously stripped — verified they now survive), and
  both the API_MODE_OVERLAY (pma_shadow_draft.py) and AGENT_PROMPT.md
  "Resolve" section instruct: each {id, answer} is the authoritative answer
  to exactly that open-question id — resolve by id, no content-matching;
  `digest_reply` without parsed answers = read the body against
  open_questions as before.
- **Maple's client digest got the answer boxes** (`pma_shadow_draft.py`
  `build_client_digest_html`): questions card now renders `Q:` prefix +
  `Answer [q-...]:` label + typing box per question + `(End of client
  questions)` sentinel — same parsing contract as the internal digest. The
  "type your answers" hint deliberately sits ABOVE the questions: anything
  between the last box and the sentinel would parse as that box's answer.
- **outbox/README.md rewritten** to the drafted-not-sent contract.
- Verified: dry-run drafting against the real 7/03 outbox file (recipients +
  subject correct), stale-file warning + quiet-day exit for 7/04, and a full
  round trip — real `build_client_digest_html` output (live question
  register) → crude HTML→text → answers typed into first/last boxes →
  `_parse_digest_answers` returns exactly those two; unanswered digest
  parses to []. Not run live (no client_secret on dev; draft creation
  untested against Graph — first live smoke on the laptop: ▶ Maple Client
  Digest with dry, then live).
- **Supersedes the "Wire Rocky to email Maple client digest to Beth" task
  chip** and implements ROCKY-UPDATE-PROMPT.md's two responsibilities in
  draft-form (send → draft; Outlook-rule routing → inbox sweep).

**What changed (continuation 3 — client questions section + reply-answer loop)**

- **Digest: "Questions for the client" is its own section**, right after the
  HubSpot updates. Sourced from the updater's `questions.jsonl` — ALL open
  questions (they repeat until answered), not just today's — each rendered as
  `Q: <matter> — <question>`, an `Answer [q-YYYY-MM-DD-n]:` label, and an
  empty dashed box to type into; section ends with an
  `(End of client questions)` sentinel. Questions *asked* today no longer
  render in the HubSpot section (answered-today items still do). Open
  questions count in the summary bar but NOT toward skip_if_empty (standing
  state, not day activity).
- **Exporter: digest replies parsed for answers** (`pma_tracker.py`). Every
  record body is scanned for `Answer [q-...]:` labels; text typed under a
  label (up to the next label / next `Q:` line / the sentinel) is cleaned
  (reply-quote `>` prefixes, &nbsp;, underscore runs) and shipped on the feed
  record as `question_answers: [{id, answer}]` + `digest_reply: true` (flag
  also set on subject match: "maple" + "daily activity digest" or "pma ticket
  updates" — covers the future Beth client digest too). Empty quoted-back
  boxes parse to nothing; duplicate ids keep the first (newest) occurrence.
  Additive optional fields — feed schema otherwise unchanged (Maple's parser
  tolerates extra keys).
- **The label format + sentinel are a cross-file contract** between
  `_build_client_questions_section_rows` (rocky.py) and
  `_parse_digest_answers` (pma_tracker.py) — comments on both sides say so.
- smoke_test_pma.py extended: answer extraction, blank-box rejection,
  duplicate-id handling, subject detection, and end-to-end record flow — all
  pass. Rendered digest verified in browser against the live register
  (3 open questions).
- Graph fetch already requests text bodies (`Prefer: outlook.body-content-type
  ="text"`), so replies arrive as plain text with labels/sentinel intact.

**What changed (continuation 2 — HubSpot section compact table format)**

- Applied HubSpot updates now render as one three-column row per change
  (per James): **Was** (muted, with a tiny uppercase field caption, old value
  truncated ~110 chars) | **Updated:** bold new value (~280 chars) |
  explanation (small gray: rationale ~160 chars + confidence). Still verbatim
  from the updater's record — truncated, never paraphrased. Ticket-name
  headers unchanged; candidates/questions/blocked keep their one-line format.
- Added a `digest-preview` entry to `.claude/launch.json` (untracked) —
  `python -m http.server 5058 -d maple_digests` for eyeballing digest HTML.

**What changed (continuation — Maple grouping/rename)**

- **`--pma-activity` renamed `--maple-pma-activity`** to group the Maple jobs.
  Legacy `--pma-activity` kept as a permanent alias; `main()` normalizes it to
  the canonical name BEFORE `acquire_instance_lock`, so both spellings share
  `state/rocky_maple-pma-activity.lock` and can't run concurrently (verified:
  alias run creates no `rocky_pma-activity.lock`). Existing Task Scheduler
  entries using the old flag keep working unchanged.
- **Dashboard "Maple" group** (`dashboard.py` registry): Maple PMA Activity
  (15:30, now `recommended` — the chain is documented) + Maple Digest (17:30),
  listed in run order; groups now render Cases / Inbox / Maple / Other. The
  Maple Updater's own ~16:00 task (run-daily-update.ps1) sits between them but
  is NOT a Rocky command — noted in the registry comment.
- **Log tags unified under Maple:** `[pma]` / `[pma-activity]` →
  `[maple-pma]` in pma_tracker.py + rocky.py. Plain-English log: tag regex
  fixed to accept hyphens (`\w+` never matched `[pma-activity]` at all), and
  PROCESS_NAMES maps maple-pma + legacy pma/pma-activity → "Maple — PMA email
  feed", maple → "Maple — daily digest", so old log lines group correctly too.
- Internal names deliberately NOT renamed (feed/state contract):
  `pma_activity_feed.jsonl`, `pma_activity_state.json`, `pma_activity_*`
  config keys, `run_pma_activity*` functions.
- Verified: py_compile, smoke_test_pma.py all pass; dashboard run live —
  /api/commands + Run card show the Maple group; legacy `[pma]` lines render
  as "Maple — PMA email feed"; both flags dispatch (dev machine stops at the
  expected missing client_secret).

**What changed**

- **`rocky.py` — `--maple-digest` expanded from one stream to three.** The digest
  email now covers: (1) app code-edit activity (existing, unchanged); (2) the
  **HubSpot ticket updates the Maple Updater wrote that day**, read from
  `Maple updater agent\logs\digest_YYYY-MM-DD.jsonl` and rendered **verbatim**
  (grouped by ticket: field, old→new value, confidence; plus new-ticket
  candidates, client questions asked/answered, blocked/failed/unmatched) — no
  Claude paraphrase, so it's an accurate record of the writes; (3) **PMA
  abstracts generated with the abstractor**, read from
  `Maple\logs\abstracts\YYYY-MM-DD.jsonl`. New config keys
  `maple_updater_logs_dir` / `maple_abstracts_dir` (code defaults = Rocky-laptop
  production paths, same pattern as `maple_logs_dir`). Refactored JSONL parsing
  into `_read_jsonl_records`; summary bar and dry-run log now lead with HubSpot
  update + abstract counts; footer notes the three sources. Empty-day +
  `skip_if_empty` logic now spans all three streams; a missing activity or
  updater *folder* still warns (missing daily updater *file* = normal no-run
  day; missing abstracts folder = none generated yet, never a warning).
- **Maple app (`src/abstractLog.js` new, `src/server.js`)** — Maple previously
  kept NO record of generated abstracts (the .docx went only to the browser).
  `handleGenerate` now best-effort appends one JSONL line per successful
  abstract (`ts`, `user`, `machine`, `fileName`, `docxFileName`, `provider`,
  `model`, `pages`, `characters`, `warnings`) to `logs\abstracts\<date>.jsonl`.
  A logging failure can never fail the generation response.
- **`dashboard.py`** — maple-digest slot moved 16:30 → **17:30** and desc
  updated: the digest must run AFTER the Maple Updater's daily HubSpot run
  (today it ran ~16:00–16:30) or the day's ticket updates aren't in the record
  yet. Daily order: pma-activity export → Maple updater run → maple-digest.
- **`config.json` (dev) + `config.example.json`** — added the two new dirs; also
  fixed the dev maple paths from the stale doubled root
  (`OneDrive\OneDrive - gejlaw.com`, an unsynced legacy copy — same trap as the
  60a90e6 build_exe fix) to the live `OneDrive - gejlaw.com` root.

**Decisions made**

- HubSpot section is rendered verbatim from the updater's own digest record
  (the authoritative log its `apply` step writes), not summarized by Claude —
  "accurate record" was the requirement.
- rocky.py's old "no Maple code is touched" note is superseded: a one-file
  additive logger in Maple was the only way to get an accurate abstract record.
- Abstract log lives beside the activity logs (`logs\abstracts\`) so Rocky
  reads everything from the one shared Maple folder.

**Open items / watch-outs**

- **NOT COMMITTED, NOT BUILT.** Rocky: needs `python build_exe.py` → OneDrive
  `rocky.exe` (+ dashboard rebuild for the registry change). Maple: server.js
  change takes effect next time Maple is (re)started on each machine.
- **Rocky-laptop Task Scheduler:** if a Maple Digest task already exists at
  16:30, move it to 17:30 (dashboard 🕑 or recreate) — code changes don't touch
  existing tasks. If none exists, 📅/⚡ Auto-setup now creates it at 17:30 daily.
- Abstracts are only captured when Maple runs from the shared OneDrive folder;
  a portable copy logs to its own local `logs\abstracts\` that Rocky can't see.
- Verified by dry-run against 2026-07-03 live data (6 applied HubSpot updates,
  3 candidates, 1 asked/3 answered questions, 1 blocked + 1 unmatched, 1 dev
  contributor) + a synthetic abstract record + a quiet day (2026-06-21).
  Claude narrative call 401'd on the dev laptop (stale API key in dev
  config.json — pre-existing; fallback summary path worked). Not sent live.
- **Still to build (separate task):** ROCKY-UPDATE-PROMPT.md in the Maple
  updater agent folder — Rocky emails the updater's `outbox\client_digest_*.html`
  to Beth daily + routes `RE: Maple — PMA ticket updates` replies into
  `Inbox\PMA emails`. Blocked on Beth's address / cc list (placeholders unfilled).

---

## Session 2026-06-28 — Daily case digest: quiet-case fix, internal/attorney split, file-integrity subsection, unified activity log

**What changed** (all `rocky.py` unless noted)

- **Quiet-case classification fix.** The digest counted *any* `activity.jsonl`
  event as activity, so a no-op scheduled `daily_run` (0 file actions, 0 new raw
  files) made a quiet case render a full "routine folder maintenance only"
  section (the RRID-0007 bug). New `_is_substantive_event()` gates active-vs-quiet
  on *real developments*: no-op `daily_run`, empty `daily_cases_email_summary`,
  `session_start/end`, and `daily_run_error` no longer count. Such cases drop to
  the bottom "no new activity" list. The substantive list (not raw activity) is
  also what's fed to the section builder.
- **Internal vs. attorney next steps.** `daily_run` now emits `internal_suggestions`
  (case-file housekeeping) separately from `recommendations` (attorney actions);
  both logged. The digest's **Recommended next steps** is restricted to
  case-advancing attorney actions; internal maintenance (updating the Status
  Memo, refreshing indexes, re-filing/renaming) is barred and never fed to the
  digest model (`build_case_digest_section` daily_run branch omits
  `internal_suggestions`; prompt rule as backstop).
- **CLAUDE.md "Rocky Suggestions" pointer.** When a daily run produces
  `internal_suggestions`, Rocky idempotently appends a `## Rocky Suggestions`
  section to that case's CLAUDE.md (`_ensure_claude_md_suggestions_pointer`)
  telling a project session to read `internal_suggestions` from `activity.jsonl`
  and ask James before acting. Won't create a CLAUDE.md where none exists. Also
  shipped in `_templates/CLAUDE.md.template`.
- **Tighter "What happened".** Per-bullet cap (~40 words / 1–2 sentences),
  summarize offer terms instead of enumerating line items, and a ban on
  standalone "this establishes…/documents…/shows…" significance-recap bullets
  (the RRID-0012 sample).
- **New "Internal filing follow-ups" subsection** (digest sections now number
  four). File-integrity gaps — a deadline implies a filing that isn't on file.
  Fed by a *real folder inventory* (`_build_case_file_inventory`) that walks
  actual case-folder filenames (names only, so OneDrive placeholders list fine;
  deliberately NOT `master_file_index.json`, which only knows Rocky-filed docs and
  would false-flag human/Cowork-saved filings).
- **Unified activity log → `activity.jsonl` is the single source of truth.**
  CLAUDE.md template rewritten: sessions append a one-line JSON object to
  `activity.jsonl` (schema + UTC timestamp + quote-escaping rules) instead of
  writing `activitylog.md`. Retired `activitylog.md` (template "Activity Log
  Format" section removed, Step 8 + Rocky-Suggestions wording updated). Digest's
  `_read_activity_since` **no longer reads `_spine_text/_activity.json`** — spine
  work logs to `activity.jsonl` like everything else.

**Decisions made**

- `activity.jsonl` is canonical. This restores BUILD_REFERENCE's stated "unified
  audit trail across all actors" design; `activitylog.md` was the later
  divergence that created a digest blind spot (digest never parsed it).
- Spine `_activity.json` separate read dropped deliberately. Its only unique
  value was auto-capture for sessions that couldn't write `activity.jsonl`; under
  the "tell Claude to log" model that's redundant. The spine writer is an external
  Cowork tool we can't redirect from here; a revert note is left in the code.
- File-integrity uses live folder contents, not `master_file_index.json`.
- Internal-vs-attorney enforced at three layers (daily_run prompt split, digest
  renderer omission, digest prompt rule) for defense in depth.

**Open items / watch-outs**

- **NOT YET BUILT OR COMMITTED.** Needs `python build_exe.py` → OneDrive
  `Program Files\rocky.exe` to reach the Rocky laptop. Working tree: `M rocky.py`,
  `M _templates/CLAUDE.md.template` (plus pre-existing `M BUILD_REFERENCE.md`).
- **Per-case log cleanup — DONE (executed 2026-06-28).** New `cleanup_case_logs.py`
  migrated 225 legacy entries (183 spine + 42 activitylog) into each case's
  `activity.jsonl` (deduped via `spine_id`/`alog_key`, `.bak` saved), then archived
  23 legacy files (`activitylog.md`, `_spine_text/_activity.json`) to each case's
  `_archive/`. Idempotent + reversible.
- **CLAUDE.md unification — DONE (executed 2026-06-28).** `unify_claude_md.py`
  rewrote all 22 case CLAUDE.mds in-place (`.preunify.bak` backed up then moved to
  each case's `_archive/`). Family A (Workflow, 13 cases) and Family B (Case Spine,
  7 cases) handled by the script; Eden (RRID-0003, hybrid) handled by a custom
  `fix_eden.py`; Whalen (RRID-0015, numbered-folder outlier) handled with
  `--include-outliers`. All bespoke content preserved. All cases now point to
  `activity.jsonl`; `activitylog.md` and `_spine_text/_activity.json` references
  purged. Rocky Suggestions section added to all 22.
- Fully-quiet days still write *no* digest (existing early-return); more cases now
  demote to no-activity, so this fires more often. The no-activity list only
  renders when ≥1 case has substantive activity.
- JSON-line logging depends on the session emitting valid one-line JSON; malformed
  lines are silently skipped by `_read_jsonl`. If flaky, add a `--log-activity` helper.
- File-integrity + upcoming-dates quality tracks each case's Status Memo hygiene
  (deadlines must actually be in the memo).

---

## Session 2026-06-26 — Retired the PMA HubSpot poller + knowledge synthesis; PMA Activity is the only PMA job

**What changed**

- **Retired the entire HubSpot poller path** (`--pma-poll`, `--pma-digest`,
  `--pma-arm`, `--pma-sleep`, `--pma-test`) — obsolete; Maple now owns ticket
  updates. Removed from **both** layers: rocky.py (CLI handlers, `main()`
  dispatch, `--help`, header docstring) and `dashboard.py` (`ROCKY_COMMANDS`
  registry, so they no longer appear as dashboard buttons/schedules).
- **Retired the corpus/knowledge path** (`--pma-knowledge`) the same way. With it
  gone, the deal-folder corpus had no producer, so the whole classifier/matcher/
  HubSpot/archive/synthesis tree in `pma_tracker.py` became dead.
- **`pma_tracker.py` rewritten down to the activity exporter only** (~1,540 →
  ~360 lines). Kept: `run_pma_activity` + `build_activity_record`,
  `fetch_folder_messages`, `maple_activity_dir`, `_recipients`, and the helpers
  it actually uses (`load/save_pma_state`, `_email_text`, `_append_jsonl`,
  `_fetch_attachments`, `_extract_attachment_text`). Everything else removed.
- **`smoke_test_pma.py` rewritten** to cover the activity exporter offline
  (body/recipient extraction, record building, state round-trip, JSONL append,
  path resolution). Passes.
- **Deleted orphaned files:** `pma_bootstrap.py`, `pma_manifest.json`
  (gitignored — held real HubSpot deal data), `pma_instructions.md`. Nothing
  live read them after the poller/knowledge were removed.
- **Removed `--ella-test`** (`run_ella_test_cli`, the Ella-mailbox-access
  diagnostic) — Ella Digest is working well, so the diagnostic was no longer
  needed. It was dispatch-only (no `--help`/docstring entry). With it gone, the
  `main()` dispatch and the dashboard `ROCKY_COMMANDS` registry now match
  exactly — every command Rocky accepts is also a dashboard button.

**Decisions made**

- Done in three scoped passes (poll+dependents → knowledge → file cleanup), each
  confirmed with James before widening scope.
- **PMA Activity is the sole surviving PMA job.** Rocky exports the "Inbox\PMA
  emails" folder to a JSONL feed; Maple does all classification/ticket work
  downstream.

**Open items / watch-outs**

- **Scheduled tasks on the Rocky laptop are NOT touched by code changes.** If
  PMA Poll/Digest/Knowledge were ever installed in Task Scheduler, delete them on
  the laptop (dashboard 🗑 or `schtasks /delete /tn "\Rocky\PMA Poll" /f`) — they
  will now fail since the flags no longer exist.
- Deletions are staged in the working tree (`D pma_bootstrap.py`,
  `D pma_instructions.md`, `M pma_tracker.py`, etc.) but **not yet committed**.
- `rocky.py` still has its own unrelated `classify_email` (Remy/case path) —
  untouched, not the PMA classifier.

---

## Session 2026-06-25 — Dashboard: run-now buttons + schedule create/edit/delete

(Same-day continuation below adds: plain-English log view, per-command
quick-schedule (📅), and one-click ⚡ Auto-setup of the recommended schedule.)

**What changed (continuation — plain-English log + automatic scheduling)**

- **Plain-English log view (`templates/dashboard.html`).** New
  **View: Plain English | Technical** toggle in the log toolbar (Plain is the
  default). Plain mode runs each line through a `humanize()` layer: friendly
  clock time (`2:43 PM`), level words (`✗ Problem`, `⚠ Heads-up`) instead of
  `[ERROR]`/`[WARNING]`, a purple process tag from the `[maple]/[pma]/…`
  subsystem prefix (case-insensitive → friendly name), Windows paths shortened
  to just the filename, `PLAIN_HIDE` regexes to drop pure noise (separator
  bars, raw `HTTP Request:` calls, token lines), and a `PLAIN_RULES` phrase map
  for the common Rocky messages (dry-run "not sent", "Couldn't reach the AI
  service", "Saved the digest file", etc.). Unmatched lines still show (cleaned
  up), and Technical mode always shows the verbatim raw line — nothing is lost.
- **Automatic-scheduling buttons (`dashboard.py` + template).** Each command in
  the registry now carries `sched_time` / `sched_freq` / `sched_interval` /
  `recommended`. Two new affordances: a per-command **📅** button that opens the
  New-schedule form pre-filled with that command's documented slot, and a
  header **⚡ Auto-setup** button → `POST /api/schedule/setup-recommended` →
  `setup_recommended()` creates the whole standard daily routine in one click
  (idempotent: skips tasks that already exist). The 8 `recommended` commands use
  their **documented** times (Daily Cases 16:00, Daily Run 16:30, Daily/Ella
  Digest 17:00, Steve 07:30, PMA Digest 08:00, Maple 16:30, PMA Poll every
  15 min). pma-activity/pma-knowledge/email-brain have no documented time, so
  they get a *suggested* time on 📅 only and are left out of Auto-setup.
- **Latent bug found via testing:** Flask/Jinja caches the compiled template in
  memory, so template edits need a dashboard restart to show (matters for the
  build→deploy loop, not runtime).

**What changed**

- **`dashboard.py` — added a Rocky command registry + run/schedule backend.**
  `ROCKY_COMMANDS` is the single allowlist of commands the UI can launch or
  schedule (flag, label, group, `dry_run`, optional `danger`). The `flag`
  doubles as the lock-file stem (`state/rocky_<flag>.lock`) so the existing
  running-now indicator lines up for free, and as the dispatch flag in
  `rocky.py` `main()`. New routes:
  - `GET /api/commands` — registry + current running set.
  - `POST /api/run` — `launch_command()` fires `rocky.exe --<flag>`
    (`python rocky.py --<flag>` in dev) as a detached `Popen`. No stdout
    capture — Rocky logs to `rocky.log`, so the live log viewer is the feedback
    channel. Rocky's own per-command lock blocks a duplicate run.
  - `POST /api/schedule/create|update-time|delete` — `schtasks` wrappers that
    create in the `\Rocky\` folder (so the task query finds them), change the
    start time, or delete (guarded to Rocky-named tasks only).
- **`templates/dashboard.html` — two new sidebar pieces.** A **"Run a Command"**
  card (commands grouped Cases/Inbox/PMA/Other, ▶ Run per row, a `dry` checkbox
  on dry-run-capable commands, a red **WRITES** tag on `pma-arm`), and a
  **"+ New"** schedule form in the Scheduled Tasks card (command dropdown,
  optional name, frequency Daily/Weekly/Hourly/EveryNmin/Once, time picker).
  Each existing task row gained 🕑 edit-time and 🗑 delete controls alongside
  the enable/disable toggle. Added a toast for action feedback. Run buttons
  reflect running state from the 10 s status poll.

**Decisions made**

- **No `/rl HIGHEST` on created tasks.** It forces an *elevated* create —
  tested, fails with "Access is denied" from a non-admin process. Rocky's jobs
  only need the logged-on rocky user's rights (OneDrive + token cache live in
  that profile), so a default run level lets the dashboard create/edit/delete
  without admin. Tasks created this way are also editable non-elevated.
- **`/it` (run only when logged on) on create + `stdin=DEVNULL` on all
  `schtasks` calls.** `schtasks /change /st` otherwise prompts "Please enter the
  run as password" and would hang the dashboard. `/it` stores no password
  (correct for the always-logged-in Rocky laptop) and DEVNULL guarantees no
  prompt can ever block. Verified full create→edit-time→toggle→delete cycle
  non-elevated.
- **Allowlist, not free-form.** `/api/run` and the schedule routes only accept
  flags in `ROCKY_COMMANDS`; time is regex-validated `HH:MM`, name is
  `[A-Za-z0-9 _-]`, delete refuses non-Rocky tasks — so the run/schedule
  endpoints can't become an arbitrary-command sink.

**Open items / watch-outs**

- **Latent bug fixed:** `main()` printed a `→`/`—` that crashed under cp1252
  stdout (Task Scheduler / redirected output). Now reconfigures stdout/stderr
  to UTF-8 at startup.
- **Run-now not live-fired on dev** (no real Anthropic/Graph keys here, same as
  email-brain). Validated: registry endpoint, bad-flag rejection, and the full
  schedule create/edit/delete lifecycle against real Task Scheduler. First live
  smoke on the Rocky laptop: click ▶ on a cheap command (e.g. `pma-test`) and
  confirm output appears in the log viewer.
- **Enable/disable of *pre-existing* tasks** (created elsewhere, possibly
  elevated) may still need an elevated dashboard — the UI already warns. Tasks
  the dashboard creates won't have that problem.
- **Plain-English log is best-effort, not exhaustive.** `humanize()` covers the
  log lines seen so far; new/unmatched lines still render (cleaned), they just
  aren't rephrased. When a subsystem starts emitting a new important message,
  add a `PLAIN_RULES` entry (and a `PROCESS_NAMES` key for any new `[tag]`).
  Verified live: Plain shows 72 lines, Technical 104 (noise hidden), toggle and
  filters interoperate.
- **Auto-setup invents no times for documented jobs**, but pma-activity/
  pma-knowledge/email-brain times on the 📅 button are my suggestions
  (18:00 / 18:30 / 02:00) — adjust if James wants different slots.
- **Rebuild + redeploy:** `python build_exe.py --dashboard` (flask/jinja2 +
  template already wired) then OneDrive-sync `dashboard.exe` to the laptop.

**Session-end state (committed)**
- `91dec16` dashboard run/schedule/plain-log; `64effad` DASHBOARD.md launch
  guide; `85a0260` brought prior multi-session work into git (email_brain.py,
  research_agent_*, rocky/pma updates, deps); `build_exe.py` fix to bundle
  `email_brain.py` + `numpy` for rocky.exe.
- **dashboard.exe is built + deployed** to OneDrive (smoke-tested OK).
- Still untracked on purpose: `.claude/` (worktrees + launch.json).

**PENDING — rocky.exe rebuild (next session, do on the Rocky laptop or a
fully-provisioned machine)**
- The dev laptop this session lacked the runtime deps; rocky.exe was NOT
  rebuilt here. `build_exe.py` is now correct (bundles email_brain + numpy).
- Steps: `pip install -r requirements.txt` → `python build_exe.py --rocky`
  (or `--all`) → it copies rocky.exe to OneDrive `Program Files\Rocky`.
- After deploy, smoke-test the new commands on the laptop, especially
  `rocky.exe --email-brain --stats` (verifies email_brain + numpy bundled) and
  a cheap `--pma-test`.

## Session 2026-06-22 — Email Brain: sent-mail corpus + retrieval index

**What changed**

- **New module `email_brain.py` + `--email-brain` command (rocky.py).** Pulls
  ALL of James's sent mail from `config['sent_brain_folders']` into a local
  single-file **SQLite** DB (`brain.db`, with an FTS5 keyword index) plus a raw
  `sent_emails.jsonl` export. For each sent message it reconstructs the inbound
  message it answered (via Graph `conversationId`) and stores a `(received →
  reply)` **pair**; pairs are embedded via **Voyage AI** (`voyage-3-large`,
  REST, vectors stored as float32 BLOBs) for similarity search. The data/index
  layer of a future "respond like James" brain — wiring retrieval into live
  drafting is deliberately a later phase.
- **Quoted-history fallback for pairing (`extract_quoted_inbound`).** When the
  real inbound isn't reachable via Graph (the archived-sends case), the brain
  parses the prior message out of the **sent body's quoted history** (Outlook
  `From:/Sent:/Subject:` blocks, `-----Original Message-----`, and Gmail/Apple
  `On … wrote:` styles) and stores it as an `inbound_quoted` pair — so old sends
  become real Q→A pairs, not just style samples. Graph inbound is still
  preferred; quoted is the fallback. `embed_source` ∈ {`inbound`, `inbound_quoted`,
  `reply`}; `_embed_key_text` embeds the inbound text whenever present (Graph or
  quoted), else the reply. Quoted inbounds get NO `messages` row (no real id) —
  just the pair's inbound_* columns. `--stats` breaks pairs out by source.
- **CLI handler `run_email_brain_cli()`** (modeled on `run_pma_activity_cli`):
  flags `--rebuild`, `--backfill-days N`, `--no-embed`, `--limit N`,
  `--query "..."` (retrieval smoke test), `--stats`. Wired into `main()` + help.
- **config.example.json:** added the Email Brain block (`sent_brain_mailbox`,
  `sent_brain_folders`, `voyage_api_key`, `voyage_model`, `email_brain_dir`).
- **requirements.txt:** added `numpy` (cosine over stored vectors). Voyage uses
  `requests` — no new SDK.
- **.gitignore:** added `email_brain/`, `*.db`, `*.sqlite*` — the corpus is
  confidential client correspondence and must never be committed (verified via
  `git check-ignore`).

**Decisions made**

- **Mailbox-aware folders (decided this session).** The Online Archive
  (`__Older Sent Items`) is an In-Place Archive mailbox Graph can't read, and
  James's primary Inbox lacks room to re-import it — so James will **drag the
  archived sends into a dedicated folder in rocky@** (`Inbox\James Older Sent`;
  NOT rocky@'s bare Inbox, which holds Rocky's operational mail). `sent_brain_folders`
  now accepts per-entry `{mailbox, path}` objects, so current Sent Items
  (jbragdon@) + archived sends (rocky@) ingest in one run. New `sent_brain_author`
  config field is the single identity treated as "James" for pairing even when
  the host mailbox is rocky@; `find_inbound_parent` skips messages from
  `{author, host_mailbox}`. App token's Application Access Policy already covers
  both jbragdon@ and rocky@.
- **Reuses the app-level token** (`acquire_app_token`) — **no new Graph
  permission**, same path as `--pma-activity`. `email_brain.py` reuses rocky helpers
  (`resolve_folder_path`, `fetch_attachments`, `build_attachment_text_block`,
  `extract_text_from_attachment`) via lazy import to avoid a circular import.
- **SQLite over pure JSONL** (James's call) because the goal is a queryable
  retrieval brain; it's still a single serverless file, consistent with the
  no-servers principle. JSONL kept as a raw export.
- **Embed the inbound side** of each pair (the query we'll match future incoming
  mail against); style-only sends (no inbound found) embed the reply instead.
- **Standalone FTS5 table keyed by `graph_id`**, NOT external-content — because
  `INSERT OR REPLACE` on `messages` changes rowid and corrupts external-content
  FTS ("database disk image is malformed"). Found + fixed during offline test.
- Corpus stored **locally** (`C:\Rocky\email_brain\`), not OneDrive — private +
  avoids syncing a large DB.

**Open items / watch-outs**

- **Archive ingestion plan = drag into rocky@.** James creates
  `Inbox\James Older Sent` in rocky@ and drags the Online Archive's
  `__Older Sent Items` into it; config already points the 2nd folder entry there.
  Folder resolution still skips gracefully if the folder doesn't exist yet.
  Archived sends' inbound counterparts aren't in rocky@, but the quoted-history
  fallback recovers most of them from the sent body — true style-only (no
  parseable quote) should be the minority. Confirm the `--stats` source split on
  the first live run.
- **Not yet run live.** Dev laptop has placeholder Anthropic/Voyage keys; live
  Graph + Voyage runs happen on the **Rocky laptop**. Verified offline: both
  modules compile (py 3.14), CLI dispatch + `--stats`, and the full DB layer
  (pairing, idempotent upserts, FTS keyword search, embed-key selection, vector
  blob round-trip). Run order on Rocky laptop: `--email-brain --limit 25
  --no-embed` → inspect `brain.db` → add real `voyage_api_key` → `--limit 25` →
  `--query "..."` → full `--email-brain` backfill.
- **Not yet scheduled/deployed.** Needs `python build_exe.py` and (optionally) a
  daily incremental Task Scheduler entry once the backfill is validated.

## Session 2026-06-19 — Ella digest: role-based categories

**What changed**

- **Rewrote `ELLA_DIGEST_SYSTEM_PROMPT` (rocky.py).** Per-case output changed
  from the old two-subsection format (**What happened** / **Action items**) to
  five role-based categories, in fixed order: **Internal Updates**,
  **Plaintiff Updates**, **Expert Review**, **Client Updates**, **Other**,
  followed by **Action Items** (kept from the old format). Empty categories are
  omitted. Added cross-referencing rule (an email fitting two categories appears
  in both with a "(also under …)" note) and inline `**FLAG: …**` prefixes for
  CV/rate-sheet/retention (Expert Review) and items needing client
  response/approval (Client Updates).
- **Updated the per-case `user_prompt`** in `_build_ella_case_section` to ask for
  the five categories + Action Items instead of "two subsections."
- Bumped the per-case word cap 200 → ~300 to fit the extra structure.

**Decisions made**

- **Role assignment is by email domain / sender, not body text.** Internal =
  any `@gallagherllp.com` address. This is robust because the From/To addresses
  are NOT run through the name-swap (only body/subject/case-name are), so domains
  reach the API intact. Generic role inference means the prompt still works for
  any of Ella's matters; the named parties (Ramos/Fish = plaintiff's counsel,
  Mathura/Mercy/Stella Maris = client, Aiken/Rowell/Webster/Kannan = internal)
  are concrete examples for the current lead matter.
- **Kept Action Items** even though the new guidance didn't mention it — dropping
  action-tracking from a litigation digest is a regression, and it's additive.
- **No renderer/HTML change needed** — `_md_section_to_html` already maps
  `**Header**` → uppercase h4 and `- ` → bullets, so the new categories render as-is.

**Open items / watch-outs**

- The five-category scheme is matter-shaped (med-mal defense). For non-litigation
  or differently-shaped matters in Ella's spreadsheet, most traffic will land in
  **Other**. Revisit if Ella's digest covers cases that don't fit this mold.

## Session 2026-06-17 (2) — Maple activity digest (daily branded email from Rocky)

**What changed**

- **New capability: `--maple-digest` (rocky.py, 4:30 PM).** Reads the Maple
  app's per-session Claude Code activity logs (JSONL on OneDrive:
  `Program Files\Maple\logs\activity\<date>__<user>__<machine>__<sess8>.jsonl`),
  rolls events up **per user**, asks Claude for a 1-2 sentence narrative of what
  each person did, and emails a **Maple-branded** HTML digest from rocky@ via
  `send_mail_guarded`. Same outbound path as the Ella/PMA digests — no parallel
  send path. Args: `--date YYYY-MM-DD` / `--yesterday` / `--dry-run`. Defaults to
  **today, local date** (Maple filenames use local date). Archives the rendered
  HTML to `C:\Rocky\maple_digests\` alongside sending.
- **Recipients (James's call):** James + asantarelli@ + kvirtue@. Code default
  `_DEFAULT_MAPLE_DIGEST_RECIPIENTS` so it works without a config edit on the
  Rocky laptop; override via config `maple_digest_recipients`. Also added
  `maple_logs_dir` and `maple_digest_skip_if_empty` (default false — set true to
  suppress the email on no-activity days, e.g. weekends).
- **Branding asset:** `Icon/maple_logo.png` (forest-green tile, white trunk,
  autumn maple-leaf canopy) generated by committed script `make_maple_logo.py`
  (Pillow). CID-embedded as `cid:maple_logo`. `Icon/` is already bundled wholesale
  by `build_exe.py`, so no build change needed.
- **Standalone PowerShell tool (built earlier same session):**
  `summarize-maple-activity.ps1` — no-AI, dependency-free, writes a per-**session**
  markdown digest to `digests/`. Kept as a local/offline archive tool; the
  scheduled EMAIL is the Python path. (`digests/` + `maple_digests/` now gitignored.)
- **config.example.json:** added the Maple block.

**Decisions made**

- **Python in rocky.py, not PowerShell, for the email** — reuses the guarded
  outbound path + MSAL token + Claude client; PowerShell would have meant a
  parallel send path (forbidden) and duplicate auth. The two log parsers (ps1 +
  Python) are simple and must stay in agreement.
- **Per-user narrative via one Claude call/day** (cheap, cross-user context),
  with a deterministic factual fallback if Claude errors (verified on dev, where
  the placeholder API key 401s — fallback rendered correctly).

**Watch-outs**

- **BOM bug found + fixed:** Maple's PowerShell logging hook writes each `.jsonl`
  file **with a UTF-8 BOM**. `json.loads` rejects the BOM on line 1, so the
  FIRST event of every file (often the `SessionStart` carrying the start
  `git_head`) was silently dropped. Fixed by reading with `utf-8-sig`. PowerShell
  `ConvertFrom-Json` tolerates the BOM, so the ps1 was unaffected — that's why
  the two disagreed (ps1 skipped 0, Python skipped 3). If Maple ever changes its
  log encoding, revisit both readers.
- **4:30 PM same-day window** misses work done after 4:30. James chose 4:30; a
  `--yesterday` morning run would capture complete days if he'd rather.
- **Not yet deployed/scheduled.** Needs `python build_exe.py` (rebuild + copy exe
  to OneDrive) and a Task Scheduler entry on the **Rocky laptop**:
  `schtasks /Create /TN "Rocky Maple Digest" /SC DAILY /ST 16:30 /TR "<rocky.exe path> --maple-digest"`.
  Real send + real Claude narrative only work on the Rocky laptop (valid API key
  + auth); dev can only `--dry-run`.

---

## Session 2026-06-17 — PMA Activity exporter (raw email feed for the Maple Updater Agent)

**What changed**

- **New command `--pma-activity` (once daily).** A deliberately simple,
  Claude-free exporter: pulls *new* emails from rocky@'s `Inbox\PMA emails`
  folder and appends each one — full plain-text body + extracted attachment text
  (PDF/DOCX/XLSX) — as a single JSONL line to `pma_activity_feed.jsonl` in the
  **Maple Updater Agent** folder on OneDrive
  (`Program Files\Maple\Maple updater agent\PMA Activity\`). Rocky does **not**
  classify, match tickets, recommend, or send any email — the Maple agent reads
  the feed and updates `PMA Ticket Tracker.xlsx` itself.
- **`pma_tracker.py`** — added `maple_activity_dir()`, `fetch_folder_messages()`
  (folder-scoped, paginated, includes ccRecipients; mirrors `fetch_pma_messages`
  but no recipient filter), `_recipients()`, `build_activity_record()`, and the
  entry point `run_pma_activity()`. Reuses the module's existing
  `_fetch_attachments`, `_extract_attachment_text`, `_append_jsonl`,
  `_email_text`, `load_pma_state`/`save_pma_state`, `SIGNATURE_IMAGE_MAX`.
- **`rocky.py`** — added `run_pma_activity_cli()` (resolves the folder via the
  existing `resolve_folder_path`, app-token read of rocky@'s own mailbox) and
  wired `--pma-activity` into `main()` dispatch + help + docstring.
- **`config.example.json`** — added `pma_activity_mailbox`,
  `pma_activity_folder` (`Inbox\PMA emails`), `maple_activity_dir`,
  `pma_activity_backfill_days` (30), `pma_activity_extract_attachments` (true).

**Decisions made**

- **Format: JSONL** (one email object per line), not a single JSON array — the
  feed is append-only and expected to grow large; JSONL appends/streams without
  rewriting, and reads fine through OneDrive (plain text, no Files-On-Demand
  placeholder issue). Per James: "maximum machine readability … enormous amounts
  of data."
- **Incremental** via a cursor in `C:\Rocky\state\pma_activity_state.json`
  (`last_received` + a capped `seen_ids` set). First run / `--backfill-days N`
  uses a lookback window (default 30d). Idempotent: re-runs and forced backfills
  skip already-exported message ids.
- **Separate from `--pma-poll`** (the HubSpot path stays as-is, observe mode).
  This is the new front-end: Rocky feeds raw email → Maple owns association +
  tracker + HubSpot sync.

**Production-path + permission fix (same session, after first laptop run)**

- **First laptop run failed to write** every line with `[WinError 5] Access is
  denied: 'C:\Users\jbragdon'`, yet still reported `exported: 27` and advanced the
  cursor — because `_append_jsonl` swallows OSErrors. Root cause: the Rocky laptop
  runs as user **`rocky`**, where Maple mounts at
  `C:\Users\rocky\OneDrive - gejlaw.com\James D. Bragdon's files - Program Files\Maple\...`
  (same base as `_DEFAULT_MAPLE_LOGS_DIR`), NOT the `jbragdon` profile. My initial
  `maple_activity_dir` default was the dev-laptop (`jbragdon`) path.
- **Fixes:** (1) `DEFAULT_MAPLE_ACTIVITY_DIR` now points at the `rocky`-profile
  production path (`...\Maple\Maple updater agent\PMA Activity`); dev laptop
  overrides via `maple_activity_dir` in config. (2) `run_pma_activity` now does a
  **pre-flight write check** (mkdir + open-append) and returns
  `{"error":"feed_not_writable"}` WITHOUT advancing the cursor if it fails — no
  more silent loss.
- **Decision (James):** keep writing into the Maple updater agent folder; grant
  the `rocky` account **Edit** (not view-only) share access to that OneDrive
  folder so the feed can be written in place.
- **State recovery:** because the failed run advanced the cursor, delete
  `C:\Rocky\state\pma_activity_state.json` before the next run so the 27 emails
  re-export.

**Watch-outs**

- A real `--pma-activity` run needs the **Rocky laptop** (the dev laptop has no
  `client_secret`, so the app token / folder resolve fail there). Logic was
  validated offline by monkeypatching `fetch_folder_messages` (JSONL shape, cursor
  advance, idempotency all green).
- The `Inbox\PMA emails` folder must exist in rocky@ and be fed by an Outlook
  rule. If `resolve_folder_path` returns None, the command aborts with a logged
  error (check the folder name/separator).
- `build_exe.py` already bundles `pma_tracker.py` + `pypdf`/`docx`/`openpyxl`; no
  build change needed.

**Open items**

- Grant `rocky` Edit access to the Maple updater agent OneDrive folder; confirm a
  clean write end-to-end.
- Commit + `python build_exe.py` + OneDrive deploy the production-path + pre-flight
  fixes (the first laptop test used config override + the already-deployed exe).
- Schedule `--pma-activity` in Task Scheduler (suggest ~4:45 AM, ahead of Maple's
  5 AM sync; can add 11:45/6:45 to feed all three Maple syncs).

---

## Session 2026-06-10 — Pending-LLT update format (Christina style) + PMA bozzuto forwarder

**What changed**

- **`pending_llt.py` — reshaped the property update emails to match Christina
  Araviakis's style** (reviewed 30+ of her real per-property update `.msg`
  files). Replaced the single Tenant|Unit|Status|Next-Steps table with
  category-segmented sections: `_classify_matter` routes each row from its
  Status + Next-Steps into **court / recert / rent / breach** (court posture
  wins). Each category gets its own call-to-action — rent → "send updated
  ledgers"; breach → "have the violations resolved, or should we file?";
  recert → "have residents completed recertification?". Court matters move to a
  read-only "already in court" status table. Rent bullets list the unit only
  (+`(after M/D)` when not yet ripe); breach bullets append the issue in parens.
  Added `_build_greeting` (first-name greeting). Retired `_RIPE_OVERRIDE_*` +
  unused `timezone` import. Template rewritten (`pending_llt_email.html`).
  Committed as `946c686`, exe rebuilt + pushed.
- **`pma_tracker.py` — forwards from `pma@bozzuto.com` are now in-scope.**
  `fetch_pma_messages` gained an `extra_senders` allowlist: a message is kept if
  `pma_recipient_filter` (pmateam@) is in To/Cc **OR** its From is allowlisted.
  Needed because Rocky polls rocky@'s inbox and a client forward to rocky@ won't
  carry pmateam@ in To/Cc. `run_pma_poll` reads `pma_forwarder_senders` (default
  `["pma@bozzuto.com"]`). Added `pma_instructions.md` rule (classify the
  *forwarded* content, not the bozzuto.com forwarder) and the config key.

**Decisions made**

- **Pending-LLT grouping stays per-building** (the current `by_property` key),
  so each draft is one building → category sections, no cross-building nesting
  needed. Family-level grouping (one "Bridge District" email across
  Alula/Poplar/Stratos) is deferred — it changes *recipients* and the BMC
  Contacts sheet is keyed per-building. The email template lives in OneDrive
  (runtime read); a version-controlled backup now sits at
  `reference_files/templates/pending_llt_email.html`.
- **Court cases excluded from the "please act" asks**, shown in a separate
  status table (per James). Rent/breach/recert kept as separate *sections in one
  email* (per James), not separate emails.

**Watch-outs**

- PMA keyword matcher (`match_candidates`) scans subject + sender domain + first
  500 chars of body. Forwarded mail opens with a `From:/Sent:` header block, so a
  deal/property name can fall past the 500-char window; the `FW:` subject is the
  strongest remaining signal. If observe-week logs show forward misses, widen the
  body-scan window for forwarded mail.
- This session's deploy commit also *lands* the previously-uncommitted PMA
  subsystem + image-handling work from the two 2026-06-07 sessions (they were
  built and deployed via the OneDrive exe but never committed), bringing git into
  line with the deployed exe.

---

## Session 2026-06-07 (3) — PMA NegotiationWatch: pmateam monitor + HubSpot updater (observe-mode build)

**What changed**

- **New subsystem: PMA tracker.** Watches `pmateam@gallagherllp.com`, keyword-matches each email against the ~125 HubSpot PMA tickets, asks Claude to classify + propose a status/summary update, and (when enabled) writes to HubSpot. Unmatched emails go to a daily 8 AM digest for Beth Crassweller + Kyle Virtue. Spec: `Desktop\PMA-NegotiationWatch-Agent-Spec.md`.
- **`pma_tracker.py` (new).** Self-contained module (mirrors `pending_llt.py`): state, manifest load, Graph poller (`fetch_pma_messages`, app-token, Inbox `$filter=receivedDateTime gt`), keyword pre-filter (`match_candidates`, whole-word), Claude classifier (`classify_email`, spec §3 prompt + `pma_instructions.md` appended), gated HubSpot updater (`hubspot_get_ticket`/`patch_ticket`, read-then-append summary), digest builder, and two entry points `run_pma_poll` / `run_pma_digest`.
- **`pma_bootstrap.py` (new, dev script).** Reads the HubSpot `.xls` export (via `xlrd`; `.xlsx` via openpyxl), maps real columns, **seeds `counterparty_keywords`** from ticket name + associated deal (Title-Case/numeric filter to drop junk), writes `pma_manifest.json`. Idempotent merge preserves hand-tuned keywords + stage IDs. Verified: 125 tickets, all with keywords.
- **`pma_instructions.md` (new).** Plain-English "brain" James edits; reloaded every poll (no rebuild), folded into the classifier prompt. Includes a guide to reading `pma_activity.jsonl` during the observe week.
- **`rocky.py`.** Added `run_pma_poll_cli` / `run_pma_digest_cli` (modeled on `run_ella_digest_cli`: app-token to read pmateam, delegated token to send digest) + `run_pma_test_cli` (`--pma-test`, mirrors `run_ella_test_cli`: verifies app-token reach to pmateam Inbox, manifest load, and matcher before the observe week). Wired `--pma-poll` / `--pma-digest` / `--pma-test` into `main()` + help + docstring.
- **`config.example.json`.** Added PMA block: `pma_mailbox`, `pma_digest_recipients`, `pma_backfill_hours`, `pma_hubspot_enabled` (default **false**), `hubspot_token`, `hubspot_status_property`/`hubspot_summary_property`.
- **`build_exe.py`** bundles `pma_tracker.py`. **`requirements.txt`** adds `xlrd>=2.0.1`. **`.gitignore`** adds `pma_manifest.json` (firm-confidential; delivered via OneDrive, not git).

**Decisions made**

- **Observe-week gating (per James).** Build everything now but default to OBSERVE MODE: poll → classify → **log the proposed HubSpot payload to `pma_activity.jsonl`, write nothing.** HubSpot writes require THREE gates: `pma_hubspot_enabled` true AND `status_changed` AND confidence in {high, medium}; `--dry-run` forces off regardless. James reads the log for ~1 week, tunes `pma_instructions.md` + manifest keywords, then flips the flag.
- **Scheduled one-shot, not a loop** — `--pma-poll` every 15 min + `--pma-digest` at 8 AM via Task Scheduler, matching every other Rocky job.
- **Reuse over new** — `acquire_app_token` (mailbox read), `send_mail_guarded` (digest from rocky@), `CLAUDE_MODEL` constant, `_extract_json_from_response` (duplicated into the module to avoid an import cycle, consistent with outbound/pending_llt keeping their own helpers).
- **Real export columns** differ from the spec's guesses: append target is the custom **"Summary Status"** property (not `hs_ticket_body`); status lives in "Ticket status". The 6 distinct status values in the export exactly match the spec's list.
- **Manifest + instructions live in the PMA Team root on OneDrive** (the Rocky laptop runs the .exe from a LOCAL copy, so `PROGRAM_DIR` is local and NOT a good home for synced/editable files). `_resolve_pma_file()` checks `pma_team_root` first, falls back to next-to-the-exe. `pma_bootstrap.py` now writes the manifest straight into `pma_team_root` (read from `config.json`) so it syncs dev→Rocky with no manual copy. Runtime state + logs stay in `DATA_DIR` (`C:\Rocky`).

**Open items / blockers (none code-side)**

0. **pmateam is a Microsoft 365 Group, not a mailbox (discovered on first server `--pma-test`).** Graph `/users/pmateam@` returns 404 ErrorInvalidUser. Fix (James's call): the pmateam group **forwards to rocky@gallagherllp.com**, and Rocky polls its OWN inbox, keeping only mail with pmateam@ in To/Cc. New `_addressed_to()` + `fetch_pma_messages(..., recipient_filter)`; config `pma_mailbox` now `rocky@gallagherllp.com` + `pma_recipient_filter` `pmateam@gallagherllp.com` (default). `--pma-test` reports inbox count vs. pmateam-addressed count. rocky@ is already in the app access policy (Test 0 passed). Caveat: relies on the forwarded copy preserving pmateam@ in To/Cc (true for group/DL delivery + redirect; "forward as attachment" would break it). Exe rebuilt + redeployed with this change.
1. **Mailbox access:** An Exchange Application Access Policy is a *restriction*, not an enabler. James confirmed Ella's app-token flow works in production and the firm has **no restrictive policy in place yet**, so Rocky's app has broad `Mail.Read` across the tenant — meaning `pmateam@` is **likely already reachable now**, no IT step needed to start. Verify with `rocky.exe --pma-test` **on the Rocky laptop** (the dev laptop has no `client_secret`, so Test 0 fails there). Only if Test 1 returns 403 does IT need to add `pmateam@` to a policy. When the firm later implements scoping, the policy must include `pmateam@`.
2. **HubSpot go-live:** Beth/Kyle create the `GEJ Rocky Integration` private app → paste `hubspot_token`; pull stage IDs via `pma_tracker.get_pipeline_stages()` (`GET /crm/v3/pipelines/tickets`) and fill `hs_pipeline_stage_id` in the manifest; confirm the "Summary Status" internal property name via `GET /crm/v3/properties/tickets`; then set `pma_hubspot_enabled: true`. First live test scoped to one ticket.
3. **Keyword tuning** happens during the observe week from the activity log.

**Knowledge corpus (added same session)**

- **Goal (James):** start building a "brain" now — capture every pmateam email + draft into a structured corpus during the observe week, synthesize it with Claude daily, train on it later.
- **Capture (in `--pma-poll`, deterministic, no extra Claude cost):** for every email, `archive_email()` saves the body (`Raw Emails/*.txt`) + draft/redline attachments (`Drafts/`, with text extracted via lazy import of rocky's `fetch_attachments`/`extract_text_from_attachment`) into a per-deal folder under the **PMA Team** root, and appends a structured event (parties, subject, body excerpt, attachment text) to that deal's `events.jsonl`. Filing key is deterministic: exactly-one keyword candidate → that deal; zero/ambiguous → `_unmatched/` bucket. Runs regardless of HubSpot `--dry-run`.
- **Synthesis (`--pma-knowledge`, new, once daily):** for each deal with events newer than its last-synthesized cursor, ONE Claude call merges prior brief + new events → updated `negotiation.json` (structured: parties, what's negotiated, key_terms, open_issues, document_versions, chronology, current_posture) + rendered `negotiation.md`. Then ONE call refreshes `_knowledge/pma_general_knowledge.md` (cross-deal patterns). `--dry-run` lists deals with new activity, no Claude calls.
- **Location:** OneDrive `PMA Team` (sibling of Rocky Cases), config `pma_team_root` (default derives from `cases_root` parent). Config also adds `pma_archive_enabled` (default true). James chose OneDrive + extract-text-now.
- **Cost bounded:** per-deal synthesis only for deals active that day (a few during observe week), incremental via the `last_event_synthesized` cursor. Verified end-to-end offline in `smoke_test_pma.py` (archive 2 emails → 1 deal, unmatched bucket, synth writes json/md/general, re-run skips).
- **Status calibration / supervised learning (per James):** during `--pma-knowledge`, each deal's synthesis now receives the manifest's **human-authored ground truth** (`current_status` + `summary_status` from the HubSpot snapshot). The brief records `hubspot_status` / `hubspot_summary_status` verbatim plus a model-generated `status_evidence` (which emails justify the recorded status). A new cross-deal `update_status_calibration()` writes `_knowledge/status_calibration.md` — learned rules for (1) email-event → status-value mapping, (2) the team's summary-line style/voice, (3) common transitions + triggers, (4) staleness flags. This is the supervised signal for tuning the classifier before go-live (the dated summary lines like "6/4: Sam sent…" align to specific corpus emails). `run_pma_knowledge` now takes `program_dir` to load the manifest. Verified in smoke test (ground truth captured onto brief, calibration md written).
- **Self-contained for the frozen exe:** `pma_tracker` carries its OWN `_fetch_attachments` + `_extract_attachment_text` (PDF/DOCX/XLSX/text) rather than importing them from `rocky.py`. The earlier `from rocky import …` would fail silently in a PyInstaller `--onefile` build (entry script isn't importable by name), which would have dropped draft-saving/extraction in production. `build_exe.py` already bundles `pypdf`/`docx`/`openpyxl` as hidden imports.
- **Initial 60-day backfill (per James):** first `--pma-poll` with no saved cursor looks back `config["pma_backfill_days"]` (default **60**) to seed the corpus; `--pma-poll --backfill-days N` forces an N-day lookback regardless of cursor. `archive_email` is now fully idempotent (early-returns if the body `.txt` already exists), so re-running a backfill does NOT duplicate `events.jsonl` lines or re-download drafts. The 60-day pull paginates and batches (10 + 2s sleep); the instance lock means an overlapping scheduled poll just exits. (Replaced the old `pma_backfill_hours`/24h default.)

**HubSpot write arming — two affirmative switches (per James)**

- HubSpot writes are now gated by **both** a config master (`pma_hubspot_enabled`) **and** a runtime **arm flag file** (`state/pma_hubspot.armed`), AND-ed with the existing confidence gates + `--dry-run`. Default = **asleep** (flag absent). The arm flag is local + not synced + not in config, so it can't be flipped on by an errant config sync.
- Toggle with `rocky.exe --pma-arm` (creates flag; reports combined posture) and `--pma-sleep` (removes it). `--pma-poll` logs the posture each run (ARMED / ASLEEP / OBSERVE), and `--pma-test` gained a "Test 4: HubSpot write posture" line. `pma_activity.jsonl` records `hubspot_armed` per email.
- Go-live is now a deliberate two-step: set `pma_hubspot_enabled: true` in config **and** run `--pma-arm`. Verified by smoke-test Case 6 (enabled + token + not dry-run but NOT armed → still "proposed", no write).

**Watch-outs**

- **`pma_manifest.json` is git-ignored** (firm data) — it must reach the Rocky laptop via OneDrive (placed in `Program Files\Rocky` beside the .exe), like `instructions.md`. A rebuild is NOT needed to update it.
- **Status writes are skipped if `hs_pipeline_stage_id` is empty** (logged), but the summary still appends — so before go-live, stage IDs MUST be populated or status never advances.
- **Local dev API key was invalid (401)** this session, so the live Claude classification call couldn't be exercised here; keyword matching, gating, logging, and JSON parsing were all verified offline (`smoke_test_pma.py`, gitignored). The production laptop has the working key.
- **`build_exe.py` still bundles `pending_llt.py`** with its prior uncommitted local changes (noted last session) — unchanged here.

---

## Session 2026-06-07 (2) — Image files: convert to PDF + read via Claude vision

**What changed**

- **New "Image handling" section in `rocky.py`** (after `build_attachment_text_block`). Helpers: `_is_image_file`, `_image_to_pdf_bytes` (Pillow), `ensure_image_pdf` (writes a sibling `.pdf`, idempotent), `_image_bytes_for_vision` (normalizes/​downscales to a Claude-supported media type), and `extract_image_text_via_vision` (sends the image to Claude vision → verbatim transcription + a one-line "[Image type]" tag). All best-effort; never raise.
- **Ingestion (`save_email_to_case`).** When an email attachment is an image (and not a skipped inline signature), Rocky writes the image *and* a converted PDF companion into `Raw Documents/`.
- **Daily run (`process_case_folder` gather loop).** Preprocess pass converts any loose image in `Raw Documents/` to a sibling PDF. Loose images are then skipped in favor of their PDF for *filing*; the PDF's text is read from the **source image via Claude vision** (pypdf returns nothing for an image-only PDF). Non-image PDFs are unchanged (pypdf path). This covers both of James's scenarios — image downloaded from email, and image already sitting in `Raw Documents/`.
- **`requirements.txt`** — added `Pillow>=10.0.0`.

**Decisions made**

- **Vision, not Tesseract OCR.** Reuses the Anthropic key Rocky already has; nothing to install on the Rocky laptop or bundle into `rocky.exe`; far better on photos/handwriting/screenshots. (User declined the structured A/B/C question; picked the option that actually achieves "so it can review and process it" with least ops burden.)
- **Vision is scoped to the daily-run path, NOT the per-email classifier.** Keeps API cost off the hot path (every inbound email) and on the once-daily, idempotent case-processing path. Trade-off noted below.
- **Image→PDF honors the literal request** ("convert it to PDF") and keeps case folders uniformly PDF; the original image is preserved as the immutable raw record.
- **The image-derived PDF is the filed unit; the source image is the read unit.** Avoids double-filing and keeps idempotency keyed on the PDF in `master_file_index.json`.

**Open items**

- **Classifier still can't "see" images.** A property manager who sends *only* a photo of a notice with an empty body won't get smarter triage at classification time (the PDF/vision read happens later in the daily run). If this matters, add a vision call into `build_attachment_text_block` — but weigh the per-email cost first.
- **HEIC/HEIF (iPhone photos).** Pillow can't open these without the `pillow-heif` plugin (not added). Such files degrade gracefully: no PDF, no vision text, image still filed. Add `pillow-heif` to requirements if iPhone photos become common.
- **Not run against real data / live token.** Verified by: `py_compile`, plus an offline smoke test (detection, `ensure_image_pdf` produces a valid `%PDF-`, idempotency, TIFF→PNG vision-prep). The vision API call itself was not exercised live.
- **`build_exe.py` (PyInstaller)** not re-tested with the new Pillow dependency. PIL has built-in PyInstaller hooks, so it should bundle without changes — confirm on next build.

**Watch-outs**

- **Cost:** each new image triggers one Claude vision call on its first daily-run pass (then it's in `master_file_index.json` and skipped). A case folder seeded with many image files will fan out one call per image on first run — same shape as the 42-PDF `--folder-update` caution from 2026-05-02.
- **Windows console logging is cp1252** — non-ASCII in `log.*()` messages raises a `UnicodeEncodeError` in the emit handler. Used `->` (not `→`) in the conversion log line for this reason. Keep log strings ASCII.
- **Sibling-stem matching** assumes the image and its PDF share a stem (`photo.jpg` → `photo.pdf`). The email-ingest prefix scheme makes collisions near-impossible; a manually dropped image whose stem matches an unrelated real PDF is a remote edge case.

---

## Session 2026-06-07 — Daily digest: date-awareness fix + "Cases with No Activity" section

**What changed**

- **`rocky.py` digest date-awareness.** The digest was describing past dates as future ("May 29 Status Hearing ... 23 days away" when May 29 was already past). Fixes:
  - `build_case_digest_section` now prepends `TODAY: YYYY-MM-DD (Weekday)` to the per-case user prompt.
  - `DIGEST_SYSTEM_PROMPT` gained a **DATES — READ CAREFULLY** block: a date earlier than TODAY is past; never call it upcoming/"N days away"; never recommend preparing for a past event; only give a day-count when correctly computed against TODAY, else give the date alone; don't invent/shift dates.
  - **Upcoming dates** subsection now restricted to dates on/after TODAY; **Recommended next steps** told to exclude already-passed events.
- **`rocky.py` "Cases with No Activity" section.** Open cases with no activity in the window were silently skipped; now they're listed at the bottom of the digest (both `.md` and HTML email), one row each: `RRID — Name (Next event: ____)`.
  - New `_is_open_case(meta)` — open unless `Open/Closed` column says closed. Closed cases are omitted entirely.
  - New `build_no_activity_next_events(client, cases, today_str)` — ONE batched Claude call across all dormant cases; reads each case's status memo, returns the most immediate future deadline/pending to-do per RRID (same TODAY-aware rules), fallback "None on file".
  - `_build_digest_text` and `_build_digest_html` gained an optional `no_activity` param and render the new section. Per-lawyer co-counsel digests do NOT get it (default None).
  - `daily_digest` collects `no_activity_cases` during the folder walk and builds `no_activity_rows` after the active sections. Result dict gained `cases_no_activity`.

**Decisions made**

- **Date reference passed as data, not hardcoded** — TODAY is injected into the prompt each run so the model stops miscomputing past/future. System prompt reinforces with explicit rules.
- **Dormant-case "next event" via one batched call**, not per-case, to keep cost flat regardless of caseload. Depends on a `*Case Status*.docx` memo existing; cases without one show "None on file".
- **Skip-if-no-activity preserved** — if NO case had activity, still no digest is written. The no-activity list only appears alongside real activity, so it never triggers a daily email by itself.
- **Closed cases excluded** from the no-activity list to avoid clutter (read from the `Open/Closed` index column).

**Open items**

- Not yet run against live data / a real inbox — verified by `py_compile` + isolated render test of the markdown/HTML builders only.
- "Next event" quality is only as good as each case's Case Status Memorandum; cases lacking one always read "None on file".

**Watch-outs**

- **`build_exe.py` bundles `pending_llt.py`**, which had uncommitted local changes this session (unrelated `--pending-llt` work, NOT touched here and NOT committed). The rebuilt `rocky.exe` therefore includes those working-copy changes even though git history doesn't. Reconcile pending_llt.py separately.
- Digest header timestamps remain UTC (unchanged); only the content date-reasoning was fixed.

---

## Session 2026-05-03 — Smarter local case matcher (no Claude in the matching path)

**What changed**

- **`rocky.py` matcher rewrite.** Replaced `match_email_to_case` with a five-tier matcher: conversation cache > RRID > case number > Match Keywords > sender identifier. All deterministic Python, no Claude call.
- **Conversation cache.** Added `state/conversation_cache.json` (`{conversationId: {rrid, matched_at}}`, 90-day TTL). `load_conversation_cache` / `save_conversation_cache` mirror the `last_check` pattern. Loaded once per poll, mutated in-memory on each successful match, persisted at end of cycle. Replies in a thread auto-match the same case even if the RRID is stripped.
- **Graph `$select` updated** to include `conversationId` (was missing — would've returned nothing for the cache).
- **Open/Closed filter.** Open cases (or blank `Open/Closed`) are tried first. Closed cases are a fallback only for RRID + case-number tiers; keyword/sender are never tried against closed cases. Closed matches log a hint to reopen the index entry. `_is_open_case` accepts blank/"open"/"o"/"active".
- **`Match Keywords` column** added to the case-index schema (optional). Comma-/semicolon-separated. Whole-word case-insensitive (`\b{re.escape(kw)}\b`) against subject+body. Looked up under either `Match Keywords (if applicable)` or `Match Keywords` to keep parity with the existing column-naming style.
- **Ambiguity = no match.** Within any tier, if >1 case matches, that tier is skipped and a warning is logged. No silent guessing.
- **Docs.** BUILD_REFERENCE.md § Stage 1 rewritten to describe the five tiers + ambiguity + Open/Closed semantics.

**Decisions made**

- **Match in Python, not Claude.** James was explicit: classification stays one Claude call; case identification is local and deterministic. Cheaper, faster, auditable, and the index is the single source of truth.
- **"Don't stretch" matching.** Ambiguity returns None rather than picking one. Most emails won't auto-match at first; James will add RRIDs to subject lines or populate Match Keywords over time.
- **Skipped property-address matching.** Case management work isn't tightly tied to specific addresses (unlike the litigation work). Adding a `Property Address` column would be noise for this workflow.
- **Conversation cache persists** across restarts (disk-backed) rather than in-memory only. Threads can span days; an in-memory cache would lose every match on restart.
- **Closed-case fallback is RRID/case# only.** A keyword or sender hit on a closed case is far more likely to be a new matter than a resumption of an old one.

**Open items**

- **`Match Keywords` column not yet added to `Rocky Case Index.xlsx`.** Code tolerates absence — matcher falls through to sender tier. James will populate as distinctive identifiers come up. Skip common surnames; only put things you'd be confident matching alone (e.g., uncommon last names, short docket titles, property nicknames).
- **No live test yet** of the new matcher against real inbox traffic. Syntax verified; behavior tested by code inspection only.
- **Conversation cache not warmed.** First few cycles after deploy will rely on the explicit-signal tiers; the cache populates as matches happen.

**Watch-outs**

- **`conversationId` requires the new `$select`.** If you ever revert that change, the cache silently stops working (every email would have `conversationId=None` and be ineligible for tier 0).
- **Closed-case match logs at INFO, not WARNING.** It's expected behavior, not a bug — but if you start seeing it frequently, the index is drifting.
- **Ambiguity warnings are the signal James cares about.** When two open cases share an RRID-less identifier (same opposing counsel, generic case number), Rocky logs the warning and returns no match. That's the correct behavior, but it means James needs to scan the log occasionally for these — they tell him which cases need a Match Keywords entry to disambiguate.
- **`save_conversation_cache` mutates the in-memory dict** to drop pruned entries (so the next poll's `load` sees the same state if no disk reload happened). Not strictly necessary today since we reload each cycle, but keeps the contract clean if that ever changes.

---

## Session 2026-05-02 — Big build: case mgmt (Phase D Stages 1/2/3), Phase A safety, deployment plan, Phase 0/A merge

**What changed**

- **Iteration 1.1 (RRID matching).** Added `load_case_index`, `find_rrids_in_text`, `match_email_to_case` to `rocky.py`. Reads `Rocky Case Index.xlsx` from OneDrive each poll. Three-tier matcher: RRID > case number > sender identifier. Surfaces matched case to classifier prompt as context. Logs `rrids_found_in_email`, `matched_rrid`, `match_method` per classification. Added `openpyxl` to `requirements.txt`.
- **Iteration 2 (attachment text extraction).** Replaced metadata-only `fetch_attachment_metadata` with `fetch_attachments` that downloads bytes (16 MB cap). Added `extract_text_from_attachment` (PDF via pypdf, DOCX via python-docx, XLSX via openpyxl, plain text). Classifier prompt now includes extracted text under per-file (5000) and total (20000) char caps. System prompt updated with security note that extracted text is untrusted. Added `pypdf`, `python-docx` to requirements.
- **Phase D Stage 1 (case-folder ingestion).** When RRID-matched, Rocky writes email body (as `.txt` with header) + attachments to `<case>/Raw Documents/` with a `{YYYYMMDDTHHMM}_{8charhash}` prefix (idempotent). Appends `email_ingested` event to per-case `activity.jsonl`. Functions: `find_case_folder` (globs by RRID substring — folders named "Last, First (RRID-XXXX)"), `save_email_to_case`, `append_case_activity`, `_sanitize_filename`. Smoke-tested live against RRID-0001.
- **Phase D Stage 2 (daily folder-update).** New skill: `python rocky.py --folder-update [RRID-XXXX]`. Walks each case folder, classifies new files in `Raw Documents/` via Claude using DYNAMICALLY DISCOVERED subfolders (the case's actual structure — works whether case has BUILD_REFERENCE schema or `litigation-case-setup` schema). Copies (not moves) raw → target subfolder, records to `master_file_index.json`, appends to `activity.jsonl`. Tracks `source_raw` for idempotency on re-run.
- **Phase D Stage 3 (daily case digest).** New skill: `python rocky.py --daily-digest [RRID-XXXX] [--hours N]`. Reads each case's `activity.jsonl` and `master_file_index.json` for the last 24h, plus the latest `Case Status Memorandum*.docx`. One Claude call per active case generates a markdown section (What happened / Recommended next steps / Upcoming dates). Consolidated output at `Rocky Cases/Daily Digests/YYYY-MM-DD.md`. Skips writing entirely if no case had activity.
- **Phase A safety code (three new modules).**
  - `permissions.py` — decodes JWT scope claim at startup; halts with sys.exit(2) if `Mail.Send`/`Mail.Send.Shared`/`Mail.Send.All` is present. Wired in immediately after first token acquisition.
  - `outbound.py` — `send_mail_guarded()` refuses non-`@gallagherllp.com` recipients OR senders. Scaffold; no callers yet (Rocky has no Mail.Send).
  - `kill_switch.py` — scans inbound subjects for "ROCKY STOP" / "ROCKY START" from authorized senders (config-driven, defaults to `user_email`). Writes/clears `state/dormant.flag`. Main loop checks `is_dormant()` each poll; if dormant, advances cursor but skips classification/ingestion. Manual flag deletion also wakes Rocky.
- **Deployment architecture.** Decided: Rocky runs ONLY on the dedicated Rocky laptop. Personal laptop is the dev workstation. Code flows via private GitHub repo + a wrapper script (`run_rocky.py`) on the Rocky laptop that does `git pull` every 5 minutes and restarts Rocky on code change or crash. Wrapper uses Ctrl+Break for graceful shutdown on Windows, force-kills after 20s grace. Launched at boot via Task Scheduler. New section "Git deployment pipeline" added to BUILD_REFERENCE.md.
- **Phase 0 / Phase A merge.** Original plan ran Phase 0 on James's primary laptop before migrating to a dedicated machine in Phase A. Collapsed: Rocky runs on the dedicated laptop from day one. Validation feedback loop is unchanged because git push gives identical dev iteration regardless of runtime location.
- **New files.** `run_rocky.py` (wrapper), `permissions.py`, `outbound.py`, `kill_switch.py`, `.gitignore` (covers `config.json`, `state/`, `*.log`, `*.jsonl`, `Azure ID Info.txt`, `__pycache__/`, OS junk, with `!Icon/*.png` and `!Icon/*.ico` exceptions), `TASKS.md` (ordered Phase 1 checklist).
- **Memory updates.** Saved `onedrive_legacy_path.md` (folder is "OneDrive - gejlaw.com" not gallagherllp — DO NOT "fix") and `onedrive_files_on_demand.md` (placeholder files unreadable to agents — pin folder locally).
- **Handoff doc.** `C:\Users\jbragdon\Desktop\Sunday Building Plans 2.md` written for tomorrow's session in plain English.
- **`config.example.json`** got `kill_switch_authorized` field with comment.

**Decisions made**

- Rocky runs only on the production laptop; primary laptop is dev only.
- Git pipeline over OneDrive code-sync. Reasons: atomic file copy (no mid-execution sync corruption), `git diff` review step, clean rollback, secrets stay out of cloud.
- Stage 2 dynamically discovers each case's subfolders rather than hardcoding a schema. Adapts to whatever structure exists (RRID-0001 has `Drafts/Fact Research/Legal Research/Miscellaneous/Pleadings`).
- Stage 1 saves use `_filename_prefix` = `{receivedYYYYMMDDTHHMM}_{md5(messageId)[:8]}`. Idempotent on re-run.
- Stage 2 COPIES raw → target subfolder rather than moving. Raw stays as immutable record; can be re-classified if rules change.
- Daily digest is a file (`Daily Digests/YYYY-MM-DD.md`) not an email until `Mail.Send` arrives on Rocky's account.
- Co-counsel digest routing deferred — case index has no co-counsel column yet.
- Phase A safety code split into separate modules per BUILD_REFERENCE.md (`permissions.py`, `outbound.py`, `kill_switch.py`), not bundled into `rocky.py`. rocky.py was already ~1300 lines.
- Forbidden-scope check is a blocklist (`Mail.Send` family) not an allowlist — keeps the audit non-brittle as iterations 3+ add `Mail.ReadWrite`.

**Open items**

- **GitHub setup** still to do tomorrow (Sunday). Not yet pushed. See TASKS.md Phase 1 step 2.
- **IT pre-reqs (3 left).** Azure AD app permissions + admin consent; Exchange mailbox delegation for `rocky@`; Conditional Access exception for device code flow on `rocky@`. Mailbox itself and Rocky laptop hardware are done.
- **`--folder-update` has not been run on real data.** RRID-0001 has 42 existing PDFs in `Raw Documents/`; running it would file all of them via 42 Claude API calls. Decision deferred to user. Recommended: try RRID-0002 first.
- **`--daily-digest` has not been run on real data.** No reason it shouldn't work, but live test deferred until at least one case has activity.
- **Daily-log-file rotation** for `rocky.log` not implemented. Easy to add when the log gets unwieldy.

**Watch-outs**

- **OneDrive Files On-Demand will bite.** The Rocky Cases folder MUST be pinned "Always keep on this device" on the Rocky laptop, or all the Phase D writes silently fail (the `xlsx` permission denied we hit during this session is the symptom). Memory file `onedrive_files_on_demand.md` documents this; flagged in Sunday Building Plans 2.md.
- **OneDrive folder name uses legacy `gejlaw.com`** even though the firm is now `gallagherllp.com`. Local sync folder name is a one-time artifact from before the rename. `CASE_INDEX_PATH` and `ROCKY_CASES_ROOT` in `rocky.py` use `gejlaw.com` deliberately. Memory `onedrive_legacy_path.md` documents.
- **42 PDFs in RRID-0001's `Raw Documents/`** are pre-existing case files (from before Stage 1 was wired). They're not from email ingestion. Running `--folder-update` will process them; that's a real $$$ + filesystem-mutation event.
- **Test coverage is offline-only for tonight's additions.** Functions tested in isolation against synthetic data and the live RRID-0001 folder. No end-to-end test of the email→classify→save→file→digest pipeline because that requires a live token + real inbound traffic.
- **Case index columns "Case No. Identifier" and "Sender Identifiers"** are blank for both current cases. Until populated, only RRID-tagged emails will match. Worth populating when convenient (e.g., DC court automated-notice email address for RRID-0001).
- **`outbound.py` has no callers yet.** When future code adds drafting/sending, USE THIS FUNCTION. Do not add a parallel non-guarded send path.
- **Smoke test scripts** were deliberately deleted after each test. They were one-off; leaving them in the repo would clutter and potentially leak debugging artifacts.

---

## Session 2026-04-30 — Folder rename to Rocky, build references filed, Setup B confirmed, rocky.py moved to root

**What changed**
- Folder renamed Minotaur → Rocky on James's Desktop. Code, docs, and configs verified clean of "minotaur"; only historical strings remain in `Icon/rocky.log` (old log entries, intentionally preserved) and one Bash permission entry in `.claude/settings.local.json` (harmless — record of the `mv` command itself).
- `Rocky_Build_Plan.docx` saved to the folder. Source: `C:\Users\jbragdon\Desktop\Minotaur_Build_Plan_Revised.docx`. All "Minotaur"/"minotaur" replaced with "Rocky"/"rocky" inside the document XML; formatting preserved (renamed via direct ZIP rewrite of the docx, not a Word round-trip, so styles/headers/footers survived). Verified: docx already used `gallagherllp.com` correctly, so no domain edits needed.
- `BUILD_REFERENCE.md` saved to the folder — condensed architectural reference covering Phase 0 → Phase E, permission progression, classifier schema, and Phase A production target. **Read this first in any future session.**
- `BUILD_REFERENCE.md` updated: all `gejlaw.com` references replaced with `gallagherllp.com` to match the firm's actual domain.
- `rocky.py` moved from `Icon/` subfolder to the project root. Icon image assets (`rocky.ico`, `rocky.png`, etc.) remain in `Icon/` where they belong.
- This `SESSIONS.md` created.

**Decisions made**
- **Firm domain is `gallagherllp.com`.** All references in BUILD_REFERENCE.md updated. (Build plan docx and current config.json were already correct.)
- **Inbox source for Rocky-specific requests: Setup B (separate M365 account).** Rocky will get her own account `rocky@gallagherllp.com` with delegated read access to James's mailbox. Device login uses Rocky's identity; `user_email` in `config.json` is the *target* mailbox (James's).
- **`rocky.py` lives at the project root**, not in `Icon/`. The earlier `Icon/` location was non-standard and would have caused `Path(__file__).parent` to resolve incorrectly relative to `instructions.md`, `state/`, `config.json`, etc. — all of which are at root.
- The build plan + reference are the source of truth for architecture going forward; ad-hoc decisions in conversation should be reflected back into one of those files (or this log) before the session ends.

**Open items**
- IT setup for `rocky@gallagherllp.com` is not yet done. Required: provision the account, grant delegated read on James's mailbox via Exchange, Conditional Access exception for device code flow, Azure AD app registration "Rocky" with `Mail.Read`. See `BUILD_REFERENCE.md` § Authentication setup (Setup B).
- **Code for Rocky to read her own inbox is NOT yet drafted.** Original ask in this session, deferred pending the IT setup above. When ready: extend `fetch_new_emails` to poll *both* James's inbox (current behavior) and Rocky's own inbox; tag each classification record with `source_mailbox`; same classifier, same schema. Forward-to-Rocky pattern (per BUILD_REFERENCE.md § Key UX patterns) will be the primary use case for Rocky's own inbox.

**Watch-outs**
- `Icon/rocky.log` is the orphaned log from when `rocky.py` was running from `Icon/` (Minotaur era). When `rocky.py` is next run from root, a fresh `rocky.log` will be created at root via `LOG_PATH = ROOT / "rocky.log"`. The orphan is harmless but can be deleted any time.
- The `anthropic_api_key` is stored in plaintext in `config.json`. Acceptable for Phase 0 on James's personal laptop. Must be addressed before Phase A (dedicated machine, broader access).
- The Python sandbox on this machine cannot execute scripts from `C:\Users\jbragdon\AppData\Roaming\Claude\local-agent-mode-sessions\...`. Future sessions doing docx/pptx/xlsx work via the Anthropic skills will need to copy script files locally or use stdlib alternatives (zipfile, etc.). This is what we did to rename the build plan.
- `classifications.jsonl` and `review.jsonl` do not yet exist in the folder — Rocky has not been run end-to-end yet.

---

## Template for future entries

```
## Session YYYY-MM-DD — short title

**What changed**
- file path — what + why (not the diff)

**Decisions made**
- the decision and the reason

**Open items**
- thing deferred + what future session needs to do it

**Watch-outs**
- surprise, gotcha, or non-obvious constraint future-you should know
```
