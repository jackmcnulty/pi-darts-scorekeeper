# Deploying to the Pi

How the scorekeeper is packaged and deployed, and what has to be checked on real
hardware. Tickets #28, #29 and #30.

There is no systemd unit for the app and there should not be one.
`restart: unless-stopped` in `deploy/compose.yaml` provides both halves of what a
unit would do -- start at boot and restart after a crash -- given that
`docker.service` is itself enabled at boot, which `bootstrap-pi.sh` does. The one
unit in the repository, #30's `darts-snapshot`, runs nothing of the app's: it
asks the container over HTTP to publish a snapshot.

| File | Responsibility |
| --- | --- |
| `deploy/Dockerfile` | Three-stage build: Node, uv, runtime. Non-root, healthchecked. |
| `deploy/compose.yaml` | Restart policy, bind mounts, published port, shutdown grace. |
| `deploy/docker-entrypoint.sh` | `exec`s uvicorn so it is PID 1 and SIGTERM reaches it. |
| `deploy/darts.env.example` | Template for `/etc/darts/darts.env`. |
| `scripts/bootstrap-pi.sh` | Idempotent host setup: directories, `darts.env`, `compose.yaml`. |
| `.dockerignore` | Allowlist. Keeps `var/darts.db` out of the image. |
| `scripts/deploy.sh` | Build, ship, migrate, restart, verify, roll back. |
| `scripts/rollback.sh` | Manual rollback, after a deploy has already succeeded. |
| `scripts/healthcheck.sh` | Ask the target which sha it is serving. |
| `scripts/deploy-lib.sh` | Rollback selection, pruning, ordering. Pure, unit-tested. |
| `scripts/deploy-remote.sh` | Everything that talks over ssh. Decides nothing. |
| `deploy/smb-darts.conf` | The whole of `/etc/samba/smb.conf`: one read-only guest share. |
| `deploy/darts-snapshot.service` | `curl -X POST /api/admin/snapshot`, sandboxed. |
| `deploy/darts-snapshot.timer` | Runs it every five minutes. |
| `deploy/avahi-darts.service` | Advertises the share to Finder. An Avahi file, not a unit. |
| `scripts/backup-pull.sh` | Run on the Mac: pull a verified copy off the Pi. See `docs/dr.md`. |
| `scripts/backup-pull-lib.sh` | Its retention, staleness and curl decisions. Pure, unit-tested. |

## The layout on the host

```
/var/lib/darts/darts.db       the live database
/var/lib/darts/backups/       darts-backup writes here
/srv/darts-share/             snapshots, shared read-only by #30
/etc/darts/darts.env          configuration, loaded by compose
/etc/darts/compose.yaml       what deploy.sh drives the container with
/etc/samba/smb.conf           the share, from deploy/smb-darts.conf (#30)
/etc/samba/smb.conf.debian-orig   the stock file, kept once
/etc/avahi/services/darts.service     the share's mDNS advertisement
/etc/systemd/system/darts-snapshot.{service,timer}
```

The two files in `/etc/darts` are installed by `bootstrap-pi.sh` and stay
root-owned: nothing in the container writes them, and Compose reads them as the
operator. `darts.env` is never overwritten by a re-run; `compose.yaml` always is.

Both directories are bind mounts rather than named volumes, so that the
`sqlite3` CLI over SSH and Samba in #30 reach the files as ordinary files
rather than through `/var/lib/docker/volumes`.

Everything is owned by uid/gid **1000:1000**. That is the `pi` user on
Raspberry Pi OS and the user the container runs as, and the two have to agree
because the container is non-root and cannot fix ownership itself.

> **If you inspect the database from the host, do it as `pi`.**
> Opening a WAL-mode database creates `darts.db-wal` and `darts.db-shm` owned
> by whoever opened it — *even for a read-only connection*. Open it as `root`
> and the container will fail its next start with
> `sqlite3.OperationalError: attempt to write a readonly database`, which
> names the database rather than the sidecar files that actually caused it.
> The fix is `sudo chown 1000:1000 /var/lib/darts/darts.db-*`.

