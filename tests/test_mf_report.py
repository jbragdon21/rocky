"""MF Daily Report: sections over a synthetic ledger, the window, and
the never-raise / advance-only-on-send contract. Graph is mocked.

    python -m pytest tests/test_mf_report.py -q
"""

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mf_brain  # noqa: E402
import mf_report  # noqa: E402

TODAY = date(2026, 10, 19)          # a Monday
NOW = datetime(2026, 10, 19, 11, 0, tzinfo=timezone.utc)


def _d(dt: date) -> str:
    return f"{dt.month}/{dt.day}/{dt.year % 100:02d}"


def _ledger():
    cases = {
        "MF-00001": {"id": "MF-00001", "label": "Stratos — Boykins, Gary (1001)",
                     "property": "Stratos", "matter_type": "eviction",
                     "created": "2026-09-13T12:00:00+00:00",
                     "first_seen_by": "llt_sheet"},
        "MF-00002": {"id": "MF-00002", "label": "Alula — Diaz, Ana (204)",
                     "property": "Alula", "matter_type": "eviction",
                     "created": "2026-10-18T13:00:00+00:00",
                     "first_seen_by": "llt_sheet"},
        "MF-00003": {"id": "MF-00003", "label": "Smith, Jo (OHR)",
                     "matter_type": "agency_complaint",
                     "created": "2026-09-13T12:00:00+00:00",
                     "first_seen_by": "folders"},
        "MF-00004": {"id": "MF-00004", "label": "Claim, Big",
                     "matter_type": "insured_litigation",
                     "created": "2026-09-13T12:00:00+00:00"},
    }
    lines = {
        # hearing in 3 days
        "MF-00001.1": {"line_id": "MF-00001.1", "case": "MF-00001",
                       "status": "Rent filed",
                       "next_steps": f"Initial Hearing: {_d(TODAY + timedelta(3))} at 9:00am",
                       "ripe_date": "8/1/26"},
        # hearing passed 2 days ago, sheet not updated
        "MF-00001.2": {"line_id": "MF-00001.2", "case": "MF-00001",
                       "status": "Smoking filed",
                       "next_steps": f"Trial {_d(TODAY - timedelta(2))}",
                       "ripe_date": ""},
        # ripe yesterday, unfiled
        "MF-00002.1": {"line_id": "MF-00002.1", "case": "MF-00002",
                       "status": "Rent sent", "next_steps": "",
                       "ripe_date": _d(TODAY - timedelta(1))},
        # old backlog, unfiled
        "MF-00002.2": {"line_id": "MF-00002.2", "case": "MF-00002",
                       "status": "Rent sent", "next_steps": "",
                       "ripe_date": "6/1/26"},
        # closed lines are ignored
        "MF-00002.3": {"line_id": "MF-00002.3", "case": "MF-00002",
                       "status": "Rent sent", "next_steps": "",
                       "ripe_date": _d(TODAY), "closed": "2026-10-01"},
    }
    obs = [
        {"source": "inbox", "case": "MF-00003",
         "ts": "2026-10-18T12:00:00+00:00",
         "evidence": {"messages": 3, "summary": "OHR set a mediation."}},
        {"source": "inbox", "case": "MF-00004",          # Smartsheet's
         "ts": "2026-10-18T12:00:00+00:00", "evidence": {"messages": 1}},
        {"source": "inbox", "case": "MF-00002",          # on the sheet
         "ts": "2026-10-18T12:00:00+00:00", "evidence": {"messages": 1}},
        {"source": "inbox", "case": "MF-00003",          # before window
         "ts": "2026-10-01T12:00:00+00:00", "evidence": {"messages": 9}},
    ]
    return cases, lines, obs


def test_court_calendar_and_missing_outcomes():
    cases, lines, _ = _ledger()
    cal = mf_report.court_calendar(lines, cases, TODAY)
    assert [r["line"] for r in cal] == ["MF-00001.1"]
    miss = mf_report.outcomes_missing(lines, cases, TODAY)
    assert [(r["line"], r["days"]) for r in miss] == [("MF-00001.2", 2)]


def test_ripe_unfiled_buckets():
    cases, lines, _ = _ledger()
    r = mf_report.ripe_unfiled(lines, cases, TODAY)
    assert [x["line"] for x in r["newly"]] == ["MF-00002.1"]
    assert r["soon"] == []
    # the filed line (MF-00001.1) is excluded; the closed one too
    assert [b["name"] for b in r["older"].values()] == ["Alula"]
    assert r["older"]["alula"]["n"] == 1


def test_new_matters_and_untracked_activity():
    cases, lines, obs = _ledger()
    since = NOW - timedelta(days=2)
    assert [c["id"] for c in mf_report.new_matters(cases, since)] == ["MF-00002"]
    act = mf_report.untracked_activity(cases, lines, obs, since)
    assert [(a["case"], a["messages"], a["summary"]) for a in act] == [
        ("MF-00003", 3, "OHR set a mediation.")]


def test_window_runs_from_last_report_with_a_cap():
    assert mf_report.report_window({}, NOW) == NOW - timedelta(days=1)
    fri = NOW - timedelta(days=3)
    assert mf_report.report_window({"last_report": fri.isoformat()}, NOW) == fri
    old = NOW - timedelta(days=30)
    assert (mf_report.report_window({"last_report": old.isoformat()}, NOW)
            == NOW - timedelta(days=mf_report.MAX_WINDOW_DAYS))


