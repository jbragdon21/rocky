# Rocky — Data Security Reference

Started 2026-07-12. A comprehensive, presentation-ready explanation of how Rocky handles firm and client data: what data she touches, where it lives, who can access it, which external services see it and on what terms, and what controls constrain her. Maintained as a living document — **update this file whenever a new data flow, vendor, or permission is added**, and re-verify the vendor policy log (§9) before presenting from it.

---

## 1. Executive summary

Rocky is a locally-run Python program (no servers, no databases exposed to a network) that reads firm email via the Microsoft Graph API, uses the Anthropic Claude API for classification and summarization, and writes work product to the firm's OneDrive. Her security posture rests on four pillars:

1. **Architectural incapability over policy.** Rocky cannot send email from James's account — not because code declines to, but because the Microsoft 365 permission (`Mail.Send`) has never been granted on that mailbox. The most dangerous capabilities are absent at the platform level, so no bug in Rocky's code can exercise them.
2. **Permissions follow validated capability.** No Graph permission is granted until working, tested code needs it. Read permissions came first; write permissions arrive only with the specific feature that uses them.
3. **Data minimization to vendors.** Only two external AI vendors ever see message content — Anthropic (contractually no-training, short retention) and Voyage AI (account opted out of retention/training; zero-day retention). Everything else stays inside the Microsoft 365 tenant or on the Rocky laptop.
4. **Everything is logged; nothing is destroyed.** Every classification, file action, and discard is an append-only JSONL audit event. The "nothing is ever deleted" rule means dispositions are moves (recoverable), with one narrow, size-capped exception for email signature-image artifacts.

## 2. What data Rocky handles

