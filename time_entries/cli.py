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

Phase 2 (Claude calls, Opus 5.5 by default; time_entries_model in config):

    --time-entries --draft --csv <timecard> [--from D] [--to D]
                   [--mail minotaur|corpus] [--no-verify] [--workers N] [--out <file.xlsx>]
        Draft and verify every blank or partial entry. Writes the house-format
        workbook (default <data>\\time_entries_data\\drafts\\) and stores the
        recommendations in te.db. Sends nothing.

    --time-entries --draft-backtest --csv <earlier export> --final <later export>
                   [--hand <hand-run workbook | _drafts.py>] [--from D] [--to D]
                   [--no-judge] [--label NAME]
        Draft the earlier export's blanks as if it had just arrived (names and
        narratives from before the period only), then score the drafts against
        what James submitted in the later export, next to the hand run's.

    --time-entries --seed-links --hand <hand run> --final <later export> [--dry-run]
        Turn a submitted hand run into confirmed email-to-matter links. Writes
        te.db only with --mail minotaur (links key on internet_message_id).

Common: --time-entries-root <folder>, --brain-db <path>. Relative --csv /
--hand / --final paths resolve against the Time Entries folder.
"""

import argparse
import collections
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from . import (backtest, correlate, db, draft, evaluate, learn, mail, names, reference,
               run, timecards, workbook)
from .paths import get_paths

log = logging.getLogger("rocky")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rocky --time-entries", add_help=False)
    p.add_argument("--time-entries", action="store_true")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--migrate", action="store_true")
    mode.add_argument("--correlate", action="store_true")
    mode.add_argument("--backtest", action="store_true")
    mode.add_argument("--draft", action="store_true")
    mode.add_argument("--draft-backtest", action="store_true")
    mode.add_argument("--seed-links", action="store_true")
    p.add_argument("--csv")
    p.add_argument("--hand")
    p.add_argument("--final")
    p.add_argument("--from", dest="date_from")
    p.add_argument("--to", dest="date_to")
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--no-verify", action="store_true")
    p.add_argument("--no-judge", action="store_true")
    p.add_argument("--dry-run", action="store_true")
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

    if a.seed_links:
        return _seed_links(a, config, paths)
    if a.draft or a.draft_backtest:
        return _draft(a, config, paths)
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


def _work_entries(entries: list[dict], settings: dict, a) -> list[dict]:
    max_narr = a.max_narr or settings["partial_max_chars"]
    return [e for e in entries if correlate.needs_work(e, max_narr)
            and (not a.date_from or e["work_date"] >= a.date_from)
            and (not a.date_to or e["work_date"] <= a.date_to)]


def _draft(a, config: dict, paths: dict) -> int:
    """--draft and --draft-backtest."""
    if not a.csv:
        print("--csv <timecard> is required")
        return 2
    if a.draft_backtest and not a.final:
        print("--final <later export> is required")
        return 2
    settings = reference.load_settings(paths["settings"])
    tc = timecards.load_timecard(_resolve(paths["root"], a.csv),
                                 timekeeper_id=timecards.JAMES_TIMEKEEPER_ID)
    entries = tc["entries"]
    work = _work_entries(entries, settings, a)
    if not work:
        print("No blank or partial entries in that timecard (and date range).")
        return 0
    cx = db.connect(paths["db"])
    before = min(e["work_date"] for e in work) if a.draft_backtest else None
    started = datetime.now()
    res = run.draft_entries(config, paths, entries, work, a.mail, cx, before=before,
                            workers=a.workers, verify=not a.no_verify,
                            progress=lambda s: print(s, flush=True))
    lo, hi = min(e["work_date"] for e in work), max(e["work_date"] for e in work)
    period = f"{lo} to {hi}"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    params = {"csv": a.csv, "final": a.final, "hand": a.hand, "mail": a.mail,
              "from": a.date_from, "to": a.date_to, "verify": not a.no_verify,
              "model": res["model"], "effort": res["effort"]}
    run_id = db.record_run(cx, "draft-backtest" if a.draft_backtest else "draft",
                           params, {"started": started.isoformat(timespec="seconds")})
    rec_ids = db.store_recommendations(cx, run_id, res["recs"])

    title = f"{period} - draft narratives"
    rm = run.readme(f"Time entries: {title} (Rocky)", period, res, a.mail)
    if a.draft_backtest:
        label = a.label or f"{Path(a.csv).stem} drafts"
        xlsx = (Path(a.out) if a.out
                else paths["backtests"] / f"{label} - {a.mail} - {stamp}.xlsx")
    else:
        label = title
        xlsx = Path(a.out) if a.out else paths["local"] / "drafts" / f"{title}.xlsx"
    workbook.build(xlsx, res["recs"], rm)

    summary = {"period": period, "entries": len(work), "usage": res["usage"],
               "confidence": {c: sum(1 for r in res["recs"] if r["kind"] == "draft"
                                     and r["confidence"] == c)
                              for c in draft.CONFIDENCE},
               "uncaptured": sum(1 for r in res["recs"] if r["kind"] == "uncaptured"),
               "errors": [e for o in res["outs"] for e in o["errors"]],
               "workbook": str(xlsx)}
    detail = {"label": label, "params": params, "summary": summary,
              "recs": res["recs"], "sheets": res["outs"]}

    if a.draft_backtest:
        final = timecards.load_timecard(_resolve(paths["root"], a.final),
                                        timekeeper_id=timecards.JAMES_TIMEKEEPER_ID)
        hand = backtest.load_hand(_resolve(paths["root"], a.hand)) if a.hand else None
        if hand:
            hand = [h for h in hand if lo <= h["date"] <= hi]
        max_narr = a.max_narr or settings["partial_max_chars"]
        scored, outs = evaluate.score_rows(res["recs"], entries, final["entries"], hand,
                                           max_narr)
        db.store_outcomes(cx, run_id, [
            {**{k: o[k] for k in ("outcome", "similarity", "final_matter", "final_hours",
                                  "final_narrative")},
             "recommendation_id": rid, "src_file": a.final}
            for rid, o in zip(rec_ids, outs) if o])
        judge_usage = draft.Usage()
        if not a.no_judge and scored:
            print(f"Judging {len(scored)} rows ...", flush=True)
            judge_usage = evaluate.judge(run.make_client(config), scored,
                                         model=res["model"])
        s = evaluate.summarize(scored, outs)
        report = evaluate.format_report(label, s, res["usage"], judge_usage.as_dict(),
                                        scored)
        print()
        print(report)
        summary.update({"backtest": s, "judge_usage": judge_usage.as_dict()})
        detail.update({"scored": scored, "backtest": s})
    else:
        c = summary["confidence"]
        print(f"\n{len(work)} entries drafted: High {c['High']}, Medium {c['Medium']}, "
              f"REVIEW {c['REVIEW']}; {summary['uncaptured']} uncaptured threads. "
              f"About ${res['usage']['cost_usd']} in {res['usage']['calls']} calls.")
        if summary["errors"]:
            print("Problems: " + "; ".join(summary["errors"]))
    db.update_run(cx, run_id, summary)
    js = xlsx.with_suffix(".json")
    js.write_text(json.dumps(detail, indent=1, default=str), encoding="utf-8")
    print(f"\nWorkbook: {xlsx}\nDetail:   {js}")
    cx.close()
    return 0


def _seed_links(a, config: dict, paths: dict) -> int:
    """--seed-links: a submitted hand run -> confirmed email-to-matter links."""
    if not (a.hand and a.final):
        print("--hand <hand run> and --final <later export> are required")
        return 2
    write = a.mail == "minotaur" and not a.dry_run
    if a.mail != "minotaur" and not a.dry_run:
        print("Links key on brain.db's internet_message_id; with --mail corpus this "
              "is a dry run.")
    settings = reference.load_settings(paths["settings"])
    hand = backtest.load_hand(_resolve(paths["root"], a.hand))
    final = timecards.load_timecard(_resolve(paths["root"], a.final),
                                    timekeeper_id=timecards.JAMES_TIMEKEEPER_ID)
    lo = date.fromisoformat(min(h["date"] for h in hand)) - timedelta(1)
    hi = date.fromisoformat(max(h["date"] for h in hand)) + timedelta(4)
    src = mail.open_mail(a.mail, paths)
    try:
        emails = src.sent_between(lo.isoformat(), hi.isoformat())
    finally:
        src.close()
    recs = evaluate.hand_recs(hand, emails)
    outs = learn.diff(recs, [], final["entries"], settings["partial_max_chars"])
    by_id = {e["id"]: e for e in emails}
    links = []
    for r, o in zip(recs, outs):
        r["link_note"] = f"hand run {Path(a.hand).name}"
        links += learn.links_for(r, o, by_id, src_file=a.final)
    resolved = sum(1 for r in recs if r["basis_ids"])
    print(f"{len(recs)} hand-run rows; {resolved} with their Basis found in {a.mail} mail.")
    print("Outcomes: " + ", ".join(f"{k} {v}" for k, v in sorted(learn.tally(outs).items())))
    verdicts = collections.Counter(x["verdict"] for x in links)
    print(f"Links: {len(links)} ({', '.join(f'{k} {v}' for k, v in verdicts.items())})")
    if not write:
        print("Dry run: nothing written.")
        return 0
    cx = db.connect(paths["db"])
    res = collections.Counter(db.upsert_link(cx, x) for x in links)
    db.record_run(cx, "seed-links", {"hand": a.hand, "final": a.final, "mail": a.mail},
                  {"rows": len(recs), "resolved": resolved, "links": len(links),
                   "result": dict(res)})
    cx.close()
    print(f"te.db links: {dict(res)}")
    return 0
