"""Where the Time Entries process keeps things.

The OneDrive "Time Entries" folder holds what James edits by hand (the
Property Reference Table, the style guide, lessons, settings) plus the
timecard and draft history. It sits inside Program Files\\Rocky, next to
rocky.exe, on both laptops (decided 2026-10-24), and mounts at a different
local path on each, so it resolves the way llt_watch.tracker_root does:
--time-entries-root, then config "time_entries_root", then the folder beside
rocky.exe, then the dev-laptop paths.

Rocky's own database (te.db) is local, never on OneDrive: it holds client
narratives and email-derived data (DATA_SECURITY.md).
"""

import logging
import sys
from pathlib import Path

log = logging.getLogger("rocky")

ROOT_DIRNAME = "Time Entries"
_DEV_ROOTS = (
    r"C:\Users\jbragdon\OneDrive - gejlaw.com\Program Files\Rocky\Time Entries",
    # Where the folder lived before the 10/2026 move into Program Files\Rocky.
    r"C:\Users\jbragdon\OneDrive - gejlaw.com\Time Entries",
)
DEFAULT_BRAIN_DB = r"C:\Minotaur\email_brain\brain.db"
# Not "time_entries": in dev mode DATA_DIR is the repo root, where that name
# is this package.
LOCAL_DIRNAME = "time_entries_data"


def argv_value(flag: str, argv: list[str] | None = None) -> str | None:
    args = sys.argv if argv is None else argv
    if flag in args:
        idx = args.index(flag)
        if idx + 1 < len(args):
            return args[idx + 1]
    return None


def resolve_root(config: dict, argv: list[str] | None = None) -> Path:
    """The Time Entries folder on this machine."""
    explicit = (argv_value("--time-entries-root", argv)
                or config.get("time_entries_root") or "").strip()
    if explicit and Path(explicit).is_dir():
        return Path(explicit)
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / ROOT_DIRNAME)
    candidates += [Path(p) for p in _DEV_ROOTS]
    for cand in candidates:
        if cand.is_dir():
            if explicit:
                log.warning(f"[time-entries] time_entries_root {explicit!r} "
                            f"not found here — using {cand}")
            return cand
    if explicit:
        return Path(explicit)
    raise FileNotFoundError(
        "Time Entries folder not found. Set time_entries_root in config.json "
        "or pass --time-entries-root. Looked in: "
        + "; ".join(str(c) for c in candidates))


def get_paths(config: dict, data_dir: Path,
              argv: list[str] | None = None) -> dict:
    root = resolve_root(config, argv)
    local = Path(config.get("time_entries_data_dir")
                 or Path(data_dir) / LOCAL_DIRNAME)
    brain = (argv_value("--brain-db", argv)
             or config.get("minotaur_brain_db") or DEFAULT_BRAIN_DB)
    return {
        "root": root,
        "reference": root / "Property Reference Table.xlsx",
        "overlay": root / "matter_overlay.json",
        "settings": root / "time_entries_settings.json",
        "lessons": root / "LESSONS.md",
        "style_guide": root / "Time Entry Style Guide.md",
        "name_index_json": root / "port" / "name_index.json",
        "corpus": root / "corpus.db",
        "timecards": root / "timecards",
        "drafts": root / "drafts",
        "local": local,
        "db": local / "te.db",
        "backtests": local / "backtests",
        "brain": Path(brain),
    }
