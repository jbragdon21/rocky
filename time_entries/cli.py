"""rocky.py --time-entries — phase 1 commands (no Claude calls).

    --time-entries --migrate [--force]
        Copy the folder's corpus.db history (entries, narratives, anomalies)
        into te.db. Run once per machine; --force replaces the migrated rows.

    --time-entries --correlate --csv <timecard> [--mail minotaur|corpus]
                   [--max-narr N] [--out <file.json>]
        Rank candidate emails for every blank or partial entry. Writes JSON.

    --time-entries --backtest --csv <timecard> --hand <workbook.xlsx | _drafts.py>
                   [--mail minotaur|corpus] [--label NAME]
        Replay a finished hand run and score the correlator against the
        emails its Basis column cites. Prints the report; full detail goes
        to <data>\\time_entries_data\\backtests\\<label>.json.

Common: --time-entries-root <folder>, --brain-db <path>. Relative --csv /
--hand paths resolve against the Time Entries folder.
"""

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path

from . import backtest, correlate, db, mail, names, reference, timecards
from .paths import get_paths

log = logging.getLogger("rocky")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rocky --time-entries", add_help=False)
    p.add_argument("--time-entries", action="store_true")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--migrate", action="store_true")
    mode.add_argument("--correlate", action="store_true")
    mode.add_argument("--backtest", action="store_true")
    p.add_argument("--csv")
    p.add_argument("--hand")
    p.add_argument("--mail", choices=("minotaur", "corpus"), default="minotaur")
    p.add_argument("--max-narr", type=int)
    p.add_argument("--out")
    p.add_argument("--label")
    p.add_argument("--force", action="store_true")
    p.add_argument("--names", choices=("db", "json"), default="db",
                   help="name index source: te.db narratives (default) or "
                        "the folder's port/name_index.json")
    p.add_argument("--time-entries-root")
    p.add_argument("--brain-db")
    return p


def _resolve(root: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else root / p


def _name_index(paths: dict, cx, source: str, before: str | None) -> dict:
    if source == "db" and cx is not None and db._meta_get(cx, "migrated_from_corpus"):
        idx, _units = names.build_index(db.narrative_rows(cx, before=before))
        log.info(f"[time-entries] name index: {len(idx)} names from te.db"
                 + (f" (narratives before {before})" if before else ""))
        return idx
    p = paths["name_index_json"]
    if p.exists():
        log.warning(f"[time-entries] name index from {p} — run --migrate so "
                    f"Rocky builds its own")
        return json.loads(p.read_text(encoding="utf-8"))
    log.warning("[time-entries] no name index (Rule 3 off) — run --migrate")
    return {}


def run_cli(config: dict, data_dir: Path, argv: list[str] | None = None) -> int:
    import sys
    args_in = sys.argv[1:] if argv is None else argv
    a, _unknown = _parser().parse_known_args(args_in)
    paths = get_paths(config, data_dir, args_in)

    if a.migrate:
        cx = db.connect(paths["db"])
        res = db.migrate_from_corpus(cx, paths["corpus"], force=a.force)
        if res.get("skipped"):
            print(f"Already migrated {res['migrated_at']} — pass --force to redo.")
        else:
            print(f"Migrated from {paths['corpus']} into {paths['db']}: "
                  f"{res['entries']} entries, {res['narratives']} narratives, "
                  f"{res['anomalies']} anomalies.")
        cx.close()
        return 0

    if not (a.correlate or a.backtest):
        print(__doc__)
        return 2
    if not a.csv:
        print("--csv <timecard> is required")
        return 2

    settings = reference.load_settings(paths["settings"])
    props, notes = reference.load_reference(paths["reference"], paths["overlay"])
    tc = timecards.load_timecard(_resolve(paths["root"], a.csv),
                                 timekeeper_id=timecards.JAMES_TIMEKEEPER_ID)
    cx = db.connect(paths["db"]) if paths["db"].exists() else None

    if a.correlate:
        max_narr = a.max_narr or settings["partial_max_chars"]
        work = [e for e in tc["entries"] if correlate.needs_work(e, max_narr)]
        if not work:
            print("No blank or partial entries in that timecard.")
            return 0
        idx = _name_index(paths, cx, a.names, None)
        m2n = names.scoring_names(idx, props, correlate.COMMON, settings)
        src = mail.open_mail(a.mail, paths)
        try:
            emails = src.sent_between(*correlate.mail_window(work, settings))
        finally:
            src.close()
        results = correlate.correlate(work, emails, props, notes, m2n, settings)
        out = Path(a.out) if a.out else paths["local"] / "candidates.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
        with_c = sum(1 for r in results if r["cands"])
        gaps = sum(1 for r in results if r["zero_hit_token"])
        print(f"{len(results)} entries need a narrative; {with_c} have candidates, "
              f"{len(results) - with_c} have none, {gaps} zero-hit tokens. -> {out}")
        if cx is not None:
            db.record_run(cx, "correlate", {"csv": a.csv, "mail": a.mail,
                                            "max_narr": max_narr},
                          {"entries": len(results), "with_cands": with_c,
                           "zero_hit": gaps})
        return 0

    # --backtest
    if not a.hand:
        print("--hand <hand-run workbook or _drafts.py> is required")
        return 2
    hand = backtest.load_hand(_resolve(paths["root"], a.hand))
    if not hand:
        print("The hand-run file has no draft rows.")
        return 2
    period_from = min(h["date"] for h in hand)
    idx = _name_index(paths, cx, a.names, period_from)
    m2n = names.scoring_names(idx, props, correlate.COMMON, settings)
    pseudo = [{"work_date": h["date"]} for h in hand]
    src = mail.open_mail(a.mail, paths)
    try:
        emails = src.sent_between(*correlate.mail_window(pseudo, settings))
    finally:
        src.close()
    result = backtest.run_backtest(hand, tc["entries"], emails, props, notes,
                                   m2n, settings)
    label = a.label or Path(a.hand).stem
    k = settings["max_candidates"]
    report = backtest.format_report(label, result, a.mail, len(emails), k)
    print(report)
    out_dir = paths["backtests"]
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = out_dir / f"{label} - {a.mail} - {stamp}.json"
    out.write_text(json.dumps({"label": label, "mail": a.mail, "csv": a.csv,
                               "hand": a.hand, "settings": settings, **result},
                              indent=1, default=str), encoding="utf-8")
    print(f"\nDetail: {out}")
    if cx is not None:
        db.record_run(cx, "backtest", {"csv": a.csv, "hand": a.hand,
                                       "mail": a.mail}, result["summary"])
    return 0
