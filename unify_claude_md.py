r"""Unify the per-case CLAUDE.md logging boilerplate onto activity.jsonl.

Boilerplate-only (2026-06-28). For each case CLAUDE.md this:
  - Replaces/insertss a canonical "## Activity Logging" section pointing at
    activity.jsonl (one-line JSON schema) and a "## Rocky Suggestions" section.
  - Removes the legacy "## Activity Log Format" and "## Activity JSON
    (_spine_text/_activity.json)" sections.
  - Condenses "## The Case Spine": keeps the registry/session-log/deadlines/notes
    role, drops the lines telling sessions to also write _spine_text/_activity.json
    and _spine_text/_activity_log.md.
  - Rewrites the workflow "Step 8" + any stray `activitylog.md` mentions.

It does NOT touch folder structure, Case Overview, Case Intelligence, Working
Checklist, Rocky Digest, or any other bespoke content. Folder standardization is
a separate project.

Lineage handling:
  A (Workflow)  -> replace Activity Logging, drop Activity Log Format
  B (Case Spine)-> insert Activity Logging after Case Intelligence/Overview,
                   drop Activity JSON, condense The Case Spine
  HYBRID / OTHER-> flagged; processed best-effort but always review the diff

Usage:
  python unify_claude_md.py --only RRID-0001            # dry-run diff, one case
  python unify_claude_md.py                              # dry-run diff, all
  python unify_claude_md.py --execute                    # write (.preunify.bak made)
"""
from __future__ import annotations

import argparse
import difflib
import re
import shutil
import sys
from pathlib import Path

DEFAULT_ROOT = Path(r"C:\Users\jbragdon\OneDrive - gejlaw.com\Rocky Cases")
RRID_RE = re.compile(r"RRID-\d+", re.IGNORECASE)

ACTIVITY_LOGGING_BLOCK = """## Activity Logging

**Every Claude session (Cowork, Claude Code, Rocky) MUST log its activity to `activity.jsonl` in this case folder.** This is the single, unified audit trail across all actors — Rocky, James, and Cowork sessions — and it is the file Rocky's daily digest reads. `activity.jsonl` is canonical; there is no separate markdown log.

### Before starting any work:
1. Read `activity.jsonl` to see what other sessions (and Rocky) have already done
2. Check what files exist to avoid duplicating work
3. Review the **Rocky Suggestions** section below — check the recent `internal_suggestions` entries in `activity.jsonl` and ask James whether he wants you to act on any of them

### After completing any action that creates, modifies, or deletes files (or other meaningful work):
Append **one line** to `activity.jsonl` — a single JSON object, no pretty-printing, no trailing comma. Use this schema:

```json
{"timestamp": "2026-06-28T14:30:00Z", "actor": "Jane Smith (Cowork)", "event": "research", "summary": "Pulled VA unlawful-detainer case law on the late-fee defense; saved two cases to Legal Research.", "files": ["Legal Research/Smith v Jones.pdf"]}
```

Field rules:
- `timestamp` — ISO-8601 in **UTC** with a `Z` or `+00:00` suffix. The digest filters its window by this; a missing or non-UTC timestamp may land your entry in the wrong day.
- `actor` — who did the work and how, e.g. `"James Bragdon (Claude Code)"`, `"Jane Smith (Cowork)"`, `"rocky"`.
- `event` — a short lowercase type: `research`, `drafting`, `document_filed`, `memo_update`, `correspondence`, etc.
- `summary` — one or two sentences, plain attorney English. **This is what surfaces in the digest.** Keep it on a single line and escape any double-quotes (`\\"`) so the JSON stays valid.
- `files` — optional list of relative paths affected.

A malformed line is **silently skipped** by Rocky's reader, so make sure the JSON is valid (one line, balanced quotes and braces). When in doubt, write a simpler summary rather than risk invalid JSON.

---
"""

ROCKY_SUGGESTIONS_BLOCK = """## Rocky Suggestions

Rocky parks internal case-file maintenance suggestions (updating the Case Status Memorandum, refreshing the File / Searchable Text / Pleadings indexes, re-filing or renaming documents) in this case's activity log rather than in James's daily digest. **When you open this case as a project, read the recent `internal_suggestions` entries in `activity.jsonl` and ask James whether he wants you to act on them before doing so.**

---
"""

FENCE_RE = re.compile(r"^\s*```")
H2_RE = re.compile(r"^##(?!#)\s+(.*)$")


def parse_sections(text: str) -> list[dict]:
    """Split into H2 sections, respecting fenced code blocks. First chunk
    (title None) is the preamble before any H2."""
    sections = [{"title": None, "lines": []}]
    in_fence = False
    for ln in text.split("\n"):
        if FENCE_RE.match(ln):
            in_fence = not in_fence
            sections[-1]["lines"].append(ln)
            continue
        m = H2_RE.match(ln)
        if m and not in_fence:
            sections.append({"title": m.group(1).strip(), "lines": [ln]})
        else:
            sections[-1]["lines"].append(ln)
    return sections


