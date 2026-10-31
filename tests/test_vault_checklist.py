"""Vault Checklist: case types, jurisdictions, matching, the per-type
document rules, sheet-write planning, and the archive. No network.

    python -m pytest tests/test_vault_checklist.py -q
"""

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import vault  # noqa: E402
import vault_checklist as vc  # noqa: E402

TODAY = date(2026, 10, 31)
CFG: dict = {}
NO_RULES = {"jurisdictions": {}, "properties": {}, "case_types": {}}


def _row(name="Smith, John", prop="The 202", client="The 202", status="Rent",
         ripe=None, next_steps="", unit="101", row=3):
    return {"row": row, "client": client, "name": name, "property": prop,
            "unit": unit, "status": status, "ripe": ripe,
            "next_steps": next_steps, "current": {}}


def _folder(rel, docs=(), archived=False, newest=None):
    parts = rel.split("\\")
    return vc.Folder(rel=rel, prop=parts[-2], tenant=parts[-1],
                     archived=archived, docs=list(docs), newest=newest)


# ----------------------------------------------------------------------------
# Case type
# ----------------------------------------------------------------------------

def test_case_types():
    c = vc.classify_case_type
    assert c("Rent") == "rent"
    assert c("Rent - JUDGMENT AWARDED") == "rent"
    assert c("Term letter sent (Rent)") == "rent"
    assert c("Non-Rent") == "nonrent"
    assert c("Water bill sent") == "nonrent"
    assert c("Non-Renewal sent") == "nonrenewal"
    assert c("Non-renew sent") == "nonrenewal"
    assert c("NTCV sent (smoking)") == "breach"
    assert c("Lease term sent (recert)") == "breach"
    assert c("Renter's Insurance") == "breach"
    assert c("Small Claims - Tenant Filed") is None
    assert c("Term letter sent") is None      # no type: never guessed
    assert c("Parking revocation") is None
    assert c("") is None


def test_case_type_teaching_file_wins():
    rules = {"parking revocation": "breach", "rent": "skip"}
    assert vc.classify_case_type("Parking revocation drafted", rules) == "breach"
    assert vc.classify_case_type("Rent", rules) is None


# ----------------------------------------------------------------------------
# Jurisdiction
# ----------------------------------------------------------------------------

TABLE = [("The 202", "DC"), ("Alvista Bowie", "MD"), ("The Milton", "VA")]


def test_jurisdiction_order():
    j = vc.resolve_jurisdiction
    assert j(_row(client="Bozzuto MD", prop="Somewhere"), [], NO_RULES, {})[0] == "MD"
    assert j(_row(client="Hanover on 8th (DC)", prop="Hanover 8th Street"),
             [], NO_RULES, {})[0] == "DC"
    assert j(_row(prop="202"), TABLE, NO_RULES, {})[0] == "DC"
    assert j(_row(prop="Nowhere", client="X",
                  next_steps="Trial (D-01-CV-26-034381)"), [], NO_RULES, {})[0] == "MD"
    assert j(_row(prop="Nowhere", client="X",
                  next_steps="WRIT ISSUED (GV26020585-00)"), [], NO_RULES, {})[0] == "VA"
    assert j(_row(prop="Nowhere", client="X",
                  next_steps="Initial Hearing: 9/18/26 (26-7159)"), [], NO_RULES, {})[0] == "DC"
    rules = {**NO_RULES, "jurisdictions": {"Nowhere": "va"}}
    assert j(_row(prop="Nowhere", client="Bozzuto MD"), [], rules, {})[0] == "VA"
    hist = {"property": {vc._norm("Arbor Oaks"): {"MD"}}}
    assert j(_row(prop="Arbor Oaks", client="Towner"), [], NO_RULES, {},
             hist) == ("MD", "property case history")
    assert j(_row(prop="Nowhere", client="X"), [], NO_RULES, {})[0] is None


def test_client_consensus_uses_current_tab_only():
    res = [{"client": "Towner", "jurisdiction": "MD", "jurisdiction_how": "x"},
           {"client": "Towner", "jurisdiction": None, "jurisdiction_how": ""},
           {"client": "Horning", "jurisdiction": "DC", "jurisdiction_how": "x"},
           {"client": "Horning", "jurisdiction": "MD", "jurisdiction_how": "x"},
           {"client": "Horning", "jurisdiction": None, "jurisdiction_how": ""}]
    vc.client_consensus(res)
    assert res[1]["jurisdiction"] == "MD"
    assert res[4]["jurisdiction"] is None     # mixed client: no guess


# ----------------------------------------------------------------------------
# Sheet parsing
# ----------------------------------------------------------------------------

