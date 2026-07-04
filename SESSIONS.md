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
