# PACER Monitor

Rocky's federal-court practice. She watches dockets, records every new
filing, collects the documents she can get for free, discovers new cases
naming a client or a tenant, and keeps all of it in an indexed database
on the shared OneDrive.

Built 2026-09-06. Code: `pacer_monitor.py` (orchestration + database),
`courtlistener.py` (CourtListener/RECAP client), `pacer_api.py` (PACER
authentication + Case Locator).

---

## The data paths, and what each one costs

Federal court data reaches Rocky four ways. They differ enormously in
price, in freshness, and in whether they work at all on an MFA-protected
account, and most of the design follows from that.

**CourtListener / RECAP — free to read, severely rate-limited.**
The RECAP Archive holds hundreds of millions of PACER documents
contributed by people who bought them. Reading it through the
CourtListener v4 API costs nothing. What it costs instead is calls: Free
Law Project cut the default authenticated allowance on 2026-05-07 to
**5 per minute, 50 per hour, 125 per day**. That number shapes everything
below. A membership or commercial agreement raises it; James decided
2026-09-06 to run on the free tier and revisit.

**@recap.email — free, and the primary way docket contents arrive.**
Add your personal `@recap.email` address as a secondary NEF recipient in
each jurisdiction where you're admitted (PACER → Utilities → Maintain
Your E-mail → Add a New E-mail Address; notices yes, all cases yes,
verify free look no, per-filing HTML). CourtListener then ingests every
filing you're noticed on, plus its free-look PDF, at no PACER cost, and
Rocky reads it through the API she already uses.

This started as a nice-to-have and became load-bearing on 2026-09-06,
when RECAP Fetch turned out to be closed to a filer's account (below).
It covers every case where the firm is counsel of record, which is most
of them, and being email-driven it is immune to the login problem that
blocks Fetch.

**PACER Case Locator (PCL) — the nationwide index, billable per page.**
The only source that answers "what federal cases exist anywhere naming
this person or company." No docket entries, no documents: case metadata
and party rows. Results come back 54 to a page and each page retrieved is
billed. There is a free QA environment with test data. **This works on an
MFA account** because `pacer_api.py` sends `otpCode` itself.

**RECAP Fetch — UNAVAILABLE on a filer's account (confirmed live
2026-09-06).** `POST /recap-fetch/` hands CourtListener a PACER username
and password so it can buy a docket report or PDF. It fails here with
`PacerLoginException: Did not get NextGenCSO cookie`. Free Law Project's
own documentation states they "do not currently support PACER or CM/ECF
accounts with MFA enabled," and the AO has made MFA mandatory for
CM/ECF-level accounts. Their suggested workaround is to disable MFA,
which a filer cannot do.

This is a limitation of CourtListener's PACER login, not of Rocky's. Her
direct integration authenticates fine. Rocky records the block in
`state.json` the first time it happens and stops attempting purchases
rather than failing once a day forever; `--pacer --fetch` prints the
alternatives and `--retry-fetch` clears the block.

### The fix: a firm Case Search Only account

PACER's guidance is that filers and other CM/ECF-level users **must**
enroll in MFA, while users with **PACER-only access have the option**. A
Case Search Only account is PACER-only access, so it can run without MFA,
which is precisely what CourtListener's login requires. Registration is
free (there's still a per-page fee to read records), and it can view
dockets and documents in CM/ECF while being unable to file anything.

Register it in the **firm's** name rather than as a second personal
account. PACER consolidated attorneys' duplicate personal accounts (the
old CJA-plus-private-work pair is gone), but firms routinely hold
multiple accounts, and a **PACER Administrative Account** exists to
manage and pay for them together. Commercial PACER integrations use this
same pattern: a firm-level Case Search Only account is the credential
their automation runs on.

**The security argument is stronger than the capability argument.**
Today `C:\Rocky\config.json` holds a password and a TOTP seed for an
account with federal e-filing privileges. A search-only account cannot
file, so moving Rocky onto one removes that exposure entirely, and it
puts her PACER charges on a bill you can allocate.

Once the account exists, in `C:\Rocky\config.json`:

```json
  "pacer_username": "<the search-only login>",
  "pacer_password": "<its password>",
  "pacer_otp_secret": "",
  "pacer_redact_flag": false,
```

`pacer_otp_secret` goes empty because the account has no MFA, and
`pacer_redact_flag` goes back to false because a non-filer makes no
redaction certification. Then clear the block:

```
rocky.exe --pacer --fetch vaed 1:26-cv-00417 --retry-fetch
```

