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
