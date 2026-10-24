"""Backtest the correlator against a finished hand run.

Each hand-run workbook row names, in its Basis column, the email(s) the
narrative was drafted from: "10/6 16:02 'RE: Marina Bay/Tenant Class
Action -- class member information' to G. Sapire ...". The question for
phase 1 is whether Rocky's correlator, reading Minotaur's mail, puts that
email among the candidates it would hand the drafter.

Per row:
  in_mail   the cited email exists in the loaded mail at all (within a week
            of the entry). A miss here is a mail-source gap, not a scoring miss.
  rank      1-based position of the first cited email among the candidates
            (None = not among them)

Pass mark (TIME_ENTRIES_DESIGN.md, Phase 1): rank <= max_candidates for at
least 90% of High rows. Known optimism: the reference workbook already
carries Matter Notes rows that the hand run added after reading this mail.
Known pessimism: the hand drafter read the whole day (daydump), so some
cited emails were never correlator candidates in the original run either.
"""

import ast
import re
from datetime import date, timedelta
from pathlib import Path

from .correlate import correlate

_PREFIXES = re.compile(
    r"^(?:(?:re|fw|fwd|aw|external|ext|accepted|declined|tentative|"
    r"canceled|cancelled)\s+)+")


def norm(s: str) -> str:
    s = (s or "").replace("\x00", " ").lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def norm_subject(s: str) -> str:
    return _PREFIXES.sub("", norm(s)).strip()


def _times(sent_at: str | None) -> tuple[str, ...]:
    if not sent_at or len(sent_at) < 16:
        return ()
    hhmm = sent_at[11:16]
    return (hhmm, hhmm.lstrip("0") if hhmm[0] == "0" else hhmm)


# A quoted subject in a Basis: '...' closed by a quote that is followed by
# whitespace, punctuation or the end, so "Regent's Park" stays one quote.
_QUOTED = re.compile(r"'(.+?)'(?=[\s,;:.)\]]|$)")
_ELLIPSIS = re.compile(r"\.\.\.|…")


def cited_subjects(basis: str) -> list[str]:
    """Normalized subjects quoted in a Basis. The hand runs often shorten
    them ('RE: Suffolk v. 19Bway' for the full subject, or 'Notification of
    Service... Certificate'), so each is kept as the part before any
    ellipsis and matched as a prefix."""
    out = []
    for m in _QUOTED.finditer(basis or ""):
        head = _ELLIPSIS.split(m.group(1))[0]
        f = norm_subject(head)
        if len(f) >= 8:
            out.append(f)
    return out


def basis_hit(email: dict, basis: str, basis_norm: str | None = None,
              cited: list[str] | None = None) -> bool:
    """Does the Basis text cite this email?

    Either the email's whole subject appears in the basis (long subjects on
    their own; short ones like 'RE: Questions' only with the send time in
    the basis too), or a subject quoted in the basis is the start of the
    email's subject."""
    ns = norm_subject(email.get("subj") or email.get("subject") or "")
    if len(ns) < 4:
        return False
    nb = basis_norm if basis_norm is not None else norm(basis)
    if f" {ns} " in f" {nb} ":
        if len(ns) >= 12:
            return True
        if any(re.search(rf"(?<![\d:]){re.escape(t)}(?!\d)", basis or "")
               for t in _times(email.get("sent_at"))):
            return True
    for f in (cited if cited is not None else cited_subjects(basis)):
        if ns == f or ns.startswith(f + " "):
            return True
    return False


# =============================================================================
# Hand-run loaders
# =============================================================================

def _load_hand_xlsx(path: Path) -> list[dict]:
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    try:
        for ws in wb.worksheets:
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue
            H = {str(h).strip(): i for i, h in enumerate(rows[0]) if h is not None}
            if not {"Suggested Narrative", "Confidence", "Basis (source email)"} <= set(H):
                continue
            out = []
            for r in rows[1:]:
                if not r or r[H["Matter"]] in (None, ""):
                    continue
                d = r[H["Work Date"]]
                d = d.date().isoformat() if hasattr(d, "date") else str(d)[:10]
                out.append({
                    "date": d,
                    "matter": str(r[H["Matter"]]).strip(),
                    "matter_name": str(r[H["Matter Name"]] or "") if "Matter Name" in H else "",
                    "hours": float(r[H["Hours"]] or 0),
                    "narrative": str(r[H["Suggested Narrative"]] or ""),
                    "confidence": str(r[H["Confidence"]] or "").strip(),
                    "basis": str(r[H["Basis (source email)"]] or ""),
                })
            return out
    finally:
        wb.close()
    raise ValueError(f"{path}: no sheet with Suggested Narrative / Confidence / "
                     f"Basis (source email) columns")


