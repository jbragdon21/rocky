"""Sam's Podcast: research editions (the manifest contract), the index
column, yesterday's window, the cards the script is built from, the show
notes, and the Transistor upload / create / publish calls.

    python -m pytest tests/test_sams_podcast.py -q
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard  # noqa: E402
import kirsten_digest as kd  # noqa: E402
import sams_podcast as sp  # noqa: E402

MEMO = """---
topic: "Chicago Bears football"
edition_date: "2026-10-23"
---

# Bears — October 23, 2026

## Lead

Bears beat the Lions.
"""


def _edition(root, day, status="complete", memos=None, write=True):
    folder = root / day
    folder.mkdir(parents=True)
    memos = memos if memos is not None else [
        {"topic": "Chicago Bears football", "file": "04-chicago-bears.md"}]
    if write:
        for m in memos:
            (folder / m["file"]).write_text(MEMO, encoding="utf-8")
    (folder / "manifest.json").write_text(json.dumps(
        {"schema_version": 1, "edition_date": day, "status": status,
         "memos": memos}), encoding="utf-8")
    return folder


def test_load_edition_complete_strips_front_matter(tmp_path):
    ed = sp.load_edition(_edition(tmp_path, "2026-10-23"))
    assert ed["date"] == "2026-10-23"
    assert ed["memos"][0]["topic"] == "Chicago Bears football"
    assert ed["memos"][0]["text"].startswith("# Bears")


def test_load_edition_refuses_incomplete_or_missing_memos(tmp_path):
    assert sp.load_edition(_edition(tmp_path, "2026-10-21",
                                    status="writing")) is None
    assert sp.load_edition(_edition(tmp_path, "2026-10-22",
                                    write=False)) is None
    folder = _edition(tmp_path, "2026-10-23")
    (folder / "04-chicago-bears.md").write_text("  \n", encoding="utf-8")
    assert sp.load_edition(folder) is None
    (tmp_path / "2026-10-24").mkdir()
    assert sp.load_edition(tmp_path / "2026-10-24") is None   # no manifest


def test_pick_edition_today_then_unused_yesterday(tmp_path):
    now = datetime(2026, 10, 23, 6, 0)
    _edition(tmp_path, "2026-10-21")
    _edition(tmp_path, "2026-10-22")
    assert sp.pick_edition(tmp_path, now, set())["date"] == "2026-10-22"
    assert sp.pick_edition(tmp_path, now, {"2026-10-22"}) is None  # 10/21 stale
    _edition(tmp_path, "2026-10-23")
    assert sp.pick_edition(tmp_path, now, {"2026-10-22"})["date"] == "2026-10-23"


def test_research_root_probes_both_onedrive_names(tmp_path):
    assert sp.research_root({}, home=tmp_path) is None
    share = tmp_path / "OneDrive - gejlaw.com" / "Rocky's files - Sam's Podcast"
    share.mkdir(parents=True)
    assert sp.research_root({}, home=tmp_path) == share
    own = tmp_path / "OneDrive - gejlaw.com" / "Podcasts" / "Sam's Podcast"
    own.mkdir(parents=True)
    assert sp.research_root({"sams_podcast_root": str(tmp_path / "nope")},
                            home=tmp_path) == own
    assert sp.research_root({"sams_podcast_root": str(share)},
                            home=tmp_path) == share


def test_sam_column_and_selection():
    rows = [{"RRID#": "RRID-0005", "File Name": "HRI", "Kirsten Digest? ": "Yes",
             "Sam's podcast": "yes"},
            {"RRID#": "RRID-0008", "File Name": "Ogunnupe",
             "Kirsten Digest? ": "Yes", "Sam's podcast": "no"},
            {"RRID#": "RRID-0009", "File Name": "Wall", "Sam's podcast": "x",
             "Open/Closed": "Closed"}]
    col = sp.sam_column(rows)
    assert col == "Sam's podcast"
    assert [c["File Name"] for c in kd.select_cases(rows, col=col)] == ["HRI"]
    # The Kirsten Digest default is unchanged.
    assert [c["File Name"] for c in kd.select_cases(rows)] == ["HRI",
                                                               "Ogunnupe"]
    assert sp.sam_column([{"RRID#": "x", "Kirsten Digest?": "y"}]) is None


def test_yesterday_window_is_local_midnight_to_midnight():
    now = datetime(2026, 10, 23, 6, 0).astimezone()
    start, end = sp.yesterday_window(now)
    assert (start.day, start.hour, end.day, end.hour) == (22, 0, 23, 0)
    evs = [{"timestamp": (start - timedelta(minutes=1)).isoformat()},
           {"timestamp": start.isoformat(), "n": 1},
           {"timestamp": (end - timedelta(seconds=1))
            .astimezone(timezone.utc).isoformat(), "n": 2},
           {"timestamp": end.isoformat()}]
    assert [e["n"] for e in sp.events_between(evs, start, end)] == [1, 2]


def test_dated_texts_drop_past_items():
    items = [{"date": "2026-10-20", "text": "old"},
             {"date": "2026-10-23", "text": "today"},
             {"date": None, "text": "undated"}, "plain", {"text": "  "}]
    assert sp._dated_texts(items, "2026-10-23") == ["today", "undated", "plain"]
    assert sp._dated_texts(items, "2026-10-23", drop_before="2026-10-20") == \
        ["old", "today", "undated", "plain"]


TOPICS = [{"kind": "topic", "title": "Chicago Bears football",
           "headline": "Bears won.", "developments": ["Beat Detroit 24-20."],
           "upcoming": ["Oct. 26, 1 p.m. Eastern — at Green Bay."],
           "angles": ["Is the defense real?"]},
          {"kind": "topic", "title": "Nationals", "error": "timeout"}]
CASES = [{"kind": "case", "title": "Bulgin <A>", "subtitle": "EDVA",
          "status": "MTD pending.", "yesterday": ["Opposition filed."],
          "next_steps": ["Nov. 19 — reply due."]},
         {"kind": "case", "title": "Hunt", "quiet": True},
         {"kind": "case", "title": "HRI", "error": "no case folder found"}]


def test_cards_text_skips_failed_and_lists_quiet():
    text = sp.cards_text(TOPICS, CASES)
    assert "TOPIC: Chicago Bears football" in text
    assert "Angles (opinion, for discussion):" in text
    assert "Nationals" not in text and "HRI" not in text
    assert "CASE: Bulgin <A> — EDVA" in text
    assert "separate case):\n- Hunt" in text
    assert "CASE" not in sp.cards_text(TOPICS, [])


def test_render_notes_escapes_and_flags_failures():
    notes = sp.render_notes(TOPICS, CASES)
    assert "<h3>Bulgin &lt;A&gt;</h3>" in notes
    assert "No card (timeout)" in notes
    assert "<strong>Quiet yesterday:</strong> Hunt" in notes
    assert "<h2>Cases</h2>" not in sp.render_notes(TOPICS, [])


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}
        self.text = json.dumps(self._body)
        self.content = self.text.encode()

    def json(self):
        return self._body


def test_transistor_upload_create_publish(monkeypatch, tmp_path):
    mp3 = tmp_path / "2026-10-23.mp3"
    mp3.write_bytes(b"ID3audio")
    calls = []

    def fake_get(url, **kw):
        calls.append(("GET", url, kw.get("params"), kw["headers"]))
        return _Resp(body={"data": {"attributes": {
            "upload_url": "https://s3/presigned", "content_type": "audio/mpeg",
            "audio_url": "https://cdn/2026-10-23.mp3"}}})

    def fake_put(url, **kw):
        calls.append(("PUT", url, kw["data"], kw["headers"]))
        return _Resp()

    def fake_post(url, **kw):
        calls.append(("POST", url, kw["data"], kw["headers"]))
        return _Resp(201, {"data": {"id": "987", "attributes":
                                    {"status": "draft"}}})

    def fake_patch(url, **kw):
        calls.append(("PATCH", url, kw["data"], kw["headers"]))
        return _Resp()

    for name, fn in (("get", fake_get), ("put", fake_put),
                     ("post", fake_post), ("patch", fake_patch)):
        monkeypatch.setattr(sp.requests, name, fn)

    url = sp.upload_audio("KEY", mp3)
    ep = sp.create_episode("KEY", "sams", "T", "S", "<p>notes</p>", url, "TX")
    sp.publish_episode("KEY", ep)
    assert ep == "987"
    get, put, post, patch = calls
    assert get[2] == {"filename": "2026-10-23.mp3"} and get[3]["x-api-key"] == "KEY"
    assert put[1] == "https://s3/presigned" and put[2] == b"ID3audio"
    assert "x-api-key" not in put[3]          # presigned URL: no API key
    assert post[2]["episode[audio_url]"] == "https://cdn/2026-10-23.mp3"
    assert post[2]["episode[show_id]"] == "sams"
    assert post[2]["episode[transcript_text]"] == "TX"
    assert patch[1].endswith("/episodes/987/publish")
    assert patch[2] == {"episode[status]": "published"}


def test_transistor_error_raises(monkeypatch):
    monkeypatch.setattr(sp.requests, "patch",
                        lambda url, **kw: _Resp(422, {"errors": ["bad"]}))
    try:
        sp.publish_episode("KEY", "1")
    except RuntimeError as e:
        assert "HTTP 422" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


def test_run_retries_publish_of_an_unpublished_episode(monkeypatch, tmp_path):
    today = datetime.now().strftime("%Y-%m-%d")
    state = tmp_path / "state" / "sams_podcast.json"
    state.parent.mkdir()
    state.write_text(json.dumps({"episodes": {today: {
        "episode_id": "55", "published": False}}}), encoding="utf-8")
    published = []
    monkeypatch.setattr(sp, "publish_episode",
                        lambda key, eid: published.append(eid))
    monkeypatch.setattr(sys, "argv", ["rocky.py", "--sams-podcast"])
    sp.run_cli({"transistor_api_key": "K"}, tmp_path)
    assert published == ["55"]
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["episodes"][today]["published"] is True
    # Published already: a second run does nothing (no Claude call).
    sp.run_cli({"transistor_api_key": "K"}, tmp_path)
    assert published == ["55"]


def test_dashboard_entry_daily_6am():
    cmd = next(c for c in dashboard.ROCKY_COMMANDS
               if c["flag"] == "sams-podcast")
    assert cmd["sched_time"] == "06:00" and cmd.get("recommended")
    assert cmd.get("sched_freq", "DAILY") == "DAILY"


def test_default_voices_use_the_show_voice():
    voices, hosts = sp._settings({})
    assert voices[0] == "Q1QcmfZPmFDVUWmzASdy" and hosts == ["Al", "Sophia"]
    assert voices[1] == "LtPsVjX1k0Kl4StEMZPK"
    assert sp._settings({"sams_podcast_hosts": ["Jo", "Sophia"]})[1][0] == "Jo"


def test_intro_path_prefers_lossless_soundtrack(tmp_path):
    assert sp.intro_path({}, tmp_path) is None
    (tmp_path / "Sam's Soundtrack.mp3").write_bytes(b"x")
    assert sp.intro_path({}, tmp_path).name == "Sam's Soundtrack.mp3"
    (tmp_path / "Sam's Soundtrack.wav").write_bytes(b"x")
    assert sp.intro_path({}, tmp_path).name == "Sam's Soundtrack.wav"
    other = tmp_path / "other.flac"
    other.write_bytes(b"x")
    assert sp.intro_path({"sams_podcast_intro": str(other)}, tmp_path) == other
    assert sp.intro_path({"sams_podcast_intro": "C:/nope.mp3"},
                         tmp_path).name == "Sam's Soundtrack.wav"
    assert sp.intro_path({}, None) is None


def test_mix_intro_trims_fades_and_overlaps():
    from array import array
    rate = 10                                   # 10 samples a second
    music = array("h", [1000] * 100)            # 10 s of music
    speech = array("h", [7] * 30)
    out = sp.mix_intro(music, speech, seconds=5, fade=2, overlap=1, rate=rate)
    assert len(out) == 40 + 30                  # hosts start at 4 s
    assert out[0] == 1000 and out[29] == 1000   # full volume before the fade
    assert out[39] == 550 and out[49] == 50 + 7  # fading, hosts under it
    assert out[40] == 500 + 7
    assert out[50:] == speech[10:]              # after the music: hosts only
    whole = sp.mix_intro(music, speech, seconds=0, fade=0, overlap=0, rate=rate)
    assert len(whole) == 130


def test_add_intro_failure_leaves_the_episode(tmp_path, monkeypatch):
    mp3 = tmp_path / "show.mp3"
    mp3.write_bytes(b"HOSTS")
    monkeypatch.setattr(sp, "_pcm", lambda path, rate=44100: 1 / 0)
    sp.add_intro({}, tmp_path / "intro.mp3", mp3)
    assert mp3.read_bytes() == b"HOSTS"


def test_attributed_lines_name_the_actor():
    evs = [{"timestamp": "2026-10-21T22:04:26Z", "event": "indemnity",
            "actor": "James Bragdon (Cowork)", "summary": "Reviewed the MSA."},
           {"timestamp": "2026-10-21T23:00:00Z", "event": "call",
            "actor": "rocky", "summary": "Call logged."}]
    lines = sp.attributed_lines(evs)
    assert lines[0].endswith("indemnity: Reviewed the MSA. [by James Bragdon]")
    assert lines[1].endswith("call: Call logged.")


def test_whole_track_is_the_default_and_unfaded(tmp_path, monkeypatch):
    from array import array
    calls = {}

    def fake_mix(music, speech, seconds, fade, overlap, rate=44100):
        calls.update(seconds=seconds, fade=fade, overlap=overlap)
        return array("h", [0])

    monkeypatch.setattr(sp, "_pcm", lambda path, rate=44100: array("h", [1]))
    monkeypatch.setattr(sp, "mix_intro", fake_mix)
    monkeypatch.setattr(sp, "encode_mp3", lambda samples: b"MIXED")
    mp3 = tmp_path / "show.mp3"
    mp3.write_bytes(b"HOSTS")
    sp.add_intro({}, tmp_path / "Sam's Soundtrack.wav", mp3)
    assert calls == {"seconds": 0, "fade": 0, "overlap": 2.0}
    assert mp3.read_bytes() == b"MIXED"
    sp.add_intro({"sams_podcast_intro_seconds": 20}, tmp_path / "x.wav", mp3)
    assert calls["seconds"] == 20 and calls["fade"] == 4.0


def test_style_guide_is_seeded_then_read_from_the_folder(tmp_path):
    guide = tmp_path / "PODCAST_STYLE.md"
    first = sp.style_guide({}, tmp_path)
    assert guide.read_text(encoding="utf-8") == sp.DEFAULT_STYLE   # seeded
    assert "<!--" not in first and "## Shape" in first             # notes dropped
    guide.write_text("<!-- for James -->\n## Shape\n- {host2} opens. "
                     "Keep it to {target} words.\n", encoding="utf-8")
    edited = sp.style_guide({}, tmp_path)
    assert edited == "## Shape\n- {host2} opens. Keep it to {target} words."
    prompt = sp.script_prompt(edited, ["Al", "Sophia"], "Sam Cowin")
    assert "- Sophia opens. Keep it to 1,400 words." in prompt
    assert "At most 1,600 words" in prompt and "Al is speaker 1" in prompt


def test_style_guide_falls_back_without_seeding_elsewhere(tmp_path):
    guide = tmp_path / "PODCAST_STYLE.md"
    guide.write_text("<!-- only a note -->\n", encoding="utf-8")
    assert "## Shape" in sp.style_guide({}, tmp_path)               # empty file
    missing = tmp_path / "custom.md"
    assert "## Shape" in sp.style_guide({"sams_podcast_style": str(missing)},
                                        tmp_path)
    assert not missing.exists()                                     # not seeded
    assert "## Shape" in sp.style_guide({}, None)


def test_script_prompt_does_not_expand_placeholders_typed_in_the_style():
    prompt = sp.script_prompt("Say {style} and {listener}.", ["Al", "Sophia"],
                              "Sam Cowin")
    assert prompt.count("Use only facts in the cards") == 1
    assert "Say {style} and Sam Cowin." in prompt


def test_write_script_sends_the_style_guide():
    seen = {}

    class Msg:
        def create(self, **kw):
            seen.setdefault("system", kw["system"])   # the script call

            class R:
                content = [type("T", (), {"text": json.dumps({"turns": [
                    {"speaker": 1, "text": "Hi Sam."},
                    {"speaker": 2, "text": "See you tomorrow."}]})})]
            return R()

    client = type("C", (), {"messages": Msg()})()
    client.beta = client
    turns = sp.write_script(client, "m", "TOPIC: Bears", "today",
                            ["Al", "Sophia"], "Sam Cowin",
                            "## Shape\n- Sophia does the sports.")
    assert len(turns) == 2
    assert "STYLE GUIDE\n## Shape\n- Sophia does the sports." in seen["system"]


class _OneReply:
    def __init__(self, reply):
        self.reply, self.calls = reply, []
        self.messages = self
        self.beta = self

    def create(self, **kw):
        self.calls.append(kw)
        if isinstance(self.reply, Exception):
            raise self.reply
        text = self.reply

        class R:
            content = [type("T", (), {"text": text})]
        return R()


def test_check_script_rewrites_and_drops_unsupported_turns():
    turns = [{"speaker": 1, "text": "The Bears host Oregon tomorrow."},
             {"speaker": 2, "text": "They head into the bye."},
             {"speaker": 1, "text": "See you tomorrow."}]
    client = _OneReply(json.dumps({"fixes": [
        {"turn": 1, "problem": "Oregon is Illinois' game",
         "text": "Illinois hosts Oregon tomorrow."},
        {"turn": 2, "problem": "no bye in the cards", "text": ""},
        {"turn": 9, "text": "out of range"}]}))
    out = sp.check_script(client, "m", "TOPIC: Bears", turns, "today")
    assert [t["text"] for t in out] == ["Illinois hosts Oregon tomorrow.",
                                        "See you tomorrow."]
    assert "1. [speaker 1] The Bears host Oregon" in \
        client.calls[0]["messages"][0]["content"]
    assert turns[0]["text"] == "The Bears host Oregon tomorrow."  # not mutated


def test_check_script_failure_keeps_the_script():
    turns = [{"speaker": 1, "text": "Hi."}]
    assert sp.check_script(_OneReply(RuntimeError("down")), "m", "x", turns,
                           "t") == turns
    assert sp.check_script(_OneReply("no json"), "m", "x", turns, "t") == turns


def test_calendar_names_the_real_weekdays():
    cal = sp.calendar("2026-10-23 (Friday)")
    assert cal.startswith("CALENDAR (use it for every weekday, past or "
                          "upcoming): Tue Oct 13;")
    assert "Sun Oct 18" in cal and "Fri Oct 23 (TODAY)" in cal
    assert "Wed Oct 28" in cal and "Sun Nov 1" in cal


def test_ask_uses_effort_on_current_models_and_temperature_on_old_ones():
    class Block:
        def __init__(self, kind, text=""):
            self.type, self.text = kind, text

    class Client:
        def __init__(self, stop="end_turn"):
            self.calls, self.stop = [], stop
            self.messages = self
            self.beta = self

        def create(self, **kw):
            self.calls.append(kw)
            stop = self.stop

            class R:
                stop_reason = stop
                content = [Block("thinking"), Block("text", "Hello "),
                           Block("text", "Sam.")]
            return R()

    c = Client()
    assert sp._ask(c, "claude-opus-5-5", "sys", [], effort="high") == "Hello Sam."
    kw = c.calls[-1]
    assert kw["output_config"] == {"effort": "high"} and "temperature" not in kw
    assert kw["fallbacks"] == "default"
    sp._ask(c, "claude-sonnet-4-5", "sys", [], temperature=0.4)
    kw = c.calls[-1]
    assert kw["temperature"] == 0.4 and "output_config" not in kw
    try:
        sp._ask(Client(stop="refusal"), "claude-opus-5-5", "sys", [])
    except RuntimeError as e:
        assert "declined" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


def test_audio_tags_survive_parsing_and_leave_the_transcript():
    raw = json.dumps({"turns": [{"speaker": 1, "text":
                                 "[Excited] Big night! [whispers] **Huge** win [laughs]."}]})
    assert sp.parse_turns(raw) == [
        {"speaker": 1, "text": "[excited] Big night! Huge win [laughs]."}]
    assert sp.clean_text("[cheerfully] Hi [pause] there *now*") == \
        "[cheerfully] Hi there now"
    assert sp.strip_tags("AL: [excited] Big night! Huge win [laughs].") == \
        "AL: Big night! Huge win."


def test_voice_settings_default_to_expressive():
    assert sp.voice_settings({}) == {"stability": 0.0}
    assert sp.voice_settings({"sams_podcast_stability": 0.5}) == \
        {"stability": 0.5}
    assert sp.voice_settings({"sams_podcast_stability": None}) is None


def test_synthesize_sends_settings_and_drops_them_if_rejected(monkeypatch,
                                                              tmp_path):
    import kirsten_podcast as kp
    bodies = []

    class R:
        def __init__(self, code):
            self.status_code, self.content = code, b"MP3"
            self.text = "bad stability" if code == 400 else ""

    def fake_post(url, **kw):
        bodies.append(dict(kw["json"]))
        return R(400 if "settings" in kw["json"] else 200)

    monkeypatch.setattr(kp.requests, "post", fake_post)
    kp.synthesize("K", [{"speaker": 1, "text": "Hi."}], ["v1", "v2"],
                  tmp_path / "a.mp3", settings={"stability": 0.0})
    assert bodies[0]["settings"] == {"stability": 0.0}
    assert "settings" not in bodies[1] and len(bodies) == 2
    bodies.clear()
    kp.synthesize("K", [{"speaker": 1, "text": "Hi."}], ["v1", "v2"],
                  tmp_path / "b.mp3")                     # Kirsten: unchanged
    assert bodies == [{"inputs": [{"text": "Hi.", "voice_id": "v1"}],
                       "model_id": kp.ELEVEN_MODEL}]


def test_style_guide_has_a_pace_section_and_the_core_allows_the_tags():
    assert "## Pace" in sp.DEFAULT_STYLE and "[excited]" in sp.DEFAULT_STYLE
    core = sp.script_prompt("x", ["Al", "Sophia"], "Sam Cowin")
    assert "[excited] [cheerfully] [laughs] [chuckles]" in core
    assert "no bracketed audio tags" not in core


STYLE_FILE = """---
host1: Max
voice1: Abcdefghij0123456789
stability: 0.7
audio_tags: excited, whispers
target_words: 900
max_words: 1000
model: claude-sonnet-5-5
---
<!-- a note for James -->
# Guide

