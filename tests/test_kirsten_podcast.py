"""Kirsten Podcast: script parsing and length guard, ElevenLabs request
chunking, the cards the script is built from, the privilege opener, the
audio-live header in the report, and publishing to Transistor.

    python -m pytest tests/test_kirsten_podcast.py -q
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kirsten_digest as kd  # noqa: E402
import kirsten_podcast as kp  # noqa: E402

CARDS = [
    ("Bulgin", "EDVA", {"status": "MTD pending.", "week": ["Filed the MTD."],
                        "next_steps": ["Nov. 19 — opposition due."]}),
    ("Hunt", "", {"status": "Stayed.", "week": [],
                  "next_steps": ["No action needed at this time."]}),
    ("HRI", "", {"error": "timeout"}),
]


def test_cards_text_skips_failed_cards_and_marks_quiet_ones():
    text = kp.cards_text(CARDS)
    assert "CASE: Bulgin — EDVA" in text and "- Filed the MTD." in text
    assert "No significant developments this week." in text
    assert "HRI" not in text
    assert kp.cards_text([CARDS[2]]) == ""
    later = kp.cards_text(CARDS, "No significant developments since our last report.")
    assert "- No significant developments since our last report." in later


def test_parse_turns_cleans_tags_and_speakers():
    raw = ('Here you go: {"turns": [{"speaker": 1, "text": "[cheerful] Welcome '
           'to the **week** update."}, {"speaker": "2", "text": "Thanks."}, '
           '{"speaker": 7, "text": "  "}, {"speaker": null, "text": "Odd."}]}')
    assert kp.parse_turns(raw) == [
        {"speaker": 1, "text": "Welcome to the week update."},
        {"speaker": 2, "text": "Thanks."},
        {"speaker": 1, "text": "Odd."}]
    assert kp.parse_turns("no json") == []


def test_fit_keeps_order_and_sign_off():
    turns = [{"speaker": 1 + i % 2, "text": "word " * 100} for i in range(9)]
    turns.append({"speaker": 2, "text": "That's the week. Goodbye."})
    out = kp.fit(turns, max_words=420)
    assert kp.word_count(out) <= 420
    assert out[-1]["text"] == "That's the week. Goodbye."
    assert out[:-1] == turns[:len(out) - 1]
    short = turns[:2]
    assert kp.fit(short, max_words=420) == short


def test_chunk_respects_request_cap():
    inputs = [{"text": "a" * 800, "voice_id": "v1"},
              {"text": "b" * 800, "voice_id": "v2"},
              {"text": "c" * 800, "voice_id": "v1"}]
    batches = kp.chunk(inputs, cap=1900)
    assert [len(b) for b in batches] == [2, 1]
    assert all(sum(len(p["text"]) for p in b) <= 1900 for b in batches)


def test_chunk_splits_an_overlong_turn_at_sentences():
    long_turn = " ".join(f"Sentence number {i} is here." for i in range(120))
    batches = kp.chunk([{"text": long_turn, "voice_id": "v1"}], cap=500)
    pieces = [p for b in batches for p in b]
    assert all(len(p["text"]) <= 500 for p in pieces)
    assert " ".join(p["text"] for p in pieces) == long_turn
    assert all(p["voice_id"] == "v1" for p in pieces)


def test_synthesize_joins_parts(monkeypatch, tmp_path):
    calls = []

    class Resp:
        status_code = 200
        content = b"\xff\xfb" * 4000

    def fake_post(url, params, headers, json, timeout):
        calls.append((params, json))
        return Resp()

    monkeypatch.setattr(kp.requests, "post", fake_post)
    turns = [{"speaker": 1, "text": "x" * 1500}, {"speaker": 2, "text": "y" * 1500}]
    out = tmp_path / "pod.mp3"
    kp.synthesize("key", turns, ["v1", "v2"], out)
    assert len(calls) == 2
    assert calls[0][0] == {"output_format": kp.OUTPUT_FORMAT}
    assert calls[0][1]["inputs"] == [{"text": "x" * 1500, "voice_id": "v1"}]
    assert calls[1][1]["inputs"][0]["voice_id"] == "v2"
    assert calls[0][1]["model_id"] == "eleven_v3"
    assert out.read_bytes() == Resp.content * 2


def test_enabled_needs_key_and_respects_off_switch():
    assert not kp.enabled({})
    assert kp.enabled({"elevenlabs_api_key": "k"})
    assert not kp.enabled({"elevenlabs_api_key": "k", "kirsten_podcast": False})


def test_report_header_says_audio_is_live_only_when_published():
    args = (CARDS[:2], "Week of October 12 – 18, 2026", datetime(2026, 10, 18, 18))
    plain = kd.render_report(*args)
    assert kd.AUDIO_LIVE not in plain and "attached" not in plain
    live = kd.render_report(*args, audio_live=True)
    assert "now live in your private podcast feed" in live
    # The banner sits above the cards, not in the footer.
    assert live.index(kd.AUDIO_LIVE) < live.index("Bulgin")


def test_email_and_podcast_open_with_the_privilege_notice():
    html = kd.render_report(CARDS[:2], "Week", datetime(2026, 10, 18, 18))
    assert ("This email is a private attorney-client communication and is "
            "protected by the attorney-client and work product privileges.") in html
    assert html.index(kd.PRIVILEGE_EMAIL) < html.index("Bulgin")
    assert kp.PRIVILEGE == ("This podcast is a private attorney-client "
                            "communication and is protected by the "
                            "attorney-client and work product privileges.")


def test_show_notes_carry_privilege_and_cards():
    notes = kp.show_notes(CARDS, "Week of October 12 – 18, 2026")
    assert notes.startswith(f"<p><em>{kp.PRIVILEGE}</em></p>")
    assert "<h3>Bulgin — EDVA</h3>" in notes and "<li>Filed the MTD.</li>" in notes
    assert "No significant developments this week." in notes      # Hunt
    assert "HRI" not in notes                                      # failed card


def test_can_publish_needs_key_and_show():
    assert not kp.can_publish({"transistor_api_key": "t"})
    assert not kp.can_publish({"kirsten_podcast_show_id": "9"})
    assert kp.can_publish({"transistor_api_key": "t", "kirsten_podcast_show_id": 9})


def test_publish_uploads_creates_and_publishes(monkeypatch, tmp_path):
    import sams_podcast as sp
    seen = []
    monkeypatch.setattr(sp, "upload_audio",
                        lambda key, path: (seen.append(("upload", key, path.name)), "https://a")[1])
    monkeypatch.setattr(sp, "create_episode",
                        lambda key, show, title, summary, notes, url, tr:
                        (seen.append(("create", show, title, summary, url, tr)), "77")[1])
    monkeypatch.setattr(sp, "publish_episode",
                        lambda key, ep: seen.append(("publish", ep)))
    mp3 = tmp_path / "2026-10-31.mp3"
    mp3.write_bytes(b"x")
    record = {}
    cfg = {"transistor_api_key": "t", "kirsten_podcast_show_id": "42"}
    assert kp.publish(cfg, mp3, "Report — October 31, 2026", "<p>n</p>",
                      "GEORGE: hi", record)
    assert record == {"episode_id": "77", "published": True}
    assert seen[0] == ("upload", "t", "2026-10-31.mp3")
    assert seen[1][1:3] == ("42", "Report — October 31, 2026")
    assert kp.PRIVILEGE in seen[1][3] and seen[1][5] == "GEORGE: hi"
    assert seen[2] == ("publish", "77")


def test_publish_failure_keeps_the_episode_for_a_retry(monkeypatch, tmp_path):
    import sams_podcast as sp
    monkeypatch.setattr(sp, "upload_audio", lambda key, path: "https://a")
    monkeypatch.setattr(sp, "create_episode", lambda *a: "77")

    def boom(key, ep):
        raise RuntimeError("Transistor publish: HTTP 500")
    monkeypatch.setattr(sp, "publish_episode", boom)
    mp3 = tmp_path / "e.mp3"
    mp3.write_bytes(b"x")
    record = {}
    cfg = {"transistor_api_key": "t", "kirsten_podcast_show_id": "42"}
    assert not kp.publish(cfg, mp3, "T", "", "", record)
    assert record == {"episode_id": "77"}
    # The retry publishes that episode without a second upload.
    uploads = []
    monkeypatch.setattr(sp, "upload_audio", lambda *a: uploads.append(a))
    monkeypatch.setattr(sp, "publish_episode", lambda key, ep: None)
    assert kp.publish(cfg, mp3, "T", "", "", record)
    assert not uploads and record["published"]


class _Claude:
    """Fake client: hands back the queued replies in order, records calls."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []
        self.messages = self
        self.beta = self

    def create(self, **kw):
        import json as _json
        self.calls.append(kw)
        reply = self.replies.pop(0)
        text = reply if isinstance(reply, str) else _json.dumps(reply)

        class R:
            stop_reason = "end_turn"
            content = [type("T", (), {"type": "text", "text": text})]
        return R()


