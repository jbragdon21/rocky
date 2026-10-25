"""Kirsten Podcast: script parsing and length guard, ElevenLabs request
chunking, the cards the script is built from, the audio note in the
report, and the draft attachment paths.

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


def test_report_footer_mentions_audio_only_when_attached():
    args = (CARDS[:2], "Week of October 12 – 18, 2026", datetime(2026, 10, 18, 18))
    assert "audio summary" not in kd.render_report(*args)
    assert "An audio summary of this report is attached." in \
        kd.render_report(*args, audio=True)


def test_attach_small_file_posts_once(monkeypatch, tmp_path):
    seen = []

    class Resp:
        status_code = 201
        text = ""

    monkeypatch.setattr(kp.requests, "post",
                        lambda url, **kw: (seen.append((url, kw["json"])), Resp())[1])
    mp3 = tmp_path / "2026-10-25.mp3"
    mp3.write_bytes(b"\x00" * 1000)
    assert kp.attach("tok", "j@x.com", "MSG", mp3)
    url, body = seen[0]
    assert url.endswith("/users/j@x.com/messages/MSG/attachments")
    assert body["name"] == "2026-10-25.mp3" and body["contentType"] == "audio/mpeg"


def test_attach_large_file_uses_upload_session(monkeypatch, tmp_path):
    puts = []

    class Created:
        status_code = 201
        text = ""

        @staticmethod
        def json():
            return {"uploadUrl": "https://upload"}

    class Done:
        status_code = 200
        text = ""

    monkeypatch.setattr(kp.requests, "post", lambda url, **kw: Created())
    monkeypatch.setattr(kp.requests, "put",
                        lambda url, data, timeout, headers: (puts.append(headers), Done())[1])
    mp3 = tmp_path / "big.mp3"
    size = 7 * 1024 * 1024
    mp3.write_bytes(b"\x00" * size)
    assert kp.attach("tok", "j@x.com", "MSG", mp3)
    assert [h["Content-Range"] for h in puts] == [
        f"bytes 0-3145727/{size}", f"bytes 3145728-6291455/{size}",
        f"bytes 6291456-{size - 1}/{size}"]


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
        "Welcome to the week update.", "The team filed the motion to dismiss.",
        "Questions? Reply to the report."]
    script_call, check_call = client.calls
    assert "temperature" not in script_call                 # Opus: effort
    assert script_call["output_config"]["effort"] == "medium"
    assert "CALENDAR (use it for every weekday" in \
        script_call["messages"][0]["content"]
    assert "Take every weekday from the CALENDAR" in script_call["system"]
    assert check_call["system"] == kp.CHECK_PROMPT
    assert check_call["output_config"]["effort"] == "high"
    assert "names a Gallagher attorney or staff member" in kp.CHECK_PROMPT


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
