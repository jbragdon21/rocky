"""
Rocky Dashboard — local web UI for monitoring and managing Rocky.

Start:
    python dashboard.py                 (dev)
    python dashboard.py --port 8080     (custom port)
    dashboard.exe                       (production)

Opens a web server at http://localhost:5001 with:
- Live log viewer (tails rocky.log + today's Maple updater run log via SSE)
- Dormant/active status toggle (kill switch)
- Scheduled task status and enable/disable controls
- Running process indicators (lock-file check)

Accessible from any device on the same network (or via Tailscale).
"""

import csv
import io
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, request, render_template

# ---------------------------------------------------------------------------
# Paths — mirror rocky.py constants
# ---------------------------------------------------------------------------

if getattr(sys, "frozen", False):
    _BUNDLE_DIR = Path(sys._MEIPASS)
    PROGRAM_DIR = Path(sys.executable).parent
else:
    _BUNDLE_DIR = Path(__file__).parent
    PROGRAM_DIR = Path(__file__).parent

DATA_DIR = Path(os.environ.get("ROCKY_DATA_DIR", r"C:\Rocky"))
if not DATA_DIR.exists():
    DATA_DIR = PROGRAM_DIR

CONFIG_PATH = DATA_DIR / "config.json"
STATE_DIR = DATA_DIR / "state"
LOG_PATH = DATA_DIR / "rocky.log"

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(
    __name__,
    template_folder=str(_BUNDLE_DIR / "templates"),
)

log = logging.getLogger("dashboard")


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


# ===================================================================
# Rocky command registry
# ===================================================================
# The single source of truth for which Rocky commands the dashboard can run
# on demand or schedule. The `flag` is the bare command name (no leading
# "--"); it must match (a) the flag rocky.py's main() dispatches on and (b)
# the lock-file stem rocky writes (state/rocky_<flag>.lock), so the
# running-now indicator lines up automatically. Anything not in this list
# cannot be launched from the dashboard — this is the allowlist that keeps
# /api/run from being an arbitrary-command sink.