## Environment

`/etc/darts/darts.env` is created from `deploy/darts.env.example` and never
overwritten by a re-run. Two entries in that file are worth understanding.

`DARTS_SNAPSHOT_DIR=/srv/darts-share` **must be set explicitly.** Its default
is `snapshots/` beside the database, so leaving it out silently publishes
snapshots to `/var/lib/darts/snapshots` and #30's share is permanently empty,
with nothing failing to say why.

`DARTS_BACKUP_DIR` is deliberately left unset, because its default -- `backups/`
beside the database -- already resolves to `/var/lib/darts/backups`.

Variables the image itself sets (`DARTS_STATIC_DIR`, `DARTS_GIT_SHA`) are
commented out rather than left blank. Compose passes `env_file` entries to the
container as real environment variables and they override the image's `ENV`, so
a blank `DARTS_STATIC_DIR=` does not mean "use the image default" -- it sets it
to empty and sends the app looking for a source checkout that is not there.

## Building

Natively on an arm64 Mac, which matches the Pi and needs no emulation:

```
docker build -f deploy/Dockerfile --build-arg GIT_SHA="$(git rev-parse --short HEAD)" -t darts:latest .
```

Expected: a few seconds to a few minutes depending on cache and network, well
inside #28's five-minute budget. Measured on an M-series Mac with base images
already pulled: **8s cold** (`--no-cache`), image **267 MB**, build context
**1.53 MB**.

CI builds the same Dockerfile for linux/amd64 on every push. That proves the
Dockerfile is coherent; it does not prove the arm64 image runs.

## Bootstrapping a fresh Pi

See the plan first — this needs no privileges and changes nothing:

```
scripts/bootstrap-pi.sh --dry-run
```

Then, on the Pi:

```
sudo scripts/bootstrap-pi.sh
```

## Deploying

```
scripts/deploy.sh --host pi@darts.local
```

That is the whole normal case. It builds the image here, ships it to the Pi
tagged with the git sha, backs up and migrates the database from inside the new
image, restarts, polls `/api/healthz` for the new sha, and rolls back to the
previous tag if that sha does not appear. Exit status is 0 only for a healthy
deploy: a deploy that failed and rolled back cleanly still exits non-zero.

See the plan first, which needs no reachable target and changes nothing:

```
scripts/deploy.sh --host pi@darts.local --dry-run
```

### Every flag

`deploy.sh`:

| Flag | Default | What it does |
| --- | --- | --- |
| `--host <host>` | `$DARTS_DEPLOY_HOST` | SSH destination. Required. |
| `--ssh-config <file>` | `$DARTS_SSH_CONFIG` | Passed to ssh as `-F`. |
| `--port <port>` | 8000 | Where the app is published. Must match `compose.yaml`. |
| `--compose-file <path>` | `/etc/darts/compose.yaml` | The compose file on the target, installed there by `bootstrap-pi.sh`. |
| `--keep <n>` | 5 | Sha-tagged images to keep. The running image and the rollback target are never deleted, whatever their age. |
| `--health-retries <n>` | 30 | Health poll attempts, one second apart. A failed poll triggers rollback. |
| `--allow-dirty` | off | Deploy with uncommitted changes. Refused by default: the image is stamped with HEAD's sha and must not claim to be a commit it is not. |
| `--skip-tests` | off | Skip the backend suite in preflight. |
| `--dry-run` | off | Print the plan to stdout and change nothing, locally or remotely. |
| `-h`, `--help` | | Usage. |

`rollback.sh`, for a build that came up healthy and was *then* found to be wrong
— something a 200 from `/api/healthz` cannot see:

