"""te.db: Rocky's local Time Entries database.

Lives at C:\\Rocky\\time_entries_data\\te.db (never on OneDrive). Phase 1
holds the timecard history and the learning set; later phases add the
recommendation and outcome tables.

  snapshots   one row per timecard export Rocky has read
  entries     every timecard row, flagged superseded when a newer or wider
              export covers it (same rules as the folder's build_corpus.py)
  narratives  James's submitted narratives and accepted drafts, the
              training set the name index is built from
  anomalies   carried over from corpus.db
  runs        one row per correlate / backtest run, with its summary

The one-time migration copies entries, narratives and anomalies out of the
folder's corpus.db. corpus.db's email table is NOT copied: Minotaur's brain
holds the same sends and more.
"""

import collections
import json
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS snapshots (
  id          INTEGER PRIMARY KEY,
  received_at TEXT,
  source      TEXT,                -- file name, or mail:<subject>
  sender      TEXT,
  sha256      TEXT UNIQUE,
  period_from TEXT,
  period_to   TEXT,
  n_rows      INTEGER,
  hours       REAL,
  note        TEXT
);

CREATE TABLE IF NOT EXISTS entries (
  id            INTEGER PRIMARY KEY,
  snapshot_id   INTEGER REFERENCES snapshots(id),   -- NULL = migrated from corpus.db
  src_file      TEXT,
  row_no        INTEGER,
  work_date     TEXT,
  matter        TEXT,
  matter_name   TEXT,
  client        TEXT,
  hours         REAL,
  narrative     TEXT,
  status        TEXT,
  non_billable  INTEGER DEFAULT 0,
  no_charge     INTEGER DEFAULT 0,
  timekeeper_id TEXT,
  period        TEXT,
  superseded    INTEGER DEFAULT 0,
  UNIQUE(src_file, row_no)
);
CREATE INDEX IF NOT EXISTS ix_te_entries_date ON entries(work_date);
CREATE INDEX IF NOT EXISTS ix_te_entries_matter ON entries(matter);

CREATE TABLE IF NOT EXISTS narratives (
  id        INTEGER PRIMARY KEY,
  work_date TEXT,
  matter    TEXT,
  narrative TEXT,
  origin    TEXT,          -- 'submitted' | 'draft-accepted'
  src_file  TEXT,
  UNIQUE(work_date, matter, narrative, src_file)
);
CREATE INDEX IF NOT EXISTS ix_te_narr_matter ON narratives(matter);

CREATE TABLE IF NOT EXISTS anomalies (
  id        INTEGER PRIMARY KEY,
  detected  TEXT,
  period    TEXT,
  matter    TEXT,
  kind      TEXT,
  token     TEXT,
  n_entries INTEGER,
  hours     REAL,
  note      TEXT,
  UNIQUE(period, matter, kind, token)
);

