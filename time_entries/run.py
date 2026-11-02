"""The phase 2 pipeline: correlate, build day sheets, draft, verify, flatten.

Shared by `--time-entries --draft` (a timecard by hand) and the drafting
backtest (evaluate.py). Phase 3 wraps the same steps in the mail trigger.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from . import correlate, daysheet, db, draft, learn, mail, names, reference

log = logging.getLogger("rocky")


def make_client(config: dict):
    from anthropic import Anthropic
    return Anthropic(api_key=config["anthropic_api_key"], max_retries=4)


def read_text(path) -> str:
    try:
        return path.read_text(encoding="utf-8") if path and path.exists() else ""
    except OSError as e:
        log.warning(f"[time-entries] {path} unreadable ({e})")
        return ""


def name_map(cx, props, settings, before: str | None, fallback_json=None) -> dict:
    if cx is not None and db._meta_get(cx, "migrated_from_corpus"):
        idx, _units = names.build_index(db.narrative_rows(cx, before=before))
    elif fallback_json:
        idx = fallback_json
    else:
        idx = {}
    return names.scoring_names(idx, props, correlate.COMMON, settings)


def draft_entries(config: dict, paths: dict, entries: list[dict], work: list[dict],
                  mail_kind: str, cx, before: str | None = None, workers: int = 3,
                  verify: bool = True, client=None, progress=None) -> dict:
    """Draft every entry in `work` (a subset of the export's `entries`).

    before: a backtest cutoff. Names and prior narratives on or after it are
    left out, so the drafter can't read the answers it is scored against."""
    say = progress or (lambda s: None)
    settings = reference.load_settings(paths["settings"])
    props, notes = reference.load_reference(paths["reference"], paths["overlay"])
    m2n = name_map(cx, props, settings, before)
    style = read_text(paths["style_guide"])
    lessons = read_text(paths["lessons"])
    model = config.get("time_entries_model") or draft.DEFAULT_MODEL
    effort = config.get("time_entries_effort") or "high"

    src = mail.open_mail(mail_kind, paths)
    lock = threading.Lock()
    cache: dict = {}

    def thread_fn(conv_id):
        if not conv_id:
            return []
        with lock:
            if conv_id not in cache:
                cache[conv_id] = src.thread(conv_id)
            return cache[conv_id]

    try:
        lo, hi = correlate.mail_window(work, settings)
        emails = src.sent_between(lo, hi)
        say(f"{len(emails)} sent emails loaded from {mail_kind} ({lo} to {hi})")
        results = correlate.correlate(work, emails, props, notes, m2n, settings)
        by_row = {r["row"]: r for r in results}
        mail_by_date: dict[str, list] = {}
        for e in emails:
            mail_by_date.setdefault(e["date"], []).append(e)
        prior_rows = db.narrative_triples(cx) if cx is not None else []
        work_rows = {e.get("row") for e in work}
        sheets = []
        for dates in daysheet.group_days(e["work_date"] for e in work):
            day_entries = [e for e in entries if e["work_date"] in set(dates)]
            matters = {e["matter"] for e in day_entries if e.get("row") in work_rows}
            prior = daysheet.prior_narratives(prior_rows, matters, dates,
                                              entries=[e for e in entries
                                                       if e.get("row") not in work_rows],
                                              before=before)
            sheets.append(daysheet.DaySheet(dates, day_entries, work_rows, mail_by_date,
                                            by_row, props, notes, prior, settings,
                                            thread_fn=thread_fn, all_mail=emails))
        say(f"{len(sheets)} day sheets, {len(work)} entries to draft"
            + (", verification on" if verify else ", verification off"))
        client = client or make_client(config)

        def one(sheet):
            res = draft.draft_sheet(client, sheet, style, lessons, model=model,
                                    effort=effort, verify=verify)
            n = len(res["rows"])
            conf = {c: sum(1 for r in res["rows"].values() if r["confidence"] == c)
                    for c in draft.CONFIDENCE}
            say(f"  {', '.join(sheet.dates)}: {n} drafted "
                f"(High {conf['High']}, Medium {conf['Medium']}, REVIEW {conf['REVIEW']}), "
                f"{len(res['uncaptured'])} uncaptured, {len(res['fixes'])} verifier fixes, "
                f"${res['usage']['cost_usd']}"
                + (f"; ERRORS: {'; '.join(res['errors'])}" if res["errors"] else ""))
            return res

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            outs = list(pool.map(one, sheets))
    finally:
        src.close()

    usage = draft.Usage()
    for o in outs:
        u = o["usage"]
        usage.calls += u["calls"]
        usage.input += u["input"]
        usage.output += u["output"]
        usage.cache_read += u["cache_read"]
        usage.cache_write += u["cache_write"]
    recs = flatten(sheets, outs, learn.ordinals(entries))
    return {"sheets": sheets, "outs": outs, "recs": recs, "emails": emails,
            "results": results, "usage": usage.as_dict(), "model": model,
            "effort": effort, "mail_window": (lo, hi)}


def flatten(sheets, outs, ords: dict) -> list[dict]:
    """Day-sheet results -> recommendation rows (db.store_recommendations,
    workbook.build, learn.diff all take these)."""
    recs = []
    for sheet, out in zip(sheets, outs):
        def ids(refs):
            return [sheet.email(m)["id"] for m in refs or [] if sheet.email(m)]
        for ref, e in sheet.entry_refs.items():
            r = out["rows"].get(ref) or {}
            first = dict(r.get("first") or {})
            first["basis_ids"] = ids(first.get("basis_refs"))
            recs.append({
                "kind": "draft", "work_date": e["work_date"], "matter": e["matter"],
                "matter_name": e.get("matter_name"), "client": e.get("client"),
                "hours": e.get("hours"), "ordinal": ords.get(e.get("row"), 0),
                "entry_row": e.get("row"), "start": (e.get("narrative") or "").strip(),
                "narrative": r.get("narrative") or "", "confidence": r.get("confidence"),
                "basis": r.get("basis") or "", "basis_ids": ids(r.get("basis_refs")),
                "note": r.get("note") or "", "first": first,
                "problem": r.get("problem") or "",
            })
        for u in out["uncaptured"]:
            recs.append({
                "kind": "uncaptured", "work_date": u.get("date"),
                "matter": u.get("matter") or "(unknown)",
                "matter_name": u.get("matter_name"), "hours": u.get("hours"),
                "narrative": u.get("narrative") or "", "confidence": None,
                "basis": u.get("basis") or "", "basis_ids": ids(u.get("basis_refs")),
                "note": u.get("note") or "",
            })
    return recs


def readme(title: str, period: str, run: dict, mail_kind: str) -> dict:
    recs = run["recs"]
    drafts = [r for r in recs if r["kind"] == "draft"]
    conf = {c: [r for r in drafts if r["confidence"] == c] for c in draft.CONFIDENCE}
    fixes = sum(len(o["fixes"]) for o in run["outs"])
    errors = [f"{', '.join(o['dates'])}: {e}" for o in run["outs"] for e in o["errors"]]
    unc = [r for r in recs if r["kind"] == "uncaptured"]
    lo, hi = run["mail_window"]
    decide = [f"{r['work_date']} {r['matter']} {r['hours']}h: {r['note']}"
              for r in conf["REVIEW"] if r.get("note")][:25]
    return {
        "title": title, "period": period,
        "emails": f"{len(run['emails'])} of James's sends, {lo} to {hi}, from {mail_kind}",
        "run_date": __import__("datetime").date.today().isoformat(),
        "results": (f"{len(drafts)} entries: High {len(conf['High'])}, Medium "
                    f"{len(conf['Medium'])}, REVIEW {len(conf['REVIEW'])}. "
                    f"{len(unc)} uncaptured threads, "
                    f"{round(sum(u.get('hours') or 0 for u in unc), 1)} suggested hours. "
                    f"Model {run['model']}, effort {run['effort']}, "
                    f"{run['usage']['calls']} calls, about ${run['usage']['cost_usd']}."),
        "verification": (f"A separate Rule 0 pass checked every row and changed {fixes}."
                         + (" Problems: " + "; ".join(errors) if errors else "")),
        "decide": decide,
    }