| Flag | Default | What it does |
| --- | --- | --- |
| `--host <host>` | `$DARTS_DEPLOY_HOST` | SSH destination. Required. |
| `--ssh-config <file>` | `$DARTS_SSH_CONFIG` | Passed to ssh as `-F`. |
| `--port <port>` | 8000 | Where the app is published. |
| `--compose-file <path>` | `/etc/darts/compose.yaml` | The compose file on the target. |
| `--to <sha>` | previous | Roll back to a specific sha. It must already be on the target. |
| `--list` | | Show the sha tags on the target, newest first, and exit. |
| `--health-retries <n>` | 30 | Health poll attempts after restarting. |
| `--dry-run` | off | Print the plan and change nothing. |
| `-h`, `--help` | | Usage. |

`healthcheck.sh`, useful on its own when something looks wrong:

| Flag | Default | What it does |
| --- | --- | --- |
| `--host <host>` | `$DARTS_DEPLOY_HOST` | SSH destination. Required. |
| `--ssh-config <file>` | `$DARTS_SSH_CONFIG` | Passed to ssh as `-F`. |
| `--port <port>` | 8000 | Where the app is published. |
| `--expect-sha <sha>` | any | Poll until the target reports this sha. |
| `--retries <n>` | 30 | Attempts, one second apart. |
| `-h`, `--help` | | Usage. |

### What the Pi needs, and what it does not

There are exactly two commands, and they own different things. **`bootstrap-pi.sh`
owns everything that lives on the host** — Docker, the directories,
`/etc/darts/darts.env` and `/etc/darts/compose.yaml`. **`deploy.sh` owns images
and the container**, and nothing else. It never writes a file to the Pi.

That is what makes a deploy one command with no setup in it, and it is also why a
deploy needs **no source checkout on the Pi and no root at any point**.

`deploy.sh` refuses to run if `/var/lib/darts` is missing or is not owned by uid
1000 — not a courtesy check, because Docker *creates* a missing bind-mount source
itself, owned by root, so `up -d` against an unprepared host succeeds and
produces a container that cannot write its own database.

It also refuses if `/etc/darts/compose.yaml` does not match
`deploy/compose.yaml` in the checkout you are deploying from. That is the one
seam in the division of labour: change the compose file here — a mount for #30's
share, a different published port — and the Pi keeps the old one until bootstrap
is re-run. Nothing about the deploy would look wrong; the container would simply
not have the mount. The error names the fix:

```
error: /etc/darts/compose.yaml on pi@darts.local differs from deploy/compose.yaml;
       re-run scripts/bootstrap-pi.sh there
```

Re-running `sudo scripts/bootstrap-pi.sh` replaces `compose.yaml` every time,
deliberately unlike `darts.env`, which is yours to edit and is never clobbered.

It needs **no Node and no Python of its own**. Every database command runs
inside the image, as uid 1000, with `--entrypoint` replacing the uvicorn
launcher:

```
docker run --rm -v /var/lib/darts:/var/lib/darts \
  --entrypoint darts-backup darts:<sha> /var/lib/darts/darts.db
```

Forgetting `--entrypoint` starts a web server instead of doing what you asked.

> **Never run these under `sudo`.** That is the same WAL-sidecar trap as the
> warning at the top of this document, and it is why `deploy.sh` needs no root
> on the Pi at any point. If a deploy ever seems to need `sudo`, something is
> wrong with the ownership of `/var/lib/darts`, not with the deploy.

To drive Compose by hand — reading the root-owned file needs no privileges:

```
docker compose -f /etc/darts/compose.yaml ps
```

### How rollback picks its target

The sha `/api/healthz` reports *before* the deploy touches anything. That is the
build demonstrably serving traffic a moment ago, which is a better answer than
"the newest image on the box" — after a failed deploy, the newest image is the
broken one. If nothing was answering, the most recently built image that is not
the one being deployed is used instead.

Build order comes from an `org.darts.built-at` label, not from
`docker image ls`. Docker's `Created` timestamp is unusable here: BuildKit
copies it from the cached parent, so once the layer cache is warm every image
claims the same instant. Images predating the label sort last.

> **Rollback restores the image, not the schema.** The migration has already run
> by the time health is checked. Migrations are additive, so an older image
> generally still serves a newer schema — but a bad *migration* is not something
> an image rollback can fix. That is what the backup from step 4 is for:
> `darts-restore` against the newest file in `/var/lib/darts/backups`.