Confirm the MFA exemption with the PACER Service Center
(800-676-6856) before relying on it. The published language distinguishes
CM/ECF-level from PACER-only access, which is clear enough to act on but
worth one phone call given the whole purchase path depends on it.

---

## Commands

```
rocky.py --pacer [--sync] [--dry-run] [--limit N] [--case <court:number>]
rocky.py --pacer --sweep [--name <sweep>] [--dry-run]
rocky.py --pacer --mail [--dry-run]          (alias: --pacer-mail)
rocky.py --pacer --digest [--hours N] [--dry-run]
rocky.py --pacer --add <court> <docket#> [--label "..."] [--client "..."] [--rrid RRID-0001]
rocky.py --pacer --remove <court> <docket#>
rocky.py --pacer --fetch <court> <docket#> [--since YYYY-MM-DD]
rocky.py --pacer --docs <court> <docket#> [--entry N]
rocky.py --pacer --find "case name" [--court vaed]
rocky.py --pacer --probe <court> <docket#>
rocky.py --pacer --status | --reindex | --auth-test
```

`--find` searches the RECAP archive by name when all you have is a
caption. Free, one API call, and it prints a ready-to-paste `--add` line
for each hit. Everything else here wants a court and a docket number up
front; this is how you get them.

### Court IDs

The two systems spell courts differently, and getting it wrong costs
money rather than merely failing: a Case Locator search with no court
matches the same case number in every district in the country and bills
each page. `pacer_api.cl_court_to_pcl()` and `pcl_court_to_cl()` handle
the translation, and callers that can't map a court refuse to search
rather than searching everywhere.

| Court | You type (CourtListener) | PACER/PCL |
|---|---|---|
| E.D. Va. | `vaed` | `vaedc` |
| W.D. Va. | `vawd` | `vawdc` |
| D.D.C. | `dcd` | `dcdc` |
| D. Md. | `mdd` | `mddc` |
| E.D. Va. Bankr. | `vaeb` | `vaebk` |
| D.D.C. Bankr. | `dcb` | `dcbk` |
| D. Md. Bankr. | `mdb` | `mdbk` |
| Fourth Circuit | `ca4` | `04ca` |
| D.C. Circuit | `cadc` | `dcca` |

Docket numbers differ too: courts and CourtListener write
`1:25-cv-00123`, PCL writes `1:2025cv00123`. Both directions are handled,
and a Case Locator lookup retries in the other spelling if the first
finds nothing.

Dashboard buttons and their scheduled slots: **PACER Sweep** 6:30 AM,
**PACER Sync** 7:00 AM, **PACER Digest** 5:15 PM. **PACER Requests**
(`--pacer-mail`) has no slot because it runs in the `--monitor` loop
every ten minutes.

`--pacer-sweep` and `--pacer-digest` exist as standalone flags so each
scheduled job gets its own Task Scheduler entry, the same way
`--litigation-digest` works.

---

## `--sync`: the daily pass

Five steps, ordered so the cheap ones happen first and a budget stop
never leaves anything half-written.

1. **Resolve.** Cases with no RECAP docket id yet get one lookup each
   (`/dockets/?court=&docket_number=`). This is the expensive half — one
   call per case — so `pacer_resolve_per_run` caps it at 10 a run. A case
   RECAP has never seen is marked `not-in-recap` and retried later.
2. **Subscribe.** Every resolved case gets a free docket alert. This is
   what makes a growing watchlist survivable: CourtListener notices the
   change, and `date_last_filing` on the docket header tells Rocky
   whether it's worth pulling entries.
3. **Refresh headers, one call per case.** There is no batch. `?id__in=`
   does not exist on `/dockets/` (the `id` field is a number-range filter,
   and CourtListener rejects unknown filter parameters rather than
   ignoring them); `id__range` would group ids but drag in every
   unrelated docket in the span. So this step costs one call per watched
   case, which is the main thing setting the ceiling on watchlist size.
   Cases are refreshed oldest-synced first and anything the budget can't
   reach is deferred.
4. **Pull entries only where something moved.** A case whose
   `date_last_filing` hasn't passed the last entry Rocky recorded is
   skipped entirely. New entries are written to `_db\entries.jsonl` and
   any PDF already in the archive is downloaded — downloads come from
   `storage.courtlistener.com`, which is free, unauthenticated, and
   outside the API budget.
5. **Buy what's stale.** An active case RECAP hasn't seen move in
   `pacer_fetch_stale_days` (default 3) gets a docket report purchased
   through RECAP Fetch, scoped with `de_date_start` to the last entry
   already on file so PACER bills fewer pages. Each purchase is checked
   against the daily cap first, and no case is bought twice in a day.

