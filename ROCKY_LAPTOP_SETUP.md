# Rocky Laptop Setup — Instructions for Jeff

Instructions for activating Rocky and Remy on the dedicated Rocky laptop. Rocky monitors James's inbox and classifies emails; Remy generates legal documents when Rocky identifies a request.

---

## Pre-requisites (IT admin — Jeff)

One pending item before the laptop setup will work:

- **Conditional Access exception.** The firm's default policy blocks device code flow. Rocky needs an exclusion for `rocky@gallagherllp.com` in Entra ID (Azure AD) → Conditional Access. Without this, the first login will fail with `AADSTS53003`. The app registration and mailbox delegation are already done.

---

## Laptop setup

### 1. Sign into OneDrive

Sign into OneDrive on the Rocky laptop with James's account (`jbragdon@gallagherllp.com`). Rocky and Remy both live on OneDrive and need the following folders synced locally.

**Pin these folders as "Always keep on this device"** (right-click → Always keep on this device):

- `OneDrive - gejlaw.com\Program Files` — contains `rocky.exe`
- `OneDrive - gejlaw.com\Rocky Cases` — case folders Rocky reads and writes to
- `OneDrive - gejlaw.com\Remy Outputs` — where Remy-generated drafts are saved (create this folder if it doesn't exist)

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
    "remy_outputs_path": "C:\\Users\\jbragdon\\OneDrive\\OneDrive - gejlaw.com\\Remy Outputs",
    "remy_python_path": "python"
}
```

**Note:** The `remy_cli_path` needs to match wherever Remy actually landed on this machine. Check the path in File Explorer and adjust if needed.

### 5. First-time authentication

Rocky authenticates as `rocky@gallagherllp.com` using device code flow (a one-time browser login). After the first login, the token refreshes automatically.

1. Open a command prompt
2. Run:
   ```
   "C:\Users\jbragdon\OneDrive\OneDrive - gejlaw.com\Program Files\rocky.exe"
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

Create three Task Scheduler entries:

#### A. Rocky — runs at boot, polls inbox every 5 minutes

- **Name:** Rocky
- **Trigger:** At startup (delay 60 seconds to let OneDrive sync)
- **Action:** Start a program
  - Program: `"C:\Users\jbragdon\OneDrive\OneDrive - gejlaw.com\Program Files\rocky.exe"`
  - Start in: `"C:\Users\jbragdon\OneDrive\OneDrive - gejlaw.com\Program Files"`
- **Settings:**
  - Run whether user is logged on or not
  - Run with highest privileges
  - If the task fails, restart every 1 minute, up to 3 times
  - Do not start a new instance if already running

#### B. Daily Run — 4:00 PM, processes each case folder's instructions

- **Name:** Rocky Daily Run
- **Trigger:** Daily at 4:00 PM
- **Action:** Start a program
  - Program: `"C:\Users\jbragdon\OneDrive\OneDrive - gejlaw.com\Program Files\rocky.exe"`
  - Arguments: `--daily-run`
  - Start in: `"C:\Users\jbragdon\OneDrive\OneDrive - gejlaw.com\Program Files"`
- **Settings:**
  - Run whether user is logged on or not
  - Stop the task if it runs longer than 1 hour

#### C. Daily Digest — 5:00 PM, generates the case activity summary

- **Name:** Rocky Daily Digest
- **Trigger:** Daily at 5:00 PM
- **Action:** Start a program
  - Program: `"C:\Users\jbragdon\OneDrive\OneDrive - gejlaw.com\Program Files\rocky.exe"`
  - Arguments: `--daily-digest`
  - Start in: `"C:\Users\jbragdon\OneDrive\OneDrive - gejlaw.com\Program Files"`
- **Settings:**
  - Run whether user is logged on or not
  - Stop the task if it runs longer than 1 hour

### 7. Verify everything works

1. Reboot the laptop
2. Wait 2 minutes for OneDrive to sync and Rocky to start
3. Check `C:\Rocky\rocky.log` — should show "Rocky starting up" and successful token acquisition
4. Send a test email to `jbragdon@gallagherllp.com` with "RRID-0001" in the subject. Within 5 minutes, Rocky should log that it matched and saved it to the case folder.
5. At 4:00 PM (or run manually: `rocky.exe --daily-run`), check that case folders with `_project/instructions.md` get processed
6. At 5:00 PM (or run manually: `rocky.exe --daily-digest`), check `Rocky Cases\Daily Digests\` for the day's file

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `AADSTS53003` on first login | Conditional Access exception not applied for rocky@ |
| `Config file not found` | `C:\Rocky\config.json` is missing or has the wrong path |
| `Case index not found` | OneDrive hasn't synced, or Rocky Cases isn't pinned locally |
| `PermissionError` on case index | Rocky Cases folder is cloud-only — pin it "Always keep on this device" |
| Remy invocation fails with "not found" | Check that `remy_cli_path` in config.json points to the real location of remy_cli.py on this machine |
| Remy invocation fails with module errors | Remy's Python dependencies aren't installed — run `pip install -r requirements.txt` in the Remy folder |
| Rocky stops processing but is running | Someone sent "ROCKY STOP" — check `C:\Rocky\state\dormant.flag`. Delete it or send "ROCKY START" |

---

## Emergency stop

From any device, send an email with **ROCKY STOP** in the subject line from `jbragdon@gallagherllp.com`. Rocky goes dormant within 5 minutes. Send **ROCKY START** to resume.

Or: RDP into the Rocky laptop and kill the process / delete `C:\Rocky\state\dormant.flag`.
