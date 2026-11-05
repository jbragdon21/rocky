"""The archive search. The CLI uses it now and the Teams chat's
search_archive tool will use the same function, so what James tests is what
Joe gets.

Queries use FTS5 syntax: "exact phrase", AND / OR / NOT, NEAR(a b, 10),
prefix*. A query FTS5 can't parse is retried once with every word quoted, so
a stray apostrophe or colon never turns into an error. Dates are Eastern
calendar days. Nothing here builds SQL from the caller's text: filters are
bound parameters, and the query string only ever reaches MATCH.
"""

import re
import sqlite3
from datetime import date, datetime, timedelta, timezone

from time_entries.mail import parse_utc, to_eastern

# bm25 weights, in messages_fts column order.
WEIGHTS = (6.0, 2.0, 3.0, 1.0, 2.0)  # subject, recipients, body_own, body_quoted, attachments
OWN_COLUMNS = "{subject body_own}"    # "Joe's own words": his subject line and top text
MAX_LIMIT = 100
ORDERS = {"rank": "score", "newest": "m.sent_utc DESC", "oldest": "m.sent_utc ASC"}


class SearchError(ValueError):
    pass


def day_start_utc(day: str | date) -> str:
    """An Eastern calendar day -> the UTC instant it starts."""
    d = date.fromisoformat(day) if isinstance(day, str) else day
    for hours in (4, 5):
        utc = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(hours=hours)
        if to_eastern(utc).utcoffset() == timedelta(hours=-hours):
            return utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    return (datetime(d.year, d.month, d.day) + timedelta(hours=5)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def eastern(sent_utc: str | None) -> str:
    dt = parse_utc(sent_utc)
    if not dt:
        return ""
    return to_eastern(dt).strftime("%Y-%m-%d %I:%M %p").replace(" 0", " ", 1)


# snippet() highlight markers that can't occur in mail text; shown as [ ].
_MARK_ON, _MARK_OFF = "\x02", "\x03"
# What SQLite says about a MATCH string it can't parse.
_QUERY_ERRORS = ("fts5", "syntax", "unterminated", "no such column", "match")
_TOKEN = re.compile(r'"([^"]*)"|(\S+)')
_OPERATORS = {"AND", "OR", "NOT", "NEAR"}


def fts_safe(query: str) -> str:
    """Every phrase and word quoted, operators dropped: what the user typed,
    read literally, all terms required."""
    parts = []
    for phrase, word in _TOKEN.findall(query or ""):
        if phrase:
            text = " ".join(re.findall(r"[\w'’-]+", phrase))
        elif word in _OPERATORS:
            continue
        else:
            text = " ".join(re.findall(r"[\w'’-]+", word))
        if text:
            parts.append(f'"{text}"')
    return " ".join(parts)


def _like(text: str) -> str:
    return "%" + re.sub(r"([\\%_])", r"\\\1", text.strip()) + "%"


def _people_short(lst: str, keep: int = 3) -> str:
    people = [p.strip() for p in (lst or "").split(";") if p.strip()]
    names = [re.sub(r"\s*<[^>]*>", "", p) or p for p in people]
    more = len(names) - keep
    return ", ".join(names[:keep]) + (f" +{more} more" if more > 0 else "")


def _flat(text: str, cap: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= cap else text[:cap].rsplit(" ", 1)[0] + " ..."


def search(conn: sqlite3.Connection, query: str = "", *, date_from: str | None = None,
           date_to: str | None = None, recipient: str | None = None,
           subject: str | None = None, fields: str = "all", order: str = "rank",
           limit: int = 20, offset: int = 0) -> dict:
    """{"total", "hits", "query_used", "rewritten", "order"}. Each hit:
    ref, sent (Eastern), to, cc, subject, snippet, attachments."""
    if order not in ORDERS:
        raise SearchError(f"order must be one of {', '.join(ORDERS)}")
    if fields not in ("all", "own"):
        raise SearchError("fields must be 'all' or 'own'")
    limit = max(1, min(int(limit or 20), MAX_LIMIT))
    filters, params = [], []
    try:
        if date_from:
            filters.append("m.sent_utc >= ?")
            params.append(day_start_utc(date_from))
        if date_to:
            filters.append("m.sent_utc < ?")
            params.append(day_start_utc(date.fromisoformat(date_to) + timedelta(days=1)))
    except ValueError:
        raise SearchError("dates must be YYYY-MM-DD")
    if recipient and recipient.strip():
        filters.append("m.recipients LIKE ? ESCAPE '\\'")
        params.append(_like(recipient))
    if subject and subject.strip():
        filters.append("m.subject LIKE ? ESCAPE '\\'")
        params.append(_like(subject))

    query = (query or "").strip()
    if not query:
        if order == "rank":
            order = "newest"
        return _browse(conn, filters, params, order, limit, offset)

    tries = [query]
    if fts_safe(query) and fts_safe(query) != query:
        tries.append(fts_safe(query))
    last = None
    for q in tries:
        match = f"{OWN_COLUMNS} : ({q})" if fields == "own" else q
        try:
            result = _fts(conn, match, filters, params, order, limit, offset)
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if not any(k in msg for k in _QUERY_ERRORS):
                raise           # a locked or broken database, not a bad query
            last = e
            continue
        result.update(query_used=q, rewritten=q != query)
        return result
    raise SearchError(f"couldn't read the search {query!r} ({last})")


def _fts(conn, match, filters, params, order, limit, offset) -> dict:
    where = " AND ".join(["messages_fts MATCH ?"] + filters)
    base = (f"FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid "
            f"WHERE {where}")
    total = conn.execute(f"SELECT COUNT(*) {base}", [match] + params).fetchone()[0]
    weights = ", ".join(str(w) for w in WEIGHTS)
    # An excerpt from Joe's own text when the match is there, then the
    # quoted history, then whichever column matched (subject, recipients).
    snips = ", ".join(
        f"snippet(messages_fts, {col}, '{_MARK_ON}', '{_MARK_OFF}', ' ... ', 24) AS s{i}"
        for i, col in enumerate((2, 3, -1)))
    rows = conn.execute(
        f"SELECT m.id, m.sent_utc, m.subject, m.to_list, m.cc_list, m.attachment_names, "
        f"{snips}, bm25(messages_fts, {weights}) AS score {base} "
        f"ORDER BY {ORDERS[order]} LIMIT ? OFFSET ?",
        [match] + params + [limit, offset]).fetchall()
    hits = []
    for r in rows:
        snip = next((r[k] for k in ("s0", "s1") if _MARK_ON in (r[k] or "")), r["s2"])
        hits.append(_hit(r, (snip or "").replace(_MARK_ON, "[").replace(_MARK_OFF, "]")))
    return {"total": total, "order": order, "hits": hits}


def _browse(conn, filters, params, order, limit, offset) -> dict:
    where = (" WHERE " + " AND ".join(filters)) if filters else ""
    total = conn.execute(f"SELECT COUNT(*) FROM messages m{where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT m.id, m.sent_utc, m.subject, m.to_list, m.cc_list, m.attachment_names, "
        f"COALESCE(NULLIF(m.body_own, ''), m.body_quoted) AS snip "
        f"FROM messages m{where} ORDER BY {ORDERS[order]} LIMIT ? OFFSET ?",
        params + [limit, offset]).fetchall()
    return {"total": total, "order": order, "query_used": "", "rewritten": False,
            "hits": [_hit(r, r["snip"]) for r in rows]}


def _hit(r, snip: str) -> dict:
    return {"ref": r["id"], "sent": eastern(r["sent_utc"]),
            "to": _people_short(r["to_list"]), "cc": _people_short(r["cc_list"], 2),
            "subject": r["subject"] or "(no subject)", "snippet": _flat(snip, 260),
            "attachments": r["attachment_names"] or ""}


def read_message(conn: sqlite3.Connection, ref: int) -> dict | None:
    row = conn.execute("SELECT * FROM messages WHERE id=?", (int(ref),)).fetchone()
    return dict(row) if row else None


# =============================================================================
# Text forms (CLI output now, tool results for the chat later)
# =============================================================================

def format_hits(result: dict, query: str = "") -> str:
    total, hits = result["total"], result["hits"]
    head = f"{total} match{'es' if total != 1 else ''}"
    if query:
        head += f" for {result.get('query_used') or query}"
        if result.get("rewritten"):
            head += f" (read literally; {query!r} isn't valid search syntax)"
    if total > len(hits):
        head += f"; {len(hits)} shown, {ORDERS_LABEL[result['order']]}"
    lines = [head]
    for h in hits:
        lines.append(f"[{h['ref']}] {h['sent']}  To: {h['to'] or '(none)'}"
                     + (f"  Cc: {h['cc']}" if h["cc"] else ""))
        lines.append(f"     {h['subject']}")
        if h["attachments"]:
            lines.append(f"     Attachments: {_flat(h['attachments'], 160)}")
        if h["snippet"]:
            lines.append(f"     {h['snippet']}")
    return "\n".join(lines)


ORDERS_LABEL = {"rank": "best first", "newest": "newest first", "oldest": "oldest first"}


def format_message(row: dict, cap: int = 20_000) -> str:
    lines = [f"[{row['id']}] {eastern(row['sent_utc'])}",
             f"Subject: {row['subject'] or '(no subject)'}",
             f"To: {row['to_list'] or '(none)'}"]
    if row["cc_list"]:
        lines.append(f"Cc: {row['cc_list']}")
    if row["bcc_list"]:
        lines.append(f"Bcc: {row['bcc_list']}")
    if row["attachment_names"]:
        lines.append(f"Attachments: {row['attachment_names']}")
    lines += ["", row["body_own"] or "(nothing typed above the history)"]
    quoted = row["body_quoted"] or ""
    if quoted:
        room = max(0, cap - len(row["body_own"] or ""))
        lines += ["", "---- quoted history ----",
                  quoted if len(quoted) <= room else quoted[:room] + "\n[... cut]"]
    return "\n".join(lines)
