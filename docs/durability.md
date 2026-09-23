# Durability, recovery, and backups

This Pi is power-cycled constantly and its SD card will fail silently one day.
This document describes what makes that survivable. Ticket #12.

All of it lives outside `backend/darts/engine/`, which the purity guard keeps
free of filesystem, clock, and SQLite access:

| Module | Responsibility |
| --- | --- |
| `darts/db/durability.py` | Checkpointing, integrity verification, quarantine. |
| `darts/db/backup.py` | Snapshots, atomic publication, retention, restore. |
| `darts/db/recovery.py` | The boot decision and the status it reports. |
| `darts/tools/backup.py`, `darts/tools/restore.py` | The two CLIs. |

## Clean shutdown

```python
from darts.db.durability import checkpoint_truncate

result = checkpoint_truncate(conn)  # PRAGMA wal_checkpoint(TRUNCATE)
assert result.truncated
```

`CheckpointResult` carries SQLite's own `(busy, log, checkpointed)` row.
**A busy checkpoint is reported, not raised.** SQLite returns `busy=1` without
error when another connection holds a read lock, so a caller that ignored the
result would report a clean shutdown it never achieved. Check `.truncated`.

After a successful truncation the `-wal` file is zero length. Once the last
connection closes, SQLite removes it entirely — so "clean shutdown" means *no
WAL or an empty one*, never a populated one. Asserting the file still exists
would be asserting the wrong thing.

#16 owns the `SIGTERM` lifespan hook that calls this.

## Boot integrity check

```python
from darts.db.recovery import check_and_recover

status = check_and_recover(Path("/srv/darts/darts.db"))
```

Both checks #12 requires are run: `PRAGMA integrity_check` **and**
`PRAGMA foreign_key_check`. Damage severe enough shows up as an exception while
the file is being *opened*, before any PRAGMA can run, so the open is inside the
guarded block too. Quieter damage — a corrupted index page, for instance — opens
cleanly and is only caught by the PRAGMA. Both paths are tested.

On failure, in order:

1. The database is moved to `darts.corrupt-<timestamp>.db`. **Nothing is ever
   deleted.**
2. The newest backup that itself passes `integrity_check` is restored. A damaged
   backup is skipped rather than allowed to block recovery.
3. The event is logged at `ERROR`, and returned as a `RecoveryStatus`.
4. If no valid backup exists, an empty database is created and the status is
   `DEGRADED` — never a crash loop.

