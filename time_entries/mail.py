"""James's sent mail, read from Minotaur's email brain.

Minotaur is the only program that pulls James's mail; Rocky only reads
`C:\\Minotaur\\email_brain\\brain.db`, and always read-only (Minotaur's
BRAIN_DATA_LAYOUT.md is the contract):

  * opened with mode=ro, never written;
  * meta.schema_version must be 1 — anything else stops the run rather
    than guessing at a changed layout;
  * deduped on internet_message_id (a copy dragged to another folder gets
    a new graph_id);
  * sent_at is UTC and is converted to Eastern with the real DST rule.
    The folder scripts subtracted a flat 4 hours, which files every send
    after 7 PM on the wrong day once daylight time ends (11/1/2026).

CorpusMail reads the folder's legacy corpus.db in the same shape. It exists
so the port can be checked against correlate3.py on the dev laptop, which
has no brain.db.
"""

import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

INVITE_RE = re.compile(r"^(Accepted|Declined|Canceled|Cancelled|Tentative)[:_]", re.I)
SCHEMA_VERSION = "1"


class BrainError(RuntimeError):
    pass


# =============================================================================
# Eastern time without tzdata (Windows Python ships no zone database)
# =============================================================================

def _nth_sunday(year: int, month: int, n: int) -> int:
    first = date(year, month, 1)
    offset = (6 - first.weekday()) % 7          # Monday=0 ... Sunday=6
    return 1 + offset + 7 * (n - 1)


def to_eastern(dt: datetime) -> datetime:
    """UTC (aware, or naive meaning UTC) -> US Eastern, DST-correct.

    US rule since 2007: daylight time from 2:00 AM local on the second Sunday
    of March (07:00 UTC) to 2:00 AM local on the first Sunday of November
    (06:00 UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    u = dt.astimezone(timezone.utc).replace(tzinfo=None)
    y = u.year
    dst_start = datetime(y, 3, _nth_sunday(y, 3, 2), 7, 0)
    dst_end = datetime(y, 11, _nth_sunday(y, 11, 1), 6, 0)
    hours = -4 if dst_start <= u < dst_end else -5
    return (u + timedelta(hours=hours)).replace(
        tzinfo=timezone(timedelta(hours=hours)))


def parse_utc(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _addr_list(raw) -> str:
    if not raw:
        return ""
    try:
        vals = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return str(raw)
    if isinstance(vals, list):
        return "; ".join(str(v) for v in vals if v)
    return str(vals)


def make_hay(email: dict) -> str:
    return " | ".join(filter(None, [email.get("subject"), email.get("to"),
                                    email.get("cc"), email.get("body"),
                                    email.get("file")])).lower()


# =============================================================================
# Sources
# =============================================================================

class BrainMail:
    """Read-only view of Minotaur's brain.db."""

    def __init__(self, path: Path | str, timeout: float = 60.0):
        p = Path(path)
        if not p.exists():
            raise BrainError(f"Minotaur brain not found at {p}")
        self.path = p
        self.cx = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True,
                                  timeout=timeout)
        self.cx.row_factory = sqlite3.Row
        try:
            row = self.cx.execute(
                "SELECT value FROM meta WHERE key='schema_version'").fetchone()
        except sqlite3.DatabaseError as e:
            self.cx.close()
            raise BrainError(f"{p} is not a readable email brain: {e}") from e
        version = row[0] if row else None
        if str(version) != SCHEMA_VERSION:
            self.cx.close()
            raise BrainError(
                f"{p} has schema_version {version!r}; this build reads "
                f"{SCHEMA_VERSION}. Minotaur changed its layout — update "
                f"time_entries/mail.py against BRAIN_DATA_LAYOUT.md first.")

    def close(self):
        self.cx.close()

    def newest_sent(self) -> datetime | None:
        row = self.cx.execute("SELECT MAX(sent_at) FROM messages "
                              "WHERE direction='sent'").fetchone()
        return parse_utc(row[0]) if row else None

    def sent_between(self, dfrom: str, dto: str) -> list[dict]:
        """James's sends with an Eastern send date in [dfrom, dto]."""
        lo = (date.fromisoformat(dfrom) - timedelta(days=1)).isoformat()
        hi = (date.fromisoformat(dto) + timedelta(days=2)).isoformat()
        rows = self.cx.execute(
            "SELECT graph_id, internet_message_id, conversation_id, sent_at, "
            "subject, to_addrs, cc_addrs, body_text, ingested_at "
            "FROM messages WHERE direction='sent' AND sent_at >= ? AND sent_at < ? "
            "ORDER BY sent_at, ingested_at", (lo, hi)).fetchall()
        out, seen = [], set()
        for r in rows:
            key = r["internet_message_id"] or r["graph_id"]
            if key in seen:
                continue
            utc = parse_utc(r["sent_at"])
            if utc is None:
                continue
            et = to_eastern(utc)
            d = et.date().isoformat()
            if not dfrom <= d <= dto:
                continue
            seen.add(key)
            subject = r["subject"] or ""
            e = {
                "id": key,
                "conversation_id": r["conversation_id"],
                "date": d,
                "sent_at": et.replace(tzinfo=None).isoformat(timespec="seconds"),
                "subject": subject,
                "to": _addr_list(r["to_addrs"]),
                "cc": _addr_list(r["cc_addrs"]),
                "body": r["body_text"] or "",
                "inv": bool(INVITE_RE.match(subject)),
            }
            e["hay"] = make_hay(e)
            out.append(e)
        return out


class CorpusMail:
    """The Time Entries folder's legacy corpus.db, in BrainMail's shape.
    Opened immutable: it sits on OneDrive and must never grow a journal."""

    def __init__(self, path: Path | str):
        p = Path(path)
        if not p.exists():
            raise BrainError(f"corpus.db not found at {p}")
        self.path = p
        self.cx = sqlite3.connect(f"file:{p.as_posix()}?mode=ro&immutable=1",
                                  uri=True)
        self.cx.row_factory = sqlite3.Row

    def close(self):
        self.cx.close()

    def newest_sent(self) -> datetime | None:
        row = self.cx.execute("SELECT MAX(sent_at) FROM emails").fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    def sent_between(self, dfrom: str, dto: str) -> list[dict]:
        out = []
        for r in self.cx.execute(
                "SELECT id, date, sent_at, subject, recipients, cc, body, "
                "is_invite, src_file FROM emails WHERE date BETWEEN ? AND ? "
                "ORDER BY id", (dfrom, dto)):
            e = {
                "id": f"corpus:{r['id']}",
                "conversation_id": None,
                "date": r["date"],
                "sent_at": r["sent_at"],
                "subject": r["subject"],
                "to": r["recipients"],
                "cc": r["cc"],
                "body": r["body"],
                "file": Path(r["src_file"] or "").name,
                "inv": bool(r["is_invite"]),
            }
            e["hay"] = make_hay(e)
            out.append(e)
        return out


def open_mail(kind: str, paths: dict):
    if kind == "corpus":
        return CorpusMail(paths["corpus"])
    return BrainMail(paths["brain"])