def _write_ledger(tmp_path):
    cases, lines, obs = _ledger()
    root = tmp_path / "Case Brain"
    root.mkdir()
    paths = mf_brain.get_paths({"mf_tracker_root": str(tmp_path)}, tmp_path)
    mf_brain.append_jsonl(paths["cases"], list(cases.values()))
    mf_brain.append_jsonl(paths["lines"], list(lines.values()))
    mf_brain.append_jsonl(paths["observations"], obs)
    mf_brain.save_state(paths, {"last_scan": NOW.isoformat()})
    return paths


def test_run_report_saves_sends_and_advances(tmp_path, monkeypatch):
    paths = _write_ledger(tmp_path)
    config = {"mf_tracker_root": str(tmp_path),
              "mf_report_recipients": ["staff@gallagherllp.com"]}
    sent = []
    import outbound
    monkeypatch.setattr(outbound, "send_mail_guarded",
                        lambda tok, frm, to, subj, html, **kw:
                        sent.append((to, subj, html)) or {"sent": True})
    rep = mf_report.run_report(config, paths, tmp_path, token="tok", now=NOW)
    assert rep["sent"]
    out = tmp_path / "Daily Reports" / "2026-10-19.html"
    assert out.exists() and "Boykins" in out.read_text("utf-8")
    to, subj, html = sent[0]
    assert to == ["staff@gallagherllp.com"]
    assert subj == "Multifamily Daily Report — October 19, 2026"
    assert "Outcome not on the sheet" in html
    assert "LLT Watch hasn&#x27;t recorded anything yet" in html
    assert "Out of date" not in html
    state = json.loads(paths["state"].read_text("utf-8"))
    assert state["last_report"] == NOW.isoformat()
    assert state["last_scan"] == NOW.isoformat()       # scan state kept


def test_failed_send_does_not_advance_window(tmp_path, monkeypatch):
    paths = _write_ledger(tmp_path)
    import outbound
    monkeypatch.setattr(outbound, "send_mail_guarded",
                        lambda *a, **kw: {"sent": False, "reason": "x"})
    rep = mf_report.run_report({"mf_tracker_root": str(tmp_path)}, paths,
                               tmp_path, token="tok", now=NOW)
    assert rep["sent"] is False
    assert "last_report" not in json.loads(paths["state"].read_text("utf-8"))


def test_weekend_skip_and_force(tmp_path, monkeypatch):
    paths = _write_ledger(tmp_path)
    sat = datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc)
    assert mf_report.run_report({}, paths, tmp_path, token=None,
                                now=sat) == {"skipped": "weekend"}
    rep = mf_report.run_report({"mf_tracker_root": str(tmp_path)}, paths,
                               tmp_path, token=None, now=sat, force=True)
    assert rep.get("path") and rep["sent"] is False


def test_build_failure_never_raises(tmp_path, monkeypatch):
    paths = _write_ledger(tmp_path)
    monkeypatch.setattr(mf_report, "build_report",
                        lambda *a, **kw: 1 / 0)
    assert "error" in mf_report.run_report({}, paths, tmp_path, token="t",
                                           now=NOW)


def test_tracker_root_falls_back_beside_frozen_exe(tmp_path, monkeypatch):
    import llt_watch
    beside = tmp_path / "Multifamily Tracker and Dashboard"
    beside.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "rocky.exe"))
    # unset, or set to a path that doesn't exist on this machine
    assert llt_watch.tracker_root({}) == beside
    assert llt_watch.tracker_root(
        {"mf_tracker_root": r"C:\nope\Multifamily Tracker"}) == beside
    # an explicit key that resolves still wins
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert llt_watch.tracker_root({"mf_tracker_root": str(other)}) == other


def test_every_court_date_in_a_cell_and_recorded_outcomes():
    cases = {"MF-1": {"id": "MF-1", "label": "A"}}
    lines = {
        # past mediation, later Bell hearing: on the calendar, not missing
        "MF-1.1": {"line_id": "MF-1.1", "case": "MF-1", "status": "Rent",
                   "next_steps": f"Mediation: {_d(TODAY - timedelta(5))} at "
                                 f"1:30 pm; Bell Hearing: "
                                 f"{_d(TODAY + timedelta(4))} at 11:00 am"},
        # past trial with the outcome written: not missing
        "MF-1.2": {"line_id": "MF-1.2", "case": "MF-1", "status": "Lease",
                   "next_steps": f"Trial: {_d(TODAY - timedelta(3))} at "
                                 f"8:45am - Dismissed"},
        # past eviction, nothing written: missing
        "MF-1.3": {"line_id": "MF-1.3", "case": "MF-1", "status": "Rent",
                   "next_steps": f"EVICTION: {_d(TODAY - timedelta(1))} (26-1)"},
    }
    cal = mf_report.court_calendar(lines, cases, TODAY)
    assert [(r["line"], r["event"]) for r in cal] == [("MF-1.1", "Hearing")]
    miss = mf_report.outcomes_missing(lines, cases, TODAY)
    assert [r["line"] for r in miss] == ["MF-1.3"]
