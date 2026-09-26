# Daily To-Do Lists — and how to onboard a colleague

One command per person. Rocky reads that person's inbox for the last 24
hours, drops every message they have already replied to, and emails them
an Urgent / Action Needed / FYI list built by Claude.

| Person | Mailbox | Command | Scheduled |
|---|---|---|---|
| Steve Metzger | `smetzger@gallagherllp.com` | `rocky.exe --steve-todo` | 7:30 AM |
| Rommel Loria | `rloria@gallagherllp.com` | `rocky.exe --rommel-todo` | 7:35 AM |

Staggered five minutes apart on purpose: separate processes with separate
instance locks, but they hit Graph and Claude back to back.

---

## How it works

`fetch_inbox_emails_unreplied()` pulls the person's Inbox with
`receivedDateTime gt <24h ago>` and expands the extended property
`PidTagLastVerbExecuted` (`0x1081`). Verb `102` (Reply) or `103`
(ReplyAll) means they already answered it, so it never reaches the
prompt — the list is what is still owed, not what arrived.

Claude gets each remaining email's From / To / Cc / Date / Subject and
the first 1,000 characters of the body, and returns markdown in three
buckets. Rocky renders that into the standard dark-header email and
sends it **from `rocky@`, to that person only** (`send_mail_guarded`, so
firm addresses only). No unreplied mail = no email; a quiet day stays
quiet.

## Adding or adjusting a person

Built-in recipients live in `TODO_USERS` in `rocky.py`. `config.json`
overrides them per key under `todo_users`:

```json
"todo_users": {
  "rommel": {
    "mailbox": "rloria@gallagherllp.com",
    "display_name": "Rommel",
    "cc": [],
    "hours": 24,
    "focus": ""
  }
}
```

