"""Litigation Updater chat: phone-readable proposals (markup -> Teams HTML)
and the follow session that runs after every --poll. Teams is mocked.

    python -m pytest tests/test_litigation_chat.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import litigation_updater as lu  # noqa: E402

LONG = " ".join(f"word{i}" for i in range(200)) + "."


# --- Formatting -----------------------------------------------------------

def test_clip_keeps_short_text_and_ellipsizes_long():
    assert lu._clip("a b c") == "a b c"
    clipped = lu._clip(LONG)
    assert clipped.endswith(" . . .") and len(clipped.split()) == 26 + 2


def test_teams_html_escapes_and_structures():
    html = lu._teams_html("**[L1] Title** <x>\nline two\n\n> quoted & kept\n"
                          "> second para\n\nReply **YES**")
    assert html.startswith("<p><b>[L1] Title</b> &lt;x&gt;<br>line two</p>")
    assert ("<blockquote>quoted &amp; kept<br><br>second para</blockquote>"
            in html)
    assert html.endswith("<p>Reply <b>YES</b></p>")


def test_length_proposal_quotes_new_text_and_clips_old():
    ask = {"id": "L0450", "kind": "cleanup_fix", "check": "length",
           "claim": "Jane Roe (The Apollo)", "column": "Summary of Claim",
           "current": LONG, "proposed": "Short and complete.",
           "reason": "Too long for the rules (201 words > 150). Compressed.",
           "dropped": "repair dates"}
    text = lu._proposal_text(ask)
    assert text.startswith("**[L0450] Shorten Summary of Claim**\n"
                           "Jane Roe (The Apollo)")
    assert "> Short and complete." in text
    assert "word24 . . ." in text and "word25" not in text
    assert "Removed: repair dates" in text
    # The long rule-breach reason isn't repeated on the phone.
    assert "Too long for the rules" not in text


def test_short_cleanup_fix_stays_inline():
    ask = {"id": "L0003", "kind": "cleanup_fix", "claim": "Arce",
           "column": "Type of Case", "current": "Discrimination (Housing)",
           "proposed": "Housing Discrimination", "reason": "vocabulary"}
    text = lu._proposal_text(ask)
    assert "Now: Discrimination (Housing)\nProposed: **Housing Discrimination**" in text
    assert "Why: vocabulary" in text


def test_update_proposal_shows_only_the_addition():
    prior = "3/1/2026 - Motion to dismiss filed."
    ask = {"id": "L0500", "kind": "update", "claim": "Roe",
           "cells": {"Status/Action Items":
                     "10/1/2026 - MTD denied; discovery opens.\n" + prior},
           "before": {"Status/Action Items": prior}}
    text = lu._proposal_text(ask)
    assert "> 10/1/2026 - MTD denied; discovery opens." in text
    assert "Motion to dismiss filed" not in text
    assert "earlier text unchanged" in text
    # An older ask without "before" still shows the full value.
    del ask["before"]
    assert "Motion to dismiss filed" in lu._proposal_text(ask)


def test_new_entry_puts_facts_on_their_own_lines():
    ask = {"id": "L0426", "kind": "new_entry", "sheet_key": "BMC",
           "classification": {"doc_label": "Complaint", "entity": "BMC",
                              "parties": "Tri-Tech v. BMC",
                              "court_or_forum": "Fairfax Circuit Court",
                              "case_number": "CL-2026-1", "summary": "S.",
                              "recommend_entry": True,
                              "recommend_reason": "active suit"}}
    text = lu._proposal_text(ask)
    assert ("Entity: BMC\nParties: Tri-Tech v. BMC\n"
            "Court: Fairfax Circuit Court\nCase No.: CL-2026-1") in text
    assert "\n\n> S.\n\n" in text
    assert text.endswith("to the BMC sheet, **NO** to skip.")


# --- Follow after poll ----------------------------------------------------

class _Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _patch_follow(monkeypatch, tmp_path, results, asks):
    clock = _Clock()
    monkeypatch.setattr(lu.time, "time", clock.time)
    monkeypatch.setattr(lu.time, "sleep", clock.sleep)
    paths = {"state": tmp_path / "state.json"}
    lu.save_state(paths, {"asks": asks})
    calls = []

    def fake_cycle(config, p, dry_run):
        calls.append(clock.t)
        return results(len(calls))
    monkeypatch.setattr(lu, "chat_cycle", fake_cycle)
    return paths, calls, clock


def test_follow_idle_exit_with_asks_still_queued(monkeypatch, tmp_path):
    paths, calls, clock = _patch_follow(
        monkeypatch, tmp_path, lambda n: {"inbound": 0},
        [{"status": "proposed"}])
    lu.follow_chat({}, paths, False, minutes=45, idle_minutes=10)
    # ~10 minutes of 15-second checks, then out — not the full 45.
    assert 39 <= len(calls) <= 41


def test_follow_stays_while_james_is_replying(monkeypatch, tmp_path):
    # A reply every 5 minutes for the first 30 minutes, then silence.
    paths, calls, clock = _patch_follow(
        monkeypatch, tmp_path,
        lambda n: {"inbound": 1 if n % 20 == 0 and n <= 120 else 0},
        [{"status": "queued"}])
    lu.follow_chat({}, paths, False, minutes=45, idle_minutes=10)
    elapsed_min = (calls[-1] - calls[0]) / 60
    assert 39 <= elapsed_min <= 41


def test_poll_dispatch_follows_only_when_asks_wait(monkeypatch, tmp_path):
    paths = {"state": tmp_path / "state.json"}
    followed = []
    monkeypatch.setattr(lu, "run_poll", lambda *a, **k: None)
    monkeypatch.setattr(lu, "follow_chat",
                        lambda *a, **k: followed.append(k))
    monkeypatch.setattr(sys, "argv", ["rocky.py", "--litigation", "--poll"])

    lu.save_state(paths, {"asks": [{"status": "done"}]})
    lu._dispatch({}, paths, dry_run=False)
    assert followed == []

    lu.save_state(paths, {"asks": [{"status": "queued"}]})
    lu._dispatch({}, paths, dry_run=False)
    assert followed == [{"idle_minutes": 10}]

    lu._dispatch({}, paths, dry_run=True)
    monkeypatch.setattr(sys, "argv", ["rocky.py", "--litigation", "--poll",
                                      "--no-follow"])
    lu._dispatch({}, paths, dry_run=False)
    lu._dispatch({"litigation_follow_after_poll": False}, paths, False)
    assert len(followed) == 1
