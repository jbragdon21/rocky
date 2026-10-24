"""Aspen timecard importer.

Three shapes have turned up in the Time Entries folder, and any of them may
arrive as .csv or .xlsx:

  * the full "Time Entries List" export: one or two preamble lines (title and
    date range, sometimes a status filter), then a header row starting
    HH:MM:SS, Hours, Work Date, Matter, ... Timekeeper ID, Timekeeper Name;
  * a seven-column export (HH:MM:SS, Hours, Work Date, Matter, Matter Name,
    Client, Narrative) with no preamble (March);
  * a six-column export (Work Date, Matter, Matter Name, Client, Narrative,
    Status) with M/D/YYYY dates and no hours at all (May).

The header is whichever of the first six rows carries Work Date, Matter and
Narrative. Rows keep their order and their file row number, because two
identical blanks (same date, matter and hours) are two entries.
"""

import csv
import io
import re
from datetime import date, datetime
from pathlib import Path

HEADER_KEYS = ("Work Date", "Matter", "Narrative")
JAMES_TIMEKEEPER_ID = "0197"

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d",
                 "%d-%b-%Y", "%b %d, %Y")
_HHMMSS = re.compile(r"^(\d+):(\d{2}):(\d{2})$")


def norm_date(value) -> str:
    """Aspen dates vary ('2026-08-30', '5/30/2026', '8/3/26'); ISO out."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    s = str(value or "").strip()
    if not s:
        return ""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return "%04d-%02d-%02d" % tuple(int(x) for x in m.groups())
    return s


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return norm_date(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value)) if abs(value) >= 1 else str(value)
    return str(value)


def read_rows(path: Path | str, content: bytes | None = None) -> list[list[str]]:
    """Every row of a .csv or .xlsx as lists of strings. Pass `content` to
    read an email attachment without writing it to disk."""
    name = str(path).lower()
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl
        src = io.BytesIO(content) if content is not None else path
        # read_only=False on purpose: read-only mode trusts the stored sheet
        # dimension, which some workbooks get wrong, and silently stops early.
        wb = openpyxl.load_workbook(src, data_only=True)
        try:
            ws = wb.worksheets[0]
            return [[_cell(v) for v in row] for row in ws.iter_rows(values_only=True)]
        finally:
            wb.close()
    raw = content if content is not None else Path(path).read_bytes()
    text = raw.decode("utf-8-sig", errors="replace")
    return list(csv.reader(io.StringIO(text)))


def find_header(rows: list[list[str]]) -> int | None:
    for i, row in enumerate(rows[:6]):
        cells = {c.strip() for c in row}
        if all(k in cells for k in HEADER_KEYS):
            return i
    return None


def is_timecard(rows: list[list[str]]) -> bool:
    return find_header(rows) is not None


def parse_hours(hours: str, hhmmss: str) -> float | None:
    h = (hours or "").strip()
    if h:
        try:
            return round(float(h), 2)
        except ValueError:
            pass
    m = _HHMMSS.match((hhmmss or "").strip())
    if m:
        hh, mm, ss = (int(x) for x in m.groups())
        return round(hh + mm / 60 + ss / 3600, 2)
    return None


def _yes(value: str) -> bool:
    return (value or "").strip().lower() in ("yes", "y", "true", "1")


def parse_rows(rows: list[list[str]], source: str = "",
               timekeeper_id: str | None = None) -> dict:
    """Normalize one export. Returns {"entries", "period", "header",
    "skipped_other_timekeepers", "source"}.

    timekeeper_id: keep only that timekeeper's rows when the export has a
    Timekeeper ID column. Exports without one are taken as they are."""
    hdr_i = find_header(rows)
    if hdr_i is None:
        raise ValueError(f"{source or 'timecard'}: no header row with "
                         f"{', '.join(HEADER_KEYS)} in the first six rows")
    header = [c.strip() for c in rows[hdr_i]]
    col = {name: i for i, name in enumerate(header) if name}

    def get(row, name):
        i = col.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    period = None
    if hdr_i > 0 and rows[0] and rows[0][0].strip().lower() == "time entries list":
        lo = norm_date(rows[0][1]) if len(rows[0]) > 1 else ""
        hi = norm_date(rows[0][2]) if len(rows[0]) > 2 else ""
        if lo and hi:
            period = (lo, hi)

    entries, skipped = [], 0
    for n, row in enumerate(rows[hdr_i + 1:], start=hdr_i + 2):
        work_date = norm_date(get(row, "Work Date"))
        if not work_date:
            continue
        tk = get(row, "Timekeeper ID")
        if timekeeper_id and "Timekeeper ID" in col and tk and tk != timekeeper_id:
            skipped += 1
            continue
        entries.append({
            "row": n,
            "work_date": work_date,
            "hours": parse_hours(get(row, "Hours"), get(row, "HH:MM:SS")),
            "matter": get(row, "Matter"),
            "matter_name": get(row, "Matter Name"),
            "client": get(row, "Client"),
            "narrative": get(row, "Narrative"),
            "status": get(row, "Status"),
            "non_billable": _yes(get(row, "Non-Billable")),
            "no_charge": _yes(get(row, "No Charge")),
            "timekeeper_id": tk,
            "timekeeper_name": get(row, "Timekeeper Name"),
        })
    if period is None and entries:
        dates = sorted(e["work_date"] for e in entries)
        period = (dates[0], dates[-1])
    return {"entries": entries, "period": period, "header": header,
            "skipped_other_timekeepers": skipped, "source": source}


def load_timecard(path: Path | str, content: bytes | None = None,
                  timekeeper_id: str | None = None) -> dict:
    return parse_rows(read_rows(path, content), source=Path(str(path)).name,
                      timekeeper_id=timekeeper_id)
