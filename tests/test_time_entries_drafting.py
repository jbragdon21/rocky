"""Time Entries phase 2: day sheets, the drafting and verification loop (on
a scripted stand-in for the Claude client), Rocky's checks, the
deterministic diff, links, the workbook and the backtest judge. No network.

    python -m pytest tests/test_time_entries_drafting.py -q
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from time_entries import daysheet, db, draft, evaluate, learn, mail, reference, run, workbook  # noqa: E402

S = reference.load_settings(None)


def email(i, d, hhmm, subject, body="", to="T. Jarrett", conv=None):
    e = {"id": f"<m{i}@x>", "conversation_id": conv, "date": d,
         "sent_at": f"{d}T{hhmm}:00", "subject": subject, "to": to, "cc": "",
         "body": body, "inv": False}
    e["hay"] = mail.make_hay(e)
    return e


def entry(row, d, matter, hours, narr="", name="Some Property"):
    return {"row": row, "work_date": d, "matter": matter, "matter_name": name,
            "client": "BMC", "hours": hours, "narrative": narr,
            "non_billable": False, "no_charge": False}


def make_sheet(entries=None, emails=None, results=None, thread_fn=None, dates=None):
    entries = entries or [entry(2, "2026-09-01", "006104-1421", 0.3),
                          entry(3, "2026-09-01", "006104-1421", 0.2, "Respond re: Hall;"),
                          entry(4, "2026-09-01", "006104-0948", 0.4,
                                "Review Marina Bay class list and send to G. Sapire;")]
    emails = emails if emails is not None else [
        email(1, "2026-09-01", "10:02", "RE: Hettinger conservice", "Agreed, file it.\nFrom: X"),
        email(2, "2026-09-01", "15:40", "RE: Hall unit 412", "Mr. Hall can stay through 9/30."),
        email(3, "2026-09-02", "07:31", "Hettinger next steps", "Morning batch text"),
        email(4, "2026-09-02", "11:00", "Later on 9/2", "not on the sheet"),
    ]
    by_date = {}
    for e in emails:
        by_date.setdefault(e["date"], []).append(e)
    work = {e["row"] for e in entries if len(e["narrative"]) < 35}
    results = results or {2: {"cands": [{"id": "<m1@x>", "score": 6, "hits": ["hettinger"]}],
                              "stage": "same-day", "tokens": ["hettinger"]},
                          3: {"cands": [], "tokens": ["novel"], "zero_hit_token": "novel"}}
    return daysheet.DaySheet(dates or ["2026-09-01"], entries, work, by_date, results,
                             {"006104-1421": {"property": "Novel South Capitol"}},
                             {}, {"006104-1421": [("2026-08-28", "Review Hettinger memo;")]},
                             S, thread_fn=thread_fn, all_mail=emails)


# =============================================================================
# Mail text and day sheets
# =============================================================================

def test_own_text_strips_quote_and_signature():
    body = ("Sounds good, please file.\n\nJames D. Bragdon\nP: 410 951 1416\n"
            "From: Someone\nSent: Monday\nold text")
    assert mail.own_text(body) == "Sounds good, please file."
    assert mail.own_text("x" * 50, 10) == "x" * 10 + " …"


def test_group_days_weekend_rides_with_monday():
    groups = daysheet.group_days(["2026-09-04", "2026-09-05", "2026-09-06", "2026-09-08"])
    # Fri alone; Sat + Sun + Monday 9/7 (pulled in for its batch); Tue alone
    assert groups == [["2026-09-04"], ["2026-09-05", "2026-09-06", "2026-09-07"],
                      ["2026-09-08"]]
    # a Saturday alone still brings Sunday's mail and Monday's batch
    assert daysheet.group_days(["2026-09-05"]) == [["2026-09-05", "2026-09-06",
                                                    "2026-09-07"]]


def test_sheet_refs_partial_next_morning_and_candidates():
    sh = make_sheet()
    assert list(sh.entry_refs) == ["E1", "E2"]           # the filled entry gets no ref
    assert 'PARTIAL: "Respond re: Hall;"' in sh.text
    assert "written: \"Review Marina Bay class list" in sh.text
    assert "next morning, before 9:00" in sh.text
    assert "Hettinger next steps" in sh.text
    assert "Later on 9/2" not in sh.text                  # after the batch hour
    assert "E1 006104-1421: M1 (6: hettinger)" in sh.text
    assert 'the token "novel" has zero hits' in sh.text
    assert "2026-08-28: Review Hettinger memo;" in sh.text
    assert sh.email("m1")["id"] == "<m1@x>"


def test_sheet_tools_read_and_search():
    chain = [{"id": "<in@x>", "direction": "received", "sent_at": "2026-09-01T09:50:00",
              "from": "T. Jarrett", "body": "Should we file?"},
             {"id": "<m1@x>", "direction": "sent", "sent_at": "2026-09-01T10:02:00",
              "from": "James", "body": "Agreed, file it."}]
    sh = make_sheet(thread_fn=lambda cid: chain if cid else [])
    sh.mail_refs["M1"]["conversation_id"] = "c1"
    out = sh.read_email(["M1", "M99"])
    assert "Should we file?" in out and "<== this email" in out
    assert "M99] no such email ref" in out
    found = sh.search_mail("unit 412", "2026-08-25", "2026-09-05")
    assert "RE: Hall unit 412" in found
    assert "No sends" in sh.search_mail("zzzz", "2026-09-01", "2026-09-02")


def test_prior_narratives_respects_backtest_cutoff():
    rows = [("2026-08-28", "A", "Review the Hettinger memo;"),
            ("2026-09-02", "A", "James's final text for the scored day;")]
    p = daysheet.prior_narratives(rows, {"A"}, ["2026-09-01"], before="2026-09-01")
    assert [n for _d, n in p["A"]] == ["Review the Hettinger memo;"]


# =============================================================================
# Rocky's checks
# =============================================================================

def good_rows():
    return {"E1": {"entry": "E1", "narrative": "Advise filing in Hettinger;",
                   "confidence": "High", "basis": "9/1 10:02", "basis_refs": ["M1"], "note": ""},
            "E2": {"entry": "E2", "narrative": "Respond re: Hall; Confirm stay through 9/30;",
                   "confidence": "High", "basis": "9/1 15:40", "basis_refs": ["M2"],
                   "note": ""}}


def test_check_rows_passes_good_set():
    assert draft.check_rows(make_sheet(), good_rows()) == []


def test_check_rows_catches_each_rule():
    sh = make_sheet()
    rows = good_rows()
    rows["E2"]["narrative"] = "Respond to Hall — stay approved"
    rows["E2"]["basis_refs"] = ["M1"]
    del rows["E1"]
    rows["E1"] = {"entry": "E1", "narrative": "x;", "confidence": "High", "basis": "",
                  "basis_refs": ["M1", "M42"], "note": ""}
    errs = "\n".join(draft.check_rows(sh, rows))
    assert 'must begin with "Respond re: Hall;"' in errs
    assert "end with a semicolon" in errs
    assert "em dash" in errs
    assert "M42 is not an email" in errs
    assert "M1 is the basis for E2, E1" in errs or "M1 is the basis for E1, E2" in errs
    # a note that says the work was split clears the double cite
    rows = good_rows()
    rows["E2"]["basis_refs"] = ["M1", "M2"]
    rows["E2"]["note"] = "Thread split with E1."
    assert draft.check_rows(sh, rows) == []


def test_settle_fixes_mechanics_and_reviews_the_rest():
    sh = make_sheet()
    rows = {"E1": {"entry": "E1", "narrative": "Advise filing — Hettinger",
                   "confidence": "High", "basis": "", "basis_refs": ["M1", "M77"], "note": ""}}
    out, done = draft.settle(sh, rows)
    assert out["E1"]["narrative"] == "Advise filing, Hettinger;"
    assert out["E1"]["basis_refs"] == ["M1"] and out["E1"]["confidence"] == "High"
    assert out["E2"]["confidence"] == "REVIEW"
    assert out["E2"]["narrative"] == "Respond re: Hall;"    # James's start kept
    assert any("no draft returned" in d for d in done)


# =============================================================================
# The conversation loop on a scripted client
# =============================================================================

def tool_use(i, name, inp):
    return SimpleNamespace(type="tool_use", id=f"t{i}", name=name, input=inp)


def resp(*blocks, stop="tool_use"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=100, output_tokens=50,
                                                 cache_read_input_tokens=1000,
                                                 cache_creation_input_tokens=0))


class FakeClient:
    """Replays scripted responses; records each request."""

    def __init__(self, script, create_script=()):
        self.script = list(script)
        self.create_script = list(create_script)
        self.requests = []
        outer = self

        class _Stream:
            def __init__(self, kw):
                outer.requests.append({**kw, "messages": list(kw["messages"])})

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get_final_message(self):
                return outer.script.pop(0)

        class _Messages:
            def stream(self, **kw):
                return _Stream(kw)

            def create(self, **kw):
                outer.requests.append(kw)
                return outer.create_script.pop(0)

        self.beta = SimpleNamespace(messages=_Messages())


def test_draft_sheet_loop_retry_and_verify():
    rows = list(good_rows().values())
    bad = [dict(rows[0], narrative="Advise filing in Hettinger"), rows[1]]
    fix = dict(rows[1], narrative="Respond re: Hall; Confirm Mr. Hall may stay through 9/30;",
               problem="Draft missed the stay date.")
    client = FakeClient([
        resp(tool_use(1, "read_email", {"refs": ["M1"]})),
        resp(tool_use(2, "submit_drafts", {"drafts": bad, "uncaptured": [], "summary": ""})),
        resp(tool_use(3, "submit_drafts", {"drafts": rows, "summary": "ok", "uncaptured": [
            {"date": "2026-09-01", "matter": "(unknown)", "matter_name": "Vendor",
             "hours": 0.1, "narrative": "Review vendor note;", "basis": "",
             "basis_refs": ["M3"], "note": ""}]})),
        resp(tool_use(4, "submit_review", {"fixes": [fix], "summary": "one fix"})),
    ])
    sh = make_sheet()
    out = draft.draft_sheet(client, sh, "STYLE", "LESSON ONE")
    assert out["errors"] == []
    assert out["rows"]["E2"]["narrative"].startswith("Respond re: Hall; Confirm Mr. Hall")
    assert out["rows"]["E2"]["first"]["narrative"] == rows[1]["narrative"]
    assert out["rows"]["E2"]["problem"] == "Draft missed the stay date."
    assert out["fixes"][0]["entry"] == "E2"
    assert len(out["uncaptured"]) == 1
    assert out["usage"]["calls"] == 4
    # the failed check went back as an error result, listing the problem
    retry = client.requests[2]["messages"][-1]["content"][0]
    assert retry["is_error"] and "semicolon" in retry["content"]
    # read_email's answer went back as a tool result
    assert "Agreed, file it." in client.requests[1]["messages"][-1]["content"][0]["content"]
    # system prompt carries the style guide and lessons, cached; fallbacks on
    req = client.requests[0]
    assert "STYLE" in req["system"][0]["text"] and "LESSON ONE" in req["system"][0]["text"]
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert req["fallbacks"] == "default" and req["output_config"] == {"effort": "high"}
    assert all(t.get("strict") for t in req["tools"])


def test_draft_sheet_never_submits_becomes_review():
    text = SimpleNamespace(type="text", text="I think...")
    client = FakeClient([resp(text, stop="end_turn"), resp(text, stop="end_turn")])
    out = draft.draft_sheet(client, make_sheet(), "", "")
    assert {r["confidence"] for r in out["rows"].values()} == {"REVIEW"}
    assert out["errors"]


def test_flatten_maps_refs_to_message_ids():
    sh = make_sheet()
    rows = {k: dict(v, first=dict(v), problem="") for k, v in good_rows().items()}
    out = {"rows": rows, "uncaptured": [{"date": "2026-09-01", "matter": "(unknown)",
                                         "hours": 0.1, "narrative": "x;", "basis": "",
                                         "basis_refs": ["M3"], "note": ""}]}
    recs = run.flatten([sh], [out], learn.ordinals(sh.entries))
    assert recs[0]["basis_ids"] == ["<m1@x>"] and recs[0]["ordinal"] == 0
    assert recs[1]["start"] == "Respond re: Hall;" and recs[1]["ordinal"] == 0
    assert recs[2]["kind"] == "uncaptured" and recs[2]["basis_ids"] == ["<m3@x>"]


# =============================================================================
# The deterministic diff and links
# =============================================================================

def rec(d, matter, hours, narr, conf="High", ordinal=0, kind="draft", ids=("<m1@x>",)):
    return {"kind": kind, "work_date": d, "matter": matter, "hours": hours,
            "ordinal": ordinal, "narrative": narr, "confidence": conf,
            "basis_ids": list(ids), "start": ""}


def test_diff_outcomes():
    early = [entry(1, "2026-09-01", "A", 0.2), entry(2, "2026-09-01", "A", 0.2)]
    final = [
        entry(1, "2026-09-01", "A", 0.2, "Review lease for Jane Roe (101) and advise client;"),
        entry(2, "2026-09-01", "A", 0.2, "Call with opposing counsel regarding schedule;"),
        entry(3, "2026-09-01", "B", 0.5, "Draft complaint for breach of lease against R. Doe;"),
        entry(4, "2026-09-02", "C", 0.3, "Respond re: vendor contract question for site;"),
        entry(5, "2026-09-02", "D", 0.1, ""),
        entry(6, "2026-09-03", "E", 0.4, "Prepare follow up regarding Kevin Johnson claim;"),
        entry(7, "2026-09-03", "F", 0.6, "Prepare response to OAG inquiry about junk fees;"),
    ]
    recs = [
        rec("2026-09-01", "A", 0.2, "Review lease for Jane Roe (101) and advise client;"),
        rec("2026-09-01", "A", 0.2, "Status check on Roe nonrenewal;", ordinal=1),
        rec("2026-09-01", "X", 0.5, "Draft complaint for breach of lease against R. Doe;"),
        rec("2026-09-02", "C", 0.3, "", conf="REVIEW"),
        rec("2026-09-02", "D", 0.1, "Status check;"),
        rec("2026-09-03", "E", 0.2, "Prepare follow up regarding Kevin Johnson claims;"),
        rec("2026-09-04", "Z", 0.1, "Gone;"),
        rec("2026-09-03", "F", 0.3, "Respond to OAG;", kind="uncaptured"),
    ]
    outs = learn.diff(recs, early, final)
    got = [o["outcome"] for o in outs]
    assert got == ["accepted", "rewritten", "recoded", "review_filled", "still_blank",
                   "rehoured", "deleted", "uncaptured_taken"]
    assert outs[5]["text_outcome"] == "accepted"


def test_links_for_and_upsert(tmp_path):
    mails = {"<m1@x>": {"conversation_id": "c1", "sent_at": "2026-09-01T10:02:00",
                        "subject": "RE: x"}}
    r = rec("2026-09-01", "A", 0.2, "x;")
    assert learn.links_for(r, {"outcome": "still_blank"}, mails) == []
    ok = learn.links_for(r, {"outcome": "edited"}, mails, src_file="later.csv")
    assert ok[0]["verdict"] == "confirmed" and ok[0]["source"] == "submitted"
    assert ok[0]["conversation_id"] == "c1"
    no = learn.links_for(r, {"outcome": "recoded", "final": None}, mails, src_file="l2.csv")
    assert no[0]["verdict"] == "rejected" and no[0]["matter"] == "A"

    cx = db.connect(tmp_path / "te.db")
    assert db.upsert_link(cx, ok[0]) == "new"
    assert db.upsert_link(cx, ok[0]) == "same"
    inferred = dict(no[0], source="inferred")
    assert db.upsert_link(cx, inferred) == "kept"      # submitted outranks inferred
    assert db.upsert_link(cx, no[0]) == "flipped"
    row = cx.execute("SELECT verdict, flipped_by FROM links").fetchone()
    assert tuple(row) == ("rejected", "l2.csv")


def test_store_recommendations_and_outcomes(tmp_path):
    cx = db.connect(tmp_path / "te.db")
    rid = db.record_run(cx, "draft", {}, {})
    ids = db.store_recommendations(cx, rid, [dict(rec("2026-09-01", "A", 0.2, "x;"),
                                                  first={"narrative": "y;",
                                                         "confidence": "Medium"},
                                                  problem="fixed")])
    db.store_outcomes(cx, rid, [{"recommendation_id": ids[0], "outcome": "accepted",
                                 "similarity": 1.0}])
    r = cx.execute("SELECT first_narrative, first_confidence, verify_problem, basis_ids "
                   "FROM recommendations").fetchone()
    assert tuple(r) == ("y;", "Medium", "fixed", '["<m1@x>"]')
    assert cx.execute("SELECT outcome FROM outcomes").fetchone()[0] == "accepted"


def test_workbook_house_format(tmp_path):
    import openpyxl
    recs = [dict(rec("2026-09-01", "A", 0.2, "Review lease;"), basis="9/1 10:02 'RE: x'",
                 note="", problem="Was the subject line.", first={"confidence": "Medium"}),
            dict(rec("2026-09-01", "(unknown)", 0.1, "Respond;", kind="uncaptured"))]
    p = workbook.build(tmp_path / "out.xlsx", recs,
                       {"title": "T", "period": "p", "results": "r", "decide": ["one"]})
    wb = openpyxl.load_workbook(p)
    assert wb.sheetnames == ["Read Me", "Draft Narratives", "Uncaptured Emails", "Summary"]
    ws = wb["Draft Narratives"]
    assert ws["G2"].value == "High" and ws["G2"].fill.fgColor.rgb.endswith("E2EFDA")
    assert ws["J2"].value == "Was the subject line. (was Medium)"
    assert ws["K2"].value == "<m1@x>" and ws.column_dimensions["K"].hidden


# =============================================================================
# The drafting backtest
# =============================================================================

def test_score_rows_and_judge_and_summary():
    early = [entry(1, "2026-09-01", "A", 0.2), entry(2, "2026-09-01", "B", 0.3)]
    final = [entry(1, "2026-09-01", "A", 0.2, "Review lease for Jane Roe (101) and advise;"),
             entry(2, "2026-09-01", "B", 0.3, "")]
    recs = [dict(rec("2026-09-01", "A", 0.2, "Review Roe lease and advise client;"),
                 first={"narrative": "Review lease;", "confidence": "Medium"}),
            rec("2026-09-01", "B", 0.3, "Something;")]
    hand = [{"date": "2026-09-01", "matter": "A", "hours": 0.2, "confidence": "High",
             "narrative": "Review lease for Jane Roe (101) and advise;", "basis": ""}]
    scored, outs = evaluate.score_rows(recs, early, final, hand)
    assert len(scored) == 1                    # B is still blank: not scored
    assert scored[0]["hand_sim"] == 1.0

    def answer(kw):
        # map whatever labels the shuffle produced back to grades
        text = kw["messages"][0]["content"]
        grades = []
        for line in text.splitlines():
            line = line.strip()
            if line[:2] in ("A:", "B:", "C:"):
                g = "same" if "Jane Roe" in line or "Roe lease" in line else "partial"
                grades.append({"row": 1, "label": line[0], "grade": g, "why": ""})
        return resp(SimpleNamespace(type="text", text=json.dumps({"grades": grades})),
                    stop="end_turn")

    class JudgeClient(FakeClient):
        def __init__(self):
            super().__init__([])
            outer = self

            class _M:
                def create(self, **kw):
                    outer.requests.append(kw)
                    return answer(kw)
            self.beta = SimpleNamespace(messages=_M())

    jc = JudgeClient()
    usage = evaluate.judge(jc, scored)
    assert usage.calls == 1
    fmt = jc.requests[0]["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    row = scored[0]
    assert row["grade_rocky"] == "same" and row["grade_hand"] == "same"
    assert row["grade_first"] == "partial"
    s = evaluate.summarize(scored, outs)
    assert s["rocky_High"]["same"] == 1 and s["hand_High"]["same_rate"] == 1.0
    assert s["passed"] is True
    rep = evaluate.format_report("t", s, {"cost_usd": 1, "calls": 2}, usage.as_dict(), scored)
    assert "PASSED" in rep


def test_hand_recs_resolve_basis():
    emails = [email(1, "2026-09-01", "10:02", "RE: Hettinger conservice strategy"),
              email(2, "2026-09-07", "07:41", "RE: Weekend notice for Roe")]
    hand = [{"date": "2026-09-01", "matter": "A", "hours": 0.3, "confidence": "High",
             "narrative": "x;", "basis": "9/1 10:02 'RE: Hettinger conservice strategy' to T."},
            {"date": "2026-09-06", "matter": "B", "hours": 0.2, "confidence": "High",
             "narrative": "y;", "basis": "9/7 07:41 'RE: Weekend notice for Roe'"}]
    recs = evaluate.hand_recs(hand, emails)
    assert recs[0]["basis_ids"] == ["<m1@x>"]
    assert recs[1]["basis_ids"] == ["<m2@x>"]           # Monday batch, a day later
