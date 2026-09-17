# Multifamily Case Dashboard — IT briefing

**Prepared 2026-09-13 for review with IT. Nothing has been built or
requested yet; this is to scope the decision.**

## What is being proposed

An internal web page where the landlord-tenant team (roughly 5–10 staff)
does its daily tracking, replacing several shared spreadsheets as the
working surface. Six panels: a court-date calendar, the weekly task list,
the pending landlord-tenant matters (sortable by property and ripe date),
new matters needing intake, matters with no tracking system today, and an
exceptions list.

The page is **read and write**. A user archives a resolved matter from
the page, and the underlying spreadsheet in Teams is updated. Behind it
sits a Python service ("Rocky", already in production for other
workflows) that reconciles four existing data sources and applies the
edits.

The question for IT is **where the web app runs and how staff
authenticate**, plus the Graph API permissions it needs.

---

## What already exists

Rocky runs today as scheduled Python jobs on a dedicated Windows laptop,
authenticating as `rocky@gallagherllp.com` with delegated Microsoft Graph
permissions. It currently holds:

`Mail.Read` · `Mail.Read.Shared` · `Mail.ReadWrite` ·
`Mail.ReadWrite.Shared` · `Mail.Send` · `Calendars.ReadWrite` ·
`Chat.Create` · `Chat.ReadWrite` · `ChatMessage.Send` ·
`Sites.Read.All` · `User.Read`

There is also an existing operations dashboard (Flask, port 5001) used
only by James over Tailscale. **It has no authentication and its API can
run Rocky commands**, so it is safe only because Tailscale is the
perimeter. The staff dashboard must be a separate application, not
another page on that one. Whichever option is chosen, a Tailscale ACL
should fence the existing port 5001 off from any new staff access.

---

## Option 1 — Azure App Service in the firm tenant, Entra ID SSO

**Shape.** The Flask app is deployed to Azure App Service (Linux, Python
3.12). Staff browse to an HTTPS URL and sign in with their normal M365
account. Deployment by zip-deploy or a GitHub Actions workflow.

