"""Time Entries phase 1: timecard import, Eastern time, the brain.db reader,
the correlator's rules, te.db supersession and migration, and the backtest
matcher. No network, no Claude, no real mail.

    python -m pytest tests/test_time_entries.py -q
"""

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from time_entries import backtest, correlate, db, mail, names, reference, timecards  # noqa: E402
from time_entries.paths import get_paths  # noqa: E402

S = reference.load_settings(None)

FULL_CSV = (
    '﻿"Time Entries List","2026-10-06","2026-10-18",\n'
    '"Time Entry Status : Pending",\n'
    '"HH:MM:SS","Hours","Work Date","Matter","Matter Name","Client","Narrative",'
    '"Status","Non-Billable","No Charge","Timekeeper ID","Timekeeper Name",\n'
    '"00:48:00","0.8","2026-10-18","006104-1762","Robert Hunt","BMC","","Pending","No","No","0197","James D. Bragdon",\n'
    '"00:12:00","0.2","2026-10-17","006104-1289","Halstead Norwalk (CT)","BMC","","Pending","No","No","0197","James D. Bragdon",\n'
    '"00:12:00","0.2","2026-10-17","006104-1289","Halstead Norwalk (CT)","BMC","","Pending","No","No","0197","James D. Bragdon",\n'
    '"00:00:14","","2026-10-16","","","","","Pending","No","Yes","0197","James D. Bragdon",\n'
    '"01:00:00","1.0","2026-10-16","006104-1289","Halstead Norwalk (CT)","BMC","Draft notice;","Pending","No","No","0222","Someone Else",\n'
)


# =============================================================================
# Timecards
# =============================================================================

def test_full_export_header_period_and_timekeeper(tmp_path):
    p = tmp_path / "List Download.csv"
    p.write_text(FULL_CSV, encoding="utf-8")
    tc = timecards.load_timecard(p, timekeeper_id="0197")
    assert tc["period"] == ("2026-10-06", "2026-10-18")
    assert tc["skipped_other_timekeepers"] == 1
    assert len(tc["entries"]) == 4
    # two identical blanks stay two entries, in file order
    dup = [e for e in tc["entries"] if e["matter"] == "006104-1289"]
    assert len(dup) == 2 and dup[0]["row"] != dup[1]["row"]
    # a blank Hours cell falls back to HH:MM:SS; flags carried through
    stub = [e for e in tc["entries"] if e["work_date"] == "2026-10-16"][0]
    assert stub["hours"] == 0.0 and stub["no_charge"] is True


def test_six_column_export_with_us_dates(tmp_path):
    p = tmp_path / "may.csv"
    p.write_text("﻿Work Date,Matter,Matter Name,Client,Narrative,Status\n"
                 "5/30/2026,000004-0007,Billing,Firm,,Pending\n", encoding="utf-8")
    e = timecards.load_timecard(p)["entries"][0]
    assert e["work_date"] == "2026-05-30" and e["hours"] is None


