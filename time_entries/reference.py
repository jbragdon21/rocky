"""Matter reference data and correlation settings.

`Property Reference Table.xlsx` stays human-owned: Rocky reads both sheets
every run and never writes to the workbook. Changes James approves through
the learning loop (phase 4) go into `matter_overlay.json` beside it, and the
loader merges the overlay on top of the Matter Notes sheet.

`time_entries_settings.json` (optional) tunes the scorer. Only keys in
DEFAULT_SETTINGS are honored, each clamped to its bounds, so a bad value
can't switch correlation off.
"""

import json
import logging
from pathlib import Path

log = logging.getLogger("rocky")

ALIAS_COL = "Aliases & Search Tokens (pipe-separated)"
NO_EMAIL_COL = "Typically No Sent Email"
CASE_NAME_COL = "Matter / Case Name"
COLLISION_COL = "Correlation Notes / Collisions"

# With window_batch_hour, window_pool_days and weight_matter_number at 0,
# these reproduce correlate3.py exactly. The 10/25 backtest turned on the
# 9 AM next-morning batch and matter numbers (193 -> 198 of 216 High rows on
# corpus.db). A one-day pool at penalty 3 also scored 198, with more churn
# (eight gained, three lost), so it stays off.
DEFAULT_SETTINGS = {
    "weight_phrase": 4,          # multi-word distinctive phrase
    "weight_word": 2,            # single distinctive word
    "weight_alias_phrase": 4,    # curated alias containing a space
    "weight_alias_word": 3,      # curated single-word alias
    "weight_contact": 1,         # property contact surname
    "weight_name_phrase": 3,     # learned resident name, two+ words
    "weight_name_word": 2,       # learned resident name, one word
    "weight_matter_number": 6,   # the matter number written in the mail
    "short_token_len": 4,        # Rule 8: word boundaries at or below this
    "window_batch_hour": 9,      # next-morning mail before this hour counts as same-day
    "window_pool_days": 0,       # whole days each side searched with the entry's own
    "window_pool_penalty": 3,    # score penalty for mail from those days
    "window_near_days": 1,       # Rule 7 stage 2
    "window_far_days": 3,        # Rule 7 stage 3
    "sunday_forward_from": 4,    # Rule 6: Sunday entries reach forward
    "sunday_forward_to": 7,
    "max_candidates": 6,
    "partial_max_chars": 35,     # shorter narratives count as partial
    "gap_lookback_days": 180,    # Rule 4 zero-hit check reaches back this far
}
_BOUNDS = {
    "weight_phrase": (0, 10), "weight_word": (0, 10),
    "weight_alias_phrase": (0, 10), "weight_alias_word": (0, 10),
    "weight_contact": (0, 10), "weight_name_phrase": (0, 10),
    "weight_name_word": (0, 10), "weight_matter_number": (0, 10),
    "short_token_len": (2, 8), "window_batch_hour": (0, 12),
    "window_pool_days": (0, 3), "window_pool_penalty": (0, 5),
    "window_near_days": (0, 3), "window_far_days": (1, 7),
    "sunday_forward_from": (1, 7), "sunday_forward_to": (1, 10),
    "max_candidates": (1, 20), "partial_max_chars": (1, 200),
    "gap_lookback_days": (7, 730),
}


def load_settings(path: Path | None) -> dict:
    settings = dict(DEFAULT_SETTINGS)
    if not path or not Path(path).exists():
        return settings
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning(f"[time-entries] {path} unreadable ({e}) — using defaults")
        return settings
    for key, value in (raw or {}).items():
        if key.startswith("_"):
            continue
        if key not in DEFAULT_SETTINGS:
            log.warning(f"[time-entries] unknown setting {key!r} ignored")
            continue
        try:
            v = int(value)
        except (TypeError, ValueError):
            log.warning(f"[time-entries] setting {key}={value!r} is not a "
                        f"number — kept {settings[key]}")
            continue
        lo, hi = _BOUNDS[key]
        if not lo <= v <= hi:
            log.warning(f"[time-entries] setting {key}={v} outside {lo}–{hi} "
                        f"— clamped")
            v = max(lo, min(hi, v))
        settings[key] = v
    if settings["window_far_days"] < settings["window_near_days"]:
        settings["window_far_days"] = settings["window_near_days"]
    if settings["sunday_forward_to"] < settings["sunday_forward_from"]:
        settings["sunday_forward_to"] = settings["sunday_forward_from"]
    return settings


def load_reference(xlsx: Path, overlay: Path | None = None) -> tuple[dict, dict]:
    """(props, notes), both keyed by matter number.

    props: from the 'Property Reference' sheet — property, matter_name,
           client, contacts.
    notes: the 'Matter Notes (quirks)' sheet row as {column: value}, with
           the overlay merged in.
    """
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    try:
        rows = list(wb["Property Reference"].values)
        H = {h: i for i, h in enumerate(rows[0])}
        props = {}
        for r in rows[1:]:
            mn = r[H["Matter Number"]]
            if not mn:
                continue
            props[str(mn).strip()] = {
                "property": r[H["Property"]],
                "matter_name": r[H["Matter Name"]],
                "client": r[H["Client"]],
                "contacts": r[H["Property Contacts"]],
            }
        rows = list(wb["Matter Notes (quirks)"].values)
        H2 = {h: i for i, h in enumerate(rows[0])}
        notes = {}
        for r in rows[1:]:
            mn = r[H2["Matter Number"]]
            if not mn:
                continue
            notes[str(mn).strip()] = {k: (r[i] if i < len(r) else None)
                                      for k, i in H2.items()}
    finally:
        wb.close()
    if overlay:
        merge_overlay(notes, load_overlay(overlay))
    return props, notes


def load_overlay(path: Path) -> dict:
    """matter_overlay.json: {matter: {"aliases": [...], "no_email": "Yes",
    "collisions": "...", "matter_name": "..."}}. Missing file = no overlay."""
    if not path or not Path(path).exists():
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning(f"[time-entries] overlay {path} unreadable ({e}) — ignored")
        return {}
    return {k: v for k, v in (data or {}).items()
            if not k.startswith("_") and isinstance(v, dict)}


def merge_overlay(notes: dict, overlay: dict) -> None:
    for mn, add in overlay.items():
        row = notes.setdefault(mn, {"Matter Number": mn})
        aliases = [a.strip() for a in str(row.get(ALIAS_COL) or "").split("|")
                   if a.strip()]
        for a in add.get("aliases") or []:
            if a.strip() and a.strip().lower() not in {x.lower() for x in aliases}:
                aliases.append(a.strip())
        row[ALIAS_COL] = " | ".join(aliases)
        if add.get("no_email"):
            row[NO_EMAIL_COL] = add["no_email"]
        if add.get("matter_name") and not row.get(CASE_NAME_COL):
            row[CASE_NAME_COL] = add["matter_name"]
        if add.get("collisions"):
            prior = str(row.get(COLLISION_COL) or "").strip()
            row[COLLISION_COL] = (prior + " " if prior else "") + add["collisions"]
