"""Joe's Email Archive, steps 1 and 2: finding a way into the mailbox, the
backfill (checkpoints, dedup, skips, failures) and the search. No network:
Graph is a stand-in that serves pages and writes .eml files.

    python -m pytest tests/test_joe_archive.py -q
"""

import email.policy
import json
import sys
from email.message import EmailMessage
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from joe_archive import cli, graph, ingest, search, store  # noqa: E402
from joe_archive.graph import GraphError  # noqa: E402

MB = "jdugan@gallagherllp.com"


def person(addr, name=""):
    return {"emailAddress": {"address": addr, "name": name}}


def gmsg(i, sent, subject="Status", body="Hello there.", to=("kwoelper@bozzuto.com",),
         cc=(), mclass="IPM.Note", imid=True, odata="#microsoft.graph.message"):
    m = {"id": f"g{i}", "subject": subject, "conversationId": f"c{i}",
         "sentDateTime": sent, "hasAttachments": False, "@odata.type": odata,
         "from": person(MB, "Joseph Dugan"),
         "toRecipients": [person(a, a.split("@")[0].title()) for a in to],
         "ccRecipients": [person(a) for a in cc], "bccRecipients": [],
         "body": {"contentType": "text", "content": body},
         "singleValueExtendedProperties": [{"id": graph.MESSAGE_CLASS, "value": mclass}]}
    if imid:
        m["internetMessageId"] = f"<im{i}@gallagherllp.com>"
    return m


def eml_bytes(subject="Status", with_attachments=False) -> bytes:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = MB
    msg["To"] = "kwoelper@bozzuto.com"
    msg.set_content("Plain body.")
    if with_attachments:
        msg.add_alternative("<p>Html <img src='cid:logo'></p>", subtype="html")
        html = msg.get_payload()[1]
        html.add_related(b"\x89PNG", maintype="image", subtype="png", cid="<logo>",
                         filename="logo.png", disposition="inline")
        msg.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf",
                           filename="Lease Addendum.pdf")
        inner = EmailMessage()
        inner["Subject"] = "Signed estoppel"
        inner.set_content("see attached")
        inner.add_attachment(b"zz", maintype="application", subtype="pdf",
                             filename="nested.pdf")
        msg.add_attachment(inner)
    return msg.as_bytes(policy=email.policy.default)


# =============================================================================
# store
# =============================================================================

def test_split_body_cuts_at_the_history():
    own, quoted = store.split_body(
        "Kirsten,\n\nWe will file Monday.\n\n\n\nJoe\n\nFrom: Kirsten Woelper\nSent: Mon\n"
        "Subject: RE: Hall\n\nCan you file?")
    assert own == "Kirsten,\n\nWe will file Monday.\n\nJoe"
    assert quoted.startswith("From: Kirsten Woelper") and quoted.endswith("Can you file?")


def test_split_body_forward_with_nothing_typed_is_all_history():
    own, quoted = store.split_body("From: Someone\nSent: Tuesday\n\nThe notice.")
    assert own == "" and "The notice." in quoted


def test_split_body_no_history():
    assert store.split_body("Just this.") == ("Just this.", "")


def test_attachment_names_skips_inline_images_and_nested_files(tmp_path):
    p = tmp_path / "m.eml"
    p.write_bytes(eml_bytes(with_attachments=True))
    assert store.attachment_names(p) == ["Lease Addendum.pdf", "Signed estoppel.eml"]


def test_attachment_names_tolerates_junk(tmp_path):
    p = tmp_path / "bad.eml"
    p.write_bytes(b"\x00\x01 not mime")
    assert store.attachment_names(p) == []
    assert store.attachment_names(tmp_path / "missing.eml") == []


def test_msg_key_prefers_internet_message_id():
    assert store.msg_key(gmsg(1, "2026-01-02T15:00:00Z")) == "<im1@gallagherllp.com>"
    assert store.msg_key(gmsg(2, "2026-01-02T15:00:00Z", imid=False)) == "graph:g2"


def test_eml_relpath_is_dated_and_stable():
    m = gmsg(1, "2026-03-04T05:06:07Z")
    rel = store.eml_relpath(m)
    assert rel.startswith("eml/2026/03/20260304-050607_") and rel.endswith(".eml")
    assert rel == store.eml_relpath(dict(m, id="moved"))   # keyed on internetMessageId


