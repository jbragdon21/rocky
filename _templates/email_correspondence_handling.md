# Email Correspondence Handling — Rocky Instructions

## Overview

When Rocky processes case emails via `--daily-cases`, each email and its attachments are saved to the case folder. This document defines the naming conventions, folder structure, and filing rules.

---

## Setup: enabling Email Correspondence for a case

Rocky only saves email text files to `Email Correspondence/` if the folder already exists. To enable it for a case:

1. Create the `Email Correspondence/` subfolder during case setup

Rocky never creates the folder itself — you create it as part of case onboarding. The case-root `CLAUDE.md` (which Cowork reads automatically) can reference email correspondence conventions.

If `Email Correspondence/` doesn't exist, emails still go to `Raw Documents/` as `.txt` files (the existing behavior). The correspondence copy is skipped silently.

---

## Email body → text file in Email Correspondence/

Every email Rocky ingests for a case with an `Email Correspondence/` folder is saved as a **text file**.

**Naming convention:**

```
YYYY-MM-DD_{sanitized_subject}.txt
```

- Date is the email's **received date** (UTC), formatted as `YYYY-MM-DD`.
- Subject is sanitized: `[EXTERNAL]` tag stripped, semicolons replaced with dashes, unsafe filename characters removed, truncated to 80 chars.
- If multiple emails arrive the same day with the same subject, append `_(2)`, `_(3)`, etc.

**Examples:**

```
2026-05-12_Filing Submitted for Case D-08-LT-25-82114-001.txt
2026-05-12_Order on Motion to Dismiss.txt
2026-05-09_Letter from Opposing Counsel re Discovery.txt
```

**File content includes a header block:**

```
Subject:     [full subject line, including [EXTERNAL] tag for audit trail]
From:        [sender name] <[sender email]>
Received:    [received date/time]
Case:        [RRID]
Attachments: [comma-separated list, if any]

========================================================================

[email body text]
```

---

## Attachments → Raw Documents/

Attachments are saved to `Raw Documents/` using Rocky's existing prefix convention:

```
{YYYYMMDDTHHMM}_{hash8}_{original_filename}
```

The `--daily-run` then classifies attachments from `Raw Documents/` into the appropriate subfolder based on the case's `_project/CLAUDE.md`. Common destinations:

| Attachment type | Typical destination |
|---|---|
| Court filings (complaints, motions, orders) | Pleadings/ or Court Documents/ |
| Correspondence from counsel | Correspondence/ |
| Leases, ledgers, client records | Client Documents/ |
| Research memos | Research/ |

**Attachments are never auto-filed to Email Correspondence/.** That folder is exclusively for email body text files. The attachment goes to Raw Documents/ first, then the daily-run files it.

---

## Court e-filing emails (Tyler Technologies / CaseFileXpress)

Court e-filing systems (e.g., DC Superior Court via Tyler Technologies, `no-reply@efilingmail.tylertech.cloud`) send notification emails with:

- **Subject:** Filing details including case number, party names, envelope number
- **Body:** HTML notification with filing status, timestamps, and filing details
- **Attachments:** The actual filed documents (PDFs of complaints, motions, orders, etc.)

**Rocky's handling:**
1. Email body → `Email Correspondence/YYYY-MM-DD_Filing Submitted for Case {case_number}.txt`
2. Attached court documents → `Raw Documents/` → daily-run files to `Court Documents/` or `Pleadings/` based on document type

**Filing classification guidance for court e-filing attachments:**
- Documents **filed BY a party** (motions, complaints, answers, briefs) → `Pleadings/`
- Documents **issued BY the court** (orders, scheduling notices, summons) → `Court Documents/`
- Certificate of service, proof of service → `Pleadings/` (filed by a party)

---

## Duplicate handling

- Email text files: if a file with the same date+subject name already exists, skip (idempotent on re-run).
- Attachments: existing `Raw Documents/` prefix-match check (unchanged from current behavior).

---

## Activity logging

Each email saved generates an `email_ingested` event in `activity.jsonl` with:
- `email_corr_path`: relative path to the saved text file in Email Correspondence/ (null if folder not set up)
- `files_saved`: list of all files saved (email text + attachments)
- Standard metadata (subject, sender, received datetime, match method)