- **`focus`** — free text about that person's practice, appended to the
  prompt. Use it when "actionable" means something specific for them
  (e.g. "Rommel handles DC rent-control filings; a RAD notice deadline
  is always Urgent"). Empty is fine — the base prompt is role-neutral.
- **`hours`** — widen the lookback over a holiday weekend.
- **`cc`** — extra recipients on their list. Off by default: the list is
  the person's own inbox, read back to them.

A brand-new key here runs immediately as `--<key>-todo`. It gets a
dashboard button and a place in the one-click Auto-setup schedule only
once `ROCKY_COMMANDS` in `dashboard.py` lists it.

---

## Permissions: what to ask IT for, once

**No Azure app-registration change and no new Graph scope.** Rocky's
existing grants already cover all three tiers; what a new person needs
is to be named in the two Exchange-side fences and, for files, a
OneDrive share. Asking once, up front, beats three tickets.

Rocky reads a mailbox by one of two credentials, and they are fenced
differently — this is the whole reason there are two asks:

| Credential | Fence | Used by |
|---|---|---|
| Delegated (`rocky@`'s own sign-in) | A per-mailbox Exchange delegation | To-do lists, James's case folders |
| App token (client credentials, `Mail.Read`) | The Application Access Policy security group (today: rocky@, jbragdon@, eaiken@, smetzger@) | Ella's case digest, Inbox Cleaner snapshots |

### Tier 1 — the to-do list (needed now)

Exchange Admin Center → Recipients → Mailboxes → *the person* →
Delegation → **Read and manage (Full Access)** → add
`rocky@gallagherllp.com`. Or:

    Add-MailboxPermission -Identity rloria@gallagherllp.com `
        -User rocky@gallagherllp.com -AccessRights FullAccess -AutoMapping $false

**Be honest with IT about the name of this right.** Exchange has no
read-only Full Access: the grant that lets a delegated Graph token open
someone else's mailbox is `FullAccess`, the same mechanism already on
James's and Steve's mailboxes. Read-only-ness here comes from Rocky's
side — the to-do list only reads, and she can never send as that person
(`Mail.Send.Shared` / `Mail.Send.All` were never granted, and
`permissions.py` halts startup if either appears on her token).
`-AutoMapping $false` keeps the mailbox out of rocky@'s Outlook.

A genuinely read-only alternative exists —
`Add-MailboxFolderPermission -Identity rloria:\Inbox -User rocky@ -AccessRights Reviewer`
— but it is not what this tenant uses today, and Graph's delegated
folder-level access is narrower than Full Access. Mirror what works on
`smetzger@` unless IT prefers to pilot the tighter grant:

    Get-MailboxPermission -Identity smetzger@gallagherllp.com -User rocky@gallagherllp.com

Until the grant lands the scheduled run fails soft: Graph returns 403,
the error goes to `rocky.log`, no mail is sent, nothing crashes.
Scheduling ahead of it costs a daily log line.

### Tier 2 — folder digest, the Ella pattern

Ella's digest reads *named subfolders* of her mailbox
(`/users/{email}/mailFolders/{id}/messages`) on the **app token**, so it
needs the mailbox added to the **Application Access Policy** group — a
second, separate IT action from Tier 1, and the one worth bundling into
the same ticket. The group is whatever the policy for app
`f012b5f9-051a-46f2-a2c8-15823ed63900` is scoped to:

    Get-ApplicationAccessPolicy | ? { $_.AppId -eq "f012b5f9-051a-46f2-a2c8-15823ed63900" }
    Add-DistributionGroupMember -Identity <that group> -Member rloria@gallagherllp.com
    Test-ApplicationAccessPolicy -Identity rloria@gallagherllp.com `
        -AppId f012b5f9-051a-46f2-a2c8-15823ed63900

`Test-ApplicationAccessPolicy` returning *Granted* is the verification.
When it does, add the mailbox to the table in `EMAIL_SAFETY.md` §1,
which enumerates the group.

Beyond permission it needs setup, not IT:

- Outlook Rules (the person's own) sorting mail into per-matter folders.
- A `case info.xlsx` listing Case Name / Folder Location / Names, the
  same shape as Ella's.

### Tier 3 — OneDrive / folder review, the daily-cases pattern

`--daily-cases` is **filesystem-only** — it reads the local synced
OneDrive folder, not Graph. So the ask is a share, not a permission:

1. The person shares the folder(s) with `rocky@gallagherllp.com`
   (**Read** for observe-only; **Edit** only if Rocky is to write
   summaries back).
2. On the Rocky laptop, add the shortcut to `rocky@`'s OneDrive and set
   it **"Always keep on this device."** Files On-Demand placeholders are
   unreadable to Rocky — a cloud-only `.docx` or `.pdf` throws
   `PermissionError`, not a download.

If a folder ever has to be read *without* syncing it, the alternative is
delegated `Files.Read.All` + admin consent — a real Azure change, and
worth avoiding unless the sync route fails. Anything living in a
SharePoint/Teams document library instead of OneDrive is already covered
by `Sites.Read.All`.

### Ready-to-send IT request

> **Subject: Rocky — mailbox access for Rommel Loria (rloria@)**
>
> We're extending Rocky (our internal assistant account,
> `rocky@gallagherllp.com`) to Rommel Loria. Each morning she'll read
> Rommel's inbox and email back a to-do list of what hasn't been
> answered yet — the same thing she already does for Steve Metzger.
>
> Two Exchange changes, please. **No Azure app-registration change and
> no new Graph permission** — everything Rocky needs is already
> consented; these two steps just name Rommel's mailbox inside the
> existing fences.
>
> **1. Mailbox access for the morning to-do list (needed now).** Grant
> `rocky@gallagherllp.com` Full Access on `rloria@gallagherllp.com` —
> Exchange admin center → Recipients → Mailboxes → rloria →
> Delegation → "Read and manage (Full Access)" → add rocky@. Or:
>
>     Add-MailboxPermission -Identity rloria@gallagherllp.com `
>         -User rocky@gallagherllp.com -AccessRights FullAccess -AutoMapping $false
>
> This is the same grant already in place on `smetzger@` (feel free to
> mirror it exactly —
> `Get-MailboxPermission -Identity smetzger@gallagherllp.com -User rocky@gallagherllp.com`).
> `-AutoMapping $false` keeps the mailbox from appearing in rocky@'s
> own Outlook. If you'd rather scope it tighter than Full Access,
> `Add-MailboxFolderPermission -Identity rloria:\Inbox -User rocky@gallagherllp.com -AccessRights Reviewer`
> would also work for reading the Inbox — your call; we haven't used
> the folder-level route here before.
>
> **2. Application Access Policy (for the next piece, and to save a
> second ticket).** Add `rloria@gallagherllp.com` to the mail-enabled
> security group that scopes Rocky's app registration (app ID
> `f012b5f9-051a-46f2-a2c8-15823ed63900`; the group currently holds
> rocky@, jbragdon@, eaiken@, smetzger@). This is what a later
> per-matter folder digest reads on, and it's the fence that keeps the
> app token from reaching any other mailbox in the tenant.
>
>     Get-ApplicationAccessPolicy | ? { $_.AppId -eq "f012b5f9-051a-46f2-a2c8-15823ed63900" }
>     Add-DistributionGroupMember -Identity <that group> -Member rloria@gallagherllp.com
>     Test-ApplicationAccessPolicy -Identity rloria@gallagherllp.com -AppId f012b5f9-051a-46f2-a2c8-15823ed63900
>
> **What Rocky can and can't do with this.** She reads Rommel's mail
> and sends the summary **from her own address** — she cannot send as
> Rommel or as anyone else: `Mail.Send.Shared` and `Mail.Send.All` have
> never been granted, and her code halts on startup if either ever
> appears on her token. Her outbound is also restricted to
> `@gallagherllp.com` recipients in code and by the existing mail-flow
> rule. The to-do list itself only reads — it moves no mail, sets no
> flags, and writes nothing to the mailbox.
>
> Nothing needed on OneDrive yet. If we add document review later,
> Rommel can share specific folders with rocky@ directly and that needs
> no admin action.

---

## Safety posture (unchanged)

- Rocky sends **from her own mailbox only**. `permissions.py` halts
  startup if `Mail.Send.Shared` or `Mail.Send.All` ever appears on the
  token.
- `outbound.py` refuses any recipient outside `@gallagherllp.com`.
- The to-do list writes nothing: no mail moves, no flags, no drafts in
  the person's mailbox. It reads and reports.
