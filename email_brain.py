"""
Email Brain — sent-mail corpus + retrieval index.

Pulls ALL of James's sent email from the folders listed in config
(`sent_brain_folders`), reconstructs the (incoming email -> James's reply) pair
behind each sent message via Graph `conversationId`, extracts body + attachment
text, and stores everything in a local single-file SQLite database (`brain.db`,
with an FTS5 full-text index) plus a raw JSONL export. Each pair's retrieval-key
text (the inbound message when present, else the reply itself) is embedded with
Voyage AI so future incoming mail can be matched against past exchanges.

This module is the data/index layer ("the brain's memory"). Wiring retrieval
into live draft generation is a separate, later phase.

Design mirrors pma_tracker.py: self-contained, takes app_token/config/data_dir
as arguments, reuses a handful of pure helpers from rocky.py via lazy import
(rocky imports this module only at CLI dispatch time, so the reverse import is
safe at call time).

Invoked from rocky.py via `--email-brain`. No new Graph permission needed —
reads jbragdon@'s mailbox with the same app-level token + Application Access
Policy used by --maple-pma-activity.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import struct
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

log = logging.getLogger("rocky.email_brain")

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"
DEFAULT_VOYAGE_MODEL = "voyage-3-large"

SCHEMA_VERSION = 1

# Caps.
BODY_TEXT_CAP = 50_000          # chars stored per message body
EMBED_TEXT_CAP = 24_000         # chars sent to Voyage per input (well under 32k-token ctx)
SEEN_IDS_CAP = 2_000            # boundary-dedup cushion per folder (DB upsert is the real guard)
EMBED_BATCH = 64                # inputs per Voyage request
GRAPH_PAGE_SIZE = 50

# Thread-boundary markers — where James's reply ends and the quoted prior
# message(s) begin. Used both to trim his top-post and to recover the inbound
# message from the quoted body when the real inbound isn't available via Graph.
_THREAD_MARKERS = [
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^_{10,}\s*$", re.MULTILINE),                        # Outlook divider line
    re.compile(r"^\s*From:\s*.+$", re.IGNORECASE | re.MULTILINE),     # quoted Outlook header
    re.compile(r"^On .{3,120}? wrote:\s*$", re.IGNORECASE | re.MULTILINE),  # Gmail/Apple style
]


# =============================================================================
# Small pure helpers
# =============================================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _addr(obj: dict | None) -> tuple[str, str]:
    """Return (address, name) from a Graph recipient/from object."""
    ea = ((obj or {}).get("emailAddress") or {})
    return (ea.get("address") or "").strip(), (ea.get("name") or "").strip()


def _recip_addrs(lst: list | None) -> list[str]:
    out = []
    for x in (lst or []):
        a, _ = _addr(x)
        if a:
            out.append(a)
    return out


def _body_text(msg: dict) -> str:
    """Plain-text body (Prefer: text header was sent), falling back to preview."""
    body = (msg.get("body") or {}).get("content")
    if not body:
        body = msg.get("bodyPreview") or ""
    return body.strip()


def _earliest_marker(text: str, start: int = 0) -> int | None:
    """Index of the earliest thread-boundary marker at/after `start`, or None."""
    earliest = None
    for pat in _THREAD_MARKERS:
        m = pat.search(text, start)
        if m and (earliest is None or m.start() < earliest):
            earliest = m.start()
    return earliest


def _strip_quoted(text: str) -> str:
    """Cut a reply at the earliest quoted-history marker, keeping the top-post."""
    if not text:
        return ""
    idx = _earliest_marker(text)
    return (text[:idx] if idx is not None else text).strip()


def _parse_addr_line(s: str) -> tuple[str, str]:
    """From a 'Name <email>' / 'email' fragment, return (name, address)."""
    if not s:
        return "", ""
    em = re.search(r"[\w.+\-']+@[\w\-]+\.[\w.\-]+", s)
    addr = em.group(0).rstrip(".") if em else ""
    name = re.sub(r"<[^>]*>", "", s)
    name = name.replace("mailto:", "").replace(addr, "").strip().strip('"').strip("[]").strip()
    return name, addr


def extract_quoted_inbound(body: str) -> dict | None:
    """Recover the message a sent reply answered FROM the sent body's quoted
    history. Used as a fallback when the real inbound isn't available via Graph
    (e.g. archived sends whose inbound counterpart isn't in the mailbox).

    Returns {from_addr, from_name, subject, sent, body_text} for the *immediate*
    prior message (the first quoted block), or None if nothing parseable.
    """
    if not body:
        return None
    idx = _earliest_marker(body)
    if idx is None:
        return None
    quoted = body[idx:]
    # Drop a leading divider / "Original Message" line so the From block (if any)
    # is at the front.
    quoted = re.sub(r"^-{2,}\s*Original Message\s*-{2,}\s*", "", quoted,
                    flags=re.IGNORECASE)
    quoted = re.sub(r"^_{10,}\s*", "", quoted).lstrip("\r\n ")

    from_name = from_addr = subject = sent = ""
    header_end = 0

    on_wrote = re.match(r"On\s+(.{3,200}?)\s+wrote:\s*", quoted, re.IGNORECASE | re.DOTALL)
    if re.match(r"\s*From:\s*", quoted, re.IGNORECASE):
        fm = re.match(r"\s*From:\s*(.+)", quoted, re.IGNORECASE)
        if fm:
            from_name, from_addr = _parse_addr_line(fm.group(1).strip())
        sm = re.search(r"^\s*Subject:\s*(.+)$", quoted, re.IGNORECASE | re.MULTILINE)
        if sm:
            subject = sm.group(1).strip()
        dm = re.search(r"^\s*(?:Sent|Date):\s*(.+)$", quoted, re.IGNORECASE | re.MULTILINE)
        if dm:
            sent = dm.group(1).strip()
        # Body starts after the contiguous header lines (From/Sent/To/Cc/Subject/…).
        hdr = re.match(
            r"(?:\s*(?:From|Sent|Date|To|Cc|Bcc|Subject|Importance|Reply-To):.*(?:\r?\n|$))+",
            quoted, re.IGNORECASE,
        )
        header_end = hdr.end() if hdr else 0
    elif on_wrote:
        from_name, from_addr = _parse_addr_line(on_wrote.group(1))
        header_end = on_wrote.end()
    # else: divider with no recognizable header — treat whole block as body.

    rest = quoted[header_end:]
    # Cut at the next nested thread marker (older message in the chain).
    nidx = _earliest_marker(rest)
    inbound_body = (rest[:nidx] if nidx is not None else rest)
    # Strip leading ">" quote characters common in Gmail/Apple style.
    inbound_body = re.sub(r"(?m)^\s*>\s?", "", inbound_body).strip()

    if not inbound_body and not subject and not from_addr:
        return None
    return {
        "from_addr": from_addr,
        "from_name": from_name,
        "subject": subject,
        "sent": sent,
        "body_text": inbound_body,
    }


def _cap(text: str, limit: int) -> str:
    if text and len(text) > limit:
        return text[:limit]
    return text


def _parse_dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


# =============================================================================
# State / cursor (mirrors pma_tracker.load_pma_state / save_pma_state)
# =============================================================================

def _state_path(data_dir: Path) -> Path:
    return data_dir / "state" / "email_brain_state.json"


def load_state(data_dir: Path) -> dict:
    p = _state_path(data_dir)
    if not p.exists():
        return {"folders": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        data.setdefault("folders", {})
        return data
    except (json.JSONDecodeError, OSError):
        return {"folders": {}}


def save_state(data_dir: Path, state: dict) -> None:
    p = _state_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


# =============================================================================
# Microsoft Graph: fetching mail
# =============================================================================

def fetch_all_folder_messages(token: str, mailbox: str, folder_id: str,
                              since: datetime | None) -> list[dict]:
    """Fetch ALL messages in a folder (full @odata.nextLink pagination).

    `since` filters to sentDateTime gt since (incremental). None = whole folder.
    Mirrors the pagination loop in pma_tracker.fetch_folder_messages.
    """
    url = f"{GRAPH_API_BASE}/users/{mailbox}/mailFolders/{folder_id}/messages"
    params: dict[str, str] = {
        "$orderby": "sentDateTime asc",
        "$top": str(GRAPH_PAGE_SIZE),
        "$select": (
            "id,internetMessageId,conversationId,subject,from,toRecipients,"
            "ccRecipients,sentDateTime,receivedDateTime,bodyPreview,body,hasAttachments"
        ),
    }
    if since is not None:
        params["$filter"] = f"sentDateTime gt {since.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Prefer": 'outlook.body-content-type="text"',
    }

    messages: list[dict] = []
    next_url: str | None = None
    page = 0
    while page == 0 or next_url:
        resp = _graph_get(next_url or url, headers, None if next_url else params)
        if resp is None:
            break
        if resp.status_code != 200:
            log.error(f"[email-brain] Graph {resp.status_code} fetching folder "
                      f"{folder_id}: {resp.text[:300]}")
            break
        data = resp.json()
        messages.extend(data.get("value", []))
        next_url = data.get("@odata.nextLink")
        page += 1
    return messages


def find_inbound_parent(token: str, read_mailbox: str, self_addrs: set[str],
                        conversation_id: str | None,
                        reply_sent_at: datetime | None, cache: dict) -> dict | None:
    """Find the inbound message a sent reply answered.

    Queries `read_mailbox` for the thread by conversationId (cached per thread),
    then returns the latest message from someone NOT in `self_addrs` (the author
    plus the host mailbox) received before the reply. Splitting the host mailbox
    from the author lets us read James's sent mail out of rocky@ while still
    treating jbragdon@ as "self" for pairing.
    """
    if not conversation_id:
        return None

    thread = cache.get(conversation_id)
    if thread is None:
        thread = _fetch_conversation(token, read_mailbox, conversation_id)
        cache[conversation_id] = thread

    selves = {s.lower() for s in self_addrs if s}
    best = None
    best_dt = None
    for m in thread:
        from_addr, _ = _addr(m.get("from"))
        if not from_addr or from_addr.lower() in selves:
            continue  # skip the author's / host mailbox's own messages
        recv = _parse_dt(m.get("receivedDateTime"))
        if reply_sent_at is not None and recv is not None and recv >= reply_sent_at:
            continue  # only messages that predate this reply
        if best_dt is None or (recv is not None and recv > best_dt):
            best, best_dt = m, recv
    return best


def _fetch_conversation(token: str, mailbox: str, conversation_id: str) -> list[dict]:
    """All messages across the mailbox sharing a conversationId."""
    cid = conversation_id.replace("'", "''")  # OData single-quote escaping
    url = f"{GRAPH_API_BASE}/users/{mailbox}/messages"
    params = {
        "$filter": f"conversationId eq '{cid}'",
        "$top": str(GRAPH_PAGE_SIZE),
        "$select": "id,conversationId,subject,from,receivedDateTime,sentDateTime,body,bodyPreview",
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Prefer": 'outlook.body-content-type="text"',
    }
    out: list[dict] = []
    next_url: str | None = None
    page = 0
    while page == 0 or next_url:
        resp = _graph_get(next_url or url, headers, None if next_url else params)
        if resp is None or resp.status_code != 200:
            if resp is not None and resp.status_code != 200:
                log.debug(f"[email-brain] conversation fetch {resp.status_code}: {resp.text[:200]}")
            break
        data = resp.json()
        out.extend(data.get("value", []))
        next_url = data.get("@odata.nextLink")
        page += 1
    return out


def _graph_get(url: str, headers: dict, params: dict | None, max_retries: int = 5):
    """GET with throttling/backoff so the full backfill runs patiently."""
    delay = 2.0
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=60)
        except requests.RequestException as e:
            log.warning(f"[email-brain] network error ({attempt+1}/{max_retries}): {e}")
            time.sleep(delay)
            delay = min(delay * 2, 60)
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after else delay
            log.info(f"[email-brain] throttled ({resp.status_code}); sleeping {wait:.0f}s")
            time.sleep(wait)
            delay = min(delay * 2, 60)
            continue
        return resp
    return None


# =============================================================================
# Record assembly
# =============================================================================

def build_record(token: str, mailbox: str, msg: dict, folder_label: str,
                 inbound: dict | None) -> dict:
    """Assemble a sent-message record (and its paired inbound, if any)."""
    from rocky import fetch_attachments, build_attachment_text_block  # lazy reuse

    from_addr, from_name = _addr(msg.get("from"))
    body = _cap(_body_text(msg), BODY_TEXT_CAP)

    attachment_text = ""
    if msg.get("hasAttachments"):
        try:
            atts = fetch_attachments(token, mailbox, msg["id"])
            attachment_text = build_attachment_text_block(atts)
        except Exception as e:
            log.debug(f"[email-brain] attachment extract failed for {msg.get('id')}: {e}")

    inbound_rec = None
    if inbound is not None:
        # Real inbound message found via Graph conversationId — preferred.
        in_addr, in_name = _addr(inbound.get("from"))
        inbound_rec = {
            "graph_id": inbound.get("id"),
            "conversation_id": inbound.get("conversationId"),
            "from_addr": in_addr,
            "from_name": in_name,
            "subject": inbound.get("subject") or "",
            "received_at": inbound.get("receivedDateTime"),
            "body_text": _cap(_strip_quoted(_body_text(inbound)), BODY_TEXT_CAP),
            "source": "graph",
        }
    else:
        # Fallback: recover the inbound message from the sent body's quoted
        # history. Critical for archived sends whose real inbound isn't in the
        # mailbox (otherwise they'd be style-only).
        q = extract_quoted_inbound(_body_text(msg))
        if q:
            inbound_rec = {
                "graph_id": None,
                "conversation_id": msg.get("conversationId"),
                "from_addr": q["from_addr"],
                "from_name": q["from_name"],
                "subject": q["subject"] or (msg.get("subject") or ""),
                "received_at": None,
                "body_text": _cap(q["body_text"], BODY_TEXT_CAP),
                "source": "quoted",
            }

    return {
        "graph_id": msg.get("id"),
        "internet_message_id": msg.get("internetMessageId"),
        "conversation_id": msg.get("conversationId"),
        "folder": folder_label,
        "direction": "sent",
        "sent_at": msg.get("sentDateTime"),
        "received_at": msg.get("receivedDateTime"),
        "from_name": from_name,
        "from_addr": from_addr,
        "to": _recip_addrs(msg.get("toRecipients")),
        "cc": _recip_addrs(msg.get("ccRecipients")),
        "subject": msg.get("subject") or "",
        "body_text": body,
        "reply_top_post": _cap(_strip_quoted(body), BODY_TEXT_CAP),
        "attachment_text": attachment_text,
        "has_attachments": bool(msg.get("hasAttachments")),
        "inbound": inbound_rec,
    }


# =============================================================================
# SQLite layer
# =============================================================================

def open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS messages (
            graph_id            TEXT PRIMARY KEY,
            internet_message_id TEXT,
            conversation_id     TEXT,
            folder              TEXT,
            direction           TEXT,
            sent_at             TEXT,
            received_at         TEXT,
            from_name           TEXT,
            from_addr           TEXT,
            to_addrs            TEXT,
            cc_addrs            TEXT,
            subject             TEXT,
            body_text           TEXT,
            attachment_text     TEXT,
            has_attachments     INTEGER,
            ingested_at         TEXT
        );
        CREATE TABLE IF NOT EXISTS pairs (
            pair_id          TEXT PRIMARY KEY,
            conversation_id  TEXT,
            reply_graph_id   TEXT,
            inbound_graph_id TEXT,
            inbound_from     TEXT,
            inbound_subject  TEXT,
            inbound_text     TEXT,
            reply_text       TEXT,
            reply_sent_at    TEXT,
            embed_source     TEXT,
            embedding        BLOB,
            embedding_model  TEXT,
            embedded_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
        CREATE INDEX IF NOT EXISTS idx_pairs_conv ON pairs(conversation_id);
        CREATE INDEX IF NOT EXISTS idx_pairs_embed ON pairs(embedding_model);
        """
    )
    cur.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    # FTS5 is optional — degrade gracefully if the sqlite build lacks it.
    try:
        cur.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                graph_id UNINDEXED, subject, body_text
            );
            """
        )
        cur.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('fts5', 'enabled')")
    except sqlite3.OperationalError:
        log.warning("[email-brain] FTS5 unavailable in this SQLite build; "
                    "keyword search will use LIKE fallback.")
        cur.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('fts5', 'disabled')")
    conn.commit()


def _fts_enabled(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM meta WHERE key='fts5'").fetchone()
    return bool(row and row[0] == "enabled")


def upsert_message(conn: sqlite3.Connection, rec: dict, direction: str) -> None:
    """Insert/replace a message row. `rec` is either a full sent record or an
    inbound sub-record; `direction` distinguishes them."""
    if direction == "sent":
        row = (
            rec["graph_id"], rec.get("internet_message_id"), rec.get("conversation_id"),
            rec.get("folder"), "sent", rec.get("sent_at"), rec.get("received_at"),
            rec.get("from_name"), rec.get("from_addr"),
            json.dumps(rec.get("to") or []), json.dumps(rec.get("cc") or []),
            rec.get("subject"), rec.get("body_text"), rec.get("attachment_text"),
            1 if rec.get("has_attachments") else 0, _now_iso(),
        )
    else:  # received (inbound sub-record)
        row = (
            rec["graph_id"], None, rec.get("conversation_id"),
            None, "received", None, rec.get("received_at"),
            rec.get("from_name"), rec.get("from_addr"),
            "[]", "[]", rec.get("subject"), rec.get("body_text"), "",
            0, _now_iso(),
        )
    conn.execute(
        """INSERT OR REPLACE INTO messages
           (graph_id, internet_message_id, conversation_id, folder, direction,
            sent_at, received_at, from_name, from_addr, to_addrs, cc_addrs,
            subject, body_text, attachment_text, has_attachments, ingested_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        row,
    )
    if direction == "sent" and _fts_enabled(conn):
        conn.execute("DELETE FROM messages_fts WHERE graph_id=?", (rec["graph_id"],))
        conn.execute(
            "INSERT INTO messages_fts(graph_id, subject, body_text) VALUES (?,?,?)",
            (rec["graph_id"], rec.get("subject") or "", rec.get("body_text") or ""),
        )