def test_insert_dedups_and_indexes(tmp_path):
    conn = store.open_db(tmp_path / "a.db")
    row = store.build_row(gmsg(1, "2026-01-02T15:00:00Z", body="right of first refusal"),
                          "Sent Items", "IPM.Note", "eml/x.eml", 10, [])
    assert store.insert(conn, row) is True
    assert store.insert(conn, dict(row, graph_id="other")) is False
    conn.commit()
    hits = conn.execute("SELECT rowid FROM messages_fts WHERE messages_fts MATCH "
                        "'\"first refusal\"'").fetchall()
    assert len(hits) == 1


# =============================================================================
# search
# =============================================================================

@pytest.fixture
def archive(tmp_path):
    conn = store.open_db(tmp_path / "archive.db")
    rows = [
        gmsg(1, "2026-01-05T15:00:00Z", subject="Hall lease",
             body="We have a right of first refusal here.\n\nFrom: Kirsten\n\nindemnify us"),
        gmsg(2, "2026-02-10T15:00:00Z", subject="Indemnification",
             body="The indemnification clause is broad.", to=("sapire@gallagherllp.com",)),
        gmsg(3, "2026-11-02T03:30:00Z", subject="Late night",
             body="Filed the motion tonight.", cc=("jbragdon@gallagherllp.com",)),
        gmsg(4, "2026-06-01T15:00:00Z", subject="Refusal letter",
             body="Sending the letter.", to=("hall@example.com",)),
    ]
    for m in rows:
        names = ["Hall Lease.pdf"] if m["id"] == "g1" else []
        store.insert(conn, store.build_row(m, "Sent Items", "IPM.Note",
                                           store.eml_relpath(m), 100, names))
    conn.commit()
    yield conn
    conn.close()


def refs(result):
    return [h["ref"] for h in result["hits"]]


def test_phrase_search_is_exact(archive):
    assert refs(search.search(archive, '"right of first refusal"')) == [1]
    assert refs(search.search(archive, '"first right of refusal"')) == []


def test_prefix_finds_variants(archive):
    assert sorted(refs(search.search(archive, "indemnif*"))) == [1, 2]


def test_own_ignores_the_quoted_history(archive):
    assert sorted(refs(search.search(archive, "indemnify"))) == [1]
    assert refs(search.search(archive, "indemnify", fields="own")) == []
    assert sorted(refs(search.search(archive, "refusal", fields="own"))) == [1, 4]


def test_subject_outranks_body(archive):
    assert refs(search.search(archive, "refusal"))[0] == 4


def test_dates_are_eastern_days(archive):
    # 03:30 UTC on Nov 2 is 10:30 PM Nov 1 Eastern (standard time began that morning).
    assert refs(search.search(archive, "motion", date_to="2026-11-01")) == [3]
    assert refs(search.search(archive, "motion", date_from="2026-11-02")) == []
    hit = search.search(archive, "motion")["hits"][0]
    assert hit["sent"] == "2026-11-01 10:30 PM"


def test_recipient_and_subject_filters(archive):
    assert refs(search.search(archive, "", recipient="sapire")) == [2]
    assert refs(search.search(archive, "", recipient="Bragdon")) == [3]   # cc counts
    assert refs(search.search(archive, "letter", subject="refusal")) == [4]
    assert refs(search.search(archive, "", recipient="100%_")) == []      # LIKE escaped


def test_bad_syntax_is_read_literally(archive):
    result = search.search(archive, "refusal's")
    assert result["rewritten"] and result["query_used"] == '"refusal\'s"'
    result = search.search(archive, 'clause AND "broad')
    assert result["rewritten"] and refs(result) == [2]


def test_browse_lists_newest_first(archive):
    result = search.search(archive, "")
    assert result["total"] == 4 and refs(result) == [3, 4, 2, 1]


def test_attachment_names_are_searchable(archive):
    assert refs(search.search(archive, '"Hall Lease.pdf"')) == [1]


def test_search_rejects_bad_dates_and_orders(archive):
    with pytest.raises(search.SearchError):
        search.search(archive, "x", date_from="11/1/2026")
    with pytest.raises(search.SearchError):
        search.search(archive, "x", order="random")


