# LetterStream — certified mailings + approved affidavits in The Vault

Rocky's certified-mail process (`--letterstream`, legacy alias `--letterstream`, suggested
schedule 8:00 AM daily). Built 2026-08-06.

## What it does

The firm sends notices by certified mail through
[LetterStream](https://www.letterstream.com). Each mailing needs an
**Affidavit of Certified Mailing** — affirmed by whoever did the
mailing: **anyone who submits a request is the affiant and approver of
the resulting affidavit** (2026-08-31; the configured default for
mailings with no known submitter is Hailey Mondragon, the Legal
Administrative Assistant) — filed together with LetterStream's
**proof of mailing** PDF. Rocky closes that loop:

1. **Discover** — the team's channel (primary): after mailing through
   the LetterStream website, download the **Proof of Mailing PDF** from
   the job's page and **email it to rocky@gallagherllp.com with "proof
   of mailing" in the subject** (`affidavit_subject_keyword`). The 8 AM
   sweep picks it up (firm senders only, PDF attachments only, SHA-256
   deduped) and replies to the submitter with the `[AM-####]` tag(s)
   created. `--fetch <tracking#>` and `--ingest <proof.pdf>` do the same
   from the command line. *Why email instead of an API pull:
   LetterStream confirmed (2026-08-16) their API can neither list
   website-submitted jobs nor serve their proofs — see "API reality"
   below. The automatic pull exists in the code and wakes up only if
   that changes.*
2. **Draft** — Claude reads the proof and extracts the tenant, address,
   property, and a documents-mailed description in the affidavit's house
   style (exemplar: *Certified Mailing Affidavit - NTPR (5/29/26) -
   Keyanna Brathwaite*). Rocky renders the affidavit `.docx` — with the
   conformed `/s/` signature and the preparation date — from the firm's
   **template** at `<affidavit_root>\_affidavits\template.docx`
   (auto-seeded from the bundled default, which is James's approved
   2026-08-16 layout: signature blocks in a borderless table, single
   spacing). **Edit that share file to change formatting — no rebuild
   needed.** It uses these placeholders, each of which must survive any
   edit: `{{TENANT}}`, `{{ADDRESS_LINE1}}`, `{{ADDRESS_LINE2}}`,
   `{{ADDRESS_FULL}}`, `{{WHEN_MAILED}}`, `{{DOCUMENTS_MAILED}}`,
   `{{AFFIANT}}`, `{{AFFIANT_TITLE}}`, `{{SIGN_DATE}}`. (Delete the
   share copy to re-seed the bundled default. The bundled source is
   `_templates/affidavit_template.docx` in the repo.)
3. **Approve** — Rocky emails the affidavit + proof from rocky@ back to
   **the submitter** (who is also the affiant named on the document;
   `affidavit_approver` — Hailey — for API/CLI mailings with no known
   submitter), tagged `[AM-####]`. Only that person's **YES** reply (or
   James's) is the recorded authorization for that exact document; the
   email tells them to check their name and title in the signature
   block. Per-person titles come from `affidavit_affiant_titles`
   (`{email: title}`), defaulting to `affidavit_affiant_title`. **NO**
   (with a note) sets it aside in `Declined\` and emails James the note.
   An unrecognized reply gets a polite "reply YES or NO" nudge.
   Unanswered items are re-sent every `affidavit_reminder_days`
   (default 3).
4. **File** — on YES, Rocky files the affidavit (PDF via Word when
   available, else the `.docx`) and the proof of mailing into **The
   Vault** through the Vault's normal machinery, so both appear in
   `Vault Index.xlsx`:

       The Vault\<Property>\<Tenant, Name>\
           Certified Mailing Affidavit - <Tenant> - <mail date>.pdf
           Proof of Mailing - <Tenant> - <mail date>.pdf

A human approves every affidavit; nothing is vaulted automatically. Below
the extraction-confidence floor (0.75) the approval email says PLEASE
CHECK EVERY FIELD, but the flow is the same. Approvals are polled
*before* the LetterStream pull, so a YES gets filed even on a day the
API is down.

## Outbound: Rocky sends the certified mail (built 2026-08-16)

The fully-automatic path. API-submitted jobs — unlike website ones — have
queryable status, tracking, and proofs, so when Rocky does the
submitting, the entire chain runs itself:

1. **Request** — a firm sender emails rocky@ with **"certified mail"**
   in the subject (`mail_request_keyword`) and **one PDF** attached: the
   complete packet to mail, in mailing order. Several PDFs on one email
   is a **batch** — one mailing per PDF, one confirmation for all of
   them (see "Batches" below). Address instructions in the body override
   the document's addressee. (Or: `rocky.exe --letterstream --mail
   <packet.pdf>` / `--mail-batch <folder>` — requester is James.)
2. **Preauth** — Rocky extracts the recipient, submits to LetterStream
   with `preauth=1` (**nothing prints, mails, or bills**), and gets the
   exact cost quote. **No cost cap** (James, 2026-09-21): the
   LetterStream account's own limits and prepay balance are the
   ceiling, and the release email quotes the cost before anyone
   authorizes it. A preauth that comes back with no readable quote is
   still refused — Rocky won't ask for a YES on an unknown number. The
   same document to the same recipient is never submitted twice from
   email (SHA-256; a deliberate re-mail goes through `--mail`).
3. **Release** — the requester gets a `[CM-####]` email quoting the
   recipient, page count, and cost. **The requester approves their own
   mailings** — any firm team member can use the channel, and only that
   requester (or James) can reply YES; that reply releases the job for
   printing/mailing/billing (LetterStream `doauth`). NO cancels — an
   unreleased job costs nothing and ages out. The same reply also
   answers **whether to prepare the Certified Mailing Affidavit**: a
   plain YES (or "Yes, with affidavit") queues it; "Yes, no affidavit"
   releases without one. Ambiguous phrasing defaults to WITH affidavit
   (the safe direction — the requester can still decline it at signing).
4. **Track → affidavit** — each morning run polls the in-flight jobs.
   Once USPS accepts one, Rocky pulls the proof of mailing via the API
   and the normal affidavit flow takes over (affidavit → the requester
   as affiant → YES → Vault). Zero manual steps between the release YES
   and the vaulted affidavit. No-affidavit jobs are just tracked to
   completion.

### Batches: many mailings, one confirmation (built 2026-09-21)

Attach **several PDFs** to one "certified mail" request email and Rocky
treats each PDF as its own mailing — its own recipient extraction, its
own `[CM-####]` — then confirms all of them in **one `[CMB-####]`
email** listing every piece with its recipient, matter number, page
count, and cost, plus the batch total. **One YES releases the whole
batch.** One NO cancels it. The requester releases their own batch,
same as a single mailing (James can release or cancel any of them).

- **Dropping one piece.** Reply NO with just that piece's `[CM-####]`
  tag in the subject; it's cancelled and the batch stays releasable, its
  total reduced. Then YES on the `[CMB-####]` email releases the rest.
- **Pieces that can't be prepared** (no readable address, LetterStream
  refuses the preauth or returns no cost quote) are itemized in the
  same confirmation email and left out of the batch. They never generate
  their own failure email — the batch speaks once.
- **No cost guard, by design** (James, 2026-09-21). The account's own
  limits govern; the confirmation email states each piece's cost and
  the total, and nothing bills until someone replies YES.
- **Size guard.** `mail_batch_max_pieces` (default 25) refuses an
  oversized request outright: nothing is submitted, and the requester is
  asked to split it. `letterstream_daily_submission_limit` (default 50,
  their Method-2 ceiling) makes the 51st submission of a day fail with
  "resend tomorrow" instead of being silently rejected.
- **Reminders** go out on the batch, once, never piece by piece.
- **Affidavits stay per-mailing.** The release reply's affidavit choice
  ("Yes, no affidavits") applies to the whole batch, but each mailing
  still produces its own affidavit that comes back for its own YES —
  every signature is authorized by a reply to that one document. A
  20-piece batch is 1 release reply and 20 affidavit replies.
- **From the command line:** `--letterstream --mail-batch <folder>`
  batches every PDF in a folder (a glob like `C:\Notices\*.pdf` works
  too), requester James.

A single PDF still takes the original one-piece path with its own
`[CM-####]` email — nothing about single requests changed.

**Mailing date/time policy (2026-08-17, time source refined
2026-08-26):** the affidavit swears to the date and time the mailing
was **communicated to LetterStream**, not the later date LetterStream
hands it to USPS. For Rocky-released jobs that's the `[CM-####]`
release moment (stamped at release; the confirmation email states it).
For website-submitted mailings, Rocky reads the LetterStream **job
number off the proof's cover page** (e.g. `14102628.1.1fc-21` → job
14102628) and pulls the submission timestamp through the API
(`jobstatus`; the earliest production-stage timestamp). If no time can
be found the affidavit shows the date alone — Rocky never guesses a
time — and the date falls back to the notice's own date, subject to
Hailey's review either way. Style note: the documents-mailed clause
abbreviates the Violence Against Women Act as **VAWA**.