def upsert_pair(conn: sqlite3.Connection, rec: dict) -> None:
    inbound = rec.get("inbound")
    if inbound:
        embed_source = "inbound_quoted" if inbound.get("source") == "quoted" else "inbound"
        inbound_from = inbound.get("from_addr")
        inbound_subject = inbound.get("subject")
        inbound_text = inbound.get("body_text")
        inbound_gid = inbound.get("graph_id")
    else:
        embed_source = "reply"
        inbound_from = inbound_subject = inbound_text = inbound_gid = None

    conn.execute(
        """INSERT OR REPLACE INTO pairs
           (pair_id, conversation_id, reply_graph_id, inbound_graph_id,
            inbound_from, inbound_subject, inbound_text, reply_text,
            reply_sent_at, embed_source, embedding, embedding_model, embedded_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,
                   (SELECT embedding FROM pairs WHERE pair_id=?),
                   (SELECT embedding_model FROM pairs WHERE pair_id=?),
                   (SELECT embedded_at FROM pairs WHERE pair_id=?))""",
        (
            rec["graph_id"], rec.get("conversation_id"), rec["graph_id"], inbound_gid,
            inbound_from, inbound_subject, inbound_text, rec.get("reply_top_post"),
            rec.get("sent_at"), embed_source,
            rec["graph_id"], rec["graph_id"], rec["graph_id"],
        ),
    )