def test_parse_sheet_with_banner_row():
    grid = [
        [None] * 12 + ["The Vault", None, None, None],
        [None, "Subsidized", None, None, "Name", "Property ", "Unit",
         "Client.Matter", "Status", "Ripe Date", "Next Steps", None,
         "Lease?", "Final Notice?", "Affidavit of Service?", "Ledger? "],
        ["The 202", None, None, None, "Akinsola, O", 202, 1020, None, "Rent",
         "11.11.26", "Ripe on 11.11.26", None, "Yes", None, None, None],
        [None, None, None, None, "Barney, J", 202, 809, None, "Rent",
         46000, "", None, None, None, None, None],
    ]
    p = vc.parse_sheet(grid)
    assert p["header_row"] == 2
    assert p["columns"] == {"lease": 12, "notice": 13, "affidavit": 14,
                            "ledger": 15}
    a, b = p["rows"]
    assert (a["row"], a["client"], a["property"], a["unit"]) == (3, "The 202", "202", "1020")
    assert a["ripe"] == date(2026, 11, 11)
    assert a["current"]["lease"] == "Yes"
    assert b["client"] == "The 202" and b["row"] == 4
    assert b["ripe"] == date(1899, 12, 30) + timedelta(days=46000)


# ----------------------------------------------------------------------------
# Matching
# ----------------------------------------------------------------------------

def _by_prop(folders):
    out = {}
    for f in folders:
        out.setdefault(f.prop, []).append(f)
    return out


def test_match_property_and_person_variants():
    fs = [_folder("The 202\\Britt, Wanda"), _folder("The 202\\Holland, D"),
          _folder("The 202\\Holland, Deleona"), _folder("Other\\Britt, Wanda")]
    hits, how = vc.match_row(_row(name="Britt, Wanda, et al. ", prop="202"),
                             _by_prop(fs), NO_RULES, {})
    assert [f.rel for f in hits] == ["The 202\\Britt, Wanda"]
    hits, _ = vc.match_row(_row(name="Holland, Deleona", prop="202"),
                           _by_prop(fs), NO_RULES, {})
    assert {f.rel for f in hits} == {"The 202\\Holland, D",
                                     "The 202\\Holland, Deleona"}


def test_match_ambiguous_people_returns_none():
    fs = [_folder("The 202\\Smith, John"), _folder("The 202\\Smith, Jane")]
    hits, how = vc.match_row(_row(name="Smith, J", prop="202"),
                             _by_prop(fs), NO_RULES, {})
    assert hits == [] and how == "ambiguous"


def test_match_widens_to_similar_then_shared_word():
    fs = [_folder("Cloisters I\\Other, Person"),
          _folder("The Cloisters\\Lanier, Latonia"),
          _folder("Solstice - 3534 E Capitol\\Brent, Brittany")]
    hits, how = vc.match_row(_row(name="Lanier, Latonia", prop="Cloisters I"),
                             _by_prop(fs), NO_RULES, {})
    assert how == "similar property + name" and hits[0].prop == "The Cloisters"
    hits, how = vc.match_row(_row(name="Brent, Brittany, et. al.",
                                  prop="Solstice I", client="MRP Realty"),
                             _by_prop(fs), NO_RULES, {})
    assert how == "shared property word + name"


def test_no_vault_wide_name_search():
    fs = [_folder("Solstice - 3534 E Capitol\\Smith, Michael")]
    hits, how = vc.match_row(_row(name="Smith, Michael et al",
                                  prop="101 Cross Street", client="Bozzuto MD"),
                             _by_prop(fs), NO_RULES, {})
    assert hits == [] and how == "no property folder"


def test_signature_guard():
    assert vc.names_match("Solaire 8200", "Solaire 8250") is None
    assert vc.names_match("Residences at the Avenue",
                          "Residences on the Avenue") == "strong"
    assert vc.names_match("202", "The 202") == "strong"


# ----------------------------------------------------------------------------
# The rules
# ----------------------------------------------------------------------------

def _doc(kind, when=None, rad=False):
    return vc.Doc(kind=kind, when=when, rad=rad, rel=f"x\\{kind}")


def test_md_breach_needs_lease_and_notice_only():
    f = _folder("P\\Smith, John", [_doc("lease"), _doc("notice", TODAY)])
    marks, _ = vc.evaluate_row(_row(), "MD", "breach", [f], [], TODAY, CFG)
    assert marks == {"lease": "Yes", "notice": "Yes", "affidavit": "N/a",
                     "ledger": "N/a"}


def test_va_rent_needs_current_ledger():
    old = _doc("ledger", TODAY - timedelta(days=45))
    f = _folder("P\\Smith, John", [_doc("lease"), _doc("notice", TODAY), old])
    marks, why = vc.evaluate_row(_row(), "VA", "rent", [f], [], TODAY, CFG)
    assert marks == {"lease": "Yes", "notice": "Yes", "affidavit": "N/a",
                     "ledger": "No"}
    assert "not current" in why["ledger"]


