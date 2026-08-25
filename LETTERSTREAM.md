# LetterStream — certified mailings + approved affidavits in The Vault

Rocky's certified-mail process (`--letterstream`, legacy alias `--letterstream`, suggested
schedule 8:00 AM daily). Built 2026-08-06.

## What it does

The firm sends notices by certified mail through
[LetterStream](https://www.letterstream.com). Each mailing needs an
**Affidavit of Certified Mailing** — affirmed by Hailey Mondragon, the
Legal Administrative Assistant — filed together with LetterStream's
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
3. **Approve** — Rocky emails the affidavit + proof from rocky@ to
   `affidavit_approver` (Hailey), tagged `[AM-####]`. Her **YES** reply is
   the recorded authorization for that exact document. **NO** (with a
   note) sets it aside in `Declined\` and emails James the note. An
   unrecognized reply gets a polite "reply YES or NO" nudge. Unanswered
   items are re-sent every `affidavit_reminder_days` (default 3).
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
   in the subject (`mail_request_keyword`) and **exactly one PDF**
   attached: the complete packet to mail, in mailing order. Address
   instructions in the body override the document's addressee. (Or:
   `rocky.exe --letterstream --mail <packet.pdf>` — requester is James.)
2. **Preauth** — Rocky extracts the recipient, submits to LetterStream
   with `preauth=1` (**nothing prints, mails, or bills**), and gets the
   exact cost quote. Quotes above `mail_max_cost` (default $50) are
   refused outright. The same document to the same recipient is never
   submitted twice from email (SHA-256; a deliberate re-mail goes
   through `--mail`).
3. **Release** — the requester gets a `[CM-####]` email quoting the
   recipient, page count, and cost. **The requester approves their own
   mailings** — any firm team member can use the channel, and only that
   requester (or James) can reply YES; that reply releases the job for
   printing/mailing/billing (LetterStream `doauth`). NO cancels — an
   unreleased job costs nothing and ages out. The same reply also
   answers **whether to prepare the Certified Mailing Affidavit**: a
   plain YES (or "Yes, with affidavit") queues it; "Yes, no affidavit"
   releases without one. Ambiguous phrasing defaults to WITH affidavit
   (the safe direction — Hailey can still decline it at signing).
4. **Track → affidavit** — each morning run polls the in-flight jobs.
   Once USPS accepts one, Rocky pulls the proof of mailing via the API
   and the normal affidavit flow takes over (affidavit → Hailey → YES →
   Vault). Zero manual steps between the release YES and the vaulted
   affidavit. No-affidavit jobs are just tracked to completion.

**Mailing-date policy (2026-08-17):** the affidavit swears to the date
and time the mailing was **communicated to LetterStream** — i.e. the
`[CM-####]` release — not the later date LetterStream hands it to USPS.
The release confirmation email states the exact date/time that will
appear on the affidavit. (Website-submitted mailings that come in via
the proof-of-mailing email channel still use the notice's own date,
subject to Hailey's review, since Rocky can't see when those were
submitted.)

Working copies of requested packets live in `<affidavit_root>\Outbound\`
(declined ones move to `Declined\`). The firm return address printed on
the coversheet comes from `mail_from` (defaults to the Baltimore
office); mail type from `letterstream_mailtype` (`certified` = with
Electronic Return Receipt). Method-2 API limit: 50 submissions/day.

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
Digest") sends one email covering this process end to end — certified
mail requested/released/mailed (with costs, approvers, and tracking),
affidavits sent/filed/declined, the day's Vault additions, and a
snapshot of everything still waiting on a reply. It subsumes the old
standalone Vault Digest. Recipients: `multifamily_digest_recipients`.
Quiet day = no email.

## Commands

| Command | What it does |
|---|---|
| `rocky.exe --letterstream` | Full run: inbox sweep (approvals, proof submissions, mail requests) → track in-flight mailings → reminders |
| `--letterstream --mail <packet.pdf>` | Request an outbound certified mailing from the command line (preauth → `[CM-####]` approval email) |
| `--letterstream --dry-run [--limit N]` | Log what would happen; no emails, no submissions, no state changes |
| `--letterstream --fetch <tracking#>` | Pull ONE mailing's proof from the LetterStream API by USPS certified tracking number (or unique doc id) and run the pipeline — **the day-to-day discovery path** (see "API reality" below) |
| `--letterstream --ingest <proof.pdf>` | Run ONE manually downloaded proof through the same pipeline (needs no API at all) |
| `--letterstream --probe` | Verify LetterStream API auth (accountstatus request; prints raw response + prepay balance) |
| `--letterstream --status` | Pending affidavits, cursors, config state |

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
   - `affidavit_approver` — Hailey's firm email (approvals only count
     from this address or James's)
   - optional: `affidavit_root`, `affidavit_cc`, `affidavit_affiant_name`
     / `_title`, `affidavit_reminder_days`, `affidavit_backfill_days`,
     `affidavit_pdf`
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

## Approval semantics (why the /s/ is applied before her YES)

The affidavit emailed to Hailey already bears her conformed `/s/`
signature and its preparation date, and the approval email says so
explicitly. That way her YES approves the *exact bytes* that get filed —
nothing is altered after she approves. Rocky records who approved, when,
and from what address in the activity log and in the Vault catalog entry
(`approved by <address> via [AM-####] email reply`). Declines never file
anything.

## Files and state

| Where | What |
|---|---|
| `<affidavit_root>\Pending\` | Working copies awaiting a reply (`AM-#### Certified Mailing Affidavit - <Tenant>.docx` + proof) |
| `<affidavit_root>\Approved\` / `Declined\` | Where those copies move on resolution (the Vault holds the canonical filed documents) |
| `<affidavit_root>\_affidavits\activity.jsonl` | Audit trail (proposed / approved / declined / reminders / errors) — travels with the share |
| `C:\Rocky\affidavits\state.json` | Local state: `[AM-####]` counter, pending queue, processed LetterStream job IDs, proof SHA-256s, approval-mail cursor |
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
