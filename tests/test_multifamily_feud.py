"""Multifamily Feud: the FEUD profile on Sam's Podcast's engine. The Daily
Reports folder found by RRID, the index column, the alternating opener, the
style file and its settings under the show's own prefix, the no-"you"
prompts, and a dry run end to end with Claude stubbed out.

    python -m pytest tests/test_multifamily_feud.py -q
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard  # noqa: E402
import multifamily_feud as mf  # noqa: E402
import sams_podcast as sp  # noqa: E402

FEUD = mf.FEUD

MEMO = """---
topic: "Court decisions"
edition_date: "2026-10-27"
---

## Lead

Judge Chhabria dismissed the CLRA claim in McAdams v. Essex.
"""


def _report(reports, day):
    folder = reports / day
    folder.mkdir(parents=True)
    names = [("Court decisions", "01-decisions.md"),
             ("Tracked cases", "02-tracked-cases.md"),
             ("Bearing on our matters", "03-our-matters.md")]
    for _, name in names:
        (folder / name).write_text(MEMO, encoding="utf-8")
    (folder / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "edition_date": day, "status": "complete",
        "memos": [{"topic": t, "file": f} for t, f in names]}),
        encoding="utf-8")
    return folder


def _cases_root(tmp_path):
    cases = tmp_path / "Rocky Cases"
    reports = cases / "Fee Class Action Tracker (RRID-0045)" / "Daily Reports"
    reports.mkdir(parents=True)
    return cases, reports


def test_reports_folder_found_by_rrid(tmp_path):
    cases, reports = _cases_root(tmp_path)
    assert mf.reports_root({"cases_root": str(cases)}) == reports
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert mf.reports_root({"cases_root": str(cases),
                            "multifamily_feud_reports": str(other)}) == other
    assert sp.editions_root({"cases_root": str(cases)}, tmp_path,
                            FEUD) == reports
    # Sam's editions stay in its own folder.
    assert sp.editions_root({}, tmp_path) == tmp_path


def test_report_edition_loads_with_the_shared_contract(tmp_path):
    _, reports = _cases_root(tmp_path)
    _report(reports, "2026-10-27")
    edition = sp.pick_edition(reports, datetime(2026, 10, 27), set(),
                              mf._TAG)
    assert edition["date"] == "2026-10-27"
    assert [m["file"] for m in edition["memos"]] == [
        "01-decisions.md", "02-tracked-cases.md", "03-our-matters.md"]
    assert edition["memos"][0]["text"].startswith("## Lead")


def test_show_folder_probes_both_onedrive_names(tmp_path):
    assert sp.research_root({}, home=tmp_path, show=FEUD) is None
    share = (tmp_path / "OneDrive - gejlaw.com" / "Rocky's files - Podcasts"
             / "Multifamily Feud")
    share.mkdir(parents=True)
    assert sp.research_root({}, home=tmp_path, show=FEUD) == share
    own = tmp_path / "OneDrive - gejlaw.com" / "Podcasts" / "Multifamily Feud"
    own.mkdir(parents=True)
    assert sp.research_root({}, home=tmp_path, show=FEUD) == own
    # Sam's root ignores the Feud's folders.
    assert sp.research_root({}, home=tmp_path) is None


def test_sams_root_found_through_the_podcasts_share(tmp_path):
    share = (tmp_path / "OneDrive - gejlaw.com" / "Rocky's files - Podcasts"
             / "Sam's Podcast")
    share.mkdir(parents=True)
    assert sp.research_root({}, home=tmp_path) == share


def test_index_column_is_the_feud_column():
    rows = [{"RRID#": "RRID-0008", "Sam Podcast?": "Yes",
             "Multifamily Feud?": "Yes"}]
    assert sp.show_column(rows, FEUD) == "Multifamily Feud?"
    assert sp.sam_column(rows) == "Sam Podcast?"


def test_opener_alternates_by_day():
    day = datetime(2026, 10, 27)
    assert sp.opener_index("alternate", day) == 0                # Archie
    assert sp.opener_index("alternate", datetime(2026, 10, 28)) == 1
    assert sp.opener_index("alternate", datetime(2026, 10, 29)) == 0
    assert sp.opener_index("alternate", datetime(2026, 11, 1)) == 1
    assert sp.opener_index("host2", day) == 1
    assert sp.opener_index(None, day) == 0 and sp.opener_index("host1", day) == 0


def test_style_file_seeded_with_the_show_settings(tmp_path):
    style = sp.load_style({}, tmp_path, FEUD)
    assert (tmp_path / "PODCAST_STYLE.md").read_text(encoding="utf-8") == \
        mf.DEFAULT_STYLE
    settings = style["settings"]
    assert settings["multifamily_feud_opener"] == "alternate"
    assert settings["multifamily_feud_intro_seconds"] == 18
    assert settings["multifamily_feud_intro_file"] == "Vintage Swing Intro.wav"
    assert settings["host1"] == "Archie" and settings["host2"] == "Frieda"
    assert not any(k.startswith("sams_podcast") for k in settings)
    assert "## Report cards" not in style["script"]
    assert "Headline" in style["topic"] and "Next steps" in style["case"]
    config = sp.apply_settings({}, settings, FEUD)
    assert config["multifamily_feud_hosts"] == ["Archie", "Frieda"]
    assert config["multifamily_feud_voices"] == ["WLKp2jV6nrS8aMkPPDRO",
                                                 "BIvP0GN1cAtSRTxNHnWS"]
    assert sp.word_limits(config, FEUD) == (1400, 1600)
    assert sp.voice_settings(config, FEUD) == {"stability": 0.5}
    assert "sams_podcast_hosts" not in config


def test_opener_setting_is_checked():
    assert sp.parse_settings({"opener": "Host 2"}, FEUD) == \
        {"multifamily_feud_opener": "host2"}
    assert sp.parse_settings({"opener": "Frieda"}, FEUD) == {}


def test_intro_comes_from_the_show_folder(tmp_path):
    intro = tmp_path / "Vintage Swing Intro.wav"
    intro.write_bytes(b"RIFF")
    config = sp.apply_settings({}, sp.load_style({}, tmp_path, FEUD)["settings"],
                               FEUD)
    assert sp.intro_path(config, tmp_path, FEUD) == intro
    assert sp.intro_path({}, tmp_path, FEUD) == intro            # by its name
    assert sp.intro_path({}, tmp_path) is None                   # Sam's looks elsewhere


def test_script_prompt_names_todays_opener_and_has_no_you():
    style = sp.load_style({}, None, FEUD)["script"]
    prompt = sp.script_prompt(style, ["Archie", "Frieda"], "", 1400, 1600,
                              list(mf.AUDIO_TAGS), opener=1, show=FEUD)
    assert "Frieda opens today's show, so the first turn is speaker 2" in prompt
    assert "**Opening.** Frieda opens" in prompt
    assert "Archie answers in the second turn" in prompt
    assert "Nobody's work is \"you\"" in prompt
    assert "This step is important" in prompt
    assert "{" + "opener}" not in prompt and "{other}" not in prompt
    assert "[curious]" in prompt and "sports" not in prompt.lower()
    # Sam's prompt is unchanged by the opener values.
    sams = sp.script_prompt("x", ["Al", "Sophia"], "Sam Cowin")
    assert "Al is speaker 1" in sams and "opens today's show" not in sams


def test_case_card_prompt_credits_people_by_name():
    prompt = sp.card_prompt(FEUD.case_core, None, "case", "", FEUD)
    assert "Never write anyone's work as \"you\"" in prompt
    assert "{listener}" not in prompt and "Sam" not in prompt


def test_notes_headings_and_cards_label():
    topics = [{"kind": "topic", "title": "Court decisions",
               "headline": "McAdams dismissed.", "developments": ["x"],
               "upcoming": [], "angles": []}]
    cases = [{"kind": "case", "quiet": True, "title": "Hettinger"}]
    notes = sp.render_notes(topics, cases, FEUD)
    assert "<h2>The report</h2>" in notes and "<h2>Our docket</h2>" in notes
    assert "Sports" not in notes
    assert sp.cards_text(topics, cases, FEUD.topic_label).startswith(
        "REPORT: Court decisions")


def test_dashboard_entry_daily_5am():
    cmd = next(c for c in dashboard.ROCKY_COMMANDS
               if c["flag"] == "multifamily-feud")
    assert cmd["sched_time"] == "05:00" and cmd.get("recommended")
    assert cmd.get("sched_freq", "DAILY") == "DAILY"


def test_dry_run_end_to_end(monkeypatch, tmp_path):
    """Report edition -> cards -> script -> fact check -> preview files, with
    Claude stubbed. The opener and the show's own prompts reach the calls."""
    cases_root, reports = _cases_root(tmp_path)
    today = datetime.now().strftime("%Y-%m-%d")
    _report(reports, today)
    show_root = tmp_path / "Multifamily Feud"
    show_root.mkdir()
    systems = []

    def fake_ask(client, model, system, messages, **kw):
        systems.append(system)
        if system.startswith("# Goal\n\nTurn one section"):
            return json.dumps({"headline": "McAdams v. Essex dismissed.",
                               "developments": ["Judge Chhabria dismissed "
                                                "the CLRA claim."],
                               "upcoming": [], "angles": []})
        if "fact-check" in system:
            return json.dumps({"fixes": []})
        return json.dumps({"turns": [
            {"speaker": 1, "text": "Welcome to Multifamily Feud."},
            {"speaker": 2, "text": "McAdams v. Essex is out."}]})

    class FakeAnthropic:
        def __init__(self, **kw):
            pass

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    monkeypatch.setattr(sp, "_ask", fake_ask)
    monkeypatch.setattr(sp, "collect_cases",
                        lambda *a, **k: ([{"kind": "case", "quiet": True,
                                           "title": "Hettinger"}], "ok"))
    monkeypatch.setattr(sys, "argv", ["rocky.py", "--multifamily-feud",
                                      "--dry-run"])
    mf.run_cli({"anthropic_api_key": "K", "cases_root": str(cases_root),
                "multifamily_feud_root": str(show_root)}, tmp_path)

    state = tmp_path / "state"
    script = (state / "multifamily_feud_preview.txt").read_text(
        encoding="utf-8")
    notes = (state / "multifamily_feud_preview.html").read_text(
        encoding="utf-8")
    assert script.startswith("ARCHIE: Welcome") or \
        script.startswith("FRIEDA: Welcome")
    assert "Multifamily Feud" in notes and "Court decisions" in notes
    assert (show_root / "PODCAST_STYLE.md").is_file()         # seeded
    assert not (state / "sams_podcast_preview.txt").exists()
    # Three report cards, one script call, one fact check, all the show's own.
    assert sum(s.startswith("# Goal\n\nTurn one section") for s in systems) == 3
    script_system = next(s for s in systems if "Write the script" in s)
    opener = ["Archie", "Frieda"][sp.opener_index("alternate", datetime.now())]
    assert f"{opener} opens today's show" in script_system
    assert systems[-1] == mf.CHECK_PROMPT
