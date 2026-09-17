# PENDING LLT MATTERS — style guide for entries

How to write a row so the docket reads the same way every week. Derived
from the 31 revision tabs on the workbook (Jan 2023 – Jul 2026, 16,317
row-observations), so every form below is one the team already uses. The
recommendations pick the dominant house form and retire the variants.

---

## One fact per column

| Column | Holds | Never holds |
|---|---|---|
| **Client** (col. A) | Client name, typed once above its block | A property name |
| **Subsidized/Non-subsidized** | `Subsidized` or `Non-subsidized` | Program names (those go in Status) |
| **Notice Contains VAWA** / **Complies w/VAWA** | `Yes` / `No` / `N/A` | Prose |
| **Name** | `Last, First` (add `, et al.` for co-residents) | Unit or property |
| **Property** | The building, spelled the same way every week | The client |
| **Unit** | The unit as the lease writes it (`418`, `S308`, `719-3`) | `#418`, `Unit 418` |
| **Client/Matter** | The firm matter number | Anything else |
| **Status** | Where the matter *is* in the process | The next step, or a narrative |
| **Ripe Date** | `M.D.YY`, digits only | Words, court dates |
| **Next Steps** | The next dated event and who owes it | Status words |

The two columns that bleed into each other are Status and Next Steps.
Status answers "what stage is this at." Next Steps answers "what happens
next and when." 224 Next Steps entries currently open with a status word
(`Lawsuit Filed.`, `Judgment awarded.`, `Notice Issued.`); those belong in
Status.

---

## Status — the eight stages

Write the stage, then the case type in parentheses. Case type stays the
same for the life of the matter; only the stage changes.

| Stage | Write | Retire these variants |
|---|---|---|
| 1. Intake, nothing sent | `Rent` | `Rent -`, `Rent - Draft` |
| 2. Notice drafted | `Term letter drafted (Rent)` | `Rent drafted`, `Rent - Drafted`, `Term letter drafting (Rent)` |
| 3. Notice sent | `Term letter sent (Rent)` | `Rent sent`, `Term sent`, `Term letter sent` with no type, `Term letter sentRent)` |
| 4. Notice reissued | `Term letter reissued (Rent)` | `Term letter sent *Reissue (Rent)`, `*Reissued term letter sent (Rent)`, `Term letter drafted (Rent)*****REISSUE` |
| 5. Filed | `Filed FTPR (Rent)` | `Filed`, `Filed (Rent)`, `FTPR complaint filed`, `Filing`, `FILING`, `Filing UD` |
| 6. Judgment | `Judgment awarded (Rent)` | `JUDGMENT AWARDED`, `Rent - JUDGMENT AWARDED`, `OBTAINED JUDGMENT`, `Rent - JUDGMEMT AWARDED`, `Rent - JUDGEMENT AWARDED` |
| 7. Writ / eviction | keep Status at stage 6; track the writ in Next Steps | — |
| 8. Collection | `Collection case (Rent)` | `Collection Case (Circuit Court). Next step: file order of default.` |

**Sentence case, one capital.** `Judgment awarded`, not `JUDGMENT
AWARDED`. All-caps currently costs five spellings of one stage.

**No trailing period, no asterisk notes.** If a matter needs a note
("reissued with current balance," "no RAD, used 3201 8th"), it goes in
Next Steps after an em-free dash, or in the notice file. 160 Status
values currently carry a sentence.

### Case types

`Rent` · `Non-Rent Charges` · `Non-Renewal` · `Conduct` · `Smoking` ·
`Noise` · `Housekeeping` · `Unauthorized Occupant` · `Sublet` ·
`Renter's Insurance` · `Recertification (HUD)` · `Recertification
(LIHTC)` · `Recertification (IZ)` · `Recertification (ADU)`

`Utilities` and `Water` fold into `Non-Rent Charges`: the sheet already
treats them that way (13 matters moved from `Sending notice (Utilities)`
straight to `Term letter sent (Non-Rent Charges)`).

### Filing vehicles

`FTPR` (failure to pay rent, DC) · `BOL` (breach of lease) · `THO`
(tenant holdover) · `UD` (unlawful detainer, VA) · `WID` (warrant in
debt) · `Collection` (circuit court)

---

## Next Steps — the grammar

Four forms cover 90% of rows. Pick the one that matches the stage.

**Before filing, waiting on the notice period:**
```
Ripe on 9.18.26
```

**Waiting on a person (client, manager, court clerk):**
```
Follow up on 9.18.26
Follow up on 9.18.26 — emailed client for update 9.11
```