# Scheduling fields per command:
#   args           extra fixed argv after the flag (e.g. litigation runs
#                  "--litigation --poll")
#   sched_time     "HH:MM" default start time (None = no sensible default)
#   sched_freq     DAILY | MINUTE | ... (default DAILY when a time is set)
#   sched_interval minutes between runs, for MINUTE frequency
#   recommended    include in the one-click "recommended daily schedule"
#                  (True only for commands with a *documented* run time)
# Documented times come from rocky.py's header docstring + BUILD_REFERENCE.
#
# The "Maple" group is the daily Maple loop, in run order:
#   maple-pma-activity (15:30, feed export) -> maple-updater (~16:00,
#   run-daily-update.ps1 — the Maple repo's own script, launched here as an
#   *external* command; writes HubSpot + the outbox digest) -> maple-digest
#   (19:00, drafts the outbox digest into James's Drafts for him to send).
#
# External commands (external=True) are not rocky.py flags: they run their
# own program (argv from external_argv()), have no rocky lock file, and are
# left out of the recommended auto-setup because the Maple side may already
# have its own Task Scheduler entry — creating a second one would double-run
# it. The dashboard still finds that existing task (the schtasks query also
# matches "maple" names and run-daily-update.ps1 actions).
ROCKY_COMMANDS = [
    {"flag": "daily-cases",   "label": "Daily Cases",     "group": "Cases", "dry_run": False, "desc": "Collect and summarize today's emails for each case",    "sched_time": "16:00", "recommended": True},
    {"flag": "daily-run",     "label": "Daily Run",       "group": "Cases", "dry_run": False, "desc": "File new documents into each case folder",              "sched_time": "16:30", "recommended": True},
    {"flag": "daily-digest",  "label": "Daily Digest",    "group": "Cases", "dry_run": False, "desc": "Write the end-of-day summary of case activity",         "sched_time": "17:00", "recommended": True},
    {"flag": "steve-todo",    "label": "Steve To-Do",     "group": "Inbox", "dry_run": False, "desc": "Build Steve's morning to-do list from his email",       "sched_time": "07:30", "recommended": True},
    {"flag": "ella-digest",   "label": "Ella Digest",     "group": "Inbox", "dry_run": False, "desc": "Write Ella's daily summary of her case emails",         "sched_time": "17:00", "recommended": True},
    {"flag": "pending-llt",   "label": "Pending LLT",     "group": "Inbox", "dry_run": True,  "desc": "Draft status-update emails for landlord-tenant matters"},
    {"flag": "maple-pma-activity", "label": "Maple PMA Activity", "group": "Maple", "dry_run": False, "desc": "Step 1 — collect the day's PMA emails for Maple", "sched_time": "15:30", "recommended": True},
    {"flag": "maple-updater", "label": "Maple Updater",   "group": "Maple", "dry_run": False, "desc": "Step 2 — Maple updates HubSpot and writes the client update", "sched_time": "16:00", "external": True},
    {"flag": "maple-digest",  "label": "Maple Digest",    "group": "Maple", "dry_run": True,  "desc": "Step 3 — put the client update in James's Drafts to send", "sched_time": "19:00", "recommended": True},
    {"flag": "litigation",    "label": "Litigation Updater", "group": "Litigation", "dry_run": True, "args": ["--poll"], "desc": "Check for Bozzuto legal notices & claim requests, propose over Teams", "sched_time": "10:00"},
    {"flag": "litigation-digest", "label": "Litigation Digest", "group": "Litigation", "dry_run": True, "desc": "Draft the day's claims activity into James's Drafts", "sched_time": "18:30"},
    {"flag": "litigation-learn", "label": "Litigation Learn", "group": "Litigation", "dry_run": False, "desc": "Fold the week's Teams feedback into the litigation brain"},
    # LetterStream's documented slot is 8:00 AM, but it's deliberately NOT
    # "recommended": the --monitor loop already runs it every 10 minutes
    # (like the hourly vault-mail/vault-inbox), and auto-setup shouldn't
    # install a daily task that double-runs beside a deployed monitor.
    # Overlap is safe (instance lock) — just redundant.
    {"flag": "letterstream",  "label": "LetterStream",    "group": "Mail",  "dry_run": True,  "desc": "Certified mail sweep — requests, releases, tracking, affidavits & reminders", "sched_time": "08:00"},
    # No schedule slot: since 2026-08-30 the Multifamily Digest generates
    # the Remy digest in-process before assembling — this button is for
    # manual/off-cycle runs only.
    {"flag": "remy-digest",   "label": "Remy Digest",     "group": "Other", "dry_run": True,  "args": ["--no-email"], "desc": "Write the day's Remy digest to GitHub + disk (manual — the Multifamily Digest generates and emails it daily)"},
    {"flag": "vault-dropbox", "label": "Vault Dropbox",   "group": "Vault", "dry_run": True,  "desc": "Pull new documents from the client Dropbox sources",        "sched_freq": "HOURLY", "sched_interval": 1},
    {"flag": "vault-mail",    "label": "Vault Rocky Inbox", "group": "Vault", "dry_run": True, "desc": "File documents emailed to rocky@ with 'Vault' in the subject", "sched_freq": "HOURLY", "sched_interval": 1},
    {"flag": "vault-inbox",   "label": "Vault James Inbox", "group": "Vault", "dry_run": True, "desc": "Sweep James's inbox for leases, ledgers & affidavits",      "sched_freq": "HOURLY", "sched_interval": 1},
    {"flag": "vault",         "label": "The Vault",       "group": "Vault", "dry_run": True,  "desc": "All Vault sources in one run — manual use; don't schedule beside the split tasks"},
    {"flag": "multifamily-digest", "label": "Multifamily Digest", "group": "Vault", "dry_run": True,  "desc": "Draft the daily digest into James's Drafts to send: certified mail, affidavits, Vault additions, Remy development, and pending items", "sched_time": "17:45", "recommended": True},
    {"flag": "monitor",       "label": "Monitor",         "group": "Other", "dry_run": False, "desc": "Fast loop — LetterStream + Vault mail sweeps every 10 min (stays running)"},
]

_COMMANDS_BY_FLAG = {c["flag"]: c for c in ROCKY_COMMANDS}

# Maple updater script — lives in the Maple OneDrive folder, not this repo.
# Default is the production path on the Rocky laptop (rocky profile); other
# machines (e.g. the dev laptop, where the same folder mounts under the
# jbragdon profile) override with "maple_updater_script" in config.json.
_DEFAULT_MAPLE_UPDATER_SCRIPT = (
    r"C:\Users\rocky\OneDrive - gejlaw.com"
    r"\James D. Bragdon's files - Program Files\Maple\Maple updater agent"
    r"\run-daily-update.ps1"
)


def maple_updater_script() -> Path:
    return Path(
        load_config().get("maple_updater_script", _DEFAULT_MAPLE_UPDATER_SCRIPT)
    )


def rocky_target() -> list[str]:
    """
    Return the argv prefix that invokes Rocky, minus the command flag.

    Frozen build → the sibling rocky.exe. Dev → ``python rocky.py``.
    """
    if getattr(sys, "frozen", False):
        return [str(PROGRAM_DIR / "rocky.exe")]
    return [sys.executable, str(PROGRAM_DIR / "rocky.py")]