CREATE TABLE IF NOT EXISTS runs (
  id         INTEGER PRIMARY KEY,
  kind       TEXT,           -- 'correlate' | 'backtest'
  started_at TEXT,
  params     TEXT,           -- JSON
  summary    TEXT            -- JSON
);
"""


def connect(path: Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cx = sqlite3.connect(path)
    cx.row_factory = sqlite3.Row
    cx.executescript(SCHEMA)
    return cx


def _meta_get(cx, key):
    row = cx.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _meta_set(cx, key, value):
    cx.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) "
               "DO UPDATE SET value=excluded.value", (key, value))


def migrate_from_corpus(cx: sqlite3.Connection, corpus: Path,
                        force: bool = False) -> dict:
    """Copy entries, narratives and anomalies out of corpus.db. Safe to call
    twice: a second call does nothing unless force=True, which replaces the
    migrated rows (and only those)."""
    done = _meta_get(cx, "migrated_from_corpus")
    if done and not force:
        return {"skipped": True, "migrated_at": done}
    src = sqlite3.connect(f"file:{Path(corpus).as_posix()}?mode=ro&immutable=1",
                          uri=True)
    src.row_factory = sqlite3.Row
    try:
        with cx:
            cx.execute("DELETE FROM entries WHERE snapshot_id IS NULL")
            cx.execute("DELETE FROM narratives WHERE src_file NOT LIKE 'mail:%'")
            cx.execute("DELETE FROM anomalies")
            row_no = collections.Counter()
            n_e = 0
            for r in src.execute("SELECT * FROM entries ORDER BY id"):
                row_no[r["src_file"]] += 1
                cx.execute(
                    "INSERT INTO entries (snapshot_id, src_file, row_no, work_date, "
                    "matter, matter_name, client, hours, narrative, status, period, "
                    "superseded) VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["src_file"], row_no[r["src_file"]], r["work_date"],
                     r["matter"], r["matter_name"], r["client"], r["hours"],
                     r["narrative"], r["status"], r["period"], r["superseded"]))
                n_e += 1
            n_n = 0
            for r in src.execute("SELECT * FROM narratives ORDER BY id"):
                cur = cx.execute(
                    "INSERT OR IGNORE INTO narratives (work_date, matter, narrative, "
                    "origin, src_file) VALUES (?,?,?,?,?)",
                    (r["work_date"], r["matter"], r["narrative"], r["origin"],
                     r["src_file"]))
                n_n += cur.rowcount
            n_a = 0
            for r in src.execute("SELECT * FROM anomalies ORDER BY id"):
                cur = cx.execute(
                    "INSERT OR IGNORE INTO anomalies (detected, period, matter, kind, "
                    "token, n_entries, hours, note) VALUES (?,?,?,?,?,?,?,?)",
                    (r["detected"], r["period"], r["matter"], r["kind"], r["token"],
                     r["n_entries"], r["hours"], r["note"]))
                n_a += cur.rowcount
            stamp = datetime.now().isoformat(timespec="seconds")
            _meta_set(cx, "migrated_from_corpus", stamp)
            _meta_set(cx, "migrated_from_path", str(corpus))
    finally:
        src.close()
    return {"skipped": False, "entries": n_e, "narratives": n_n,
            "anomalies": n_a, "migrated_at": stamp}


def ingest_timecard(cx: sqlite3.Connection, timecard: dict, sha256: str,
                    source: str, sender: str | None = None,
                    received_at: str | None = None) -> tuple[int, bool]:
    """Store one parsed export (timecards.parse_rows output). Returns
    (snapshot_id, is_new); an export Rocky has already read is not stored
    twice."""
    row = cx.execute("SELECT id FROM snapshots WHERE sha256=?", (sha256,)).fetchone()
    if row:
        return row[0], False
    entries = timecard["entries"]
    lo, hi = timecard.get("period") or (None, None)
    src_file = f"snapshot:{sha256[:12]}"
    with cx:
        cur = cx.execute(
            "INSERT INTO snapshots (received_at, source, sender, sha256, "
            "period_from, period_to, n_rows, hours) VALUES (?,?,?,?,?,?,?,?)",
            (received_at or datetime.now().isoformat(timespec="seconds"),
             source, sender, sha256, lo, hi, len(entries),
             round(sum(e["hours"] or 0 for e in entries), 2)))
        sid = cur.lastrowid
        for e in entries:
            cx.execute(
                "INSERT INTO entries (snapshot_id, src_file, row_no, work_date, "
                "matter, matter_name, client, hours, narrative, status, "
                "non_billable, no_charge, timekeeper_id, period) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, src_file, e["row"], e["work_date"], e["matter"],
                 e["matter_name"], e["client"], e["hours"], e["narrative"],
                 e["status"], int(e["non_billable"]), int(e["no_charge"]),
                 e["timekeeper_id"], e["work_date"][:7]))
            if e["narrative"]:
                cx.execute(
                    "INSERT OR IGNORE INTO narratives (work_date, matter, narrative, "
                    "origin, src_file) VALUES (?,?,?,?,?)",
                    (e["work_date"], e["matter"], e["narrative"], "submitted",
                     src_file))
    mark_superseded(cx)
    return sid, True


def mark_superseded(cx: sqlite3.Connection) -> int:
    """Overlapping exports double-count; keep one authoritative source per
    row (ported from build_corpus.py, where it fixed an August that read
    330.8 h instead of 203.6 h).

    Row key: (work_date, matter, hours) WITH multiplicity. For each key the
    highest-priority source wins (newest max work date, then most rows), so
    two genuine 0.2 h entries on one matter and day both survive. Then a
    wider export of a month replaces a narrower one outright, even where
    hours were edited between exports. Nothing is deleted."""
    with cx:
        cx.execute("UPDATE entries SET superseded=0")
        meta = {r[0]: (r[1], r[2]) for r in cx.execute(
            "SELECT src_file, MAX(work_date), COUNT(*) FROM entries GROUP BY 1")}
        order = {f: i for i, f in enumerate(
            sorted(meta, key=lambda f: meta[f], reverse=True))}
        groups = collections.defaultdict(lambda: collections.defaultdict(list))
        for r in cx.execute("SELECT id, work_date, matter, hours, src_file FROM entries"):
            groups[(r[1], r[2], r[3])][r[4]].append(r[0])
        n = 0
        for bysrc in groups.values():
            if len(bysrc) == 1:
                continue
            winner = min(bysrc, key=lambda f: order.get(f, 999))
            for f, ids in bysrc.items():
                if f == winner:
                    continue
                for i in ids:
                    cx.execute("UPDATE entries SET superseded=1 WHERE id=?", (i,))
                    n += 1
        cov = collections.defaultdict(dict)
        for r in cx.execute("SELECT period, src_file, COUNT(*), MIN(work_date), "
                            "MAX(work_date) FROM entries GROUP BY 1, 2"):
            cov[r[0]][r[1]] = (r[2], r[3], r[4])
        for period, bysrc in cov.items():
            if len(bysrc) < 2:
                continue
            winner = max(bysrc, key=lambda f: (bysrc[f][2], bysrc[f][0]))
            wlo, whi = bysrc[winner][1], bysrc[winner][2]
            for f, v in bysrc.items():
                if f != winner and v[1] >= wlo and v[2] <= whi:
                    cur = cx.execute("UPDATE entries SET superseded=1 "
                                     "WHERE period=? AND src_file=?", (period, f))
                    n += cur.rowcount
    return n


def narrative_rows(cx: sqlite3.Connection, before: str | None = None) -> list[tuple]:
    """(matter, narrative) pairs for the name index. `before` (ISO date)
    leaves out narratives on or after that date, so a backtest can't learn
    names from the very entries it is scoring."""
    sql = ("SELECT matter, narrative FROM narratives WHERE matter<>'' "
           "AND narrative<>''")
    params: list = []
    if before:
        sql += " AND work_date<>'' AND work_date < ?"
        params.append(before)
    return [(r[0], r[1]) for r in cx.execute(sql, params)]


def record_run(cx: sqlite3.Connection, kind: str, params: dict,
               summary: dict) -> int:
    with cx:
        cur = cx.execute(
            "INSERT INTO runs (kind, started_at, params, summary) VALUES (?,?,?,?)",
            (kind, datetime.now().isoformat(timespec="seconds"),
             json.dumps(params, default=str), json.dumps(summary, default=str)))
    return cur.lastrowid
