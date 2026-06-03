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


# ===================================================================
# Entry point
# ===================================================================

def main():
    import argparse

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