def external_argv(flag: str) -> list[str]:
    """Argv for an external (non-rocky.py) command in the registry."""
    if flag == "maple-updater":
        # No extra flags needed for live feedback: the dashboard tails the
        # updater's own run log (logs\scheduled_run_*.log) directly, so every
        # run shows in the viewer no matter how it was launched.
        return [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(maple_updater_script()),
        ]
    raise ValueError(f"No external argv defined for: {flag}")


def command_argv(flag: str, dry_run: bool = False,
                 extra_args: list[str] | None = None) -> list[str]:
    """Full argv for any registry command (rocky flag or external)."""
    cmd = _COMMANDS_BY_FLAG[flag]
    if cmd.get("external"):
        return external_argv(flag)
    # "args" = extra fixed argv after the flag (subcommands, e.g.
    # --litigation --poll). extra_args = per-launch additions from a
    # dedicated endpoint (e.g. --letterstream --fetch <tracking#>) — never
    # raw user input; callers validate first.
    argv = (rocky_target() + [f"--{flag}"] + list(cmd.get("args") or [])
            + list(extra_args or []))
    if dry_run and cmd.get("dry_run"):
        argv.append("--dry-run")
    return argv


def rocky_command_string(flag: str, dry_run: bool = False) -> str:
    """
    Build the quoted command line for Task Scheduler's ``/tr`` argument.
    """
    parts = command_argv(flag, dry_run)
    return " ".join(f'"{p}"' if " " in p else p for p in parts)


# External commands have no rocky lock file, so track the Popen handles we
# spawned ourselves. (A run started by Task Scheduler won't show here — the
# dashboard only knows about launches it made. Good enough for a button.)
_EXTERNAL_PROCS: dict[str, subprocess.Popen] = {}