def test_format_hits_and_message(archive):
    text = search.format_hits(search.search(archive, "refusal", limit=1), "refusal")
    assert text.startswith("2 matches for refusal; 1 shown, best first")
    assert "[4]" in text
    full = search.format_message(search.read_message(archive, 1))
    assert "Attachments: Hall Lease.pdf" in full and "---- quoted history ----" in full


def test_day_start_utc_tracks_dst():
    assert search.day_start_utc("2026-07-01") == "2026-07-01T04:00:00Z"
    assert search.day_start_utc("2026-12-01") == "2026-12-01T05:00:00Z"
    assert search.day_start_utc("2026-03-08") == "2026-03-08T05:00:00Z"   # DST starts 2 AM
    assert search.day_start_utc("2026-11-01") == "2026-11-01T04:00:00Z"   # DST ends 2 AM


# =============================================================================
# access
# =============================================================================

def test_find_access_tries_paths_in_order(monkeypatch):
    tokens = {"app": (None, "no client_secret in config"),
              "delegated-read": ("t1", "ok"), "delegated": ("t2", "ok")}
    monkeypatch.setattr(graph, "token_for", lambda config, path: tokens[path])

    def folder(keeper, mb, fid):
        if keeper.path == "delegated-read":
            raise GraphError(403, "ErrorAccessDenied: Access is denied.")
        return {"id": "SENT", "totalItemCount": 12}

    monkeypatch.setattr(graph, "folder", folder)
    path, keeper, mb, sent, attempts = ingest.find_access({}, MB)
    assert path == "delegated" and mb == MB and sent["totalItemCount"] == 12
    assert [(a["path"], a["ok"]) for a in attempts] == [
        ("app", False), ("delegated-read", False), ("delegated", True)]


def test_find_access_tries_the_legacy_domain_on_404(monkeypatch):
    monkeypatch.setattr(graph, "token_for", lambda config, path: ("t", "ok"))

    def folder(keeper, mb, fid):
        if mb.endswith("@gallagherllp.com"):
            raise GraphError(404, "ErrorInvalidUser")
        return {"id": "SENT"}

    monkeypatch.setattr(graph, "folder", folder)
    path, _k, mb, _s, _a = ingest.find_access({}, MB)
    assert path == "app" and mb == "jdugan@gejlaw.com"


def test_find_access_reports_when_nothing_works(monkeypatch):
    monkeypatch.setattr(graph, "token_for", lambda config, path: (None, "nope"))
    path, keeper, _mb, _s, attempts = ingest.find_access({}, MB)
    assert path is None and keeper is None and len(attempts) == 3


def test_config_auth_pins_one_path(monkeypatch, tmp_path):
    seen = []

    def find(config, mailbox, paths):
        seen.append(paths)
        return "app", "K", mailbox, {}, []

    monkeypatch.setattr(ingest, "find_access", find)
    state = {}
    ingest.resolve_access({"joe_archive_auth": "app"}, tmp_path, state)
    assert seen[-1] == ("app",) and state["auth"] == "app"
    ingest.resolve_access({}, tmp_path, {"auth": "delegated", "mailbox": MB})
    assert seen[-1][0] == "delegated"


# =============================================================================
# backfill
# =============================================================================

class FakeGraph:
    """Sent Items as a list, served oldest first in pages, honoring `since`."""

    def __init__(self, msgs, page=2):
        self.msgs = sorted(msgs, key=lambda m: m["sentDateTime"])
        self.page = page
        self.downloads = []
        self.fail = set()
        self.since_seen = []

    def iter_pages(self, keeper, mb, folder_id="sentitems", since=None, top=None,
                   max_pages=None):
        self.since_seen.append(since)
        rows = [m for m in self.msgs if not since or m["sentDateTime"] >= since]
        for i in range(0, len(rows), self.page):
            yield [dict(m) for m in rows[i:i + self.page]]

    def download_mime(self, keeper, mb, gid, dest):
        self.downloads.append(gid)
        if gid in self.fail:
            raise GraphError(500, "ErrorInternalServerError")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(eml_bytes(subject=gid, with_attachments=gid == "g1"))
        return dest.stat().st_size

    def message(self, keeper, mb, gid):
        return next((dict(m) for m in self.msgs if m["id"] == gid), None)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    msgs = [gmsg(1, "2026-01-01T10:00:00Z", body="alpha"),
            gmsg(2, "2026-01-02T10:00:00Z", body="bravo"),
            gmsg(3, "2026-01-03T10:00:00Z", subject="Read: Status",
                 mclass="REPORT.IPM.Note.IPNRN"),
            gmsg(4, "2026-01-04T10:00:00Z", subject="Accepted: Call",
                 mclass="IPM.Schedule.Meeting.Resp.Pos"),
            gmsg(5, "2026-01-05T10:00:00Z", body="echo"),
            gmsg(6, "2026-01-06T10:00:00Z", body="foxtrot")]
    g = FakeGraph(msgs)
    monkeypatch.setattr(graph, "iter_pages", g.iter_pages)
    monkeypatch.setattr(graph, "download_mime", g.download_mime)
    monkeypatch.setattr(graph, "message", g.message)
    monkeypatch.setattr(ingest, "resolve_access",
                        lambda config, root, state: (graph.TokenKeeper({}, "app", "t"), MB))
    return g