def test_dc_nonrent_needs_rad_notice():
    f = _folder("P\\Smith, John", [_doc("notice", TODAY, rad=False),
                                   _doc("affidavit", TODAY)])
    marks, why = vc.evaluate_row(_row(status="Non-Rent"), "DC", "nonrent",
                                 [f], [], TODAY, CFG)
    assert marks["notice"] == "No" and "RAD" in why["notice"]
    assert marks["affidavit"] == "Yes" and marks["lease"] == "No"
    f.docs[0].rad = True
    marks, _ = vc.evaluate_row(_row(), "DC", "nonrent", [f], [], TODAY, CFG)
    assert marks["notice"] == "Yes"


def test_dc_rent_takes_any_notice_and_issued_folder():
    marks, _ = vc.evaluate_row(_row(), "DC", "rent", [], [_doc("notice", TODAY)],
                               TODAY, CFG)
    assert marks == {"lease": "No", "notice": "Yes", "affidavit": "No",
                     "ledger": "No"}


def test_dc_breach():
    marks, _ = vc.evaluate_row(_row(), "DC", "breach", [], [], TODAY, CFG)
    assert marks == {"lease": "No", "notice": "No", "affidavit": "No",
                     "ledger": "N/a"}


def test_unknown_type_or_state_leaves_cells_blank():
    assert set(vc.evaluate_row(_row(), None, "rent", [], [], TODAY, CFG)[0]
               .values()) == {""}
    assert set(vc.evaluate_row(_row(), "DC", None, [], [], TODAY, CFG)[0]
               .values()) == {""}


def test_old_notice_from_a_prior_matter_does_not_count():
    ripe = date(2026, 11, 11)
    f = _folder("P\\Smith, John", [_doc("notice", date(2025, 3, 1))])
    marks, _ = vc.evaluate_row(_row(ripe=ripe), "MD", "breach", [f], [],
                               TODAY, CFG)
    assert marks["notice"] == "No"
    f.docs.append(_doc("notice", date(2026, 10, 10)))
    marks, _ = vc.evaluate_row(_row(ripe=ripe), "MD", "breach", [f], [],
                               TODAY, CFG)
    assert marks["notice"] == "Yes"


def test_issued_rent_notices_match_on_name_and_unit():
    issued = [{"rel": "a", "when": date(2026, 7, 17), "tokens": set(
        "5333 connecticut adolfo nunez 402 ntpr july".split())}]
    got = vc.issued_notices_for(_row(name="Nunez, Adolfo", prop="5333",
                                     unit="402"), issued)
    assert len(got) == 1 and got[0].when == date(2026, 7, 17)
    assert vc.issued_notices_for(_row(name="Nunez, Adolfo", prop="Illume",
                                      unit="402"), issued) == []


def test_kind_from_filename():
    k = vc.kind_from_filename
    assert k("Lease - Smith, John - 2026-05-01.pdf") == "lease"
    assert k("Affidavit of Service - Smith, John - undated.pdf") == "affidavit"
    assert k("Lease Violation Notice.pdf") == "notice"
    assert k("RAD filing packet - Smith - 2026-01-01.pdf") == "notice"
    assert k("303 Whitehead Lease Exp. 8.31.2027.pdf") == "lease"


# ----------------------------------------------------------------------------
# Writes
# ----------------------------------------------------------------------------

def test_plan_writes_only_changed_cells_in_runs():
    rows = [{"row": r, "current": {"lease": cur}} for r, cur in
            ((3, ""), (4, "No"), (5, ""), (6, "Yes"), (9, ""))]
    desired = {3: {"lease": "Yes"}, 4: {"lease": "No"}, 5: {"lease": "No"},
               6: {"lease": "No"}, 9: {"lease": ""}}
    ops = vc.plan_writes(rows, desired, {"lease": 12})
    assert ("M3:M3", "values", {"values": [["Yes"]]}) in ops
    assert ("M5:M6", "values", {"values": [["No"], ["No"]]}) in ops
    assert ("M5:M6", "fill", {"color": vc.FILLS["No"]}) in ops
    assert not any(a.startswith("M4") or a.startswith("M9") for a, _, _ in ops)
    repaint = vc.plan_writes(rows, desired, {"lease": 12}, repaint=True)
    assert ("M3:M6", "values", {"values": [["Yes"], ["No"], ["No"], ["No"]]}) in repaint
    assert ("M4:M6", "fill", {"color": vc.FILLS["No"]}) in repaint


# ----------------------------------------------------------------------------
# Archive
# ----------------------------------------------------------------------------

