"""
Rocky wrapper — keeps rocky.py running and pulls code updates from GitHub.

Loop forever:
  1. Sleep CHECK_INTERVAL seconds
  2. If Rocky has crashed, restart her
  3. Run `git pull`. If anything changed, kill Rocky and start fresh.

Run this on the Rocky laptop (NOT on James's primary laptop). Launched at boot
via Windows Task Scheduler entry "Rocky Wrapper".

Logs to wrapper.log (gitignored). Operational events only — Rocky's own
classification log goes to classifications.jsonl as before.
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

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable                  # use the Python that's running this wrapper
ROCKY_SCRIPT = ROOT / "rocky.py"
WRAPPER_LOG = ROOT / "wrapper.log"

# Sibling repos to git-pull each cycle. Rocky's own repo is always pulled
# (ROOT). Add additional dirs here that should track upstream alongside Rocky.
# A change in ANY tracked repo triggers a Rocky restart so the new code is
# loaded in-process.
EXTRA_REPOS = [
    Path("C:/Remy"),
]

# How often to check for code updates / liveness. 30 minutes — code pushes
# are infrequent enough that polling more often is overkill.
CHECK_INTERVAL_SECONDS = 1800

# Max time to wait for Rocky to shut down gracefully before force-killing.
SHUTDOWN_GRACE_SECONDS = 20

# Run `git pull` with this timeout. Network hiccups should not hang the wrapper.
GIT_PULL_TIMEOUT_SECONDS = 60


# =============================================================================
# Logging
# =============================================================================

def log(msg: str) -> None:
    """Append a timestamped line to wrapper.log and stdout."""
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}Z] {msg}"
    print(line, flush=True)
    try:
        with open(WRAPPER_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        # Logging must never crash the wrapper.
        pass


# =============================================================================
# Git operations
# =============================================================================

def _git_pull_one(repo_path: Path) -> bool:
    """Pull a single repo. Returns True if it actually changed, False otherwise."""
    if not repo_path.exists():
        log(f"Repo path does not exist, skipping: {repo_path}")
        return False
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=GIT_PULL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        log(f"git pull timed out for {repo_path}. Skipping this cycle.")
        return False
    except FileNotFoundError:
        log("git command not found. Is Git for Windows installed and on PATH?")
        return False
    except Exception as e:
        log(f"git pull failed unexpectedly for {repo_path}: {e!r}")
        return False

    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        log(f"git pull on {repo_path} returned non-zero ({result.returncode}). Output:\n{output}")
        return False
    if "Already up to date" in output or "Already up-to-date" in output:
        return False
    log(f"git pull on {repo_path} pulled changes:\n{output}")
    return True


def git_pull_and_check_for_changes() -> bool:
    """
    Run `git pull` against every tracked repo (Rocky + EXTRA_REPOS).
    Return True if ANY repo received new commits — a change in Rocky OR Remy
    (or any future sibling) triggers a Rocky restart so the new code loads.

    Returns False on errors — transient git/network problems should never
    trigger a restart loop.
    """
    repos = [ROOT, *EXTRA_REPOS]
    any_changed = False
    for repo in repos:
        if _git_pull_one(repo):
            any_changed = True
    return any_changed


# =============================================================================
# Rocky process lifecycle
# =============================================================================

def start_rocky() -> subprocess.Popen:
    """Start rocky.py as a child process. Returns the Popen handle."""
    log(f"Starting Rocky: {PYTHON} {ROCKY_SCRIPT}")
    # On Windows, CREATE_NEW_PROCESS_GROUP lets us send Ctrl+Break for a graceful
    # shutdown. On non-Windows this flag doesn't exist; ignore it.
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    return subprocess.Popen(
        [PYTHON, str(ROCKY_SCRIPT)],
        cwd=str(ROOT),
        creationflags=creationflags,
    )


def stop_rocky(proc: subprocess.Popen) -> None:
    """Stop a running Rocky process. Graceful first, force-kill if needed."""
    if proc.poll() is not None:
        return  # already exited

    log("Stopping Rocky...")
    try:
        if os.name == "nt":
            # Send Ctrl+Break to the new process group so Rocky's KeyboardInterrupt
            # handler runs and can finish the current poll cycle cleanly.
            proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
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
    log(f"Rocky wrapper starting up. Repo root: {ROOT}")
    log(f"Check interval: {CHECK_INTERVAL_SECONDS}s")
    log("=" * 60)

    if not ROCKY_SCRIPT.exists():
        log(f"FATAL: rocky.py not found at {ROCKY_SCRIPT}. Aborting.")
        sys.exit(1)

    rocky = start_rocky()

    while True:
        try:
            time.sleep(CHECK_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            log("Wrapper received KeyboardInterrupt. Shutting down Rocky and exiting.")
            stop_rocky(rocky)
            sys.exit(0)

        # Liveness check first — cheaper than a git pull.
        if rocky.poll() is not None:
            log(f"Rocky exited unexpectedly with code {rocky.returncode}. Restarting.")
            rocky = start_rocky()
            continue

        # Check for code updates.
        try:
            changed = git_pull_and_check_for_changes()
        except Exception as e:
            log(f"Unexpected error in git pull check: {e!r}. Continuing.")
            continue

        if changed:
            log("Code updated. Restarting Rocky.")
            stop_rocky(rocky)
            rocky = start_rocky()


if __name__ == "__main__":
    main()