def lineage(titles: list[str]) -> str:
    has_a = "Activity Logging" in titles or "Activity Log Format" in titles
    has_b = "The Case Spine" in titles or any(t.startswith("Activity JSON") for t in titles)
    return "HYBRID" if has_a and has_b else "B" if has_b else "A" if has_a else "OTHER"


def condense_spine(block_lines: list[str]) -> list[str]:
    out = []
    for ln in block_lines:
        low = ln.strip().lower()
        if low.startswith("also update `_spine_text/_activity_log.md`"):
            continue
        if low.startswith("also append structured events to `_spine_text/_activity.json`"):
            out.append("Activity logging is unified in `activity.jsonl` — see the "
                       "Activity Logging section above.")
            continue
        out.append(ln)
    return out


def transform(text: str) -> tuple[str, str]:
    secs = parse_sections(text)
    titles = [s["title"] for s in secs if s["title"]]
    lin = lineage(titles)

    al_lines = ACTIVITY_LOGGING_BLOCK.rstrip("\n").split("\n")
    rs_lines = ROCKY_SUGGESTIONS_BLOCK.rstrip("\n").split("\n")

    out: list[dict] = []
    inserted_al = False
    for s in secs:
        t = s["title"]
        if t in ("Activity Log Format", "Log Entries") or (t and t.startswith("Activity JSON")):
            continue  # drop legacy logging scaffolding
        if t == "Activity Logging":
            out.append({"title": "Activity Logging", "lines": al_lines})
            out.append({"title": "Rocky Suggestions", "lines": rs_lines})
            inserted_al = True
            continue
        if t == "The Case Spine":
            s = {"title": t, "lines": condense_spine(s["lines"])}
        out.append(s)

    # Family B / OTHER have no Activity Logging section — insert it (plus Rocky
    # Suggestions) right after Case Intelligence, else after Case Overview, else
    # after the preamble.
    if not inserted_al:
        anchor = next((i for i, s in enumerate(out) if s["title"] == "Case Intelligence (Read Before Any Work)"), None)
        if anchor is None:
            anchor = next((i for i, s in enumerate(out) if s["title"] == "Case Overview"), None)
        at = (anchor + 1) if anchor is not None else 1
        out[at:at] = [{"title": "Activity Logging", "lines": al_lines},
                      {"title": "Rocky Suggestions", "lines": rs_lines}]

    text2 = "\n".join(ln for s in out for ln in s["lines"])

    # Workflow Step 8 + any stray activitylog.md references.
    text2 = text2.replace(
        "1. Append an entry to `activitylog.md` with the full list of files affected",
        "1. Append a JSON-line entry to `activity.jsonl` (see Activity Logging above) with the full list of files affected in the `files` field",
    )
    text2 = text2.replace("`activitylog.md`", "`activity.jsonl`")
    text2 = re.sub(r"\n{3,}", "\n\n", text2)
    text2 = re.sub(r"(\n---)\n(##(?!#))", r"\1\n\n\2", text2)  # blank line after --- before a heading
    text2 = text2.rstrip("\n") + "\n"
    return text2, lin


def main() -> None:
    try:  # diffs/content contain em-dashes, arrows, etc.; avoid cp1252 crashes
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--only", default="")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--include-outliers", action="store_true",
                    help="also write HYBRID/OTHER cases (default: skip for manual review)")
    args = ap.parse_args()

    root = Path(args.root)
    cases = sorted(d for d in root.iterdir()
                   if d.is_dir() and RRID_RE.search(d.name)
                   and (not args.only or args.only.upper() in d.name.upper()))

    for case in cases:
        cm = case / "CLAUDE.md"
        if not cm.exists():
            continue
        before = cm.read_text(encoding="utf-8")
        after, lin = transform(before)
        changed = before != after
        is_outlier = lin in ("HYBRID", "OTHER")
        flag = "  <-- REVIEW (outlier)" if is_outlier else ""
        print(f"\n===== {case.name}  [{lin}]{flag}  {'CHANGED' if changed else 'no change'} =====")
        if args.execute and is_outlier and not args.include_outliers:
            print("  SKIPPED — outlier; handle manually (or pass --include-outliers)")
            continue
        if changed and not args.execute:
            diff = difflib.unified_diff(
                before.split("\n"), after.split("\n"),
                fromfile="CLAUDE.md (before)", tofile="CLAUDE.md (after)", lineterm="")
            print("\n".join(diff))
        if changed and args.execute:
            shutil.copy2(cm, cm.with_suffix(".md.preunify.bak"))
            cm.write_text(after, encoding="utf-8")
            print("  written (.preunify.bak saved)")

    if not args.execute:
        print("\nDRY RUN — re-run with --execute (and usually --only) to write.")


if __name__ == "__main__":
    main()