## Manual verification checklist

Everything below needs the physical Pi. Each step gives the command and what a
pass looks like. The automated half of this ticket -- the dry-run assertions and
the image build -- is covered by CI and by `tests/deploy/`.

### 1. Bootstrap is idempotent

```
sudo scripts/bootstrap-pi.sh          # first run
sudo scripts/bootstrap-pi.sh          # second run
```

**Expect:** the first run installs Docker and creates the directories. The
second exits 0 and reports `already done:` for every step except the `chown`s,
which are re-applied deliberately. No errors, and no duplicate lines in
`/etc/apt/sources.list.d/docker.list`.

**Check the env file was not clobbered:** edit a value in
`/etc/darts/darts.env`, re-run the script, and confirm your edit survives.

### 2. The container is non-root and can still write

```
docker compose -f deploy/compose.yaml up -d
docker compose -f deploy/compose.yaml exec darts id
ls -ln /var/lib/darts
```

**Expect:** `uid=1000(darts) gid=1000(darts)`, and `darts.db` appearing in
`/var/lib/darts` owned by `1000 1000`.

### 3. Health

```
curl -s localhost:8000/api/healthz
docker ps --format '{{.Names}}\t{{.Status}}'
```

**Expect:** `{"status":"healthy",...}` with the `git_sha` of the image you
built, and `Up N seconds (healthy)` — not `(health: starting)` after the 20s
start period, and never `(unhealthy)`.

### 4. Logs are readable

```
docker logs darts | tail
```

**Expect:** one line per event in logfmt, e.g.

```
ts=2026-09-25T15:17:08.788Z level=INFO logger=darts.api.main request_id=- msg="boot check complete" database=/var/lib/darts/darts.db state=healthy
```

### 5. Clean shutdown checkpoints the WAL

```
docker compose -f deploy/compose.yaml stop
docker logs darts | tail -5
ls -l /var/lib/darts/
```

**Expect:** a line reading
`msg="shutdown checkpoint" truncated=true busy=false`, and **no `darts.db-wal`
file, or one of zero length.** SQLite removes the WAL entirely when the last
connection closes, so its absence is a pass — asserting the file still exists
would be asserting the wrong thing (see `docs/durability.md`).

`truncated=false` or `busy=true` is a failure: the checkpoint did not complete
and `stop_grace_period` may be too short.

### 6. Crash recovery

```
docker compose -f deploy/compose.yaml up -d
docker inspect -f '{{.RestartCount}}' darts
# kill the server process inside the container
docker compose -f deploy/compose.yaml exec -u 0 darts kill -9 1
sleep 15
docker inspect -f '{{.State.Status}} {{.RestartCount}}' darts
```

**Expect:** `running` with `RestartCount` incremented, and
`curl localhost:8000/api/healthz` answering again.

> **`docker kill` does not do this.** #28's criterion names `docker kill`, but
> Docker Engine records an explicit `docker kill` as a *manual* stop and does
> not apply the restart policy to it — verified on Engine 29.5.2 with both
> `unless-stopped` and `always`. Killing the process *inside* the container is
> a genuine crash and does restart. See the note in the #28 PR.

### 7. Replacing the image leaves the database untouched

```
sudo sqlite3 -readonly /var/lib/darts/darts.db 'select count(*) from players;'
# ... build and load a new image, then:
docker compose -f deploy/compose.yaml up -d --force-recreate
curl -s localhost:8000/api/version
curl -s localhost:8000/api/players
```

**Expect:** `git_sha` changes to the new build; the player rows are identical,
same ids and same `created_at`. (Then re-read the warning at the top of this
document about sidecar ownership if you ran `sqlite3` as root.)

### 8. Full power cycle

**Pull the power cord. Wait. Plug it back in.** Do not use `reboot` — the point
is to prove an unclean stop is survivable.

```
curl -s localhost:8000/api/healthz
```

**Expect:** the app answers with `"status":"healthy"` with no manual
intervention at all. `"status":"restored"` means the boot check repaired the
database, which is a pass for this step but worth reading
`docs/durability.md` about. `"status":"degraded"` is a failure.