**Authentication.** Either App Service's built-in Authentication
("Easy Auth") pointed at Entra ID, or MSAL for Python implementing the
OIDC authorization-code flow against a new Entra app registration (web
platform, redirect URI on the app's hostname). Existing Conditional
Access policies apply automatically. Note the firm's CA policy currently
blocks device-code flow; that restriction does not affect an interactive
web app.

**Network.** Public HTTPS by default. If the page should be reachable
only from firm networks, App Service supports IP access restrictions, or
VNet integration with a Private Endpoint for a true intranet-only
deployment.

**Data.** This is the significant consequence: Rocky's data currently
lives as files on a OneDrive/SharePoint share that syncs to the Rocky
laptop's local disk. An Azure-hosted app cannot read a locally synced
folder, so it would read and write that data through the Graph API
instead, or the ledger would move to Azure storage. Either is fine, but
it is real work and should be priced in.

**Indicative cost.** App Service B1 is roughly $13/month, S1 roughly
$70/month; B1 is adequate for this user count. No additional licensing.

**Trade-offs.** Proper availability, backup and TLS handled by the
platform; real user identity on every edit; no client software for staff.
Against that: an Azure subscription and deployment pipeline to own, and
the data-access rework above.

---

## Option 2 — Rocky laptop, reached over Tailscale

**Shape.** A second Flask app on the existing Windows laptop (say port
5002), started at boot by Task Scheduler. Staff install Tailscale, join
the firm tailnet, and browse to the machine's tailnet address.

**Authentication.** None by default; the tailnet is the perimeter.
Tailscale Serve can pass the authenticated Tailscale identity to the app
as a header, which gives a usable username for the audit log. Entra SSO
can still be added on top and probably should be if the page can write.

**Network.** Every staff member needs the Tailscale client. Tailscale's
free tier covers 3 users and 100 devices; beyond that the paid tiers run
roughly $6/user/month. **ACLs must be configured** so staff can reach the
new app's port and not the unauthenticated operations dashboard on 5001.

**Data.** Direct filesystem access to the already-synced share. No
rework at all — this is the option's main advantage.

**Trade-offs.** Fastest and cheapest to stand up, and no IT deployment
pipeline. Against that: a laptop becomes the single point of failure for
the team's daily tracking tool; it already runs Rocky's monitor loop and
scheduled jobs; Windows Update reboots take the page down; there is no
high availability, and backup is whatever OneDrive sync provides. Client
software on every staff machine is a standing support burden.

---

## Option 3 — SharePoint lists with a Power Apps front end

**Shape.** Rocky writes its reconciled case state into SharePoint lists
on the existing MultifamilyHousing site. A Power Apps canvas app is the
UI, and Power Automate flows handle write-backs to the spreadsheet.

**Authentication.** Built in. Staff are already signed in, and existing
DLP and governance policies apply with no new work.

**Licensing.** Power Apps using standard connectors (SharePoint, Excel,
Outlook, Teams) is generally included with M365 business plans. Calling
Rocky's own API, or any custom connector, is a **premium** connector and
needs per-user or per-app licensing (roughly $5–20/user/month) — worth
confirming against the firm's actual plan before committing.

**Data.** SharePoint lists. The case population is around 2,000 records,
comfortably under the 5,000-item list view threshold, but the threshold
should be designed around rather than discovered.

**Trade-offs.** No hosting for the firm to run, mobile access for free,
governance already in place. Against that: the logic splits across Python
and the Power Platform, giving two maintenance surfaces; Power Apps is
difficult to version-control or code-review compared to a Python repo.

---

## Option 4 — Start on Tailscale, migrate to Azure

Build Option 2 first to establish which panels and which write actions
the team actually uses, then move to Option 1 once the shape is settled.
Defers the Azure work and the IT ticket. The standard risk applies: the
temporary host becomes the permanent one.

---

## Graph API permissions the write features need

Today Rocky can read the SharePoint spreadsheet (`Sites.Read.All`) and
already has the mail and calendar rights it needs. Writing requires:

| Capability | Permission | Note |
|---|---|---|
| Update the LLT spreadsheet | **`Sites.Selected`** | See below — preferred over `Sites.ReadWrite.All` |
| Read the task-list notebook | `Notes.Read` | Currently blocked; the notebook is OneNote and the API returns 401 without it |
| Update the task-list notebook | `Notes.ReadWrite` | Phase 2 only |
| Move/delete mail folders | `Mail.ReadWrite` | **already granted** |
| Publish court dates to a calendar | `Calendars.ReadWrite` | **already granted** |

**`Sites.Selected` is the one worth IT's attention.** Rather than
granting write access to every SharePoint site in the tenant
(`Sites.ReadWrite.All`), `Sites.Selected` grants the application write
access to **named sites only** — here, just MultifamilyHousing. It is
the least-privilege option and the one we would ask for.

**Two further points on writes:**

1. **Spreadsheet edits must use the Graph Excel API** (the workbook
   session and range endpoints), which changes individual cells and
   coexists with people editing the file in Excel Online. The naive
   approach of downloading the workbook, editing it, and uploading it
   back would silently destroy a colleague's concurrent edits. This
   should be validated against the real file first: it is 1.3 MB with 33
   worksheets, and the Excel API has size and complexity limits.
2. **Writes can run as the signed-in user** (OAuth on-behalf-of) rather
   than as Rocky. Doing so makes SharePoint's own version history show
   the actual person who made each change, which is a materially better
   audit trail than every edit appearing as `rocky@`. It requires the
   dashboard's Entra app registration to be configured for delegated
   access.

---

## Other points IT will want to raise

**Client confidentiality.** The data is client matter information for
landlord-tenant cases: resident names, units, balances, case numbers.
Anything hosted must sit inside the firm's own tenant and be covered by
existing retention and eDiscovery policy. No third-party hosting.

**Auditability.** Every edit made from the page is recorded with the
user, the timestamp, the before value and the after value, in an
append-only log. That log is also how the system earns the right to take
on more automated work over time.

**Reversibility.** No action deletes data without a logged before-state.
Folder operations were tested on a single folder first and confirmed
reversible before any batch ran.

**Backup.** The ledger is currently plain files on the firm's SharePoint,
so it inherits SharePoint's versioning and retention. If the app moves to
Azure, the backup story moves with it and needs to be specified.

**Mailbox access.** Extending the dashboard to cover a second staff
member's inbox (Christina's is the next one) requires adding that
mailbox to the Application Access Policy. No code change, no new
permission — one Exchange step per person.

---

## Recommendation

**Option 1, Azure App Service with Entra ID SSO**, for a tool the whole
team will depend on daily and that can write to shared records. Real
identity on every edit is the part that matters most, because it is both
the audit trail and the safety mechanism.

If the Azure work cannot be scheduled soon, **Option 4** is a reasonable
path, provided the Tailscale ACL fencing off the existing
unauthenticated dashboard is done on day one.

## Questions for IT

1. Is there an existing Azure subscription this could deploy into, and a
   preferred deployment pattern?
2. Is `Sites.Selected` acceptable, scoped to the MultifamilyHousing site?
3. Should the page be internet-reachable with SSO, or restricted to firm
   networks by IP or Private Endpoint?
4. Does the firm's M365 plan include the Power Apps connectors Option 3
   would need, if that route is preferred?
5. Any objection to granting `Notes.Read` so the team's task-list
   notebook can be read?
