# Deploying to the Pi

How the scorekeeper is packaged and what has to be checked on real hardware.
Ticket #28.

There is no systemd unit anywhere in this repository and there should not be
one. `restart: unless-stopped` in `deploy/compose.yaml` provides both halves of
what a unit would do -- start at boot and restart after a crash -- given that
`docker.service` is itself enabled at boot, which `bootstrap-pi.sh` does.

| File | Responsibility |
| --- | --- |
| `deploy/Dockerfile` | Three-stage build: Node, uv, runtime. Non-root, healthchecked. |
| `deploy/compose.yaml` | Restart policy, bind mounts, published port, shutdown grace. |
| `deploy/docker-entrypoint.sh` | `exec`s uvicorn so it is PID 1 and SIGTERM reaches it. |
| `deploy/darts.env.example` | Template for `/etc/darts/darts.env`. |
| `scripts/bootstrap-pi.sh` | Idempotent host setup. |
| `.dockerignore` | Allowlist. Keeps `var/darts.db` out of the image. |

Delivering an image to the Pi -- build, transfer, health check, rollback -- is
#29 and is not described here.

## The layout on the host

```
/var/lib/darts/darts.db       the live database
/var/lib/darts/backups/       darts-backup writes here
/srv/darts-share/             snapshots, shared read-only by #30
/etc/darts/darts.env          configuration, loaded by compose
```

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