def _embed_key_text(conn: sqlite3.Connection, pair_row: sqlite3.Row) -> str:
    """The text we embed for a pair: the inbound subject+body if we have it
    (whether from Graph or recovered from the quoted body), else the reply's
    subject+top-post (style-only)."""
    if pair_row["inbound_text"]:
        subj = pair_row["inbound_subject"] or ""
        return _cap(f"{subj}\n\n{pair_row['inbound_text']}".strip(), EMBED_TEXT_CAP)
    # style-only: embed the reply itself
    reply = pair_row["reply_text"] or ""
    msg = conn.execute(
        "SELECT subject FROM messages WHERE graph_id=?", (pair_row["reply_graph_id"],)
    ).fetchone()
    subj = (msg["subject"] if msg else "") or ""
    return _cap(f"{subj}\n\n{reply}".strip(), EMBED_TEXT_CAP)


# =============================================================================
# Voyage embeddings
# =============================================================================

def embed_texts(texts: list[str], api_key: str, model: str,
                input_type: str = "document") -> list[list[float]]:
    """Embed a batch of texts via the Voyage REST API. Returns one vector per
    input, in order. Batches of <= EMBED_BATCH."""
    out: list[list[float]] = []
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    for i in range(0, len(texts), EMBED_BATCH):
        chunk = [t if t else " " for t in texts[i:i + EMBED_BATCH]]
        payload = {"input": chunk, "model": model, "input_type": input_type}
        delay = 3.0
        for attempt in range(6):
            try:
                resp = requests.post(VOYAGE_API_URL, headers=headers,
                                     json=payload, timeout=120)
            except requests.RequestException as e:
                log.warning(f"[email-brain] Voyage network error: {e}")
                time.sleep(delay); delay = min(delay * 2, 60); continue
            if resp.status_code == 429 or resp.status_code >= 500:
                log.info(f"[email-brain] Voyage throttled ({resp.status_code}); "
                         f"sleeping {delay:.0f}s")
                time.sleep(delay); delay = min(delay * 2, 60); continue
            if resp.status_code != 200:
                raise RuntimeError(f"Voyage error {resp.status_code}: {resp.text[:300]}")
            data = resp.json()["data"]
            data.sort(key=lambda d: d["index"])
            out.extend(d["embedding"] for d in data)
            break
        else:
            raise RuntimeError("Voyage embedding failed after retries")
    return out


