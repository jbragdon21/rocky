"""Time Entries phase 3: the mail trigger (scope notes, export detection, the
replies), the run (mail coverage, the wait, delivery, failures) and the judge
grading two runs in one call. No network and no Claude calls: the mail, the
drafting pipeline and the judge are stand-ins.

    python -m pytest tests/test_time_entries_live.py -q
"""

import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from time_entries import db, evaluate, live, mail, run, trigger  # noqa: E402

HDR = ('"HH:MM:SS","Hours","Work Date","Matter","Matter Name","Client","Narrative",'
       '"Status","Non-Billable","No Charge","Timekeeper ID","Timekeeper Name"\n')


def export(rows, tk="0197") -> bytes:
    body = '"Time Entries List","2026-10-19","2026-10-23",\n' + HDR
    for d, m, h, narr in rows:
        body += (f'"00:12:00","{h}","{d}","{m}","Some Property","BMC","{narr}","Pending",'
                 f'"No","No","{tk}","James D. Bragdon",\n')
    return body.encode("utf-8")


ROWS = [("2026-10-19", "006104-1421", 0.3, ""),
        ("2026-10-20", "006104-1421", 0.2, "Respond re: Hall;"),
        ("2026-10-23", "006104-0948", 0.4,
         "Review Marina Bay class list and send to G. Sapire;")]


def message(i, sender="kvirtue@gallagherllp.com", subject="Time entries",
            body="Here are James' current time entries.", received=None):
    return {"id": f"id{i}", "subject": subject, "internetMessageId": f"<im{i}@x>",
            "from": {"emailAddress": {"address": sender}},
            "receivedDateTime": received or "2026-11-02T14:00:00Z",
            "body": {"content": body}}


def make_paths(tmp_path):
    root = tmp_path / "Time Entries"
    (root / "drafts").mkdir(parents=True)
    local = tmp_path / "local"
    return {"root": root, "reference": root / "x.xlsx", "overlay": root / "o.json",
            "settings": root / "s.json", "lessons": root / "LESSONS.md",
            "style_guide": root / "g.md", "drafts": root / "drafts", "local": local,
            "db": local / "te.db", "backtests": local / "backtests",
            "brain": tmp_path / "brain.db", "corpus": tmp_path / "corpus.db",
            "timecards": root / "timecards", "name_index_json": root / "n.json"}


# =============================================================================
# The note on top
# =============================================================================

def test_parse_scope():
    period = ("2026-10-19", "2026-10-30")
    assert trigger.parse_scope("Here are James' time entries", period) == {}
    assert trigger.parse_scope("just 10/19–10/23 please", period) == {
        "from": "2026-10-19", "to": "2026-10-23"}
    assert trigger.parse_scope("10/23 to 10/19, blanks only", period) == {
        "from": "2026-10-19", "to": "2026-10-23", "blanks_only": True}
    assert trigger.parse_scope("only 10/21", period) == {"from": "2026-10-21",
                                                         "to": "2026-10-21"}
    # A bare M/D across New Year takes the year nearest the export.
    assert trigger.parse_scope("12/29 - 1/2", ("2026-12-28", "2027-01-03")) == {
        "from": "2026-12-29", "to": "2027-01-02"}
    assert trigger.parse_scope("skip partials", None) == {"blanks_only": True}


# =============================================================================
# Reading an attachment
# =============================================================================

def test_read_export_kinds():
    assert trigger.read_export("a.csv", export(ROWS))["kind"] == "timecard"
    assert trigger.read_export("a.csv", export(ROWS, tk="0222"))["kind"] == "no_james"
    assert trigger.read_export("a.csv", b"name,amount\nx,1\n")["kind"] == "unreadable"
    assert trigger.read_export("a.xlsx", b"not a workbook")["kind"] == "unreadable"


def test_read_rows_finds_the_export_behind_a_cover_sheet():
    import openpyxl
    from time_entries import timecards
    wb = openpyxl.Workbook()
    wb.active.title = "Cover"
    wb.active.append(["Notes for James"])
    ws = wb.create_sheet("Export")
    ws.append(["Work Date", "Matter", "Hours", "Narrative"])
    ws.append(["2026-10-19", "006104-1421", 0.3, ""])
    buf = io.BytesIO()
    wb.save(buf)
    rows = timecards.read_rows("a.xlsx", buf.getvalue())
    assert rows[0][:2] == ["Work Date", "Matter"]


