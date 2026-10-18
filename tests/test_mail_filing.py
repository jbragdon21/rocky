"""Every process that reads rocky@'s Inbox files an email into an Inbox
subfolder once it has finished with it. Graph is mocked.

    python -m pytest tests/test_mail_filing.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rocky  # noqa: E402


def _patch_move(monkeypatch, ok=True, token="tok"):
    moved = []
    monkeypatch.setattr(rocky, "_MAIL_MOVE_TOKEN", {})
    monkeypatch.setattr(rocky, "acquire_mail_move_token", lambda c: token)
    monkeypatch.setattr(
        rocky, "file_message_to_inbox_subfolder",
        lambda tok, mbx, mid, name, cache=None:
        moved.append((mbx, mid, name)) or ok)
    return moved


# --- Shared helper --------------------------------------------------------

def test_file_processed_mail_default_override_and_off(monkeypatch):
    moved = _patch_move(monkeypatch)
    assert rocky.file_processed_mail({}, "r@x", "m1", "k", "PACER")
    assert rocky.file_processed_mail({"k": "Other"}, "r@x", "m2", "k", "PACER")
    assert not rocky.file_processed_mail({"k": ""}, "r@x", "m3", "k", "PACER")
    assert not rocky.file_processed_mail({}, "r@x", None, "k", "PACER")
    assert moved == [("r@x", "m1", "PACER"), ("r@x", "m2", "Other")]


def test_file_processed_mail_without_token_leaves_mail(monkeypatch):
    moved = _patch_move(monkeypatch, token=None)
    assert not rocky.file_processed_mail({}, "r@x", "m1", "k", "PACER")
    assert moved == []


# --- PACER requests -------------------------------------------------------

def _pacer_env(monkeypatch, tmp_path, sent):
    import pacer_monitor as pm
    import outbound
    import vault
    import anthropic
    paths = {"mail_state": tmp_path / "mail_state.json",
             "activity": tmp_path / "activity.jsonl", "local": tmp_path}
    msgs = [
        {"id": "p1", "subject": "PACER: pull docket 1:26-cv-1",
         "receivedDateTime": "2026-10-18T10:00:00Z",
         "from": {"emailAddress": {"address": "kvirtue@gallagherllp.com"}},
         "body": {"content": "please"}},
        {"id": "x1", "subject": "lunch?",
         "receivedDateTime": "2026-10-18T10:05:00Z",
         "from": {"emailAddress": {"address": "kvirtue@gallagherllp.com"}}},
    ]
    monkeypatch.setattr(pm, "ensure_dirs", lambda p: None)
    monkeypatch.setattr(rocky, "acquire_app_token", lambda c: "app")
    monkeypatch.setattr(vault, "fetch_inbox_messages", lambda *a, **k: msgs)
    monkeypatch.setattr(anthropic, "Anthropic", lambda **k: None)
    monkeypatch.setattr(pm, "make_cl_client", lambda *a, **k: None)
    monkeypatch.setattr(pm, "_classify_mail",
                        lambda *a: {"intent": "docket", "confidence": 0.9})
    monkeypatch.setattr(pm, "_handle_mail_intent",
                        lambda *a: {"body": "here you go"})
    monkeypatch.setattr(pm, "rebuild_index", lambda p: None)
    monkeypatch.setattr(outbound, "send_mail_guarded",
                        lambda *a, **k: {"sent": sent})
    return pm, paths


def test_pacer_request_filed_after_reply(monkeypatch, tmp_path):
    moved = _patch_move(monkeypatch)
    pm, paths = _pacer_env(monkeypatch, tmp_path, sent=True)
    counts = pm.run_mail({"anthropic_api_key": "k"}, paths)
    assert counts["filed"] == 1
    # Only the request, never unrelated mail.
    assert moved == [("rocky@gallagherllp.com", "p1", "PACER")]


def test_pacer_request_kept_when_reply_fails(monkeypatch, tmp_path):
    moved = _patch_move(monkeypatch)
    pm, paths = _pacer_env(monkeypatch, tmp_path, sent=False)
    pm.run_mail({"anthropic_api_key": "k"}, paths)
    assert moved == []


# --- MF digest replies ----------------------------------------------------

class _Resp:
    status_code = 200

    def __init__(self, value):
        self._value = value

    def json(self):
        return {"value": self._value}


def test_mf_digest_files_answered_replies_only(monkeypatch, tmp_path):
    import requests
    import mf_digest
    import mf_brain
    moved = _patch_move(monkeypatch)
    replies = [
        {"id": "a1", "from": {"emailAddress": {
            "address": "jbragdon@gallagherllp.com"}}, "body": {"content": "x"}},
        {"id": "a2", "from": {"emailAddress": {
            "address": "jbragdon@gallagherllp.com"}}, "body": {"content": ""}},
        {"id": "a3", "from": {"emailAddress": {
            "address": "someone@else.com"}}, "body": {"content": "x"}},
    ]
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(replies))
    monkeypatch.setattr(mf_digest, "parse_answers",
                        lambda body: {"Q1": "yes"} if body else {})
    monkeypatch.setattr(mf_brain, "read_jsonl", lambda p: [])
    monkeypatch.setattr(mf_brain, "append_jsonl", lambda p, rows: None)
    monkeypatch.setattr(mf_digest, "_fold_into_learning", lambda *a: None)
    paths = {"answers": tmp_path / "a.jsonl", "questions": tmp_path / "q.jsonl"}

    mf_digest.collect_answers({}, paths, "tok", dry_run=True)
    assert moved == []
    mf_digest.collect_answers({}, paths, "tok")
    assert moved == [("rocky@gallagherllp.com", "a1", "MF Digest")]