def test_plan_archive_grace_idle_and_restore():
    now = datetime(2026, 10, 31, tzinfo=timezone.utc)
    live_on = _folder("P\\On, Sheet", newest=now)
    off_long = _folder("P\\Off, Long", newest=now)
    off_short = _folder("P\\Off, Short", newest=now)
    never_idle = _folder("P\\Never, Idle", newest=now - timedelta(days=200))
    never_fresh = _folder("P\\Never, Fresh", newest=now - timedelta(days=10))
    back = _folder("_Archive\\P\\Back, Again", archived=True)
    stays = _folder("_Archive\\P\\Stays, Put", archived=True)
    state = {"folders": {"P\\Off, Long": {"last_matched": "2026-10-01"},
                         "P\\Off, Short": {"last_matched": "2026-10-25"}}}
    plan = vc.plan_archive(
        [live_on, off_long, off_short, never_idle, never_fresh, back, stays],
        {"P\\On, Sheet", "_Archive\\P\\Back, Again"}, state, TODAY, CFG)
    assert {f.rel for f, _ in plan["archive"]} == {"P\\Off, Long",
                                                   "P\\Never, Idle"}
    assert [f.rel for f in plan["restore"]] == ["_Archive\\P\\Back, Again"]
    assert [(f.rel, d) for f, d in plan["pending"]] == [("P\\Off, Short", 8)]


def test_move_folder_and_catalog_replay(tmp_path):
    root = tmp_path
    meta = root / "_vault"
    meta.mkdir()
    (root / "P" / "Smith, John").mkdir(parents=True)
    (root / "P" / "Smith, John" / "Lease - Smith, John - 2026-01-01.pdf").write_bytes(b"x")
    (meta / "catalog.jsonl").write_text(json.dumps(
        {"path": "P\\Smith, John\\Lease - Smith, John - 2026-01-01.pdf",
         "disposition": "filed"}) + "\n", encoding="utf-8")
    paths = {"meta": meta, "catalog": meta / "catalog.jsonl", "root": root}

    n = vc.move_folder(root, meta, "P\\Smith, John", "_Archive\\P\\Smith, John",
                       "test")
    assert n == 1 and not (root / "P").exists()
    entry = vault.load_catalog(paths)[0]
    assert entry["path"].startswith("_Archive\\P\\Smith, John\\")
    assert (root / entry["path"]).exists()

    # A new document arrives for the same tenant while archived, then the
    # row comes back: the restore merges file by file.
    (root / "P" / "Smith, John").mkdir(parents=True)
    (root / "P" / "Smith, John" / "Lease - Smith, John - 2026-01-01.pdf").write_bytes(b"new")
    vc.move_folder(root, meta, "_Archive\\P\\Smith, John", "P\\Smith, John",
                   "back")
    entry = vault.load_catalog(paths)[0]
    assert (root / entry["path"]).read_bytes() == b"x"
    assert entry["path"].startswith("P\\Smith, John\\")
    assert len(list((root / "P" / "Smith, John").iterdir())) == 2


def test_roman_numeral_buildings_never_match():
    fs = [_folder("Weinberg Village IV\\Doe, Jane")]
    hits, how = vc.match_row(_row(name="Doe, Jane", prop="Weinberg Villages V",
                                  client="Franklin Group"),
                             _by_prop(fs), NO_RULES, {})
    assert hits == []
    assert not vc.signatures_conflict(vc._norm("Solstice I"),
                                      vc._norm("Solstice - 3534 E Capitol"))


class _FakeWorkbook:
    ws = "/ws"

    def __init__(self, names):
        self.names, self.ops = names, None

    def read(self, address):
        return [[n] for n in self.names]

    def batch(self, ops):
        self.ops = ops
        return 0


def test_write_sheet_refuses_when_rows_moved():
    parsed = {"rows": [{"row": 2, "name": "A, B", "current": {}},
                       {"row": 3, "name": "C, D", "current": {}}],
              "columns": {"lease": 12}}
    desired = {2: {"lease": "Yes"}, 3: {"lease": "No"}}
    moved = _FakeWorkbook(["Name", "NEW, ROW", "A, B"])
    out = vc.write_sheet(moved, parsed, desired, 0, repaint=False)
    assert out["written"] == 0 and "changed during the run" in out["skipped"]
    assert moved.ops is None
    ok = _FakeWorkbook(["Name", "A, B", "C, D"])
    out = vc.write_sheet(ok, parsed, desired, 0, repaint=False)
    assert out["written"] == 2
    paths = [p for _, p, _ in ok.ops]
    assert "/ws/range(address='M2:M3')" in paths
    assert "/ws/range(address='M2:M2')/format/fill" in paths