Every outcome ends with migrations applied *and the views reinstalled*, so a
backup taken at an older schema version is serviceable the moment recovery
returns. Views are not carried by the migration ledger, so without that second
step a restored database would come back with its tables but no query surface,
and nothing would notice until the first statistics request. See
[Views](data-model.md#views).

### What is *not* treated as corruption

Only an explicit allowlist of SQLite malformation messages, plus a failing
`integrity_check`/`foreign_key_check`, counts as damage. A permissions error, an
exhausted disk, a locked file, or an unsupported migration history **propagates
untouched**. Quarantining and replacing a healthy database because the disk was
briefly full would cause exactly the data loss this is meant to prevent.

### Sidecars

`-wal` and `-shm` always travel with the main file. Leaving them behind would
let SQLite replay a stale log over whatever replaces the database; deleting them
would discard committed history that has not been checkpointed yet — precisely
the history a damaged database most needs.

### Recovery status

`RecoveryStatus` is what #16's `/api/healthz` reports:

| Field | Meaning |
| --- | --- |
| `state` | `healthy`, `restored`, or `degraded`. |
| `checked_at` | UTC timestamp of the boot check. |
| `detail` | The failure that triggered recovery, if any. |
| `quarantined_to` | Where the damaged file was kept. |
| `restored_from` | The backup used, if one was. |
| `degraded` | `state is DEGRADED` — #16 returns 503 on this. |
| `auto_restored` | Whether this boot performed a restore. |

## Backups

```sh
uv run darts-backup /srv/darts/darts.db
uv run darts-backup /srv/darts/darts.db --backup-dir /mnt/usb/darts --no-prune
```

Backups default to a `backups/` directory beside the database, so relocating the
database relocates both. #16 owns the Settings object that will make this
configurable; until then `--backup-dir` is the override.

Copies go through `Connection.backup()`, which reads pages inside a read
transaction — a snapshot taken mid-game is a point-in-time image, where `cp`
would capture a torn file. The copy is written to a temporary file **in the
destination directory** and published with `os.replace`, so a reader never
observes a half-written backup. Its WAL is collapsed before publication, leaving
one self-contained file that desktop tools can open directly.

Naming is `darts-<YYYYMMDDTHHMMSSZ>.db`, with a `-1`, `-2` suffix distinguishing
backups taken inside the same second. **"Newest" means the timestamp in the
filename, never mtime**, because restoring or copying a backup rewrites mtime.

Each backup gets a `<name>.db.json` manifest holding the timestamp, schema
version, size, source path, and per-table row counts — all read back out of the
finished artifact rather than the live database. The database is renamed into
place *before* its manifest is written, so an interrupted run can leave a backup
without a manifest (restorable, and reported as such) but never a manifest
promising a backup that is absent.

### Retention

Grandfather-father-son over UTC buckets, keeping the last **24 hourly** and
**30 daily**:

- Group by UTC hour; keep the newest backup in each of the 24 most recent hour
  buckets.
- Group by UTC day; keep the newest backup in each of the 30 most recent day
  buckets.
- Retain the **union**. Delete everything else, manifests included.

The windows overlap rather than compete: a burst of backups this afternoon
cannot evict last week's dailies, and an idle day costs an hour bucket rather
than a day of history. Ten backups within one hour collapse to one.

**The newest backup is always kept**, whatever the limits are set to — even
`--hourly 0 --daily 30`.

## Restore

```sh
uv run darts-restore /srv/darts/darts.db                    # newest valid backup
uv run darts-restore /srv/darts/darts.db --from /path/to/darts-....db
uv run darts-restore /srv/darts/darts.db --force            # no confirmation
```

Without `--force`, an existing database is only overwritten after an interactive
confirmation. With no terminal attached there is nobody to ask, so an unattended
caller must pass `--force` rather than have consent assumed for it.

The database being replaced is moved to `darts.replaced-<timestamp>.db` with its
sidecars, not deleted. That keeps a mistaken restore recoverable and guarantees
no stale `-wal` survives to be replayed over the restored file.

A backup that fails its own `integrity_check` is refused.

## What the tests prove

```sh
uv run pytest tests/db -v
```

- `test_durability.py` — a real subprocess is `SIGKILL`ed mid-write after it
  acknowledges its commits and announces an open transaction. Every acknowledged
  commit survives, the in-flight transaction leaves no trace, and the database
  passes `integrity_check`. Also: WAL truncation on clean shutdown, busy
  checkpoints reported rather than raised, quarantine keeping sidecars, and the
  corruption/environmental-failure distinction.
- `test_integrity_recovery.py` — corruption detected both at open and by the
  PRAGMA, quarantine, newest-good restore, skipping damaged backups, the
  no-valid-backup degraded path, and environmental failures never replacing a
  database.
- `test_backup.py` — snapshot consistency under concurrent writes, atomic
  publication with no leftover temporaries, the retention bucket arithmetic, and
  a backup → wipe → restore round-trip.
- `test_backup_cli.py` — both CLIs, including confirmation and exit codes.

**Scope note.** The `SIGKILL` tests prove that an abruptly killed *process*
leaves a sound database. They say nothing about losing power to the SD card
mid-write, which no CI runner can reproduce; `synchronous = FULL` from #11 is
what addresses that, and only real hardware can confirm it.

The round-trip is verified by comparing every row of all 12 tables plus
`user_version`. #13 views, #14 repositories, #15 play service and #19 stats
queries do not exist yet, so there is no statistics API to compare — statistics
are derived from the source tables this does compare.