# =============================================================================
# The mail step
# =============================================================================

def test_scan_queues_replies_and_ignores(tmp_path):
    paths = make_paths(tmp_path)
    msgs = [
        message(1),                                                    # export
        message(2, subject="RE: lunch", body="see attached"),          # not ours
        message(3, body="Time entries attached, just 10/19-10/20"),    # same file
        message(4, body="James time entries"),                         # unreadable
        message(5, sender="someone@bozzuto.com"),                      # outside
        message(6, sender="jbragdon@gallagherllp.com",
                subject="RE: Rocky — Time entries: 10/19 to 10/23 draft narratives",
                body="Thanks"),                                        # reply to Rocky
        message(7, sender="jbragdon@gallagherllp.com", subject="Timesheet",
                body="Here you go"),                                   # other timekeeper
    ]
    files = {"id1": [{"name": "List Download.csv", "contentBytes": export(ROWS)}],
             "id2": [{"name": "menu.xlsx", "contentBytes": b"junk"}],
             "id3": [{"name": "List Download.csv", "contentBytes": export(ROWS)}],
             "id4": [{"name": "time.csv", "contentBytes": b"a,b\n1,2\n"}],
             "id5": [{"name": "x.csv", "contentBytes": export(ROWS)}],
             "id6": [{"name": "2026-10-19 draft.xlsx", "contentBytes": b"junk"}],
             "id7": [{"name": "tk.csv", "contentBytes": export(ROWS, tk="0222")}]}
    replies, filed = [], []
    counts = trigger.scan({}, paths, msgs, lambda m: files[m["id"]],
                          lambda to, subj, body: replies.append((to, subj, body)),
                          lambda m: filed.append(m["id"]))
    assert counts["queued"] == 1 and counts["duplicates"] == 1
    assert counts["ignored"] == 3 and counts["done"] == 7
    assert filed == ["id1", "id3", "id4", "id7"]
    who = [r[0] for r in replies]
    assert who == ["kvirtue@gallagherllp.com", "kvirtue@gallagherllp.com",
                   "jbragdon@gallagherllp.com"]
    assert "already has this exact export" in replies[0][2]
    assert "Work Date, Matter" in replies[1][2]
    assert "no rows for James" in replies[2][2]

    cx = db.connect(paths["db"])
    snaps = db.pending_snapshots(cx)
    assert len(snaps) == 1
    s = snaps[0]
    assert s["sender"] == "kvirtue@gallagherllp.com"
    assert s["internet_message_id"] == "<im1@x>"
    assert json.loads(s["scope"]) == {}
    assert Path(s["file_path"]).read_bytes() == export(ROWS)


def test_scan_stops_on_a_failed_download(tmp_path):
    paths = make_paths(tmp_path)
    msgs = [message(1, subject="RE: lunch"), message(2), message(3)]
    files = {"id1": [{"name": "a.pdf", "contentBytes": b"x"}], "id2": [],
             "id3": [{"name": "List Download.csv", "contentBytes": export(ROWS)}]}
    counts = trigger.scan({}, paths, msgs, lambda m: files[m["id"]],
                          lambda *a: None, lambda m: None)
    assert counts["done"] == 1 and counts["queued"] == 0


def test_scan_dry_run_writes_nothing(tmp_path):
    paths = make_paths(tmp_path)
    files = {"id1": [{"name": "List Download.csv", "contentBytes": export(ROWS)}]}
    counts = trigger.scan({}, paths, [message(1)], lambda m: files[m["id"]],
                          lambda *a: 1 / 0, lambda m: 1 / 0, dry_run=True)
    assert counts["queued"] == 1 and not paths["db"].exists()


