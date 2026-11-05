"""archive.db: one row per archived send, an FTS5 index over it, and the
helpers that turn a Graph listing row plus its .eml into that row.

The FTS5 table uses external content (content='messages'), kept in step by
triggers, so snippet() reads the text straight from `messages`. The unicode61
tokenizer does no stemming: a phrase search matches Joe's words exactly.
"""

import email
import email.policy
import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from time_entries.mail import parse_utc

SCHEMA_VERSION = "1"
BODY_CAP = 200_000   # chars kept per body half; the .eml always has everything
# An Enterprise Vault shortcut: a cut-down body and no attachments, standing
# in for an email the Vault holds. It keeps the original's internetMessageId.
SHORTCUT_PREFIX = "IPM.Note.EnterpriseVault.Shortcut"
SHORTCUT_SQL = "message_class LIKE 'IPM.Note.EnterpriseVault.Shortcut%'"


def is_shortcut(mclass: str | None) -> bool:
    return (mclass or "").startswith(SHORTCUT_PREFIX)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS messages(
    id               INTEGER PRIMARY KEY,
    msg_key          TEXT NOT NULL UNIQUE,
    graph_id         TEXT,
    internet_msg_id  TEXT,
    conversation_id  TEXT,
    folder           TEXT,
    message_class    TEXT,
    sent_utc         TEXT NOT NULL,
    subject          TEXT NOT NULL DEFAULT '',
    from_addr        TEXT NOT NULL DEFAULT '',
    recipients       TEXT NOT NULL DEFAULT '',
    to_list          TEXT NOT NULL DEFAULT '',
    cc_list          TEXT NOT NULL DEFAULT '',
    bcc_list         TEXT NOT NULL DEFAULT '',
    has_attachments  INTEGER NOT NULL DEFAULT 0,
    attachment_names TEXT NOT NULL DEFAULT '',
    body_own         TEXT NOT NULL DEFAULT '',
    body_quoted      TEXT NOT NULL DEFAULT '',
    eml_path         TEXT,
    eml_bytes        INTEGER,
    archived_utc     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_sent ON messages(sent_utc);
CREATE INDEX IF NOT EXISTS messages_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS messages_graph ON messages(graph_id);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    subject, recipients, body_own, body_quoted, attachment_names,
    content='messages', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2', prefix='2 3'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, subject, recipients, body_own, body_quoted,
                             attachment_names)
    VALUES (new.id, new.subject, new.recipients, new.body_own, new.body_quoted,
            new.attachment_names);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, subject, recipients, body_own,
                             body_quoted, attachment_names)
    VALUES ('delete', old.id, old.subject, old.recipients, old.body_own,
            old.body_quoted, old.attachment_names);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, subject, recipients, body_own,
                             body_quoted, attachment_names)
    VALUES ('delete', old.id, old.subject, old.recipients, old.body_own,
            old.body_quoted, old.attachment_names);
    INSERT INTO messages_fts(rowid, subject, recipients, body_own, body_quoted,
                             attachment_names)
    VALUES (new.id, new.subject, new.recipients, new.body_own, new.body_quoted,
            new.attachment_names);
END;
CREATE TABLE IF NOT EXISTS failures(
    graph_id  TEXT PRIMARY KEY,
    sent_utc  TEXT,
    subject   TEXT,
    error     TEXT,
    tries     INTEGER NOT NULL DEFAULT 1,
    last_utc  TEXT,
    folder    TEXT
);
"""


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_utc(iso: str | None) -> str:
    """Graph's sentDateTime in one fixed form, so string comparison sorts."""
    dt = parse_utc(iso)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else ""


def open_db(path: Path, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")   # search while a backfill writes
        conn.executescript(SCHEMA)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(failures)")}
        if "folder" not in cols:      # archives made before 11/5 (none ran live)
            conn.execute("ALTER TABLE failures ADD COLUMN folder TEXT")
        conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
                     (SCHEMA_VERSION,))
        conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


# =============================================================================
# Turning a Graph row into an archive row
# =============================================================================

