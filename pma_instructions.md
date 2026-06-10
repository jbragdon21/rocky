# PMA NegotiationWatch — Classifier Instructions

Plain-English rules that refine how Rocky classifies emails sent to
`pmateam@gallagherllp.com` and proposes HubSpot ticket updates. Edit this file
freely — Rocky reloads it on every poll, so changes take effect within ~15
minutes with no rebuild. This is the primary tuning surface during the observe
week.

These rules are appended to the base classifier prompt. They override nothing
about the JSON output format; they only sharpen judgment.

---

## How to read the activity log during the observe week

Each poll writes one line per email to `pma_activity.jsonl` (in `C:\Rocky\`).
Look at the `outcome` and `proposed_payload` fields:

- `proposed` — Rocky *would* update HubSpot but writes are off. Read the
  `proposed_payload` and `classification.reasoning`. If it's wrong, add a rule
  below or fix the ticket's `counterparty_keywords` in the manifest.
- `unmatched_keyword` — no keyword hit. If it *should* have matched a ticket,
  add a keyword to that ticket in the manifest (re-run `pma_bootstrap.py` or edit
  `pma_manifest.json` directly).
- `unmatched_lowconf` / `unmatched_null` — keyword hit but Claude wasn't
  confident. Usually correct (FYI/scheduling mail), but skim for misses.

---

## Status-change rules

Set `status_changed: true` only on a clear workflow transition. Guidance:

- **"Document Draft Pending" → "Active Negotiations- Doc with GEJ"** when GEJ
  (our firm) receives a draft or comments to turn around.
- **→ "Active Negotiations- Doc with Bozzuto"** when the draft has been sent to
  BMC / Bozzuto for their review.
- **→ "Active Negotiations- Doc with Client"** when the draft is with the
  owner/client side (counterparty) for review.
- **→ "Signatures Pending"** when an execution copy is circulated for signature.
- **→ "On Hold"** when a party signals the deal is paused, stalled, or dead.

"GEJ" = Gallagher LLP (us). "BMC" / "Bozzuto" = our client's management company.
"Client" = the property owner / counterparty on the agreement.

Do **not** change status for: scheduling, acknowledgments ("thanks", "received"),
internal FYI forwards, or status-quo check-ins.

## Summary line style

- One sentence, 15–30 words, past tense, prefixed with the date in M/D format.
- Name who did what to whom: "6/5: Sam sent the revised draft to BMC; one open
  point is the departure fee on owner sale."
- Mirror the existing "Summary Status" voice on the tickets (concise, factual).

## Forwarded mail from the client (pma@bozzuto.com)

Rocky also receives PMA emails forwarded in from `pma@bozzuto.com`. Treat these
as part of the PMA review process, exactly like mail addressed to the team inbox:
read the *forwarded* message beneath the forward header (its original sender,
subject, and body), match it to a ticket, and propose an update if it signals a
workflow transition. `pma@bozzuto.com` is only the forwarder — base the match and
the summary line on the underlying email's property/deal content and the original
sender, not on "pma@bozzuto.com" itself.

## Matching hints

- The "Legal Contact" on a ticket (e.g., Samantha Stephey) is usually the GEJ
  attorney; an email from/to them about a named property is a strong signal.
- Property addresses and building names are the most reliable identifiers; firm
  names like "Related" are weaker (many deals share a sponsor).
- If two tickets are plausible, pick the higher-confidence one and say so in
  `reasoning`. When genuinely unsure, prefer `confidence: low` so it routes to the
  digest instead of writing a wrong update.

---

## Firm-specific overrides (add as you learn during the observe week)

<!-- Examples:
- Emails from jbarnes@... about "Santa Monica" are almost always the CEI deals
  (1907 Wilshire / 1527 Lincoln / 528 Arizona) — match on the street address, not
  "CEI Santa Monica" alone.
- Treat "redline" or "comments attached" as a draft-received signal.
-->
