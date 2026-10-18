"""Litigation Updater cleanup: the length-audit pre-filter and the
cleanup_fix flow in dry-run and live modes. Smartsheet and Claude are
mocked — nothing here touches the network.

    python -m pytest tests/test_litigation_cleanup.py -q
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import litigation_updater as lu  # noqa: E402

SS_TOKEN = "ss-SECRET-token-123"
API_KEY = "sk-ant-SECRET-key-456"

COLUMNS = ["Combined Claimant (Project)", "Entity", "Type of Case",
           "Case No.", "Summary of Claim", "Third Party Disclosure Summary"]

LONG_SUMMARY = (
    "On March 3, 2026, BMC received a summons in a lawsuit filed by Jane "
    "Roe, a resident at The Apollo, in the Superior Court of the District "
    "of Columbia, Case No. 2026-CAB-001234. Ms. Roe alleges that she first "
    "reported a leak under her kitchen sink on May 2, 2024. She alleges "
    "that maintenance visited on May 9, 2024 and replaced a gasket.\n"
    "She alleges the leak returned in July 2024 and that she sent three "
    "emails to the leasing office. She alleges that mold appeared in the "
    "cabinet in October 2024. She alleges that a vendor inspected on "
    "November 14, 2024 and recommended replacing the cabinet.\n"
    "She alleges the cabinet was not replaced until January 2025 and that "
    "she was displaced for four days. She alleges respiratory symptoms and "
    "a doctor's visit on February 2, 2025. She asserts claims for breach of "
    "the warranty of habitability, negligence, and violation of the D.C. "
    "Consumer Protection Procedures Act, and seeks $250,000 in damages.\n"
    "BMC denies liability and has tendered the claim to the owner's "
    "carrier, which has accepted coverage."
)
CONDENSED_SUMMARY = (
    "On March 3, 2026, BMC received a summons in a lawsuit filed by Jane "
    "Roe, a resident at The Apollo, in the Superior Court of the District "
    "of Columbia, Case No. 2026-CAB-001234. Ms. Roe alleges a kitchen leak "
    "reported from May 2024 was improperly remediated, causing mold and a "
    "brief displacement. She asserts breach of the warranty of "
    "habitability, negligence, and D.C. Consumer Protection Procedures Act "
    "claims and seeks $250,000. BMC denies liability and has tendered the "
    "claim to the owner's carrier, which has accepted coverage."
)
ROUTINE_SUMMARY = (
    "On April 1, 2026, BMC received notice of a slip and fall claim by Sam "
    "Poe. Mr. Poe alleges he slipped on ice on the front steps. He alleges "
    "the steps had not been salted that morning. He alleges a wrist "
    "injury. BMC has reported the claim to the owner's carrier."
)
ROUTINE_CONDENSED = (
    "On April 1, 2026, BMC received notice of a slip and fall claim by Sam "
    "Poe, who alleges he slipped on ice on the front steps. BMC has "
    "reported the claim to the owner's carrier."
)
SHORT_SUMMARY = ("On June 1, 2026, BMC received notice of a potential claim "
                 "by Lee Doe. BMC has reported it to the owner's carrier.")


def _sheet(rows: list[dict]) -> dict:
    cols = [{"id": 100 + i, "title": t, "primary": i == 0}
            for i, t in enumerate(COLUMNS)]
    by_title = {c["title"]: c["id"] for c in cols}
    return {"id": 1, "columns": cols, "rows": [
        {"id": r["id"], "cells": [{"columnId": by_title[t], "value": v}
                                  for t, v in r.items() if t != "id"]}
        for r in rows]}


ROWS = [
    {"id": 11, "Combined Claimant (Project)": "Jane Roe (The Apollo)",
     "Entity": "BMC", "Type of Case": "Consumer Protection",
     "Case No.": "2026-CAB-001234", "Summary of Claim": LONG_SUMMARY},
    {"id": 12, "Combined Claimant (Project)": "Sam Poe (Union Place)",
     "Entity": "BMC", "Type of Case": "Slip and Fall",
     "Summary of Claim": ROUTINE_SUMMARY},
    {"id": 13, "Combined Claimant (Project)": "Lee Doe (Meriel)",
     "Entity": "BMC", "Type of Case": "Bodily Injury",
     "Summary of Claim": SHORT_SUMMARY},
]

REWRITES = {"Jane Roe (The Apollo)": CONDENSED_SUMMARY,
            "Sam Poe (Union Place)": ROUTINE_CONDENSED}


class _Resp:
    def __init__(self, text):
        self.content = [type("B", (), {"text": text})()]
        self.stop_reason = "end_turn"


class FakeAnthropic:
    """Answers the cleanup prompts: rewrite requests get REWRITES[claim];
    the compliance review gets an empty JSON / a stub conventions memo."""
    prompts: list[str] = []
    rewrites: dict = REWRITES

    def __init__(self, api_key=None):
        self.messages = self

    def create(self, model, max_tokens, messages):
        prompt = messages[0]["content"]
        FakeAnthropic.prompts.append(prompt)
        if "Condense it." in prompt:
            claim = next(c for c in self.rewrites if f'"{c}"' in prompt)
            return _Resp(json.dumps({"proposed": self.rewrites[claim],
                                     "reason": "compressed the chronology",
                                     "dropped": "repair dates"}))
        if "CONVENTIONS memo" in prompt:
            return _Resp("# conventions stub")
        return _Resp(json.dumps({"fixes": [], "missing": []}))


@pytest.fixture
def env(tmp_path, monkeypatch):
    config = {"litigation_root": str(tmp_path / "Litigation Updates"),
              "smartsheet_token": SS_TOKEN, "anthropic_api_key": API_KEY,
              "litigation_claim_column": "Combined Claimant (Project)",
              "litigation_open_sheets": [{"key": "BMC", "sheet_id": 1,
                                          "entities": ["BMC"]}]}
    paths = lu.get_paths(config, tmp_path / "data")
    lu.ensure_dirs(paths)
    (paths["voices"] / "voice_summary.md").write_text("# summary voice",
                                                      encoding="utf-8")
    (paths["voices"] / "voice_disclosure.md").write_text("# disclosure",
                                                         encoding="utf-8")
    sheet = _sheet(ROWS)
    monkeypatch.setattr(lu, "fetch_open_sheets", lambda tok, cfg: [
        {**lu.open_sheet_specs(cfg)[0], "sheet": sheet}])
    monkeypatch.setattr(lu, "get_sheet", lambda tok, sid: sheet)
    writes: list = []
    monkeypatch.setattr(lu, "update_row",
                        lambda tok, sh, rid, cells: writes.append((rid, cells)))
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    FakeAnthropic.prompts = []
    FakeAnthropic.rewrites = REWRITES
    return {"config": config, "paths": paths, "sheet": sheet,
            "writes": writes}


def _length_asks(paths):
    return [a for a in lu.load_state(paths).get("asks", [])
            if a.get("check") == "length"]


# --- Pre-filter -----------------------------------------------------------

def test_metrics_ignore_abbreviation_periods():
    m = lu.text_metrics("Ms. Roe sued in the U.S. District Court on Jan. 5, "
                        "2026, No. 1:26-cv-1. BMC denies liability.")
    assert m == {"words": 17, "sentences": 2, "paragraphs": 1}


def test_metrics_count_paragraphs_as_lines():
    assert lu.text_metrics("One.\n\nTwo.\nThree.")["paragraphs"] == 3
    assert lu.text_metrics("")["sentences"] == 0


def _summary_limits(config=None, routine=False):
    limits = lu.length_limits(config or {})["Summary of Claim"]
    return lu._effective_limits(limits, routine)


def test_summary_word_threshold_boundary():
    limits = _summary_limits()
    at = " ".join(["word"] * 150) + "."
    assert lu.over_limits(lu.text_metrics(at), limits) == []
    over = lu.over_limits(lu.text_metrics(at[:-1] + " word."), limits)
    assert over == ["151 words > 150"]


def test_summary_sentence_and_paragraph_thresholds():
    limits = _summary_limits()
    seven = " ".join(f"Sentence {i} here." for i in range(7))
    assert lu.over_limits(lu.text_metrics(seven), limits) == []
    assert lu.over_limits(lu.text_metrics(seven + " Eight."), limits) == [
        "8 sentences > 7"]
    assert lu.over_limits(lu.text_metrics("One.\nTwo."), limits) == [
        "2 paragraphs > 1"]


def test_routine_claims_get_three_sentences():
    limits = _summary_limits(routine=True)
    assert limits["sentences"] == 3 and limits["words"] == 80
    assert lu.over_limits(lu.text_metrics("A. B. C."), limits) == []
    assert lu.over_limits(lu.text_metrics("A b. C d. E f. G h."), limits) == [
        "4 sentences > 3"]


def test_routine_detection():
    assert lu.is_routine({"Type of Case": "Slip & Fall"}, {})
    assert lu.is_routine({"Type of Case": "Habitability"}, {})
    assert not lu.is_routine({"Type of Case": "Class Action / Mold"}, {})
    assert not lu.is_routine({"Type of Case": "Class Action - slip"}, {})
    assert not lu.is_routine({"Type of Case": "Employment Discrimination"}, {})


def test_disclosure_limits_and_config_override():
    limits = lu.length_limits({})
    d = limits["Third Party Disclosure Summary"]
    assert (d["sentences"], d["paragraphs"]) == (5, 1)
    # Status/Action Items has no rule, so it isn't audited by default...
    assert "Status/Action Items" not in limits
    # ...but config can add it, and null drops a column.
    cfg = {"litigation_length_limits": {
        "Status/Action Items": {"words": 50},
        "Third Party Disclosure Summary": None}}
    limits = lu.length_limits(cfg)
    assert limits["Status/Action Items"] == {"words": 50}
    assert "Third Party Disclosure Summary" not in limits


def test_prefilter_flags_only_over_limit_cells(env):
    entries = lu.fetch_open_sheets(SS_TOKEN, env["config"])
    flagged = lu.length_prefilter(env["config"], entries)
    assert {f["row_id"] for f in flagged} == {11, 12}
    roe = next(f for f in flagged if f["row_id"] == 11)
    assert not roe["routine"] and "4 paragraphs > 1" in roe["over"]
    poe = next(f for f in flagged if f["row_id"] == 12)
    assert poe["routine"] and poe["over"] == ["5 sentences > 3"]


def test_facts_dropped_catches_money_and_case_number():
    row = {"Case No.": "2026-CAB-001234"}
    assert lu.facts_dropped(LONG_SUMMARY, CONDENSED_SUMMARY, row, {}) == []
    lost = lu.facts_dropped(LONG_SUMMARY, "BMC denies liability.", row, {})
    assert "$250,000" in lost and "2026-CAB-001234" in lost


# --- Cleanup flow ---------------------------------------------------------

def test_dry_run_reports_without_queueing(env):
    res = lu.run_cleanup(env["config"], env["paths"], None, dry_run=True,
                         length_only=True)
    assert res["length"] == {"would_queue": 2}
    assert lu.load_state(env["paths"]).get("asks") is None
    assert env["writes"] == []
    md = Path(res["report"]).read_text(encoding="utf-8")
    assert "DRY RUN" in md and CONDENSED_SUMMARY in md
    csv_text = Path(res["report"]).with_suffix(".csv").read_text(
        encoding="utf-8-sig")
    assert "would_queue" in csv_text and "Sam Poe" in csv_text
    # The rewrite prompt carries the voice guide and the routine rule.
    poe_prompt = next(p for p in FakeAnthropic.prompts if "Sam Poe" in p)
    assert "# summary voice" in poe_prompt and "ROUTINE claim" in poe_prompt


def test_live_run_queues_cleanup_fix_asks(env):
    res = lu.run_cleanup(env["config"], env["paths"], None, dry_run=False,
                         length_only=True)
    assert res["length"] == {"queued": 2}
    asks = _length_asks(env["paths"])
    assert [a["kind"] for a in asks] == ["cleanup_fix", "cleanup_fix"]
    roe = next(a for a in asks if a["row_id"] == 11)
    assert roe["status"] == "queued"
    assert roe["column"] == "Summary of Claim"
    assert roe["current"] == LONG_SUMMARY
    assert roe["proposed"] == CONDENSED_SUMMARY
    assert "4 paragraphs > 1" in roe["reason"]
    # Queued for the chat, never written directly.
    assert env["writes"] == []
    text = lu._proposal_text(roe)
    assert "Removed: repair dates" in text
    assert "Reply **YES**" in text


def test_rerun_does_not_requeue_same_cell(env):
    lu.run_cleanup(env["config"], env["paths"], None, False, length_only=True)
    state = lu.load_state(env["paths"])
    for a in state["asks"]:
        a["status"] = "declined"
    lu.save_state(env["paths"], state)
    res = lu.run_cleanup(env["config"], env["paths"], None, False,
                         length_only=True)
    assert res["length"] == {"already_asked": 2}
    assert len(_length_asks(env["paths"])) == 2


def test_live_queue_is_paced_by_batch(env):
    env["config"]["litigation_length_audit_batch"] = 1
    res = lu.run_cleanup(env["config"], env["paths"], None, False,
                         length_only=True)
    assert res["length"] == {"queued": 1, "deferred": 1}
    # The deferred cell wasn't sent to Claude at all.
    assert sum("Condense it." in p for p in FakeAnthropic.prompts) == 1
    # Next run: the first ask is still live, so nothing more is queued.
    res = lu.run_cleanup(env["config"], env["paths"], None, False,
                         length_only=True)
    assert res["length"] == {"already_asked": 1, "deferred": 1}


def test_rewrite_that_drops_facts_is_not_queued(env):
    FakeAnthropic.rewrites = {**REWRITES,
                              "Jane Roe (The Apollo)": "BMC denies liability."}
    res = lu.run_cleanup(env["config"], env["paths"], None, False,
                         length_only=True)
    assert res["length"] == {"queued": 1, "rewrite_failed": 1}
    assert [a["row_id"] for a in _length_asks(env["paths"])] == [12]
    # Retried once with the failure fed back.
    roe_prompts = [p for p in FakeAnthropic.prompts if "Jane Roe" in p]
    assert len(roe_prompts) == 2 and "rejected" in roe_prompts[1]


def test_full_cleanup_drops_compliance_fix_on_length_flagged_cell(env,
                                                                monkeypatch):
    def create(self, model, max_tokens, messages):
        prompt = messages[0]["content"]
        if "Review these entries" in prompt:
            return _Resp(json.dumps({"fixes": [
                {"row_id": 11, "claim": "Jane Roe (The Apollo)",
                 "column": "Summary of Claim", "current": "x",
                 "proposed": "reworded", "reason": "voice"},
                {"row_id": 13, "claim": "Lee Doe (Meriel)",
                 "column": "Type of Case", "current": "Bodily Injury",
                 "proposed": "Slip & Fall", "reason": "vocab"}],
                "missing": []}))
        return FakeAnthropic.create(self, model, max_tokens, messages)
    import anthropic
    Fake2 = type("Fake2", (FakeAnthropic,), {"create": create})
    monkeypatch.setattr(anthropic, "Anthropic", Fake2)
    res = lu.run_cleanup(env["config"], env["paths"], None, False)
    assert res["fixes_queued"] == 1
    cols = [(a["row_id"], a["column"], a.get("check"))
            for a in lu.load_state(env["paths"])["asks"]]
    assert (13, "Type of Case", None) in cols
    assert (11, "Summary of Claim", None) not in cols


def test_approved_length_fix_writes_only_if_cell_unchanged(env):
    lu.run_cleanup(env["config"], env["paths"], None, False, length_only=True)
    state = lu.load_state(env["paths"])
    roe = next(a for a in state["asks"] if a["row_id"] == 11)
    ack = lu.execute_ask(None, SS_TOKEN, env["config"], env["paths"], state,
                         roe, dry_run=False)
    assert env["writes"] == [(11, {"Summary of Claim": CONDENSED_SUMMARY})]
    assert "Fixed" in ack
    # The cell is edited before the next approval: nothing is written.
    poe = next(a for a in state["asks"] if a["row_id"] == 12)
    poe["current"] = "something else"
    ack = lu.execute_ask(None, SS_TOKEN, env["config"], env["paths"], state,
                         poe, dry_run=False)
    assert len(env["writes"]) == 1 and "changed since" in ack


def test_missing_voice_files_stop_the_audit(env):
    (env["paths"]["voices"] / "voice_summary.md").unlink()
    res = lu.run_cleanup(env["config"], env["paths"], None, False,
                         length_only=True)
    assert res == {"error": "no_voices"}


def test_secrets_never_logged_or_reported(env, caplog, capsys):
    caplog.set_level("DEBUG")
    res = lu.run_cleanup(env["config"], env["paths"], None, False,
                         length_only=True)
    report = Path(res["report"])
    blobs = [caplog.text, capsys.readouterr().out,
             report.read_text(encoding="utf-8"),
             report.with_suffix(".csv").read_text(encoding="utf-8-sig"),
             env["paths"]["activity"].read_text(encoding="utf-8"),
             env["paths"]["state"].read_text(encoding="utf-8"),
             *FakeAnthropic.prompts]
    for blob in blobs:
        assert SS_TOKEN not in blob and API_KEY not in blob