# Where Joe's reply ends and the quoted history begins (ported from
# Minotaur's email_brain.py, which tuned them on James's 113k sends).
THREAD_MARKERS = [
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.I | re.M),
    re.compile(r"^_{10,}\s*$", re.M),                      # Outlook divider line
    re.compile(r"^\s*From:\s*.+$", re.I | re.M),           # quoted Outlook header
    re.compile(r"^On .{3,120}? wrote:\s*$", re.I | re.M),  # Gmail / Apple
]
_BLANKS = re.compile(r"\n[ \t]*\n(?:[ \t]*\n)+")


def split_body(text: str) -> tuple[str, str]:
    """(Joe's own text, the quoted history below it). A forward with nothing
    typed above it is all history."""
    text = _BLANKS.sub("\n\n", (text or "").replace("\r\n", "\n")).strip()
    cut = None
    for pat in THREAD_MARKERS:
        m = pat.search(text)
        if m and (cut is None or m.start() < cut):
            cut = m.start()
    if cut is None:
        return text[:BODY_CAP], ""
    return text[:cut].strip()[:BODY_CAP], text[cut:].strip()[:BODY_CAP]


def _people(lst) -> list[str]:
    out = []
    for r in lst or []:
        ea = (r or {}).get("emailAddress") or {}
        name, addr = (ea.get("name") or "").strip(), (ea.get("address") or "").strip()
        if name and addr and name.lower() != addr.lower():
            out.append(f"{name} <{addr}>")
        elif addr or name:
            out.append(addr or name)
    return out


def msg_key(msg: dict) -> str:
    """internetMessageId survives a move between folders; the Graph id doesn't."""
    imid = (msg.get("internetMessageId") or "").strip()
    return imid.lower() if imid else f"graph:{msg.get('id')}"


def eml_relpath(msg: dict) -> str:
    sent = norm_utc(msg.get("sentDateTime")) or "0000-00-00T00:00:00Z"
    digest = hashlib.sha1(msg_key(msg).encode("utf-8")).hexdigest()[:12]
    stamp = sent[:19].replace("-", "").replace(":", "").replace("T", "-")
    return f"eml/{sent[:4]}/{sent[5:7]}/{stamp}_{digest}.eml"


def attachment_names(eml_path: Path) -> list[str]:
    """Real attachments in the .eml: named parts that aren't inline images,
    and attached emails (named by their subject). An attached email's own
    attachments are left out."""
    try:
        with open(eml_path, "rb") as fh:
            root = email.message_from_binary_file(fh, policy=email.policy.default)
    except Exception:
        return []
    names: list[str] = []

    def visit(part, top: bool):
        ctype = part.get_content_type()
        if ctype == "message/rfc822" and not top:
            inner = part.get_payload()
            inner = inner[0] if isinstance(inner, list) and inner else inner
            subj = (inner.get("subject") if hasattr(inner, "get") else "") or "message"
            names.append(part.get_filename() or f"{subj}.eml")
            return
        if part.is_multipart():
            for sub in part.iter_parts():
                visit(sub, False)
            return
        name = part.get_filename()
        if not name or top:
            return
        if part.get_content_disposition() == "inline" and part.get("Content-ID"):
            return   # a signature logo or pasted image
        names.append(name)

    try:
        visit(root, True)
    except Exception:
        pass
    return names


def build_row(msg: dict, folder: str, mclass: str, eml_rel: str | None,
              eml_bytes: int | None, attachments: list[str]) -> dict:
    to, cc, bcc = (_people(msg.get(k)) for k in
                   ("toRecipients", "ccRecipients", "bccRecipients"))
    recipients = "; ".join(to + cc + bcc)
    own, quoted = split_body((msg.get("body") or {}).get("content") or "")
    sender = ((msg.get("from") or {}).get("emailAddress") or {}).get("address") or ""
    return {
        "msg_key": msg_key(msg),
        "graph_id": msg.get("id"),
        "internet_msg_id": msg.get("internetMessageId"),
        "conversation_id": msg.get("conversationId"),
        "folder": folder,
        "message_class": mclass,
        "sent_utc": norm_utc(msg.get("sentDateTime")),
        "subject": (msg.get("subject") or "").strip(),
        "from_addr": sender.lower(),
        "recipients": recipients,
        "to_list": "; ".join(to),
        "cc_list": "; ".join(cc),
        "bcc_list": "; ".join(bcc),
        "has_attachments": 1 if (attachments or msg.get("hasAttachments")) else 0,
        "attachment_names": "; ".join(attachments),
        "body_own": own,
        "body_quoted": quoted,
        "eml_path": eml_rel,
        "eml_bytes": eml_bytes,
        "archived_utc": now_utc(),
    }