### 9. Reachable from the phone

```
hostname -I
```

Open `http://<that-address>:8000/` on the iPhone, on the same LAN.

**Expect:** the app loads and a leg can be scored end to end. This overlaps
#32's device QA pass and is signed off there.

## The broken-build rollback drill

Required by #29, and performed once. **The run recorded below was against a
stand-in Linux host over SSH, not on the Pi** — an aarch64 VM with Docker
29.5.2, systemd and the same directory layout. It is a fair rehearsal of the
mechanics and it is not the Pi: the disk is SSD-backed rather than an SD card,
so every duration here is optimistic, and the host is Ubuntu 24.04 rather than
Raspberry Pi OS Bookworm. Re-run it on the hardware.

### Running it

Break the application deliberately, on a throwaway branch — a module-level
`raise` in `backend/darts/api/main.py` is enough, and is a truer simulation than
killing a container, because it produces an image that builds cleanly and then
cannot serve:

```
git checkout -b broken-drill
# add `raise RuntimeError("drill")` after the imports in backend/darts/api/main.py
git commit -am "TEMPORARY: broken build for the rollback drill"
scripts/deploy.sh --host <target> --skip-tests
```

`--skip-tests` is not cheating. It is the realistic scenario: a broken build only
reaches the Pi if somebody skipped the gates.

Afterwards, delete the branch. The broken image is left on the target and gets
culled by ordinary pruning within a few deploys.

### What happened

Deploying `69123c6` (broken) over `1d236da` (healthy):

```
==> currently serving: 1d236da
==> ... darts-backup ... wrote /var/lib/darts/backups/darts-20260925T165045Z.db
        (schema version 3, 6 row(s)); pruned 1
==> ... darts-migrate ... schema version 3; applied 0 migration(s); 4 view(s)
==> rollback target if this fails: 1d236da
==> on_target docker tag darts:69123c6 darts:latest
==> on_target docker compose -f /etc/darts/compose.yaml up -d
 Container darts Recreated
warning: target did not report sha 69123c6 after 30 attempt(s)
warning: the new build did not report 69123c6 in time; rolling back to 1d236da
==> on_target docker tag darts:1d236da darts:latest
==> on_target docker compose -f /etc/darts/compose.yaml up -d
 Container darts Recreated
warning: rolled back to 1d236da; /api/healthz reports it after 31s
error: deploy of 69123c6 failed and was rolled back to 1d236da
```

Exit status 1.

**31 seconds**, against the 40-second budget. The shape of that number matters
more than the number: 29s of it is the health poll itself — 30 attempts one
second apart, which is the ticket's own figure — and the retag, restart and
re-verification took about 2s on top. The headroom is in the poll, not in the
recovery, so the budget is met because the old image is already on the box and
starting it is fast. On slower storage the ~2s tail grows; the 29s does not.

Afterwards, `/api/healthz` reported the previous sha and the data was untouched:

```
{"status":"healthy","schema_version":3,"git_sha":"1d236da", ...}
```

### Checking the database came through it

Compare before and after a deploy, a rollback, or both. The inode shows the file
was written in place rather than replaced; the digest shows the contents did not
change.

```
ssh <target> stat -c 'inode=%i size=%s' /var/lib/darts/darts.db
```

For the contents, from inside the image — so the Pi needs no Python and no
`sqlite3`, and the database is opened read-only through a URI so it cannot
create WAL sidecars:

```
docker run --rm -v /var/lib/darts:/var/lib/darts \
  --entrypoint python darts:latest -c '
import hashlib, sqlite3
c = sqlite3.connect("file:/var/lib/darts/darts.db?mode=ro", uri=True)
t = [r[0] for r in c.execute(
    "select name from sqlite_master where type=\"table\" "
    "and name not like \"sqlite_%\" order by name")]
d = hashlib.sha256()
for n in t:
    rows = c.execute("select * from " + n).fetchall()
    d.update(("%s:%d\n" % (n, len(rows))).encode())
    for r in sorted(repr(x) for x in rows):
        d.update(r.encode())
print(d.hexdigest())'
```

