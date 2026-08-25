# Rocky and the Firm's Email System — How the Boundaries Work

A plain-English explanation of what Rocky can and cannot do inside the firm's Microsoft 365 environment, written for anyone at the firm who wants to understand the safety model. The companion document `DATA_SECURITY.md` covers vendors, data storage, and audit trails in full detail; this document answers one question: **could Rocky ever interfere with the firm's email system as a whole?**

The short answer: no. Rocky's access ends at a short, named list of mailboxes, and Microsoft enforces that boundary on its own servers. Extending the boundary requires a deliberate action by firm IT for each additional mailbox. The rest of this document explains the mechanics.

---

## 1. The key distinction: broad permissions, narrow reach

Rocky's Azure app registration lists permissions that sound sweeping. `Mail.Read` as an application permission would, on its own, let a program read any mailbox in a tenant. Microsoft designed a second control for exactly this situation, and the firm uses it: an **Exchange Application Access Policy**.

The policy works like a fence around the permission. The app registration says *what kind* of access Rocky has (read mail). The access policy says *whose* mail: a mail-enabled security group containing, currently, four mailboxes:

| Mailbox | Why it's in the list |
|---|---|
| `rocky@gallagherllp.com` | Rocky's own operational mailbox |
| `jbragdon@gallagherllp.com` | James's mail (case folders, Vault ingestion, classification) |
| `eaiken@gallagherllp.com` | Inbox Cleaner user |
| `smetzger@gallagherllp.com` | Inbox Cleaner user |

When Rocky's code requests any mailbox outside that group, Microsoft's servers reject the request with an access-denied error before any data leaves Exchange. The enforcement point is in the cloud, on Microsoft's side. Rocky's code quality is irrelevant to it: a bug, a bad prompt, or even a stolen credential could still reach only those four mailboxes.

## 2. Rocky's two credentials

Rocky talks to Microsoft 365 in two ways, and each has its own ceiling.

**The app token (automated reads).** This is the client-credentials login used for scheduled jobs. It carries `Mail.Read` only. It can read mail in the four policy mailboxes. It holds no write permission, no send permission, and no delete permission of any kind. Most of Rocky's daily work (case email summaries, Vault ingestion, litigation-notice polling, inbox snapshots) runs on this read-only token.

**The delegated sign-in (acting as rocky@).** For anything beyond reading, Rocky signs in as `rocky@gallagherllp.com`, a standard licensed user account IT provisioned for her. Delegated permissions have a hard ceiling built into Microsoft's model: a delegated token can never do more than the signed-in user could do herself in Outlook. Rocky-as-rocky@ can:

- Send email **from rocky@'s own address only**, and the code restricts recipients to `@gallagherllp.com` addresses (section 4).
- Read, draft in, and file within James's mailbox, because IT granted rocky@ an Exchange "Full Access" delegation on that one mailbox. This is how Rocky places draft replies in James's Drafts folder. The same delegation model would apply per-mailbox for any future user.
- Send Teams chat messages as herself (the approval-loop conversations).
- Read SharePoint/OneDrive content that rocky@'s account has been given access to (`Sites.Read.All`, delegated, so it reaches only sites shared with her).

Neither credential carries `Mail.Send.Shared` or `Mail.Send.All`, the permissions that allow sending as another person. Those have never been granted, and Rocky's startup self-audit (section 5) halts the program if they ever appear.

## 3. What "the entire firm's email system" would require, and why Rocky can't get there

For a program to run havoc across a tenant's email, it needs at least one of the following. Rocky has none of them:

1. **Tenant-wide mailbox access.** Blocked by the Application Access Policy. Reading a fifth mailbox requires IT to add it to the security group. There is no API call, code change, or configuration on Rocky's side that widens this; only an Exchange administrator can.
2. **Send-as or impersonation rights.** Never granted. Every email Rocky sends leaves from `rocky@gallagherllp.com` under her own name. She cannot send as James, as the firm, or as anyone else.
3. **Admin or directory roles.** rocky@ is a standard user account. It holds no Exchange admin role, cannot modify other users' mailbox rules or permissions, cannot alter mail flow, and cannot change its own permissions.
4. **Delete or purge capability at scale.** The app token is read-only. Write access exists only inside James's mailbox via the Full Access delegation, and Rocky's design there is move-and-log, never delete (dispositions go to folders like Deleted Items, where Exchange retention keeps them recoverable).

