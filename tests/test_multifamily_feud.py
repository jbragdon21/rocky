"""Multifamily Feud: the FEUD profile on Sam's Podcast's engine. The Daily
Reports folder found by RRID, the index column, the alternating opener, the
style file and its settings under the show's own prefix, the no-"you"
prompts, and a dry run end to end with Claude stubbed out.

    python -m pytest tests/test_multifamily_feud.py -q
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard  # noqa: E402
import multifamily_feud as mf  # noqa: E402
import sams_podcast as sp  # noqa: E402

FEUD = mf.FEUD

MEMO = """---
topic: "Research"
---

## Lead

Judge Chhabria dismissed the CLRA claim in McAdams v. Essex.
"""

FOLDERS = {"research": ("Daily ChatGPT research",),
           "hits": ("Courthouse News", "Hit Reports"),
           "summary": ("Tracked Cases", "Daily Summary of Tracked Cases")}


def _case_folder(tmp_path):
    cases = tmp_path / "Rocky Cases"
    folder = cases / "Multifamily Feud (RRID-0045)"
    for parts in FOLDERS.values():
        folder.joinpath(*parts).mkdir(parents=True)
    return cases, folder


def _put(folder, source, name, text=MEMO, mtime=None):
    path = folder.joinpath(*FOLDERS[source], name)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _old(days):
    """A save time comfortably outside the settle window."""
    return (datetime.now() - timedelta(days=days, minutes=10)).timestamp()


def test_case_folder_found_by_rrid(tmp_path):
    cases, folder = _case_folder(tmp_path)
    assert mf.tracker_root({"cases_root": str(cases)}) == folder
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert mf.tracker_root({"cases_root": str(cases),
                            "multifamily_feud_folder": str(other)}) == other


def test_gather_reads_the_three_folders_and_skips_the_rest(tmp_path):
    cases, folder = _case_folder(tmp_path)
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    two_ago = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    _put(folder, "research", f"{today}_Research_Memo.md", mtime=_old(0))
    _put(folder, "research", f"{two_ago}_Research_Memo.md", mtime=_old(0))
    _put(folder, "hits", "CNS hit report.md", mtime=_old(1))     # by save day
    _put(folder, "hits", "old hit.md", mtime=_old(3))
    _put(folder, "summary", f"{yesterday} summary.txt", mtime=_old(0))
    _put(folder, "summary", "~$draft.md", mtime=_old(0))
    _put(folder, "summary", "notes.json", mtime=_old(0))
    (folder / "Daily ChatGPT research" / ".research_work").mkdir()
    _put(folder, "research", f".research_work/{today}_memo.pending.md",
         mtime=_old(0))
    edition = mf.gather({"cases_root": str(cases)}, now, set())
    assert [(m["topic"], m["file"]) for m in edition["memos"]] == [
        ("Daily research memo", f"{today}_Research_Memo.md"),
        ("Courthouse News hits", "CNS hit report.md"),
        ("Tracked cases summary", f"{yesterday} summary.txt")]
    assert edition["memos"][0]["text"].startswith("## Lead")  # front matter off
    assert len(edition["inputs"]) == 3
    assert edition["inputs"][0].startswith(
        f"Daily ChatGPT research/{today}_Research_Memo.md@")


def test_gather_uses_each_file_once_unless_saved_again(tmp_path):
    cases, folder = _case_folder(tmp_path)
    now = datetime.now()
    path = _put(folder, "hits", "CNS hit report.md", mtime=_old(0))
    config = {"cases_root": str(cases)}
    first = mf.gather(config, now, set())
    assert mf.gather(config, now, set(first["inputs"])) is None
    os.utime(path, (_old(0) + 60, _old(0) + 60))               # saved again
    assert mf.gather(config, now, set(first["inputs"]))["memos"]


def test_gather_waits_for_a_file_still_being_saved(tmp_path):
    cases, folder = _case_folder(tmp_path)
    _put(folder, "summary", "summary.md")                       # saved just now
    assert mf.gather({"cases_root": str(cases)}, datetime.now(), set()) is None


def test_gather_survives_a_missing_folder_and_empty_files(tmp_path):
    cases, folder = _case_folder(tmp_path)
    (folder / "Courthouse News" / "Hit Reports").rmdir()
    _put(folder, "summary", "summary.md", text="   ", mtime=_old(0))
    _put(folder, "research", "memo.md", mtime=_old(0))
    edition = mf.gather({"cases_root": str(cases)}, datetime.now(), set())
    assert [m["topic"] for m in edition["memos"]] == ["Daily research memo"]


def test_long_report_split_at_headings():
    text = ("# Title\nintro\n## A\n" + "a" * 30 + "\n## B\n" + "b" * 30
            + "\n## C\n" + "c" * 90 + "\n")
    pieces = mf.split_report(text, cap=60)
    assert pieces[0].startswith("# Title") and "## A" in pieces[0]
    assert pieces[1].startswith("## B")
    assert all(len(p) <= 60 for p in pieces)
    assert "".join(pieces).count("c") == 90
    assert mf.split_report("short") == ["short"]


def test_split_pieces_become_numbered_cards(tmp_path):
    cases, folder = _case_folder(tmp_path)
    big = "".join(f"## Part {i}\n" + "x" * 5000 + "\n" for i in range(4))
    _put(folder, "research", "memo.md", text=big, mtime=_old(0))
    edition = mf.gather({"cases_root": str(cases)}, datetime.now(), set())
    assert [m["topic"] for m in edition["memos"]] == [
        "Daily research memo (1 of 2)", "Daily research memo (2 of 2)"]
    assert len(edition["inputs"]) == 1


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


def test_style_tags_and_joes_preferences_reach_the_script():
    style = sp.load_style({}, None, FEUD)
    tags = style["settings"]["multifamily_feud_audio_tags"]
    assert tags == list(mf.AUDIO_TAGS) and "jumping in" in tags
    # The two-word cue survives cleaning; a cue not on the list doesn't.
    assert sp.clean_text("[jumping in] With prejudice? [shouts] Damn.",
                         tags) == "[jumping in] With prejudice? Damn."
    prompt = sp.script_prompt(style["script"], ["Archie", "Frieda"], "", 1400,
                              1600, tags, opener=0, show=FEUD)
    assert "## Joe's preferences" in prompt and "the bullet wins" in prompt
    assert "## Voice cues" in prompt and "[interrupting]" in prompt
    assert "HOW THE SCRIPT IS ANNOTATED" not in prompt        # comment stripped
    assert "That's the Feud. Back tomorrow." in prompt
    assert "See you" not in prompt


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
    assert "<h2>Outside news</h2>" in notes and "<h2>Our docket</h2>" in notes
    assert "Sports" not in notes
    assert sp.cards_text(topics, cases, FEUD.topic_label).startswith(
        "REPORT: Court decisions")


def test_dashboard_entry_daily_5am():
    cmd = next(c for c in dashboard.ROCKY_COMMANDS
               if c["flag"] == "multifamily-feud")
    assert cmd["sched_time"] == "05:00" and cmd.get("recommended")
    assert cmd.get("sched_freq", "DAILY") == "DAILY"


def test_dry_run_end_to_end(monkeypatch, tmp_path):
    """The three report folders -> cards -> script -> fact check -> preview
    files, with Claude stubbed. The opener and the show's own prompts reach
    the calls; files an earlier episode used are skipped."""
    cases_root, folder = _case_folder(tmp_path)
    today = datetime.now().strftime("%Y-%m-%d")
    _put(folder, "research", f"{today}_Research_Memo.md", mtime=_old(0))
    _put(folder, "hits", f"{today} CNS hits.md", mtime=_old(0))
    _put(folder, "summary", f"{today} tracked cases.md", mtime=_old(0))
    show_root = tmp_path / "Multifamily Feud"
    show_root.mkdir()
    systems = []

    def fake_ask(client, model, system, messages, **kw):
        systems.append(system)
        if system.startswith("# Goal\n\nTurn one of today's reports"):
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
    config = {"anthropic_api_key": "K", "cases_root": str(cases_root),
              "multifamily_feud_root": str(show_root)}
    mf.run_cli(dict(config), tmp_path)

    state = tmp_path / "state"
    script = (state / "multifamily_feud_preview.txt").read_text(
        encoding="utf-8")
    notes = (state / "multifamily_feud_preview.html").read_text(
        encoding="utf-8")
    assert script.startswith("ARCHIE: Welcome") or \
        script.startswith("FRIEDA: Welcome")
    assert "Multifamily Feud" in notes and "<h2>Outside news</h2>" in notes
    assert "Courthouse News hits" in notes and "Tracked cases summary" in notes
    assert (show_root / "PODCAST_STYLE.md").is_file()         # seeded
    assert not (state / "sams_podcast_preview.txt").exists()
    # Three report cards, one script call, one fact check, all the show's own.
    card = "# Goal\n\nTurn one of today's reports"
    assert sum(s.startswith(card) for s in systems) == 3
    script_system = next(s for s in systems if "Write the script" in s)
    opener = ["Archie", "Frieda"][sp.opener_index("alternate", datetime.now())]
    assert f"{opener} opens today's show" in script_system
    assert systems[-1] == mf.CHECK_PROMPT

    # An episode yesterday already used two of the files: only the third
    # makes a card.
    keys = mf.gather(config, datetime.now(), set())["inputs"]
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    (state / "multifamily_feud.json").write_text(json.dumps(
        {"episodes": {yesterday: {"inputs": keys[:2]}}}), encoding="utf-8")
    systems.clear()
    mf.run_cli(dict(config), tmp_path)
    assert sum(s.startswith(card) for s in systems) == 1
