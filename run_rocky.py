"""
Rocky wrapper — keeps rocky.exe running on the Rocky laptop.

Simple crash-recovery loop: if Rocky exits unexpectedly, restart her after
a short delay. No git, no code updates — OneDrive handles sync.

Run this on the Rocky laptop (NOT on James's primary laptop). Launched at
boot via Windows Task Scheduler entry "Rocky Wrapper".

Logs to wrapper.log (in DATA_DIR, not alongside the .exe on OneDrive).
"""

import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# =============================================================================
# Configuration
# =============================================================================

# The .exe lives on OneDrive. Runtime data is local.
if getattr(sys, "frozen", False):
    PROGRAM_DIR = Path(sys.executable).parent
    DATA_DIR = Path(r"C:\Rocky")
else:
    PROGRAM_DIR = Path(__file__).resolve().parent
    DATA_DIR = PROGRAM_DIR

ROCKY_EXE = PROGRAM_DIR / "rocky.exe"
ROCKY_SCRIPT = PROGRAM_DIR / "rocky.py"
WRAPPER_LOG = DATA_DIR / "wrapper.log"

# How long to wait before restarting after a crash.
RESTART_DELAY_SECONDS = 30

# Max time to wait for Rocky to shut down gracefully before force-killing.
SHUTDOWN_GRACE_SECONDS = 20


# =============================================================================
# Logging
# =============================================================================

def log(msg: str) -> None:
    """Append a timestamped line to wrapper.log and stdout."""
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}Z] {msg}"
    print(line, flush=True)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(WRAPPER_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# =============================================================================
# Rocky process lifecycle
# =============================================================================

def _rocky_command() -> list[str]:
    """Pick the right command: rocky.exe if it exists, else python rocky.py."""
    if ROCKY_EXE.exists():
        return [str(ROCKY_EXE)]
    if ROCKY_SCRIPT.exists():
        return [sys.executable, str(ROCKY_SCRIPT)]
    log(f"FATAL: neither {ROCKY_EXE} nor {ROCKY_SCRIPT} found.")
    sys.exit(1)


def start_rocky() -> subprocess.Popen:
    """Start Rocky as a child process."""
    cmd = _rocky_command()
    log(f"Starting Rocky: {' '.join(cmd)}")
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(cmd, cwd=str(PROGRAM_DIR), creationflags=creationflags)


def stop_rocky(proc: subprocess.Popen) -> None:
    """Stop a running Rocky process. Graceful first, force-kill if needed."""
    if proc.poll() is not None:
        return

    log("Stopping Rocky...")
    try:
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.terminate()
    except Exception as e:
        log(f"Could not signal Rocky cleanly ({e!r}); will force-kill.")

    try:
        proc.wait(timeout=SHUTDOWN_GRACE_SECONDS)
        log("Rocky stopped cleanly.")
    except subprocess.TimeoutExpired:
        log(f"Rocky did not stop within {SHUTDOWN_GRACE_SECONDS}s. Force-killing.")
        proc.kill()
        proc.wait()


# =============================================================================
# Main loop
# =============================================================================

def main() -> None:
    log("=" * 60)
    log(f"Rocky wrapper starting up.")
    log(f"Program dir: {PROGRAM_DIR}")
    log(f"Data dir:    {DATA_DIR}")
    log("=" * 60)

    rocky = start_rocky()

    while True:
        try:
            rocky.wait()
        except KeyboardInterrupt:
            log("Wrapper received KeyboardInterrupt. Shutting down.")
            stop_rocky(rocky)
            sys.exit(0)

        log(f"Rocky exited with code {rocky.returncode}. "
            f"Restarting in {RESTART_DELAY_SECONDS}s.")
        time.sleep(RESTART_DELAY_SECONDS)
        rocky = start_rocky()


if __name__ == "__main__":
    main()
