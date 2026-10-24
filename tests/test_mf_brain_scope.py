"""MF Case Brain scope: only flagged eviction folders, never the
insured/monitored branch, never a resident's claim against the client.
Graph is mocked.

    python -m pytest tests/test_mf_brain_scope.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mf_brain  # noqa: E402

INSURED = "__Bozzuto Insured/Monitored Litigation"


def _paths(tmp_path, scope_text=None):
    paths = mf_brain.get_paths({"mf_tracker_root": str(tmp_path)}, tmp_path)
    paths["root"].mkdir(parents=True, exist_ok=True)
    if scope_text is not None:
        paths["scope"].write_text(scope_text, encoding="utf-8")
    return paths


def _f(fid, *path):
    return {"id": fid, "name": path[-1], "path": ["Inbox", *path],
            "depth": len(path), "n": 5, "kids": 0}


TREE = [
    _f("dc1", "__Bozzuto Management", "__DC", "Stratos", "Boykins, Gary (1001)"),
    _f("dc2", "__Bozzuto Management", "__DC", "Stratos", "Pittman, Amira (OHR)"),
    _f("va1", "__Bozzuto Management", "__Virginia", "Alula", "Diaz, Ana (204)"),
    _f("ins", INSURED, "Hettinger, Laura (utility charges)"),
    _f("hor", "__Horning", "__Cloisters", "Lee, Sam (12)"),
]


def test_load_scope_parses_lines_comments_and_inbox_prefix(tmp_path):
    paths = _paths(tmp_path, "# header\n\nInbox\\__Bozzuto Management\\__DC\n"
                             "  __Horning    [29]\n#__WPC\n")
    assert mf_brain.load_scope(paths) == [
        ("bozzutomanagement", "dc"), ("horning",)]
    assert mf_brain.load_scope(_paths(tmp_path / "x")) == []


def test_path_scope_and_descent():
    scope = [("bozzutomanagement", "dc")]
    dc = ["Inbox", "__Bozzuto Management", "__DC", "Stratos", "Boykins, Gary"]
    va = ["Inbox", "__Bozzuto Management", "__Virginia", "Alula", "Diaz, Ana"]
    assert mf_brain.path_in_scope(dc, scope)
    assert not mf_brain.path_in_scope(va, scope)
    # On the way to a flagged subtree: walk in, but it is not itself in scope.
    assert mf_brain.should_descend(["Inbox", "__Bozzuto Management"], scope)
    assert not mf_brain.path_in_scope(["Inbox", "__Bozzuto Management",
                                       "Smith, Jo"], scope)
    assert not mf_brain.should_descend(va[:3], scope)
    # Insured/monitored is out even with no file, and even if flagged.
    for sc in ([], [("bozzutoinsuredmonitoredlitigation",)]):
        assert not mf_brain.should_descend(["Inbox", INSURED], sc)
        assert not mf_brain.path_in_scope(["Inbox", INSURED, "X, Y"], sc)
    assert mf_brain.path_in_scope(va, [])


def test_case_in_scope():
    scope = [("bozzutomanagement", "dc")]
    sheet = {"matter_type": "eviction", "first_seen_by": "llt_sheet"}
    assert mf_brain.case_in_scope(sheet, scope)
    folder = {"matter_type": "other",
              "folder_path": "Inbox\\__Bozzuto Management\\__DC\\S\\A, B"}
    assert mf_brain.case_in_scope(folder, scope)
    assert not mf_brain.case_in_scope(
        dict(folder, folder_path="Inbox\\__WPC\\S\\A, B"), scope)
    for mt in ("agency_complaint", "habitability", "insured_litigation"):
        assert not mf_brain.case_in_scope(dict(folder, matter_type=mt), [])


def test_scan_folders_skips_unflagged_insured_and_resident_claims(monkeypatch):
    seen = {}

    def fake_walk(token, mailbox, max_depth=4, scope=None):
        seen["scope"] = scope
        return TREE

    monkeypatch.setattr(mf_brain, "walk_folders", fake_walk)
    cases, fmap = {}, {}
    scope = [("bozzutomanagement", "dc"), ("horning",)]
    d = mf_brain.scan_folders(cases, fmap, "tok", "me@x", scope)
    assert seen["scope"] == scope
    assert d["in_scope"] == {"dc1", "hor"}
    assert set(fmap) == {"dc1", "hor"}
    assert sorted(c["name"] for c in cases.values()) == [
        "Boykins, Gary (1001)", "Lee, Sam (12)"]

    # No file: everything but insured, still no resident claims.
    d = mf_brain.scan_folders({}, {}, "tok", "me@x", [])
    assert d["in_scope"] == {"dc1", "va1", "hor"}


def test_scan_inbox_reads_only_in_scope_folders(tmp_path, monkeypatch):
    import requests

    class R:
        status_code = 200

        def json(self):
            return {"value": [
                {"id": "m1", "parentFolderId": "ins",
                 "receivedDateTime": "2026-10-20T12:00:00Z"},
                {"id": "m2", "parentFolderId": "dc1",
                 "receivedDateTime": "2026-10-20T13:00:00Z"}]}

    monkeypatch.setattr(requests, "get", lambda *a, **k: R())
    paths = _paths(tmp_path)
    fmap = {"ins": "MF-00001", "dc1": "MF-00002"}
    state = {"inbox_cursor": "2026-10-20T00:00:00+00:00"}
    c = mf_brain.scan_inbox({"mf_brain_max_catchup_days": 3650}, paths, {},
                            fmap, "tok", "me@x", state,
                            scope_ids={"dc1"}, dry_run=True)
    assert c["folders_touched"] == 1

    # A walk that found nothing holds the cursor instead of skipping mail.
    c = mf_brain.scan_inbox({}, paths, {}, fmap, "tok", "me@x", state,
                            scope_ids=set(), dry_run=True)
    assert c["cursor"] == state["inbox_cursor"] and c["messages"] == 0


def test_scoped_cases_reads_the_share_file(tmp_path):
    paths = _paths(tmp_path, "__Bozzuto Management\\__DC\n")
    cases = {
        "MF-1": {"matter_type": "eviction"},
        "MF-2": {"matter_type": "other",
                 "folder_path": "Inbox\\__Bozzuto Management\\__DC\\S\\A, B"},
        "MF-3": {"matter_type": "other",
                 "folder_path": "Inbox\\__Towner Mgmt\\S\\C, D"},
        "MF-4": {"matter_type": "insured_litigation",
                 "folder_path": f"Inbox\\{INSURED}\\E, F"},
    }
    assert set(mf_brain.scoped_cases(paths, cases)) == {"MF-1", "MF-2"}
