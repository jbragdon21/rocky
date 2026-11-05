"""Joe's Email Archive: jdugan@'s Sent Items, copied to the Rocky laptop and
searchable. Design: JOE_ARCHIVE_DESIGN.md.

    graph.py   tokens, paging and MIME downloads (read only)
    store.py   archive.db (SQLite + FTS5) and the .eml parser
    search.py  the one search function the CLI and (later) the chat share
    ingest.py  --check and --backfill / --sync
    census.py  --census: every send in every folder, counted
    evault.py  --vault-check: can Rocky reach the Vault's full copies?
    cli.py     rocky.py --joe-archive ...

Rocky never writes to Joe's mailbox. Everything lands under
joe_archive_root (default <data>\\joe_archive_data, C:\\Rocky\\joe_archive_data on the
Rocky laptop), which is local and never synced.
"""
