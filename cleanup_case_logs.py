"""One-off maintenance: unify per-case activity logs onto activity.jsonl.

Background (2026-06-28): the digest now reads a single activity log,
`activity.jsonl`. Two legacy logs are no longer read by Rocky:

  - `activitylog.md`               (Family A "Workflow" CLAUDE.md wrote this)
  - `_spine_text/_activity.json`   (Family B "Case Spine" CLAUDE.md wrote this)

For each case this script:
  1. MIGRATES real entries from both legacy logs INTO activity.jsonl as JSON
     lines (deduped + idempotent — safe to re-run), preserving the history.
  2. ARCHIVES the legacy files into an `_archive/` subfolder (move, never
     delete), timestamp-suffixed so nothing is clobbered.

`activity.jsonl` is only appended to, never rewritten in place destructively
(a .bak copy is made before appending in --execute mode). It classifies each
case's CLAUDE.md lineage for reporting only — it does NOT edit CLAUDE.md
(that's the separate "unify CLAUDE.md" step).

Usage:
    python cleanup_case_logs.py                 # DRY RUN (default) — report only
    python cleanup_case_logs.py --execute        # migrate + archive for real
    python cleanup_case_logs.py --only "RRID-0001"   # limit to one case
    python cleanup_case_logs.py --root "<path>"  # override case-root folder
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = Path(r"C:\Users\jbragdon\OneDrive - gejlaw.com\Rocky Cases")
RRID_RE = re.compile(r"RRID-\d+", re.IGNORECASE)
ALOG_HEADER_RE = re.compile(r"^###\s+(.*\S)\s*$")
# Split a header into <timestamp-part> <sep> <title> on the first " — " / " - ".
ALOG_SPLIT_RE = re.compile(r"\s+[—–\-]+\s+")
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
TIME_RE = re.compile(r"(\d{2}:\d{2})")


def rrid_of(case: Path) -> str:
    m = RRID_RE.search(case.name)
    return m.group(0).upper() if m else ""


def normalize_ts(ts: str) -> str:
    """Return an ISO-8601 UTC timestamp. Naive inputs are treated as UTC."""
    ts = ts.strip()
    if not ts:
        return datetime.now(timezone.utc).isoformat()
    # md form "YYYY-MM-DD HH:MM" -> add seconds
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", ts):
        ts = ts.replace(" ", "T") + ":00"
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    # no offset present -> assume UTC
    if not re.search(r"[+\-]\d{2}:\d{2}$", ts):
        ts = ts + "+00:00"
    return ts


def load_existing_keys(activity_jsonl: Path) -> tuple[set, set]:
    """Return (spine_ids, alog_keys) already present, for idempotent re-runs."""
    spine_ids, alog_keys = set(), set()
    if not activity_jsonl.exists():
        return spine_ids, alog_keys
    for line in activity_jsonl.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("spine_id"):
            spine_ids.add(obj["spine_id"])
        if obj.get("alog_key"):
            alog_keys.add(obj["alog_key"])
    return spine_ids, alog_keys


def spine_records(case: Path, rrid: str, seen: set) -> list[dict]:
    sp = case / "_spine_text" / "_activity.json"
    if not sp.exists():
        return []
    try:
        data = json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for ev in data.get("events", []):
        sid = ev.get("id") or hashlib.md5(
            json.dumps(ev, sort_keys=True).encode()).hexdigest()[:12]
        if sid in seen:
            continue
        seen.add(sid)
        out.append({
            "timestamp": normalize_ts(ev.get("timestamp", "")),
            "actor": ev.get("actor", "Cowork"),
            "event": ev.get("type", "spine_event"),
            "summary": ev.get("summary", ""),
            "rrid": rrid,
            "details": ev.get("details", {}),
            "source": "spine",
            "spine_id": sid,
        })
    return out


def activitylog_records(case: Path, rrid: str, seen: set) -> list[dict]:
    al = case / "activitylog.md"
    if not al.exists():
        return []
    lines = al.read_text(encoding="utf-8").splitlines()
    # Header lines that actually carry a real date (skip the template's
    # "[YYYY-MM-DD HH:MM]" placeholder and prose headings like "Field definitions").
    def parse_header(ln: str):
        m = ALOG_HEADER_RE.match(ln)
        if not m:
            return None
        parts = ALOG_SPLIT_RE.split(m.group(1), maxsplit=1)
        left = parts[0]
        title = parts[1].strip() if len(parts) > 1 else ""
        dm = DATE_RE.search(left)
        if not dm:
            return None  # not a real timestamped entry
        tm = TIME_RE.search(left)
        return dm.group(1), (tm.group(1) if tm else "00:00"), (title or left.strip())

    headers = [(i, h) for i, ln in enumerate(lines) if (h := parse_header(ln))]
    out = []
    for n, (start, hdr) in enumerate(headers):
        end = headers[n + 1][0] if n + 1 < len(headers) else len(lines)
        block = lines[start:end]
        date_s, time_s, title = hdr
        user = sess = status = ""
        desc_parts = []
        for ln in block[1:]:
            s = ln.strip()
            if s.lower().startswith("- **user:**"):
                user = s.split("**", 2)[-1].lstrip(":* ").strip()
            elif s.lower().startswith("- **session type:**"):
                sess = s.split("**", 2)[-1].lstrip(":* ").strip()
            elif s.lower().startswith("- **status:**"):
                status = s.split("**", 2)[-1].lstrip(":* ").strip()
            elif s.startswith("- **") or s.startswith("- ") or not s:
                continue  # other field bullets / file sub-bullets / blanks
            else:
                desc_parts.append(s)
        desc = " ".join(desc_parts).strip()
        key = hashlib.md5(f"{rrid}|{date_s} {time_s}|{title}".encode()).hexdigest()[:12]
        if key in seen:
            continue
        seen.add(key)
        actor = f"{user} ({sess})".strip() if user or sess else "session"
        summary = f"{title} — {desc}" if desc else title
        out.append({
            "timestamp": normalize_ts(f"{date_s} {time_s}"),
            "actor": actor,
            "event": "session_log",
            "summary": summary,
            "rrid": rrid,
            "status": status,
            "source": "activitylog_md",
            "alog_key": key,
        })
    return out


def archive(case: Path, rel: str, execute: bool, stamp: str) -> str | None:
    src = case / rel
    if not src.exists():
        return None
    dest = case / "_archive" / f"{rel.replace('/', '__')}.archived-{stamp}"
    if execute:
        dest.parent.mkdir(exist_ok=True)
        shutil.move(str(src), str(dest))
        return f"archived {rel} -> _archive/{dest.name}"
    return f"would archive {rel} -> _archive/{dest.name}"


def classify(case: Path) -> str:
    cm = case / "CLAUDE.md"
    if not cm.exists():
        return "MISSING"
    t = cm.read_text(encoding="utf-8", errors="replace")
    a = "## Activity Logging" in t or "## Activity Log Format" in t
    b = "## The Case Spine" in t or "Activity JSON (`_spine_text" in t
    return "HYBRID(A+B)" if a and b else "B(Spine)" if b else "A(Workflow)" if a else "OTHER"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"Root not found: {root}")
        sys.exit(1)

    mode = "EXECUTE" if args.execute else "DRY RUN"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    print(f"=== Activity-log unification ({mode}) ===\nRoot: {root}\n")

    cases = sorted(d for d in root.iterdir()
                   if d.is_dir() and RRID_RE.search(d.name)
                   and (not args.only or args.only.upper() in d.name.upper()))

    tot_spine = tot_alog = tot_arch = 0
    for case in cases:
        rrid = rrid_of(case)
        aj = case / "activity.jsonl"
        spine_ids, alog_keys = load_existing_keys(aj)
        new_spine = spine_records(case, rrid, spine_ids)
        new_alog = activitylog_records(case, rrid, alog_keys)

        print(f"{case.name}  [{classify(case)}]")
        print(f"    migrate: {len(new_spine)} spine + {len(new_alog)} activitylog "
              f"-> activity.jsonl")

        if args.execute and (new_spine or new_alog):
            if aj.exists():
                shutil.copy2(aj, aj.with_suffix(".jsonl.bak"))
            with open(aj, "a", encoding="utf-8") as f:
                for rec in sorted(new_spine + new_alog, key=lambda r: r["timestamp"]):
                    f.write(json.dumps(rec) + "\n")

        for rel in ("activitylog.md", "_spine_text/_activity.json"):
            msg = archive(case, rel, args.execute, stamp)
            if msg:
                print(f"    {msg}")
                tot_arch += 1
        tot_spine += len(new_spine)
        tot_alog += len(new_alog)
        print()

    verb = "Migrated" if args.execute else "Would migrate"
    print(f"{verb} {tot_spine} spine + {tot_alog} activitylog entries; "
          f"{'archived' if args.execute else 'would archive'} {tot_arch} file(s) "
          f"across {len(cases)} case(s).")
    if not args.execute:
        print("\nDRY RUN — re-run with --execute to perform migration + archival.")


if __name__ == "__main__":
    main()
