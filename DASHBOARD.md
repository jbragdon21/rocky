# Rocky Dashboard — how to launch it on the Rocky laptop

The dashboard is a small web page that shows what Rocky is doing and lets you
control it from any device:

- **Live activity log** — in **Plain English** by default (toggle to Technical
  for the raw log).
- **Pause / Resume Rocky** (the kill switch).
- **Run a command now** — one-click ▶ on any Rocky job.
- **Scheduling** — 📅 to schedule one job, **⚡ Auto-setup** to install the whole
  recommended daily schedule at once, or **+ New** to set a custom time. Each
  scheduled job can be edited (🕑), turned on/off, or deleted (🗑).

It runs as `dashboard.exe` and needs nothing installed — Python and all
libraries are bundled inside the .exe.

---

## One-time setup on the Rocky laptop

1. **Get the latest `dashboard.exe`.** After a build it syncs via OneDrive to:

   ```
   ...\OneDrive - gejlaw.com\Program Files\Rocky\dashboard.exe
   ```

   Wait for OneDrive to show the green check on that file, then copy it next to
   `rocky.exe`:

   ```powershell
   Copy-Item "$env:USERPROFILE\OneDrive - gejlaw.com\Program Files\Rocky\dashboard.exe" "C:\Rocky\dashboard.exe" -Force
   ```

   (Running from `C:\Rocky` matters: the dashboard reads `rocky.log`, `state\`,
   and `config.json` from there automatically.)

---

## Launching it

Open PowerShell and run:

```powershell
cd C:\Rocky
.\dashboard.exe
```

Leave that window open — it's the server. You'll see:

```
Rocky Dashboard → http://localhost:5001
```

Then open a browser on the laptop to **http://localhost:5001**.

To stop it: close the PowerShell window (or press `Ctrl+C` in it).

### Viewing it from another device (your office laptop, phone, etc.)

- **Same network:** find the Rocky laptop's address with `ipconfig` (look for
  the IPv4 Address, e.g. `192.168.1.50`), then browse to
  `http://192.168.1.50:5001` from the other device. The first launch may pop a
  Windows Firewall prompt — allow it on **Private** networks.
- **Over Tailscale:** browse to `http://<rocky-tailscale-name>:5001`. Nothing
  else to open up.

The dashboard listens on all network interfaces by default (`--host 0.0.0.0`),
so remote access works out of the box.

### Options

```powershell
.\dashboard.exe --port 8080      # use a different port (default 5001)
.\dashboard.exe --host 127.0.0.1 # local-only, not reachable from other devices
```

---

## Make it start automatically at login (recommended)

So the dashboard is always up after a reboot, add a shortcut to the Startup
folder:

1. Press `Win + R`, type `shell:startup`, press Enter. A folder opens.
2. Right-click → **New → Shortcut**.
3. For the location, paste:
   ```
   C:\Rocky\dashboard.exe
   ```
4. Name it `Rocky Dashboard`, click Finish.
5. Right-click the new shortcut → **Properties** → set **Start in** to
   `C:\Rocky`, click OK.

Now it launches whenever the rocky user logs in. (A console window will stay
open; you can minimize it.)

---

## A note on scheduling and admin rights

- Tasks you create **from the dashboard** (📅, ⚡ Auto-setup, + New) work
  without administrator rights, and you can edit/delete/toggle them freely.
- If you ever need to enable/disable a task that was created **outside** the
  dashboard (e.g. set up by hand earlier, with elevated rights), Windows may
  refuse unless the dashboard is run as administrator. If a toggle says it
  failed, close the dashboard and relaunch PowerShell with **Run as
  administrator**, then start it again.

The dashboard never sends email and cannot act as you — it only starts the same
Rocky jobs you already run, pauses/resumes Rocky, and manages their schedule.

---

## Updating to a newer build

When a new `dashboard.exe` is built and synced:

1. Stop the running dashboard (close its window).
2. Re-copy it from OneDrive to `C:\Rocky` (the `Copy-Item` line above).
3. Launch it again.

That's it — no reinstall, no settings to migrate.