def test_old_te_db_gains_the_snapshot_columns(tmp_path):
    import sqlite3
    p = tmp_path / "te.db"
    cx = sqlite3.connect(p)
    cx.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY, received_at TEXT, "
               "source TEXT, sender TEXT, sha256 TEXT UNIQUE, period_from TEXT, "
               "period_to TEXT, n_rows INTEGER, hours REAL, note TEXT)")
    cx.commit()
    cx.close()
    cx = db.connect(p)
    cols = {r[1] for r in cx.execute("PRAGMA table_info(snapshots)")}
    assert set(db.SNAPSHOT_COLUMNS) <= cols


# =============================================================================
# The run
# =============================================================================

def test_needed_through():
    assert live.needed_through(["2026-10-23"], 9) == datetime(2026, 10, 24, 9)  # Fri
    assert live.needed_through(["2026-10-24"], 9) == datetime(2026, 10, 27, 9)  # Sat
    assert live.needed_through(["2026-10-25"], 9) == datetime(2026, 10, 27, 9)  # Sun
    assert live.needed_through(["2026-10-19", "2026-10-21"], 9) == datetime(2026, 10, 22, 9)


class FakeSrc:
    def __init__(self, newest, ingest):
        self.newest, self.ingest = newest, ingest

    def newest_sent(self):
        return self.newest

    def last_ingest(self):
        return self.ingest

    def close(self):
        pass


def fake_draft(calls, fail=False):
    def draft_entries(config, paths, entries, work, mail_kind, cx, **kw):
        calls.append([e["work_date"] for e in work])
        if fail:
            raise RuntimeError("API down")
        recs = [{"kind": "draft", "work_date": e["work_date"], "matter": e["matter"],
                 "matter_name": "Some Property", "hours": e["hours"], "ordinal": 0,
                 "entry_row": e["row"], "narrative": "Review notice;",
                 "confidence": "REVIEW" if i else "High", "basis": "", "basis_ids": [],
                 "note": "No email that day." if i else "", "first": {}, "problem": ""}
                for i, e in enumerate(work)]
        recs.append({"kind": "uncaptured", "work_date": "2026-10-19",
                     "matter": "(unknown)", "hours": 0.1, "narrative": "Review note;",
                     "basis": "", "basis_ids": [], "note": ""})
        return {"recs": recs, "outs": [{"dates": ["2026-10-19"], "errors": [],
                                        "fixes": []}],
                "emails": [{}] * 12, "results": [{"zero_hit_token": "novel",
                                                  "matter": "006104-1421"}],
                "usage": {"calls": 4, "cost_usd": 1.23}, "model": "claude-opus-5-5",
                "effort": "high", "mail_window": ("2026-10-18", "2026-10-24"),
                "props": {"006104-1421": {}}}
    return draft_entries


def queue(paths, content=None, sender="kvirtue@gallagherllp.com", scope="{}",
          received="2026-11-02T14:00:00Z"):
    content = content or export(ROWS)
    f = paths["local"] / "snap.csv"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(content)
    from time_entries import timecards
    tc = timecards.load_timecard(f, timekeeper_id="0197")
    cx = db.connect(paths["db"])
    sid, _ = db.ingest_timecard(cx, tc, str(hash(content)), source="mail:x",
                                sender=sender, received_at=received)
    db.queue_snapshot(cx, sid, file_path=str(f), subject="Time entries", scope=scope)
    cx.close()
    return sid


def setup_run(monkeypatch, tmp_path, src, fail=False):
    paths = make_paths(tmp_path)
    calls, sent = [], []
    monkeypatch.setattr(run, "draft_entries", fake_draft(calls, fail))
    monkeypatch.setattr(mail, "open_mail", lambda kind, p: src)

    def send(to, cc, subject, body, attachments):
        sent.append({"to": to, "cc": cc, "subject": subject, "body": body,
                     "attachments": attachments})
        return True
    return paths, calls, sent, send


NOW = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)   # an hour after the email