The theoretical worst case, assuming Rocky's code were completely wrong or her laptop fully compromised, is therefore bounded: an attacker holding her tokens could read the four policy mailboxes, manipulate contents of James's mailbox, and send internal-only email from rocky@'s own address. Disruptive to those specific mailboxes, and fully visible in Microsoft Purview's audit log, but with no path to the other mailboxes in the tenant. That ceiling is Microsoft's enforcement, so it holds regardless of what runs on the laptop.

## 4. Outbound email: three independent layers

Rocky sends email (digests, affidavit approval requests, Vault confirmations) from her own mailbox. Three layers keep that capability contained, and each works even if the others fail:

1. **Platform:** delegated `Mail.Send` covers rocky@'s own mailbox only. The permissions to send as others are absent.
2. **Code:** every send goes through one function, `send_mail_guarded()` in `outbound.py`, which refuses any recipient or sender outside `@gallagherllp.com`. No other send path exists in the codebase.
3. **Exchange:** a tenant-side mail-flow rule rejects outbound mail from Rocky to non-firm addresses, so even a rewritten or bypassed code guard could not reach an external recipient.

The practical consequence: Rocky's email output can only ever be internal messages, from her own clearly-labeled address, to firm addresses.

## 5. In-code safeguards (the second line, behind the platform)

These matter less than the Microsoft-side ceiling but shrink day-to-day risk within it:

- **Startup permission audit** (`permissions.py`): on every run, Rocky decodes her own access token and halts immediately if any send-as-others scope is present. A tripwire against misconfiguration by anyone, including IT.
- **Kill switch** (`kill_switch.py`): an email with subject "ROCKY STOP" from an authorized sender suspends all processing until "ROCKY START".
- **Nothing is deleted.** Dispositions are moves. The single narrow exception (email signature-image artifacts under 200 KB) is enforced in code and logged.
- **Human approval for bulk actions.** Inbox Cleaner moves nothing without a person approving each cohort, and only the mailbox owner's reply counts.
- **Append-only audit logs.** Every classification, file action, send, and approval is a timestamped JSONL event, alongside Microsoft's own Purview mailbox auditing.

## 6. How the boundary moves (and who moves it)

Every expansion of Rocky's reach is a deliberate, per-mailbox IT action, made only after the code that needs it exists and has been reviewed (the project's standing rule: permissions follow validated capability, never anticipated need).

| To let Rocky... | IT must... |
|---|---|
| Read another user's mail | Add that mailbox to the Application Access Policy group |
| Write/file in another user's mailbox | Grant rocky@ Full Access on that mailbox in Exchange |
| Send externally | Remove the mail-flow rule **and** the code guard would still refuse (this has never been requested and is contrary to design) |
| Send as another user | Grant `Mail.Send.Shared`/`.All`, which Rocky's own startup audit treats as a fatal error |

---

## Verification items for IT (confirm and date)

The platform-side claims above rest on tenant configuration that lives in Exchange and Azure, so they should be confirmed there rather than taken from this document:

- [ ] Application Access Policy exists, is scoped to `Mail.Read`, and its group contains exactly: rocky@, jbragdon@, eaiken@, smetzger@ (`Test-ApplicationAccessPolicy` per mailbox is the quick check).
- [ ] The outbound mail-flow rule rejecting rocky@'s external mail is enabled (listed as a Phase A item in `DATA_SECURITY.md`; confirm current status).
- [ ] rocky@ holds no admin/directory roles and no Send As / Send on Behalf rights on any mailbox.
- [ ] Purview mailbox auditing covers rocky@ and jbragdon@.
- [ ] The app registration's consented permissions match this document (delegated: Mail.Read, Mail.ReadWrite(.Shared), Mail.Send, Sites.Read.All, Calendars.ReadWrite, Chat.Create, Chat.ReadWrite, ChatMessage.Send; application: Mail.Read).

*Last updated 2026-08-16. Maintained alongside `DATA_SECURITY.md`; update both when a permission or data flow changes.*