Measured across a successful deploy, a same-sha redeploy, a failed deploy and
the rollback that followed it: **inode unchanged, size unchanged, digest
unchanged**, and the three seeded players kept identical ids and `created_at`.

> **The raw bytes of `darts.db` do change, and that is correct.** #29 asks for
> "byte-identical"; taken literally that is unachievable on any deploy that
> restarts the app. Measured across one deploy that applied no migration at all:
> `sha256` went from `7fcfcd04…` to `229e1d92…` while the logical digest, the
> inode and the size were all identical. Stopping the container checkpoints the
> WAL, which folds committed frames into the main file — the data is the same,
> the file is not. Compare contents, not bytes.

### The tag budget survives a failure

Re-run after the drill, because a failed deploy is the path where the ordinary
prune never executes:

```
docker image ls darts --format '{{.Tag}}' | grep -v '^latest$' | wc -l
```

**Expect 5**, and the failed sha to be gone — `deploy.sh` deletes the broken
image once the rollback is confirmed healthy. Measured: 5 tags after a failed
drill, `darts:<broken sha>` absent, previous sha serving.

The first version of this left **six**. The prune never ran, because the
rollback path exits non-zero, and simply moving the prune onto that path would
have been worse: it protects the sha being deployed, which there is the broken
build, and that is also the newest image — so a newest-first prune would have
kept the one artefact known to be bad and culled a working one to fit it.

### What this drill does not cover

- A **bad migration**, as opposed to a broken image. Rollback restores the
  image; the schema stays migrated. Use the backup.
- **Transfer time over a real network.** On the stand-in the Mac's Docker CLI and
  the target share one daemon, so `deploy.sh` legitimately skipped the transfer.
  The pipeline was verified separately: `docker save` emits 57,727,488 bytes, and
  `docker save … | ssh … docker load` reported `Loaded image` and materialised a
  tag that had been deleted first. On a Pi over LAN to an SD card this is the
  dominant cost of a deploy and is not represented by any timing here.
- **A Pi with no system Python.** The stand-in is Ubuntu, which ships one. What
  was shown is that the app is not installed on the host — no `darts-backup`, no
  `darts-migrate`, no `node`, no `npm` — and that no deploy step invokes an
  interpreter on the target.

## The snapshot share (#30)

A read-only, passwordless SMB share of `/srv/darts-share`, so a Mac can open the
data in DB Browser, DBeaver, TablePlus, DuckDB or pandas. In Finder:
**Go > Connect to Server**, `smb://darts.local/darts`, **Connect As: Guest**.
What the files are and how to query them is at the top of `docs/data-model.md`.

Everything is installed by `sudo scripts/bootstrap-pi.sh`: the `samba` and
`avahi-daemon` packages, `deploy/smb-darts.conf` as the whole of
`/etc/samba/smb.conf`, the Avahi advertisement, and the timer. **An
already-bootstrapped Pi needs one re-run** to pick all of that up, and #30 also
edited a comment in `compose.yaml`, so `deploy.sh` refuses to deploy until that
re-run has happened (the error names the fix).

Like `compose.yaml`, every file here is a repository artefact: replaced on every
run, with the service that reads it restarted on every run. Debian's own
`smb.conf` is kept once, as `smb.conf.debian-orig`, and never overwritten.

**Only the snapshot is shared, never `/var/lib/darts`.** Nothing on the host
opens the live database either: the timer asks the app to take the snapshot.

```
systemctl list-timers darts-snapshot.timer   # when it last ran and runs next
journalctl -u darts-snapshot.service         # each run logs the manifest it got
cat /srv/darts-share/snapshot.json           # created_at is the age of the copy
testparm -s /etc/samba/smb.conf              # what smbd will actually use
```

If the app is down, the timer's POST fails into the journal and the share keeps
serving the last good snapshot; `created_at` says how old it is. The dart that
wins a match also republishes it, so a finished match is on the share within
seconds rather than at the next tick.

