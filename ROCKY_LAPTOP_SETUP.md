# Rocky Laptop Setup — Instructions for Jeff

Instructions for activating Rocky and Remy on the dedicated Rocky laptop. Rocky monitors James's inbox and classifies emails; Remy generates legal documents when Rocky identifies a request.

---

## Pre-requisites (IT admin — Jeff)

One pending item before the laptop setup will work:

- **Conditional Access exception.** The firm's default policy blocks device code flow. Rocky needs an exclusion for `rocky@gallagherllp.com` in Entra ID (Azure AD) → Conditional Access. Without this, the first login will fail with `AADSTS53003`. The app registration and mailbox delegation are already done.

---

## Where the program runs from

**The Rocky laptop runs the `.exe` files directly out of the OneDrive folder.** They are not copied to the C: drive. This is deliberate: a rebuild on James's dev laptop syncs to the Rocky laptop on its own, with no copy-paste step.

This works because the frozen build splits program from data ([`rocky.py`](rocky.py) ~line 193):

| | Location | Contents |
|---|---|---|
| **Program** (`PROGRAM_DIR`) | the OneDrive folder the `.exe` sits in | `rocky.exe`, `dashboard.exe`, `restart_dashboard.cmd`, `instructions.md` |
| **Data** (`DATA_DIR`) | `C:\Rocky` — always local, never synced | `config.json`, `state\token_cache.json`, `rocky.log`, `classifications.jsonl` |

API keys and auth tokens stay on the machine. The program folder is only ever read from, so Rocky never writes into a synced folder and can't cause a sync storm.

**The canonical program path on the Rocky laptop is:**

```
C:\Users\rocky\OneDrive - gejlaw.com\James D. Bragdon's files - Program Files\Rocky
```

Two things differ from James's dev laptop: the profile is `rocky`, and folders shared from James's account pick up a `James D. Bragdon's files - ` prefix. The dev-laptop equivalent — the path `build_exe.py` deploys to — is `C:\Users\jbragdon\OneDrive - gejlaw.com\Program Files\Rocky`. **Use the `rocky` path in every command and scheduled task on this machine.**

Everything below refers to that folder as **`<PROGRAM>`**. Substitute the full path when typing commands.

---

## Laptop setup

### 1. Sign into OneDrive

Sign in as `rocky@gallagherllp.com`; James's folders arrive as shares on that account (that's what produces the `James D. Bragdon's files - ` prefix above). Rocky and Remy both live on OneDrive and need the following folders synced locally.

**Pin these folders as "Always keep on this device"** (right-click → Always keep on this device):

