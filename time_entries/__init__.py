"""
Time Entries — Rocky's port of the OneDrive "Time Entries" process.

James's Aspen timecard export arrives with blank or partial narratives. This
package correlates each one with his sent mail (read from Minotaur's email
brain), and later phases draft the narratives, verify them, email the
recommendations, and learn from what he actually submitted. Design and phase
plan: TIME_ENTRIES_DESIGN.md at the repo root.

Phase 1 (this code) is deterministic only, with no Claude calls:

    paths.py       where the Time Entries folder, te.db and brain.db live
    timecards.py   Aspen export importer (both CSV shapes, plus .xlsx)
    reference.py   Property Reference Table + Matter Notes + overlay + settings
    mail.py        read-only reader for Minotaur's brain.db (and corpus.db)
    names.py       resident / counterparty name index (Rule 3)
    correlate.py   port of the folder's correlate3.py (Rules 1-11)
    db.py          te.db schema, corpus.db migration, timecard supersession
    backtest.py    replay a past hand run and score the correlator against it
    cli.py         rocky.py --time-entries --migrate | --correlate | --backtest

Phase 2 adds the Claude calls (Opus 5.5), still run by hand from the CLI:

    daysheet.py    the per-day packet (port of daydump.py) and its two tools
    draft.py       drafting + verification calls, Rocky's checks on the output
    run.py         the pipeline: correlate, day sheets, draft, verify, flatten
    learn.py       the deterministic diff against a later export, and links
    workbook.py    the house-format workbook (port of build_workbook.py)
    evaluate.py    the drafting backtest, its judge, and hand-run link seeding
    cli.py         ... | --draft | --draft-backtest | --seed-links

Nothing here imports rocky (it logs to C:\\Rocky at import time), and nothing
here ever writes to brain.db or corpus.db.
"""
