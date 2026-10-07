# Operating the scorekeeper

How to look after the Pi day to day: logs, restart, deploy, roll back, back up,
restore. Then the device checklist that signs v1 off. Ticket #32.

Each task gives the command and what a pass looks like. How the machinery
works is in [deploy.md](deploy.md), [durability.md](durability.md) and
[dr.md](dr.md); this page links there rather than repeating it. Commands
starting `scripts/` run on the Mac from the repository. Everything else runs
over `ssh pi@darts.local`.

**If your Pi user is not `pi`**, read every `pi@darts.local` here and in the
linked docs as your user, and bootstrap with
`sudo BOOTSTRAP_USER=<you> scripts/bootstrap-pi.sh`. That user **must be uid
1000** (`id -u`), because uid 1000 owns the data and runs the container. The
first user Raspberry Pi Imager creates always is.

> **The one rule.** Anything that opens the database on the Pi runs **inside
> the image, as uid 1000**: `docker run --rm -v /var/lib/darts:/var/lib/darts
> --entrypoint <tool> darts:latest …`. Never `sudo sqlite3`, never as root.
> Opening a WAL database creates `darts.db-wal` and `darts.db-shm` owned by
> whoever opened it, even read-only, and the next start then fails with
> `attempt to write a readonly database`. The fix, if it happens:
> `sudo chown 1000:1000 /var/lib/darts/darts.db-*`
> ([deploy.md → The layout on the host](deploy.md#the-layout-on-the-host)).

## Is it up?

```bash
curl -s http://darts.local:8000/api/healthz
```

| `status` | HTTP | Meaning |
| --- | --- | --- |
| `healthy` | 200 | Normal. `git_sha` is the build being served. |
| `restored` | 200 | The boot check found damage and restored the newest good on-Pi backup. Read [dr.md → Scenario 2](dr.md#scenario-2-database-corruption) now. |
| `degraded` | 503 | Damage and no usable backup; the app is running on an empty database. [dr.md → Scenario 2](dr.md#scenario-2-database-corruption). |

`"detail":"created a new database"` with `healthy` means there was **no**
database at start. On a Pi with history, that is data loss being reported as
health: [dr.md → The one thing to know first](dr.md#the-one-thing-to-know-first).

No answer at all: the Pi is off, or the container is down (next section).

## Logs

```bash
ssh pi@darts.local 'docker logs --since 1h darts'
ssh pi@darts.local 'docker logs -f darts'               # follow; Ctrl-C to stop
ssh pi@darts.local 'docker ps --format "{{.Names}}\t{{.Status}}"'
```

**Pass:** one logfmt line per event, and `Up … (healthy)` from `docker ps`:

```
ts=2026-09-25T15:17:08.788Z level=INFO logger=darts.api.main request_id=- msg="boot check complete" database=/var/lib/darts/darts.db state=healthy
```

Every request is logged with a `request_id`, also returned as the
`x-request-id` response header; the phone does not display it, so to find a
failure, look for `level=ERROR` around the time it happened:
`docker logs --since 10m darts 2>&1 | grep level=ERROR`. Lines
worth knowing: `boot check complete` (each start), `automatic backup` /
`automatic backup skipped: no matches yet`, `shutdown checkpoint
truncated=true busy=false` (each clean stop), `snapshot after match` (#30's
share). Docker keeps the log for the life of the container, so a deploy
starts a fresh one.

## Restart

```bash
ssh pi@darts.local 'docker compose -f /etc/darts/compose.yaml restart'
```

**Pass:** `docker logs --since 2m darts` shows `shutdown checkpoint
truncated=true busy=false`, then `boot check complete … state=healthy`, then
`automatic backup`; and [health](#is-it-up) says `healthy`.

There is no systemd unit and there should not be one. `restart: unless-stopped`
starts the app at boot and after a crash
([deploy.md](deploy.md#deploying-to-the-pi)). Every start takes an on-Pi
backup, so restarting is cheap and safe. `stop` and `start` do what they say;
a container you `stop` stays stopped across reboots until you `start` it.

## Deploy

```bash
scripts/deploy.sh --host pi@darts.local --dry-run     # the plan; changes nothing
scripts/deploy.sh --host pi@darts.local
```

**Pass:** exit 0, and health reports the commit you deployed:

```bash
git rev-parse --short HEAD
curl -s http://darts.local:8000/api/version
```

It refuses a dirty worktree: commit or stash first. It runs the backend tests
before building (`--skip-tests` to skip), backs up and migrates from inside the
new image, restarts, and **rolls back by itself** if the new sha is not healthy
within 30 s. In that case it exits non-zero even though the Pi is serving the
old build again. Every flag:
[deploy.md → Every flag](deploy.md#every-flag).

## Roll back

For a build that came up healthy and is wrong in a way health cannot see.

```bash
scripts/rollback.sh --host pi@darts.local --list           # shas on the Pi, newest first
scripts/rollback.sh --host pi@darts.local                  # to the previous one
scripts/rollback.sh --host pi@darts.local --to 1a2b3c4     # to a specific one
```

**Pass:** `curl -s http://darts.local:8000/api/version` reports the sha you
rolled back to. **Rollback restores the image, not the schema.** A bad
migration needs a [restore](#restore) from the backup the deploy took.
How the previous sha is chosen:
[deploy.md → How rollback picks its target](deploy.md#how-rollback-picks-its-target).

## Back up

There are three kinds, and only the last one survives losing the SD card
([dr.md → Where the copies are](dr.md#where-the-copies-are)).

**Automatic, on the Pi.** The app backs itself up on every start and every 24
hours, into `/var/lib/darts/backups/`, keeping 24 hourly and 30 daily. It never
backs up a database with no matches
([durability.md → Automatic backups](durability.md#automatic-backups)).

```bash
ssh pi@darts.local 'ls -lt /var/lib/darts/backups | head'
```

**By hand, on the Pi**, before anything you are nervous about:

```bash
ssh pi@darts.local 'docker run --rm -v /var/lib/darts:/var/lib/darts --entrypoint darts-backup darts:latest /var/lib/darts/darts.db'
```

**Pass:** `wrote /var/lib/darts/backups/darts-<timestamp>.db (schema version 3,
… row(s)); pruned …`. Safe while the app
is running: the copy is a consistent snapshot, not a file copy.

**Off the Pi, onto the Mac.** The only copy that survives the card dying.
Nothing does this on a schedule, by decision, so run it after a session you
would mind losing:

```bash
scripts/backup-pull.sh
```

**Pass:** `==> pulled darts-….db (… bytes, schema version 3, integrity ok)`.
Every message it can print, and what it means:
[dr.md → Taking a copy](dr.md#taking-a-copy).

## Restore

[dr.md](dr.md) is the runbook. Start at the scenario that matches what
happened, and it ends in
[Restoring a Mac copy onto the Pi](dr.md#restoring-a-mac-copy-onto-the-pi).
**A restore passes when the data matches, never when health turns green**
([What a pass looks like](dr.md#what-a-pass-looks-like)).

## Device checklist (v1 sign-off)

Everything above was rehearsed on a stand-in Linux VM. Nothing in this repo
has ever run on the Pi or on the iPhone 17 Pro. This checklist is that
first run. It is two parts in one sitting, with the phone in one hand and the
Mac beside the Pi:

- **Part A**: #32's own iPhone checks. The Playwright specs cannot do these,
  because Playwright's WebKit is Safari's engine, not iOS: it has no notch, no
  home-screen install and no real wake lock.
- **Part B**: every device check an earlier ticket deferred to #32, from #24 to
  #31. Each links to where it is already written down.

Suggested order: **B1–B3** (set the Pi up), then **Part A** (it plays the
matches the later steps need), then **B4–B11**, with **B10** last because it
wipes the database.

Record each result in [the table at the end](#results): pass, fail, or a note.
A fail is a finding to file, not something to fix during the sitting.

### Known before you start

These were found before the device pass, verified in WebKit on this Mac but
not on the device. Expect them, and record what the phone actually does.

- **The wake lock cannot work over plain HTTP.** `navigator.wakeLock` exists
  only in a secure context (HTTPS, or `localhost`). The Pi serves
  `http://darts.local:8000`, which is neither. Measured in WebKit:
  `isSecureContext` is `true` on `localhost` and `false` on a LAN address, and
  `wakeLock` and `serviceWorker` are both missing on the LAN address. #24 knew
  and made the hook silently do nothing
  (`frontend/src/play/wakeLock.ts`). **A4 is expected to fail as built.** The
  same rule means #21's service worker never registers on the phone, so there
  is no offline shell either. Fixing it means HTTPS on the LAN with a
  certificate the iPhone trusts: [#71](https://github.com/jackmcnulty/pi-darts-scorekeeper/issues/71).
- **Landscape has no layout.** The manifest asks for portrait, but iOS ignores
  that for home-screen apps. At 874×402 in WebKit, 5 of the 22 keypad keys are
  fully on screen and the rest are clipped at the 56 px touch floor, by #24's
  "clip visibly" design. See A6.

### Part A: the iPhone 17 Pro

Use the app installed to the home screen (A5 first), not a Safari tab. A tab
has toolbars, and the layout was built for the full 402×874 screen.

#### A5. Home-screen install

In Safari, open `http://darts.local:8000/` (or the Pi's address from
`ssh pi@darts.local 'hostname -I'`). **Share → Add to Home Screen → Add.**

**Pass:** the icon appears named **Darts** (the icon art is a known placeholder).
Opening it shows no Safari address bar or toolbar. The status bar sits over
the app's dark background. Swiping it away and reopening it from the icon
opens the app again, not Safari.

#### A1. Safe areas

Open the play screen of a 501 match, a cricket match, and `/stats`.

**Pass:** nothing sits under the Dynamic Island or the clock. The top line
("501 · Leg 1 · Best of 3") is fully readable. The bottom row of keys (MISS,
UNDO) is fully above the home-indicator bar with clear space, and tapping
UNDO never triggers the home gesture. No text or key is cut off by the rounded
corners.

#### A2. One-handed keypad reachability

Hold the phone in one hand and score with that thumb only: a full visit using
Triple and Double, a 25, a BULL, and UNDO. Do it right-handed, then left-handed.

**Pass:** every key, the Single/Double/Triple selector and UNDO are reachable
without changing your grip or using the other hand. Across a whole leg, no dart
lands on the neighbouring key by mistake. Note any key you had to stretch for.

#### A3. No zoom on double-tap

Double-tap quickly: on a number key, on the scoreboard, on empty space, and on
the `/stats` leaderboard. Pinch on the play screen too.

**Pass:** the page never zooms, from any of them. A double tap on a number key
enters **two** darts (both appear in the visit). UNDO them afterwards. A pinch
does nothing.

#### A4. Wake lock through a full match

**Settings → Display & Brightness → Auto-Lock → 30 seconds.** Turn Low Power
Mode off. Play a full best-of-3 501 between two players, and between visits
leave the phone untouched for longer than a minute at least three times.

**Pass:** the screen never dims or locks while the play screen is open. It
locks normally within 30 s of going back to the home screen. **Expected to fail
over HTTP**: see [Known before you start](#known-before-you-start). Record what
happens; if it dims, note after how long. Set Auto-Lock back afterwards.

This match is also the one B4 and B10 need, so finish it.

#### A6. Rotation

During a match, turn the phone to landscape mid-visit (after one dart), then
back to portrait. Do the same on `/stats` and on a sheet (finish a leg).

**Pass, for v1:** nothing is lost. The dart already thrown is still in the visit,
the remaining score is unchanged, and the next dart after rotating back is
recorded normally. Portrait looks exactly as it did before rotating, with no
leftover zoom or offset. **Landscape itself has no design** (see above), so
record what it looks like rather than passing or failing it. Whether it needs
designing is a decision for after v1.

#### A7. The 402×874 fit (deferred from #24–#27)

#24 was verified by construction. #25–#27 were verified in Chrome at 402×781
with the insets modelled, not on the device
([architecture.md](architecture.md), the four notes that end "joins #32").
Look at each screen in portrait:

`/` · `/players` (with the Add sheet open) · `/setup` · an x01 play screen,
including the bust banner · a cricket play screen · the leg-complete sheet and
the match-complete sheet · `/history` · `/history/<id>` · `/stats` ·
`/stats/<id>`

**Pass:** no sideways scrolling anywhere. On the play screens, every key is
fully visible with nothing clipped at the bottom, including while the bust
banner is showing. Both sheets fit on screen without scrolling, with their
buttons fully visible. No label is cut off with "…" where a number should be.

### Part B: the deferred hardware checks

#### B1. Flash the card

Raspberry Pi Imager: **Raspberry Pi OS (64-bit) Bookworm**, Lite is enough. In
its settings: hostname **`darts`**, user **`pi`**, SSH on with your Mac's key,
your Wi-Fi if not on Ethernet. Boot it, then from the Mac:

```bash
ssh pi@darts.local 'uname -m; . /etc/os-release; echo $VERSION_CODENAME; id -u'
```

**Pass:** `aarch64`, `bookworm`, `1000`. If `darts.local` does not resolve,
the hostname is wrong: bootstrap's Avahi setup and the share both depend on it
([deploy.md → The snapshot share](deploy.md#the-snapshot-share-30)).

#### B2. Bootstrap, twice

Get the repository onto the Pi (`git clone` it into `~pi`), then run
[deploy.md step 1](deploy.md#1-bootstrap-is-idempotent) there, both runs.

**Pass:** as written there. This is also the first time the Docker-install
branch has run anywhere, and the first time the Samba/Avahi branch has run on
Bookworm (the stand-in is Ubuntu), so note anything it prints that is not
`already done:` on the second run.

#### B3. First deploy

From the Mac, on a clean checkout of `main`:

```bash
scripts/deploy.sh --host pi@darts.local
```

**Pass:** exit 0, and `curl -s http://darts.local:8000/api/healthz` shows
`healthy`, `"detail":null`, and `git_sha` = `git rev-parse --short HEAD`.
`null` rather than "created a new database" because `deploy.sh` migrates
before it starts the app, and migrating creates the file. Note how long it took: the image
transfer over the LAN to the SD card has never been timed
([deploy.md → What this drill does not cover](deploy.md#what-this-drill-does-not-cover)).

#### B4. deploy.md steps 2–9

[deploy.md → Manual verification checklist](deploy.md#manual-verification-checklist),
steps 2 to 9, as written: non-root and writable, health, logs, clean-shutdown
checkpoint, crash recovery, image replacement, **a real power-cord pull**, and
reachable from the phone. Where a step says `deploy/compose.yaml`,
`/etc/darts/compose.yaml` is the same file installed by bootstrap. Step 7's
"build and load a new image" is covered by B6's two deploys. Step 9 is
satisfied by Part A.

**Pass:** each step's **Expect:**.

#### B5. darts.local and the share: deploy.md steps 10–15

[deploy.md → Checklist on the Pi](deploy.md#checklist-on-the-pi), steps 10 to 15:
Finder mount, writes refused, DB Browser without a lock error, DuckDB (use
**the form written there**: the ticket's literal `ATTACH` fails in DuckDB
1.5.5), the five-minute manifest, and a finished match on the share within
seconds. Finish a match on the phone for step 15.

**Pass:** each step's **Expect:**. `smb://darts.local/darts` resolving at all
is the Avahi check.

#### B6. The broken-build rollback drill, on hardware

[deploy.md → The broken-build rollback drill](deploy.md#the-broken-build-rollback-drill),
**Running it**, against `pi@darts.local` instead of the stand-in, on a
throwaway branch, which you delete afterwards.

**Pass:** the deploy exits non-zero, health reports the previous sha within
the retry budget, and the match history is unchanged
(`curl -s http://darts.local:8000/api/export/matches.csv | shasum -a 256`
before and after). Note the time from start to rolled back. The stand-in took
31 s with no network transfer.

#### B7. Automatic backups on the Pi

After B4's power cycle and any restart:

```bash
ssh pi@darts.local 'docker logs darts 2>&1 | grep "automatic backup"; ls -lt /var/lib/darts/backups | head'
```

**Pass:** an `automatic backup` line after each start since the first match
(earlier starts say `skipped: no matches yet`), each with a matching
`darts-<ts>.db` owned by `1000`.

#### B8. backup-pull.sh against the real Pi

From the Mac: `scripts/backup-pull.sh`, then switch the Pi off and run it again.

**Pass:** the first run says `pulled … integrity ok`. The second exits 0 with
`info: the Pi is not reachable … probably switched off -- nothing pulled`,
in about 5 s. #31 measured that only with no Pi on the network at all.

#### B9. A cold start from the phone

With the Pi off, open the installed app; then switch the Pi on and wait.

**Pass:** record what the phone shows while the Pi is off, and that the app
works without reinstalling once health answers (time from power-on to
`healthy`). With no service worker over HTTP (see above), expect an error page
while the Pi is down rather than the offline shell.

#### B10. Restore from a Mac copy (#32's restore criterion, and #31's drill on the Pi)

This deletes the Pi's database, backups and share, as if the card had been
lost. It takes about five minutes and is only safe while the Pi's history is
test data from this sitting.

1. **Digests before.** From the Mac, save them:

   ```bash
   curl -s http://darts.local:8000/api/export/darts.csv | shasum -a 256 > /tmp/before.txt
   curl -s http://darts.local:8000/api/export/matches.csv | shasum -a 256 >> /tmp/before.txt
   curl -s http://darts.local:8000/api/export/stats.json | sed 's/"generated_at":"[^"]*",//' | shasum -a 256 >> /tmp/before.txt
   cat /tmp/before.txt
   ```

2. **Pull:** `scripts/backup-pull.sh`. Note the file name it prints.
3. **Wipe**, as `pi` (uid 1000 owns these, so no `sudo`):

   ```bash
   ssh pi@darts.local 'docker compose -f /etc/darts/compose.yaml stop && rm -f /var/lib/darts/darts.db* /var/lib/darts/backups/* /srv/darts-share/darts-latest.db /srv/darts-share/snapshot.json'
   ssh pi@darts.local 'docker compose -f /etc/darts/compose.yaml start'
   curl -s http://darts.local:8000/api/healthz
   ```

   **Expect** `healthy` with `"detail":"created a new database"`: the trap
   [dr.md](dr.md#the-one-thing-to-know-first) is built around. This is not a pass.

4. **Restore**, exactly as
   [dr.md → Restoring a Mac copy onto the Pi](dr.md#restoring-a-mac-copy-onto-the-pi)
   is written, with `COPY=` the file from step 2. Follow it word for word: the
   point is to find out whether the page is right.
5. **Digests after**, the same three commands into `/tmp/after.txt`, then
   `diff /tmp/before.txt /tmp/after.txt`.

**Pass:** `diff` prints nothing, health says `healthy` with `"detail":null`,
and a new player can be added on the phone. Record the digests, the wipe-to-
verified time, and any step of dr.md that did not work as written.

#### B11. Health after the sitting

```bash
curl -s http://darts.local:8000/api/healthz
ssh pi@darts.local 'ls -ln /var/lib/darts /var/lib/darts/backups | head; docker ps'
```

**Pass:** `healthy`, everything owned by `1000 1000`, container `(healthy)`.
Then set the phone's Auto-Lock back.

### Results

Device: iPhone 17 Pro, iOS ___ · Pi: ___ · Image sha: ___ · Date: ___

| Item | Result | Notes |
| --- | --- | --- |
| A1 Safe areas | not run | |
| A2 One-handed reach | not run | |
| A3 No zoom on double-tap | not run | |
| A4 Wake lock through a match | not run | Expected to fail over HTTP |
| A5 Home-screen install | not run | |
| A6 Rotation | not run | Landscape has no layout |
| A7 402×874 fit | not run | |
| B1 Flash | not run | |
| B2 Bootstrap twice | not run | |
| B3 First deploy | not run | |
| B4 deploy.md steps 2–9 | not run | |
| B5 deploy.md steps 10–15 | not run | |
| B6 Rollback drill | not run | |
| B7 Automatic backups | not run | |
| B8 backup-pull.sh | not run | |
| B9 Cold start | not run | |
| B10 Restore from a Mac copy | not run | |
| B11 Health after | not run | |
