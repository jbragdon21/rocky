"""Monitor self-relaunch: a new rocky.exe landing beside the running one is
detected only once its mtime settles, and a relaunched monitor waits for
its predecessor's instance lock instead of exiting.

    python -m pytest tests/test_monitor_relaunch.py -q
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import rocky  # noqa: E402


def test_unchanged_image_is_not_a_new_build(tmp_path):
    exe = tmp_path / "rocky.exe"
    exe.write_bytes(b"old")
    started = rocky._image_mtime(exe)
    assert not rocky._new_build_landed(exe, started, settle=0)


def test_changed_image_is_a_new_build(tmp_path):
    exe = tmp_path / "rocky.exe"
    exe.write_bytes(b"old")
    started = rocky._image_mtime(exe)
    os.utime(exe, (started + 60, started + 60))
    assert rocky._new_build_landed(exe, started, settle=0)


def test_missing_image_mid_rename_is_not_a_new_build(tmp_path):
    exe = tmp_path / "rocky.exe"
    exe.write_bytes(b"old")
    started = rocky._image_mtime(exe)
    exe.unlink()
    assert not rocky._new_build_landed(exe, started, settle=0)


def test_image_still_changing_waits(tmp_path, monkeypatch):
    exe = tmp_path / "rocky.exe"
    exe.write_bytes(b"old")
    started = rocky._image_mtime(exe)
    os.utime(exe, (started + 60, started + 60))

    def sync_still_writing(_seconds):
        os.utime(exe, (started + 90, started + 90))
    monkeypatch.setattr(rocky.time, "sleep", sync_still_writing)
    assert not rocky._new_build_landed(exe, started, settle=1)


# A child that holds the monitor lock for `hold` seconds, then exits.
HOLDER = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, {root!r})
    import rocky
    rocky.STATE_DIR = __import__("pathlib").Path({state!r})
    fh = rocky.acquire_instance_lock("monitor")
    print("held", flush=True)
    time.sleep({hold})
""")


def _holder(tmp_path, hold, relaunch=False):
    env = dict(os.environ)
    env.pop(rocky.MONITOR_RELAUNCH_ENV, None)
    if relaunch:
        env[rocky.MONITOR_RELAUNCH_ENV] = "1"
    code = HOLDER.format(root=str(ROOT), state=str(tmp_path), hold=hold)
    return subprocess.Popen([sys.executable, "-c", code], env=env,
                            stdout=subprocess.PIPE, text=True)


def test_relaunched_monitor_waits_for_lock(tmp_path):
    old = _holder(tmp_path, hold=4)
    assert old.stdout.readline().strip() == "held"
    new = _holder(tmp_path, hold=0, relaunch=True)
    out, _ = new.communicate(timeout=60)
    old.wait(timeout=30)
    assert "held" in out


def test_ordinary_second_monitor_still_bows_out(tmp_path):
    old = _holder(tmp_path, hold=6)
    assert old.stdout.readline().strip() == "held"
    new = _holder(tmp_path, hold=0)
    out, _ = new.communicate(timeout=30)
    old.wait(timeout=30)
    assert "already running" in out and "held" not in out
