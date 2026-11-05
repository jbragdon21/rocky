# Joe's Email Archive: design

Proposed 2026-11-03. Steps 1 and 2 of the build order (the access check, the
backfill and the search) were built 2026-11-04 on the `joe-archive` branch and
have not run against Joe's mailbox; see "Status" at the end. The open
questions need James's answers before the backfill runs.

Rocky copies every message in jdugan@'s Sent Items to the Rocky laptop and
indexes it in a local SQLite database. Joe asks Rocky questions in a 1:1 Teams
chat. Rocky searches, offers what it found, and on Joe's YES emails him the
original message as an attachment.

---

## 1. Mailbox access (check before anything else)

Rocky has two proven ways into a colleague's mailbox, and "Rocky has
permission" could mean either one:

| Path | What IT did | Token | Precedent |
|---|---|---|---|
| App token | Added the mailbox to the Application Access Policy group | `acquire_app_token` (client credentials, `Mail.Read` application) | Inbox Cleaner step 1; Ella's folder reads |
| Delegated | Gave rocky@ Full Access on the mailbox | rocky@'s device-code token (`Mail.ReadWrite.Shared`, consented 2026-07-05) | Minotaur; Inbox Cleaner `write_via: delegated` |

The `acquire_app_token` docstring lists rocky@, eaiken@, jbragdon@ and
smetzger@ in the policy. jdugan@ is not on that list, so the grant is either
Full Access or a policy change the docstring doesn't record.

`--joe-archive --check` tries both and prints:

- which path works
- the Sent Items item count and total size
- the count of Joe-authored mail outside Sent Items (mail he files into
  matter folders would otherwise be missed)
- whether an Online Archive exists, if Graph exposes it

Rocky only reads Joe's mailbox. No step in this design writes to it.

**Online Archive.** Graph could not reach James's Online Archive. His archived
sends had to be moved into rocky@'s mailbox (`Inbox\__Older Sent Items`,
2026-08-22) before the brain could ingest them. If Joe's older sends sit in an
Online Archive, they need the same move.

---

## 2. Backfill and sync

**Backfill** (`--joe-archive --backfill [--limit N]`) runs from the command
line on the Rocky laptop, outside the monitor, because it takes hours.

1. Page through Sent Items with `$select` metadata, oldest first.
2. Download each message's full MIME (`GET /messages/{id}/$value`) to
   `C:\Rocky\joe_archive_data\eml\YYYY\MM\<id-hash>.eml`. The `.eml` carries the
   message's attachments, so it serves as the archive copy and as the
   attachment Joe later receives.
3. Parse the `.eml`, split Joe's own text from the quoted history, and write
   the row (section 3).
4. Checkpoint after every page so a rerun resumes.

Port the proven pieces from Minotaur's `email_brain.py` rather than importing
Minotaur: `TokenKeeper` (45-minute refresh), checkpointed streaming ingest,
`internetMessageId` dedup, and quoted-history recovery. Skip read receipts and
meeting responses.

**Sync** (`--joe-archive --sync`) runs nightly from Task Scheduler. It uses a
delta query on Sent Items and stores the `deltaLink` in
`C:\Rocky\joe_archive_data\state.json`. A message Joe later deletes stays in the
archive.

**Storage** stays on `C:\Rocky\joe_archive_data\` and never goes on OneDrive. It
holds privileged mail from another attorney's practice, and OneDrive handles
100,000 small files badly. The mailbox is the source of truth, so the archive
can be rebuilt and needs no backup. Check free disk space against the size
`--check` reports before the backfill.

**Time.** Store UTC and convert to Eastern with `zoneinfo`. Time Entries found
that a flat UTC-4 offset misdates mail after DST ends.

---

## 3. Database and search

`C:\Rocky\joe_archive_data\archive.db`:

```
messages
  id               INTEGER PRIMARY KEY      -- the "ref" Claude and Joe see
  graph_id         TEXT
  internet_msg_id  TEXT UNIQUE
  conversation_id  TEXT
  sent_utc         TEXT
  subject          TEXT
  to_list, cc_list, bcc_list  TEXT          -- "Name <addr>; ..."
  attachment_names TEXT
  has_attachments  INTEGER
  body_own         TEXT                     -- what Joe wrote
  body_quoted      TEXT                     -- the history below it
  eml_path         TEXT
  eml_bytes        INTEGER

messages_fts  USING fts5(subject, recipients, body_own, body_quoted,
                         attachment_names, content='messages',
                         tokenize='unicode61 remove_diacritics 2',
                         prefix='2 3')
