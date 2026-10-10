# Runbook: from a blank Pi to a released, deployed scorekeeper

Every step needed to set up, release, deploy, look after and reset the
scorekeeper, in the order you would do them. Follow it top to bottom for a new
Pi, or jump to the section you need. Each step has the exact command and what a
pass looks like.

This page is the *how*. The *why* lives in [deploy.md](deploy.md),
[ops.md](ops.md), [dr.md](dr.md) and [durability.md](durability.md), and this
page links there instead of repeating it. You should never need them to get
something done.

## Conventions

- **Where a command runs.** Commands starting `scripts/` run **on the Mac**, from
  the root of the repository checkout. Commands starting `ssh jackm@darts.local`
  run on the Pi from the Mac. Commands under "on the Pi" run in an SSH session
  there.
- **The Pi user is `jackm`, uid 1000.** Everything on the Pi that holds data is
  owned by uid 1000, and the container runs as uid 1000. If you set up a Pi
  with a different user, it **must** be uid 1000 (`id -u`). Substitute it
  wherever you see `jackm`, and pass it to bootstrap as `BOOTSTRAP_USER`.
- **The app is at `https://darts.local/`.** Port 8000 is on the Pi's loopback
  only and cannot be reached from the LAN. From the Mac, every `curl` trusts our
  own root explicitly. Set this once per terminal:

  ```bash
  export DARTS_CA="$HOME/Library/Application Support/darts-tls/darts-root.crt"
  ```

- **Never open the database as root.** Anything that opens the database on the
  Pi runs inside the image, as uid 1000. Never use `sudo sqlite3`, and never
  `sudo docker run … darts-*`. If it happens anyway, the next start fails with
  `attempt to write a readonly database`. The fix is
  `sudo chown 1000:1000 /var/lib/darts/darts.db-*`.
- **Never `docker compose down`, never delete `/var/lib/darts`.** Use `stop` and
  `start`. Nothing on this page deletes data: when something is replaced, it is
  moved aside.

---

## Part 1: one-time setup