### The call budget, and what happens when it runs out

Every call goes through a rolling ledger on disk
(`C:\Rocky\pacer\cl_usage.json`) that holds raw timestamps for the last
24 hours. Rocky checks it *before* each request and refuses to make one
that would breach a window, so she never earns a 429. Minute exhaustion
waits for the next slot; hour or day exhaustion stops the pass, because a
scheduled job shouldn't block for an hour.

Three things keep that from turning into a stuck watchlist:

**Reserve.** `courtlistener_reserve_daily` (default 20) is withheld from
the scheduled sync. A busy morning sync can spend 105 of the 125 and
still leave someone emailing "send me the docket" at 2:00 PM with budget
to answer from. Interactive commands run without the reserve and see the
full remaining allowance.

**Fair ordering.** Cases are processed oldest-synced first, and unresolved
cases least-recently-attempted first. A fixed order would re-try the same
head of the list every run and starve the tail once the watchlist outgrew
the daily budget.

**Deferral that actually defers.** Cases the budget couldn't reach are
written to `state.json` as a queue, named in the log, and shown by
`--status`. They deliberately keep their old `last_synced`, which sorts
them to the front next run. Bumping the timestamp would send the case to
the back of the queue and starve exactly the one that just missed out.

`--status` prints Rocky's own count, what a sync is allowed to spend, and
CourtListener's count from `/api-usage/` (its own throttle scope, so
asking is free).

---

## `--sweep`: finding cases nobody told us about

Each entry in config `pacer_sweeps` is a standing party search against
the nationwide index. For a landlord-tenant practice the highest-value
use is a bankruptcy tripwire: a tenant filing Chapter 7 or 13 stays the
eviction the moment it's filed, and PCL sees the filing that day.

```json
{
  "name": "tenant-bankruptcies-dmv",
  "party_last_name": "Sanderson",
  "jurisdiction_type": "bk",
  "courts": ["vaeb", "dcb", "mdb"],
  "bankruptcy_chapters": ["7", "13"],
  "date_filed_days": 30,
  "max_pages": 1,
  "interval_days": 7,
  "auto_watch": true,
  "enabled": true
}
```

`max_pages` is a money control, not a politeness control — each page of
54 results is billed. A loose name against the nationwide index can run
to hundreds of pages, so start at 1 and raise it only when the results
justify it. Sweeps run on `interval_days` (default
`pacer_sweep_interval_days`, 7); `--sweep --name <sweep>` forces one now.

New hits land in `_db\sweeps.jsonl` and the **Discovered** tab of
PACER Index.xlsx. With `auto_watch: true` they're also added to the
watchlist, with the sweep name in Notes.

PCL court IDs carry a type suffix that CourtListener drops (`vaedc` →
`vaed`), and PCL writes case numbers as `1:2015cv01445` where courts
write `1:15-cv-01445`. Both conversions are handled in `pacer_monitor.py`
(`_pcl_court_to_cl`, `_pcl_number_to_docket`).

---

## `--mail`: the team's front door

Anyone at the firm emails `rocky@gallagherllp.com` with "pacer" in the
subject. Claude reads the message and works out the intent, Rocky does
it, and replies:

| Ask | What Rocky does |
|---|---|
| *watch 1:25-cv-00123 in vaed* | Adds it to the watchlist; picked up on the next sync |
| *status on 1:25-cv-00123 vaed* | Last ten entries, judge, filing date, CourtListener link |
| *docket for 1:25-cv-00123 vaed* | Every entry Rocky has recorded |
| *send document 14 in 1:25-cv-00123 vaed* | Attaches the PDF if it's on file |
| *any federal cases for Acme Property LLC?* | Explains that a nationwide search costs a page fee and points at `pacer_sweeps` |

Replies go out through `outbound.send_mail_guarded` — internal addresses
only, from rocky@. Level 0 holds: nothing here can send from James's
account, and there is no send path that bypasses the guard. External
senders get no reply at all, and the refusal is logged.

The mail sweep keeps its own cursor (`C:\Rocky\pacer\mail_state.json`)
and its own instance lock, so it runs every monitor cycle without queuing
behind a long sync.

---

## The database

Config `pacer_monitor_root`, default `PACER Monitor Database` beside
Rocky Cases. Pin it "Always keep on this device" on the Rocky laptop.

