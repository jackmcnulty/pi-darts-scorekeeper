# Operating the scorekeeper

How to look after the Pi day to day: logs, restart, deploy, roll back, back up,
restore. Then the device checklist that signs v1 off. Ticket #32.

[runbook.md](runbook.md) has the same tasks, plus first-time setup, cutting a
release and wiping to a fresh start, in one self-contained page in the order
you do them.

Each task gives the command and what a pass looks like. How the machinery
works is in [deploy.md](deploy.md), [durability.md](durability.md) and
[dr.md](dr.md); this page links there rather than repeating it. Commands
starting `scripts/` run on the Mac from the repository. Everything else runs
over `ssh pi@darts.local`.

**Talking to the app from the Mac goes over HTTPS** (#71), to
`https://darts.local/`, with a certificate from our own root, which nothing on
the Mac trusts by default. Every `curl` here passes it explicitly. Set this once
per terminal; it is where [scripts/make-cert.sh](../scripts/make-cert.sh)
keeps the root:

```bash
export DARTS_CA="$HOME/Library/Application Support/darts-tls/darts-root.crt"
```

Port 8000 is no longer reachable from the LAN. It is published on the Pi's
loopback only, for Caddy, the snapshot timer and `healthcheck.sh`, which all run
on the Pi itself.

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
curl -s --cacert "$DARTS_CA" https://darts.local/api/healthz
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
curl -s --cacert "$DARTS_CA" https://darts.local/api/version
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

**Pass:** `curl -s --cacert "$DARTS_CA" https://darts.local/api/version` reports the sha you
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

## Trusting the Pi's certificate

The phone reaches the app at **`https://darts.local/`**. Safari gives a page the
wake lock (#24) and a service worker (#21) only over HTTPS, so over the old
`http://darts.local:8000` both were silently off: the screen dimmed mid-match
and there was no offline shell. That was #71.

How it fits together, and why each piece is where it is:

- **A root certificate of our own**, made once on the Mac by
  `scripts/make-cert.sh`. Its private key never leaves the Mac. It may sign
  only for `darts.local`, so it cannot be used to impersonate any other site
  to the phone. **Whoever holds `darts-root.key` can still impersonate
  `darts.local` to the phone.** Keep it where the script puts it. If it leaks or
  is lost, delete the directory and start again from step 1. The phone then has
  to trust the new root (steps 3–4).
- **A leaf certificate for `darts.local`**, signed by that root, valid 820 days.
  iOS refuses anything over 825, user-installed root or not. Nothing on the Pi
  renews or checks it, so the Pi's clock does not matter. The Pi has no RTC
  battery and boots with a stale clock after every power cut, which would break
  a short-lived certificate.
- **Caddy on the Pi**, installed by `bootstrap-pi.sh`, serves it on 443,
  redirects 80, and proxies to the app on `127.0.0.1:8000`. There is **no HSTS
  header**, so going back to plain HTTP can never lock the phone out
  ([deploy.md → HTTPS](deploy.md#https-71)).

### Setting it up (once), and renewing (every ~2 years)

1. **On the Mac**, make the root and the leaf. A second run reuses the root and
   issues a new leaf: that is the renewal, and then only steps 2 and 5 apply.

   ```bash
   scripts/make-cert.sh
   ```

2. **Put the leaf on the Pi** and re-run bootstrap there with it. This needs
   sudo on the Pi, and it is also how the changed `compose.yaml` (8000 on
   loopback) reaches an already-bootstrapped Pi. `deploy.sh` refuses to run
   until it has.

   ```bash
   ssh jackm@darts.local 'mkdir -p -m 700 darts-tls'
   scp "$HOME/Library/Application Support/darts-tls/darts-leaf."{crt,key} jackm@darts.local:darts-tls/
   ssh -t jackm@darts.local 'cd ~/pi-darts-scorekeeper && git pull && sudo BOOTSTRAP_USER=jackm scripts/bootstrap-pi.sh --tls-from ~/darts-tls && rm -r ~/darts-tls'
   scripts/deploy.sh --host jackm@darts.local
   ```

   **Pass:** bootstrap ends with `https://darts.local/` and
   `skip: certificate valid until …`. The deploy exits 0.
   `curl -s --cacert "$DARTS_CA" https://darts.local/api/healthz` answers, and
   `curl -sI http://darts.local/` is a redirect to `https://darts.local/`.

3. **Get the root onto the iPhone.** In Finder, open
   `~/Library/Application Support/darts-tls/` (**Go → Go to Folder**), and
   AirDrop **`darts-root.crt`** to the phone. Only that file. Never the `.key`
   files. The phone says **Profile Downloaded**.
4. **Install it, then trust it.** These are two separate steps, and iOS needs
   both.
   - **Settings → General → VPN & Device Management → Darts LAN root →
     Install**.
   - **Settings → General → About → Certificate Trust Settings**, and switch on
     **Darts LAN root (darts.local only)**.
5. **Re-add the home-screen icon.** `https://darts.local` is a different web
   app to iOS from `http://darts.local:8000`. Delete the old **Darts** icon,
   then in Safari open `https://darts.local/` and **Share → Add to Home Screen →
   Add**. Nothing is lost, because the server holds every match. The app is
   simply starting fresh in a new origin.

### The #71 checklist, on the phone

Run after the steps above, and record results in
[the table below](#71-results). Criteria 1–3 of #71 can only be signed off
here.

**H1. No certificate warning** (criterion 1). Open `https://darts.local/` in
Safari. **Pass:** the app loads with no "This connection is not private" page,
and tapping the address bar's site info shows the connection as secure. Then
open the home-screen icon. **Pass:** it opens straight into the app.

**H2. The screen stays awake** (criterion 2). Run [A4](#a4-wake-lock-through-a-full-match)
from the home-screen icon. **Pass:** as A4 says.

**H3. The offline shell** (criterion 3). Open the home-screen app once with the
Pi on and go to a couple of screens, so the service worker installs and caches
the shell. Close the app (swipe it away). Then switch the Pi **off** at the
wall. Open the app from the icon. **Pass:** the app's own screens appear, not
Safari's "cannot open the page". Anything that needs the server shows
**Cannot reach the scoreboard** rather than a blank page. Switch the Pi back on
and, once it has booted, the same app works again without reinstalling. This is
the first time anybody has checked a Pi-off cold start
([B9](#b9-a-cold-start-from-the-phone) never ran it).

*Optional, if H3 fails:* on the Mac, Safari → **Develop → (the iPhone) →
darts.local**, needs **Settings → Apps → Safari → Advanced → Web Inspector**
on the phone. In its console,
`[isSecureContext, 'wakeLock' in navigator, !!navigator.serviceWorker.controller]`
should be `[true, true, true]`.

**H4. The scripts against the new address** (criterion 4). From the Mac:

```bash
scripts/deploy.sh --host jackm@darts.local            # pass: exit 0
scripts/healthcheck.sh --host jackm@darts.local       # pass: prints the sha
scripts/backup-pull.sh                                # pass: ==> pulled darts-….db (… integrity ok)
ssh jackm@darts.local 'journalctl -u darts-snapshot.service --since -15min --no-pager | tail -5'
```

**Pass** for the last: a run within the last five minutes that logged a
manifest, not a curl error.

#### #71 results

| Item | Result | How, and notes |
| --- | --- | --- |
| H1 No certificate warning | **pass** | Jack, on the phone, 2026-10-10, against `69fa192`: root installed and trusted, `https://darts.local/` loads with no warning, icon re-added. |
| H2 Wake lock (A4 over HTTPS) | **pass** | Jack, on the phone, 2026-10-10. A4 now passes over HTTPS; #32's A4 failure below was the plain-HTTP address. |
| H3 Offline shell, Pi off | **pass** | Jack, on the phone, 2026-10-10. The first Pi-off cold start anyone has run. |
| H4 Scripts against the new address | **pass** | 2026-10-10, from the Mac against the real Pi, `69fa192`. Bootstrap with `--tls-from` installed Caddy 2.6.2-5, and `caddy validate` passed. `deploy.sh` exit 0 in 63 s (healthy after 3 s). `healthcheck.sh` printed `69fa192`. `backup-pull.sh` pulled 200704 bytes, `integrity ok`, over HTTPS with `--cacert`. The snapshot timer's first run after the deploy (13:06:43) logged its manifest. Also measured: `http://darts.local/` is a 308 to `https://darts.local/`, there is no `Strict-Transport-Security`, `:8000` is refused from the LAN (listening on `127.0.0.1` only), `https://<ip>/` fails the handshake, and `curl` without the root is exit 60. |

Rehearsed in a browser, not on the phone: Playwright's WebKit at 402×781,
loading `https://darts.local/` from the real Pi through a local CONNECT proxy
(WebKit cannot resolve `.local` itself), with `ignoreHTTPSErrors` because it
cannot be given a root. It reported `isSecureContext: true`, `wakeLock` present,
and `/sw.js` registered, activated and controlling the page after a reload. On
the old plain-HTTP address, #32 measured all three as missing. Whether the
iPhone trusts the root, and so gets the same, is H1–H3.

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

- **The wake lock cannot work over plain HTTP.** *Fixed by #71: the app is
  now served at `https://darts.local/`. Set it up with
  [Trusting the Pi's certificate](#trusting-the-pis-certificate) before A5. What
  follows is what #32 found.* `navigator.wakeLock` exists
  only in a secure context (HTTPS, or `localhost`). The Pi served
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

In Safari, open `https://darts.local/`. That address only, never an IP
address, which the certificate does not cover. It needs the root trusted first:
[Trusting the Pi's certificate](#trusting-the-pis-certificate). **Share → Add to
Home Screen → Add.**

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
locks normally within 30 s of going back to the home screen. Over the old
plain-HTTP address this failed, as expected (#32's result below). It is
expected to pass over `https://darts.local/`, which is #71's H2. Record what
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

**Pass:** exit 0, and `curl -s --cacert "$DARTS_CA" https://darts.local/api/healthz` shows
`healthy`, `"detail":null`, and `git_sha` = `git rev-parse --short HEAD`.
`null` rather than "created a new database" because `deploy.sh` migrates
before it starts the app, and migrating creates the file. Note how long it took: the image
transfer over the LAN to the SD card has never been timed
([deploy.md → What this drill does not cover](deploy.md#what-this-drill-does-not-cover)).

#### B4. deploy.md steps 2–9

[deploy.md → Manual verification checklist](deploy.md#manual-verification-checklist),
steps 2 to 9, as written: non-root and writable, health, logs, clean-shutdown
checkpoint, crash recovery, image replacement, **a real power-cord pull**, and
reachable from the phone. Step 6's crash command was corrected during this
device pass: kill the server from the host, not from inside the container. Where a step says `deploy/compose.yaml`,
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
(`curl -s --cacert "$DARTS_CA" https://darts.local/api/export/matches.csv | shasum -a 256`
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
   curl -s --cacert "$DARTS_CA" https://darts.local/api/export/darts.csv | shasum -a 256 > /tmp/before.txt
   curl -s --cacert "$DARTS_CA" https://darts.local/api/export/matches.csv | shasum -a 256 >> /tmp/before.txt
   curl -s --cacert "$DARTS_CA" https://darts.local/api/export/stats.json | sed 's/"generated_at":"[^"]*",//' | shasum -a 256 >> /tmp/before.txt
   cat /tmp/before.txt
   ```

2. **Pull:** `scripts/backup-pull.sh`. Note the file name it prints.
3. **Wipe**, as `pi` (uid 1000 owns these, so no `sudo`):

   ```bash
   ssh pi@darts.local 'docker compose -f /etc/darts/compose.yaml stop && rm -f /var/lib/darts/darts.db* /var/lib/darts/backups/* /srv/darts-share/darts-latest.db /srv/darts-share/snapshot.json'
   ssh pi@darts.local 'docker compose -f /etc/darts/compose.yaml start'
   curl -s --cacert "$DARTS_CA" https://darts.local/api/healthz
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
curl -s --cacert "$DARTS_CA" https://darts.local/api/healthz
ssh pi@darts.local 'ls -ln /var/lib/darts /var/lib/darts/backups | head; docker ps'
```

**Pass:** `healthy`, everything owned by `1000 1000`, container `(healthy)`.
Then set the phone's Auto-Lock back.

### Results

Device pass 2026-10-07 (UTC). Pi: **Raspberry Pi 5 Model B**, Debian 12
Bookworm, aarch64, booting from a USB drive (`/dev/sda2`), Docker 29.8.2,
operator `jackm` (uid 1000), hostname `darts`. Image: `1ae5fe3` from B3, then
`1f628c7` from B4 step 7 onwards. iPhone: iPhone 17 Pro, iOS 27.0.1.

"Over SSH" below means run from the Mac against the real Pi with Jack's
passwordless SSH. Nothing in this table was run on the stand-in.

| Item | Result | How, and notes |
| --- | --- | --- |
| A1 Safe areas | **pass** | Jack, on the phone. |
| A2 One-handed reach | **pass** | Jack, on the phone. |
| A3 No zoom on double-tap | **pass** | Jack, on the phone. |
| A4 Wake lock through a match | **fail, as expected** | Jack, on the phone. The screen is not held awake: `navigator.wakeLock` does not exist over plain HTTP. [#71](https://github.com/jackmcnulty/pi-darts-scorekeeper/issues/71). |
| A5 Home-screen install | **pass** | Jack, on the phone. |
| A6 Rotation | **pass for v1**; landscape has no layout | Jack, on the phone: nothing lost on rotating. Landscape itself is the known gap (5 of 22 keys fit, measured in WebKit). No ticket; a decision for after v1. |
| A7 402×874 fit | **pass** | Jack, on the phone. Closes the fit deferred by #24–#27. |
| B1 Flash | **pass** | Over SSH: `aarch64`, `bookworm`, uid 1000, hostname `darts`, `darts.local` resolves from the Mac. User is `jackm`, not `pi`; see the note at the top. |
| B2 Bootstrap twice | **pass** | Both runs by Jack on the Pi. The second rewrote `compose.yaml` and `smb.conf` as designed, and `docker.list` holds the Docker source exactly once. First run: Docker (29.8.2, Compose 5.6.0) installed on Bookworm, Samba 4.17.12 installed with `smb.conf` validated, `nmbd` disabled, Avahi present, hostname `darts`, the snapshot timer enabled. Both branches ran for the first time anywhere. |
| B3 First deploy | **pass** | Run by Jack. Healthy on `1ae5fe3`, `"detail":null`. Not timed. |
| B4 deploy.md steps 2–9 | **pass**: 2–7, 9; 8 **partly**: a clean reboot, not a power cut | Over SSH. 2: container `uid=1000(darts)`, files owned 1000. 3: `healthy`, `Up … (healthy)`. 4: logfmt. 5: `shutdown checkpoint truncated=true busy=false`, no `-wal` left. 6: **the documented command did not crash anything** (no `kill` in the image; PID 1 ignores SIGKILL from inside); corrected to a host-side kill, after which `restarts=1` and healthy in 3 s. 7: deploying `1f628c7` over `1ae5fe3` left `/api/players` byte-identical and the matches digest unchanged; the normal deploy took 54.5 s including the 34 s test suite. 9: Jack scored a 301 leg on the phone (match 1). 8: Jack ran `sudo reboot`. The app checkpointed on the way down (`truncated=true`), the Pi booted in 50.6 s, and the app was healthy 72 s after it stopped, with no intervention and the data intact. That proves it comes back at boot. **A sudden power loss, which is what step 8 asks for, was not tested.** |
| B5 deploy.md steps 10–15 | **pass** | From the Mac, `mount_smbfs -N //guest@darts.local/darts`: exactly the two files, no password. 11: `touch`, `cp`, `rm` all `Permission denied`, Pi unchanged. 13: DuckDB counted 20, equal to `row_counts.darts`, no sidecar. 14: from the journal, timer runs 300.8, 301.1, 301.0, 301.0 s apart (the first two failed with curl exit 7 before the app was deployed, as designed). 15: a match finished at 02:18:08.023 was on the share 60 ms later (`snapshot after match`). 10 (the Finder mount and sidebar) and 12 (DB Browser, no lock error): Jack, at the Mac. |
| B6 Rollback drill | **pass** | Over SSH, from a throwaway worktree with `raise RuntimeError("drill")` in `main.py`, `--skip-tests`. Exit 1; rolled back to `1ae5fe3`, healthy 43 s after the broken start; 70 s end to end including build and transfer. Matches digest unchanged; the broken image was deleted from the Pi. |
| B7 Automatic backups | **pass** | Over SSH: the first start with a match backed up 62 ms after its boot check; each later start did too, with `pruned=1` collapsing the same hour. The first start of all logged nothing to back up, correctly (no matches). |
| B8 backup-pull.sh | **pull pass**; Pi-off half not run | `pulled darts-20261007T021230Z.db (196608 bytes, schema version 3, integrity ok)`, 0.19 s over the LAN. The Pi-off half was not run: the only time the Pi was down was a 17 s reboot. #31 measured that path with no Pi on the network at all. |
| B9 Cold start | not run | Needs the Pi switched off with the phone in hand. Expected to show an error rather than the offline shell ([#71](https://github.com/jackmcnulty/pi-darts-scorekeeper/issues/71)). |
| B10 Restore from a Mac copy | **pass** | Over SSH, `dr.md`'s procedure word for word with `jackm@`. Variant: instead of wiping the Pi, the Pi was changed after the pull (two players and a match added for B5), then the copy restored over it, so nothing of Jack's was deleted. Digests before and after, byte-identical: `darts.csv` `4a5f8d37…f795`, `matches.csv` `4b4a5391…6455`, `stats.json` `c5f1d1cc…1b5b`. Health `healthy`, `"detail":null`; 20 darts and 1 match, equal to the copy; the test players gone. The procedure took 5 s; the replaced database was kept as `darts.replaced-20261007T021832Z.db`. dr.md's "add a player" write check was left to the phone rather than leave a test player behind. |
| B11 Health after | **pass** | Over SSH: `healthy` on `1f628c7`, everything owned 1000, container `(healthy)`, 9% of the disk used. |

#### Found during the device pass

Filed, none blocking v1:
[#67](https://github.com/jackmcnulty/pi-darts-scorekeeper/issues/67) setup defaults,
[#68](https://github.com/jackmcnulty/pi-darts-scorekeeper/issues/68) single-player,
[#69](https://github.com/jackmcnulty/pi-darts-scorekeeper/issues/69) the visit strip,
[#70](https://github.com/jackmcnulty/pi-darts-scorekeeper/issues/70) a way home,
[#71](https://github.com/jackmcnulty/pi-darts-scorekeeper/issues/71) HTTPS on the LAN,
[#72](https://github.com/jackmcnulty/pi-darts-scorekeeper/issues/72) the match sheet's averages.
Fixed in #32's docs: deploy.md step 6's crash command; B3's expected health
detail; operators who are not `pi`.