Do this once per Mac, once per Pi and once per phone. A Pi that is already
running skips straight to [Part 2](#part-2-cutting-a-release).

### 1.1 The Mac

You need `git`, `gh` (logged in: `gh auth status`), `uv`, Docker and an SSH key.
Docker on this Mac is **colima**:

```bash
brew install git gh uv colima docker docker-buildx docker-compose
mkdir -p ~/.docker/cli-plugins
ln -sfn "$(brew --prefix)/opt/docker-buildx/bin/docker-buildx" ~/.docker/cli-plugins/docker-buildx
ln -sfn "$(brew --prefix)/opt/docker-compose/bin/docker-compose" ~/.docker/cli-plugins/docker-compose
colima start
gh auth login
git clone https://github.com/jackmcnulty/pi-darts-scorekeeper.git
cd pi-darts-scorekeeper
uv sync
```

The two `ln` lines matter. Homebrew's Docker plugins are not found on their
own: without buildx the build fails with "buildx component is missing".

**Pass:** `docker info --format '{{.Architecture}}'` says `aarch64`, and `uv run pytest -q -m "not perf" tests`
passes. `deploy.sh` runs that suite before every deploy.

`colima start` again after every Mac reboot. If `docker info` says it cannot
connect, colima is not running.

### 1.2 The certificate (on the Mac)

The phone needs HTTPS for the wake lock and the offline shell. The certificate
comes from a root of our own, made here, whose key never leaves the Mac.

```bash
scripts/make-cert.sh
```

**Pass:** `~/Library/Application Support/darts-tls/` holds `darts-root.crt`,
`darts-root.key`, `darts-leaf.crt` and `darts-leaf.key`. **Never copy
`darts-root.key` anywhere.** Only the two `darts-leaf.*` files go to the Pi, and
only `darts-root.crt` goes to the phone.

### 1.3 Flash the Pi

Use Raspberry Pi Imager:
- **Raspberry Pi OS (64-bit), Bookworm.** Lite is enough.
- Open its settings (the gear icon) and set:
  - hostname **`darts`**
  - user **`jackm`** (the first user Imager creates is uid 1000)
  - SSH on, with your Mac's public key
  - Wi-Fi, unless the Pi is on Ethernet

Boot it, then from the Mac:

```bash
ssh jackm@darts.local 'uname -m; . /etc/os-release; echo $VERSION_CODENAME; id -u'
```

**Pass:** `aarch64`, `bookworm`, `1000`. If `darts.local` does not resolve, the
hostname is wrong. Fix it before going on, because Avahi and the share depend
on it.

### 1.4 Bootstrap the Pi

Bootstrap installs Docker, Caddy, Samba, Avahi and the snapshot timer. It also
creates the directories and writes `/etc/darts/{darts.env,compose.yaml}`. It is
idempotent, and it is the only step that needs root.

```bash
ssh jackm@darts.local 'git clone https://github.com/jackmcnulty/pi-darts-scorekeeper.git'
ssh jackm@darts.local 'mkdir -p -m 700 darts-tls'
scp "$HOME/Library/Application Support/darts-tls/darts-leaf."{crt,key} jackm@darts.local:darts-tls/
ssh -t jackm@darts.local 'cd ~/pi-darts-scorekeeper && sudo BOOTSTRAP_USER=jackm scripts/bootstrap-pi.sh --tls-from ~/darts-tls && rm -r ~/darts-tls'
```

To see the plan without changing anything, run `scripts/bootstrap-pi.sh
--dry-run` on the Pi first.

The first run adds `jackm` to the `docker` group, which takes effect at the next
login, so open a new SSH session before going on. Then run it a second time,
without `--tls-from`:

```bash
ssh -t jackm@darts.local 'cd ~/pi-darts-scorekeeper && sudo BOOTSTRAP_USER=jackm scripts/bootstrap-pi.sh'
```

**Pass:**
- The second run exits 0 and reports `already done:` / `skip:` for every step
  except the `chown`s, and `skip: certificate valid until …`.
- `ssh jackm@darts.local docker ps` works without sudo.
- `ssh jackm@darts.local 'ls -ld /var/lib/darts'` shows `jackm jackm`.

`/etc/darts/darts.env` is yours to edit and a re-run never overwrites it.
`compose.yaml` and the Caddyfile are overwritten on every run, on purpose.

### 1.5 The first deploy

Follow [Part 3](#part-3-deploying-a-release). On a fresh Pi, health reports
`"detail":null` and not "created a new database", because the deploy migrates
and so creates the file before the app starts. Then check HTTPS from the Mac:

```bash
curl -s --cacert "$DARTS_CA" https://darts.local/api/healthz
curl -sI http://darts.local/ | head -3
```

**Pass:** `"status":"healthy"`, and the second command is a redirect to
`https://darts.local/`.

### 1.6 The phone (iPhone)

1. **Send the root.** In Finder, open
   `~/Library/Application Support/darts-tls/` (**Go → Go to Folder**) and AirDrop
   **`darts-root.crt`** to the phone. Send only that file. The phone says
   **Profile Downloaded**.
2. **Install it:** **Settings → General → VPN & Device Management → Darts LAN
   root → Install**.
3. **Trust it:** **Settings → General → About → Certificate Trust Settings**,
   and switch on **Darts LAN root (darts.local only)**. Installing and trusting
   are separate steps, and iOS needs both.
4. **Add the app:** in Safari open `https://darts.local/`, then **Share → Add to
   Home Screen → Add**. If an old **Darts** icon from `http://darts.local:8000`
   exists, delete it first. iOS treats it as a different app.

**Pass:** Safari opens `https://darts.local/` with no certificate warning, and
the home-screen icon opens full-screen. The full phone checklist is
[ops.md → The #71 checklist](ops.md#the-71-checklist-on-the-phone).

---

## Part 2: cutting a release

A release is an annotated git tag `vX.Y.Z` on `main` plus a GitHub release. The
version lives in exactly one place, `backend/darts/__init__.py`
(`__version__`). The package version is read from it, and `/api/healthz` and
`/api/version` report it. Use semantic versioning:
- **patch** for fixes
- **minor** for new features
- **major** for a change to what the phone or the data means

### 2.1 Bump the version, by PR

Never commit to `main`. On a branch from an up-to-date `main`:

```bash
git fetch origin
git switch -c release-X.Y.Z origin/main
# edit backend/darts/__init__.py:  __version__ = "X.Y.Z"
git commit -am "Release X.Y.Z"
git push -u origin release-X.Y.Z
gh pr create --base main --head release-X.Y.Z --title "Release X.Y.Z" --body "Bumps __version__ to X.Y.Z."
```

Pass `--head` explicitly. A branch made from `origin/main` tracks `main`, and
without `--head` `gh` thinks the head is `main` and refuses.

**Pass:** CI is green on the PR. Merge it with a squash, as every PR here is
merged:

```bash
gh pr merge <number> --squash
```

### 2.2 Tag the merge commit

```bash
git switch main
git pull --ff-only
git log --oneline -1                       # the "Release X.Y.Z (#NN)" commit
grep __version__ backend/darts/__init__.py # __version__ = "X.Y.Z"
git tag -a vX.Y.Z -m "X.Y.Z"
git push origin vX.Y.Z
```

**Pass:** `git ls-remote --tags origin vX.Y.Z` prints the tag. Never move a tag
that has been pushed. If a release is wrong, cut the next patch version.

### 2.3 Publish the GitHub release

```bash
gh release create vX.Y.Z --verify-tag --title "vX.Y.Z" --generate-notes
```

`--generate-notes` lists every PR merged since the previous tag, or every PR
there is for the first release. Add a short summary at the top in the web
editor if you like. There are no build artifacts to attach: the Pi gets its
image from `deploy.sh`, built from the tag (Part 3).

**Pass:** `gh release view vX.Y.Z` shows it, marked `Latest`.

---

## Part 3: deploying a release

`deploy.sh` does the whole deploy in one command:
1. Refuses a dirty worktree.
2. Runs the backend tests.
3. Builds the arm64 image here, stamped with the commit's short sha.
4. Streams the image to the Pi over SSH.
5. Stops the app and backs up the database.
6. Migrates from inside the new image, then starts it.
7. Polls `/api/healthz` for the new sha, and rolls back to the previous image
   if the new sha does not appear within 30 s.

It needs no root and writes no files on the Pi.

### 3.1 Before you deploy

```bash
colima start                                   # if docker info fails
git fetch origin --tags
git switch --detach vX.Y.Z                     # deploy the tag, not a branch
git status --short                             # must print nothing
ssh jackm@darts.local 'cat /etc/darts/compose.yaml' | diff - deploy/compose.yaml && echo compose ok
ssh jackm@darts.local 'cat /etc/caddy/Caddyfile'    | diff - deploy/Caddyfile    && echo caddy ok
```

**If either diff prints anything**, the release changed host configuration and
the Pi needs bootstrap re-run before the deploy. `deploy.sh` refuses a
mismatched `compose.yaml` and names this fix:

```bash
ssh -t jackm@darts.local 'cd ~/pi-darts-scorekeeper && git fetch --tags && git switch --detach vX.Y.Z && sudo BOOTSTRAP_USER=jackm scripts/bootstrap-pi.sh'
```

Optionally, take a copy off the Pi first. The deploy also backs up on the Pi
itself:

```bash
scripts/backup-pull.sh
```

### 3.2 Deploy

```bash
scripts/deploy.sh --host jackm@darts.local --dry-run    # the plan; changes nothing
scripts/deploy.sh --host jackm@darts.local
```

Expect a minute or two: tests, build and image transfer.

**Pass:** exit status 0. A deploy that failed and rolled back cleanly still
exits non-zero, and the Pi is then serving the previous build. Every flag is in
[deploy.md → Every flag](deploy.md#every-flag).

### 3.3 Verify

```bash
git rev-parse --short HEAD
curl -s --cacert "$DARTS_CA" https://darts.local/api/version
curl -s --cacert "$DARTS_CA" https://darts.local/api/healthz
ssh jackm@darts.local 'docker ps --format "{{.Names}}\t{{.Image}}\t{{.Status}}"'
```

**Pass:**
- `/api/version` is `{"version":"X.Y.Z","git_sha":"<the sha above>"}`.
- Health is `"status":"healthy"` with `"detail":null`. **Not** "created a new
  database" on a Pi that had history; see
  [dr.md → The one thing to know first](dr.md#the-one-thing-to-know-first).
- `docker ps` shows `darts  darts:latest  Up … (healthy)`.
- On the phone, the app opens and shows your players and history.

Then put the Mac's checkout back on a branch:

```bash
git switch main
```

### 3.4 Roll back

Use this for a build that came up healthy but is wrong in a way health can't
see. The image is rolled back; the schema is not.

```bash
scripts/rollback.sh --host jackm@darts.local --list         # shas on the Pi, newest first
scripts/rollback.sh --host jackm@darts.local                # to the previous one
scripts/rollback.sh --host jackm@darts.local --to <sha>     # to a specific one
```

**Pass:** `curl -s --cacert "$DARTS_CA" https://darts.local/api/version` reports
the sha you rolled back to. Migrations are additive, so an older image serves a
newer schema. A bad *migration* needs [5.2 Restore](#52-restore-a-copy-onto-the-pi)
from the backup the deploy took, which is the newest file in
`/var/lib/darts/backups/`.

---

## Part 4: day to day

| Task | Command (on the Mac) | Pass |
| --- | --- | --- |
| Is it up? | `curl -s --cacert "$DARTS_CA" https://darts.local/api/healthz` | `healthy`, HTTP 200. `restored` or `degraded`: [dr.md → Scenario 2](dr.md#scenario-2-database-corruption). |
| Logs | `ssh jackm@darts.local 'docker logs --since 1h darts'` | logfmt lines; look for `level=ERROR`. |
| Follow logs | `ssh jackm@darts.local 'docker logs -f darts'` | Ctrl-C to stop. |
| Restart | `ssh jackm@darts.local 'docker compose -f /etc/darts/compose.yaml restart'` | logs show `shutdown checkpoint truncated=true`, then `boot check complete … state=healthy`. |
| Container state | `ssh jackm@darts.local 'docker ps --format "{{.Names}}\t{{.Status}}"'` | `Up … (healthy)`. |
| Disk | `ssh jackm@darts.local 'df -h /var/lib/darts'` | plenty free. |

There is no systemd unit for the app. `restart: unless-stopped` starts it at
boot and after a crash. A container you `stop` stays stopped, across reboots
too, until you `start` it.

---

## Part 5: backups and restore

### 5.1 Backups

The **automatic, on-Pi** backups:
- run on every start and every 24 hours, into `/var/lib/darts/backups/`
- keep 24 hourly and 30 daily
- are skipped for a database with no matches

To list them:

```bash
ssh jackm@darts.local 'ls -lt /var/lib/darts/backups | head'
```

**By hand, on the Pi**, before anything you are nervous about:

```bash
ssh jackm@darts.local 'docker run --rm -v /var/lib/darts:/var/lib/darts --entrypoint darts-backup darts:latest /var/lib/darts/darts.db'
```

**Pass:** `wrote /var/lib/darts/backups/darts-<timestamp>.db (schema version 3, …)`.

**Off the Pi, onto the Mac.** This is the only copy that survives the SD card
dying. Nothing runs it on a schedule, so run it after any session you would
mind losing:

```bash
scripts/backup-pull.sh
```

**Pass:** `==> pulled darts-….db (… bytes, schema version 3, integrity ok)`.
Copies go to `~/Library/Application Support/darts-backups/`, newest 30 kept.
Every message it can print is in [dr.md → Taking a copy](dr.md#taking-a-copy).

### 5.2 Restore a copy onto the Pi

Stop the app first, or `restart: unless-stopped` can start it in the gap and
give you a healthy, empty scorekeeper. `darts-restore` checks the copy's
integrity, and moves the database it replaces aside to
`darts.replaced-<ts>.db`.

```bash
COPY=darts-YYYYMMDDTHHMMSSZ.db     # in ~/Library/Application Support/darts-backups/
ssh jackm@darts.local 'docker compose -f /etc/darts/compose.yaml stop'
ssh jackm@darts.local 'mkdir -p /tmp/darts-restore'
scp ~/Library/Application\ Support/darts-backups/$COPY jackm@darts.local:/tmp/darts-restore/
ssh jackm@darts.local "chmod 644 /tmp/darts-restore/$COPY && docker run --rm -v /var/lib/darts:/var/lib/darts -v /tmp/darts-restore:/restore:ro --entrypoint darts-restore darts:latest /var/lib/darts/darts.db --from /restore/$COPY --force"
ssh jackm@darts.local 'docker compose -f /etc/darts/compose.yaml start && rm -rf /tmp/darts-restore'
```

To restore an on-Pi backup instead, skip the `scp` and use
`--from /var/lib/darts/backups/<file>` without the second `-v`.

**Pass:** a restore passes when the data is back, never just because health is
green.
1. Health says `healthy` with `"detail":null`, **not** "created a new database".
2. The row counts match the copy:

   ```bash
   sqlite3 -readonly ~/Library/Application\ Support/darts-backups/$COPY 'select count(*) from darts; select count(*) from matches;'
   curl -s --cacert "$DARTS_CA" https://darts.local/api/export/darts.csv   | tail -n +2 | wc -l
   curl -s --cacert "$DARTS_CA" https://darts.local/api/export/matches.csv | tail -n +2 | wc -l
   ```

3. The phone can add a player or throw a dart.

Scenarios (lost card, corruption, accidental deletion) are in [dr.md](dr.md).

---

## Part 6: a fresh start (wipe the data)

This empties the scorekeeper: no players, no matches, no darts. **Nothing is
deleted.** The database, every on-Pi backup and the share's snapshot are moved
into a dated `wiped-<timestamp>` directory on the Pi, and a verified copy goes
to the Mac first. Old backups are moved out, not left in place, for a reason:
until the first new match, the automatic backup does not run. In that window, a
corruption restore would bring the old data back.

### 6.1 Take a copy, and keep it out of rotation

`backup-pull.sh` keeps only the newest 30 copies, so the pre-wipe copy would
eventually be pruned. Put it somewhere retention does not look:

```bash
scripts/backup-pull.sh
mkdir -p ~/Library/Application\ Support/darts-backups-archive
cp -p "$(ls -t ~/Library/Application\ Support/darts-backups/darts-*.db | head -1)" ~/Library/Application\ Support/darts-backups-archive/pre-wipe-$(date -u +%Y%m%dT%H%M%SZ).db
ls -l ~/Library/Application\ Support/darts-backups-archive/
```

**Pass:** `integrity ok` from the pull, and the archive holds a file of the
same size.

### 6.2 Stop, move everything aside, start

```bash
ssh jackm@darts.local 'docker compose -f /etc/darts/compose.yaml stop'
ssh jackm@darts.local 'bash -s' <<'EOF'
set -euo pipefail
shopt -s nullglob
A=/var/lib/darts/wiped-$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$A/backups" "$A/share"
cd /var/lib/darts
for f in darts.db darts.db-wal darts.db-shm; do
  if [ -e "$f" ]; then mv -v "$f" "$A/"; fi
done
for f in darts.replaced-*.db darts.corrupt-*.db; do mv -v "$f" "$A/"; done
for f in backups/*; do mv -v "$f" "$A/backups/"; done
for f in /srv/darts-share/*; do mv -v "$f" "$A/share/"; done
echo "archived to $A"
EOF
ssh jackm@darts.local 'docker compose -f /etc/darts/compose.yaml start'
```

All of this runs as `jackm`, with no `sudo`: every file involved is owned by
uid 1000.

### 6.3 Verify it is empty, and still works

```bash
curl -s --cacert "$DARTS_CA" https://darts.local/api/healthz
curl -s --cacert "$DARTS_CA" https://darts.local/api/players
curl -s --cacert "$DARTS_CA" 'https://darts.local/api/matches?limit=1'
ssh jackm@darts.local 'ls -la /var/lib/darts /var/lib/darts/backups'
```

**Pass:**
- Health is `"status":"healthy"` with `"detail":"created a new database"`. That
  is the one time this is the answer you want.
- `/api/players` is `[]`, and `/api/matches` has `"total":0`.
- `/var/lib/darts` holds a new `darts.db` and the `wiped-…` directory, and
  `backups/` is empty. It stays empty until a match exists.
- Within five minutes the snapshot timer republishes `/srv/darts-share/` from
  the empty database.
- On the phone, the app opens to an empty home screen, and adding a player
  works.

### 6.4 Undoing a wipe

Restore the archived database with [5.2](#52-restore-a-copy-onto-the-pi), using
`--from /var/lib/darts/wiped-<timestamp>/darts.db` (no `scp`, no second `-v`).
Move the backups back with `mv /var/lib/darts/wiped-<timestamp>/backups/*
/var/lib/darts/backups/`. When you are sure you will never want it, deleting
the `wiped-…` directory is up to you.

---

## Part 7: renewing the certificate (every ~2 years)

The leaf certificate lasts 820 days, and bootstrap warns from 30 days before it
expires. Renewing reuses the root, so the phone needs nothing.

```bash
scripts/make-cert.sh
ssh jackm@darts.local 'mkdir -p -m 700 darts-tls'
scp "$HOME/Library/Application Support/darts-tls/darts-leaf."{crt,key} jackm@darts.local:darts-tls/
ssh -t jackm@darts.local 'cd ~/pi-darts-scorekeeper && git fetch origin --tags && git switch --detach vX.Y.Z && sudo BOOTSTRAP_USER=jackm scripts/bootstrap-pi.sh --tls-from ~/darts-tls && rm -r ~/darts-tls'
```

`vX.Y.Z` is the release the Pi is running (`/api/version`), so bootstrap
installs that release's host configuration and nothing newer.

**Pass:** bootstrap prints `skip: certificate valid until <a date ~820 days
out>`, and Safari still opens `https://darts.local/` with no warning.

---

## When something goes wrong

| Symptom | Cause | Fix |
| --- | --- | --- |
| `docker info`: cannot connect | colima is not running | `colima start` |
| deploy: `worktree has uncommitted changes` | dirty checkout | commit or stash; deploy a tag (3.1) |
| deploy: `compose.yaml … differs from deploy/compose.yaml` | the release changed host config | re-run bootstrap from the tag (3.1) |
| deploy exits non-zero and says it rolled back | the new build was not healthy within 30 s | `ssh jackm@darts.local 'docker logs --since 10m darts'`; the Pi is serving the old build |
| `attempt to write a readonly database` | something opened the DB as root | `ssh jackm@darts.local 'sudo chown 1000:1000 /var/lib/darts/darts.db-*'`, then restart |
| health says `created a new database` on a Pi with history | the database is missing | [dr.md → The one thing to know first](dr.md#the-one-thing-to-know-first) |
| `restored` or `degraded` | the boot check found damage | [dr.md → Scenario 2](dr.md#scenario-2-database-corruption) |
| curl exit 60 / Safari certificate warning | root missing or untrusted, or the leaf expired | Mac: check `$DARTS_CA`; phone: 1.6 step 3; expired: Part 7 |
| `http://darts.local:8000` refuses | by design since #71 | use `https://darts.local/` |
| `darts.local` does not resolve | the Pi is off, or its hostname is not `darts` | power it on; check 1.3 |