def test_run_waits_for_the_brain_then_runs_anyway(monkeypatch, tmp_path):
    short = datetime(2026, 10, 20, 20, 0, tzinfo=timezone.utc)      # Tue 4 PM ET
    paths, calls, sent, send = setup_run(monkeypatch, tmp_path, FakeSrc(short, short))
    sid = queue(paths)
    assert live.run_pending({}, paths, "minotaur", send, now=NOW) == {"waiting": 1}
    assert calls == [] and sent == []
    cx = db.connect(paths["db"])
    assert db.snapshot(cx, sid)["status"] == "pending"
    assert "waiting for mail" in db.snapshot(cx, sid)["error"]
    cx.close()

    later = NOW + timedelta(hours=13)
    assert live.run_pending({}, paths, "minotaur", send, now=later) == {"done": 1}
    assert "Entries on the last day may be missing mail" in sent[0]["body"]


def test_run_delivers_the_workbook(monkeypatch, tmp_path):
    current = datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)
    paths, calls, sent, send = setup_run(monkeypatch, tmp_path, FakeSrc(current, current))
    sid = queue(paths)
    assert live.run_pending({}, paths, "minotaur", send, now=NOW) == {"done": 1}
    assert calls == [["2026-10-19", "2026-10-20"]]     # the filled entry is left alone
    m = sent[0]
    assert m["to"] == ["jbragdon@gallagherllp.com"]
    assert m["cc"] == ["kvirtue@gallagherllp.com"]
    assert m["subject"] == "Rocky — Time entries: 10/19 to 10/20 draft narratives"
    assert m["attachments"][0].parent == paths["drafts"]
    assert m["attachments"][0].name == "2026-10-19 to 2026-10-20 - draft narratives.xlsx"
    body = m["body"]
    assert "High 1 (0.3 h), Medium 0 (0 h), REVIEW 1 (0.2 h)" in body
    assert "10/20 006104-1421 0.2 h: No email that day." in body
    assert '006104-1421 ("novel")' in body
    assert "may be missing mail" not in body
    cx = db.connect(paths["db"])
    snap = db.snapshot(cx, sid)
    assert snap["status"] == "done"
    assert cx.execute("SELECT COUNT(*) FROM recommendations WHERE run_id=?",
                      (snap["run_id"],)).fetchone()[0] == 3
    cx.close()


def test_run_scope_and_james_as_sender(monkeypatch, tmp_path):
    current = datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)
    paths, calls, sent, send = setup_run(monkeypatch, tmp_path, FakeSrc(current, current))
    queue(paths, sender="jbragdon@gallagherllp.com",
          scope=json.dumps({"blanks_only": True}))
    live.run_pending({}, paths, "minotaur", send, now=NOW)
    assert calls == [["2026-10-19"]]
    assert sent[0]["cc"] == []


def test_run_with_nothing_to_draft(monkeypatch, tmp_path):
    current = datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)
    paths, calls, sent, send = setup_run(monkeypatch, tmp_path, FakeSrc(current, current))
    queue(paths, content=export([ROWS[2]]))
    assert live.run_pending({}, paths, "minotaur", send, now=NOW) == {"nothing": 1}
    assert calls == [] and "nothing to draft" in sent[0]["subject"]


def test_run_failure_tells_james_only(monkeypatch, tmp_path):
    current = datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)
    paths, calls, sent, send = setup_run(monkeypatch, tmp_path, FakeSrc(current, current),
                                         fail=True)
    sid = queue(paths)
    assert live.run_pending({}, paths, "minotaur", send, now=NOW) == {"failed": 1}
    assert sent[0]["to"] == ["jbragdon@gallagherllp.com"] and sent[0]["cc"] == []
    assert f"--snapshot {sid}" in sent[0]["body"]
    cx = db.connect(paths["db"])
    assert db.snapshot(cx, sid)["status"] == "failed"
    assert "API down" in db.snapshot(cx, sid)["error"]
    # Not retried by itself: nothing is pending any more.
    assert db.pending_snapshots(cx) == []
    cx.close()


# =============================================================================
# The judge, two runs at once
# =============================================================================

def judge_client(grade_for):
    reqs = []

    def create(**kw):
        reqs.append(kw)
        grades = []
        row = 0
        for line in kw["messages"][0]["content"].splitlines():
            line = line.strip()
            if line.startswith("ROW "):
                row = int(line.split()[1].rstrip(":"))
            elif len(line) > 2 and line[1] == ":" and line[0] in "ABCDEFGH":
                grades.append({"row": row, "label": line[0],
                               "grade": grade_for(line[3:]), "why": ""})
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps({"grades": grades}))],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                  cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0))
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create))), reqs