- **Email** from `jbragdon@gallagherllp.com` (per-case Outlook folders, Sent Items) and `rocky@gallagherllp.com` (her own operational mailbox); prospectively colleagues' mailboxes (Inbox Cleaner — metadata-only in its read phase) under the same access-policy mechanism. Email content includes privileged attorney-client communications and attachments.
- **Case files** in `OneDrive – gejlaw.com\Rocky Cases\` — pleadings, correspondence, client documents, drafts, per-case activity logs.
- **PMA / client-matter documents** handled by the PMA tracker and Maple pipelines.
- **Derived corpora**: `classifications.jsonl`, per-case `activity.jsonl`, the email-brain SQLite corpus (sent-mail + inbound pairs), Inbox Cleaner snapshots/ledgers.

## 3. Where data lives

| Location | What | Notes |
|---|---|---|
| Rocky laptop, `C:\Rocky\` (local disk only) | `config.json` (API keys, tenant IDs), `state\` (MSAL token cache, cursors, dormant flag), `rocky.log`, `classifications.jsonl`, Inbox Cleaner machine data (snapshots, move logs). *Stale copy* of the email-brain corpus (`email_brain\`) left behind by the 2026-08-02 migration — safe to delete once Minotaur's copy is trusted. | Deliberately *not* on OneDrive: private, and the corpora are confidential client correspondence. |
| Rocky laptop, `C:\Minotaur\` (local disk only) | Minotaur's `config.json` (same key classes as Rocky's), its own MSAL token cache, `activity.jsonl` (tool audit trail incl. session notes quoting James), and the **live email-brain corpus** (`email_brain\brain.db` + JSONL — moved from Rocky 2026-08-02) | Same doctrine as `C:\Rocky\`: never on OneDrive. Minotaur's OneDrive folder carries source/docs only. |
| OneDrive (firm tenant, synced) | `Rocky Cases\` (per-case folders, case index, instructions, digests), `Program Files\` (deployed .exe), human-facing Inbox Cleaner artifacts (rules, workbook) | Shared-data filesystem, not a deployment or secrets store. Access governed by firm OneDrive sharing. |
| Git repository (dev laptop) | Source code and docs only | `.gitignore` excludes `config.json`, `state/`, `*.log`, `*.jsonl`, `email_brain/`, `*.db`, `*.sqlite*`, `Azure ID Info.txt` — verified via `git check-ignore`. No client data or credentials are ever committed. |

## 4. Access control (Microsoft 365 permission model)

- **Level 0 safety architecture:** `Mail.Send` is never granted on James's delegated mailbox. Enforced at the M365 permission level, not in code. Rocky's code additionally contains no send-from-James function.
- **Read-only baseline:** the classifier/case pipeline runs on `Mail.Read`. The permission-progression table in `BUILD_REFERENCE.md` §"Permission progression" governs when each additional permission may be granted (drafts → `Mail.ReadWrite`; sending → `Mail.Send` on *Rocky's own mailbox only*).
- **Mailbox scoping:** app-token (client credentials) access is constrained by an Exchange **Application Access Policy** to an explicit list of mailboxes (currently jbragdon@ and rocky@). Adding a colleague's mailbox (Inbox Cleaner, email brain archive folder) is an explicit IT action per mailbox — the app cannot roam the tenant.
- **Outbound allowlist (Phase A):** when Rocky sends from her own account, code (`outbound.py`) refuses non-`@gallagherllp.com` recipients, and an Exchange mail-flow rule enforces the same tenant-side — defense in depth.
- **Teams:** delegated scopes only (`Chat.Create`, `Chat.ReadWrite`, `ChatMessage.Send`) via rocky@'s own identity; kept in a separate scope list so mail commands never acquire chat consent. In approval loops, only the target mailbox owner's replies count as approval, and a reply can only approve/decline a proposed action — never define a new one.

## 5. External services (data processors)

| Service | What it receives | Terms (verified date — see §9) |
|---|---|---|
| **Microsoft Graph / M365** | All mailbox and file traffic | Stays inside the firm's own tenant; covered by the firm's existing Microsoft agreements. Purview audit logging applies (coverage confirmation is an open Phase A item). |
| **Anthropic Claude API** | Email bodies/attachments text for classification, summarization, digests; images for vision checks | Commercial Terms: customer content **not used for training**; short default retention (per Anthropic's published policy: API inputs/outputs deleted on the order of days, 30 days maximum standard; zero-data-retention agreements available on request). Verified 2026-07-12. |
| **Voyage AI (MongoDB)** | Full text of inbound→reply email pairs, transiently, for embedding (email brain) | **Default terms retain data and permit training — unacceptable.** The account is **opted out** (**completed 2026-07-12**; ToS §3(iii) opt-out — Voyage will not use the data to train future models; zero-day retention; evidence: `docs/voyage_opt_out_2026-07-12.png`). Opt-out required payment method + org Admin and is one-way. Free tier is prohibited for this project (free-tier data is trained on). SOC 2 / certification status unverified (§8). |
| **Healthchecks.io** (Phase A, planned) | Liveness pings only | No message or file content transmitted. |
| **Tailscale** (remote management) | Encrypted tunnel for RDP to the Rocky laptop | Transport only; no firm data stored with the vendor. |

All vendor API traffic is HTTPS/TLS in transit.

## 6. Code-level safety controls

- **`permissions.py`** — decodes the token's scope claim at startup; **halts the program** (exit 2) if any send-capable scope (`Mail.Send`, `.Shared`, `.All`) is ever present. A tripwire against permission misconfiguration by anyone, including IT.
- **`outbound.py`** — the only sanctioned send path; refuses non-firm senders *and* recipients.
- **`kill_switch.py`** — "ROCKY STOP" / "ROCKY START" email subjects from authorized senders toggle a dormant flag that suspends all processing.
- **Dashboard allowlist** — the web dashboard can only launch flags in a fixed registry (`ROCKY_COMMANDS`); inputs are validated (time regex, name charset, Rocky-prefixed task names only). It cannot become an arbitrary-command sink.
- **Nothing is ever deleted** — dispositions are moves (e.g., newsletters → Deleted Items, recoverable under Exchange retention). The single exception, the daily-run DISCARD action for email signature-image artifacts, has a **code-level guardrail the model cannot override**: only small images / image-sibling PDFs ≤200 KB qualify; everything else is refused. Discards are logged and indexed, never silent.
- **Raw documents are immutable** — filing *copies* raw → target folder; the original ingested record is preserved.
- **Human approval for bulk actions** — Inbox Cleaner moves nothing without a human-approved cohort; write permission is not even requested until the read-only analysis phase is complete and reviewed.

## 7. Audit trail

- `classifications.jsonl` — every email classification, append-only.
- Per-case `activity.jsonl` — every summary, file action, discard, with actor attribution (Rocky, James, Cowork session).
- `master_file_index.json` — per-case document index including `discarded` dispositions.
- `rocky.log` — operational log; surfaced human-readably in the dashboard.
- Microsoft Purview — tenant-side audit of mailbox access (coverage confirmation pending, §8).
- `SESSIONS.md` — development-side change log: what changed, what was decided, why.

## 8. Known gaps and open items (be candid about these when presenting)

1. **API keys are plaintext in `config.json`** on the Rocky laptop (flagged since the first session; acceptable for current phase, should move to Windows Credential Manager / DPAPI before broader deployment).
2. **Voyage SOC 2 / security certifications unverified** — their docs don't publish one; ask support@voyageai.com for security documentation. (MongoDB ownership does not automatically extend MongoDB's attestations.)
3. **Rocky laptop disk encryption (BitLocker) status** — confirm and record.
4. **Purview audit coverage of both mailboxes** — Phase A checklist item, not yet confirmed.
5. **Tailscale/RDP access list** — document who holds access to the Rocky laptop.
6. **OneDrive sharing scope of `Rocky Cases\`** — document who the folder is shared with and review periodically.
7. **Anthropic zero-data-retention agreement** — available on request; consider whether the firm wants ZDR formalized rather than relying on the standard short-retention default.

## 9. Vendor policy verification log

Re-verify entries older than ~90 days before presenting or relying on them.

| Date | Vendor | What was verified | Source |
|---|---|---|---|
| 2026-07-12 | Voyage AI | Default = retain + may train; paid-account opt-out gives zero-day retention; free tier trains on data; opt-out is one-way, requires payment method + org Admin | docs.voyageai.com/docs/faq; voyageai.com/privacy; voyageai.com/tos |
| 2026-07-12 | Anthropic | Commercial Terms: no training on API customer content; standard API retention on the order of days (≤30); ZDR available via sales | platform.claude.com/docs/en/manage-claude/api-and-data-retention; privacy.claude.com |
| 2026-07-12 | Voyage AI | **Opt-out COMPLETED by James** — dashboard ToS page shows "Opted Out" (ToS §3(iii): data not used to train future models; zero-day retention) | Screenshot: `docs/voyage_opt_out_2026-07-12.png` |

## 10. Decisions of record

- **2026-07-12 — Voyage AI approved for email-brain embeddings *conditional on* the account-level opt-out from data retention/training.** **Condition satisfied same day:** James opted the organization out; the dashboard ToS page shows "Opted Out" (screenshot: `docs/voyage_opt_out_2026-07-12.png`). Rationale: opted-out Voyage (TLS in transit, zero-day retention, no training, transient processing role) meets the reasonable-efforts standard for cloud handling of client data (cf. ABA Formal Ops. 477R/498) and is materially the same exposure category as the already-accepted Anthropic API flow. The free tier is prohibited.