def _vec_to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _blob_to_vec(blob: bytes):
    import numpy as np
    return np.frombuffer(blob, dtype="<f4")


def embed_pending_pairs(conn: sqlite3.Connection, config: dict) -> int:
    """Embed every pair whose embedding is still NULL. Returns count embedded."""
    api_key = config.get("voyage_api_key")
    if not api_key or api_key.startswith("PASTE"):
        log.warning("[email-brain] no voyage_api_key configured; skipping embeddings.")
        return 0
    model = config.get("voyage_model") or DEFAULT_VOYAGE_MODEL

    rows = conn.execute(
        "SELECT * FROM pairs WHERE embedding IS NULL"
    ).fetchall()
    if not rows:
        log.info("[email-brain] no pairs pending embedding.")
        return 0

    log.info(f"[email-brain] embedding {len(rows)} pair(s) via Voyage ({model})...")
    embedded = 0
    for i in range(0, len(rows), EMBED_BATCH):
        batch = rows[i:i + EMBED_BATCH]
        texts = [_embed_key_text(conn, r) for r in batch]
        vecs = embed_texts(texts, api_key, model, input_type="document")
        now = _now_iso()
        for r, v in zip(batch, vecs):
            conn.execute(
                "UPDATE pairs SET embedding=?, embedding_model=?, embedded_at=? "
                "WHERE pair_id=?",
                (_vec_to_blob(v), model, now, r["pair_id"]),
            )
        conn.commit()
        embedded += len(batch)
        # Record dim once.
        if vecs:
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('embedding_dim', ?)",
                (str(len(vecs[0])),),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('embedding_model', ?)",
                (model,),
            )
        conn.commit()
        log.info(f"[email-brain]   embedded {embedded}/{len(rows)}")
    return embedded