def run(root, **kw):
    lines = []
    result = ingest.backfill({}, root, out=lines.append, **kw)
    return result, lines


def test_backfill_archives_skips_and_finishes(fake, tmp_path):
    result, lines = run(tmp_path)
    assert result["archived"] == 4 and result["skipped"] == 2 and result["finished"]
    conn = store.open_db(tmp_path / "archive.db")
    rows = conn.execute("SELECT graph_id, attachment_names, eml_path FROM messages "
                        "ORDER BY sent_utc").fetchall()
    assert [r[0] for r in rows] == ["g1", "g2", "g5", "g6"]
    assert rows[0][1] == "Lease Addendum.pdf; Signed estoppel.eml"
    assert all((tmp_path / r[2]).exists() for r in rows)
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["backfill"]["cursor"] == "2026-01-06T10:00:00Z"
    assert state["backfill"]["complete_utc"]


def test_backfill_limit_then_resume(fake, tmp_path):
    result, lines = run(tmp_path, limit=1)
    assert result["archived"] == 1 and not result["finished"]
    assert any("run it again" in ln for ln in lines)
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["backfill"]["cursor"] == "2026-01-01T10:00:00Z"
    result, _ = run(tmp_path)
    assert result["archived"] == 3 and result["finished"]
    assert fake.since_seen[-1] == "2026-01-01T10:00:00Z"
    assert fake.downloads.count("g1") == 1       # resumed, not redone


def test_rerun_downloads_only_new_sends(fake, tmp_path):
    run(tmp_path)
    fake.downloads.clear()
    fake.msgs.append(gmsg(7, "2026-01-07T10:00:00Z", body="golf"))
    result, _ = run(tmp_path, overlap_days=2)
    assert fake.downloads == ["g7"] and result["archived"] == 1
    assert fake.since_seen[-1] == "2026-01-04T10:00:00Z"


def test_failure_is_recorded_then_retried(fake, tmp_path):
    fake.fail.add("g2")
    result, _ = run(tmp_path)
    assert result["failed"] == 1 and result["archived"] == 3
    conn = store.open_db(tmp_path / "archive.db")
    assert [tuple(r) for r in conn.execute("SELECT graph_id, tries FROM failures")] == \
        [("g2", 1)]
    fake.fail.clear()
    result, _ = run(tmp_path)
    assert result["archived"] == 1
    assert conn.execute("SELECT COUNT(*) FROM failures").fetchone()[0] == 0


def test_a_page_of_nothing_but_failures_stops_the_run(fake, tmp_path):
    fake.page = 6
    fake.fail.update({"g1", "g2", "g5", "g6"})
    result, lines = run(tmp_path)
    assert not result["finished"] and any("every download" in ln for ln in lines)
    assert "cursor" not in json.loads((tmp_path / "state.json").read_text())["backfill"]


def test_same_message_twice_on_a_page_is_archived_once(fake, tmp_path):
    fake.msgs.append(dict(gmsg(1, "2026-01-01T10:00:00Z"), id="g1-copy"))
    result, _ = run(tmp_path)
    assert result["archived"] == 4 and "g1-copy" not in fake.downloads


# =============================================================================
# cli
# =============================================================================