```

**Why FTS5.** Joe wants specific language, and FTS5 handles that well: exact
phrases (`"right of first refusal"`), AND/OR/NOT, `NEAR`, prefixes
(`indemnif*`), bm25 ranking and `snippet()` excerpts. Rocky's Python has it
(SQLite 3.50.4, checked 2026-11-03). The `unicode61` tokenizer does no
stemming, so a phrase search matches what Joe wrote word for word; Claude adds
variants with prefixes when it wants them. Keeping `body_own` apart from
`body_quoted` lets a search target Joe's own words.

**One search function** serves the CLI and Claude:

```
search(query, date_from=None, date_to=None, recipient=None,
       subject=None, fields=("all"|"own"), limit=20)
```

It builds parameterized SQL. Claude never writes SQL. `--joe-archive --search
"..."` lets James test queries before Joe sees anything.

**Later, only if testing shows misses:** index attachment text (PDF and DOCX),
and add embeddings for conceptual questions (Voyage, as Minotaur's brain does).
Claude turning one question into several keyword searches covers much of that
ground, so start without them.

---

## 4. The Teams chat (part of the monitor)

Two Rocky processes already run this shape. Inbox Cleaner keeps one live
proposal at a time and counts only the mailbox owner's replies. The Litigation
Updater follows the chat in the background after activity, and it already
emails documents in answer to free-text requests ("do you have the settlement
agreement in the Johnson case").

**Setup.** `teams.ensure_one_on_one_chat(rocky@, jdugan@)`, with the chat id
saved in state. The Teams scopes are already consented.

**Each monitor cycle** (every 10 minutes) fetches chat messages since the
cursor. Rocky makes no Claude call unless Joe wrote something. Only Joe's
Teams user id counts; any other sender is logged and ignored.

**Joe writes, and Rocky runs one Claude tool-use loop.** The two calls in the
first sketch (Claude picks a search, then Claude writes the answer) become one
loop, so Claude can search again when the first try returns nothing or 300
hits:

| Tool | What Rocky does |
|---|---|
| `search_archive` | Runs `search()` locally, returns ranked lines: ref, date, recipients, subject, snippet |
| `read_message` | Returns one message's full text by ref |
| `submit_reply` | Ends the loop: the chat text, plus the refs to offer |

Cap the loop at about six tool calls. Claude sees only result lines and the
messages it asks to read. The calls go through `claude_call` (Opus 5.5).

**The reply.**

- One strong match: date, recipients, subject and a snippet, then "Want me to
  email it to you? YES or NO."
- Several: a numbered list of up to five, then "Reply with the numbers you
  want, or NO."
- Nothing: what Rocky searched, and a question that might narrow it.

**Joe's next message.** YES or numbers go to delivery. NO closes the offer.
Anything else is a new question, and the offer lapses. A regex settles the
plain answers; an ambiguous one goes to Claude with the pending offer in view.

**Speed.** Run the chat step in the background the way
`MONITOR_LITIGATION_ARGS` does (`--joe-archive --chat --poll
--follow-if-active`). After Joe writes, Rocky stays on the chat and checks
every 15 seconds until it goes quiet. Joe's first message can wait up to one
monitor interval; after that the conversation moves quickly. Idle checks cost
a Teams fetch and no Claude call.

**Log.** Every message both ways goes to `C:\Rocky\joe_archive_data\chat.jsonl`.

---

## 5. Delivery

On YES, Rocky sends from rocky@ to Joe through `outbound.send_mail_guarded`
with the `.eml` attached. Outlook opens it as the original message, its
attachments included.

- Subject: `From your sent mail: <original subject>`. The body gives the
  date and recipients.
- The recipient is fixed by config (`joe_archive_owner`). Claude chooses which
  messages to offer and never who receives them. Archived mail is untrusted
  text fed to Claude, and this rule is what keeps a planted instruction in an
  old email from sending anything anywhere; the most it can do is offer Joe
  the wrong message.
- Sending from rocky@ fits the outbound allowlist (internal recipients only).
  A draft in rocky@'s Drafts would be invisible to Joe, and a draft in Joe's
  mailbox would need write access there.
- **Size.** `send_mail_guarded` puts attachments inline in one `sendMail`
  request, and Graph takes attachments only up to 3 MB that way. Larger messages need the draft
  path: create the draft in rocky@'s mailbox, upload with
  `createUploadSession`, then send. Build it inside `outbound.py` so the
  allowlist guard stays in one place.

---

## 6. Data security

- Joe's mail reaches Anthropic as search result lines and whatever messages
  Claude reads, under the same terms as James's mail. Add this flow to
  `DATA_SECURITY.md`.
- The archive sits at rest on the Rocky laptop. Confirm BitLocker is on.
- Joe should know about the archive and agree before the backfill runs.

---

## 7. Commands

```
rocky.py --joe-archive --check
rocky.py --joe-archive --backfill [--limit N]
rocky.py --joe-archive --sync
rocky.py --joe-archive --search "query" [--from DATE] [--to DATE]
rocky.py --joe-archive --chat [--poll] [--follow-if-active] [--dry-run]
```

Config: `joe_archive_mailbox`, `joe_archive_owner` (both jdugan@),
`joe_archive_root` (default `C:\Rocky\joe_archive_data`), `monitor_joe_archive`
(default true once live). Dashboard entry under its own group.

---

## 8. Build order

1. `--check`, run by James on the Rocky laptop. It settles access and size.
2. Backfill, database and `--search`. James tries the kinds of questions Joe
   would ask.
3. The chat loop with `--dry-run`: Rocky composes replies and logs them,
   sending nothing. Then live with Joe.
4. Delivery, with the upload-session path.
5. Nightly sync on Task Scheduler.

---

## Open questions

1. Which grant does Rocky have on jdugan@? `--check` answers this.
2. Does Joe have an Online Archive, and does he file sent mail out of Sent
   Items?
3. Has Joe agreed, and does the firm need to sign off on archiving a second
   attorney's mail on the Rocky laptop?
4. Should James sit in the chat as an observer during the pilot, or should it
   stay 1:1 with Joe? The design assumes 1:1, since the results are Joe's mail.

---

## Status (2026-11-04): steps 1 and 2 built

Package `joe_archive/` (graph, store, search, ingest, cli), wired into
`rocky.py --joe-archive` and `build_exe.py` (`--collect-submodules`). Tests:
`tests/test_joe_archive.py` (37, no network); the full suite passes.

What differs from the sections above:

- **The data folder is `joe_archive_data`**, since in dev mode the data
  directory is the repo and `joe_archive\` is the package.
- **`--check` tries three auth paths in order**: `app`, `delegated-read`
  (Mail.Read.Shared) and `delegated` (Mail.ReadWrite.Shared). On a 404 it
  retries the mailbox on the legacy gejlaw.com domain. It records the first
  path that reads Sent Items in `state.json`, and `joe_archive_auth` in
  config can pin one. It also runs the backfill's exact listing request and
  one MIME download, so a bad request shape shows up before the backfill.
- **Filed sends** are counted per folder with
  `$filter=from/emailAddress/address eq ...&$count=true`. A folder whose
  count Graph won't give is reported as uncounted. Legacy-DN from-addresses
  (seen in James's archive, 8/22) won't match that filter.
- **Skipped classes**: `REPORT.*` (read receipts), `IPM.Schedule.Meeting.Resp*`,
  `IPM.Recall*` and `IPM.Outlook.Recall`, read from PR_MESSAGE_CLASS through
  `$expand=singleValueExtendedProperties`. Meeting requests Joe sent are kept.
- **Failures** go to a `failures` table and are retried at the start of the
  next run (five tries). A page on which every download fails stops the run,
  so a lost token can't fill that table.
- **The backfill stops** with under 2 GB free (`joe_archive_min_free_gb`).
- **`--sync`** is the cursor-based backfill rerun with a two-day overlap.
  The delta query in section 2 isn't needed for that, and a send Joe deletes
  stays archived either way.
- **Locks**: `--check`, `--backfill` and `--sync` share the `joe-archive`
  lock. `--search`, `--show` and `--stats` take none, so they work during a
  backfill (archive.db is in WAL mode).
- **Search excerpts** come from Joe's own text when the match is there, then
  the quoted history, then the subject or recipients.

## Status (2026-11-05): deployed, access checked

- Merged into `time-entries` and deployed in rocky.exe 11/5 12:06.
- `--check` on the Rocky laptop: the **app** path reads jdugan@'s Sent
  Items (67,219 items, 15.1 GB, 2019-03-18 to 11/5). Listing and MIME
  download work; 566 GB free on C:.
- It also read `sample classes: (none) x10`. `message_class` compared the
  property id as text with `String 0x001A`, but Graph answers with its
  own form (`String 0x1a`), so every class read blank. That would have
  archived read receipts and recalls and stored no class. Fixed by
  comparing the parsed tag (`graph.prop_tag`). The test fixtures now use
  Graph's form; nine tests fail on the old code. When a class still reads
  blank, `--check` prints the property ids Graph returned.

Next on the Rocky laptop, after a deploy:

1. `rocky.exe --joe-archive --check`
2. `rocky.exe --joe-archive --backfill --limit 200`, then a few `--search`
   runs on what it pulled
3. `rocky.exe --joe-archive --backfill` until it finishes (rerun to resume)

