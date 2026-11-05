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

from joe_archive import census, cli, graph, ingest, search, store  # noqa: E402
from joe_archive.graph import GraphError  # noqa: E402

MB = "jdugan@gallagherllp.com"


def person(addr, name=""):
    return {"emailAddress": {"address": addr, "name": name}}


def gmsg(i, sent, subject="Status", body="Hello there.", to=("kwoelper@bozzuto.com",),
         cc=(), mclass="IPM.Note", imid=True, odata="#microsoft.graph.message",
         prop_id="String 0x1a"):
    # prop_id is Graph's own short form, not the request's "String 0x001A":
    # fixtures built from graph.MESSAGE_CLASS hid the 11/5 blank-class bug.
    m = {"id": f"g{i}", "subject": subject, "conversationId": f"c{i}",
         "sentDateTime": sent, "hasAttachments": False, "@odata.type": odata,
         "from": person(MB, "Joseph Dugan"),
         "toRecipients": [person(a, a.split("@")[0].title()) for a in to],
         "ccRecipients": [person(a) for a in cc], "bccRecipients": [],
         "body": {"contentType": "text", "content": body},
         "singleValueExtendedProperties": [{"id": prop_id, "value": mclass}]}
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
        # Other folders for the filed pass: id -> (path, messages).
        self.folders = {"DELETEDITEMS": ("Deleted Items", []), "DRAFTS": ("Drafts", [])}
        self.listed = []

    def walk_folders(self, keeper, mb, **kw):
        return ([{"id": "SENTITEMS", "path": "Sent Items", "items": len(self.msgs)}]
                + [{"id": fid, "path": path, "items": len(rows)}
                   for fid, (path, rows) in self.folders.items()])

    def iter_index(self, keeper, mb, folder_id, from_addrs=None, top=None):
        self.listed.append(folder_id)
        rows = [dict(m) for m in self.folders[folder_id][1]]
        for i in range(0, len(rows), 2):
            yield rows[i:i + 2]

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
        every = self.msgs + [m for _p, rows in self.folders.values() for m in rows]
        return next((dict(m) for m in every if m["id"] == gid), None)


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
    monkeypatch.setattr(graph, "iter_index", g.iter_index)
    monkeypatch.setattr(graph, "folder", lambda k, mb, name: {"id": name.upper()})
    monkeypatch.setattr(ingest, "walk_folders", g.walk_folders)
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
    classes = dict(conn.execute("SELECT graph_id, message_class FROM messages"))
    assert classes == {"g1": "IPM.Note", "g2": "IPM.Note", "g5": "IPM.Note",
                       "g6": "IPM.Note"}


@pytest.mark.parametrize("prop_id", ["String 0x1a", "String 0x001A", "string 0x1A"])
def test_message_class_reads_either_form_of_the_tag(prop_id):
    msg = {"singleValueExtendedProperties": [{"id": prop_id, "value": "REPORT.IPM.Note.IPNRN"}]}
    assert graph.message_class(msg) == "REPORT.IPM.Note.IPNRN"


@pytest.mark.parametrize("prop_id", ["String 0x1b", "Integer 0x1a", "String {guid} Name x",
                                     "String 0xZZ", "", None])