# =============================================================================
# Search (smoke test / future retrieval entry points)
# =============================================================================

def vector_search(conn: sqlite3.Connection, config: dict, query: str, k: int = 5) -> list[dict]:
    import numpy as np

    api_key = config.get("voyage_api_key")
    model = config.get("voyage_model") or DEFAULT_VOYAGE_MODEL
    if not api_key or api_key.startswith("PASTE"):
        raise RuntimeError("voyage_api_key required for vector search")

    qvec = np.asarray(
        embed_texts([query], api_key, model, input_type="query")[0], dtype="<f4"
    )
    qn = qvec / (np.linalg.norm(qvec) or 1.0)

    rows = conn.execute(
        "SELECT pair_id, conversation_id, reply_graph_id, inbound_subject, "
        "inbound_from, inbound_text, reply_text, embed_source, embedding "
        "FROM pairs WHERE embedding IS NOT NULL"
    ).fetchall()
    if not rows:
        return []

    mat = np.vstack([_blob_to_vec(r["embedding"]) for r in rows])
    norms = np.linalg.norm(mat, axis=1)
    norms[norms == 0] = 1.0
    sims = (mat @ qn) / norms
    top = np.argsort(-sims)[:k]
    results = []
    for idx in top:
        r = rows[int(idx)]
        results.append({
            "score": float(sims[int(idx)]),
            "embed_source": r["embed_source"],
            "inbound_from": r["inbound_from"],
            "inbound_subject": r["inbound_subject"],
            "inbound_text": (r["inbound_text"] or "")[:600],
            "reply_text": (r["reply_text"] or "")[:1200],
        })
    return results


