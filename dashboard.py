"""
Rocky Dashboard — local web UI for monitoring and managing Rocky.

Start:
    python dashboard.py                 (dev)
    python dashboard.py --port 8080     (custom port)
    dashboard.exe                       (production)

Opens a web server at http://localhost:5001 with:
- Live log viewer (tails rocky.log via Server-Sent Events)
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
#   sched_time     "HH:MM" default start time (None = no sensible default)
#   sched_freq     DAILY | MINUTE | ... (default DAILY when a time is set)
#   sched_interval minutes between runs, for MINUTE frequency
#   recommended    include in the one-click "recommended daily schedule"
#                  (True only for commands with a *documented* run time)
# Documented times come from rocky.py's header docstring + BUILD_REFERENCE.
# pma-activity / pma-knowledge / email-brain run "once daily" with no documented
# time, so they carry a *suggested* time for the quick-schedule button but are
# left out of the bulk auto-setup.
ROCKY_COMMANDS = [
    {"flag": "daily-cases",   "label": "Daily Cases",     "group": "Cases", "dry_run": False, "desc": "Fetch + summarize today's case emails",       "sched_time": "16:00", "recommended": True},
    {"flag": "daily-run",     "label": "Daily Run",       "group": "Cases", "dry_run": False, "desc": "Run per-case folder skills",                  "sched_time": "16:30", "recommended": True},
    {"flag": "daily-digest",  "label": "Daily Digest",    "group": "Cases", "dry_run": False, "desc": "Generate the daily case digest",              "sched_time": "17:00", "recommended": True},
    {"flag": "steve-todo",    "label": "Steve To-Do",     "group": "Inbox", "dry_run": False, "desc": "Steve's daily to-do list from inbox",         "sched_time": "07:30", "recommended": True},
    {"flag": "ella-digest",   "label": "Ella Digest",     "group": "Inbox", "dry_run": False, "desc": "Ella's daily case digest from inbox",         "sched_time": "17:00", "recommended": True},
    {"flag": "pending-llt",   "label": "Pending LLT",     "group": "Inbox", "dry_run": True,  "desc": "Draft LLT status emails by property"},
    {"flag": "pma-activity",  "label": "PMA Activity",    "group": "PMA",   "dry_run": True,  "desc": "Export PMA emails to JSONL for Maple",        "sched_time": "18:00"},
    {"flag": "pma-poll",      "label": "PMA Poll",        "group": "PMA",   "dry_run": True,  "desc": "Classify pmateam + update HubSpot",           "sched_time": "09:00", "sched_freq": "MINUTE", "sched_interval": 15, "recommended": True},
    {"flag": "pma-digest",    "label": "PMA Digest",      "group": "PMA",   "dry_run": True,  "desc": "Email PMA unmatched digest",                  "sched_time": "08:00", "recommended": True},
    {"flag": "pma-knowledge", "label": "PMA Knowledge",   "group": "PMA",   "dry_run": True,  "desc": "Synthesize PMA negotiation knowledge",        "sched_time": "18:30"},
    {"flag": "pma-test",      "label": "PMA Test",        "group": "PMA",   "dry_run": False, "desc": "Diagnose pmateam access + matcher"},
    {"flag": "pma-arm",       "label": "PMA Arm",         "group": "PMA",   "dry_run": False, "danger": True, "desc": "Turn ON HubSpot writes"},
    {"flag": "pma-sleep",     "label": "PMA Sleep",       "group": "PMA",   "dry_run": False, "desc": "Turn OFF HubSpot writes"},
    {"flag": "maple-digest",  "label": "Maple Digest",    "group": "Other", "dry_run": True,  "desc": "Email the Maple activity digest",             "sched_time": "16:30", "recommended": True},
    {"flag": "email-brain",   "label": "Email Brain",     "group": "Other", "dry_run": False, "desc": "Build sent-mail corpus + index",              "sched_time": "02:00"},
    {"flag": "monitor-remy",  "label": "Monitor Remy",    "group": "Other", "dry_run": False, "desc": "Poll inbox for Remy requests (long-running)"},
]

_COMMANDS_BY_FLAG = {c["flag"]: c for c in ROCKY_COMMANDS}


def rocky_target() -> list[str]:
    """
    Return the argv prefix that invokes Rocky, minus the command flag.

    Frozen build → the sibling rocky.exe. Dev → ``python rocky.py``.
    """
    if getattr(sys, "frozen", False):
        return [str(PROGRAM_DIR / "rocky.exe")]
    return [sys.executable, str(PROGRAM_DIR / "rocky.py")]


def rocky_command_string(flag: str, dry_run: bool = False) -> str:
    """
    Build the quoted command line for Task Scheduler's ``/tr`` argument.
    """
    parts = rocky_target()
    quoted = " ".join(f'"{p}"' if " " in p else p for p in parts)
    line = f"{quoted} --{flag}"
    if dry_run:
        line += " --dry-run"
    return line


def launch_command(flag: str, dry_run: bool = False) -> dict:
    """
    Launch a Rocky command as a detached background process.

    Rocky writes its own progress to rocky.log, so the live log viewer is the
    feedback channel — we don't capture stdout here. Rocky's own per-command
    lock prevents a duplicate run if one is already in flight.
    """
    if flag not in _COMMANDS_BY_FLAG:
        return {"success": False, "error": f"Unknown command: {flag}"}

    if flag in get_running_commands():
        return {"success": False, "error": f"{flag} is already running."}

    argv = rocky_target() + [f"--{flag}"]
    if dry_run and _COMMANDS_BY_FLAG[flag].get("dry_run"):
        argv.append("--dry-run")

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
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


def stream_log(path: Path):
    """Generator that yields new log lines as SSE ``data:`` frames."""
    # Wait for the file to appear.
    while not path.exists():
        yield "data: \n\n"
        time.sleep(2)

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(0, 2)  # jump to end
        while True:
            line = f.readline()
            if line:
                yield f"data: {json.dumps(line.rstrip())}\n\n"
            else:
                time.sleep(0.5)
                yield ": keepalive\n\n"


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

def _query_schtasks() -> list[dict]:
    """
    Query Windows Task Scheduler for Rocky-related tasks.

    Tries a ``\\Rocky\\`` folder first; falls back to a full query filtered
    by name.
    """
    tasks: list[dict] = []
    csv_text = ""

    try:
        # Attempt 1: tasks inside a \\Rocky\\ folder.
        r = subprocess.run(
            ["schtasks", "/query", "/fo", "CSV", "/v", "/tn", "\\Rocky\\"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            csv_text = r.stdout
    except Exception:
        pass

    if not csv_text:
        try:
            # Attempt 2: all tasks, filter by name.
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
        return tasks

    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        for row in reader:
            name = row.get("TaskName", "")
            if "rocky" not in name.lower():
                continue
            tasks.append({
                "name": name,
                "status": row.get("Status", "Unknown"),
                "next_run": row.get("Next Run Time", "N/A"),
                "last_run": row.get("Last Run Time", "N/A"),
                "last_result": row.get("Last Result", "N/A"),
                "enabled": row.get("Scheduled Task State", "").strip().lower()
                    == "enabled",
            })
    except Exception as exc:
        log.warning("Failed to parse schtasks output: %s", exc)

    return tasks


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
    """Delete a scheduled task. Only Rocky tasks are deletable."""
    if "rocky" not in task_name.lower():
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
    """SSE endpoint — pushes new log lines in real time."""
    return Response(
        stream_log(LOG_PATH),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/log-history")
def api_log_history():
    """Return the last 500 log lines (initial page load)."""
    return jsonify({"lines": tail_log(LOG_PATH, 500)})


@app.route("/api/status")
def api_status():
    """Snapshot of Rocky's current state — polled every few seconds by the UI."""
    log_stat = LOG_PATH.stat() if LOG_PATH.exists() else None
    return jsonify({
        "dormant": get_dormant_status(),
        "running": get_running_commands(),
        "tasks": _query_schtasks(),
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