_COLS = ("msg_key", "graph_id", "internet_msg_id", "conversation_id", "folder",
         "message_class", "sent_utc", "subject", "from_addr", "recipients", "to_list",
         "cc_list", "bcc_list", "has_attachments", "attachment_names", "body_own",
         "body_quoted", "eml_path", "eml_bytes", "archived_utc")


def insert(conn: sqlite3.Connection, row: dict) -> bool:
    """True if the row is new. A send already archived (same msg_key) is left alone."""
    cur = conn.execute(
        f"INSERT OR IGNORE INTO messages({', '.join(_COLS)}) "
        f"VALUES ({', '.join('?' for _ in _COLS)})", [row[c] for c in _COLS])
    conn.execute("DELETE FROM failures WHERE graph_id=?", (row["graph_id"],))
    return bool(cur.rowcount)


def upsert(conn: sqlite3.Connection, row: dict) -> tuple[str, str | None]:
    """("new" | "replaced" | "kept", old eml path). A full copy replaces an
    archived Vault shortcut of the same send in place, so its ref stays;
    anything else already archived is left alone. "replaced" comes with the
    shortcut's .eml path when it differs from the new one (the caller deletes
    it)."""
    cur = conn.execute("SELECT id, message_class, eml_path FROM messages WHERE msg_key=?",
                       (row["msg_key"],)).fetchone()
    conn.execute("DELETE FROM failures WHERE graph_id=?", (row["graph_id"],))
    if cur is None:
        insert(conn, row)
        return "new", None
    if is_shortcut(cur[1]) and not is_shortcut(row["message_class"]):
        cols = [c for c in _COLS if c != "msg_key"]
        conn.execute(f"UPDATE messages SET {', '.join(f'{c}=?' for c in cols)} WHERE id=?",
                     [row[c] for c in cols] + [cur[0]])
        return "replaced", (cur[2] if cur[2] and cur[2] != row["eml_path"] else None)
    return "kept", None


def known_keys(conn: sqlite3.Connection, keys: list[str]) -> set[str]:
    return set(known_kinds(conn, keys))


def known_kinds(conn: sqlite3.Connection, keys: list[str]) -> dict[str, bool]:
    """{msg_key: archived as a Vault shortcut?} for the keys already archived."""
    out: dict[str, bool] = {}
    keys = list(dict.fromkeys(keys))
    for i in range(0, len(keys), 500):          # SQLite's bound-parameter limit
        chunk = keys[i:i + 500]
        marks = ", ".join("?" for _ in chunk)
        for key, mclass in conn.execute(
                f"SELECT msg_key, message_class FROM messages WHERE msg_key IN ({marks})",
                chunk):
            out[key] = is_shortcut(mclass)
    return out


def wanted(known: dict[str, bool], key: str, mclass: str) -> bool:
    """Archive this copy? Yes if the send is new, or if it's a full copy of
    a send archived only as a shortcut."""
    return key not in known or (known[key] and not is_shortcut(mclass))


def record_failure(conn: sqlite3.Connection, msg: dict, error: str,
                   folder: str = "Sent Items") -> None:
    conn.execute(
        "INSERT INTO failures(graph_id, sent_utc, subject, error, tries, last_utc, folder) "
        "VALUES (?, ?, ?, ?, 1, ?, ?) ON CONFLICT(graph_id) DO UPDATE SET "
        "error=excluded.error, tries=failures.tries + 1, last_utc=excluded.last_utc",
        (msg.get("id"), norm_utc(msg.get("sentDateTime")), (msg.get("subject") or "")[:300],
         error[:500], now_utc(), folder))


def stats(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT COUNT(*), MIN(sent_utc), MAX(sent_utc), COALESCE(SUM(eml_bytes), 0), "
        f"SUM(has_attachments), SUM({SHORTCUT_SQL}), SUM(folder != 'Sent Items') "
        "FROM messages").fetchone()
    fails = conn.execute("SELECT COUNT(*) FROM failures").fetchone()[0]
    return {"messages": row[0], "oldest": row[1], "newest": row[2], "eml_bytes": row[3],
            "with_attachments": row[4] or 0, "shortcuts": row[5] or 0,
            "filed": row[6] or 0, "failures": fails}