def test_judge_grades_both_runs_in_one_call():
    rows = [{"date": "2026-09-01", "matter": "m1", "hours": 0.2, "final": "Review lease;",
             "rocky": "Review lease;", "rocky_conf": "High", "first": "Review lease;",
             "first_conf": "High", "hand": "Review lease;", "hand_conf": "High",
             "rocky_sim": 1.0},
            {"date": "2026-09-02", "matter": "m2", "hours": 0.1, "final": "Call;",
             "rocky": "Draft notice;", "rocky_conf": "High", "first": "", "rocky_sim": 0.1,
             "first_conf": None}]
    earlier = [{"date": "2026-09-02", "matter": "m2", "hours": 0.1, "final": "Call;",
                "rocky": "Call with manager;", "rocky_conf": "Medium"},
               {"date": "2026-09-01", "matter": "m1", "hours": 0.2, "final": "Review lease;",
                "rocky": "Review lease;", "rocky_conf": "High"}]
    assert evaluate.attach_compare(rows, earlier) == 2
    client, reqs = judge_client(lambda t: "same" if t in ("Review lease;",
                                                          "Call with manager;")
                                else "different")
    evaluate.judge(client, rows)
    assert len(reqs) == 1
    text = reqs[0]["messages"][0]["content"]
    # Identical texts on a row are shown once: row 1 has one candidate.
    assert text.count("Review lease;") == 2          # FINAL + one candidate
    r1, r2 = rows
    assert {r1[f"grade_{w}"] for w in ("rocky", "first", "hand", "compare")} == {"same"}
    assert r2["grade_rocky"] == "different" and r2["grade_compare"] == "same"
    assert r2["grade_first"] == "none"
    s = evaluate.summarize(rows, [])
    assert s["compare_all"]["same"] == 2 and s["rocky_on_compare_rows"]["same"] == 1
    assert s["compare_Medium"]["same"] == 1
    assert "Compared run, all rows" in evaluate.format_report(
        "t", s, {"cost_usd": 0, "calls": 0}, {"cost_usd": 0}, rows)


def test_agreement():
    a = [{"grade_rocky": "same", "grade_hand": "same", "grade_first": "none"}]
    b = [{"grade_rocky": "same", "grade_hand": "partial", "grade_first": "none"}]
    assert evaluate.agreement([a, b]) == {"compared": 2, "agreed": 1, "rate": 0.5}


def test_rejudge_cli(monkeypatch, tmp_path, capsys):
    from time_entries import cli
    paths = make_paths(tmp_path)
    paths["backtests"].mkdir(parents=True)
    row = {"date": "2026-09-01", "matter": "m1", "hours": 0.2, "final": "Review lease;",
           "rocky": "Review lease;", "rocky_conf": "High", "rocky_sim": 1.0,
           "first": "Review lease;", "first_conf": "High", "grade_rocky": "wrong"}
    (paths["backtests"] / "a.json").write_text(json.dumps({"scored": [row]}))
    (paths["backtests"] / "b.json").write_text(json.dumps(
        {"scored": [dict(row, rocky="Draft lease;", rocky_conf="Medium")]}))
    client, reqs = judge_client(lambda t: "same" if t == "Review lease;" else "partial")
    monkeypatch.setattr(run, "make_client", lambda config: client)
    monkeypatch.setattr(cli, "get_paths", lambda config, data_dir, argv: paths)
    assert cli.run_cli({}, tmp_path, ["--time-entries", "--rejudge", "a.json",
                                      "--compare", "b.json", "--repeat", "2"]) == 0
    out = capsys.readouterr().out
    assert len(reqs) == 2
    assert "Compared run, rows it called Medium    1 rows | same   0" in out
    assert "3 of 3 grades the same (100%)" in out     # rocky, first, compare
    assert list(paths["backtests"].glob("a - rejudged *.json"))
