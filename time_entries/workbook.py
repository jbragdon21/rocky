"""The recommendations workbook, ported from the folder's build_workbook.py.

Same house format as the hand runs: Read Me, Draft Narratives, Uncaptured
Emails and Summary sheets, with rows shaded by confidence (High green,
Medium amber, REVIEW red). Two additions: a hidden Basis IDs column holding
the internet_message_ids behind each Basis, and a Verifier column saying
what the verification pass changed.
"""

import collections
from pathlib import Path

FILL = {"High": "E2EFDA", "Medium": "FFF2CC", "REVIEW": "F8CBAD"}
HDR_FILL = "1F3864"


def build(path: Path | str, recs: list[dict], readme: dict) -> Path:
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    thin = Side(style="thin", color="BFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap = Alignment(wrap_text=True, vertical="top")

    def header(ws, cols, widths):
        for j, (name, w) in enumerate(zip(cols, widths), 1):
            c = ws.cell(row=1, column=j, value=name)
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor=HDR_FILL)
            c.alignment = wrap
            c.border = border
            ws.column_dimensions[get_column_letter(j)].width = w
        ws.freeze_panes = "A2"

    drafts = sorted((r for r in recs if r["kind"] == "draft"),
                    key=lambda r: (r["work_date"], r["matter"], r.get("entry_row") or 0))
    unc = sorted((r for r in recs if r["kind"] == "uncaptured"),
                 key=lambda r: (r["work_date"], r["matter"]))
    wb = openpyxl.Workbook()

    # ---- Read Me
    ws = wb.active
    ws.title = "Read Me"
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 120
    lines = [(readme.get("title"), None), (None, None),
             ("Period", readme.get("period")), ("Emails", readme.get("emails")),
             ("Run date", readme.get("run_date")), (None, None), ("Sheets", None),
             ("Draft Narratives",
              "One row per blank or partial entry, in work-date order. Suggested Narrative is in "
              "house style; Confidence is High (same-day email read and drafted from the body), "
              "Medium (thread identified, date or allocation inferred), or REVIEW (no reliable "
              "basis; the note says why). Basis names the emails relied on. Review Note flags "
              "hours, duplicates, coding questions. Verifier says what the second pass changed."),
             ("Uncaptured Emails",
              "Sent-mail threads with substantive text and no time entry on that matter that "
              "day. Each has a suggested matter, hours and narrative. \"(unknown)\" means the "
              "matter could not be identified from the email."),
             ("Summary", "Counts and hours by confidence and by date."), (None, None),
             ("Results", readme.get("results")), ("Verification", readme.get("verification")),
             (None, None), ("Things to decide", None)]
    for i, item in enumerate(readme.get("decide") or [], 1):
        lines.append((str(i), item))
    for r, (k, v) in enumerate(lines, 1):
        ws.cell(row=r, column=1, value=k)
        ws.cell(row=r, column=2, value=v).alignment = wrap
        if r == 1:
            ws.cell(row=1, column=1).font = Font(bold=True, size=14)
        elif k:
            ws.cell(row=r, column=1).font = Font(bold=True)

    # ---- Draft Narratives
    ws = wb.create_sheet("Draft Narratives")
    cols = ["Work Date", "Hours", "Matter", "Matter Name", "Client", "Suggested Narrative",
            "Confidence", "Basis (source email)", "Review Note", "Verifier", "Basis IDs"]
    header(ws, cols, [11, 6, 12, 38, 24, 70, 11, 55, 60, 40, 30])
    for r, d in enumerate(drafts, 2):
        verifier = ""
        if d.get("problem"):
            first = (d.get("first") or {}).get("confidence")
            verifier = d["problem"] + (f" (was {first})" if first and first != d["confidence"]
                                       else "")
        row = [d["work_date"], d.get("hours"), d["matter"], d.get("matter_name"),
               d.get("client"), d.get("narrative"), d.get("confidence"), d.get("basis"),
               d.get("note"), verifier, "\n".join(d.get("basis_ids") or [])]
        fill = PatternFill("solid", fgColor=FILL.get(d.get("confidence"), "FFFFFF"))
        for j, v in enumerate(row, 1):
            c = ws.cell(row=r, column=j, value=v)
            c.alignment = wrap
            c.fill = fill
            c.border = border
    ws.column_dimensions["K"].hidden = True
    ws.auto_filter.ref = f"A1:J{len(drafts) + 1}"

    # ---- Uncaptured Emails
    ws = wb.create_sheet("Uncaptured Emails")
    cols = ["Email Date", "Suggested Matter", "Matter Name / Thread", "Suggested Hours",
            "Suggested Narrative", "Basis (source email)", "Note", "Basis IDs"]
    header(ws, cols, [11, 14, 40, 9, 70, 55, 55, 30])
    for r, u in enumerate(unc, 2):
        row = [u["work_date"], u["matter"], u.get("matter_name"), u.get("hours"),
               u.get("narrative"), u.get("basis"), u.get("note"),
               "\n".join(u.get("basis_ids") or [])]
        for j, v in enumerate(row, 1):
            c = ws.cell(row=r, column=j, value=v)
            c.alignment = wrap
            c.border = border
            if str(u["matter"]).startswith("("):
                c.fill = PatternFill("solid", fgColor="EDEDED")
    ws.column_dimensions["H"].hidden = True
    ws.auto_filter.ref = f"A1:G{len(unc) + 1}"

    # ---- Summary
    ws = wb.create_sheet("Summary")
    ws.column_dimensions["A"].width = 26
    for col in "BCDEF":
        ws.column_dimensions[col].width = 13
    by_conf = collections.defaultdict(lambda: [0, 0.0])
    for d in drafts:
        by_conf[d["confidence"]][0] += 1
        by_conf[d["confidence"]][1] += d.get("hours") or 0
    ws.append(["Confidence", "Entries", "Hours"])
    for k in ("High", "Medium", "REVIEW"):
        ws.append([k, by_conf[k][0], round(by_conf[k][1], 1)])
    ws.append(["Total", len(drafts), round(sum(d.get("hours") or 0 for d in drafts), 1)])
    ws.append([])
    ws.append(["Work Date", "Entries to draft", "Hours", "High", "Medium", "REVIEW"])
    by_day = collections.defaultdict(lambda: [0, 0.0, 0, 0, 0])
    for d in drafts:
        x = by_day[d["work_date"]]
        x[0] += 1
        x[1] += d.get("hours") or 0
        x[{"High": 2, "Medium": 3}.get(d["confidence"], 4)] += 1
    for day in sorted(by_day):
        x = by_day[day]
        ws.append([day, x[0], round(x[1], 1), x[2], x[3], x[4]])
    ws.append([])
    ws.append(["Uncaptured threads listed", len(unc), "Suggested hours",
               round(sum(u.get("hours") or 0 for u in unc), 1)])
    for r in (1, 7):
        for c in ws[r]:
            c.font = Font(bold=True)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