**After filing, with a court date:**
```
Initial Hearing: 9/18/26 at 9:00am (26-7159)
```
Event name, colon, date, `at`, time, case number in parentheses. Several
events on one row are separated by semicolons with the case number once
at the end:
```
Mediation: 9/18/26 at 10:00am; Trial: 10/2/26 at 9:00am (26-7159)
```

**After judgment:**
```
WRIT FILED (26-7159)
WRIT ISSUED (GV26020585-00)
EVICTION: 10/15/26 (26-7159)
Begin collection process
```

**On hold.** Lead with the hold so it is scannable in a sorted column:
```
HOLD per client email 9.10.26 — ripe 9.18.26
```

Never leave a bare `Initial Hearing:` with no date. There are 90 of
those; each one is a row nobody can act on.

---

## Dates, times, case numbers

**Dates.** `M.D.YY` in the Ripe Date column and in `Ripe on` / `Follow up
on` text. `M/D/YY` in court-date lines, which is how the courts write
them. Always include the year: 52 entries read `Follow up on 3.17` and
there is no way to tell which year from the cell.

**Times.** `9:00am`, closed up, lowercase. Not `9am`, `9:00 AM`, or
`9:00a.m.`

**Case numbers**, in parentheses at the end of the line:

| Court | Format | Example |
|---|---|---|
| DC Landlord & Tenant | `YY-####` | `(26-7159)` |
| DC L&T, long form | `YYYY-LTB-#####` | `(2026-LTB-007159)` |
| DC Small Claims | `YYYY-SCB-#####` | `(2026-SCB-001884)` |
| DC Civil Actions | `YYYY-CAB-#####` | `(2026-CAB-006430)` |
| Virginia GDC | `GVYY######-##` | `(GV26020585-00)` |
| Maryland District | `D-##-CV-##-######` | `(D-08-CV-26-001234)` |

379 of the 382 current court-date lines already carry a case number.
Keep that up; it is the only link from this sheet to the docket.

---

## Quick reference card

```
Name          Doe, Jane                 (Last, First; ", et al." for co-residents)
Unit          418                       (as the lease writes it; no "#")
Status        Term letter sent (Rent)   (stage + type, sentence case, no period)
Ripe Date     9.18.26                   (digits only)
Next Steps    Ripe on 9.18.26           (before filing)
              Initial Hearing: 9/18/26 at 9:00am (26-7159)   (after filing)
              Follow up on 9.18.26      (waiting on someone)
              HOLD per client email 9.10.26 — ripe 9.18.26   (on hold)
```

Stages, in order:
```
Rent  ->  Term letter drafted (Rent)  ->  Term letter sent (Rent)
      ->  [Term letter reissued (Rent)]  ->  Filed FTPR (Rent)
      ->  Judgment awarded (Rent)  ->  Collection case (Rent)
```

---

## Notes

**What this is worth.** The Status column holds 1,761 distinct values for
roughly 40 real states, and 583 of them appear exactly once. 163 stages
are written more than one way: `Non-renewal sent` has seven spellings
across 1,251 rows, including `Non-Renreal sent` 32 times. Every variant
is a matter that drops out of a filter, a sort, or a count. It also
degrades two things Rocky already does: the LLT Watch digest reports
`Status: Non-renewal sent → Non-Renewal sent` as a change when nothing
happened, and `pending_llt._classify_matter` routes matters into the
rent / breach / recert sections of the property-manager emails by
pattern-matching this column.

**Two cells to fix now.** On the 7.25.26 tab, `9.4.6` and `12.31` in Ripe
Date cannot be read as dates by anything, so those matters are invisible
to the ripe check-in sweep. Four others (`9/3/26`, `9/6/26`, `9/7/26`,
`Trial 9/4/26`) parse but are off-form.

**Where the sheet is already disciplined:** 565 of 574 populated ripe
dates are in house form, and court-date lines almost always carry the
case number. The drift is concentrated in Status.

**Two artifacts worth knowing about.** Four values sit in an unnamed
column L on the 2025 tabs, one of them a truncated `Initi` — someone
typed a cell one column right. And the four column headers were renamed
between 11.8.25 and 4.25.26 (`Client/Matter` became `Client.Matter`,
`Complies w/VAWA` became `Complies w.VAWA`), which is why older tabs read
differently.

**Enforcement, if you want it.** Excel data validation on Status (a
dropdown built from the stage × type list) would end the variant problem
at the source rather than cleaning it up afterward. Rocky could also flag
off-form entries in the digest, listing any Status that does not match the
controlled vocabulary. Say the word on either.