def _load_hand_py(path: Path) -> list[dict]:
    """A drafts/_work/*_drafts.py source: add(...) calls plus fix(...)
    overrides. Parsed with ast and literal_eval, never executed."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    D: dict[tuple, list] = {}
    order: list[tuple] = []
    for node in tree.body:
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)):
            continue
        fn = node.value.func.id
        args = [ast.literal_eval(a) for a in node.value.args]
        kw = {k.arg: ast.literal_eval(k.value) for k in node.value.keywords}
        if fn == "add":
            date_, matter, hours, narr, conf, basis = args[:6]
            note = args[6] if len(args) > 6 else kw.get("note", "")
            key = (date_, matter, float(hours))
            if key not in D:
                order.append(key)
            D.setdefault(key, []).append([narr, conf, basis, note])
        elif fn == "fix":
            date_, matter, hours = args[:3]
            idx = kw.get("idx", args[3] if len(args) > 3 else 0)
            rec = D[(date_, matter, float(hours))][idx]
            for i, name in enumerate(("narr", "conf", "basis", "note")):
                if kw.get(name) is not None:
                    rec[i] = kw[name]
    out = []
    for key in order:
        for narr, conf, basis, _note in D[key]:
            out.append({"date": key[0], "matter": key[1], "matter_name": "",
                        "hours": key[2], "narrative": narr,
                        "confidence": conf, "basis": basis})
    return out


def load_hand(path: Path | str) -> list[dict]:
    p = Path(path)
    if p.suffix.lower() == ".py":
        return _load_hand_py(p)
    return _load_hand_xlsx(p)


# =============================================================================
# The backtest
# =============================================================================

def run_backtest(hand: list[dict], timecard_entries: list[dict],
                 emails: list[dict], props: dict, notes: dict, m2n: dict,
                 settings: dict) -> dict:
    names = {}
    for e in timecard_entries:
        if e.get("matter") and e.get("matter_name"):
            names.setdefault(e["matter"], e["matter_name"])
    entries = [{"row": i, "work_date": h["date"], "hours": h["hours"],
                "matter": h["matter"],
                "matter_name": names.get(h["matter"]) or h.get("matter_name") or "",
                "narrative": ""} for i, h in enumerate(hand)]
    results = correlate(entries, emails, props, notes, m2n, settings)

    by_date: dict[str, list[dict]] = {}
    for e in emails:
        by_date.setdefault(e["date"], []).append(e)

    rows = []
    for h, res in zip(hand, results):
        nb = norm(h["basis"])
        cited = cited_subjects(h["basis"])
        rank = None
        for i, c in enumerate(res["cands"], 1):
            if basis_hit(c, h["basis"], nb, cited):
                rank = i
                break
        d0 = date.fromisoformat(h["date"])
        nearby = [e for k in range(-7, 8)
                  for e in by_date.get((d0 + timedelta(k)).isoformat(), [])]
        in_mail = any(basis_hit(e, h["basis"], nb, cited) for e in nearby)
        rows.append({
            "date": h["date"], "matter": h["matter"], "hours": h["hours"],
            "confidence": h["confidence"], "basis": h["basis"][:240],
            "in_mail": in_mail, "rank": rank, "stage": res["stage"],
            "n_cands": len(res["cands"]),
            "top": [f"{c['date']} {(c.get('sent_at') or '')[11:16]} {c['subj'][:70]}"
                    for c in res["cands"][:3]],
            "zero_hit_token": res["zero_hit_token"],
        })

    def group(conf):
        g = [r for r in rows if r["confidence"] == conf]
        n = len(g)
        inm = [r for r in g if r["in_mail"]]

        def share(xs, k):
            return round(sum(1 for r in xs if r["rank"] and r["rank"] <= k)
                         / len(xs), 4) if xs else None
        return {"rows": n, "in_mail": len(inm),
                "in_mail_rate": round(len(inm) / n, 3) if n else None,
                "hit@1": share(g, 1), "hit@3": share(g, 3),
                f"hit@{settings['max_candidates']}": share(g, settings["max_candidates"]),
                "hit@k_given_in_mail": share(inm, settings["max_candidates"])}

    summary = {conf: group(conf) for conf in ("High", "Medium")}
    high = summary["High"]
    summary["pass_mark"] = 0.9
    summary["passed"] = bool(high["rows"]) and (
        (high[f"hit@{settings['max_candidates']}"] or 0) >= 0.9)
    return {"summary": summary, "rows": rows}


def format_report(label: str, result: dict, mail_kind: str,
                  n_emails: int, k: int) -> str:
    s = result["summary"]
    lines = [f"Time entries backtest: {label}",
             f"Mail source: {mail_kind} ({n_emails} sent emails loaded)", ""]
    for conf in ("High", "Medium"):
        g = s[conf]
        if not g["rows"]:
            continue
        lines.append(
            f"{conf:6s} {g['rows']:4d} rows | cited email in mail: {g['in_mail']} "
            f"({_pct(g['in_mail_rate'])}) | hit@1 {_pct(g['hit@1'])} "
            f"hit@3 {_pct(g['hit@3'])} hit@{k} {_pct(g[f'hit@{k}'])} "
            f"| hit@{k} where in mail {_pct(g['hit@k_given_in_mail'])}")
    lines.append("")
    lines.append(f"Pass mark: hit@{k} >= 90% on High rows -> "
                 + ("PASSED" if s["passed"] else "NOT MET"))
    misses = [r for r in result["rows"]
              if r["confidence"] == "High" and not r["rank"]]
    if misses:
        none = sum(1 for r in misses if not r["n_cands"])
        lines += ["", f"High rows the correlator missed ({len(misses)}): "
                      f"{none} with no candidates at all, "
                      f"{len(misses) - none} where the cited email ranked "
                      f"below {k} or never scored"]
        for r in misses:
            lines.append(f"  {r['date']} {r['matter']} {r['hours']}h "
                         f"[{'in mail' if r['in_mail'] else 'NOT in mail'}; "
                         f"{r['n_cands']} cands, stage {r['stage']}]")
            lines.append(f"      basis: {r['basis'][:150]}")
            for t in r["top"]:
                lines.append(f"      cand:  {t}")
    return "\n".join(lines)


def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"
