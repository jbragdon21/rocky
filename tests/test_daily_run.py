"""Daily run (process_case_folder): reports the case instructions ask for,
filing into nested folders, the list of files changed since the last run,
and the larger text caps. Claude is faked.

    python -m pytest tests/test_daily_run.py -q
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rocky  # noqa: E402


class FakeClient:
    """Returns a canned daily-run reply; records each call's arguments."""

    def __init__(self, reply: dict):
        self.reply = reply
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(
            text=json.dumps(self.reply))])

    @property
    def prompt(self) -> str:
        return self.calls[-1]["messages"][0]["content"]


def _reply(**kw) -> dict:
    return {"analysis": "Screened the brief.", "file_actions": [],
            "recommendations": [], "internal_suggestions": [], **kw}


def _case(tmp_path, *folders) -> Path:
    case = tmp_path / "Multifamily Feud (RRID-0045)"
    (case / "Raw Documents").mkdir(parents=True)
    (case / "CLAUDE.md").write_text("Screen each CNS brief.", encoding="utf-8")
    for f in folders:
        (case / f).mkdir(parents=True, exist_ok=True)
    return case


def _run(case, client):
    return rocky.process_case_folder(client, case, "Multifamily Feud",
                                     "RRID-0045", "")


def _events(case, name=None):
    events = [json.loads(line) for line in
              (case / "activity.jsonl").read_text(encoding="utf-8").splitlines()]
    return [e for e in events if name is None or e.get("event") == name]


def _age(path, hours):
    t = time.time() - hours * 3600
    os.utime(path, (t, t))


def test_brief_filed_into_nested_folder_and_hit_report_written(tmp_path):
    case = _case(tmp_path, "Courthouse News/Courthouse News Reports Daily Briefs",
                 "Courthouse News/Hit Reports")
    brief = "x" * 15000 + " Cordeiro v. GREP Atlantic certified. " + "y" * 3000
    (case / "Raw Documents" / "cns.txt").write_text(brief, encoding="utf-8")
    report = "# CNS Hit Report: 2026-10-26\n\n## Cordeiro v. GREP Atlantic\n- Certified."
    client = FakeClient(_reply(
        file_actions=[{"source_raw": "cns.txt",
                       "target_folder": "Courthouse News/Courthouse News Reports Daily Briefs",
                       "suggested_name": "CNS Daily Brief 2026-10-26",
                       "summary": "CNS brief"}],
        reports=[{"folder": "Courthouse News/Hit Reports",
                  "name": "2026-10-26 CNS Hit Report", "text": report}]))
    result = _run(case, client)

    # The brief's text past 5,000 characters reached the screen.
    assert "Cordeiro v. GREP Atlantic certified." in client.prompt
    assert client.calls[-1]["max_tokens"] == rocky.DAILY_RUN_MAX_TOKENS
    filed = (case / "Courthouse News" / "Courthouse News Reports Daily Briefs"
             / "CNS Daily Brief 2026-10-26.txt")
    assert filed.read_text(encoding="utf-8") == brief
    index = json.loads((case / "master_file_index.json").read_text(encoding="utf-8"))
    assert index["files"][0]["target_folder"] == \
        "Courthouse News/Courthouse News Reports Daily Briefs"
    hit = case / "Courthouse News" / "Hit Reports" / "2026-10-26 CNS Hit Report.md"
    assert hit.read_text(encoding="utf-8") == report + "\n"
    assert not list((case / "Courthouse News" / "Hit Reports").glob("*.partial"))
    assert result["processed"] == 1 and result["reports"] == 1
    written = _events(case, "report_written")
    assert written[0]["path"] == "Courthouse News/Hit Reports/2026-10-26 CNS Hit Report.md"
    assert written[0]["summary"] == "CNS Hit Report: 2026-10-26"
    run = _events(case, "daily_run")[0]
    assert run["reports_requested"] == 1


def test_no_report_when_the_run_returns_none(tmp_path):
    case = _case(tmp_path, "Courthouse News/Hit Reports")
    result = _run(case, FakeClient(_reply()))
    assert result["reports"] == 0
    assert not list((case / "Courthouse News" / "Hit Reports").iterdir())
    assert not _events(case, "report_written")