def test_cli_search_and_show(fake, tmp_path, capsys):
    run(tmp_path)
    root = ["--root", str(tmp_path)]
    assert cli.run_cli({}, tmp_path, ["--joe-archive", "--search", "bravo"] + root) == 0
    out = capsys.readouterr().out
    assert out.startswith("1 match for bravo") and "[2]" in out
    assert cli.run_cli({}, tmp_path, ["--joe-archive", "--show", "2"] + root) == 0
    assert "bravo" in capsys.readouterr().out
    assert cli.run_cli({}, tmp_path, ["--joe-archive", "--stats"] + root) == 0
    assert capsys.readouterr().out.startswith("4 sends archived")


def test_cli_without_an_archive(tmp_path, capsys):
    assert cli.run_cli({}, tmp_path, ["--joe-archive", "--search", "x",
                                      "--root", str(tmp_path / "none")]) == 1
    assert "No archive" in capsys.readouterr().out


def test_cli_rejects_unknown_options(tmp_path, capsys):
    assert cli.run_cli({}, tmp_path, ["--joe-archive", "--serch", "x"]) == 2


def test_default_root_stays_out_of_the_package(tmp_path):
    assert cli.get_root({}, tmp_path) == tmp_path / "joe_archive_data"
    assert cli.get_root({"joe_archive_root": "D:/x"}, tmp_path) == Path("D:/x")


# =============================================================================
# --check
# =============================================================================

def test_check_reports_access_size_and_filed_sends(fake, monkeypatch, tmp_path):
    keeper = graph.TokenKeeper({}, "delegated", "t")
    monkeypatch.setattr(ingest, "find_access", lambda config, mb: (
        "delegated", keeper, MB, {"totalItemCount": 6, "sizeInBytes": 3 * 1024 ** 3},
        [{"path": "app", "mailbox": MB, "ok": False, "note": "no client_secret in config"},
         {"path": "delegated", "mailbox": MB, "ok": True, "note": "reads Sent Items"}]))
    monkeypatch.setattr(ingest, "_first_sent", lambda k, mb, d:
                        "2019-04-01T12:00:00Z" if d == "asc" else "2026-11-03T12:00:00Z")
    monkeypatch.setattr(graph, "folder", lambda k, mb, name: {"id": name.upper()})
    monkeypatch.setattr(ingest, "walk_folders", lambda k, mb: [
        {"id": "SENTITEMS", "path": "Sent Items", "items": 6},
        {"id": "F1", "path": "Inbox/Bozzuto", "items": 900},
        {"id": "F2", "path": "Inbox/Empty", "items": 0},
        {"id": "F3", "path": "Clients/Hall", "items": 40}])
    counts = {"F1": 120, "F3": None}
    monkeypatch.setattr(ingest, "authored_count", lambda k, mb, fid, addrs: counts[fid])
    lines = []
    report = ingest.check({}, tmp_path, out=lines.append)
    text = "\n".join(lines)
    assert report["auth"] == "delegated"
    assert "Sent Items: 6 items, 3.0 GB" in text
    assert "sample classes: IPM.Note x" in text and "MIME download works" in text
    assert "120 of Joe's sends sit in 1 other folders (1 folders couldn't be counted)" in text
    assert "Inbox/Bozzuto" in text
    assert not (tmp_path / "_check").exists()          # the sample .eml is cleaned up
    saved = json.loads((tmp_path / "check.json").read_text())
    assert saved["filed"]["folders"] == [{"path": "Inbox/Bozzuto", "from_joe": 120,
                                          "items": 900}]
    assert json.loads((tmp_path / "state.json").read_text())["auth"] == "delegated"


def test_check_with_no_way_in_says_what_it_needs(monkeypatch, tmp_path):
    monkeypatch.setattr(ingest, "find_access", lambda config, mb: (
        None, None, MB, None,
        [{"path": p, "mailbox": MB, "ok": False, "note": "403"} for p in graph.AUTH_ORDER]))
    lines = []
    report = ingest.check({}, tmp_path, out=lines.append)
    assert report["auth"] is None
    assert any("Application Access Policy" in ln for ln in lines)
    assert any("Full Access" in ln for ln in lines)


def test_snippet_prefers_joes_own_text(archive):
    # Message 4 has "refusal" only in its subject, message 1 only in Joe's text.
    hits = {h["ref"]: h["snippet"] for h in search.search(archive, "refusal")["hits"]}
    assert "[refusal]" in hits[1] and hits[1].startswith("We have a right")
    assert hits[4] == "[Refusal] letter"
    both = search.search(archive, "lease")["hits"]
    assert both[0]["ref"] == 1 and "\x02" not in both[0]["snippet"]