def test_write_script_uses_opus_calendar_and_a_client_fact_check():
    client = _Claude(
        {"turns": [{"speaker": 1, "text": "Welcome to the week update."},
                   {"speaker": 2, "text": "James Bragdon filed the MTD."},
                   {"speaker": 1, "text": "Questions? Reply to the report."}]},
        {"fixes": [{"turn": 2, "problem": "names Gallagher staff",
                    "text": "The team filed the motion to dismiss."}]})
    turns = kp.write_script(client, "claude-opus-5-5", CARDS, "Week of Oct 19",
                            ["George", "Sarah"])
    assert [t["text"] for t in turns] == [
        kp.PRIVILEGE,
        "Welcome to the week update.", "The team filed the motion to dismiss.",
        "Questions? Reply to the report."]
    assert turns[0]["speaker"] == 1
    # The check never sees the notice, so it can't drop it.
    script_call, check_call = client.calls
    assert "temperature" not in script_call                 # Opus: effort
    assert script_call["output_config"]["effort"] == "medium"
    assert "CALENDAR (use it for every weekday" in \
        script_call["messages"][0]["content"]
    assert "Take every weekday from the CALENDAR" in script_call["system"]
    assert check_call["system"] == kp.CHECK_PROMPT
    assert check_call["output_config"]["effort"] == "high"
    assert "names a Gallagher attorney or staff member" in kp.CHECK_PROMPT
    assert kp.PRIVILEGE not in check_call["messages"][0]["content"]
    assert "do not repeat it" in script_call["system"]