**`samba-ad-dc.service` shows as enabled and is not running.** The package enables
it, and it refuses to start on anything that is not an Active Directory domain
controller (`start condition unmet`). Harmless; bootstrap leaves it alone.

**The hostname has to be `darts`.** Avahi publishes the machine's own name, so
`darts.local` resolves only if that is what the Pi is called. Choose it in
Raspberry Pi Imager when flashing. Bootstrap warns, and does not rename, if it
is something else; the share then works at `smb://<that name>.local/darts`.

### Checklist on the Pi

Continues the numbering above. Needs the Pi and a Mac on the same LAN.

#### 10. The share mounts read-only from Finder

Finder: **Go > Connect to Server**, `smb://darts.local/darts`, **Guest**.

**Expect:** it mounts, showing exactly `darts-latest.db` and `snapshot.json`.
The Pi also appears under **Locations** in the sidebar without typing anything
(that is the Avahi advertisement). No `.darts-*` temporaries are ever visible.

#### 11. Writes fail

Drag any file onto the mounted share, and from Terminal:

```
touch /Volumes/darts/x
rm /Volumes/darts/snapshot.json
```

**Expect:** Finder refuses the copy, and both commands fail with a permission or
read-only error. Nothing on the Pi changes: `ls -la /srv/darts-share`.

#### 12. DB Browser opens it without a lock error

Open `/Volumes/darts/darts-latest.db` in DB Browser for SQLite, browse `darts`,
and leave it open while the next timer run happens (step 14).

**Expect:** no lock error, no `darts-latest.db-wal` appearing in the share, and
nothing new on the Pi: `ls -la /var/lib/darts` still shows only `darts.db` and
`backups/` (plus `darts.db-wal`/`-shm` owned by 1000 while the app has a
connection open). If DB Browser offers to open read-write, the share will refuse
it anyway.

#### 13. DuckDB reads it

```
duckdb -c "ATTACH '/Volumes/darts/darts-latest.db' AS darts (TYPE sqlite, READ_ONLY); SELECT count(*) FROM darts.darts;"
```

**Expect:** the same count as `snapshot.json`'s `row_counts.darts`. The ticket
writes this as `ATTACH 'darts-latest.db' (READ_ONLY); SELECT count(*) FROM
darts;`. Without `AS`, DuckDB names the attached catalog after the file,
`darts-latest`, and does not switch to it, so a bare `FROM darts` looks in the
in-memory database instead. Measured with DuckDB 1.5.5 on a Mac, against a
50,008-dart snapshot file (not through the mount): the ticket's form fails with
`Catalog Error: Table with name darts does not exist! Did you mean
"darts-latest.darts"?`, and the form above returns 50008, matching
`snapshot.json`, leaving no sidecar. The views read through DuckDB too. Reading
a SQLite file makes DuckDB download its `sqlite_scanner` extension on first use,
so do this once with internet access.

#### 14. The manifest advances every five minutes

```
ssh pi@darts.local 'for i in 1 2 3; do grep created_at /srv/darts-share/snapshot.json; sleep 300; done'
```

**Expect:** three `created_at` values five minutes apart (to the second, give or
take one).

#### 15. A finished match is on the share within seconds

Finish a match on the phone, then on the Mac within a few seconds:

```
cat /Volumes/darts/snapshot.json
```

**Expect:** `created_at` is the moment the match ended, not the last five-minute
tick, and `docker logs darts | grep "snapshot after match"` names the match.

### The rehearsal on the stand-in

**Everything below was verified against a stand-in Linux host over SSH, not on
the Pi**: the same aarch64 Ubuntu 24.04 VM as #29's drill, with systemd 255 and
Samba 4.19 (Bookworm has 4.17). It proves the host half works on a systemd host.
It does not prove anything about Finder, DB Browser or `darts.local`, because
the VM is not routable from the Mac and its hostname is `colima`.

Recorded 2026-09-29, with branch `30-samba-snapshot-share` deployed as `7d6f03b`.

