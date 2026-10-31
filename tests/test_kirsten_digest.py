"""Kirsten Digest: case selection off the index column, folder matching
with shared RRIDs, housekeeping kept out of the prompt, rendering, the
period since the last report, the on-demand run (publish, then draft),
and the weekly schedule's /d day.

    python -m pytest tests/test_kirsten_digest.py -q
"""

import json
import sys
from datetime import datetime, timedelta, timezone
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
    # The Kirsten Digest went on demand 10/31; the fallback to a command's
    # sched_days is still how a WEEKLY task gets its day.
    monkeypatch.setitem(dashboard._COMMANDS_BY_FLAG, "kirsten-digest",
                        {**dashboard._COMMANDS_BY_FLAG["kirsten-digest"],
                         "sched_days": "SUN"})
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


def test_collect_applies_config_cases_root(tmp_path, monkeypatch):
    """rocky.exe imports a second copy of rocky; collect() must set its
    paths from config rather than trust the dev-laptop default."""
    import rocky
    root = tmp_path / "James D. Bragdon's files - Rocky Cases"
    root.mkdir()
    monkeypatch.setattr(rocky, "CASE_INDEX_PATH", Path(rocky._DEFAULT_CASES_ROOT) / "x.xlsx")
    monkeypatch.setattr(rocky, "ROCKY_CASES_ROOT", Path(rocky._DEFAULT_CASES_ROOT))
    monkeypatch.setattr(rocky, "DAILY_DIGESTS_DIR", Path(rocky._DEFAULT_CASES_ROOT), raising=False)
    monkeypatch.setattr(rocky, "load_case_index", lambda: [])
    assert kd.collect({"cases_root": str(root)}, 7)[0] is None
    assert rocky.ROCKY_CASES_ROOT == root
    assert rocky.CASE_INDEX_PATH == root / "Rocky Case Index.xlsx"


def test_activity_lines_can_name_who_logged_each_item():
    evs = [{"timestamp": "2026-10-21T22:04:26Z", "event": "research",
            "actor": "James Bragdon (Cowork)", "summary": "Reviewed the MSA."},
           {"timestamp": "2026-10-21T23:00:00Z", "event": "call",
            "actor": "rocky", "summary": "Call logged."},
           {"timestamp": "2026-10-22T10:00:00Z", "event": "note",
            "actor": "Kirsten Woelper", "summary": "Sent the lease."}]
    plain = kd.activity_lines(evs)
    tagged = kd.activity_lines(evs, authors=True)
    assert not any("[by" in line for line in plain)
    assert tagged[0].endswith("Reviewed the MSA. [by James Bragdon]")
    assert tagged[1].endswith("Call logged.")                 # Rocky: no tag
    assert tagged[2].endswith("Sent the lease. [by Kirsten Woelper]")
    assert len(tagged) == len(plain) == 3


def test_summarize_case_uses_ask_and_explains_the_author_tags():
    seen = {}

    class C:
        def __init__(self):
            self.messages = self
            self.beta = self

        def create(self, **kw):
            seen.update(kw)

            class R:
                stop_reason = "end_turn"
                content = [type("T", (), {"type": "text", "text":
                    '{"status": "MTD pending.", "week": [], "next_steps": []}'})]
            return R()

    out = kd.summarize_case(C(), "claude-opus-5-5", {"File Name": "Bulgin"},
                            ["Mon 10/19 research: x [by James Bragdon]"], "", "",
                            "w", "2026-10-19 (Monday)")
    assert out["status"] == "MTD pending."
    assert "temperature" not in seen and seen["max_tokens"] == 8000
    assert 'A line ending [by NAME] was logged by that person' in seen["system"]
    assert "[by James Bragdon]" in seen["messages"][0]["content"]


def test_dashboard_entry_is_on_demand():
    cmd = dashboard._COMMANDS_BY_FLAG["kirsten-digest"]
    assert not cmd.get("recommended")
    assert not any(k.startswith("sched_") for k in cmd)
    assert "podcast feed" in cmd["desc"]


END = datetime(2026, 10, 31, 22, 0, tzinfo=timezone.utc)


def test_window_starts_at_the_last_report():
    start, why = kd.window_start({"last_issued": "2026-10-18T22:01:00+00:00",
                                  "last_drafted": "2026-10-18"}, END)
    assert start == datetime(2026, 10, 18, 22, 1, tzinfo=timezone.utc)
    assert why.startswith("since the last report")


def test_window_old_state_uses_midnight_of_the_drafted_day():
    start, why = kd.window_start({"last_drafted": "2026-10-25"}, END)
    local = start.astimezone()
    assert (local.year, local.month, local.day, local.hour) == (2026, 10, 25, 0)
    assert "from midnight" in why


def test_window_days_wins_and_nothing_on_record_is_a_week():
    assert kd.window_start({"last_issued": "2026-10-01T00:00:00+00:00"},
                           END, days=3)[0] == END - timedelta(days=3)
    assert kd.window_start({}, END)[0] == END - timedelta(days=7)
    assert kd.window_start({"last_issued": "junk"}, END)[0] == END - timedelta(days=7)


def test_a_longer_period_is_worded_since_our_last_report():
    week = kd.Period(END - timedelta(days=7), END)
    long = kd.Period(END - timedelta(days=20), END)
    assert week.weekly and week.label.startswith("Week of ")
    assert not long.weekly and not long.label.startswith("Week of")
    assert long.label == "October 11 – October 31, 2026"
    html = kd.render_report([("Hunt", "", {"status": "Stayed.", "week": [],
                                           "next_steps": ["x"]})],
                            long.label, datetime(2026, 10, 31), period=long)
    assert "No significant developments since our last report." in html
    assert "Since our last report" in html and "this week" not in html