def test_make_rebuilds_the_glued_audio_as_one_stream(monkeypatch, tmp_path):
    import math
    from array import array
    import miniaudio
    import sams_podcast as sp
    tone = array("h", (int(5000 * math.sin(2 * math.pi * 220 * k / 44100))
                       for k in range(44100 * 2)))
    piece = sp.encode_mp3(tone)

    def fake_synth(key, turns, voices, out, *a, parts=None, **kw):
        parts.extend([piece, piece, piece])
        out.write_bytes(piece * 3)
        return 6.0

    monkeypatch.setattr(kp, "synthesize", fake_synth)
    monkeypatch.setattr(kp, "write_script", lambda *a, **kw: [
        {"speaker": 1, "text": "Hello."}, {"speaker": 2, "text": "Bye."}])
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda api_key: object())
    mp3 = kp.make({"anthropic_api_key": "k", "elevenlabs_api_key": "e"},
                  CARDS, "Week", tmp_path / "w.mp3", tmp_path / "w.txt")
    assert mp3 == tmp_path / "w.mp3"
    assert abs(miniaudio.mp3_get_file_info(str(mp3)).duration - 6) < 0.3


def test_default_models_are_opus():
    import sams_podcast as sp
    assert sp.DEFAULT_MODEL == "claude-opus-5-5"