def launch_command(flag: str, dry_run: bool = False,
                   extra_args: list[str] | None = None) -> dict:
    """
    Launch a registry command as a detached background process.

    Rocky writes its own progress to rocky.log, so the live log viewer is the
    feedback channel — we don't capture stdout here. Rocky's own per-command
    lock prevents a duplicate run if one is already in flight; external
    commands rely on the _EXTERNAL_PROCS tracking above.
    """
    if flag not in _COMMANDS_BY_FLAG:
        return {"success": False, "error": f"Unknown command: {flag}"}

    if flag in get_running_commands():
        return {"success": False, "error": f"{flag} is already running."}

    cmd = _COMMANDS_BY_FLAG[flag]
    if cmd.get("external"):
        script = maple_updater_script()
        if not script.exists():
            return {
                "success": False,
                "error": f"Updater script not found: {script}. Check "
                         f"'maple_updater_script' in config.json / OneDrive sync.",
            }
    argv = command_argv(flag, dry_run, extra_args)

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(PROGRAM_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
    except Exception as exc:
        log.error("Failed to launch %s: %s", flag, exc)
        return {"success": False, "error": str(exc)}

    if cmd.get("external"):
        _EXTERNAL_PROCS[flag] = proc

    log.info("Launched '%s' (dry_run=%s) via dashboard.", flag, bool(dry_run))
    return {"success": True, "flag": flag}


# ===================================================================
# Log tailing
# ===================================================================

def tail_log(path: Path, num_lines: int = 500) -> list[str]:
    """Return last *num_lines* lines from *path*."""
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return [l.rstrip() for l in f.readlines()[-num_lines:]]
    except Exception:
        return []


# --- Maple updater run log --------------------------------------------------
# run-daily-update.ps1 appends to logs\scheduled_run_<date>.log line-by-line
# during every run (however it was launched), including the relayed progress
# of the Python step. The viewer tails it alongside rocky.log. Its lines are
# "YYYY-MM-DD HH:MM:SS  msg" (no level tag); rewrite them into rocky.log's
# format so the front-end's level filters and Plain-English rules apply as-is.

_MAPLE_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(.*)$")


def maple_run_log_path() -> Path:
    """Today's Maple updater run log (may not exist yet)."""
    return (maple_updater_script().parent / "logs"
            / f"scheduled_run_{datetime.now():%Y-%m-%d}.log")


def _normalize_maple_line(line: str, last_ts: list) -> str:
    """Rewrite one Maple run-log line into rocky.log's format.

    last_ts is a 1-element list carrying the most recent timestamp forward so
    continuation lines (the multi-line run summary) stay ordered.
    """
    line = line.lstrip("\ufeff").rstrip()
    m = _MAPLE_LINE.match(line)
    if m:
        last_ts[0] = m.group(1)
        msg = m.group(2)
    else:
        msg = line
    level = "ERROR" if msg.lstrip().startswith(("FATAL", "| FATAL")) else "INFO"
    return f"{last_ts[0]},000 [{level}] [maple-updater] {msg}"


def _maple_ts_seed() -> list:
    return [f"{datetime.now():%Y-%m-%d} 00:00:00"]


_TS_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")


def merged_log_history(num_lines: int = 500) -> list[str]:
    """Last lines of rocky.log + today's Maple run log, merged by timestamp."""
    entries = []  # (timestamp key, insertion order, line)

    def add(lines):
        last = ""
        for ln in lines:
            m = _TS_PREFIX.match(ln)
            if m:
                last = m.group(1)
            entries.append((last, len(entries), ln))

    add(tail_log(LOG_PATH, num_lines))
    seed = _maple_ts_seed()
    add([_normalize_maple_line(l, seed)
         for l in tail_log(maple_run_log_path(), num_lines)])
    entries.sort(key=lambda e: (e[0], e[1]))
    return [e[2] for e in entries[-num_lines:]]


def stream_logs_merged():
    """SSE generator — pushes new lines from rocky.log and today's Maple run
    log as they appear. The Maple path is re-resolved every poll so the tail
    follows the date rollover, and a file that appears mid-stream (a run that
    just started) is streamed from its beginning.
    """
    offsets: dict[str, int] = {}
    buffers: dict[str, bytes] = {}
    maple_ts = _maple_ts_seed()

    def new_lines(path: Path, start_at_end: bool) -> list[str]:
        key = str(path)
        try:
            if not path.exists():
                offsets.pop(key, None)
                buffers.pop(key, None)
                return []
            size = path.stat().st_size
            if key not in offsets:
                offsets[key] = size if start_at_end else 0
                buffers[key] = b""
            if size < offsets[key]:      # file was truncated — start over
                offsets[key], buffers[key] = 0, b""
            if size == offsets[key]:
                return []
            with open(path, "rb") as f:
                f.seek(offsets[key])
                chunk = f.read()
                offsets[key] = f.tell()
            # Hold any partial trailing line (as bytes, so a multi-byte char
            # split across reads is never mangled) until it completes.
            buf = buffers[key] + chunk
            *raw, buffers[key] = buf.split(b"\n")
            return [r.decode("utf-8", errors="replace").rstrip("\r")
                    for r in raw]
        except OSError:
            return []

    first = True
    while True:
        lines = new_lines(LOG_PATH, start_at_end=first)
        lines += [_normalize_maple_line(l, maple_ts)
                  for l in new_lines(maple_run_log_path(), start_at_end=first)]
        first = False
        if lines:
            for ln in lines:
                yield f"data: {json.dumps(ln)}\n\n"
        else:
            time.sleep(0.5)
            yield ": keepalive\n\n"


# ===================================================================
# LetterStream (certified mail) — pipeline snapshot for the sidebar card
# ===================================================================

def letterstream_summary() -> dict:
    """
    What the certified-mail process is waiting on, for the Certified Mail
    card: affidavits awaiting Hailey's YES, mailings awaiting the
    requester's release, and released jobs still in flight.

    Reads the process's local state file directly (mailing_affidavits.py
    keeps it in DATA_DIR\\affidavits\\state.json) — no LetterStream or
    Graph calls, so it's cheap enough for the 10-second status poll.
    """
    try:
        state = json.loads((DATA_DIR / "affidavits" / "state.json")
                           .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    config = load_config()

    affidavits = []
    for tag, entry in sorted((state.get("pending") or {}).items()):
        fields = entry.get("fields") or {}
        detail = fields.get("property") or ""
        sent = (entry.get("created") or "")[:10]
        if sent:
            detail = f"{detail} — sent {sent}" if detail else f"sent {sent}"
        affidavits.append({"tag": tag,
                           "who": fields.get("tenant") or "?",
                           "detail": detail})

    releases = []
    for tag, entry in sorted((state.get("mail_pending") or {}).items()):
        cost = entry.get("cost")
        bits = []
        if isinstance(cost, (int, float)):
            bits.append(f"${cost:.2f}")
        if entry.get("requester"):
            bits.append(f"waiting on {entry['requester']}")
        releases.append({"tag": tag,
                         "who": entry.get("label") or "?",
                         "detail": " — ".join(bits)})

    in_flight = []
    for tag, entry in sorted((state.get("in_flight") or {}).items()):
        released = (entry.get("released") or "")[:10]
        in_flight.append({"tag": tag,
                          "who": entry.get("label") or "?",
                          "detail": f"released {released}" if released else ""})

    return {
        "configured": bool(config.get("letterstream_api_id")
                           and config.get("letterstream_api_key")),
        "affidavits": affidavits,
        "releases": releases,
        "in_flight": in_flight,
    }


# ===================================================================
# Dormant status (kill switch)
# ===================================================================

def get_dormant_status() -> dict:
    flag = STATE_DIR / "dormant.flag"
    if flag.exists():
        try:
            data = json.loads(flag.read_text(encoding="utf-8"))
            return {"dormant": True, **data}
        except Exception:
            return {"dormant": True}
    return {"dormant": False}


def toggle_dormant() -> dict:
    flag = STATE_DIR / "dormant.flag"
    if flag.exists():
        flag.unlink()
        log.info("Dormant mode cleared via dashboard.")
        return {"dormant": False}
    else:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "reason": "Paused via Rocky Dashboard",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        flag.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        log.info("Dormant mode activated via dashboard.")
        return {"dormant": True, **payload}


# ===================================================================
# Running-process detection (lock files)
# ===================================================================

def get_running_commands() -> list[str]:
    """
    Check which Rocky commands are currently running by probing lock files.
    A held lock means a rocky.exe process owns that command right now.
    """
    running = []

    # External commands: alive if a Popen we spawned hasn't exited yet.
    for flag, proc in list(_EXTERNAL_PROCS.items()):
        if proc.poll() is None:
            running.append(flag)
        else:
            del _EXTERNAL_PROCS[flag]

    if not STATE_DIR.exists():
        return running

    for lock_file in STATE_DIR.glob("rocky_*.lock"):
        command = lock_file.stem.replace("rocky_", "")
        try:
            fh = open(lock_file, "r+")
            try:
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                # Lock acquired → file is free → command NOT running.
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                fh.close()
            except (OSError, IOError):
                # Cannot lock → another process holds it → command IS running.
                fh.close()
                running.append(command)
        except Exception:
            pass

    return running


# ===================================================================
# Task Scheduler integration
# ===================================================================

# Extracts "--daily-cases" etc. from a task's command line so a scheduled
# task can be tied back to the registry command it runs.
_FLAG_IN_ACTION = re.compile(r"--([a-z][a-z0-9-]*)")

# schtasks "Last/Next Run Time" on an en-US system, e.g. "7/5/2026 4:00:00 PM".
_SCHTASKS_DT_FORMATS = ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S")

# The full verbose schtasks dump is slow (a second or two), and /api/status
# is polled every 10 s — cache it briefly. Mutations invalidate the cache so
# edits show up on the next poll.
_TASKS_CACHE: dict = {"at": 0.0, "tasks": []}
_TASKS_CACHE_TTL = 20  # seconds


def _invalidate_task_cache():
    _TASKS_CACHE["at"] = 0.0


def _parse_schtasks_dt(s: str) -> float | None:
    """Parse a schtasks date string to an epoch, or None."""
    s = (s or "").strip()
    for fmt in _SCHTASKS_DT_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.year < 2000:      # schtasks "never ran" sentinel (11/30/1999)
                return None
            return dt.timestamp()
        except ValueError:
            continue
    return None


def _match_task_flag(name: str, action: str) -> str | None:
    """Map a scheduled task to a registry command flag (or None)."""
    if "run-daily-update.ps1" in action.lower():
        return "maple-updater"
    m = _FLAG_IN_ACTION.search(action)
    if m and m.group(1) in _COMMANDS_BY_FLAG:
        return m.group(1)
    # Fallback: display name starts with a command's label (dashboard-created
    # tasks are named after the label).
    display = re.sub(r"^\\+.*\\+", "", name).lower()
    for cmd in ROCKY_COMMANDS:
        if display.startswith(cmd["label"].lower()):
            return cmd["flag"]
    return None


def _query_schtasks(force: bool = False) -> list[dict]:
    """
    Query Windows Task Scheduler for Rocky- and Maple-related tasks.

    One full verbose query (cached): the Maple updater's own task may live
    outside the ``\\Rocky\\`` folder, so a folder-scoped query would miss it.
    Kept: any task with rocky/maple in the name, or whose action runs
    run-daily-update.ps1.
    """
    if not force and time.time() - _TASKS_CACHE["at"] < _TASKS_CACHE_TTL:
        return _TASKS_CACHE["tasks"]

    csv_text = ""
    try:
        r = subprocess.run(
            ["schtasks", "/query", "/fo", "CSV", "/v"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if r.returncode == 0:
            csv_text = r.stdout
    except Exception:
        pass

    if not csv_text:
        return _TASKS_CACHE["tasks"]  # keep last good result on failure

    tasks: list[dict] = []
    seen: set[str] = set()
    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        for row in reader:
            name = row.get("TaskName", "")
            action = row.get("Task To Run", "") or ""
            lower = name.lower()
            if lower == "taskname":            # /v repeats the header row
                continue
            if ("rocky" not in lower and "maple" not in lower
                    and "run-daily-update" not in action.lower()):
                continue
            if lower in seen:                  # one row per trigger — dedupe
                continue
            seen.add(lower)
            flag = _match_task_flag(name, action)
            cmd = _COMMANDS_BY_FLAG.get(flag) if flag else None
            tasks.append({
                "name": name,
                "status": row.get("Status", "Unknown"),
                "next_run": row.get("Next Run Time", "N/A"),
                "last_run": row.get("Last Run Time", "N/A"),
                "last_result": row.get("Last Result", "N/A"),
                "enabled": row.get("Scheduled Task State", "").strip().lower()
                    == "enabled",
                "flag": flag,
                "group": cmd["group"] if cmd else None,
            })
    except Exception as exc:
        log.warning("Failed to parse schtasks output: %s", exc)

    _TASKS_CACHE["tasks"] = tasks
    _TASKS_CACHE["at"] = time.time()
    return tasks


def command_run_info(tasks: list[dict]) -> dict:
    """
    Per-command "last ran" / "next scheduled run" for the dashboard.

    Last ran: rocky commands truncate-and-rewrite their lock file at every
    start (scheduled or manual), so its mtime is the last start time. The
    Maple updater has no lock; its script writes logs\\scheduled_run_*.log on
    every run, so the newest of those stands in. schtasks "Last Run Time" is
    the fallback when neither artifact exists.
    Next run: earliest enabled scheduled task matched to the command.
    """
    info: dict[str, dict] = {}
    for cmd in ROCKY_COMMANDS:
        flag = cmd["flag"]
        last_epoch = None

        if cmd.get("external"):
            logs_dir = maple_updater_script().parent / "logs"
            try:
                mtimes = [p.stat().st_mtime
                          for p in logs_dir.glob("scheduled_run_*.log")]
                if mtimes:
                    last_epoch = max(mtimes)
            except OSError:
                pass
        else:
            lock = STATE_DIR / f"rocky_{flag}.lock"
            if lock.exists():
                try:
                    last_epoch = lock.stat().st_mtime
                except OSError:
                    pass

        matched = [t for t in tasks if t.get("flag") == flag]
        next_run, next_epoch = None, None
        for t in matched:
            if not t.get("enabled"):
                continue
            s = t.get("next_run") or ""
            if s.strip() in ("", "N/A", "Never", "Disabled"):
                continue
            e = _parse_schtasks_dt(s)
            if next_run is None or (e is not None
                                    and (next_epoch is None or e < next_epoch)):
                next_run, next_epoch = s.strip(), e
        if last_epoch is None:
            for t in matched:
                e = _parse_schtasks_dt(t.get("last_run") or "")
                if e is not None and (last_epoch is None or e > last_epoch):
                    last_epoch = e

        info[flag] = {
            "last_epoch": last_epoch,
            "next_run": next_run,
            "scheduled": bool(matched),
        }
    return info


def toggle_task(task_name: str, enable: bool) -> bool:
    """Enable or disable a Windows scheduled task by full name."""
    flag = "/enable" if enable else "/disable"
    try:
        r = subprocess.run(
            ["schtasks", "/change", "/tn", task_name, flag],
            capture_output=True,
            text=True,
            timeout=10,
        )
        _invalidate_task_cache()
        return r.returncode == 0
    except Exception:
        return False


_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{1,80}$")
_VALID_FREQ = {"DAILY", "WEEKLY", "HOURLY", "MINUTE", "ONCE"}


def _run_schtasks(args: list[str]) -> tuple[bool, str]:
    """Run a schtasks command; return (ok, message).

    stdin is forced to DEVNULL so a "run as password" prompt (which schtasks
    can emit on /change) gets EOF and never blocks the dashboard. Combined
    with /it on create (run only when logged on, no stored password), Rocky
    tasks edit cleanly without elevation.
    """
    try:
        r = subprocess.run(
            ["schtasks", *args],
            capture_output=True,
            text=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
        )
        out = (r.stdout or "") + (r.stderr or "")
        _invalidate_task_cache()
        return r.returncode == 0, out.strip()
    except Exception as exc:
        return False, str(exc)


def create_task(
    flag: str,
    time_str: str,
    frequency: str = "DAILY",
    interval: int | None = None,
    dry_run: bool = False,
    name: str | None = None,
) -> tuple[bool, str, str]:
    """
    Create a Windows scheduled task that runs a Rocky command.

    Returns (ok, full_task_name, message). The task is created in the
    ``\\Rocky\\`` folder so the dashboard's task query picks it up.
    """
    if flag not in _COMMANDS_BY_FLAG:
        return False, "", f"Unknown command: {flag}"

    frequency = (frequency or "DAILY").upper()
    if frequency not in _VALID_FREQ:
        return False, "", f"Invalid frequency: {frequency}"

    # ONCE/DAILY/WEEKLY take a start time; HOURLY/MINUTE take an interval.
    needs_time = frequency in {"ONCE", "DAILY", "WEEKLY"}
    if needs_time and not _TIME_RE.match(time_str or ""):
        return False, "", "Time must be HH:MM (24-hour)."

    label = _COMMANDS_BY_FLAG[flag]["label"]
    display = (name or label).strip()
    if not _NAME_RE.match(display):
        return False, "", "Name may use letters, numbers, spaces, _ and - only."

    full_name = f"\\Rocky\\{display}"
    tr = rocky_command_string(flag, dry_run)

    # /it = run only when the logged-on user is present. The Rocky laptop stays
    # logged in as the rocky profile, so this matches reality and means no
    # password is stored (which is what lets non-elevated /change succeed).
    args = ["/create", "/tn", full_name, "/tr", tr, "/sc", frequency, "/it", "/f"]
    if needs_time:
        args += ["/st", time_str]
    if frequency in {"HOURLY", "MINUTE"} and interval:
        args += ["/mo", str(int(interval))]
    # No "/rl HIGHEST" on purpose: it forces the create to require an elevated
    # (admin) process. Rocky's jobs only need the logged-on rocky user's normal
    # rights (OneDrive + token cache live in that profile), so a default run
    # level lets the dashboard create/edit/delete tasks without running as
    # administrator. Tasks created this way are also modifiable non-elevated.

    ok, msg = _run_schtasks(args)
    return ok, full_name, msg


def update_task_time(task_name: str, time_str: str) -> tuple[bool, str]:
    """Change the start time of an existing scheduled task."""
    if not _TIME_RE.match(time_str or ""):
        return False, "Time must be HH:MM (24-hour)."
    return _run_schtasks(["/change", "/tn", task_name, "/st", time_str])


def delete_task(task_name: str) -> tuple[bool, str]:
    """Delete a scheduled task. Only Rocky/Maple tasks are deletable."""
    lower = task_name.lower()
    if "rocky" not in lower and "maple" not in lower:
        return False, "Refusing to delete a non-Rocky task."
    return _run_schtasks(["/delete", "/tn", task_name, "/f"])


def setup_recommended() -> dict:
    """
    Create the standard daily Rocky schedule in one shot.

    Only commands flagged ``recommended`` (those with a documented run time)
    are installed, each at its documented time/frequency. A command whose task
    name already exists is skipped, so this is safe to re-run.
    """
    existing = {t["name"].lower() for t in _query_schtasks()}
    created, skipped, failed = [], [], []

    for cmd in ROCKY_COMMANDS:
        if not cmd.get("recommended"):
            continue
        full_name = f"\\Rocky\\{cmd['label']}"
        if full_name.lower() in existing:
            skipped.append(cmd["label"])
            continue
        ok, _name, msg = create_task(
            flag=cmd["flag"],
            time_str=cmd.get("sched_time", ""),
            frequency=cmd.get("sched_freq", "DAILY"),
            interval=cmd.get("sched_interval"),
        )
        (created if ok else failed).append(
            cmd["label"] if ok else f"{cmd['label']} ({msg})"
        )

    return {"created": created, "skipped": skipped, "failed": failed}


# ===================================================================
# Flask routes
# ===================================================================

@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/stream")
def api_stream():
    """SSE endpoint — pushes new log lines in real time (rocky + Maple)."""
    return Response(
        stream_logs_merged(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/log-history")
def api_log_history():
    """Return the last 500 log lines, both logs merged (initial page load)."""
    return jsonify({"lines": merged_log_history(500)})


@app.route("/api/status")
def api_status():
    """Snapshot of Rocky's current state — polled every few seconds by the UI."""
    log_stat = LOG_PATH.stat() if LOG_PATH.exists() else None
    force = request.args.get("fresh") == "1"
    tasks = _query_schtasks(force=force)
    return jsonify({
        "dormant": get_dormant_status(),
        "running": get_running_commands(),
        "tasks": tasks,
        "run_info": command_run_info(tasks),
        "letterstream": letterstream_summary(),
        "log_size": log_stat.st_size if log_stat else 0,
        "log_modified": log_stat.st_mtime if log_stat else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/dormant", methods=["POST"])
def api_dormant_toggle():
    return jsonify(toggle_dormant())


@app.route("/api/tasks/toggle", methods=["POST"])
def api_task_toggle():
    data = request.get_json(silent=True) or {}
    task_name = data.get("name")
    enable = data.get("enable", True)
    if not task_name:
        return jsonify({"error": "Missing task name"}), 400
    ok = toggle_task(task_name, enable)
    return jsonify({"success": ok, "name": task_name, "enabled": enable})


@app.route("/api/commands")
def api_commands():
    """The allowlist of Rocky commands the dashboard can run/schedule."""
    return jsonify({"commands": ROCKY_COMMANDS, "running": get_running_commands()})


@app.route("/api/run", methods=["POST"])
def api_run():
    """Launch a Rocky command on demand."""
    data = request.get_json(silent=True) or {}
    flag = data.get("flag")
    dry_run = bool(data.get("dry_run", False))
    if not flag:
        return jsonify({"success": False, "error": "Missing command flag"}), 400
    result = launch_command(flag, dry_run)
    code = 200 if result.get("success") else 409
    return jsonify(result), code


# A USPS certified tracking number (20+ digits) or a LetterStream doc id.
# The strict shape is what lets us hand it to the subprocess safely.
_LS_REF_RE = re.compile(r"^[A-Za-z0-9._-]{3,64}$")


@app.route("/api/letterstream/fetch", methods=["POST"])
def api_letterstream_fetch():
    """
    Pull ONE mailing's proof from LetterStream by tracking number (or doc
    id) and run it through the affidavit pipeline — the day-to-day
    discovery path, since the API can't list website-submitted jobs.
    Runs `rocky --letterstream --fetch <ref>` under the letterstream lock.
    """
    data = request.get_json(silent=True) or {}
    ref = re.sub(r"\s+", "", str(data.get("ref") or ""))
    if not _LS_REF_RE.match(ref):
        return jsonify({
            "success": False,
            "error": "Enter the USPS certified tracking number (or "
                     "LetterStream doc id) — letters, digits, dots and "
                     "dashes only.",
        }), 400
    result = launch_command("letterstream", extra_args=["--fetch", ref])
    code = 200 if result.get("success") else 409
    return jsonify(result), code


@app.route("/api/schedule/create", methods=["POST"])
def api_schedule_create():
    """Create a new scheduled task for a Rocky command."""
    data = request.get_json(silent=True) or {}
    ok, full_name, msg = create_task(
        flag=data.get("flag", ""),
        time_str=data.get("time", ""),
        frequency=data.get("frequency", "DAILY"),
        interval=data.get("interval"),
        dry_run=bool(data.get("dry_run", False)),
        name=data.get("name"),
    )
    code = 200 if ok else 400
    return jsonify({"success": ok, "name": full_name, "message": msg}), code


@app.route("/api/schedule/update-time", methods=["POST"])
def api_schedule_update_time():
    """Change the start time of an existing scheduled task."""
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    if not name:
        return jsonify({"success": False, "error": "Missing task name"}), 400
    ok, msg = update_task_time(name, data.get("time", ""))
    code = 200 if ok else 400
    return jsonify({"success": ok, "name": name, "message": msg}), code


@app.route("/api/schedule/delete", methods=["POST"])
def api_schedule_delete():
    """Delete a scheduled task (Rocky tasks only)."""
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    if not name:
        return jsonify({"success": False, "error": "Missing task name"}), 400
    ok, msg = delete_task(name)
    code = 200 if ok else 400
    return jsonify({"success": ok, "name": name, "message": msg}), code


@app.route("/api/schedule/setup-recommended", methods=["POST"])
def api_schedule_setup_recommended():
    """Create the standard daily Rocky schedule (idempotent)."""
    result = setup_recommended()
    result["success"] = not result["failed"]
    return jsonify(result)


# ===================================================================
# Entry point
# ===================================================================

def main():
    import argparse

    # When stdout is redirected (Task Scheduler, nohup) the console codepage
    # may be cp1252, which can't encode the characters below. Force UTF-8 so
    # startup never dies on an encode error.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Rocky Dashboard")
    parser.add_argument(
        "--port", type=int, default=5001, help="Port to listen on (default 5001)"
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind address (default 0.0.0.0 — all interfaces)",
    )
    args = parser.parse_args()

    print(f"Rocky Dashboard → http://localhost:{args.port}")
    print(f"  Watching log:  {LOG_PATH}")
    print(f"  State dir:     {STATE_DIR}")
    print()

    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