def test_xlsx_export_with_date_cells(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Time Entries List", "2026-10-01", "2026-10-05"])
    ws.append(["HH:MM:SS", "Hours", "Work Date", "Matter", "Matter Name",
               "Client", "Narrative"])
    ws.append(["00:06:00", 0.1, datetime(2026, 10, 5), "006104-1776",
               "Virginia Fees Investigation", "BMC", None])
    p = tmp_path / "export.xlsx"
    wb.save(p)
    tc = timecards.load_timecard(p)
    e = tc["entries"][0]
    assert (e["work_date"], e["hours"], e["narrative"]) == ("2026-10-05", 0.1, "")
    assert tc["period"] == ("2026-10-01", "2026-10-05")
    # the same bytes as an email attachment
    assert timecards.load_timecard("a.xlsx", content=p.read_bytes())["entries"]


def test_not_a_timecard():
    assert not timecards.is_timecard([["Name", "Unit"], ["A", "1"]])
    with pytest.raises(ValueError):
        timecards.parse_rows([["Name", "Unit"]])


# =============================================================================
# Eastern time
# =============================================================================

def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_eastern_after_dst_ends_11_1_2026():
    # 11:30 PM EST on Monday 11/2. A flat UTC-4 would file it on 11/3.
    et = mail.to_eastern(utc(2026, 11, 3, 4, 30))
    assert et.date().isoformat() == "2026-11-02" and et.hour == 23


def test_eastern_fall_back_and_spring_forward_edges():
    assert mail.to_eastern(utc(2026, 11, 1, 5, 30)).utcoffset().total_seconds() == -4 * 3600
    assert mail.to_eastern(utc(2026, 11, 1, 6, 30)).utcoffset().total_seconds() == -5 * 3600
    assert mail.to_eastern(utc(2026, 3, 8, 6, 59)).hour == 1      # 1:59 EST
    assert mail.to_eastern(utc(2026, 3, 8, 7, 0)).hour == 3       # 3:00 EDT
    assert mail.to_eastern(utc(2026, 7, 1, 23, 30)).date().isoformat() == "2026-07-01"


# =============================================================================
# brain.db reader
# =============================================================================

def make_brain(path, version="1", rows=()):
    cx = sqlite3.connect(path)
    cx.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE messages (graph_id TEXT PRIMARY KEY, internet_message_id TEXT,
          conversation_id TEXT, folder TEXT, direction TEXT, sent_at TEXT,
          received_at TEXT, from_name TEXT, from_addr TEXT, to_addrs TEXT,
          cc_addrs TEXT, subject TEXT, body_text TEXT, attachment_text TEXT,
          has_attachments INTEGER, ingested_at TEXT);
    """)
    cx.execute("INSERT INTO meta VALUES ('schema_version', ?)", (version,))
    for r in rows:
        cx.execute("INSERT INTO messages (graph_id, internet_message_id, "
                   "conversation_id, direction, sent_at, to_addrs, cc_addrs, "
                   "subject, body_text, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?)", r)
    cx.commit()
    cx.close()


def test_brain_reader_dedupes_converts_and_filters(tmp_path):
    p = tmp_path / "brain.db"
    make_brain(p, rows=[
        ("g1", "<a@x>", "c1", "sent", "2026-11-03T04:30:00Z", '["k@bozzuto.com"]',
         "[]", "RE: Halstead Norwalk", "body", "2026-11-03T05:00:00Z"),
        ("g2", "<a@x>", "c1", "sent", "2026-11-03T04:30:00Z", '["k@bozzuto.com"]',
         "[]", "RE: Halstead Norwalk", "body", "2026-11-04T05:00:00Z"),   # copy
        ("g3", "<b@x>", "c2", "received", "2026-11-02T15:00:00Z", "[]", "[]",
         "inbound", "x", "2026-11-03T05:00:00Z"),
        ("g4", "<c@x>", "c3", "sent", "2026-11-02T16:00:00Z", "[]", "[]",
         "Accepted: Mediation", "", "2026-11-03T05:00:00Z"),
    ])
    src = mail.BrainMail(p)
    got = src.sent_between("2026-11-02", "2026-11-02")
    src.close()
    assert [e["id"] for e in got] == ["<c@x>", "<a@x>"]
    late = got[1]
    assert late["date"] == "2026-11-02" and late["sent_at"].endswith("23:30:00")
    assert late["to"] == "k@bozzuto.com" and "halstead norwalk" in late["hay"]
    assert got[0]["inv"] is True


def test_brain_reader_refuses_other_schema_and_never_writes(tmp_path):
    p = tmp_path / "brain.db"
    make_brain(p, version="2")
    with pytest.raises(mail.BrainError):
        mail.BrainMail(p)
    make_brain(tmp_path / "ok.db")
    src = mail.BrainMail(tmp_path / "ok.db")
    with pytest.raises(sqlite3.OperationalError):
        src.cx.execute("DELETE FROM meta")
    src.close()


# =============================================================================
# Correlator rules
# =============================================================================

def test_rule8_word_boundaries_for_short_tokens():
    assert not correlate.hay_has("re: christina's lease", "hri")
    assert correlate.hay_has("hri | invoice", "hri")
    assert correlate.hay_has("re: the porter del ray", "porter del")


def test_inverted_names_and_generic_words():
    # "View, The" has no distinctive word, so it yields nothing (the alias
    # sheet carries "The View"); "Witmer, The" is de-inverted.
    assert correlate.distinctive("View, The", S) == []
    assert "the witmer" in dict(correlate.distinctive("Witmer, The", S))
    assert "meeting" not in dict(correlate.distinctive("Parc Plymouth Meeting", S))


def test_rule9_collision_tokens_never_score_bare():
    props = {"006104-1246": {"property": "Halstead Centreville"},
             "006104-1289": {"property": "Halstead Norwalk"}}
    centreville = correlate.matter_tokens("006104-1246", "", props, {}, {}, S)
    norwalk = correlate.matter_tokens("006104-1289", "", props, {}, {}, S)
    assert "halstead" not in centreville and "centreville" in centreville
    assert "halstead" in norwalk          # the one exempt matter


def test_rule6_sunday_reaches_forward():
    sunday = correlate.windows("2026-10-11", S)
    assert sunday[-1][0].startswith("week-forward")
    days = [dt for dt, _pen, _hour in sunday[-1][1]]
    assert "2026-10-15" in days and "2026-10-18" in days
    assert not correlate.windows("2026-10-12", S)[-1][0].startswith("week")


def test_zero_settings_restore_the_staged_windows():
    off = dict(S, window_batch_hour=0, window_pool_days=0)
    stages = correlate.windows("2026-10-14", off)
    assert [lbl for lbl, _ in stages] == ["same-day", "±1d", "±3d"]
    assert stages[0][1] == [("2026-10-14", 0, None)]
    assert stages[1][1] == [("2026-10-13", 1, None), ("2026-10-15", 1, None)]


def _email(i, d, subject, body="", at="10:00"):
    e = {"id": i, "date": d, "subject": subject, "to": "", "cc": "",
         "body": body, "inv": False, "sent_at": f"{d}T{at}:00"}
    e["hay"] = mail.make_hay(e)
    return e


def test_next_morning_batch_counts_for_the_day_before():
    props = {"006104-0889": {"property": "Falls Green"},
             "006104-1717": {"property": "The Gramercy"}}
    emails = [_email("weak", "2026-09-09", "RE: Gramercy renewal", "falls"),
              _email("batch", "2026-09-10", "RE: Falls Green Unit C604", at="07:31"),
              _email("later", "2026-09-10", "RE: Falls Green follow up", at="11:15")]
    entry = [{"row": 1, "work_date": "2026-09-09", "matter": "006104-0889",
              "matter_name": "Falls Green", "narrative": ""}]
    got = correlate.correlate(entry, emails, props, {}, {}, S)[0]
    assert [c["id"] for c in got["cands"]] == ["batch", "weak"]
    off = correlate.correlate(entry, emails, props, {}, {}, dict(S, window_batch_hour=0))[0]
    assert [c["id"] for c in off["cands"]] == ["weak"]     # the old staged search


def test_pool_days_join_the_first_search_penalized():
    props = {"006104-1717": {"property": "The Gramercy"}}
    emails = [_email("a", "2026-09-03", "RE: Gramercy renewal lease"),
              _email("b", "2026-09-04", "RE: Gramercy follow up")]
    entry = [{"row": 1, "work_date": "2026-09-03", "matter": "006104-1717",
              "matter_name": "The Gramercy", "narrative": ""}]
    pooled = dict(S, window_pool_days=1, window_pool_penalty=3)
    got = correlate.correlate(entry, emails, props, {}, {}, pooled)[0]
    a, b = got["cands"]
    assert (a["id"], b["id"]) == ("a", "b") and a["score"] - b["score"] == 3


def test_matter_number_written_in_mail():
    rx = correlate.matter_number_re("006104-1524")
    for text in ("please pay this under 6104-1524", "matter 006104-1524.",
                 "c/m 6104 - 1524"):
        assert rx.search(text), text
    for text in ("6104-15240", "16104-1524", "6104-1525"):
        assert not rx.search(text), text
    assert correlate.matter_number_re("006103-0157").search("pay under 6103-157")
    general = correlate.matter_number_re("006104-0000")
    assert general.search("bill to 6104-0") and not general.search("6104-0222")
    assert correlate.matter_number_re("") is None

    emails = [_email("inv", "2026-09-08", "RE: AUG Invoice: MRE0069A",
                     "Please pay this under 6104-1524")]
    entry = [{"row": 1, "work_date": "2026-09-08", "matter": "006104-1524",
              "matter_name": "RealPage MD AG", "narrative": ""}]
    got = correlate.correlate(entry, emails, {}, {}, {}, S)[0]
    assert got["cands"][0]["id"] == "inv"
    assert "#006104-1524" in got["cands"][0]["hits"]
    none = correlate.correlate(entry, emails, {}, {}, {}, dict(S, weight_matter_number=0))[0]
    assert none["cands"] == []


def test_correlate_prefers_same_day_and_reports_zero_hit_token():
    props = {"006104-1717": {"property": "The Gramercy"},
             "006104-0796": {"property": "Ballston Quarter"}}
    emails = [_email("a", "2026-09-03", "RE: Gramercy renewal lease"),
              _email("b", "2026-09-04", "RE: Gramercy follow up")]
    entries = [{"row": 1, "work_date": "2026-09-03", "matter": "006104-1717",
                "matter_name": "The Gramercy", "narrative": ""},
               {"row": 2, "work_date": "2026-09-03", "matter": "006104-0796",
                "matter_name": "", "narrative": ""}]
    res = correlate.correlate(entries, emails, props, {}, {}, S)
    assert [c["id"] for c in res[0]["cands"]] == ["a"]   # 9/4 10:00 is past the batch hour
    assert res[0]["stage"].startswith("same-day")
    assert res[1]["cands"] == [] and res[1]["zero_hit_token"]


def test_overlay_aliases_reach_the_scorer():
    notes = {}
    reference.merge_overlay(notes, {"006104-1127": {"aliases": ["Izakaya", "izakaya"]}})
    assert notes["006104-1127"][reference.ALIAS_COL] == "Izakaya"
    t = correlate.matter_tokens("006104-1127", "", {}, notes, {}, S)
    assert t["izakaya"] == S["weight_alias_word"]


def test_settings_are_bounded(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"window_far_days": 99, "nonsense": 1,
                             "weight_word": "x", "_comment": "ok"}))
    s = reference.load_settings(p)
    assert s["window_far_days"] == 7 and "nonsense" not in s
    assert s["weight_word"] == reference.DEFAULT_SETTINGS["weight_word"]


def test_name_index_guardrails():
    idx, units = names.build_index([
        ("006104-1530", "Draft notice for Jing Li (535);"),
        ("006104-1530", "Follow up regarding Berhane agreement;"),
        ("006104-1364", "Follow up regarding Berhane renewal;"),
        ("006104-1530", "Review Woldearegay request;"),
        ("006104-1530", "Revise Woldearegay agreement;"),
        ("006104-1530", "Respond re: Woldearegay;"),
        ("006104-1475", "Follow up regarding Woldearegay;"),
        ("006104-1530", "Prepare Herouas (415) notice;"),
    ])
    assert "berhane" not in idx                        # under 75% on one matter
    assert idx["woldearegay"]["amb"] == 2              # kept, but ambiguous
    assert units["535"] == {"006104-1530": 1}
    props = {"006104-1530": {"property": "The Alder"}}
    m2n = dict(names.scoring_names(idx, props, correlate.COMMON, S))
    scored = [nm for nm, _ in m2n.get("006104-1530", [])]
    assert "herouas" in scored and "woldearegay" not in scored


# =============================================================================
# te.db
# =============================================================================

def _tc(entries, period):
    return {"entries": entries, "period": period}


def _entry(row, d, matter, hours, narr=""):
    return {"row": row, "work_date": d, "hours": hours, "matter": matter,
            "matter_name": "", "client": "", "narrative": narr, "status": "Pending",
            "non_billable": False, "no_charge": False, "timekeeper_id": "0197"}


def test_snapshot_ingest_dedupes_and_wider_export_supersedes(tmp_path):
    cx = db.connect(tmp_path / "te.db")
    narrow = _tc([_entry(3, "2026-10-06", "006104-1289", 0.2),
                  _entry(4, "2026-10-06", "006104-1289", 0.2)],
                 ("2026-10-06", "2026-10-10"))
    sid, new = db.ingest_timecard(cx, narrow, "aaa", "part 1.csv")
    assert new and db.ingest_timecard(cx, narrow, "aaa", "part 1.csv") == (sid, False)
    wide = _tc([_entry(3, "2026-10-06", "006104-1289", 0.2, "Prepare follow up;"),
                _entry(4, "2026-10-06", "006104-1289", 0.2),
                _entry(5, "2026-10-12", "006104-1762", 0.3)],
               ("2026-10-06", "2026-10-18"))
    db.ingest_timecard(cx, wide, "bbb", "part 2.csv", sender="assistant@gallagherllp.com")
    live = cx.execute("SELECT COUNT(*) FROM entries WHERE superseded=0").fetchone()[0]
    assert live == 3
    assert cx.execute("SELECT COUNT(*) FROM narratives").fetchone()[0] == 1
    assert cx.execute("SELECT sender FROM snapshots WHERE sha256='bbb'").fetchone()[0]


def test_migration_copies_history_once(tmp_path):
    corpus = tmp_path / "corpus.db"
    c = sqlite3.connect(corpus)
    c.executescript("""
        CREATE TABLE entries (id INTEGER PRIMARY KEY, work_date TEXT, matter TEXT,
          matter_name TEXT, client TEXT, hours REAL, narrative TEXT, status TEXT,
          period TEXT, src_file TEXT, superseded INTEGER DEFAULT 0);
        CREATE TABLE narratives (id INTEGER PRIMARY KEY, work_date TEXT, matter TEXT,
          narrative TEXT, origin TEXT, src_file TEXT);
        CREATE TABLE anomalies (id INTEGER PRIMARY KEY, detected TEXT, period TEXT,
          matter TEXT, kind TEXT, token TEXT, n_entries INTEGER, hours REAL, note TEXT);
        INSERT INTO entries VALUES (1,'2026-09-03','006104-1717','Gramercy','BMC',0.1,'','Posted','2026-09','timecards/a.csv',0);
        INSERT INTO narratives VALUES (1,'2026-09-02','006104-1717','Draft notice for Alexa Lutchen (422);','submitted','timecards/a.csv');
        INSERT INTO narratives VALUES (2,'2026-10-02','006104-1717','Follow up regarding Lutchen;','submitted','timecards/b.csv');
        INSERT INTO anomalies VALUES (1,'2026-09-05','2026-09','006104-0796','export_gap','ballston',6,1.0,'');
    """)
    c.commit()
    c.close()
    cx = db.connect(tmp_path / "te.db")
    res = db.migrate_from_corpus(cx, corpus)
    assert (res["entries"], res["narratives"], res["anomalies"]) == (1, 2, 1)
    assert db.migrate_from_corpus(cx, corpus)["skipped"]
    assert len(db.narrative_rows(cx, before="2026-10-01")) == 1


# =============================================================================
# Backtest matcher and hand-run loader
# =============================================================================

def test_basis_hit_full_prefix_and_short_subjects():
    basis = ("10/6 16:02 'RE: Marina Bay/Tenant Class Action -- class member "
             "information' to G. Sapire; 9/12 'RE: Suffolk v. 19Bway' (six "
             "replies); 09:15 'RE: Questions' to K. Grossbard")
    assert backtest.basis_hit({"subj": "RE: Marina Bay/Tenant Class Action -- "
                                       "class member information"}, basis)
    assert backtest.basis_hit({"subj": "RE: Suffolk v. 19Bway - Notice of Service "
                                       "of Depo Subpoena to Bozzuto"}, basis)
    assert backtest.basis_hit({"subj": "Questions", "sent_at": "2026-09-12T09:15:00"}, basis)
    assert not backtest.basis_hit({"subj": "RE: Suffolk Construction invoice"}, basis)
    assert not backtest.basis_hit({"subj": "RE: Lease"}, basis)


def test_hand_drafts_py_is_parsed_not_executed(tmp_path):
    p = tmp_path / "x_drafts.py"
    p.write_text(
        'D = {}\n'
        'def add(*a): pass\n'
        'add("2026-10-06","006104-1334",0.1,"Review summons;","High","10/6 09:53 \'RE: Summons\'","")\n'
        'add("2026-10-06","006104-1334",0.1,"Second;","Medium","b","")\n'
        'fix("2026-10-06","006104-1334",0.1, idx=1, conf="REVIEW")\n', encoding="utf-8")
    rows = backtest.load_hand(p)
    assert [r["confidence"] for r in rows] == ["High", "REVIEW"]
    bad = tmp_path / "bad_drafts.py"
    bad.write_text('add(__import__("os").getcwd(),"m",0.1,"n","High","b")\n',
                   encoding="utf-8")
    with pytest.raises(ValueError):
        backtest.load_hand(bad)


def test_paths_honor_config_root(tmp_path):
    root = tmp_path / "Time Entries"
    root.mkdir()
    paths = get_paths({"time_entries_root": str(root)}, tmp_path, argv=[])
    assert paths["root"] == root
    assert paths["db"] == tmp_path / "time_entries_data" / "te.db"