## Pace
Slow it down, {host1}.

## Topic cards
- Two developments at most.

## Case cards
- Status only.
"""


def test_style_file_settings_win_over_config(tmp_path):
    (tmp_path / "PODCAST_STYLE.md").write_text(STYLE_FILE, encoding="utf-8")
    style = sp.load_style({}, tmp_path)
    cfg = sp.apply_settings({"sams_podcast_voices": ["OLDvoice0123456789ab",
                                                     "OLDvoice2123456789ab"],
                             "sams_podcast_stability": 0.0}, style["settings"])
    voices, hosts = sp._settings(cfg)
    assert voices == ["Abcdefghij0123456789", "OLDvoice2123456789ab"]
    assert hosts == ["Max", "Sophia"]
    assert sp.voice_settings(cfg) == {"stability": 0.7}
    assert sp.word_limits(cfg) == (900, 1000)
    assert sp.audio_tags(cfg) == ["excited", "whispers"]
    assert cfg["sams_podcast_model"] == "claude-sonnet-5-5"


def test_style_file_sections_split_and_comments_dropped(tmp_path):
    (tmp_path / "PODCAST_STYLE.md").write_text(STYLE_FILE, encoding="utf-8")
    style = sp.load_style({}, tmp_path)
    assert style["script"] == "# Guide\n\n## Pace\nSlow it down, {host1}."
    assert style["topic"] == "- Two developments at most."
    assert style["case"] == "- Status only."
    prompt = sp.card_prompt(sp.TOPIC_CORE, style["topic"], "topic", "Sam Cowin")
    assert "CARD STYLE (how many items, how long, what to favor)\n- Two " \
        "developments at most." in prompt
    assert "a daily audio show for Sam Cowin" in prompt
    assert "{" not in sp.card_prompt(sp.CASE_CORE, style["case"], "case",
                                     "Sam Cowin").replace('{"', "").replace(
                                         "{\n", "")


def test_bad_settings_fall_back_and_missing_card_sections_use_defaults(tmp_path):
    (tmp_path / "PODCAST_STYLE.md").write_text(
        "---\nvoice1: not a voice!\nstability: 3\nmax_words: lots\n"
        "colour: blue\ntarget_words: 1200\n---\n## Pace\nQuick.\n",
        encoding="utf-8")
    style = sp.load_style({}, tmp_path)
    assert style["settings"] == {"sams_podcast_target_words": 1200}
    cfg = sp.apply_settings({"sams_podcast_stability": 0.5}, style["settings"])
    assert sp._settings(cfg)[0][0] == "Q1QcmfZPmFDVUWmzASdy"     # default kept
    assert sp.voice_settings(cfg) == {"stability": 0.5}          # config kept
    assert sp.word_limits(cfg) == (1200, 1600)
    assert style["topic"].startswith("- **Headline:**")          # defaults
    assert style["case"].startswith("- **Status:**")


def test_max_under_target_is_raised_and_tags_can_be_turned_off():
    cfg = sp.apply_settings({}, {"sams_podcast_target_words": 1800,
                                 "sams_podcast_max_words": 1000,
                                 "sams_podcast_audio_tags": []})
    assert sp.word_limits(cfg) == (1800, 2000)
    assert sp.audio_tags(cfg) == []
    prompt = sp.script_prompt("x", ["Al", "Sophia"], "Sam Cowin", 1800, 2000,
                              tags=[])
    assert "No bracketed text at all." in prompt and "At most 2,000" in prompt
    assert sp.parse_turns('{"turns": [{"speaker": 1, "text": "[excited] Hi"}]}',
                          tags=[]) == [{"speaker": 1, "text": "Hi"}]
    assert sp.parse_turns('{"turns": [{"speaker": 1, "text": '
                          '"[whispers] Hi [excited]"}]}', tags=["whispers"]) \
        == [{"speaker": 1, "text": "[whispers] Hi"}]


def test_intro_timing_comes_from_settings(tmp_path, monkeypatch):
    from array import array
    seen = {}

    def fake_mix(music, speech, seconds, fade, overlap, rate=44100):
        seen.update(seconds=seconds, fade=fade, overlap=overlap)
        return array("h", [0])

    monkeypatch.setattr(sp, "_pcm", lambda path, rate=44100: array("h", [1]))
    monkeypatch.setattr(sp, "mix_intro", fake_mix)
    monkeypatch.setattr(sp, "encode_mp3", lambda samples: b"M")
    mp3 = tmp_path / "s.mp3"
    mp3.write_bytes(b"H")
    sp.add_intro({"sams_podcast_intro_seconds": 30, "sams_podcast_intro_fade": 6,
                  "sams_podcast_intro_overlap": 1}, tmp_path / "i.wav", mp3)
    assert seen == {"seconds": 30, "fade": 6, "overlap": 1}


def test_default_style_settings_block_parses_clean():
    style = sp.load_style({}, None)
    assert set(style["settings"]) == {
        "host1", "voice1", "host2", "voice2", "sams_podcast_listener",
        "sams_podcast_stability", "sams_podcast_intro_seconds",
        "sams_podcast_intro_fade", "sams_podcast_intro_overlap",
        "sams_podcast_target_words", "sams_podcast_max_words",
        "sams_podcast_audio_tags", "sams_podcast_model"}
    assert "---" not in style["script"] and "voice1" not in style["script"]