def test_message_class_ignores_other_properties(prop_id):
    msg = {"singleValueExtendedProperties": [{"id": prop_id, "value": "IPM.Note"}]}
    assert graph.message_class(msg) == ""
    assert graph.message_class({}) == ""


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
    monkeypatch.setattr(ingest, "walk_folders", lambda k, mb, **kw: [
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
    assert "extended properties returned" not in text
    assert "120 of Joe's sends sit in 1 other folders (1 folders couldn't be counted)" in text
    assert "4 folders, 2 with mail; checking all of them" in text
    assert "Inbox/Bozzuto" in text
    assert not (tmp_path / "_check").exists()          # the sample .eml is cleaned up
    saved = json.loads((tmp_path / "check.json").read_text())
    assert saved["filed"]["folders"] == [{"path": "Inbox/Bozzuto", "from_joe": 120,
                                          "items": 900}]
    assert json.loads((tmp_path / "state.json").read_text())["auth"] == "delegated"


def test_check_shows_what_graph_returned_when_no_class_reads(fake, monkeypatch, tmp_path):
    for m in fake.msgs:
        m["singleValueExtendedProperties"] = []
    keeper = graph.TokenKeeper({}, "app", "t")
    monkeypatch.setattr(ingest, "find_access", lambda config, mb: (
        "app", keeper, MB, {"totalItemCount": 6, "sizeInBytes": 1024 ** 3},
        [{"path": "app", "mailbox": MB, "ok": True, "note": "reads Sent Items"}]))
    monkeypatch.setattr(ingest, "_first_sent", lambda k, mb, d: "2026-01-01T10:00:00Z")
    lines = []
    report = ingest.check({}, tmp_path, out=lines.append, scan_folders=False)
    text = "\n".join(lines)
    assert "sample classes: (none) x" in text
    assert "extended properties returned: none" in text
    assert report["listing"]["property_ids"] == {}


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


# =============================================================================
# --census
# =============================================================================

SC = "IPM.Note.EnterpriseVault.Shortcut"


@pytest.fixture
def mailbox(monkeypatch):
    """A mailbox with Vault shortcuts, filed copies, deleted sends, a broken
    folder, and Drafts the census must not read."""
    m = {i: gmsg(i, sent, mclass=c, imid=imid) for i, sent, c, imid in [
        (1, "2019-03-18T17:46:57Z", SC, True),          # shortcut, no full copy
        (2, "2019-05-01T10:00:00Z", SC, True),          # shortcut; full copy filed
        (3, "2024-02-01T10:00:00Z", "IPM.Note", True),  # also in Deleted Items
        (4, "2024-03-01T10:00:00Z", "IPM.Schedule.Meeting.Resp.Pos", True),
        (5, "2024-04-01T10:00:00Z", "IPM.Note", False),  # no internetMessageId
        (6, "2023-01-05T10:00:00Z", "IPM.Note", True),  # only filed
        (7, "2020-06-01T10:00:00Z", SC, True),          # only filed, as a shortcut
        (8, "2022-07-01T10:00:00Z", "IPM.Note", True),  # only deleted
        (9, "2021-08-01T10:00:00Z", "IPM.Note", True),  # only in a Deleted subfolder
        (10, "2024-05-01T10:00:00Z", "IPM.Note", True)]}
    full2 = dict(m[2], singleValueExtendedProperties=[{"id": "String 0x1a", "value": "IPM.Note"}])
    data = {"SENTITEMS": [m[1], m[2], m[3], m[4], m[5]],
            "F1": [full2, m[6], m[7]],
            "DELETEDITEMS": [m[8], m[3]],
            "D2": [m[9]],
            "DRAFTS": [m[10]]}
    folders = [{"id": "SENTITEMS", "path": "Sent Items", "items": 5},
               {"id": "F1", "path": "Inbox/Cases and Matters/A", "items": 3},
               {"id": "DELETEDITEMS", "path": "Deleted Items", "items": 2},
               {"id": "D2", "path": "Deleted Items/Old", "items": 1},
               {"id": "F2", "path": "Inbox/Broken", "items": 4},
               {"id": "EMPTY", "path": "Inbox/Empty", "items": 0},
               {"id": "DRAFTS", "path": "Drafts", "items": 1}]
    calls = []

    def iter_index(keeper, mb, folder_id, from_addrs=None, top=None):
        calls.append((folder_id, from_addrs))
        if folder_id in fail:
            raise GraphError(500, "boom")
        rows = [dict(x) for x in data.get(folder_id, [])]
        for i in range(0, len(rows), 2):
            yield rows[i:i + 2]

    fail = {"F2"}
    monkeypatch.setattr(graph, "iter_index", iter_index)
    monkeypatch.setattr(graph, "folder", lambda k, mb, name: {"id": name.upper()})
    monkeypatch.setattr(ingest, "walk_folders", lambda k, mb, **kw: [dict(f) for f in folders])
    monkeypatch.setattr(ingest, "resolve_access",
                        lambda config, root, state: (graph.TokenKeeper({}, "app", "t"), MB))
    return {"calls": calls, "fail": fail}


def test_census_pairs_copies_and_finds_what_sent_items_lacks(mailbox, tmp_path):
    lines = []
    report = census.run({}, tmp_path, out=lines.append)
    s = report["sends"]
    assert s["unique"] == 8 and s["skipped_receipts_and_replies"] == 1
    assert s["in_sent_items"] == 4 and s["copied_elsewhere"] == 2
    assert (s["only_outside"], s["only_filed"], s["only_deleted"],
            s["only_filed_and_deleted"]) == (4, 2, 2, 0)
    assert s["no_message_id"] == 1
    assert report["sent_items_listed"] and report["folders_total"] == 7
    assert report["folders_listed"] == 5          # not Drafts, not the empty folder
    assert report["folders_unreadable"][0]["path"] == "Inbox/Broken"
    years = report["years"]
    assert years["2019"] == {"unique": 2, "in_sent_items": 2, "vault_only": 1}
    assert years["2020"] == {"unique": 1, "only_outside": 1, "vault_only": 1}
    assert years["2024"] == {"unique": 2, "in_sent_items": 2}
    only = {f["path"]: f["only_here"] for f in report["folders"]}
    assert only == {"Inbox/Cases and Matters/A": 2, "Deleted Items": 1,
                    "Deleted Items/Old": 1, "Sent Items": 0}
    assert json.loads((tmp_path / "census.json").read_text())["sends"] == s


def test_census_reports_vault_shortcuts(mailbox, tmp_path):
    lines = []
    s = census.run({}, tmp_path, out=lines.append)
    assert s["sends"]["shortcut_somewhere"] == 3
    assert s["sends"]["vault_only"] == 2                    # 1 and 7
    assert s["vault_only_range"] == ["2019-03-18T17:46:57Z", "2020-06-01T10:00:00Z"]
    assert s["sends"]["sent_shortcut_full_elsewhere"] == 1  # 2: full copy filed
    text = "\n".join(lines)
    assert "with no full copy anywhere in the mailbox (sent 2019-03-18 to 2020-06-01)" in text
    assert "1 folders couldn't be listed" in text


def test_census_reads_sent_items_whole_and_other_folders_for_joe_only(mailbox, tmp_path):
    census.run({}, tmp_path, out=lambda line: None)
    by_folder = dict(mailbox["calls"])
    assert by_folder["SENTITEMS"] is None
    assert MB.lower() in by_folder["F1"] and MB.lower() in by_folder["DELETEDITEMS"]
    assert "DRAFTS" not in by_folder and "EMPTY" not in by_folder


def test_census_says_when_sent_items_could_not_be_listed(mailbox, tmp_path):
    mailbox["fail"].add("SENTITEMS")
    lines = []
    report = census.run({}, tmp_path, out=lines.append)
    assert report["sent_items_listed"] is False
    assert "INCOMPLETE: Sent Items couldn't be listed" in "\n".join(lines)
    assert cli.run_cli({}, tmp_path, ["--census", "--root", str(tmp_path)]) == 1


def test_census_cli_runs_and_takes_the_lock(mailbox, tmp_path, capsys):
    assert "--census" in cli.WRITE_FLAGS
    assert cli.run_cli({}, tmp_path, ["--joe-archive", "--census", "--root", str(tmp_path)]) == 0
    assert "Saved" in capsys.readouterr().out


def test_walk_folders_reports_progress(monkeypatch):
    page = {"value": [{"id": f"f{i}", "displayName": f"F{i}", "totalItemCount": 1,
                       "childFolderCount": 0} for i in range(600)]}
    monkeypatch.setattr(graph, "get_json", lambda keeper, url, params=None: page)
    seen = []
    folders = ingest.walk_folders(graph.TokenKeeper({}, "app", "t"), MB, progress=seen.append)
    assert len(folders) == 600 and seen == [250, 500]


# =============================================================================
# --vault-check
# =============================================================================

from joe_archive import evault  # noqa: E402

SECRET_SUBJECT = "Zanzibar settlement"
SECRET_WORDS = "quokka indemnity waiver"
EV_LINK = ("https://ev01.gejlaw.local/EnterpriseVault/ViewMessage.asp?"
           "VaultId=1ABC&SavesetId=201903181746")


def fake_jwt(claims: dict) -> str:
    import base64
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"e30.{body}.sig"


class FakeResp:
    def __init__(self, status, data=None):
        self.status_code = status
        self._data = data or {}
        self.text = json.dumps(self._data)
        self.headers = {}

    def json(self):
        return self._data


@pytest.fixture
def vault_mailbox(monkeypatch):
    """Sent Items with Vault shortcuts (2019-2023), full sends after, one
    shortcut whose full copy is filed elsewhere, and Graph's archive routes."""
    short = "Joe here. " + SECRET_WORDS + " <<Lease.pdf>> archived"
    msgs = [gmsg(i, sent, subject=SECRET_SUBJECT, body=b, mclass=c) for i, sent, b, c in [
        (1, "2019-03-18T17:46:57Z", short, SC),
        (2, "2019-03-19T10:00:00Z", short[:12], SC),
        (3, "2020-06-02T10:00:00Z", short, SC),           # full copy filed elsewhere
        (4, "2020-06-03T10:00:00Z", "Accepted", "IPM.Schedule.Meeting.Resp.Pos"),
        (5, "2023-09-02T10:00:00Z", SECRET_WORDS * 40, "IPM.Note"),
        (6, "2024-06-02T10:00:00Z", SECRET_WORDS * 3, "IPM.Note")]]
    full3 = dict(msgs[2], id="g3-filed", body={"content": short + " " + "x" * 300},
                 singleValueExtendedProperties=[{"id": "String 0x1a", "value": "IPM.Note"}])
    calls = {"routes": [], "lookups": 0}

    def iter_pages(keeper, mb, folder_id="sentitems", since=None, top=None, max_pages=None):
        rows = [dict(m) for m in msgs if not since or m["sentDateTime"] >= since]
        yield rows[:top or 50]

    def get_json(keeper, url, params=None, headers=None):
        flt = (params or {}).get("$filter", "")
        if url.endswith("/messages") and "internetMessageId eq" in flt:
            calls["lookups"] += 1
            imid = flt.split("eq ", 1)[1].strip("'")
            same = [dict(m) for m in msgs + [full3] if m.get("internetMessageId") == imid]
            return {"value": same}
        if url.endswith("/sentitems/messages"):       # HTML bodies, oldest first
            return {"value": [{"id": "g1", "body": {"content":
                    f'<p>{SECRET_WORDS}</p><a href="{EV_LINK}">View</a>'}}]}
        raise AssertionError(url)

    def get(keeper, url, params=None, headers=None, **kw):
        calls["routes"].append(url)
        if url.endswith("/settings/exchange"):
            if "/beta/" in url:
                return FakeResp(200, {"primaryMailboxId": "MBX:p", "archiveMailboxId": "MBX:a"})
            return FakeResp(200, {"primaryMailboxId": "MBX:p"})
        if "/admin/exchange/mailboxes/" in url:
            return FakeResp(403, {"error": {"code": "ErrorAccessDenied",
                                            "message": "Access is denied."}})
        raise AssertionError(url)

    token = fake_jwt({"roles": ["User.Read.All", "Mail.Send", "Mail.Read"]})
    monkeypatch.setattr(graph, "iter_pages", iter_pages)
    monkeypatch.setattr(graph, "get_json", get_json)
    monkeypatch.setattr(graph, "get", get)
    monkeypatch.setattr(ingest, "resolve_access",
                        lambda config, root, state: (graph.TokenKeeper({}, "app", token), MB))
    monkeypatch.setattr(evault, "probe_host", lambda stem: {
        "stem": stem, "resolves": True, "status": 401, "auth": ["NTLM", "Negotiate"]})
    return calls


def test_vault_check_prints_numbers_only(vault_mailbox, tmp_path):
    lines = []
    evault.run({}, tmp_path, out=lines.append)
    text = "\n".join(lines)
    saved = (tmp_path / "vault_check.json").read_text()
    for secret in (SECRET_SUBJECT, "quokka", "Lease.pdf", "Joe here", "VaultId", "1ABC"):
        assert secret not in text and secret not in saved


def test_vault_check_measures_shortcuts_against_full_sends(vault_mailbox, tmp_path):
    report = evault.run({}, tmp_path, out=lambda line: None)
    sc, full = report["sample"]["shortcuts"], report["sample"]["full"]
    assert sc["n"] == 3 and full["n"] == 2 and report["sample"]["skipped"] == 1
    assert sc["has_attachments"] == 0 and sc["lists_attachments"] == 2
    assert sc["mentions_archive"] == 2
    assert sc["chars_max"] == len("Joe here. " + SECRET_WORDS + " <<Lease.pdf>> archived")
    assert full["chars_max"] == len(SECRET_WORDS * 40)
    assert sum(sc["buckets"].values()) == 3 and sc["buckets"]["1-250"] == 3


def test_vault_check_finds_a_full_copy_filed_elsewhere(vault_mailbox, tmp_path):
    lines = []
    report = evault.run({}, tmp_path, out=lines.append)
    found = report["lookup"]
    assert found["looked_up"] == 3 and found["full_copy"] == 1
    assert found["only_shortcut"] == 2 and vault_mailbox["lookups"] == 3
    assert found["pairs_whole_text"] == 0 and found["pairs_kept_ratio_median"] < 0.3
    assert report["verdict"][0].startswith("1 of 3 sampled shortcuts have a full copy")
    assert "back in the mailbox" in report["verdict"][0]


def test_vault_check_a_few_filed_copies_are_not_the_vault(vault_mailbox, monkeypatch,
                                                          tmp_path):
    monkeypatch.setattr(evault, "BACK_IN_MAILBOX", 0.5)
    report = evault.run({}, tmp_path, out=lambda line: None)
    assert report["verdict"][0].startswith("Only 1 of 3 sampled shortcuts")
    assert "won't pull" in report["verdict"][0]


def test_vault_check_says_when_nothing_pulls(vault_mailbox, monkeypatch, tmp_path):
    monkeypatch.setattr(evault, "copies", lambda keeper, mb, imid: [])
    report = evault.run({}, tmp_path, out=lambda line: None)
    assert report["verdict"][0].startswith("None of the 3 sampled shortcuts")


def test_vault_check_reports_permissions_and_archive_routes(vault_mailbox, tmp_path):
    lines = []
    report = evault.run({}, tmp_path, out=lines.append)
    text = "\n".join(lines)
    assert report["permissions"] == ["Mail.Read", "Mail.Send", "User.Read.All"]
    assert "Archive-related: none" in text
    beta = report["archive_routes"][1]
    assert beta["mailboxes"][1] == {"label": "archiveMailboxId", "archive": True,
                                    "folders": "403 ErrorAccessDenied: Access is denied."}
    assert any("lists an archive mailbox, but Rocky can't read it" in v
               for v in report["verdict"])
    assert any("outside Graph" in v for v in report["verdict"])


def test_vault_check_names_the_vault_server_without_ids(vault_mailbox, tmp_path):
    lines = []
    report = evault.run({}, tmp_path, out=lines.append)
    assert report["vault_links"]["stems"] == {"https://ev01.gejlaw.local/EnterpriseVault": 1}
    assert "anonymous request answered 401, sign-in NTLM/Negotiate" in "\n".join(lines)


def test_vault_links_ignore_other_links():
    stems = evault.vault_links([
        "see https://gallagherllp.sharepoint.com/sites/Vault/x and "
        "<https://EV01.gejlaw.local/EnterpriseVault/Download.asp?a=1>.",
        EV_LINK + " " + EV_LINK])
    assert stems == {"https://ev01.gejlaw.local/EnterpriseVault": 2}


def test_token_claims_tolerates_junk():
    assert evault.token_claims("not-a-jwt") == {}
    assert evault.token_claims(fake_jwt({"scp": "Mail.Read"}))["scp"] == "Mail.Read"


def test_vault_check_cli_takes_the_lock(vault_mailbox, tmp_path, capsys):
    assert "--vault-check" in cli.WRITE_FLAGS
    assert cli.run_cli({}, tmp_path, ["--joe-archive", "--vault-check",
                                      "--root", str(tmp_path)]) == 0
    assert "Verdict:" in capsys.readouterr().out


# =============================================================================
# Vault shortcuts and the filed pass (scope: James, 11/5)
# =============================================================================

def short(i, sent, body="Cut-down text.", imid=True):
    return gmsg(i, sent, body=body, mclass=SC, imid=imid)


def test_upsert_full_copy_replaces_a_shortcut_and_keeps_its_ref(tmp_path):
    conn = store.open_db(tmp_path / "a.db")
    m = short(1, "2019-03-18T17:46:57Z", body="cut")
    assert store.upsert(conn, store.build_row(m, "Sent Items", SC, "eml/a.eml", 5, [])) == \
        ("new", None)
    full = dict(m, id="g1-full", body={"content": "the whole quokka clause"})
    row = store.build_row(full, "Inbox/Cases/A", "IPM.Note", "eml/b.eml", 50, ["x.pdf"])
    assert store.upsert(conn, row) == ("replaced", "eml/a.eml")
    conn.commit()
    r = conn.execute("SELECT id, graph_id, message_class, folder, attachment_names "
                     "FROM messages").fetchall()
    assert [tuple(x) for x in r] == [(1, "g1-full", "IPM.Note", "Inbox/Cases/A", "x.pdf")]
    assert refs(search.search(conn, "quokka")) == [1]       # the index follows
    assert refs(search.search(conn, "cut")) == []


def test_upsert_never_trades_a_full_copy_for_a_shortcut(tmp_path):
    conn = store.open_db(tmp_path / "a.db")
    m = gmsg(1, "2019-03-18T17:46:57Z", body="whole")
    store.upsert(conn, store.build_row(m, "Sent Items", "IPM.Note", "eml/a.eml", 50, []))
    again = store.build_row(m, "Sent Items", SC, "eml/a.eml", 5, [])
    assert store.upsert(conn, again) == ("kept", None)
    again = store.build_row(m, "Sent Items", "IPM.Note", "eml/a.eml", 50, [])
    assert store.upsert(conn, again) == ("kept", None)


def test_known_kinds_and_wanted(tmp_path):
    conn = store.open_db(tmp_path / "a.db")
    for m, c in ((short(1, "2019-01-01T00:00:00Z"), SC),
                 (gmsg(2, "2024-01-01T00:00:00Z"), "IPM.Note")):
        store.upsert(conn, store.build_row(m, "Sent Items", c, "eml/x", 1, []))
    known = store.known_kinds(conn, ["<im1@gallagherllp.com>", "<im2@gallagherllp.com>",
                                     "<im9@gallagherllp.com>"])
    assert known == {"<im1@gallagherllp.com>": True, "<im2@gallagherllp.com>": False}
    assert store.wanted(known, "<im1@gallagherllp.com>", "IPM.Note")
    assert not store.wanted(known, "<im1@gallagherllp.com>", SC)
    assert not store.wanted(known, "<im2@gallagherllp.com>", "IPM.Note")
    assert store.wanted(known, "<im9@gallagherllp.com>", SC)


@pytest.fixture
def vault_fake(fake):
    """Sent Items: two Vault shortcuts and the six fake sends. Filed: a full
    copy of shortcut 21, a send found only there, a copy of send 1. Deleted
    Items: one send found only there."""
    fake.msgs[:0] = [short(20, "2019-03-18T17:46:57Z"), short(21, "2019-04-01T10:00:00Z")]
    full21 = dict(gmsg(21, "2019-04-01T10:00:00Z", body="the whole quokka clause"),
                  id="g21-full")
    fake.folders["F1"] = ("Inbox/Cases and Matters/A",
                          [full21, gmsg(30, "2023-01-05T10:00:00Z", body="filed only"),
                           dict(fake.msgs[2], id="g1-copy")])
    fake.folders["DELETEDITEMS"] = ("Deleted Items", [gmsg(31, "2022-07-01T10:00:00Z")])
    fake.folders["D2"] = ("Deleted Items/Old", [gmsg(32, "2021-08-01T10:00:00Z")])
    return fake


def test_backfill_archives_shortcuts_then_the_filed_pass(vault_fake, tmp_path):
    result, lines = run(tmp_path)
    assert result["finished"]
    assert result["archived"] == 7 and result["shortcuts"] == 2 and result["filed"] == 1
    assert result["replaced"] == 1 and result["skipped"] == 2
    conn = store.open_db(tmp_path / "archive.db")
    rows = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT graph_id, message_class, folder FROM messages")}
    assert rows["g20"] == (SC, "Sent Items")
    assert rows["g21-full"] == ("IPM.Note", "Inbox/Cases and Matters/A")   # replaced
    assert "g21" not in rows
    assert rows["g30"] == ("IPM.Note", "Inbox/Cases and Matters/A")        # filed only
    assert "g31" not in rows and "g32" not in rows                         # deleted: out
    assert "g1-copy" not in vault_fake.downloads
    assert "DELETEDITEMS" not in vault_fake.listed and "D2" not in vault_fake.listed
    assert "DRAFTS" not in vault_fake.listed
    text = "\n".join(lines)
    assert "1 not yet archived, 1 full copies of sends archived as Vault shortcuts" in text
    assert "Archive: 7 sends (1 Vault shortcuts, 2 from other folders)" in text
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["backfill"]["complete_utc"] and state["filed"]["complete_utc"]


def test_the_filed_pass_runs_once(vault_fake, tmp_path):
    run(tmp_path)
    vault_fake.listed.clear()
    result, _ = run(tmp_path)
    assert vault_fake.listed == [] and result["finished"] and result["archived"] == 0


def test_deleted_items_come_in_when_config_says_so(vault_fake, tmp_path):
    lines = []
    ingest.backfill({"joe_archive_include_deleted": True}, tmp_path, out=lines.append)
    conn = store.open_db(tmp_path / "archive.db")
    got = {r[0] for r in conn.execute("SELECT graph_id FROM messages")}
    assert {"g31", "g32"} <= got


def test_limit_spans_sent_items_and_the_filed_pass(vault_fake, tmp_path):
    result, lines = run(tmp_path, limit=6)          # Sent Items holds 6 wanted sends
    assert result["archived"] == 6 and not result["finished"]
    assert vault_fake.listed == []                  # limit used up: no filed pass yet
    result, _ = run(tmp_path, limit=1)
    assert result["replaced"] + result["archived"] == 1 and not result["finished"]
    result, _ = run(tmp_path)
    assert result["finished"]
    conn = store.open_db(tmp_path / "archive.db")
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 7


def test_filed_phase_alone(vault_fake, tmp_path):
    result, lines = run(tmp_path, phase="filed")
    assert vault_fake.since_seen == []              # Sent Items untouched
    assert result["archived"] == 3 and result["finished"]   # 21 full, 30, and 1's copy


def test_a_failed_filed_send_retries_into_its_folder(vault_fake, tmp_path):
    vault_fake.fail.add("g30")
    result, _ = run(tmp_path)
    assert result["failed"] == 1
    conn = store.open_db(tmp_path / "archive.db")
    assert conn.execute("SELECT folder FROM failures").fetchone()[0] == \
        "Inbox/Cases and Matters/A"
    vault_fake.fail.clear()
    run(tmp_path)
    assert conn.execute("SELECT folder FROM messages WHERE graph_id='g30'").fetchone()[0] == \
        "Inbox/Cases and Matters/A"


def test_an_unlistable_folder_leaves_the_filed_pass_unfinished(vault_fake, monkeypatch,
                                                               tmp_path):
    real = vault_fake.iter_index

    def flaky(keeper, mb, folder_id, from_addrs=None, top=None):
        if folder_id == "F1":
            raise GraphError(500, "boom")
        yield from real(keeper, mb, folder_id, from_addrs, top)

    monkeypatch.setattr(graph, "iter_index", flaky)
    result, lines = run(tmp_path)
    assert not result["finished"] and "1 folders couldn't be listed" in "\n".join(lines)
    assert "complete_utc" not in json.loads((tmp_path / "state.json").read_text())["filed"]


def test_search_flags_shortcuts(vault_fake, tmp_path, capsys):
    run(tmp_path)
    conn = store.open_db(tmp_path / "archive.db")
    hits = search.search(conn, "", limit=50)["hits"]
    flagged = {h["ref"] for h in hits if h["shortcut"]}
    assert len(flagged) == 1
    text = search.format_hits(search.search(conn, "Cut-down"), "Cut-down")
    assert search.SHORTCUT_NOTE in text
    ref = next(iter(flagged))
    assert search.SHORTCUT_NOTE in search.format_message(search.read_message(conn, ref))
    filed = conn.execute("SELECT id FROM messages WHERE graph_id='g30'").fetchone()[0]
    assert "Filed in: Inbox/Cases and Matters/A" in search.format_message(
        search.read_message(conn, filed))
    assert cli.run_cli({}, tmp_path, ["--stats", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "1 Vault shortcuts; 2 from other folders" in out and "filed pass finished" in out


def test_start_is_for_a_trial_root_only(vault_fake, tmp_path, capsys):
    real = tmp_path / "real"
    cfg = {"joe_archive_root": str(real)}
    assert cli.run_cli(cfg, tmp_path, ["--backfill", "--start", "2024-01-01"]) == 2
    assert cli.run_cli(cfg, tmp_path, ["--backfill", "--start", "2024-01-01",
                                       "--root", str(real)]) == 2
    assert cli.run_cli(cfg, tmp_path, ["--sync", "--start", "2024-01-01",
                                       "--root", str(tmp_path / "t")]) == 2
    assert cli.run_cli(cfg, tmp_path, ["--backfill", "--start", "1/1/2024",
                                       "--root", str(tmp_path / "t")]) == 2
    assert "trial archive" in capsys.readouterr().out
    trial = tmp_path / "trial"
    assert cli.run_cli(cfg, tmp_path, ["--backfill", "--start", "2026-01-05", "--limit", "1",
                                       "--root", str(trial)]) == 0
    assert vault_fake.since_seen[-1] == "2026-01-05T00:00:00Z"
    conn = store.open_db(trial / "archive.db")
    assert [r[0] for r in conn.execute("SELECT graph_id FROM messages")] == ["g5"]
    assert not real.exists()


def test_a_retried_shortcut_never_overwrites_a_full_copy(vault_fake, tmp_path):
    vault_fake.fail.add("g21")                      # the Sent Items shortcut fails
    result, _ = run(tmp_path)
    assert result["failed"] == 1 and result["archived"] == 7   # full copy came in filed
    conn = store.open_db(tmp_path / "archive.db")
    rel = conn.execute("SELECT eml_path FROM messages WHERE graph_id='g21-full'").fetchone()[0]
    before = (tmp_path / rel).read_bytes()
    vault_fake.fail.clear()
    vault_fake.downloads.clear()
    result, _ = run(tmp_path)
    assert "g21" not in vault_fake.downloads and result["archived"] == 0
    assert (tmp_path / rel).read_bytes() == before
    assert conn.execute("SELECT COUNT(*) FROM failures").fetchone()[0] == 0