```
PACER Monitor Database\
  PACER Watchlist.xlsx     hand-editable — what to watch
  PACER Index.xlsx         generated — Cases / Recent Filings / Discovered
  README.md                written by Rocky, kept current
  _db\cases.jsonl          append-only case records (last write per key wins)
  _db\entries.jsonl        append-only docket entries + their documents
  _db\sweeps.jsonl         PCL discovery hits
  _db\spend.jsonl          every billable transaction, with its receipt
  _db\activity.jsonl       run events
  Documents\<court>\<case>\   downloaded PDFs
```

Local, on the machine that runs Rocky (`C:\Rocky\pacer\`):

```
  state.json               sync + sweep cursors
  mail_state.json          the mail cursor
  index.db                 SQLite index — DERIVED, rebuild with --reindex
  cl_usage.json            the CourtListener rate ledger
  pacer_token_production.json   cached PACER session token
```

**Why the SQLite index is local.** The records of account are JSONL on
the share: readable, mergeable, recoverable, and safe for OneDrive to
sync. A SQLite file on OneDrive is a sync conflict waiting to happen —
the sync client can lock it mid-write and spin off a conflict copy, and
two machines writing it would corrupt it. `--reindex` rebuilds the whole
database from the JSONL in seconds, so losing it costs nothing.

`pacer_token_production.json` holds a live PACER session token, which is
exactly why the local directory is in `.gitignore` and never on the
share.

### Watchlist columns

| Column | Meaning |
|---|---|
| Court | CourtListener/PACER court id — `vaed`, `vawd`, `dcd`, `mdd`, `vaeb`, `vawb`, `dcb`, `mdb`, `ca4`, `cadc` |
| Docket Number | As the court writes it: `1:25-cv-00123` |
| Label | Name for digests |
| Client | Who it's for |
| RRID | Matching Rocky Cases ID |
| Watch | `Y` to watch, `N` to park |
| Notes | Free text; Rocky writes provenance here for cases she added |

Rocky also reads federal dockets out of **Rocky Case Index.xlsx** when it
has `PACER Court` and `Docket Number` columns. It doesn't today, and
that's fine — the seeding step logs one line and skips. Add those two
columns and federal matters enroll themselves. Set
`pacer_case_index_seed: false` to turn it off.

---

## Money

Every billable action draws down `pacer_daily_spend_cap` (default
$10.00/day) and lands in `_db\spend.jsonl` with its receipt. The cap is
read from the shared log, not a local counter, so two machines can't each
spend a full cap.

PCL reports its real fee in every response receipt. **PACER does not tell
the API what a docket report or a PDF actually cost**, so purchases are
recorded at the $3.00 per-document ceiling. The ledger therefore
over-states spending and the cap bites early. That's deliberate: a
conservative estimate stops a runaway; an optimistic one funds it.

`pacer_purchase_enabled: false` disables every billable call. Rocky still
reads RECAP, still records filings, still answers mail — she just never
spends.

**A note on RECAP and confidentiality.** Anything Rocky buys through
RECAP Fetch is contributed to the public RECAP Archive, which is the
bargain that makes the archive exist. That's normally fine for federal
civil filings, which are already public. It is *not* fine for sealed
material, and RECAP Fetch can't reach sealed documents anyway. Don't
point this at a case where the fact of the firm's interest is itself
sensitive.

---

## Setup

1. **CourtListener token** — free at
   `courtlistener.com/profile/api/`. Put it in `courtlistener_token`.
2. **PACER credentials** — James's production login goes in
   `pacer_username` / `pacer_password`. If the account requires a client
   code for searching, set `pacer_client_code` too, or every PCL search
   fails. PACER forces a password change every 180 days; when sweeps
   start failing on "PACER login refused," that's usually why.
3. **PACER MFA** — the account almost certainly has it. Error text
   *"Invalid username, password, or one-time passcode"* is the tell. See
   the next section.
3. **Verify both** — `rocky.py --pacer --auth-test` checks each and
   prints the remaining API budget.
4. **Calibrate on one case** — `rocky.py --pacer --probe vaed 1:25-cv-00123`
   prints what RECAP holds and what the Case Locator returns, side by
   side, without changing anything.
5. **Add cases** — edit PACER Watchlist.xlsx, or
   `rocky.py --pacer --add vaed 1:25-cv-00123 --label "Smith v. Jones"`.
6. **First sync** — `rocky.py --pacer --sync --dry-run`, then for real.
7. **Optional but recommended** — set up @recap.email in each
   jurisdiction. It costs nothing and it's the difference between a
   monitor that reports yesterday's filings and one that reports today's.

Test against the free QA environment first if you want: set
`pacer_environment` to `qa` and use a separate QA PACER account from
`qa-pacer.uscourts.gov`. QA searches aren't billed.

---

## PACER MFA

The Administrative Office made multifactor authentication mandatory for
CM/ECF-level accounts through 2025, so an ordinary username and password
now gets refused with *"Invalid username, password, or one-time
passcode."*

The auth API grew an `otpCode` field for this in the April 2025 revision
of its guide. The codes are ordinary TOTP: six digits, thirty-second
window, generated from a base32 secret. `pacer_api.totp_now()` implements
RFC 6238 on the standard library, so nothing new gets bundled, and it's
verified against the RFC's published test vectors.

**Getting the secret.** Sign in to PACER, open **Manage My Account →
Settings → multifactor authentication**, and enrol. PACER shows a QR code
*and* the secret key as text beside it. The text is what goes in
`pacer_otp_secret`. If you're already enrolled and never kept the secret,
re-enrol to get a fresh one.

**On chaining it to Duo.** A TOTP seed isn't tied to one holder, so Duo
Mobile can keep the PACER account and Rocky can hold the same secret
independently. Both generate identical codes because both are running the
same clock-based function over the same seed. What doesn't work is
pointing Rocky at Duo: there's no API for her to ask your phone for a
code, and Duo's own push approval isn't what PACER's `otpCode` field
expects. She needs the seed, not the app.

**Security.** Putting the seed next to the password means both factors
live on one machine, which is the ordinary trade for unattended access
and the reason PACER documents programmatic OTP generation at all.
`C:\Rocky\config.json` is local-only and gitignored, and the machine is
under your control. If you'd rather keep the factors genuinely apart, a
separate PACER login used only by Rocky is the cleaner answer, and it has
the side benefit of putting her PACER charges on their own bill.

### The filer redaction flag

An account with e-filing privileges cannot authenticate at all without
`redactFlag`. Leave it off and PACER refuses the login, quoting the
redaction rule in full as the error.

Sending it certifies compliance with Fed. R. App. P. 25(a)(5), Fed. R.
Civ. P. 5.2, Fed. R. Crim. P. 49.1, and Fed. R. Bankr. P. 9037: redact
Social Security and taxpayer ID numbers, dates of birth, names of minor
children, financial account numbers, and in criminal cases home
addresses, across all documents including attachments. It's the same
attestation CM/ECF collects at every interactive login.

`pacer_redact_flag` defaults to **false** deliberately. Turning it on is
the account holder's act, not Rocky's, so it isn't hardcoded and isn't
switched on by a default. Worth noting what Rocky does with the
authenticated session: PACER Monitor searches and downloads. Nothing in
it files anything.

**When it breaks.** TOTP depends on an accurate clock. A machine whose
time has drifted more than about thirty seconds fails with an invalid
passcode and nothing else looks wrong. `--pacer --status` prints the code
Rocky would send right now and how long until it rolls, which you can
compare against your phone in two seconds. Rocky also retries once on the
next window before giving up, since a code can legitimately roll over
mid-request or be refused for reuse.

---

## Watch-outs

- **The 125-a-day ceiling is the binding constraint**, not PACER money.
  Per sync run: 1 call to resolve each new case, 1 call per watched case
  to refresh its header, and 1–3 more for each case that actually moved.
  With the default 20-call reserve a sync has 105 to spend, so roughly 80
  quiet cases or 35 busy ones per day. Past that the queue simply takes
  more than one run to drain, which is a delay rather than a failure.
  Raise `courtlistener_rate_limits` the day a membership lands.
- **Check filters against the API, don't assume them.**
  `rocky.exe --pacer --filters dockets` runs an OPTIONS request and
  prints every supported filter with its lookup types. An assumed
  `id__in` shipped and died on the first live sync; this command exists
  so the next guess gets checked first.
- **`_RateGovernor` is advisory, not authoritative.** It counts Rocky's
  own calls. If a browser session or a second machine spends the same
  account's budget, Rocky will still take a 429; she handles it by
  burning the rest of the window in her ledger and stopping. Compare her
  count against CourtListener's in `--status` when the two disagree.
- **Staleness is a guess.** A quiet case and a case nobody has bought
  look identical from outside PACER. That's why purchases are capped by
  money rather than by certainty.
- **Court ID mismatches.** CourtListener renames four PACER courts
  (`azb`→`arb`, `cofc`→`uscfc`, `neb`→`nebraskab`, `nysb-mega`→`nysb`);
  `courtlistener.normalize_court()` handles them. District courts lose
  PACER's trailing `c` (`vaedc`→`vaed`).
- **No new Graph permissions.** Mail reads use the existing app token
  (Application Access Policy already covers rocky@); replies go through
  the guarded sender. Nothing here touched the M365 permission surface.