def keyword_search(conn: sqlite3.Connection, query: str, k: int = 5) -> list[dict]:
    if _fts_enabled(conn):
        rows = conn.execute(
            "SELECT m.subject, m.from_addr, m.body_text FROM messages_fts f "
            "JOIN messages m ON m.graph_id=f.graph_id "
            "WHERE messages_fts MATCH ? ORDER BY rank LIMIT ?",
            (query, k),
        ).fetchall()
    else:
        like = f"%{query}%"
        rows = conn.execute(
            "SELECT subject, from_addr, body_text FROM messages "
            "WHERE subject LIKE ? OR body_text LIKE ? LIMIT ?",
            (like, like, k),
        ).fetchall()
    return [{"subject": r["subject"], "from_addr": r["from_addr"],
             "body_text": (r["body_text"] or "")[:600]} for r in rows]


# =============================================================================
# Orchestrator
# =============================================================================

def run_email_brain(app_token: str, config: dict, data_dir: Path, *,
                    resolved_folders: list[tuple[str, str, str]],
                    rebuild: bool = False, backfill_days: int | None = None,
                    no_embed: bool = False, limit: int | None = None) -> dict:
    """Pull sent mail from each resolved folder, pair + store, then embed.

    `resolved_folders` is a list of (label, mailbox, folder_id) tuples — rocky.py
    resolves each path in its own mailbox and skips unresolvable ones. The host
    mailbox may differ per folder (e.g. current Sent Items in jbragdon@, archived
    sends dragged into rocky@), but `sent_brain_author` is the single identity
    treated as "James" for reply→inbound pairing."""
    author = config.get("sent_brain_author") or config.get("user_email") \
        or "jbragdon@gallagherllp.com"

    brain_dir = Path(config["email_brain_dir"]) if config.get("email_brain_dir") \
        else (data_dir / "email_brain")
    brain_dir.mkdir(parents=True, exist_ok=True)
    db_path = brain_dir / "brain.db"
    jsonl_path = brain_dir / "sent_emails.jsonl"

    if rebuild:
        for p in (db_path, jsonl_path, _state_path(data_dir)):
            if p.exists():
                p.unlink()
        log.info("[email-brain] --rebuild: cleared db, jsonl, and cursor state.")

    state = load_state(data_dir)
    conn = open_db(db_path)

    processed = 0
    paired = 0          # real inbound found via Graph
    paired_quoted = 0   # inbound recovered from the sent body's quoted history
    conv_cache: dict = {}

    for label, mbox, folder_id in resolved_folders:
        fstate = state["folders"].setdefault(folder_id, {"last_sent": None, "seen_ids": []})

        # Determine the incremental cursor.
        since: datetime | None = None
        if fstate.get("last_sent"):
            since = _parse_dt(fstate["last_sent"])
        elif backfill_days is not None:
            since = datetime.now(timezone.utc) - timedelta(days=backfill_days)
        # else: None -> full backfill of the entire folder.

        log.info(f"[email-brain] folder {mbox}:{label!r}: fetching "
                 + ("ALL" if since is None else f"since {since.isoformat()}"))
        messages = fetch_all_folder_messages(app_token, mbox, folder_id, since)
        log.info(f"[email-brain] folder {mbox}:{label!r}: {len(messages)} message(s) returned")

        seen = set(fstate.get("seen_ids", []))
        max_sent = _parse_dt(fstate.get("last_sent"))

        for msg in messages:
            if limit is not None and processed >= limit:
                break
            gid = msg.get("id")
            if not gid or gid in seen:
                continue

            reply_sent_at = _parse_dt(msg.get("sentDateTime"))
            inbound = find_inbound_parent(
                app_token, mbox, {author, mbox}, msg.get("conversationId"),
                reply_sent_at, conv_cache
            )
            rec = build_record(app_token, mbox, msg, label, inbound)

            upsert_message(conn, rec, "sent")
            inbound_rec = rec.get("inbound")
            if inbound_rec:
                if inbound_rec.get("graph_id"):
                    upsert_message(conn, inbound_rec, "received")
                    paired += 1
                else:
                    paired_quoted += 1  # quoted-only: no real message row, just the pair
            upsert_pair(conn, rec)

            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

            seen.add(gid)
            if reply_sent_at is not None and (max_sent is None or reply_sent_at > max_sent):
                max_sent = reply_sent_at
            processed += 1
            if processed % 100 == 0:
                conn.commit()
                log.info(f"[email-brain]   processed {processed}...")

        conn.commit()
        # Persist cursor (advance only on real progress).
        if max_sent is not None:
            fstate["last_sent"] = max_sent.strftime("%Y-%m-%dT%H:%M:%SZ")
        fstate["seen_ids"] = list(seen)[-SEEN_IDS_CAP:]
        save_state(data_dir, state)

        if limit is not None and processed >= limit:
            log.info(f"[email-brain] hit --limit {limit}; stopping.")
            break

    embedded = 0
    if not no_embed:
        try:
            embedded = embed_pending_pairs(conn, config)
        except Exception as e:
            log.error(f"[email-brain] embedding phase failed: {e}")

    # Summary stats.
    msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    pair_count = conn.execute("SELECT COUNT(*) FROM pairs").fetchone()[0]
    paired_count = conn.execute(
        "SELECT COUNT(*) FROM pairs WHERE inbound_graph_id IS NOT NULL"
    ).fetchone()[0]
    conn.close()

    result = {
        "processed_this_run": processed,
        "paired_via_graph_this_run": paired,
        "paired_via_quoted_this_run": paired_quoted,
        "embedded_this_run": embedded,
        "total_messages": msg_count,
        "total_pairs": pair_count,
        "total_pairs_with_graph_inbound": paired_count,
        "db_path": str(db_path),
        "jsonl_path": str(jsonl_path),
    }
    return result


