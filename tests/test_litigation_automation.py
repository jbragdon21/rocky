"""Litigation Updater running unattended from the monitor: the background
launch, follow-only-when-active, the 1-business-day re-up, and the intake
retry cap. Graph, Teams, and Claude are mocked.

    python -m pytest tests/test_litigation_automation.py -q
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import litigation_updater as lu  # noqa: E402
import rocky  # noqa: E402


def _local(y, m, d, h):
    """An aware datetime at local wall-clock hour h (what the window reads)."""
    return datetime(y, m, d, h).astimezone()


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# --- Re-up timing ---------------------------------------------------------

def test_reup_hours_default_supersedes_old_seven_days():
    assert lu._reup_hours({}) == 24
    assert lu._reup_hours({"litigation_reup_days": 7}) == 24
    assert lu._reup_hours({"litigation_reup_days": 0}) == 0
    assert lu._reup_hours({"litigation_reup_hours": 4,
                           "litigation_reup_days": 0}) == 4


def test_reup_after_a_day_inside_working_hours():
    monday_10 = _local(2026, 10, 19, 10)
    ask = {"proposed_at": _iso(monday_10 - timedelta(hours=25))}
    assert lu._reup_due({}, ask, now=monday_10)
    ask = {"proposed_at": _iso(monday_10 - timedelta(hours=23))}
    assert not lu._reup_due({}, ask, now=monday_10)
    # A re-up resets the clock.
    ask = {"proposed_at": _iso(monday_10 - timedelta(days=5)),
           "reupped_at": _iso(monday_10 - timedelta(hours=2))}
    assert not lu._reup_due({}, ask, now=monday_10)


def test_reup_waits_for_weekday_daytime():
    stale = {"proposed_at": _iso(_local(2026, 10, 14, 10))}
    assert not lu._reup_due({}, stale, now=_local(2026, 10, 19, 3))   # 3 AM
    assert not lu._reup_due({}, stale, now=_local(2026, 10, 18, 11))  # Sunday
    assert not lu._reup_due({}, stale, now=_local(2026, 10, 19, 17))  # 5 PM
    assert lu._reup_due({}, stale, now=_local(2026, 10, 19, 9))
    assert lu._reup_due({"litigation_reup_window": [0, 24]}, stale,
                        now=_local(2026, 10, 19, 3))


def test_reup_off():
    stale = {"proposed_at": "2026-01-01T00:00:00Z"}
    assert not lu._reup_due({"litigation_reup_hours": 0}, stale,
                            now=_local(2026, 10, 19, 10))


# --- Follow only when the chat moved --------------------------------------

def test_chat_was_active():
    assert not lu.chat_was_active({"inbound": 0})
    assert not lu.chat_was_active({"error": "teams_not_enabled"})
    for key, val in (("inbound", 1), ("proposed", "L1"), ("decided", ["L1"]),
                     ("reupped", "L1")):
        assert lu.chat_was_active({key: val})


def test_follow_if_active_dispatch(monkeypatch, tmp_path):
    paths = {"state": tmp_path / "state.json"}
    lu.save_state(paths, {"asks": [{"status": "proposed"}]})
    followed = []
    chat = {"inbound": 0}
    monkeypatch.setattr(lu, "run_poll", lambda *a, **k: {"chat": chat})
    monkeypatch.setattr(lu, "follow_chat", lambda *a, **k: followed.append(1))
    monkeypatch.setattr(sys, "argv", ["rocky.py", "--litigation", "--poll",
                                      "--follow-if-active"])
    lu._dispatch({}, paths, dry_run=False)
    assert followed == []           # an ask waits, but nobody's there
    chat["inbound"] = 1
    lu._dispatch({}, paths, dry_run=False)
    assert followed == [1]          # James just replied: stay on the chat


# --- Intake retry cap -----------------------------------------------------

def _intake_env(monkeypatch, tmp_path, messages, classify):
    import vault
    paths = lu.get_paths({"litigation_root": str(tmp_path / "root")},
                         tmp_path / "data")
    lu.ensure_dirs(paths)
    monkeypatch.setattr(vault, "fetch_inbox_messages",
                        lambda *a, **k: list(messages))
    monkeypatch.setattr(lu, "_eligible_attachments", lambda *a, **k: [])
    monkeypatch.setattr(lu, "classify_notice", classify)
    monkeypatch.setattr(lu, "find_existing_entry", lambda *a, **k: None)
    return paths


def _notice(n):
    return {"id": f"g{n}", "internetMessageId": f"<m{n}@x>",
            "subject": f"Complaint {n}",
            "receivedDateTime": f"2026-10-18T1{n}:00:00Z",
            "from": {"emailAddress": {"address": "legalnotices@bozzuto.com"}},
            "body": {"content": "See attached."}}


def test_unreadable_mail_held_then_skipped_with_alert(monkeypatch, tmp_path):
    bad, good = _notice(1), _notice(2)
    calls = []

    def classify(client, config, paths, docs, ctx):
        calls.append(ctx)
        return None if "Complaint 1" in ctx else {
            "claim_name": "Roe", "entity": "BMC", "recommend_entry": True}
    paths = _intake_env(monkeypatch, tmp_path, [bad, good], classify)
    config = {"litigation_intake_max_attempts": 3}
    state = {}

    for attempt in (1, 2):
        counts = lu.intake_pass(None, "tok", None, config, paths, state, 7,
                                dry_run=True)
        assert counts["queued"] == 0           # held: good mail waits
        assert state["intake_failures"]["<m1@x>"] == attempt
        assert not state.get("chat_alerts")

    counts = lu.intake_pass(None, "tok", None, config, paths, state, 7,
                            dry_run=True)
    assert counts["queued"] == 1               # gave up on 1, 2 went through
    assert state["mail_cursor"] == good["receivedDateTime"]
    assert "<m1@x>" in state["intake_skipped"]
    assert "<m1@x>" not in state["intake_failures"]
    assert len(state["chat_alerts"]) == 1
    assert "Complaint 1" in state["chat_alerts"][0]

    # The skipped mail is re-read (cursor precision) but never retried.
    before = len(calls)
    state["mail_cursor"] = None
    lu.intake_pass(None, "tok", None, config, paths, state, 7, dry_run=True)
    assert not any("Complaint 1" in c for c in calls[before:])


def test_success_clears_failure_count(monkeypatch, tmp_path):
    results = iter([None, {"claim_name": "Roe", "entity": "BMC"}])
    paths = _intake_env(monkeypatch, tmp_path, [_notice(1)],
                        lambda *a: next(results))
    state = {}
    lu.intake_pass(None, "tok", None, {}, paths, state, 7, dry_run=True)
    assert state["intake_failures"] == {"<m1@x>": 1}
    lu.intake_pass(None, "tok", None, {}, paths, state, 7, dry_run=True)
    assert state["intake_failures"] == {}


# --- Monitor launches litigation in the background ------------------------

class _Proc:
    def __init__(self, code=None):
        self.code = code

    def poll(self):
        return self.code


def test_monitor_litigation_skips_while_running(monkeypatch):
    launched = []
    monkeypatch.setattr("subprocess.Popen",
                        lambda cmd, **k: launched.append((cmd, k)) or _Proc())
    running = _Proc(None)
    assert rocky._monitor_litigation(running) is running
    assert launched == []

    child = rocky._monitor_litigation(_Proc(0))
    assert isinstance(child, _Proc) and len(launched) == 1
    cmd, kwargs = launched[0]
    assert cmd[-3:] == ["--litigation", "--poll", "--follow-if-active"]
    assert kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"

    rocky._monitor_litigation(None)
    assert len(launched) == 2