Working copies of requested packets live in `<affidavit_root>\Outbound\`
(declined ones move to `Declined\`). The firm return address printed on
the coversheet comes from `mail_from` (defaults to the Baltimore
office); mail type from `letterstream_mailtype` (`certified` = with
Electronic Return Receipt). Method-2 API limit: 50 submissions/day,
enforced locally by `letterstream_daily_submission_limit`.

**Naming convention:** the uploaded file and the LetterStream job are
named **"LastName Matter#"** (e.g. `Brathwaite 1234.001.pdf`, job
`Brathwaite 1234.001 488383` — job names must be unique account-wide,
hence the suffix). The matter number is extracted from the request
email — **requesters should state it in the body** ("matter 1234.001");
if none is found, the `[CM-####]` approval email says NOT GIVEN so the
requester can NO-and-resend. The `Outbound\` working copy uses the same
name prefixed with the tag.

**Expedited production (pending support answer):** the firm wants
expedited as the default, but the Feb 2023 API doc has no expedite
field. Ask LetterStream support for the API argument matching the
website's expedite option; when they name it, put it in config
`letterstream_extra_fields` (e.g. `{"expedite": "1"}`) — it merges into
every submission, no rebuild.

## Daily visibility: the Multifamily Digest

`rocky.exe --multifamily-digest` (5:30 PM daily; dashboard "Multifamily
Digest") builds one digest covering this process end to end — certified
mail requested/released/mailed (with costs, approvers, and tracking;
batches appear as one line, not one per piece),
affidavits sent/filed/declined, the day's Vault additions, and a
snapshot of everything still waiting on a reply. It subsumes the old
standalone Vault Digest. Since 2026-08-29 it is created as a DRAFT in
James's Drafts folder, pre-addressed to the multifamily group (same
model as the Maple Digest) — James reviews and sends; Rocky no longer
emails it from rocky@. Recipients: built-in group list, override with
`multifamily_digest_draft_recipients`. Quiet day = no draft.

## Commands

| Command | What it does |
|---|---|
| `rocky.exe --letterstream` | Full run: inbox sweep (approvals, proof submissions, mail requests) → track in-flight mailings → reminders |
| `--letterstream --mail <packet.pdf>` | Request an outbound certified mailing from the command line (preauth → `[CM-####]` approval email) |
| `--letterstream --mail-batch <folder\|glob>` | Request every PDF in a folder as one batch (preauth each → one `[CMB-####]` email whose single YES releases them all) |
| `--letterstream --dry-run [--limit N]` | Log what would happen; no emails, no submissions, no state changes |
| `--letterstream --fetch <tracking#>` | Pull ONE mailing's proof from the LetterStream API by USPS certified tracking number (or unique doc id) and run the pipeline — **the day-to-day discovery path** (see "API reality" below) |
| `--letterstream --ingest <proof.pdf>` | Run ONE manually downloaded proof through the same pipeline (needs no API at all) |
| `--letterstream --probe` | Verify LetterStream API auth (accountstatus request; prints raw response + prepay balance) |
| `--letterstream --status` | Pending affidavits, cursors, config state |

**From the Rocky Dashboard** (see `DASHBOARD.md`): the sidebar's
**Certified Mail** card shows everything pending (affidavits awaiting
approval, mailings awaiting release — batched pieces marked with their
`[CMB-####]` and its total — and jobs in the mail) straight from the
process's state file, and its **Fetch proof** box runs `--fetch` for a
pasted tracking number. The full sweep is in the Run a Command list
(group "Mail", schedulable at the suggested 8:00 AM) — though if the
`--monitor` loop is running, it already sweeps every 10 minutes and a
daily task is redundant (safe, but skip it).

## Prerequisites

1. **LetterStream API activation** (the gating step). API access is
   granted per account: email **support@letterstream.com** from the
   account's sign-up address with a quick overview (law firm, pulling
   proof-of-mailing PDFs for internally generated affidavits, low
   volume, **Automation** mode). Once approved, the API documentation
   and sample code unlock under **My Account** in the LetterStream
   portal, along with the API ID/key.
2. **Config** (`config.json` on the Rocky laptop):
   - `letterstream_api_id` / `letterstream_api_key`
   - `affidavit_approver` — Hailey's firm email, the DEFAULT approver
     for mailings with no known submitter; email-submitted requests are
     approved by their own submitter (or James) instead
   - batch guards: `mail_batch_max_pieces` (25),
     `letterstream_daily_submission_limit` (50) — no cost cap by design
   - optional: `affidavit_root`, `affidavit_cc`, `affidavit_affiant_name`
     / `_title`, `affidavit_affiant_titles` (`{email: title}` overrides
     for the signature block), `affidavit_reminder_days`,
     `affidavit_backfill_days`, `affidavit_pdf`, `user_display_name`
     (James's name on affidavits he requests from the CLI)
3. **Share folder** — default `<cases_root parent>\Mailing Affidavits`
   (created on first run: `Pending\`, `Approved\`, `Declined\`,
   `_affidavits\activity.jsonl`). Pin "Always keep on this device" on
   the Rocky laptop.
4. **Task Scheduler** — daily 8:00 AM entry running
   `rocky.exe --letterstream` (same pattern as the other Rocky jobs).
5. No new Graph permissions: inbox reads use the app token
   (Application Access Policy already covers rocky@), approval emails go
   out from rocky@ through `outbound.send_mail_guarded` (internal-only
   allowlist — Level 0 holds).

## API reality (calibrated 2026-08-06 against the real docs)

`letterstream.py` implements LetterStream's documented API ("Mail
Fulfillment by LetterStream — Integration API", Feb 3, 2023 PDF from My
Account → API Information):

- **Auth** (every request): `a` = API_ID, `t` = a unique numeric id
  10–18 digits that LetterStream accepts **only once ever** (Rocky uses
  millisecond timestamps with a monotonic bump), `h` =
  `md5(base64(last6(t) + API_KEY + first6(t)))`. Probe responses:
  `AUTHOK` good / `IDOK` id found but hash failed / `DUP` reused t /
  `BAD` bad id.
- **Available calls**: `accountstatus=1`; `jobstatus=` / `docstatus=` /
  `batchstatus=` for **known** ids; `cert=<tracking#>` or
  `doc_id=<id>` + `getinfo=track|trackx|sig|proof`
  (`trackx` + `responseformat=json` returns JSON; `proof` streams the
  proof-of-mailing PDF, raw or base64 — the client handles both).
- **Missing**: there is **no call that lists recent jobs**. The API only
  answers about ids you already know. Consequences:
  - Day-to-day discovery is `--fetch <tracking#>` — Hailey (or anyone)
    grabs the certified tracking number from the LetterStream dashboard
    (or the mailing confirmation) and Rocky does the rest.
  - **Open question for LetterStream support**: is there an API call to
    enumerate recent jobs (the dashboard clearly has the data)? If they
    provide one, put its POST params in config
    `letterstream_list_params` — `list_recent_mailings()` will use them
    and the 8 AM run becomes fully automatic with no code change. (Their
    "API PUSH" tracking callback exists but requires a public web
    endpoint, which doesn't fit Rocky's architecture.)
  - Raw API responses are appended to
    `C:\Rocky\affidavits\letterstream_raw.jsonl`; extend `_JOB_KEYS` in
    `letterstream.py` from real data if the normalizer misses fields.

The client is read-only against LetterStream (status + download; it
never creates or modifies mail jobs). The extraction, generation,
approval, and vault steps are independent of all this and already
tested end-to-end via `--ingest`.

## Approval semantics (why the /s/ is applied before the YES)

Each affidavit is emailed to its own affiant — the person who submitted
the request — already bearing *their* conformed `/s/` signature and its
preparation date, and the approval email says so explicitly. That way
their YES approves the *exact bytes* that get filed — nothing is altered
after they approve — and nobody's signature is ever authorized by
someone else's reply (James, as supervising attorney, can also approve
or decline any affidavit). Rocky records who approved, when, and from
what address in the activity log and in the Vault catalog entry
(`approved by <address> via [AM-####] email reply`). Declines never file
anything.

## Files and state

| Where | What |
|---|---|
| `<affidavit_root>\Pending\` | Working copies awaiting a reply (`AM-#### Certified Mailing Affidavit - <Tenant>.docx` + proof) |
| `<affidavit_root>\Approved\` / `Declined\` | Where those copies move on resolution (the Vault holds the canonical filed documents) |
| `<affidavit_root>\_affidavits\activity.jsonl` | Audit trail (proposed / approved / declined / reminders / errors) — travels with the share |
| `C:\Rocky\affidavits\state.json` | Local state: `[AM-####]` / `[CM-####]` / `[CMB-####]` counters, the pending, `mail_pending`, `mail_batches`, and `in_flight` queues, processed LetterStream job IDs, proof SHA-256s, per-day submission counts, approval-mail cursor |
| `C:\Rocky\affidavits\letterstream_raw.jsonl` | Raw API responses for calibration |

## Failure behavior

- LetterStream API error → pull skipped, logged, nothing lost (jobs are
  only marked processed after a successful proposal).
- Claude extraction failure or unreadable proof → job held and retried
  next run (`extraction_incomplete` / `proof_unreadable` in the activity
  log).
- Approval email send failure → the affidavit stays pending and the
  reminder pass retries.
- Approved but pending files missing (share mishap) → nothing vaulted,
  James emailed to investigate.
- Duplicate protection: LetterStream job IDs and proof SHA-256s are both
  tracked; a re-listed or re-ingested mailing is skipped.