def stats(config: dict, data_dir: Path) -> dict:
    brain_dir = Path(config["email_brain_dir"]) if config.get("email_brain_dir") \
        else (data_dir / "email_brain")
    db_path = brain_dir / "brain.db"
    if not db_path.exists():
        return {"error": f"no database at {db_path}"}
    conn = open_db(db_path)
    out = {
        "db_path": str(db_path),
        "messages": conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
        "sent": conn.execute("SELECT COUNT(*) FROM messages WHERE direction='sent'").fetchone()[0],
        "received": conn.execute("SELECT COUNT(*) FROM messages WHERE direction='received'").fetchone()[0],
        "pairs": conn.execute("SELECT COUNT(*) FROM pairs").fetchone()[0],
        "pairs_graph_inbound": conn.execute(
            "SELECT COUNT(*) FROM pairs WHERE embed_source='inbound'").fetchone()[0],
        "pairs_quoted_inbound": conn.execute(
            "SELECT COUNT(*) FROM pairs WHERE embed_source='inbound_quoted'").fetchone()[0],
        "pairs_style_only": conn.execute(
            "SELECT COUNT(*) FROM pairs WHERE embed_source='reply'").fetchone()[0],
        "embedded": conn.execute(
            "SELECT COUNT(*) FROM pairs WHERE embedding IS NOT NULL").fetchone()[0],
    }
    dim = conn.execute("SELECT value FROM meta WHERE key='embedding_dim'").fetchone()
    out["embedding_dim"] = dim[0] if dim else None
    conn.close()
    return out
