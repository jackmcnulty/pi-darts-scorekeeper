"""Statistics, computed entirely by query over raw darts.

Nothing in here is a stored counter. Every number is derived from the darts
themselves at the moment it is asked for, which is why a metric added later is
a new `.sql` file and nothing else -- no migration, no backfill, and full
retroactive history over play that happened before the metric existed.

The queries read the views in `darts.db.views`, never the base tables;
`tests/db/test_view_bypass.py` scans `sql/` and enforces it.
"""