- `James D. Bragdon's files - Program Files\Rocky` — contains `rocky.exe` and `dashboard.exe`
- `OneDrive - gejlaw.com\Rocky Cases` — case folders Rocky reads and writes to
- `OneDrive - gejlaw.com\Remy Outputs` — where Remy-generated drafts are saved (create this folder if it doesn't exist)

Pinning the program folder is not optional. `rocky.exe` is ~54 MB; if Storage Sense dehydrates it to a cloud-only placeholder, a scheduled launch turns into a download, and fails outright if the laptop is offline.

### 2. Install Python 3.12

Rocky itself is a standalone `.exe` and doesn't need Python. But **Remy does** — it runs as a Python script that Rocky calls.

1. Download Python 3.12 from https://www.python.org/downloads/ (not 3.13 or 3.14 — some dependencies lag)
2. During install, **check "Add Python to PATH"**
3. Open a command prompt and verify: `python --version` should show 3.12.x

### 3. Set up Remy

Remy's code is on OneDrive at James's Desktop (or wherever it syncs). On the Rocky laptop:

1. Open a command prompt
2. Navigate to the Remy folder:
   ```
   cd "C:\Users\jbragdon\Desktop\REMY"
   ```
   (The exact path depends on where OneDrive syncs James's Desktop. Check File Explorer.)
3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
4. Copy `config.example.json` to `config.json` in the same folder
5. Edit `config.json` and add the Anthropic API key (James will provide this)

### 4. Create Rocky's local data folder

Rocky's config and runtime state live locally at `C:\Rocky` — not on OneDrive. This keeps API keys and auth tokens off the cloud.

Create the folder `C:\Rocky` and put `config.json` in it. Nothing else belongs there — in particular, **do not copy the `.exe` files into `C:\Rocky`.** If a previous setup left copies there, delete `C:\Rocky\rocky.exe` and `C:\Rocky\dashboard.exe` so a stale build can't be launched by accident, but leave `config.json`, `state\`, and `rocky.log` alone.

1. Create the folder `C:\Rocky`
2. Create `C:\Rocky\config.json` with this content (James will fill in the real values):

```json
{
    "client_id": "________-____-____-____-____________",
    "tenant_id": "________-____-____-____-____________",
    "user_emails": [
        "jbragdon@gallagherllp.com",
        "rocky@gallagherllp.com"
    ],
    "anthropic_api_key": "sk-ant-__________________________",
    "kill_switch_authorized": [
        "jbragdon@gallagherllp.com"
    ],
    "enable_remy_invocation": true,
    "remy_cli_path": "C:\\Users\\jbragdon\\Desktop\\REMY\\remy_cli.py",
    "remy_outputs_path": "C:\\Users\\jbragdon\\OneDrive - gejlaw.com\\Remy Outputs",
    "remy_python_path": "python"
}
```

**Note:** The `remy_cli_path` needs to match wherever Remy actually landed on this machine. Check the path in File Explorer and adjust if needed.

### 5. First-time authentication

Rocky authenticates as `rocky@gallagherllp.com` using device code flow (a one-time browser login). After the first login, the token refreshes automatically.

1. Open a command prompt
2. Run:
   ```
   "C:\Users\rocky\OneDrive - gejlaw.com\James D. Bragdon's files - Program Files\Rocky\rocky.exe"
   ```
3. Rocky will print a message like:
   ```
   To sign in, use a web browser to open https://microsoft.com/devicelogin
   and enter the code XXXXXXXX to authenticate.
   ```
4. Open that URL in a browser on any device, enter the code, and sign in as `rocky@gallagherllp.com`
5. Rocky should start polling. You'll see log output confirming it connected. Press Ctrl+C to stop it for now.

If you get `AADSTS53003`, the Conditional Access exception (step in Pre-requisites above) hasn't been applied yet.

### 6. Set up Task Scheduler

**Easiest route: let the dashboard do it.** Start `dashboard.exe` from `<PROGRAM>`, open http://localhost:5001, and use the schedule panel's "set up recommended" action. The dashboard builds each task's command line from its own location (`PROGRAM_DIR`, [`dashboard.py:192`](dashboard.py:192)), so tasks it creates while running from OneDrive point at the OneDrive `rocky.exe` automatically — no path to mistype.

If tasks already exist pointing at `C:\Rocky\rocky.exe`, delete them first, then recreate. Editing the start time won't fix the program path.

> **Verify the first one actually runs.** The OneDrive path contains spaces *and* an apostrophe (`James D. Bragdon's files`), where `C:\Rocky\rocky.exe` had neither — so this is the first time `schtasks` quoting matters here. Run one task manually from Task Scheduler and confirm it fires before trusting the rest.

To create them by hand instead, the three daily jobs are:

| Name | Trigger | Arguments |
|---|---|---|
| Rocky Daily Cases | Daily 4:00 PM | `--daily-cases` |
| Rocky Daily Run | Daily 4:30 PM | `--daily-run` |
| Rocky Daily Digest | Daily 5:00 PM | `--daily-digest` |

For each one:

- **Action:** Start a program
  - Program: `"C:\Users\rocky\OneDrive - gejlaw.com\James D. Bragdon's files - Program Files\Rocky\rocky.exe"`
  - Start in: `"C:\Users\rocky\OneDrive - gejlaw.com\James D. Bragdon's files - Program Files\Rocky"`
- **Settings:**
  - **Run only when the user is logged on.** The laptop stays logged in as `rocky`, and that profile is where the OneDrive folder and the token cache live. It also means no stored password, which is what lets the dashboard create and edit these tasks without running as administrator.
  - Stop the task if it runs longer than 1 hour

Nothing more is needed to keep Rocky current. Each task is a fresh one-shot launch, so it runs whatever build OneDrive has landed.

### 7. Set up the dashboard restart task

The dashboard is the one long-lived process, so it's the only thing that needs restarting. A running `dashboard.exe` keeps serving the code it started with, and its image file can block the update OneDrive is trying to land. `restart_dashboard.cmd` (deployed to `<PROGRAM>` by `build_exe.py`) handles both: it kills the process, pauses briefly to release the lock, and relaunches from the same folder. It locates itself via `%~dp0`, so the same file works on either laptop.

Create one task:

- **Name:** `\Rocky\Dashboard Restart`
- **Trigger:** Daily at 3:00 AM, **and** At log on (so a reboot brings the dashboard back up)
- **Action:** Start a program
  - Program: `"C:\Users\rocky\OneDrive - gejlaw.com\James D. Bragdon's files - Program Files\Rocky\restart_dashboard.cmd"`
- **Settings:** Run only when the user is logged on

Or from an elevated command prompt on the laptop:

```
schtasks /create /tn "\Rocky\Dashboard Restart" /tr "\"C:\Users\rocky\OneDrive - gejlaw.com\James D. Bragdon's files - Program Files\Rocky\restart_dashboard.cmd\"" /sc DAILY /st 03:00 /it /f
```

The script is safe to run when nothing is running — `taskkill` no-ops and it just starts the dashboard — so it doubles as a "make sure the dashboard is up" job. It appends to `C:\Rocky\dashboard_restart.log`.

### 8. Verify everything works

1. Reboot the laptop and wait 2 minutes for OneDrive to sync
2. Run `rocky.exe --daily-cases` manually. Check `C:\Rocky\rocky.log` for successful token acquisition and email fetch from case folders.
3. Run `rocky.exe --daily-run` manually. Check that case folders with `_project/instructions.md` get processed.
4. Run `rocky.exe --daily-digest` manually. Check `Rocky Cases\Daily Digests\` for the day's file.
5. Open the dashboard and read the build stamp in the header (`build · dash <date> · rocky <date>`). Those are the build times of the two exes in `<PROGRAM>` — compare them against the last rebuild on the dev laptop to confirm the sync arrived.

---

## Confirming the laptop is running the latest build

The dashboard header answers this without an RDP session:

- **`build · dash … · rocky …`** — build times of `dashboard.exe` and `rocky.exe` in the program folder. `rocky.exe` updating here is all that's needed; the next scheduled run uses it.
- **`⟳ New build — restart`** (amber badge) — a newer `dashboard.exe` landed after the running one started, so the dashboard itself is a build behind. The 3:00 AM task clears it; run `restart_dashboard.cmd` to clear it now.

If the build times don't move after a rebuild, the problem is OneDrive sync, not Rocky: check the OneDrive icon on the laptop for a paused or errored state.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `AADSTS53003` on first login | Conditional Access exception not applied for rocky@ |
| `Config file not found` | `C:\Rocky\config.json` is missing or has the wrong path |
| `Case index not found` | OneDrive hasn't synced, or Rocky Cases isn't pinned locally |
| `PermissionError` on case index | Rocky Cases folder is cloud-only — pin it "Always keep on this device" |
| A rebuild doesn't take effect | Check the dashboard's build stamp. If it hasn't moved, OneDrive hasn't synced. If it moved but behavior didn't change, the dashboard needs a restart — look for the amber badge. |
| Dashboard shows the amber badge for days | The 3:00 AM restart task isn't running. Check `C:\Rocky\dashboard_restart.log` for entries. |
| Scheduled task fails immediately, exit code 0x1 | Program path is wrong or unquoted. It must be the full `C:\Users\rocky\OneDrive - gejlaw.com\James D. Bragdon's files - Program Files\Rocky\rocky.exe`, quoted. |
| Task launch fails once, then works | The exe was mid-swap as OneDrive landed an update. Harmless; the next run is fine. |
| `No instructions file found` in the log | `instructions.md` is missing from the program folder. `build_exe.py` seeds it there, but only if absent — copy it in manually if needed. |
| Rocky runs an ancient build | Check for leftover `C:\Rocky\rocky.exe` and a task still pointing at it. Delete both; recreate the task from the dashboard. |
| Remy invocation fails with "not found" | Check that `remy_cli_path` in config.json points to the real location of remy_cli.py on this machine |
| Remy invocation fails with module errors | Remy's Python dependencies aren't installed — run `pip install -r requirements.txt` in the Remy folder |
| Rocky stops processing but is running | Someone sent "ROCKY STOP" — check `C:\Rocky\state\dormant.flag`. Delete it or send "ROCKY START" |

---

## Emergency stop

From any device, send an email with **ROCKY STOP** in the subject line from `jbragdon@gallagherllp.com`. Rocky goes dormant within 5 minutes. Send **ROCKY START** to resume.

Or: RDP into the Rocky laptop and kill the process / delete `C:\Rocky\state\dormant.flag`.
