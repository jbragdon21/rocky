"""Kirsten Digest: case selection off the index column, folder matching
with shared RRIDs, housekeeping kept out of the prompt, rendering, and
the weekly schedule's /d day.

    python -m pytest tests/test_kirsten_digest.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard  # noqa: E402
import kirsten_digest as kd  # noqa: E402

COL = "Kirsten Digest?"


def _row(rrid, name, flag, status="Open"):
    return {"RRID#": rrid, "File Name": name, "Client": "Bozzuto Management Company",
            COL: flag, "Open/Closed": status}


def test_select_only_yes_and_open():
    cases = [
        _row("RRID-0003", "Eden, Artemus", "Yes"),
        _row("RRID-0004", "Ricks, Sharday", "no"),
        _row("RRID-0007", "Clowney, George", None),
        _row("RRID-0019", "Ghumman, Saira", " y "),
        _row("RRID-0005", "HRI", "yes", status="Closed"),
    ]
    got = [c["RRID#"] for c in kd.select_cases(cases)]
    assert got == ["RRID-0003", "RRID-0019"]


def test_select_restricts_to_named_rrids():
    cases = [_row("RRID-0003", "Eden", "yes"), _row("RRID-0019", "Ghumman", "yes")]
    assert [c["RRID#"] for c in kd.select_cases(cases, ["rrid-0019"])] == ["RRID-0019"]


def test_closed_sheet_row_overrides_open_row():
    cases = [_row("RRID-0003", "Eden", "yes"),
             _row("RRID-0003", "Eden", "yes", status="Closed")]
    assert kd.select_cases(cases) == []


def test_no_column_means_no_cases():
    assert kd.kirsten_column([{"RRID#": "RRID-0001"}]) is None
    assert kd.select_cases([{"RRID#": "RRID-0001", "File Name": "x"}]) == []


def test_find_folder_breaks_shared_rrid_ties_by_name(tmp_path):
    for name in ("Board, Bianca (RRID-0040)", "Murray, Sheena (RRID-0040)",
                 "Wall, Jesse (RRID-0040)", "Eden, Artemus (RRID-0003)",
                 "_Daily Digests"):
        (tmp_path / name).mkdir()
    assert kd.find_folder(tmp_path, {"RRID#": "RRID-0040",
                                     "File Name": "Wall, Jesse"}).name.startswith("Wall")
    # Index RRID off by one from the folder: fall back to the name.
    assert kd.find_folder(tmp_path, {"RRID#": "RRID-0041",
                                     "File Name": "Board, Bianca"}).name.startswith("Board")
    assert kd.find_folder(tmp_path, {"RRID#": "RRID-0003",
                                     "File Name": "Eden"}).name.startswith("Eden")
    assert kd.find_folder(tmp_path, {"RRID#": "RRID-0099",
                                     "File Name": "Nobody"}) is None


def test_activity_lines_drop_housekeeping_and_noops():
    events = [
        {"event": "document_indexed", "timestamp": "2026-10-14T15:00:00Z",
         "summary": "Indexed PLD-012"},
        {"event": "daily_run", "timestamp": "2026-10-14T20:30:00Z",
         "file_actions_requested": 0, "new_raw_files_seen": 0,
         "analysis": "nothing new"},
        {"event": "session_start", "timestamp": "2026-10-15T14:00:00Z",
         "summary": "opened"},
        {"event": "daily_cases_email_summary", "timestamp": "2026-10-15T20:00:00Z",
         "emails_fetched": 1, "files_saved": 0,
         "emails": [{"from_name": "Opposing Counsel",
                     "one_line": "Served second interrogatories",
                     "deadlines": [{"date": "2026-11-14", "description": "answers due"}]}]},
        {"event": "drafting", "timestamp": "2026-10-16T18:00:00Z",
         "summary": "Drafted opposition to motion to dismiss"},
    ]
    lines = kd.activity_lines(events)
    text = "\n".join(lines)
    assert "Indexed" not in text and "nothing new" not in text and "opened" not in text
    assert "Served second interrogatories" in text and "2026-11-14" in text
    assert "Drafted opposition" in text
    assert len(lines) == 2


def test_parse_json_tolerates_fences():
    assert kd._parse_json('```json\n{"status": "x", "week": []}\n```') == {
        "status": "x", "week": []}
    assert kd._parse_json("not json") is None


def test_render_escapes_and_flags_errors():
    html = kd.render_report([
        ("Eden, Artemus", "DC Superior Court",
         {"status": "Discovery open.", "week": ["Served <RFPs> & ROGs."],
          "next_steps": ["Nov. 14 — answers due."]}),
        ("Ghumman, Saira", "", {"status": "Stayed.", "week": [],
                                "next_steps": ["No action needed at this time."]}),
        ("HRI", "", {"error": "timeout"}),
    ], "Week of October 12 – 18, 2026", __import__("datetime").datetime(2026, 10, 18, 18))
    assert "Served &lt;RFPs&gt; &amp; ROGs." in html
    assert "No significant developments this week." in html
    assert "fill in or delete this card before sending" in html
    assert "3 cases" in html and "1 with activity this week" in html
    assert "Gallagher-Bozzuto Litigation Report" in html


def test_weekly_task_gets_sunday(monkeypatch):
    seen = {}
    monkeypatch.setattr(dashboard, "_run_schtasks",
                        lambda args: (seen.setdefault("args", args), (True, "ok"))[1])
    ok, name, _ = dashboard.create_task("kirsten-digest", "18:00", "WEEKLY")
    assert ok and name == "\\Rocky\\Kirsten Digest"
    args = seen["args"]
    assert args[args.index("/sc") + 1] == "WEEKLY"
    assert args[args.index("/st") + 1] == "18:00"
    assert args[args.index("/d") + 1] == "SUN"


def test_weekly_days_validated(monkeypatch):
    monkeypatch.setattr(dashboard, "_run_schtasks", lambda args: (True, "ok"))
    ok, _, msg = dashboard.create_task("kirsten-digest", "18:00", "WEEKLY",
                                       days="SUNDAY; rm")
    assert not ok and "Days" in msg


def test_daily_task_has_no_day(monkeypatch):
    seen = {}
    monkeypatch.setattr(dashboard, "_run_schtasks",
                        lambda args: (seen.setdefault("args", args), (True, "ok"))[1])
    dashboard.create_task("daily-cases", "16:00", "DAILY")
    assert "/d" not in seen["args"]


def test_week_bullets_drop_pre_week_items():
    items = [{"date": "2026-10-08", "text": "Filed the motion to dismiss."},
             {"date": "2026-10-14", "text": "Opposition finalized."},
             {"date": None, "text": "Undated item stays."},
             "Plain string stays.",
             {"date": "2026-10-15", "text": ""}]
    assert kd.week_bullets(items, "2026-10-11") == [
        "Opposition finalized.", "Undated item stays.", "Plain string stays."]


def test_firm_names_from_gallagher_senders():
    events = [
        {"event": "email_ingested", "from_name": "Tyler Fultz",
         "from_address": "tfultz@gallagherllp.com"},
        {"event": "email_ingested", "from_name": "Joy Arinze",
         "from_address": "jarinze@bozzuto.com"},
        {"event": "daily_cases_email_summary",
         "emails": [{"from_name": "Sam Cowin",
                     "from_address": "SCowin@GallagherLLP.com"}]},
    ]
    assert kd.firm_names(events) == ["Sam Cowin", "Tyler Fultz"]


def test_next_steps_drop_past_dates():
    items = [{"date": "2026-07-24", "text": "July 24, 2026 — mediation."},
             {"date": "2026-11-04", "text": "Nov. 4 — opposition due."},
             {"date": None, "text": "We are confirming the filing status."},
             "Plain string stays.",
             {"date": "2026-10-18", "text": "Today stays."}]
    assert kd.next_step_bullets(items, "2026-10-18") == [
        "Nov. 4 — opposition due.", "We are confirming the filing status.",
        "Plain string stays.", "Today stays."]


def test_next_steps_all_past_fall_back_to_nothing_pending():
    items = [{"date": "2026-06-15", "text": "June 15 — response due."}]
    assert kd.next_step_bullets(items, "2026-10-18") == [kd.NOTHING_PENDING]
    assert kd.next_step_bullets([], "2026-10-18") == [kd.NOTHING_PENDING]


def test_cases_root_falls_back_to_shared_folder_spelling(tmp_path, monkeypatch):
    import rocky
    monkeypatch.setattr(rocky, "_DEFAULT_CASES_ROOT", str(tmp_path / "dev-default"))
    shared = tmp_path / "OneDrive - gejlaw.com" / "James D. Bragdon's files - Rocky Cases"
    shared.mkdir(parents=True)
    missing = str(tmp_path / "OneDrive - gejlaw.com" / "Rocky Cases")
    assert rocky.resolve_cases_root({"cases_root": missing}, home=tmp_path) == shared
    assert rocky.resolve_cases_root({}, home=tmp_path) == shared


def test_cases_root_config_wins_when_it_exists(tmp_path):
    import rocky
    real = tmp_path / "Somewhere" / "Rocky Cases"
    real.mkdir(parents=True)
    (tmp_path / "OneDrive - gejlaw.com" / "Rocky Cases").mkdir(parents=True)
    assert rocky.resolve_cases_root({"cases_root": str(real)}, home=tmp_path) == real
    gone = tmp_path / "nowhere"
    assert rocky.resolve_cases_root({"cases_root": str(gone)},
                                    home=tmp_path / "empty") == gone
