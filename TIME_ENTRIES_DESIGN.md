# Time Entries in Rocky: Design Draft

Design of 2026-10-24. James has settled decisions 1–5, and Phase 1 is built (see "Build phases").

Rocky takes over the process in `OneDrive - gejlaw.com\Program Files\Rocky\Time Entries\` (at the
OneDrive root until 10/25). That process takes James's
Aspen timecard export, correlates every blank or partial entry with his sent mail, drafts narratives
in his style, verifies the drafts against the email bodies, lists sent threads that have no entry,
and delivers a workbook. Today a Claude session runs all of it by hand from the folder's `CLAUDE.md`,
using `.msg` exports that James drops into `emails\`. Under this design, the trigger is a spreadsheet
emailed to rocky@, the mail comes from Minotaur's email brain, the drafting and checking run as
Claude API calls, and the result comes back to James by email. Each new spreadsheet also starts a
learning pass, which compares what James submitted against what Rocky recommended last time.

This moves "phase 3, timesheet correlation" from Minotaur's roadmap to Rocky. Minotaur stays the
only program that pulls James's sent mail. Rocky only reads it.

---

## Decisions needed from James

| # | Decision | Recommendation |
|---|---|---|
| 1 | Where the data lives | The database goes on the Rocky laptop at `C:\Rocky\time_entries_data\`. The files James edits by hand go on OneDrive: the Property Reference Table, the style guide, and the lessons file. See "Where things live." |
| 2 | How much of the learning step applies itself | Changes that come from James's own submitted text apply automatically: new training narratives, the resident-name index, accuracy stats. Rocky emails any change to rules, aliases or collisions as a numbered proposal, and it takes effect when James replies YES. Rocky never edits its own Python. |
| 3 | Model for drafting and verification | Opus 5.5 for every call: drafting, verification and learning. A 13-of-46 error rate on High rows is what the verifier exists to catch, so this is a bad place to save money. Set by `time_entries_model` in config. |
| 4 | What the spreadsheet is and who sends it | **Decided 2026-10-24:** anyone at the firm can send it, not only James. Rocky picks it up when the attachment has the Aspen export's format, or when the subject or the note on top says something like "here are James' current time entries." Recommendations go to James with the sender cc'd. Lessons go to James alone, and only his YES applies them. See "Trigger." |
| 5 | Folder access on the Rocky laptop | **Decided 2026-10-24:** move the whole folder to `Program Files\Rocky\Time Entries`, where Pending LLT Records and the Multifamily Tracker already live. Rocky finds it the way `llt_watch.tracker_root` finds the tracker: a `time_entries_root` config key, then the folder beside `rocky.exe`. James deletes `emails\` (7,208 `.msg`, 3.7 GB) before the move, since `corpus.db` already holds every one of those emails in full and Minotaur's brain is the source from here on. `corpus.db` (95 MB) stays until Phase 1 migrates its history, then gets archived off OneDrive. |

---

## What carries over

| In the Time Entries folder | In Rocky |
|---|---|
| `emails\*.msg` exports, parsed by `olemsg.py` | Read from `C:\Minotaur\email_brain\brain.db`, read-only. The export step, the export-gap hunting, `.msg` dedupe and the `ingest_folder.py` workaround all go away. |
| `timecards\*.csv` | Arrives as an email attachment. The importer handles both CSV shapes (header on line 1–3, found by the `Narrative` column) plus `.xlsx`. |
| `corpus.db` tables `entries`, `narratives`, `matters`, `anomalies` | Migrated once into Rocky's database. The `emails` table is not migrated, because Minotaur holds the same sends and more. |
| `correlate3.py` and its eleven rules | Ported as a module with the same scoring, windows and filters. |
| `daydump.py` | Builds the per-day packet that goes into the drafting call. |
| `reverse_pass.py` plus the hand pass over the day dumps | The drafting call lists uncaptured threads for its day, using the reverse-pass guesses as hints. |
| `build_workbook.py` | Ported. The Read Me, Draft Narratives, Uncaptured Emails and Summary sheets keep the same shading. |
| `build_names.py`, `detect_anomalies.py` | Ported and run on every round. |
| `drafting_agent_spec.md`, Rule 0, the `CLAUDE.md` overrides | Become the drafting and verification prompts. |
| `Time Entry Style Guide.md` | Goes into the system prompt verbatim. It stays on OneDrive, where James edits it. |
| `Property Reference Table.xlsx` (both sheets) | Read each run. It stays human-owned. Approved changes from Rocky go into an overlay file next to it, and Rocky never writes to the workbook itself (see "Learning"). |
| `MEMORY.md` matter knowledge and correlation traps | Split up. Anything the scorer acts on goes to the Matter Notes sheet or the overlay. Anything the drafter needs to know goes to `LESSONS.md`. |
| `RUN_LOG.md` | Becomes the `runs` table, plus a short summary in each recommendations email. |
| Subagent drafting by day, then two verifier passes, with `fix()` overrides | One drafting call per day, then one verification call per day. Rocky stores both versions. |

The folder's `CLAUDE.md` workflow keeps working after cutover, for any deep dive James wants to run
by hand. The difference is that those sessions would read Rocky's database instead of `corpus.db`.

---

## The flow

```
James, or whoever handles his time, emails the Aspen export to rocky@
  └─ monitor (every 10 min): --time-entries-mail
       detect the spreadsheet, hash-dedupe, ingest it as a snapshot, file the email
       to Inbox\Time Entries, start a detached --time-entries-run <snapshot>
            ├─ 1. Learn: compare this snapshot with the last recommendations   (Opus 5.5)
            │       and save the email-to-matter links the snapshot confirms
            ├─ 2. Check mail coverage in brain.db for the snapshot's dates
            ├─ 3. Correlate the blanks and partials                       (deterministic)
            ├─ 4. Draft, one call per day                                       (Opus 5.5)
            ├─ 5. Verify, one call per day                                      (Opus 5.5)
            ├─ 6. Build the workbook and store the recommendations
            └─ 7. Email James: recommendations workbook + lessons proposed [TL-####]
James replies YES / YES 1 3 / NO 2 to the lessons email → the approved lessons apply
```

Learning runs before the new recommendations. That way a lesson Rocky draws from the last round
can inform this round's drafts once James approves it. On the first approved round, a new lesson
starts applying from the next spreadsheet.

### Trigger: `--time-entries-mail`

- Scans rocky@'s Inbox for mail with a `.csv` or `.xlsx` attachment from any @gallagherllp.com
  address. The export may come from James or from someone who handles his time. Mail from outside
  the firm is never processed, and Rocky's outbound guard couldn't reply to it anyway.
- Rocky looks for two signals, and either one is enough:
  - **Format.** One of the attachment's first six rows holds `Work Date`, `Matter`, `Hours` and
    `Narrative`. This catches the raw Aspen export even when the email says nothing.
  - **Words.** The subject or the note on top mentions time entries ("here are James' current time
    entries," "James time entries," "timecards for James"). The check reads the note through
    `forwarding_note`, so a time-entries mention deep in a quoted chain never triggers a run.
    The phrases come from `time_entries_keywords` in config.
- What happens in each case:
  - **Format matches.** Rocky processes the spreadsheet, with or without the words.
  - **Words match, but the format doesn't parse.** Rocky replies to the sender that it couldn't read
    the spreadsheet and names the columns it expected. This covers an export saved with the wrong
    columns.
  - **Neither matches.** Rocky leaves the email alone.
- **Timekeeper check.** The full export carries `Timekeeper Name` and `Timekeeper ID`. Rocky
  processes only rows for James D. Bragdon (0197). An export with no James rows gets a short reply
  saying so. The style guide, the name index and the lessons are all built on James's own entries,
  so another timekeeper's export would get bad drafts. The short six-column export has no
  timekeeper column, so for that one Rocky assumes the entries are James's.
- **Who gets what.** The recommendations email goes to James with the sender cc'd, set by
  `time_entries_results_to`. The `[TL-####]` lessons email goes to James alone. Only a YES from
  James applies a lesson, even when someone else sent the spreadsheet.
- Rocky dedupes on the attachment's sha256, so sending the same export twice does nothing. When
  the sender isn't James, Rocky logs who sent it on the snapshot.
- The run itself can take longer than 15 minutes, which is the monitor's timeout for each child
  command. So the monitor step only detects the spreadsheet and ingests it. It then starts
  `--time-entries-run` detached, the same way the Litigation Updater runs in the background, and
  `acquire_instance_lock` keeps two runs from overlapping.
- When James's note on top of the email says what to cover ("just 10/19–10/23", "blanks only",
  "include partials"), Rocky reads it with the existing `forwarding_note` helper. With no note, the
  run covers every blank and partial in the snapshot.
- The processed email moves to rocky@ `Inbox\Time Entries`.

### Snapshot ingest

- Each spreadsheet becomes a row in `snapshots`, and its lines go into `entries`. The supersession
  logic from `build_corpus.py` is kept: rows are keyed on (work_date, matter, hours) with
  multiplicity, and a wider export of the same period replaces a narrower one.
- Rocky carries the `Non-Billable` and `No Charge` flags through from the export.
- Filled narratives go to `narratives` (origin `submitted`). They are the training set and the
  source of the name index.
- An entry counts as partial when it has fewer than 35 characters (the folder's `--max-narr 35`).
  Rocky completes partial text and never overwrites what James started.

### 1. Learn from the last round

This runs only when an earlier recommendation round overlaps the new snapshot's dates.

**Deterministic diff (no Claude).** Each stored recommendation is matched to the new snapshot on
(work_date, matter, hours, ordinal), falling back to (work_date, matter) when hours changed. Each
one gets an outcome:

| Outcome | Meaning |
|---|---|
| `accepted` | The submitted narrative matches the draft (similarity of 0.9 or more) |
| `edited` | Same substance with changed wording, so the diff is stored |
| `rewritten` | James wrote something different |
| `review_filled` | Rocky flagged REVIEW and James filled the entry. This is the case the 8/30 retrospective studied: was the answer in the mail? |
| `still_blank` | James hasn't gotten to it |
| `recoded` / `rehoured` / `deleted` | The entry moved matters, changed hours, or disappeared |
| `uncaptured_taken` / `uncaptured_ignored` | For an uncaptured suggestion: a new entry did or did not appear on that matter within a day |

**The learning call (Opus 5.5).** Every outcome other than `accepted` and `still_blank` goes in,
along with Rocky's basis and notes, the final narrative, the current `LESSONS.md`, the Matter Notes
rows for those matters, and the day's mail list for each `review_filled` row. The question is the
one James posed: what can we learn from this? The call answers in JSON:

```json
{"summary": "...",
 "lessons": [
   {"kind": "matter_note | drafting_rule | style | setting | question",
    "target": "006104-1717 aliases | LESSONS.md | window_days | ...",
    "change": "exact text or value to add",
    "evidence": ["2026-10-14 006104-1717 0.3: draft said X, James wrote Y"],
    "confidence": "high | medium"}]}
```

**What applies by itself.** New `submitted` narratives, the rebuilt name index, the accuracy
numbers for the round, and the confirmed email-to-matter links (below). All of these come from
James's own text or from arithmetic.

**What waits for James.** Each lesson goes into a `[TL-####]` email as a numbered item, five at most
per round. James answers with `YES` (all), `YES 1 3`, or `NO 2`. The reply parsing works like
LetterStream's YES/NO loop. Each kind of approved lesson lands in its own place:

- `matter_note` goes to `matter_overlay.json` next to the Property Reference Table (aliases,
  collisions, contacts, the typically-no-email flag). The scorer reads the overlay on top of the
  workbook. A periodic "fold these into the sheet" item keeps the workbook the master copy.
- `drafting_rule` and `style` go to `LESSONS.md`, which is part of the drafting and verification
  prompts.
- `setting` changes a value in `time_entries_settings.json`: window days, distance penalty, token
  weights, the partial-narrative threshold. Only keys that already exist can change, and only
  within set bounds.
- `question` goes to the open-questions list, which every recommendations email carries until James
  answers.

Anything that would need a code change becomes a note to James. Proposals stay manual because of
what `LEARNED_RULES.md` records about automatic alias harvesting: it mapped `at&t` to a property
named "Billing" and `grossman` to "The Asher." A wrong basis costs more than a REVIEW flag because
it looks finished. Proposals James never answers expire after 14 days and do not count against the
next round's five.

**Accuracy tracking.** Each round records the share of High drafts accepted unchanged, edited, or
rewritten; the same for Medium; the count of `review_filled` rows the learning call judged
recoverable from mail; the take rate for uncaptured suggestions; and suggested hours against hours
James entered. The email reports the trend, so you can see whether the lessons help.

#### Confirmed email-to-matter links

Today nothing records which matter an email belongs to. Minotaur's `brain classify` tags each pair
by recipient domain and by one of 40 topic clusters, whose ids reshuffle as the corpus grows, and
neither tag knows matters. The folder's `corpus.db` has a `links` table built for this, and it holds
0 rows. A finished timecard answers the question for every entry Rocky drafted, so the learning
diff saves the answer in a `links` table in te.db.

| Column | Holds |
|---|---|
| `work_date`, `matter` | The submitted entry the email supports |
| `entry_id`, `snapshot_id` | The entry row, and the spreadsheet that confirmed it |
| `recommendation_id` | The draft or uncaptured suggestion that cited the email |
| `internet_message_id` | The join key into brain.db. It is Minotaur's dedupe key and survives a re-ingest, which `graph_id` can't promise |
| `conversation_id`, `sent_at`, `subject` | Kept so a link stays readable without brain.db. No bodies |
| `verdict` | `confirmed` or `rejected` |
| `source` | `submitted` when James's submission settles it, `inferred` when the learning call picked the email |
| `outcome` | The learning outcome that wrote the row |

Rows are unique on (work_date, matter, internet_message_id). One entry can cite several emails, and
one email can support two entries when the work was split.

Each outcome writes rows this way:

- `accepted` and `edited`: every Basis email becomes `confirmed` / `submitted`. James kept the
  draft's substance on the same matter, and the evidence comes with it.
- `uncaptured_taken`: the suggestion's Basis email links to the new entry, `confirmed` / `submitted`.
- `recoded`: the Basis email becomes `rejected` for the old matter. If the learning call says it
  belongs to the new matter, that row goes in as `confirmed` / `inferred`.
- `rewritten` and `review_filled`: the learning call already sees the day's mail. When it names the
  email behind James's final narrative, the row is `confirmed` / `inferred`.
- `still_blank`, `deleted`, `rehoured` with no other change, and `uncaptured_ignored` write nothing.
  A deleted entry can be non-billable work on the right matter, so deletion proves nothing about the
  email.

Only `submitted` rows count as ground truth, which follows decision 2: they come from James's own
submission. `inferred` rows are kept for the learning call and for review, and on their own they
never feed an alias proposal or a backtest score. Rows are never deleted. When a later snapshot
contradicts a link, for example James recoding an entry he had accepted, the verdict flips and the
`runs` record names the snapshot that flipped it.

What the links are for:

- **Alias proposals with evidence.** Words that keep turning up in a matter's confirmed emails but
  aren't among its tokens become `matter_note` proposals, each citing the messages. They still need
  James's YES, for the `at&t` reason above. This is `LEARNED_RULES.md` Rule 1 ("learn topic aliases
  from later mail") with ground truth behind it.
- **Backtest ground truth.** Backtests can score against confirmed links as well as hand-run Basis
  citations, so the pass mark still means something after the hand process stops.
- **Thread evidence.** A confirmed email marks its `conversation_id`. Whether later mail in that
  thread should score toward the same matter is a scorer change, so it waits for backtest evidence.
  The known risk is the invoice-approval thread that names one matter while discussing another (the
  RealPage note in `MEMORY.md`).
- **Minotaur.** The table is the first set of matter-labeled emails anywhere. Minotaur could read
  it read-only, the way Rocky reads brain.db, for example to split its `external` recipient class
  into clients and opposing counsel. That is Minotaur's call and outside this build.

### 2. Mail coverage check

Before drafting, Rocky compares brain.db against the snapshot's date range, with two queries: the
newest `sent_at`, and send times per day (the folder learned to check by the hour, not just by the
date). A Sunday entry needs the Monday-morning batch. If the mail stops short, Rocky waits and
retries every monitor cycle for up to `time_entries_wait_hours` (default 12). After that it runs
anyway, and the Read Me sheet says which hours are missing.

This makes the nightly `minotaur.py brain ingest` a hard dependency, and it isn't scheduled yet.
Rocky never runs Minotaur's ingest itself.

Reading brain.db:

- Opened read-only (`?mode=ro`). Rocky retries if the database is locked by an ingest or classify run.
- Checks `meta.schema_version = 1` and stops with a clear message if Minotaur changes its schema.
- Uses `direction='sent'` rows for James's work and the `received` rows in the same
  `conversation_id` for the reply chain. Dedupes on `internet_message_id`.
- Converts `sent_at` from UTC with `zoneinfo("America/New_York")`. The folder's scripts subtract a
  fixed 4 hours (`build_corpus.py:188`, `correlate3.py:197`), which goes wrong when daylight time
  ends on 11/1/2026: after that, an email sent after 7 PM Eastern lands on the wrong day. The folder
  process has the same problem if it runs on November mail before cutover.
- Strips quoted history and James's signature from his own text, the way `own_text()` does. The
  full body stays available, because some of James's sends survive only as quotes inside a reply.

### 3. Correlate

This is `correlate3.py` as it stands: distinctive property tokens, curated aliases, contact
surnames, the name index, collision filters, word boundaries for short tokens, the ±3-day window
with a distance penalty, the Sunday forward window, invites kept in the pool, and a zero-hit token
check. Its inputs change. Mail comes from brain.db, and the matter data comes from the workbook plus
the overlay. Weights and windows come from `time_entries_settings.json`.

With Minotaur's mail, a token with zero hits anywhere almost always means a missing alias. An
export gap is no longer the usual cause. The flag stays, with a new label: "zero hits in all mail."
Matters flagged typically-no-email still default to REVIEW on a miss, and blank-matter entries are
always REVIEW with a request to assign the matter.

### 4. Draft, one call per day

A Sunday and the following Monday go in the same call, because of the Monday 07:xx batch. Each call
carries:

- **System:** the style guide; the drafting rules from `drafting_agent_spec.md` and the `CLAUDE.md`
  overrides (Rule 0, Rule 10, the weekend batch, reading the reply chain, and treating
  @gallagherllp.com as a colleague and never the advisee); and `LESSONS.md`.
- **User:** the day sheet, which lists every entry that day (filled and blank) and every sent email
  with its Eastern time, recipients and James's own text. It also carries:
  - the correlator's ranked candidates for each blank
  - the full thread text for each candidate, capped by length
  - the Matter Notes rows for the matters in play
  - James's own narratives on those matters for ±14 days, where the drafters kept finding resident
    names
  - the colleague list
- **Output:** JSON records shaped like today's `add()` and `u()`. A draft carries date, matter,
  hours, narrative, confidence, basis and note. An uncaptured record carries date, matter, matter
  name, suggested hours, narrative, basis and note.

Rocky checks the output in code before going on:

- Every blank and partial gets exactly one draft, and each key matches the snapshot exactly.
- Partial drafts begin with James's existing text.
- Narratives end in a semicolon and contain no em dashes.
- Confidence is High, Medium or REVIEW.
- No email is cited as the basis for two entries unless the note says the work was split.

A failed check sends the call back once with the errors listed. If it fails again, those rows
become REVIEW with the reason stated.

### 5. Verify, one call per day

This is a separate call with the same packet plus the drafts, and its only job is Rule 0. It checks
each High and Medium row against the body and reply chain for these errors:

- drafting from the subject line instead of the body
- inverted advice
- crediting James with a colleague's draft
- naming a colleague, opposing counsel or an agency as the client
- billing one email twice
- stitching together emails from adjacent days
- putting work on the wrong matter
- missing a resident name that appears in James's prior narratives

It also re-checks every REVIEW flag, since writing off billable work is the error nobody goes
looking for. It returns fixes. Rocky applies them and stores both versions, which lets the learning
pass see what the verifier caught.

### 6–7. Build and deliver

- The workbook is the house format from `build_workbook.py`. File name:
  `YYYY-MM-DD to YYYY-MM-DD - draft narratives.xlsx`.
- **Basis** stays human-readable (`10/6 16:02 'RE: Marina Bay...' to G. Sapire`). A hidden column
  holds the `internet_message_id`.
- A copy goes to the OneDrive `Program Files\Rocky\Time Entries\drafts\` folder.
- Rocky sends through `send_mail_guarded` from rocky@ to James, with the workbook attached. If someone
  else sent the spreadsheet, they are cc'd. The body
  covers:
  - counts by confidence, with hours
  - escalations: hours that outrun the record, likely miscodings, likely duplicates, matters with
    zero hits
  - open questions
  - the mail coverage window
  - the accuracy trend
- The lessons proposals go out as a separate `[TL-####]` email, so a YES reply can't be confused with
  a reply about the workbook.

---

## Where things live

| What | Where | Why |
|---|---|---|
| `te.db`: snapshots, entries, narratives, recommendations, outcomes, links, lessons, anomalies, runs | `C:\Rocky\time_entries_data\` on the Rocky laptop (not `time_entries`, which is the source package's name in the repo) | It holds client narratives and quoted mail. Under `DATA_SECURITY.md`, corpora stay off OneDrive. The folder has also corrupted SQLite on a synced mount before (the `corpus.db-journal` incident on 9/5). Minotaur's mail is only on that laptop anyway. |
| Property Reference Table, `matter_overlay.json`, style guide, `LESSONS.md`, `time_entries_settings.json`, open questions | OneDrive `Program Files\Rocky\Time Entries\` (pinned with the rest of the program folder) | James edits these from either laptop. Rocky reads them on every run and writes only the overlay and `LESSONS.md`, and only after a YES. |
| Recommendation workbooks | Emailed, with a copy in OneDrive `Program Files\Rocky\Time Entries\drafts\` | This matches where the drafts live today. |
| Sent mail | `C:\Minotaur\email_brain\brain.db`, read-only | Single source of truth |

After cutover, the 95 MB `corpus.db` on OneDrive, which holds 7,210 email bodies, should be archived
off OneDrive. Before retiring it, Rocky spot-checks that brain.db holds the same sends for April
through October.

---

## Code layout

```
time_entries/
  __init__.py
  db.py            schema (including links), migration from corpus.db, snapshot supersession
  timecards.py     CSV (both shapes) and XLSX importer
  mail.py          brain.db reader: read-only, schema check, Eastern dates, own_text, threads
  reference.py     Property Reference Table + overlay + settings; time_entries_root resolution
                   (--time-entries-root, config key, folder beside rocky.exe, dev default)
  correlate.py     port of correlate3.py
  names.py         port of build_names.py
  anomalies.py     port of detect_anomalies.py
  daysheet.py      port of daydump.py, as packets for the prompts
  draft.py         drafting + verification calls, output validation
  learn.py         diff, outcomes, confirmed links, learning call, [TL-####] proposals and replies
  workbook.py      port of build_workbook.py
  run.py           --time-entries-mail, --time-entries-run, --time-entries (CLI on a file)
tests/test_time_entries_*.py
```

There are three CLI entry points:

- `--time-entries-mail` is the monitor step.
- `--time-entries-run <snapshot>` is the detached run.
- `--time-entries --csv <file> [--from --to] [--dry-run]` runs by hand on a local file. With
  `--dry-run` it writes the workbook and sends nothing.

`time_entries/` stays a package so `rocky.py` doesn't grow. `build_exe.py` needs `openpyxl`, which
Rocky already bundles.

---

## Build phases

**Phase 0 (James, before code):**

- Answer the decisions above.
- Schedule `minotaur.py brain ingest` nightly on the Rocky laptop.
- Delete `Time Entries\emails\`, then move the folder to `Program Files\Rocky\Time Entries`. Keep
  `corpus.db`: it holds the email text the hand process reads, plus the entry and narrative
  history Phase 1 migrates. **Done 2026-10-25.** All 7,560 `.msg` files were confirmed in
  `corpus.db` by path before the delete.
- On the Rocky laptop, confirm brain.db carries his sent meeting responses (`subject LIKE
  'Accepted:%'`). Rule 2 depends on them. If they aren't there, Minotaur's ingest needs a change.

**Phase 1: port and backtest correlation (no Claude calls).** Port the deterministic modules,
migrate `corpus.db`, and replay October Part 2 (10/6–10/18) against Minotaur's mail. Pass mark: the
email cited as the Basis of each High row in the hand-built workbook appears among Rocky's top
candidates for that entry at least 90% of the time. Tests cover:

- both CSV shapes and XLSX
- supersession
- the time change on 11/1
- word-boundary matching
- collision filters

**Phase 1 status (2026-10-24).** The port is built in `time_entries/` and runs as
`rocky.py --time-entries --migrate | --correlate | --backtest`. Tests are in
`tests/test_time_entries.py`.

- **The port matches the original.** On the September Part 2, October Part 1 and October Part 2
  timecards, scored against `corpus.db`, it returns the same candidates, scores, stages and tokens
  as `correlate3.py` for all 257 entries.
- **The migration works.** On the dev laptop it copied 2,642 entries, 3,178 narratives and 229
  anomalies from `corpus.db`.
- **Stand-in backtest on the dev laptop.** There's no brain.db on this laptop, so `corpus.db` stood
  in for the mail. The name index was rebuilt from narratives dated before each period, so a run
  can't learn names from the entries it is scoring.

  | Hand run | High rows | Cited email found in mail | Ranked in the top 6 |
  |---|---|---|---|
  | October Part 2 (`_work` drafts source) | 49 | 49 | 44 (89.8%) |
  | October Part 1 | 16 | 16 | 15 (93.8%) |
  | September Part 2 | 117 | 116 | 104 (88.9%) |
  | **All three** | **182** | **181** | **163 (89.6%)** |
  | October Part 4 (10/19–10/24, hand run of 10/24, added 10/25) | 34 | 34 | 30 (88.2%) |

  Two of the Part 4 misses are Monday 10/19 sends. The `.msg` export had nothing for 10/19, so
  `corpus.db` holds those emails only as quotes inside later replies, dated the day of the reply.
  brain.db should hold the sends themselves.

  That sits just under the 90% mark. There are two kinds of miss. Some matters produce no tokens at
  all: "Residences on the Avenue" is all generic words, and the instructor class matter has none.
  In the others, the cited email scored but ranked below six others on a busy day.
- **The real backtest hasn't run yet.** It needs Minotaur's mail on the Rocky laptop. `rocky.exe`
  was deployed from this branch on 10/25 for it, and the folder moved into `Program Files\Rocky`
  the same morning. Tuning waits
  for those numbers, because brain.db carries recipient addresses where the `.msg` exports carried
  display names. That changes contact-surname scoring, so tuning on `corpus.db` would have to be
  redone.
- **A stale workbook.** `drafts\2026-10 Part 2 - draft narratives.xlsx` holds 10 rows, while its
  `_work` source holds the run's 76, so the backtest reads the `_work` file for that period.

**Phase 2: drafting and verification, CLI only.** Backtest against periods where James's final
narratives exist in a later export. The September 1–5 blanks are filled in the September Part 2
export, and the August Part 2 blanks in Part 3. Score Rocky's drafts against what he actually
submitted, using the same outcome categories the learning step uses. Go live only if the High rows
hold up at least as well as the hand runs did after verification.

Phase 2 also creates the `links` table and seeds it from the hand runs. The Phase 1 backtest
already resolves each hand-cited Basis to an email in brain.db. Running the deterministic outcome
diff over each hand run whose entries James has since submitted in a later export turns those into
`submitted` links. That way the table starts with the hand runs' labeled emails, without waiting
for a live round. The seed needs brain.db, so it runs on the Rocky laptop.

**Phase 3: the mail trigger and email delivery go live.** Add `--time-entries-mail` to
`monitor_commands`. The first live spreadsheet runs with James reviewing the workbook exactly as he
does now.

**Phase 4: the learning loop.** The first real round is the next export covering 10/6–10/18 after
James submits those entries. It compares his submitted text with the October Part 2 workbook.
From then on, every round's diff writes its confirmed and rejected links. Alias proposals draw on
them, and the recommendations email reports how many links the round added.

Later, not in this build: James's calendar through Graph. The 8/30 retrospective found that 25 of
the 40 REVIEW entries James filled himself were calls, invoice work or meetings with no email trace,
and his calendar is the only source that reaches them. It needs a Calendars.Read grant on his
mailbox, which is a permissions decision for later.

---

## Watch-outs

- **Aspen exports have no entry ID.** Matching a recommendation to the later submitted entry relies on
  (date, matter, hours, order). If James changes the hours and the matter on the same entry, the
  match fails, and the learning pass counts it as `deleted` plus a new entry. The learning call
  should get those as possible pairs.
- **The 195 matters missing from the Property Reference Table** (open question 1c in `MEMORY.md`)
  are still the biggest accuracy lever. Rocky lists the unknown matters it hits each round, sorted
  by hours, so the list shrinks.
- **A confirmed link is only as good as James's review.** An accepted draft confirms the email
  Rocky cited, even if James accepted it without opening the email. Two things limit the damage: a
  later recode flips the verdict, and an alias built from links still needs his YES.
- **The run has to know what day it is.** A spreadsheet sent Sunday night reaches Rocky before Monday's
  07:xx batch exists, so the coverage check holds the Sunday entries until that batch lands or the
  wait expires.
- **Docs to update when this is built:** `BUILD_REFERENCE.md`, where phase 3 now belongs to Rocky;
  `DATA_SECURITY.md`, for the new data store, the links table and the read-only path to Minotaur;
  and a new `TIME_ENTRIES.md` operator guide in Rocky's usual style.
