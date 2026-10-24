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

Nothing here imports rocky (it logs to C:\\Rocky at import time), and nothing
here ever writes to brain.db or corpus.db.
"""