def test_report_guardrails(tmp_path):
    case = _case(tmp_path, "Courthouse News/Hit Reports", ".hidden")
    (case.parent / "outside").mkdir()
    ok = {"folder": "Courthouse News\\Hit Reports", "text": "# Hit"}
    bad = [{"folder": "../outside", "name": "a", "text": "t"},
           {"folder": "Raw Documents", "name": "a", "text": "t"},
           {"folder": "Courthouse News/Missing", "name": "a", "text": "t"},
           {"folder": ".hidden", "name": "a", "text": "t"},
           {"folder": "Courthouse News/Hit Reports", "name": "a", "text": "  "},
           {"folder": "Courthouse News/Hit Reports", "name": "  ", "text": "t"},
           "not a dict"]
    assert rocky.write_case_reports(case, "RRID-0045", bad[:5]) == []
    assert rocky.write_case_reports(case, "RRID-0045", bad[5:]) == []
    written = rocky.write_case_reports(
        case, "RRID-0045", [{**ok, "name": "_ 2026/10/26: Hit?.md"}])
    assert [p.name for p in written] == ["2026-10-26- Hit-.md"]
    assert not list((case.parent / "outside").iterdir())
    assert not list((case / "Raw Documents").iterdir())

    # Never over an existing file.
    hits = case / "Courthouse News" / "Hit Reports"
    (hits / "Same.md").write_text("first", encoding="utf-8")
    again = rocky.write_case_reports(case, "RRID-0045",
                                     [{**ok, "name": "Same"}, {**ok, "name": "Same"}])
    assert [p.name for p in again] == ["Same (2).md", "Same (3).md"]
    assert (hits / "Same.md").read_text(encoding="utf-8") == "first"

    # At most DAILY_RUN_REPORTS_MAX in one run.
    many = [{**ok, "name": f"R{i}"} for i in range(rocky.DAILY_RUN_REPORTS_MAX + 3)]
    assert len(rocky.write_case_reports(case, "RRID-0045", many)) == \
        rocky.DAILY_RUN_REPORTS_MAX
    assert rocky.write_case_reports(case, "RRID-0045", "nope") == []


def test_case_subfolder_paths(tmp_path):
    case = _case(tmp_path, "Courthouse News/Hit Reports", "_project/x")
    assert rocky.case_subfolder(case, "Courthouse News/Hit Reports") == \
        case / "Courthouse News" / "Hit Reports"
    assert rocky.case_subfolder(case, "/Courthouse News\\Hit Reports/") == \
        case / "Courthouse News" / "Hit Reports"
    for rel in ("", "..", "Courthouse News/../..", "_project/x", "Raw Documents",
                "Courthouse News/Nope", None):
        assert rocky.case_subfolder(case, rel) is None


def test_missing_nested_target_falls_back(tmp_path):
    case = _case(tmp_path, "Courthouse News", "Miscellaneous")
    (case / "Raw Documents" / "a.txt").write_text("hello", encoding="utf-8")
    _run(case, FakeClient(_reply(file_actions=[
        {"source_raw": "a.txt", "target_folder": "Courthouse News/Nope",
         "suggested_name": "A", "summary": "s"}])))
    assert (case / "Miscellaneous" / "A.txt").is_file()
    assert not (case / "Courthouse News" / "Nope").exists()


def test_tracked_case_pleadings_stay_out_of_the_case_timeline(tmp_path, monkeypatch):
    case = _case(tmp_path, "Tracked Cases/Active/TC01 Smith/Pleadings", "Pleadings")
    for name in ("order.txt", "ours.txt"):
        (case / "Raw Documents" / name).write_text("ORDER", encoding="utf-8")
    calls = []
    monkeypatch.setattr(rocky, "log_pleading_facts",
                        lambda *a, **k: calls.append(a[4]))
    _run(case, FakeClient(_reply(file_actions=[
        {"source_raw": "order.txt",
         "target_folder": "Tracked Cases/Active/TC01 Smith/Pleadings",
         "suggested_name": "TC01 Order", "summary": "s"},
        {"source_raw": "ours.txt", "target_folder": "Pleadings",
         "suggested_name": "Our Order", "summary": "s"}])))
    assert (case / "Tracked Cases" / "Active" / "TC01 Smith" / "Pleadings"
            / "TC01 Order.txt").is_file()
    assert calls == ["Pleadings/Our Order.txt"]


