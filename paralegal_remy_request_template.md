# Run Remy — Paralegal Request Template

Send to: **rocky@gallagherllp.com**

Subject line MUST start with: **`Run Remy:`**
Suggested format: `Run Remy: <Resident Last Name> - <Project Type>`
Example: `Run Remy: Smith - rent complaint`

## How to use this template

1. Copy everything in the box below into the body of a new email.
2. Fill in the values after each colon. Leave blank if not applicable.
3. Attach the PDFs Rocky needs (lease, ledger, notice, affidavit, etc.) and name them clearly. Filenames matter — Rocky uses them to figure out which PDF is which.
4. Send.
5. Within ~5 minutes Rocky drops a draft `.docx` into the **Remy Outputs** folder on James's OneDrive. James reviews and finalizes from there.

If something goes wrong (missing field, mis-named attachment, bad form-type), Rocky logs the problem and skips. James will tell you what to fix.

---

## Email body — paste this in and fill it out

```
Project: rent-complaint
Resident: Smith, John
Property: Takoma Central

# Attachments — name your files clearly so Rocky finds them.
# Use lower-case names matching the labels below.
Lease: lease.pdf
Ledger: ledger.pdf
Notice: notice.pdf
Affidavit: affidavit.pdf
Incoming: incoming.pdf

# Optional fields — fill in only what applies. Yes/no fields default to NO.
Subsidized: no
Tenant rent portion:
Subsidy portion:
Rent over tenant portion: no
Subsidy failed to pay: no
Subsidy terminated: no
Other pending case: no
Pending case info:
Stay DC status: a
Has Stay DC email: no
Dangerous conduct: no
Non-rent charges: no
Form type:
Jurisdiction:
Agreement type: general
Special provisions:
Attorney: bragdon
```

---

## Project field — what to put

| Project value | What it produces | Required attachments |
|---|---|---|
| `rent-complaint` | DC Form 1-A rent complaint packet | Ledger, Notice, Affidavit |
| `complaint` | DC Form 1-B breach complaint packet | Lease, Notice, Affidavit |
| `warning-letter` | DC/VA/MD breach, rent, nonrenewal, or warning letter | Lease (Ledger optional for DC rent) |
| `settlement` | Settlement agreement | Lease |
| `response-letter` | Response letter shell | Incoming letter |

## Form type field — only for `warning-letter`

Examples (use the exact string from Remy's GUI form-type dropdown):
- `VA 21/30 (Breach)`
- `VA Nonremediable`
- `VA Immediate`
- `DC Rent (Breach)`
- `DC Non-Rent (Breach)`
- `DC Nonrenewal`
- `MD 14-day`
- `MD 30-day`

## Jurisdiction field — `Virginia`, `DC`, or `Maryland`

Required for `warning-letter`. Case-insensitive. Two-letter abbreviations also accepted.

## Agreement type field — only for `settlement`

- `move_out_only`
- `early_termination`
- `concession_only`
- `move_out_concession`
- `early_termination_concession`
- `transfer`
- `general` (default if blank)

## Attorney field

- `bragdon` (default — James)
- `araviakis` (Christina)

## Yes/no fields

Accepted: `yes`, `y`, `true`, `1`, `on` → YES. Anything else (including blank) → NO.

## Money fields (Tenant rent portion, Subsidy portion)

Dollar signs and commas are OK and stripped automatically. `$1,200` and `1200` work the same.

## Stay DC status

Single letter `a` through `e` only. Default is `a`. Only matters for rent-complaint.