**The timer.** Four consecutive runs from the journal
(`journalctl -u darts-snapshot.service -o short-precise`), with `snapshot.json`
read back after each:

| Started | Since previous | Run took | `created_at` | Mode |
| --- | --- | --- | --- | --- |
| 20:14:10.000 | -- | 24 ms | `20260930T001410Z` | 644 |
| 20:19:10.800 | 300.80 s | 33 ms | `20260930T001910Z` | 644 |
| 20:24:11.595 | 300.80 s | 34 ms | `20260930T002411Z` | 644 |
| 20:29:12.491 | 300.90 s | 17 ms | `20260930T002912Z` | 644 |

Each interval is about 0.8 s longer than five minutes, because
`OnUnitActiveSec` counts from the previous activation and each activation lands
a moment late. So the runs creep against the wall clock, by about four minutes a
day, and never bunch up. That is the trade for a monotonic timer, and it is fine
for a share nobody reads to the second. Started after boot, the timer ran
immediately: `systemctl restart` at 20:09:08, first run at 20:09:09.

**The pre-#30 file mode, reproduced.** Before the new image was deployed, the
timer ran against the old one and published `-rw------- 1000 1000`. A guest
`smbclient` against the stand-in's smbd then got
`NT_STATUS_ACCESS_DENIED opening remote file \snapshot.json`. After the deploy,
every published file was `-rw-r--r--`.

**Reads and writes over SMB.** No Mac could reach the VM, so a guest `smbclient`
ran in a container sharing the VM's network namespace, against the stand-in's
systemd-managed smbd. Only `darts` and `IPC$` were advertised. The
`darts-latest.db` it fetched passed `integrity_check`, was in journal mode
`delete`, and contained the match finished below; opening it left no sidecar.
`put`, `rm`, `mkdir` and `rename` were each refused with
`NT_STATUS_ACCESS_DENIED`, and so was `put` as a named user. The same config
under Bookworm's Samba 4.17.12, in a `debian:bookworm` container on the Mac,
also refused a `put` with the share directory at mode 777, so `read only = yes`
holds without the filesystem's help.

**The match-completion hook.** A best-of-one cricket match played over HTTP on
the host, 15 darts. The 14 darts before the winner did not touch `snapshot.json`.
The winning dart's round trip was 2.5 ms, and `snapshot.json` was replaced
**1.9 ms after its response arrived**. The container logged
`msg="snapshot after match" match_id=1 created_at=20260930T001210Z bytes=196608`.

**How long a snapshot takes.** On the stand-in's 196,608-byte database, five
`POST /api/admin/snapshot` round trips took 14.0, 6.4, 5.7, 5.2 and 4.5 ms. On a
50,008-dart database (3,572 cricket matches played through the API, 14,520,320
bytes), `snapshot.create` took 108–111 ms inside the image on the stand-in and
97–106 ms on an M-series Mac. `PRAGMA integrity_check` returned `ok` and the
copy's `journal_mode` was `delete`, so it has no WAL sidecar. An SD card will be
slower; run step 14 on the Pi for the real figure.

**Nothing outside the container opened the database.** After the timer runs and
the hook, `/var/lib/darts` held `darts.db` and `backups/`, both `1000 1000`, and
no root-owned sidecar.

**What the stand-in caught.** Bootstrap's first version *reloaded* smbd. The
package had already started smbd with the stock config, and a reload does not
rebind sockets, so `smb ports = 445` did not take effect and smbd was still
listening on 139. It now restarts; after the second run, only 445 was listening.
The package also enabled `nmbd`, which bootstrap disables because NetBIOS is off.

**The second bootstrap run was clean.** Every guarded step reported `already
done`: both packages, the kept `smb.conf.debian-orig` (not overwritten), smbd,
nmbd (disabled), Avahi, and the timer's enablement. The repository files were
reinstalled and smbd and the timer restarted, as designed. Guest reads and
refused writes behaved the same afterwards. Restarting the timer did *not* fire
it this time, because the service had run before; the next run stayed five
minutes after the last.