def test_changed_files_listed_since_the_last_run(tmp_path):
    case = _case(tmp_path, "Tracked Cases/Active/TC01 Smith/Pleadings",
                 "Pleadings", "_archive")
    last = datetime.now(timezone.utc) - timedelta(hours=2)
    (case / "activity.jsonl").write_text(json.dumps(
        {"timestamp": last.isoformat(), "actor": "rocky", "event": "daily_run",
         "rrid": "RRID-0045"}) + "\n", encoding="utf-8")
    new = case / "Tracked Cases" / "Active" / "TC01 Smith" / "Pleadings" / "Order.txt"
    new.write_text("Judge granted the motion to dismiss.", encoding="utf-8")
    old = case / "Tracked Cases" / "Active" / "TC01 Smith" / "Old.txt"
    old.write_text("stale", encoding="utf-8")
    _age(old, 3)
    (case / "_archive" / "a.txt").write_text("archived", encoding="utf-8")
    (case / "Tracked Cases" / "~$lock.docx").write_text("x", encoding="utf-8")
    (case / "Raw Documents" / "raw.txt").write_text("raw email", encoding="utf-8")
    (case / "Pleadings" / "Filed.txt").write_text("rocky filed", encoding="utf-8")
    (case / "master_file_index.json").write_text(json.dumps(
        {"rrid": "RRID-0045", "files": [{"path": "Pleadings/Filed.txt",
                                         "source_raw": "filed.txt"}]}),
        encoding="utf-8")
    (case / "CASE_SPINE.md").write_text("# Spine", encoding="utf-8")

    client = FakeClient(_reply())
    _run(case, client)
    block = client.prompt.split("FILES ADDED OR CHANGED SINCE THE LAST DAILY RUN")[1]
    assert "(outside Raw Documents; 2 file(s))" in block
    assert "Tracked Cases/Active/TC01 Smith/Pleadings/Order.txt" in block
    assert "Judge granted the motion to dismiss." in block       # excerpt
    assert "CASE_SPINE.md" in block
    for left_out in ("Old.txt", "_archive", "~$lock", "raw.txt", "Filed.txt",
                     "activity.jsonl", "master_file_index.json", "CLAUDE.md"):
        assert left_out not in block.split("Global instructions")[0]
    assert _events(case, "daily_run")[-1]["changed_files_seen"] == 2


def test_changed_files_skip_rocky_reports_and_cap_excerpts(tmp_path, monkeypatch):
    case = _case(tmp_path, "Courthouse News/Hit Reports")
    report = case / "Courthouse News" / "Hit Reports" / "R.md"
    report.write_text("# report", encoding="utf-8")
    now = datetime.now(timezone.utc)
    (case / "activity.jsonl").write_text(
        json.dumps({"timestamp": (now - timedelta(hours=1)).isoformat(),
                    "actor": "rocky", "event": "report_written",
                    "path": "Courthouse News/Hit Reports/R.md"}) + "\n",
        encoding="utf-8")
    _age(case / "CLAUDE.md", 48)
    files = []
    for i in range(4):
        p = case / f"memo{i}.txt"
        p.write_text("m" * 10000, encoding="utf-8")
        files.append(p)
    monkeypatch.setattr(rocky, "CHANGED_TEXT_CAP_TOTAL", 7000)
    client = FakeClient(_reply())
    _run(case, client)
    block = client.prompt.split("FILES ADDED OR CHANGED SINCE THE LAST DAILY RUN")[1]
    assert "R.md" not in block                     # Rocky's own report
    assert "4 file(s)" in block
    assert block.count("m" * 3000) == 2 and "m" * 3001 not in block
    assert block.count("[...truncated...]") == 3   # 3000, 3000, then 1000