def _run(monkeypatch, tmp_path, argv, config=None, published=True):
    """run_cli with the case reading, Claude, ElevenLabs, Transistor and
    Graph all faked. Returns (drafts, published episodes)."""
    import rocky
    import kirsten_podcast as kp
    import pending_llt
    cards = [("Bulgin", "EDVA", {"status": "MTD pending.", "week": ["Filed."],
                                 "next_steps": ["Nov. 19 — opposition due."]})]
    starts = []

    def fake_collect(cfg, since, rrids=None):
        starts.append(since)
        return cards, "ok", kd.Period(since, datetime.now(timezone.utc))

    def fake_make(cfg, c, label, mp3, script, audio=True, quiet=None):
        mp3.parent.mkdir(parents=True, exist_ok=True)
        mp3.write_bytes(b"mp3")
        script.write_text("GEORGE: hi\n", encoding="utf-8")
        return mp3

    pubs = []

    def fake_publish(cfg, mp3, title, notes, transcript, record):
        pubs.append((title, transcript))
        record["episode_id"] = "77"
        if published:
            record["published"] = True
        return published

    drafts = []
    monkeypatch.setattr(kd, "collect", fake_collect)
    monkeypatch.setattr(kp, "make", fake_make)
    monkeypatch.setattr(kp, "publish", fake_publish)
    monkeypatch.setattr(rocky, "ROCKY_CASES_ROOT", tmp_path / "cases")
    monkeypatch.setattr(rocky, "get_msal_app", lambda cfg: None)
    monkeypatch.setattr(rocky, "acquire_token", lambda app: "tok")
    monkeypatch.setattr(rocky, "audit_token_scopes", lambda tok: None)
    monkeypatch.setattr(pending_llt, "create_draft_email",
                        lambda **kw: (drafts.append(kw),
                                      {"created": True, "message_id": "M1"})[1])
    monkeypatch.setattr(sys, "argv", ["rocky.py", "--kirsten-digest"] + argv)
    cfg = {"elevenlabs_api_key": "e", "transistor_api_key": "t",
           "kirsten_podcast_show_id": "42", **(config or {})}
    kd.run_cli(cfg, tmp_path)
    return drafts, pubs, starts


def test_run_publishes_then_drafts_with_the_live_header(monkeypatch, tmp_path):
    state = tmp_path / "state" / "kirsten_digest.json"
    state.parent.mkdir()
    state.write_text(json.dumps({"last_drafted": "2026-10-25",
                                 "last_issued": "2026-10-25T22:00:00+00:00"}))
    drafts, pubs, starts = _run(monkeypatch, tmp_path, [])
    assert starts == [datetime(2026, 10, 25, 22, tzinfo=timezone.utc)]
    assert len(pubs) == 1 and pubs[0][1] == "GEORGE: hi\n"
    assert pubs[0][0].startswith(kd.REPORT_NAME + " — ")
    html = drafts[0]["html_body"]
    assert kd.AUDIO_LIVE in html and kd.PRIVILEGE_EMAIL in html
    assert drafts[0]["to_addresses"] == ["kwoelper@bozzuto.com"]
    saved = json.loads(state.read_text())
    assert saved["last_drafted"] == datetime.now().strftime("%Y-%m-%d")
    assert datetime.fromisoformat(saved["last_issued"]) > starts[0]
    assert saved["podcast"]["published"] and saved["podcast"]["episode_id"] == "77"


def test_rerun_same_day_reuses_the_live_episode(monkeypatch, tmp_path):
    today = datetime.now().strftime("%Y-%m-%d")
    state = tmp_path / "state" / "kirsten_digest.json"
    state.parent.mkdir()
    # The draft failed after the episode went live: no last_drafted today.
    state.write_text(json.dumps({"podcast": {"date": today, "episode_id": "77",
                                             "published": True}}))
    drafts, pubs, _ = _run(monkeypatch, tmp_path, [])
    assert pubs == [] and kd.AUDIO_LIVE in drafts[0]["html_body"]


def test_failed_publish_drafts_without_the_header(monkeypatch, tmp_path):
    drafts, pubs, _ = _run(monkeypatch, tmp_path, [], published=False)
    assert len(pubs) == 1
    assert kd.AUDIO_LIVE not in drafts[0]["html_body"]
    saved = json.loads((tmp_path / "state" / "kirsten_digest.json").read_text())
    assert saved["podcast"]["episode_id"] == "77" and not saved["podcast"].get("published")


def test_no_show_id_saves_the_mp3_but_says_nothing(monkeypatch, tmp_path):
    drafts, pubs, _ = _run(monkeypatch, tmp_path, [],
                           config={"kirsten_podcast_show_id": ""})
    assert pubs == [] and kd.AUDIO_LIVE not in drafts[0]["html_body"]


def test_dry_run_records_nothing(monkeypatch, tmp_path):
    drafts, pubs, _ = _run(monkeypatch, tmp_path, ["--dry-run"])
    assert drafts == [] and pubs == []
    assert not (tmp_path / "state" / "kirsten_digest.json").exists()
    preview = (tmp_path / "state" / "kirsten_digest_preview.html").read_text(encoding="utf-8")
    assert kd.AUDIO_LIVE in preview
