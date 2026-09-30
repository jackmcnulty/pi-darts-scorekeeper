# Disaster recovery

How to get the scorekeeper's history back after losing the SD card, after the
database is damaged, and after a mistake. Ticket #31. What each piece of
machinery does is in [durability.md](durability.md) and [deploy.md](deploy.md);
this is the runbook that uses them.

## Where the copies are

| Copy | Where | Written | Survives the card dying |
| --- | --- | --- | --- |
| The database | `/var/lib/darts/darts.db` on the Pi | Every dart | No |
| On-Pi backups | `/var/lib/darts/backups/darts-<ts>.db` | **Only by `deploy.sh`**, just before each deploy | No |
| The share snapshot | `/srv/darts-share/darts-latest.db` | Every 5 minutes, and when a match ends | No |
| Mac copies | `~/Library/Application Support/darts-backups/darts-<ts>.db` | **When you run `scripts/backup-pull.sh`** | Yes |

Only the last row is off the card. Nothing pulls on a schedule, by decision, so
**the recovery point after losing the card is your last pull**: every dart thrown
since then is gone. Pull after a session you would mind losing.

## The one thing to know first

**A missing database is not treated as damage.** When `darts.db` does not exist,
the boot check creates an empty one and reports it `healthy`
([durability.md → Boot integrity check](durability.md#boot-integrity-check)). So
after a wipe, `/api/healthz` says:

```json
{"status":"healthy", ..., "auto_restored":false, "detail":"created a new database"}
```

with no players, no matches and no darts. That was reproduced in the drill below.
**A restore passes when the history is back, not when the app says healthy**:
compare the data, never just the status. And stop the container before touching
the database, or `restart: unless-stopped` can start it in the gap and hand you
exactly that healthy, empty scorekeeper.

## Taking a copy

From the repository on the Mac:

```bash
scripts/backup-pull.sh
```

It downloads `GET /api/export/db` (a copy the app takes the moment you ask, WAL
included), refuses it unless `PRAGMA integrity_check` says `ok` and it has a
schema version, renames it into place, and keeps the newest 30. It needs only
what ships with macOS: `/usr/bin/curl`, `/usr/bin/sqlite3`, `/bin/bash`.

| You see | Meaning | Exit |
| --- | --- | --- |
| `==> pulled darts-….db (… bytes, schema version 3, integrity ok) in 0s` | A new copy. | 0 |
| `info: the Pi is not reachable at … (curl exit 28: Could not resolve host: darts.local); probably switched off -- nothing pulled` | The Pi is off. Normal. Takes about 5 s, the mDNS timeout. | 0 |
| `info: … (curl exit 7: … Couldn't connect to server) …` | Nothing on port 8000: the Pi is off with its address still cached, **or it is up and the app is down**. | 0 |
| `warning: rejected the download (… bytes): integrity_check said: …` | A damaged copy. The copies already on the Mac are untouched. | 1 |
| `warning: the pull from … failed (curl exit 22: …)` | The app answered with an error. | 1 |
| `warning: the newest local copy is 3d 4h old (…), past the 24h threshold` | Added to any run that did not get a new copy, when your newest is old. | — |

Settings, all optional, from the environment: `BACKUP_PULL_URL`,
`BACKUP_PULL_DIR`, `BACKUP_PULL_KEEP` (30), `BACKUP_PULL_STALE_HOURS` (24).
`scripts/backup-pull.sh --help` lists them.

The copies are plain SQLite files. To look inside one on the Mac:

```bash
sqlite3 -readonly ~/Library/Application\ Support/darts-backups/darts-20260930T193127Z.db 'select count(*) from darts; select count(*) from matches;'
```

## Restoring a Mac copy onto the Pi

Every scenario that needs the Mac's copy ends here. `darts-restore` runs
**inside the image, as uid 1000**, because a database or sidecar written by any
other user leaves the container unable to write (`attempt to write a readonly
database`). It checks the copy's integrity itself, and moves the database it
replaces aside to `darts.replaced-<ts>.db` rather than deleting it.

```bash
COPY=darts-20260930T193127Z.db    # the copy to restore: newest, or the one from before the mistake
ssh pi@darts.local 'docker compose -f /etc/darts/compose.yaml stop'
ssh pi@darts.local 'mkdir -p /tmp/darts-restore'
scp ~/Library/Application\ Support/darts-backups/$COPY pi@darts.local:/tmp/darts-restore/
ssh pi@darts.local "chmod 644 /tmp/darts-restore/$COPY && docker run --rm -v /var/lib/darts:/var/lib/darts -v /tmp/darts-restore:/restore:ro --entrypoint darts-restore darts:latest /var/lib/darts/darts.db --from /restore/$COPY --force"
ssh pi@darts.local 'docker compose -f /etc/darts/compose.yaml start && rm -rf /tmp/darts-restore'
```

`--force` because there is no terminal for `darts-restore` to ask. The copy is
staged outside `/var/lib/darts` and mounted read-only, so the container does the
only write into the data directory. On the Pi, `pi` is uid 1000 and the `chmod`
is belt and braces; on a host where the SSH user is someone else, it is what lets
the container read the file.

A restored copy at an older schema version is migrated when the app starts, as
any other database is.

### What a pass looks like

1. `curl -s http://darts.local:8000/api/healthz` says `healthy` with
   `"detail":null`. **Not `created a new database`.**
2. The history matches the copy. The row counts from the copy on the Mac (the
   `sqlite3` command above) equal the rows the Pi now serves:

   ```bash
   curl -s http://darts.local:8000/api/export/darts.csv | tail -n +2 | wc -l
   curl -s http://darts.local:8000/api/export/matches.csv | tail -n +2 | wc -l
   ```

3. If you took digests beforehand (you will have, in a drill), they match:

   ```bash
   curl -s http://darts.local:8000/api/export/darts.csv | shasum -a 256
   curl -s http://darts.local:8000/api/export/matches.csv | shasum -a 256
   curl -s http://darts.local:8000/api/export/stats.json | sed 's/"generated_at":"[^"]*",//' | shasum -a 256
   ```

   `stats.json` is digested without `generated_at`, which is the time of the
   request rather than data.

4. Something can be written: add a player or throw a dart on the phone.

## Scenario 1: total SD card loss

The database, the on-Pi backups and the share are all gone. The Mac's newest copy
is all there is.

1. Flash a new card with Raspberry Pi OS Bookworm, hostname `darts`, user `pi`
   ([deploy.md → Bootstrapping a fresh Pi](deploy.md#bootstrapping-a-fresh-pi)).
2. `sudo scripts/bootstrap-pi.sh` on the Pi. It recreates `/var/lib/darts`,
   `/var/lib/darts/backups` and `/srv/darts-share`, owned by 1000.
3. `scripts/deploy.sh --host pi@darts.local` from the Mac. **The app comes up
   healthy and empty. That is expected here and is not the pass.**
4. [Restore the Mac's newest copy](#restoring-a-mac-copy-onto-the-pi).
5. Check [what a pass looks like](#what-a-pass-looks-like).

The share repopulates on its own within five minutes. The on-Pi backup archive
stays empty until the next deploy.

**Lost:** everything thrown after your last pull.

## Scenario 2: database corruption

**The boot check handles this on its own**, as long as the card itself works. At
start-up a database that fails `integrity_check` or `foreign_key_check` is moved
aside to `darts.corrupt-<ts>.db`, the newest on-Pi backup that passes its own
check is restored, and `/api/healthz` reports `"status":"restored"` and
`"auto_restored":true`, with the failure in `detail`
([durability.md → Boot integrity check](durability.md#boot-integrity-check)).

What it restores from is the on-Pi backup archive, and **that is only written by
`deploy.sh`**. So the automatic repair takes you back to your last deploy, which
may be weeks old. Before accepting it:

1. Find which backup it used. Health does not say; the container log does:

   ```bash
   ssh pi@darts.local 'docker logs darts 2>&1 | grep "from the backup taken at"'
   ```

   That timestamp is how far back you went.
2. Compare it with the newest Mac copy (its name is its timestamp), and with the
   share's `darts-latest.db`, which is at most five minutes older than the moment
   the Pi went down, **if it passes its own check**. The share is republished
   every five minutes from the database the app is now serving, so grab it
   straight away or it will be overwritten with the older, restored state:

   ```bash
   mkdir -p ~/darts-share-rescue && mount_smbfs -N //guest@darts.local/darts ~/darts-share-rescue
   cp ~/darts-share-rescue/darts-latest.db ~/Library/Application\ Support/darts-backups/darts-rescue.db
   umount ~/darts-share-rescue
   sqlite3 -readonly ~/Library/Application\ Support/darts-backups/darts-rescue.db 'PRAGMA integrity_check;'
   ```

   Name it back to `darts-<YYYYMMDDTHHMMSSZ>.db`, using the `created_at` from
   `snapshot.json`, if you want `backup-pull.sh`'s retention to manage it.
   Otherwise it is left alone.
3. If either is newer and says `ok`, [restore it](#restoring-a-mac-copy-onto-the-pi).
   The automatically restored database is moved aside, not lost.

If there was no valid on-Pi backup, `/api/healthz` returns **503** with
`"status":"degraded"` and the app runs on an empty database. Restore the Mac's
newest copy.

**Lost:** at most the darts after the copy you restore. The damaged file is kept
as `darts.corrupt-<ts>.db` if you want to try salvaging from it.

## Scenario 3: accidental data loss

A match abandoned by mistake, darts undone that should not have been, or
anything else the app did exactly as asked. The database is healthy, so nothing
repairs itself. You are choosing a moment to go back to.

1. **Do not pull again until you have decided.** A pull adds a copy containing
   the mistake, and retention evicts the oldest copy each time you are past 30.
2. Find the newest copy from *before* the mistake. The names are UTC timestamps.
   Inspect a candidate on the Mac with `sqlite3 -readonly` before choosing. The
   on-Pi backups (`/var/lib/darts/backups/`) are candidates too, if a deploy
   happened recently.
3. [Restore it](#restoring-a-mac-copy-onto-the-pi). For an on-Pi backup, skip
   the `scp` and point `--from` at it through a read-only mount of
   `/var/lib/darts/backups`.

**Lost:** everything after the copy you choose, including anything good thrown
after the mistake. The database you replaced is kept as
`darts.replaced-<ts>.db`, so a wrong choice can be undone the same way.

## The recorded drill

**Verified against a stand-in Linux host over SSH, not on the Pi.** The stand-in
is the aarch64 Ubuntu 24.04 VM from #29 and #30, running `main` deployed as
`7b571ce`, reached from the Mac through colima's forward of port 8000. The drill
on the Pi itself is part of #32's device pass.

Recorded 2026-09-30, times UTC.

**The data.** 210 matches played through the HTTP API beforehand: 150 best-of-3
x01 (501 and 301, double out, with busts and checkouts) and 60 cricket (standard
and cut-throat), between six players. That joined the stand-in's existing
history for **15,336 darts, 215 matches and 23 players**.

**Before (19:31:24).** Digests of the three exports, taken twice to prove they
are stable:

| Export | sha256 | Bytes | Rows |
| --- | --- | --- | --- |
| `darts.csv` | `7873be2c7c260ffbb3b44c3d7a2c331f239315d3969170652a47bbf17fa5c241` | 2,275,425 | 15,336 darts |
| `matches.csv` | `b8324524f4bee540be2b8cb5c8228e708db786ee2f7b6b301a2e37ec1d6f1cc0` | 43,578 | 215 matches |
| `stats.json`* | `0b1d73ac34e51bb513ea96ad76b815b41fe4b46416b1e6e5b2959caaa419fc63` | 43,565 | 23 players |

\* Canonicalised: `generated_at` removed, keys sorted.

**The pull (19:31:27).** `scripts/backup-pull.sh` pointed at the stand-in:

```
==> pulled darts-20260930T193127Z.db (4411392 bytes, schema version 3, integrity ok) in 0s
```

94 ms wall time. `PRAGMA integrity_check` on the Mac: `ok`. The copy's sha256 was
`08bd007fe51ce10cc8177959055deffa4d07c227a5dbcf4ac4e691bcb0eb73ec`, and it held
15,336 darts and 215 matches.

**The wipe (19:31:27).** The container was stopped, then `darts.db` and its
sidecars, **all five on-Pi backups** and **both files on the share** were
deleted, leaving the three directories empty, as a freshly bootstrapped card has
them. Wiping only `darts.db` would have tested the Pi's own recovery rather than
the Mac's copy.

**The trap, reproduced (19:31:28).** The container was started on the wiped
state, as a fresh deploy would start it:

```json
{"status":"healthy","schema_version":3,"git_sha":"7b571ce","version":"0.1.0","auto_restored":false,"checked_at":"2026-09-30T19:31:28Z","detail":"created a new database"}
```

Healthy, with 0 darts, 0 matches and 0 players.

**The restore (19:31:29 to 19:31:31).** Exactly [the procedure
above](#restoring-a-mac-copy-onto-the-pi):

```
restored /var/lib/darts/darts.db from /restore/darts-20260930T193127Z.db; previous database kept at /var/lib/darts/darts.replaced-20260930T193130Z.db
```

Stop to healthy took **1.9 s**. `/api/healthz` then reported `"detail":null`.
Everything in `/var/lib/darts` was owned by 1000, and the database was back in
`wal` mode with its `-wal`/`-shm` owned by 1000. A write (creating a player)
returned 201.

**After (19:31:31).** The same three digests, byte for byte, as before the wipe:
every dart, every match and the lifetime stats. **PASS.** From the start of the
wipe to the verified comparison took 5.4 s, including the deliberate empty start;
the whole drill, digests included, took 8.4 s.

### What the stand-in cannot tell you

- **`darts.local`.** The stand-in was reached as `localhost:8000`. On this Mac, a
  `darts.local` nobody answers for fails in 5.05 s with curl exit 28, which the
  script treats as "switched off". That was measured with no Pi on the network,
  not with a real one.
- **The Pi's speed.** A 4.4 MB pull took 94 ms over a loopback forward. The LAN
  and the SD card will be slower; the script allows 300 s.
- **Scenario 2 and 3 on hardware.** Corruption recovery at boot is covered by the
  backend suite ([durability.md → What the tests prove](durability.md#what-the-tests-prove)),
  not by this drill. Both scenarios end in the same restore procedure the drill
  exercised.
