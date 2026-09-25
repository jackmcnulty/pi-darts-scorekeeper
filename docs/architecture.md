# Architecture

## Overview

## Components

### Engine

### Database

Three layers, bottom to top. Each may use the one below it; none may use the
one above.

| Layer | Package | Knows about |
| --- | --- | --- |
| Storage | `darts.db` | SQLite: connections, migrations, views, backup, recovery |
| Repositories | `darts.repo` | Rows. May import `darts.db` *and* `darts.engine` |
| Services | `darts.services` | Game writes. May import `darts.repo` *and* `darts.engine` |
| Engine | `darts.engine` | Nothing else at all — see below |

The engine sits beside the repositories rather than beneath them. It is pure:
no clock, no randomness, no I/O, no frameworks, and no knowledge that
`darts.db`, `darts.repo`, `darts.api` or `darts.services` exist. That is not a
convention, it is enforced — `tests/engine/test_purity.py` parses every engine
source file and fails on a banned import, discovering new modules by itself.
Repositories are allowed to depend on the engine, and do: `create_match` asks
`engine.rotation.starting_team` who opens leg 0.

Schema changes go through `darts.db.migrate` and are immutable once applied;
views are a replaceable layer on top and need no migration. `docs/data-model.md`
covers both in detail.

#### The caller owns the transaction

No function in `darts.repo` opens a transaction. Repository functions take a
`sqlite3.Connection`, issue their statements on it, and leave the boundary to
whoever called them:

```python
with transaction(conn):
    created = create_match(conn, config, teams)
```

The reason is mechanical. `darts.db.connection.transaction` refuses to nest —
it raises if the connection is already in one — so that a helper can never
commit its caller's half-finished work. If each repository function owned a
transaction, `create_match` could not call four of them. Pushing the boundary
up one level is what makes the repositories composable at all.

`create_match` is the exception that proves the rule: it *checks* that a
transaction is open and refuses to run without one. Writing a match, its teams,
its members and leg 0 atomically is the promise it makes, and it is not a
promise it can keep by itself.

#### A match's configuration is validated once

`matches` stores its configuration twice — as JSON in `config_json`, and again
in the promoted columns that the indexes and the statistics layer read. Storing
a fact twice is how the two come to disagree, so exactly one object produces
both. `repo.config.GameConfig` is a pydantic model that mirrors the schema's own
CHECK constraints, and `create_match` writes `config_json` and the promoted
columns from that one validated instance.

The CHECK stays as a backstop, but it is deliberately never the thing that
reports the error: by the time SQLite complains, a statement has already run.
`GameConfig` rejects an invalid configuration before any row is written.
`tests/repo/test_config.py` offers the same grid of values to the model and to
a raw INSERT and asserts they agree on every one, so the mirror stays a mirror.

### Services

`darts.services.play` is the only module in the codebase that writes recorded
play. A repository knows how to write a row; it does not know whether the row
is legal. This layer does, and it is where the transaction boundary the
repositories left open finally closes.

#### One dart, one transaction

`play.throw` records exactly one dart and commits. It loads the leg's darts,
replays them, replays them again with the new dart on the end, writes the rows
the difference implies — the dart, a visit if this dart opens one, any cricket
effect and point events — refreshes the caches, and advances the leg and the
match if the dart finished either. All of it inside one `transaction(conn)`.

A power cut therefore costs the dart in flight and nothing else, which is the
promise #12 makes about a Pi that is power-cycled rather than shut down.

`throw` is idempotent on the `client_dart_id` the frontend mints. The column is
unique database-wide and the lookup is too: a key already present inserts
nothing and returns the current state, so a double tap or a retry over flaky
wifi cannot record two darts. The same key describing a *different* dart raises
`IdempotencyConflictError` rather than quietly returning somebody else's throw.

#### Undo is a hard delete; a bust is not

`play.undo` deletes the leg's last dart outright. Its cricket effect and point
events go by cascade; its visit row goes too if that dart was the only one in
it, because the schema will not do that for you. Then the leg is replayed and
every derived row rewritten from what is left. There is no `is_undone` flag and
no redo: an undo is a misclick, not data.

A bust is the exact opposite and is preserved in full — the offending dart, the
darts it voided, `is_bust`, `score_after == score_before`. It is something that
really happened. Undoing *across* a bust is the interesting case: the visit
reverts to incomplete and the darts the bust had voided count again.

Undoing the dart that won a leg reopens the leg, closes the empty leg that dart
had opened, and unwins the match if that leg decided it. Undo does not reach
back across a leg that has since been played into; that is history.

#### The caches hold a leg in progress and nothing else

`leg_team_state` and `cricket_leg_state` are written by this layer and never
read by it. Everything the service decides comes from replaying `darts`. If it
trusted the caches there would be two sources of truth and no way to tell which
had drifted.

The invariant is narrow: **a leg holds cache rows exactly when it has at least
one dart and no winner.** A leg nobody has thrown into has nothing to resume,
and a finished leg is reconstructed from its darts. So completing a leg deletes
its cache rows, and so does undoing back to an empty leg.
`play.rebuild_caches(conn, leg_id)` recomputes them, deleting as readily as it
writes; over a correct database it changes nothing, which
`tests/fixtures/test_rebuild_caches.py` asserts row by row against the seeded
fixture.

`darts-verify` goes further and recomputes *every* derived value — visit scores
and flags, per-dart verdicts, cricket effects and point events, both caches, and
the leg and match winners — reporting each disagreement by name.

#### Errors say which layer failed

`repo.errors.RepoError` answers "is there such a row"; `services.errors.ServiceError`
answers "is that a legal thing to do". Two hierarchies, so a caller of the
repository layer is never in a position to catch a game-rule error, and so #18
has two stable bases to map onto status codes. `LegCompleteError`,
`MatchCompleteError`, `IdempotencyConflictError` and `NothingToUndoError` each
name one situation rather than being told apart by their message.

### API

`darts.api` sits above every other layer. `main.create_app` builds the
application from a `Settings`; `uvicorn darts.api.main:app` gets one read from
the environment, and tests pass their own so nothing depends on the machine.

| Module | Responsibility |
| --- | --- |
| `api/main.py` | The app factory and the lifespan hooks. |
| `api/deps.py` | Per-request settings, boot status and connection. |
| `api/errors.py` | The error envelope and every exception handler. |
| `api/health.py` | `/api/healthz` and `/api/version`. |
| `api/stats.py` | The three `/api/stats` reads and their response models. |
| `api/static.py` | The built frontend, the SPA fallback, cache headers. |
| `api/logging_conf.py` | Structured logging and the request-id middleware. |
| `config.py` | Every path and port, from `DARTS_*`. |

#### Configuration is one environment variable per thing

`Settings` is a frozen dataclass read once at startup, with `DARTS_DB_PATH`,
`DARTS_BACKUP_DIR`, `DARTS_SNAPSHOT_DIR`, `DARTS_STATIC_DIR`, `DARTS_PORT`,
`DARTS_GIT_SHA` and `DARTS_LOG_LEVEL`. Backups and snapshots default to sitting
beside the database, so moving the database to a USB SSD is `DARTS_DB_PATH` and
a file copy. An unusable port or log level fails at startup rather than at
first use. `git_sha` is whatever the image build stamped in and `unknown`
otherwise; #28's Docker build is what sets it.

#### One connection per request

A FastAPI dependency opens one connection, yields it, and closes it when the
request ends — `deps.ConnectionDep`. Dependency entry, synchronous endpoint
execution, and cleanup may run on different worker threads. API connections
therefore use `check_same_thread=False`; the factory retains `True` by default
for other callers. Each connection belongs exclusively to one request, and
these stages execute sequentially. Never share it with concurrent work within
a request or with another request. The concurrent player-creation regression
covers the thread-handoff failure that single-request tests cannot expose.

The API layer never opens a transaction. It calls a service, and
[the caller owns the transaction](#the-caller-owns-the-transaction) means the
service is that caller.

#### One error envelope

Every `/api` failure is `{"error": {"code", "message", "detail"}}`. `code` is a
closed vocabulary — `validation_error`, `invalid_request`, `not_found`,
`conflict`, `service_unavailable`, `internal` — and `detail` carries what the
code implies: pydantic's per-field errors for a 422, a `reason` discriminator
for a domain refusal, the health report for a 503.

The mapping lives in `errors.install_error_handlers`, so an endpoint only ever
raises. `NotFoundError` is a 404, `DuplicateNameError` a 409, `InvalidMatchError`
a 422, any other `RepoError` a 400, and every `ServiceError` a 409 — each
carrying `{"reason": "leg_complete"}` and the like, because several distinct
refusals share the one code. An unhandled exception is a 500 whose traceback
goes to the log and never to the client.

The schema did not say any of this until #21. Because the handlers return a raw
`JSONResponse`, FastAPI never saw the shape and documented every failure as its
own `HTTPValidationError` — which no route has returned since #16. The generated
TypeScript client inherited that, so the frontend rule that no response type is
hand-written was impossible to keep for errors.

`api/openapi.py` fixes it in one place rather than with `responses=` on
twenty-three operations across five merged tickets. `DartsApp.openapi()`
rewrites the generated document: every failure response, plus a `default` added
to every operation, points at `ErrorEnvelope`, and the superseded
`HTTPValidationError`/`ValidationError` schemas are dropped once nothing
references them. The `default` matters — a route that enumerates only its 422
can still answer 500, and the client has to be ready for it.

It is a documentation change; not a byte of what the server sends moved.
`tests/api/test_openapi_envelope.py` is in two halves, and the second is the one
that earns its keep: it makes real requests that really fail and validates the
bodies against the very model the schema now advertises, so the document and the
server cannot drift apart again without a test going red.

#### Health has two independent failure conditions

`/api/healthz` reports the cached boot `RecoveryStatus` *and* probes the
database on every request, because the boot check cannot know the card went
read-only an hour later. The probe opens a connection and reads
`PRAGMA user_version`, which is the schema version the endpoint reports anyway;
opening is itself the writability test, since `connect()` sets
`journal_mode = WAL` and that fails on a database that cannot be written. So
the check costs no write, which matters on a card that #28's `HEALTHCHECK` will
poll for years.

`degraded`, `unavailable` and `not_started` are 503. `restored` is 200: the
restore already happened and the database is serviceable, so a monitor
restarting the box over it would only throw the recovery away.

#### Routing order is load-bearing

`/api` routes and `/api/openapi.json` are declared before the static mount at
`/`, so they keep winning against it. `StaticFiles(html=True)` is not an SPA
fallback — it serves `index.html` for a *directory* request, which makes `/`
work and `/history/42` a 404 — so `static.SpaStaticFiles` falls back to
`index.html` on any 404 except under `api/`, where a typo must stay a JSON 404
rather than becoming a page. `StaticFiles` also sets no `Cache-Control` at all,
so hashed assets are marked `immutable` and everything else `no-cache` here.

With no build present — the normal state in development and on CI, since
`frontend/dist` is gitignored — nothing is mounted and unrouted paths say so.

### Frontend

The shell every screen hangs off: routing, one typed way to reach the API, and
the PWA layer. Built in #21; screens are #22 onwards.

#### The server is authoritative, and the shell is built to say so

Nothing on the phone decides anything about a game. The score, the checkout,
whether a visit busts, whose throw it is — all of it is the server's, and the
client's job is to show the last thing the server said and to be honest when it
cannot reach it. Three decisions fall straight out of that, and each one would
look arbitrary without it:

- **Mutations are never retried.** `POST /api/legs/{leg_id}/darts` records a
  dart and is not idempotent, so a retry after a response that was sent but
  never arrived would score the same dart twice. A failed mutation stays
  failed, the toast explains why, and the player taps again — a decision they
  can see rather than one made for them. Queries retry freely; they only read.
- **Offline means "the app shell loads", never "you can play".** The service
  worker precaches enough to boot and nothing else.
- **A caught render error says the score is safe**, because it is.

#### The service worker never touches `/api`

`sw/handler.ts` holds every routing decision as a plain function and `sw/sw.ts`
is the dozen lines of listeners that cannot be unit-tested. The split exists
because #21 requires the `/api` rule be proved by a test rather than by
inspection, and a decision buried in a worker global can only be inspected.

The rule is enforced structurally, not by care. `chooseStrategy` is
**synchronous**, so the fetch listener can decline a request outright —
`respondWith` is never called, and the browser makes the request itself exactly
as if no worker were installed. An `async` decision could not do that: it would
have to call `respondWith` first and work out what to do afterwards, putting
every `/api` call inside the worker's control flow. `/api` is also checked
before any other rule, so no later rule can reach it by accident.

Strategies: `/api` and non-GET are network-only; navigations get the network
with the cached shell behind them, which is what makes a deep link boot
offline; hashed assets are cache-first; everything else is network-first. That
mirrors the cache headers in *Routing order is load-bearing* above —
`sw/handler.ts`'s `HASHED` and `api/static.py`'s `HASHED` are the same rule on
the two sides of the wire.

#### Nothing describes a payload by hand

`api/client.ts` is a thin wrapper over `openapi-fetch` and the generated
`schema.d.ts`. Failures arrive as exceptions rather than as a branch of the
return value, because TanStack Query decides what to retry by catching, and a
screen that forgot to check an `error` field would render `undefined` instead
of saying something went wrong. `ApiError` means the server answered and
refused; `OfflineError` means no answer arrived at all. Only the second is a
connection problem, and that distinction is what drives the toast.

This was only half true before #21 — see *One error envelope* above.

#### Reachability is decided by evidence, not by `navigator.onLine`

`api/connection.ts` moves only when a real request really succeeds or really
fails: an `OfflineError` loses the connection, and *any* answer finds it again,
including a 404 or a 409 — a server that refuses is a server that is plainly
there. `navigator.onLine` reports an association with an access point, which in
a garage is routinely true while the Pi is off or still booting, which is
exactly the case this has to catch.

There is no polling and no timer, so nothing here depends on a clock. Recovery
needs a request to have happened, and TanStack Query's retries,
`refetchOnReconnect` and `refetchOnWindowFocus` are what make one happen.

#### The layout is the only thing that knows about the notch

#4 put `viewport-fit=cover` in `index.html` and the `--safe-*` tokens in
`tokens.css`. Without something applying them, `viewport-fit=cover` is strictly
worse than not setting it, because the app then paints *under* the Dynamic
Island. `RootLayout` applies all four as padding, once. A screen that genuinely
needs to reach the edge undoes it locally with a negative margin of the same
token — deliberately an escape hatch and not a prop, which would invite every
screen to have an opinion about the notch.

#### Every PWA asset is asserted into the build

The SPA fallback that makes `/history/42` survive a reload also means a missing
`apple-touch-icon.png` comes back as `200 text/html` rather than as a 404 — the
server is structurally incapable of reporting one. So `src/pwa.test.ts` runs a
real Vite build, reads the emitted `index.html` and manifest, and resolves every
asset they reference against what the build actually wrote.

`sw.js` is emitted at the root under that exact name: a hashed service worker
could not be registered by a fixed URL, and one under `/assets/` would be scoped
to `/assets/` and never see a navigation. It is also asserted to be
self-contained, because Rollup is otherwise free to lift shared code into a
hashed chunk the worker cannot name.

The home-screen icons are committed PNGs rather than a build step, so `npm ci &&
npm run build` needs no rasteriser and the bytes on the Pi are the bytes in git.
`scripts/gen-icons.py` is how they were made — pure `zlib` and `struct`, no
third-party packages, byte-reproducible.

#### Four TypeScript projects, because `DOM` and `WebWorker` disagree

`app`, `node`, `test`, and now `sw`. Both libs declare `self`, incompatibly, so
a service worker cannot be typechecked by the app project and the app cannot be
typechecked by the worker's. Only `sw/sw.ts` lives in the fourth;
`sw/handler.ts` stays in `app` and is checked by both.

## Deployment

## Durability and disaster recovery

### Setup API (#17)

`services.setup` owns every player/setup write transaction, including duplicate-name
checks and the full match creation. The API validates requests and calls the service;
repositories still never open transactions. Match lists use a service-owned read
transaction so their count and page share one snapshot.

Player create and patch take `{"display_name": "Ana"}`; patch edits the name and
requires it. Names are trimmed and must be nonblank. Create returns 201; patch and
archive return the player with 200. Archive is idempotent and keeps historical
members visible. Colour and scoreboard short name were deferred to #22, and
arrived there — see *Home screen and player management (#22)*.

Match create takes `{"config": {...}, "teams": [{"player_ids": [1], "name": null},
{"player_ids": [2]}]}` and returns full detail with 201. `GameConfig` is nested in
the request, so FastAPI rejects invalid configuration before endpoint execution.
Its field validators preserve locations such as `body.config.out_rule`, including
missing game-specific fields and odd `best_of`. API team validation requires two
teams, nonempty membership, distinct positive player IDs, and a starting-team index
within range. The repository retains one-team support. Unknown players produce 404;
archived players produce the existing `invalid_match` domain 422.

`GET /api/matches` returns `{items, total, limit, offset}`. `limit` defaults to 50
and accepts 1–100; `offset` defaults to 0 and must be nonnegative. `status` accepts
`in_progress`, `complete`, or `abandoned`; omission includes all three. Matches sort
by `created_at DESC, id DESC`. Only page members have their teams loaded; offset
pagination can shift if matches are created between page requests.

Both list items and detail include config, status, timestamps, winner, teams and
members, and `current_leg_id` (the latest leg, including the final leg on a finished
match). The resume card filters `in_progress` and uses this leg ID to navigate.
Live scores and turn position are loaded from #18 after navigation, not calculated
by this list endpoint. Every success response has an OpenAPI response model.

Abandonment sets `abandoned_at` once and leaves `completed_at` and winner null.
Repeating it returns the same result; abandoning a completed match returns 409
with reason `match_complete`. Existing play and undo refuse further mutations of
an abandoned match with `MatchAbandonedError`; a retry of an already recorded dart
remains a read-only idempotent response. No darts, visits, legs, or caches are deleted.

### Play API and generated client types (#18)

Four routes, all of which hand back the same complete state so the play screen
never needs a follow-up read:

| Route | Returns |
| --- | --- |
| `GET /api/matches/{id}/state` | `MatchStateResponse` — the fat read |
| `POST /api/legs/{id}/darts` | `MatchStateResponse` — 200, not 201 |
| `POST /api/legs/{id}/undo` | `MatchStateResponse` |
| `GET /api/legs/{id}/checkout` | `CheckoutResponse` — hints alone, for debugging |

Endpoints take `deps.ConnectionDep` and call `services.play`, which owns
scoring, rotation, busts, undo, advancement and its own transactions. The API
validates a payload and maps frozen dataclasses onto the wire; it decides no
rule. `services.state` still imports no web framework.

**The state read is addressed by match, the writes by leg.** `play.state` takes
a *leg* id, so `GET /matches/{id}/state` resolves the match's latest leg —
#17's `current_leg_id` — first. That is the leg in play for a live match, the
deciding leg for a finished one, and wherever play stopped for an abandoned one.

**`current_leg` and `active_leg` are different questions.** `current_leg` is the
leg that was addressed, shown complete with its winner if the last dart won it.
`active_leg` is the full state of the leg the next dart goes into, and is
present only when that is a *different* leg — which is exactly the one response
that reports a leg being won. It is null otherwise, including when it would
merely repeat `current_leg`, and when the match is over. A client can therefore
paint the finished scoreboard and start the next leg from a single response.

**`current_visit` and `previous_visit` are disjoint and leg-scoped.**
`current_visit` is the part-thrown visit and is null whenever the last visit
finished — bust, checkout, or third dart. `previous_visit` is the last visit
that *finished*, which is what the recap line reads. Neither reaches back across
a leg boundary, so the first visit of a new leg recaps nothing. `LegState` on
the service side carries the same split; there is no single "last visit".

**An abandoned match reads, but offers nothing to do.** `play._project` still
derives `active_leg_id` from unfinished legs, which is true of the rows: that
leg exists and its darts are worth reading. It is not true of the game, and #17
already refuses every dart and undo aimed at one. So the API reports `status`
explicitly and then withholds everything actionable — no `active_leg_id`, no
`active_leg`, no `next_thrower`, no hints and no named thrower on them. Scores,
marks, visits and the tally all still read.

**Checkout hints.** `services.hints.for_leg` is pure and wraps
`engine.checkout.suggest`, which is a lookup into the committed table and never
a search. Paths are serialised as throw labels — `["T20", "T20", "BULL"]`. An
empty `paths` always carries a `reason`: `not_x01`, `match_abandoned`,
`leg_complete`, `no_thrower`, `not_open` or `not_checkable`. `not_open` is the
double-in and master-in case: the table is built from the out-rule alone, so a
team that has not opened would be handed a path whose first darts do not score,
and suggesting nothing is better than suggesting that. Under the usual
straight-in a team is open from its first dart and this never fires.

**Validation happens during request parsing, not in the endpoint.** `DartWrite`
refuses an illegal segment, multiplier or combination as a field-level 422, so
`Throw(segment, multiplier)` cannot raise inside a handler — a `ValueError`
escaping there would be a 500. The legal set is derived from
`engine.throws.ALL_THROWS`, which has 63 members: 62 counts the board's scoring
segments and excludes the miss. `client_dart_id` is opaque non-blank text, not a
UUID; the column is a globally unique TEXT and nothing needs more than that.

**Idempotency is current-state, not stored responses.** A repeated
`client_dart_id` inserts nothing and returns the state as it stands now, so an
immediate retry is byte-identical to the first call. A retry sent after other
darts have landed returns the newer state, which is the honest answer — nothing
records historical responses or replays them. The same key describing a
different dart, or aimed at a different leg, is a 409 with reason
`idempotency_conflict`. Undo hard-deletes a dart, which frees its key again.

### Statistics API (#19)

Three reads, all taking `?game_type=&variant=&since=&match_id=`:

| Route | Returns |
| --- | --- |
| `GET /api/stats/players/{id}` | `PlayerReportResponse` — lifetime, within the filter, plus `?last_matches=` |
| `GET /api/stats/leaderboard` | `LeaderboardResponse` — ranked, plus `?min_darts=` and `?last_matches=` |
| `GET /api/stats/matches/{id}` | `MatchReportResponse` — per player and per leg |

The metric definitions and the reason the scope is composed rather than bound
live in [data-model.md](data-model.md#statistics-queries); this is the wiring.

`?last_matches=` was added by #27 and is **each player's own last N matches**, so
it means the same thing on a one-player report and down a leaderboard column,
where it makes a form table. It is a `WindowedFilter` subclass rather than a fifth
field on `Filter`, because `Filter` is also #20's export filter and the exports
have no window; widening the base would have added a parameter to `/api/export`
that nothing there implements. The two windowed endpoints echo a
`WindowedFilterResponse` and the other two still echo `FilterResponse`.

The echo reports the window *asked for*, not the one found: a request for ten
matches from a player who has played six echoes ten, while `matches_played` on the
same response says six. A screen labels its column from the latter, because "last
6 matches" is true where "last 10 matches" over six is not. The window is refused
on `/matches/{id}` — that report is already one match, so there is no window to
choose, and `extra="forbid"` makes asking a 422 rather than a parameter that looks
accepted and quietly does nothing.

`darts.stats` is a query layer beside `darts.repo`, not above it: `queries.py`
loads and binds the packaged SQL, `report.py` assembles rows into frozen
dataclasses, and neither opens a transaction. `services.stats` does, with a
**deferred** read transaction — a report is several statements and they must see
one database, but taking the write lock would make looking at a chart block a
dart being recorded.

**Identity is resolved through the repositories, not the statistics queries.** A
player who does not exist is a `NotFoundError` and a 404; a player who exists and
has never thrown is a well-formed 200 full of zeroes and nulls. A query returning
no rows cannot tell those apart, and the ticket requires both answers.

**Filters are parsed into a Pydantic query model**, so a bad `game_type` or an
unparseable `since` is a field-level 422 during request parsing rather than a
`ValueError` inside a handler, which would be a 500. `extra="forbid"` means
`?gametype=x01` is refused instead of silently answered with unfiltered numbers.
FastAPI only expands such a model when it is the route's *only* query parameter,
which is why the leaderboard's `min_darts` is a field on the model rather than an
argument beside it; `tests/api/test_openapi.py` pins that.

`GET /api/stats/matches/{id}` accepts `?match_id=` for uniformity with the other
two, but only if it names the match already in the path — a contradiction is a
422 rather than a silently ignored parameter.

#### Generated TypeScript client types

`frontend/src/api/schema.d.ts` is generated from the schema the app actually
serves and is committed. To regenerate it:

```sh
cd frontend && npm run gen:api
```

`uv run darts-openapi -o <file>` builds the real FastAPI app against a
**throwaway database in a temporary directory** and fetches `/api/openapi.json`
through the route rather than reading `app.openapi()` — routing order is
load-bearing here, and asking the object would not notice a schema shadowed by
the static mount. It requires an output path: `configure_logging` writes the
application log to stdout, so a piped schema would arrive with boot lines in
front of it. Output is sorted and indented so two dumps are the same bytes.

`npm run gen:api:check` regenerates in memory and compares, failing on a stale
*or missing* file. It never writes: a check that repaired what it was checking
would pass on a branch that never committed the file, which is the one failure
it exists to catch. CI runs it in its own `contract` job, because generation
needs Python and Node together while the `frontend` job is Node-only.

`openapi-typescript` still declares a peer of `typescript@^5.x` while this repo
is on 6.0. It only uses the compiler API to build and print an AST, so
`package.json` overrides that peer to the TypeScript already installed rather
than resolving a second copy. Generated output is run through the repo's own
Prettier config, so it passes `npm run lint` like every other file instead of
being exempted from it.

### Export API and DB snapshot generator (#20)

Getting data out, over HTTP and as a file for desktop tools.

| Route | Returns |
| --- | --- |
| `GET /api/export/matches.csv` | One CSV row per match |
| `GET /api/export/darts.csv` | One CSV row per recorded dart |
| `GET /api/export/stats.json` | `StatsExportResponse` — every player plus the leaderboard |
| `GET /api/export/db` | A fresh point-in-time copy of the database |
| `POST /api/admin/snapshot` | `SnapshotResponse` — the published manifest |
| `POST /api/admin/rebuild-caches` | `RebuildResponse` — legs visited and changed |

All three exports take #19's `?game_type=&variant=&since=&match_id=` filter,
reusing `api.stats.Filter` unchanged; `stats.json` adds `?min_darts=`. The two
CSV headers are a published contract and are documented, column by column, in
[data-model.md](data-model.md#the-published-csv-exports).

Nothing here is authenticated, including `/api/admin`. That is the same trust
model as `POST /api/matches`: one household, one LAN, behind one router. Both
admin actions are synchronous and idempotent, so a double-tap or a retry is
harmless. Anyone exposing this box beyond the LAN has to revisit that decision
for the whole app, not for these two routes.

#### Copying, publishing and describing a database file

`darts.db.artifact` holds what a backup and a snapshot both need, extracted from
#12 rather than duplicated: copy through `Connection.backup()` (which reads
pages inside a read transaction, so a copy taken mid-game is a point-in-time
image where `cp` would tear), collapse the copy's WAL so the artifact is one
self-contained file, write the temporary **in the destination directory** and
publish it with `os.replace`, and build the manifest by reading the *finished*
file back. `db.backup` adds the timestamped history and retention; `services.
snapshot` adds one fixed filename.

`services.snapshot` publishes `darts-latest.db` and `snapshot.json` into
`DARTS_SNAPSHOT_DIR` (default: `snapshots/` beside the database). The name never
changes, because the point is a path that can be written into #30's Samba
config, a cron job or a bookmark and stay correct. `uv run darts-snapshot
/path/to/darts.db` does the same thing from a shell.

The database is renamed into place *before* its manifest, as backups do: an
interrupted run can leave a snapshot whose `snapshot.json` still describes the
previous one, but never a manifest promising a snapshot that is not there. The
manifest carries `created_at`, so a reader can always tell which it has.

#### Streaming, and why two routes open their own connection

`GET /api/export/db` never serves the live database. That file is in WAL mode, so
its most recent committed pages are in a `-wal` sidecar the client would not
receive — the download would be missing the last few darts and no desktop tool
could open it without the sidecar. Each request takes a fresh copy, streams it,
and deletes it afterwards, including when the download is abandoned. It does not
touch `darts-latest.db`: a download must never be able to leave the shared
snapshot half-written.

The CSV routes are the one place in the app that does **not** take
`deps.ConnectionDep`. A `StreamingResponse` body is consumed *after* the
endpoint returns, by which time the dependency's `with connection(...)` block
has closed the connection and the cursor behind it. So those routes open a
connection inside the generator that produces the body and close it when
iteration ends — normally, on an error, or when Starlette closes the generator
because the client disconnected. The connection is still used sequentially by
exactly one request, which is the rule `deps` states;
`check_same_thread=False` because Starlette drives a sync generator through the
threadpool. `stats.json` is not a stream and uses `ConnectionDep` like
everything else.

Streaming is real at both ends. `services.export` yields one formatted line at a
time off an open cursor, and the `ORDER BY` on `export_darts` is satisfied by
walking `legs(match_id, leg_index)` then `darts(leg_id, seq_in_leg)` — indexes
the schema already has — so SQLite needs no sorter either.
`tests/stats/test_query_plans.py` asserts that over 50,000 darts, because a
header reordering that quietly reintroduced a temporary B-tree would still be
correct and would start buffering the whole table on a Pi.

#### Rebuilding the caches

`POST /api/admin/rebuild-caches` sweeps **every** leg, not only the unfinished
ones, one transaction per leg. `leg_team_state` and `cricket_leg_state` exist
only to resume an interrupted leg, so a finished leg correctly holds no rows and
`services.play.rebuild_caches` deletes as readily as it writes. A sweep limited
to unfinished legs could therefore never clean up a finished leg that wrongly
held some, which is exactly the drift worth repairing. Over a correct database
it changes nothing and reports `legs_changed: 0`. One transaction per leg rather
than one for the sweep, because this can run while somebody is throwing.

### Home screen and player management (#22)

#### A player is a name, a colour and a short name

#22's scope assumed two attributes that had never existed. `players` held
`display_name`, `is_archived` and `created_at`, and `PlayerWrite` forbade
anything else, so there was no colour to pick and nowhere to put a scoreboard
label. `0003_player_identity.sql` adds both as nullable columns, because
`ALTER TABLE ADD` cannot invent a per-row value and every player created before
it genuinely has neither.

**The colour is stored as an index, not a colour.** #4's eight accents were
found by a search maximising the smallest perceptual distance across normal
vision and simulated protanopia, deuteranopia and tritanopia, and its docstring
forbids hand-editing them. Hex values copied into the database would be a second
definition free to drift from the one the screens paint with, so `accent_index`
is 1–8 and `tokens.css` stays the single source.
`tests/db/test_accent_palette.py` fails if the count on either side moves.

**Uniqueness is deliberately not a constraint.** "Two players cannot be assigned
the same accent colour" is impossible past eight active players, and the choice
between refusing a ninth person, reusing silently, and reusing visibly was
Jack's. Reusing visibly won: `repo.players.next_accent_index` hands out the
lowest-numbered least-held accent — always a free one below nine players — and
the picker marks a taken swatch and names who holds it. The rule that matters
is enforced where it can be: the accent is chosen *inside* the write
transaction, so two phones adding a player at the same moment cannot both be
handed the same free colour.

**`PATCH` is genuinely partial**, for these two fields only. A `None` cannot
mean both "leave it alone" and "clear it", so `repo.players.UNSET` is the
absence and `None` is the clearing; the route reads `model_fields_set` to tell
the two apart. Without that, renaming a player through a client that sent only
`display_name` would silently erase their colour.

#### The resume card is absent, never empty

`GET /api/matches?status=in_progress&limit=1` answers the home screen in one
request, and `MatchResponse` already carries the teams and their members, so the
card can name who is playing without a second call. It renders nothing when
there is nothing to resume, and nothing while the answer is still in flight: a
card is a claim that a game is waiting, and a skeleton would make that claim
before it is known and then take it back.

It links to `/play/:matchId` rather than to a leg. `current_leg_id` is on the
payload, but which leg is current changes while you walk to the board, so the
play screen resolves it — the same reason the route was shaped that way in #21.

Nothing on the card is guessed. `variant` and `start_score` are each nullable
because the other game type has no use for them, and a match arriving without
the one it needs is described by what it does have rather than by a
plausible-looking default.

**It is the newest in-progress match, and there can be more than one.** #22
asserted here and in `api/matches.ts` that there could not, on the grounds that
#23 would refuse to start a match while another was in progress. That was a
prediction about an unwritten screen stated as a fact, and neither side does
it: two `POST /api/matches` in a row both return 201, and #23 warns rather than
refuses. `list_matches` orders `created_at DESC, id DESC`, so the card shows the
newest — and an older one is not lost, it stays in progress and #26's history
screen will show it. #23 corrected the comment.

#### Archived players are hidden by the server, not by a screen

`GET /api/players` excludes them unless asked, which is the list every picker
will get — so "hidden from the picker" is not a rule #23 onwards can forget to
apply. The management screen is the one caller that passes
`include_archived=true`, and it exists because a screen that cannot show you
what it retired is hard to trust. Un-archiving is in the repository and not on
the API; #22 did not need it and did not add it.

#### One inline error, branched on the discriminator

A repeated name comes back as a 409 whose `detail.reason` is `duplicate_name`,
and the form branches on `reasonOf(error)` — not on the status, which several
distinct refusals share, and not on the message, which is prose. It lands under
the name field rather than in a toast, because it is a fact about that field,
and it clears the moment the name changes.

#### Screens are tested through the router, in front of a mock Pi

`src/test-harness.tsx` mounts `App` under a `MemoryRouter` and the app's own
`createQueryClient`, with MSW answering. Screens are therefore never imported
directly by a test: a route wired to the wrong component fails in the test
rather than in somebody's hands, and retry and staleness behave as they will on
the phone. Its query client gets a fresh `ConnectionMonitor` rather than the
module-level one, which would otherwise carry a dropped connection between
tests.

It is not a `*.test.tsx`, so it is named explicitly in three places —
`tsconfig.test.json`'s includes, `tsconfig.app.json`'s excludes, and the
coverage excludes. It is scaffolding, and counting it as app code either way
would be wrong.

`RootLayout` owns the notch and nothing else, so each screen owns its own
gutter, exactly as `Placeholder.css` has since #21.

### Match setup screen (#23)

`/setup` builds a `POST /api/matches` body and nothing else. It never sees a
leg: on 201 it navigates to `/play/:matchId` and #24 takes over.

#### The screen holds a reducer, and the reducer holds everything

`setup/config.ts` is the whole of #23's logic — a `SetupState`, a pure
`reduce`, and a `buildMatch` that turns state into the generated `MatchWrite`.
`Setup.tsx` renders it and posts it. Three things follow that would otherwise
be rules somebody has to remember:

- **The start button and the payload are one decision.** `buildMatch` returns
  `null` exactly when the state is not startable, which *is* #23's "at least 2
  teams and every team has at least 1 player". The button is disabled when it
  returns `null` and posts what it returns otherwise, so the two cannot
  disagree about whether this is a match.
- **Changing the game type keeps the teams**, because the `game` action copies
  `assignments` through untouched. It is a property of the reducer rather than
  something each screen has to be careful about.
- **The enumeration is possible at all.** `config.test.ts` walks every
  reachable state — 6 games × 3 in-rules × 3 out-rules × 5 leg counts × 7 team
  shapes — and checks each body against the rules transcribed from
  `repo/config.py` and `api/matches.py`.

It is a `.ts` and not part of `Setup.tsx` because
`react-refresh/only-export-components` fails a file exporting both a component
and something else — the same reason `api/connection.ts` sits beside
`components/ConnectionToast.tsx`.

#### Tapping a player fills the smaller team

#23 says both "solo is the default, one player per team" and "a 2v2 match is
reachable in 6 taps or fewer". Those cannot both hold literally: four solo
players are a four-way match, and pairing them up costs two taps more than the
budget allows. Jack's call was to read "solo is the default" as "the default
for two players" and make the fill alternate.

So a tap on an unselected player puts them on the smaller team, A on a tie.
From empty that is round-robin — A, B, A, B — which makes two players a 1v1,
three a 2v1 and four a 2v2 with no pairing taps at all. Phrasing it as "the
smaller team" rather than "every other tap" is what keeps it sensible after
somebody is moved or dropped: the next tap refills the gap instead of counting
past it.

Tapping again walks a cycle: the team the fill chose, then the other one, then
off the list. `Assignment.origin` records where the fill put them, and that is
what makes the cycle total — a fixed A → B → off cycle would strand anyone the
fill dropped on B, who could then never be moved to A. Uneven teams are one
tap away in either direction, which is what #23's 2v1 and 2v3 ask for.

Two teams, not N. The server is happy with more and `rotation.starting_team`
rotates through any number, but a team-count control is not in #23, and a third
bucket nobody asked for would cost a tap on the way to every match that does
not want one. `TeamId` is `'A' | 'B'` rather than `0 | 1` so that confusing a
team with an array index cannot typecheck.

#### Legs to win, because every reachable value has to be legal

`GameConfig` requires an odd `best_of` — `CHECK (best_of % 2 = 1)` in
`0001_init.sql`, mirrored by `_check_odd_best_of`. #23 asked for a "best-of
stepper (1, 3, 5, 7, …)", and #4's approved mockup showed `Stepper label="Legs
to win" min={1} max={9}` with the default step of 1, which would walk straight
through the even numbers the database refuses.

The screen steps legs by one and sends `best_of = 2 × legs − 1`. Every value a
thumb can reach is legal by construction rather than by a stepper whose `+`
adds two, and "first to three" is how the count is said out loud. `MAX_LEGS` is
5, which is the mockup's `max={9}` read as the best-of it actually was.

#### One flat picker over six games

#23 lists 301 / 501 / 701 / Cricket / cut-throat / quick; #4's mockup had a
two-way x01-or-cricket control plus a chip row and no variant control at all.
The backlog wins, and it is also the shape that keeps the six-tap budget: 501
is the default, so the common case costs no taps in this section. It does mean
the half of #23's first criterion about "hiding the variant control" is
satisfied by construction — there is no separate variant control, because the
variant is part of the choice.

The x01 in-rule and out-rule controls are absent rather than disabled under
cricket: a greyed-out "Double out" beside a cricket game implies it could
apply, and it cannot. The leg stepper stays, because every game is played over
legs. Both x01 rules survive a trip through cricket and back, so switching game
type is never destructive.

#### The starter is hardcoded to `alternate`

`GameConfig` carries `start_rule` and `fixed_team`, and the engine implements
all four rules in `rotation.py`. #23's scope has no starter control and neither
did the mockup, so the screen sends `alternate` and `fixed_team: 0` — the
values the server would have defaulted to. `loser_starts` is the common pub
convention and is unreachable from the UI today; **#24 owns exposing it.**

Both fields are sent explicitly rather than omitted. The served schema's
`required` for `GameConfig` is only `['game_type', 'best_of']`, but
`openapi-typescript` emits a property with a default as always present, so the
generated type demands them. Sending them typechecks, is valid, and is honest
about what the match is. The generator is not the thing to fix.

#### A match in progress is a warning, not a wall

Opening `/setup` while something is still being played shows a line naming it
with a link to resume, and leaves the start button alone. Nothing on the server
stops a second match — two `POST /api/matches` both return 201 — and on a
shared phone at a board, "start another one" is a thing people legitimately do.
What would be wrong is starting one without saying the first is still open.

#### The payload claim was measured, not just asserted

"The payload posted validates server-side on the first try for every reachable
UI configuration" is a claim about all of them, which worked examples cannot
discharge. It was checked two ways during #23:

1. `setup/config.test.ts` enumerates the reachable states and asserts each body
   against the constraints transcribed from the server. This is the committed
   test.
2. The same enumeration — 1,890 bodies — was dumped and fed through the real
   `MatchWrite.model_validate`. All 1,890 were accepted, with positive controls
   confirming the validator still rejected an even `best_of`, an x01 config
   carrying a variant, and a player on two teams. Six of them, one per game the
   picker offers, were then posted through a live `TestClient` and came back
   201 with the teams echoed exactly.

The second is a development-time measurement rather than a committed test,
because a committed one would mean a backend test file for a frontend ticket.
The committed test is the transcription, and the transcription is the thing to
re-read if the server's rules ever move.

### x01 play screen (#24)

`/play/:matchId` is the screen the whole app exists for. It renders and it
posts, and that is all it does — which is possible because #18's play API is
unusually generous.

#### It is a rendering problem, not a game-logic one

Every write returns the complete new `MatchStateResponse`, so a dart is one
round trip and one repaint. The checkout paths, the bust and the score it
reverted to, the thrower, both visits and the leg tally all arrive already
decided. Three things the client conspicuously does **not** do:

- **No checkout table.** `CheckoutResponse.paths` is best-first and already
  accounts for the out-rule and the darts left in the visit, recomputed on
  every response. So "the hint updates after every dart and respects both" is
  discharged by rendering whatever the latest payload carried.
- **No bust revert.** `score_after == score_before` on a bust *is* the revert,
  and `caused_bust` names the dart. The screen draws a banner.
- **No optimistic update and no patched state.** Mutations write the response
  into the cache with `setQueryData` rather than invalidating, because the
  authoritative answer is already in hand. A scoreboard repainted from two
  responses can show a score from one and a thrower from the other.

`play/x01.ts` and `play/keypad.ts` hold the decisions and `Play.tsx` renders
them — the `setup/config.ts` split, for the same `react-refresh` reason and the
same enumerability.

#### Sixty-three throws, not sixty-two

#24 said 62 twice, in an acceptance criterion and in a test requirement. The
engine says 63 and `engine/throws.py` ends with a comment written specifically
to pre-empt this off-by-one: 62 counts the board's *scoring* segments, which
excludes the miss. A miss is a legal thing for a dart to do, `DartWrite` accepts
`(0, 0)`, and #4's mockup gave it a key. The ticket was corrected to 63.

#### The latch covers the numbers, and only the numbers

The multiplier latch applies to the 20 numbered keys — 60 throws. 25, BULL and
MISS are absolute: they post `(25,1)`, `(25,2)` and `(0,0)` whatever the latch
says, and the latch still resets afterwards. There is no triple bull (`Throw`
raises and `DartWrite` 422s on `(25,3)`), BULL already *is* the double of 25, so
"double 25" would be a second spelling of an existing key, and a miss has no
multiplier. Disabling them while latched was the alternative; it costs a tap and
hides a third of the board behind a mode. Being absolute is what keeps all 63
throws one tap away and makes an unsendable combination unreachable rather than
merely refused.

23 keys × 3 latch positions is 69 taps, of which the nine on the absolute keys
collapse to three darts — 63.

The latch resets to Single on the tap that *sends*, not on the response. A tap
dropped by the in-flight guard never reaches the reset, so a suppressed tap
cannot silently change what the next one means. While the latch is on, each
numbered key captions itself with what it will actually post — "60" under the 20
on Triple — which is the cheapest available guard against the mis-entry the
resetting latch exists to prevent.

#### Idempotency does not stop a double tap

#24 asks that a double tap not submit two darts and attributes it to #15's
`client_dart_id`. That is not what idempotency does: a fresh id per tap makes two
taps two *different* darts, and the server records both, correctly. Nor can the
two be told apart in general — T20-T20-T20 is the most common sequence in the
game, so a time-based debounce would eat real throws.

So the suppression is in the client: while a request is in flight, a tap on a
throw key is ignored outright. On the LAN that window is a couple of
milliseconds, which is why the keys are not greyed for it. The test asserts that
**one request reached the server**, not merely that two ids matched.

Where `client_dart_id` genuinely earns its keep is the retry. Mutations never
retry automatically, because a dart whose request timed out may well have been
recorded. A failed dart keeps its id, and "Try again" re-sends the identical
body — which the server either records once or recognises and answers with the
current state. A 4xx gets no retry button, because it will fail identically
forever.

#### The 402×874 criterion is verified by construction, not tested

jsdom does no layout: `getBoundingClientRect` is uniformly zero, so any "not
taller than 874px" assertion passes vacuously — against a screen 3000px tall as
readily as this one. There is no viewport-test precedent in the repo and this
ticket did not invent one.

What the screen does instead is what #4's `mockup.css` does: `height: 100dvh;
overflow: hidden`, with every row above the keypad `flex: none` and the keypad
grid the single `flex: 1 1 auto`. A row that appears — the bust banner, the
error strip — takes its height out of the keys, which stop shrinking at the 56px
touch floor, at which point the overflow **clips visibly** instead of quietly
becoming scrollable. A layout mistake is then something you can see rather than
something you discover with three darts in your hand.

`Play.test.tsx` asserts those declarations by parsing the stylesheet as text,
which is the `tokens.test.ts` precedent and the only honest option: jsdom does
not implement `env()` either, so `getComputedStyle` on a safe-area inset
resolves to 0 and would pass against a rule with no padding at all. **The pixel
fit is verified by construction and belongs to #32's device pass.**

#### Two legs, one route, and the ends

- **`active_leg`** is non-null for exactly one response in a match: the one
  reporting a leg won, where `current_leg` is the leg just finished. The screen
  prefers it, so the board rolls straight on and the win shows up in the tally.
  #26 owns the interstitial that stops to say who won it.
- **Undo addresses the leg that has a last dart**, which at a leg boundary is
  the *won* leg rather than the empty one opened behind it. `play.undo` supports
  exactly that — it reopens the leg, closes the empty one and unwins the match
  if that leg decided it — and refuses only when a *later* leg has been thrown
  into. Undo therefore stays available on a won match, which is the only way to
  fix a mis-entered winning dart, even though darts are refused.
- **A cricket match gets a notice naming #25**, not this board and not a crash.
  `/play/:matchId` serves both game types and #23 will happily start a cricket
  match and navigate here today.

#### The three-dart average came from the server

#4's mockup renders an average on each `ScoreCard` and no play payload carried
one. Deriving it in the client would have put a second definition of a statistic
#19 already owns next to the one on the history screen, where the two would
eventually disagree — so `TeamLegResponse` was widened instead.

It is #19's arithmetic exactly: `3 × sum(counted × score) / darts`, from
`stats/sql/x01.sql`, and #19's rule for an empty one — a count of nothing is 0,
an average of nothing is `None`. A busted visit therefore scores nothing while
its darts still cost their place in the denominator, which is the case a client
computing `(start − remaining) / darts × 3` would get wrong.

Two scopes differ from `x01_totals`, because a live scoreboard is not a player's
record. It is per *team*, since that is what a `ScoreCard` is, and it pools both
members in a 2v2. And it is per *leg*, matching the `remaining` it sits beside —
which also means it needs nothing beyond the replay `play._project` has already
done, rather than a new query on the hot path of every dart. It is computed in
the projection and kept off `TeamCache`, so `leg_team_state` and #13's verify
pass over it are unchanged and no migration was needed.

#### Who a 2v2 card names

`ScoreCard` has one name slot, `MemberResponse` still has no `short_name` (#22
flagged that widening and it is open), `TeamResponse.name` is nullable and #23
never sets it — and two unbounded display names will not fit beside a 60px
score. So the card leads with whoever is actually at the oche, which is the
thing the screen exists to say, and puts the rest of the team on the second line
where it can ellipsize. A team whose turn it is not leads with its first member.

#### The keypad is a component because #25 is built on it

#25's scope is literally "the cricket board, reusing the keypad from #24", so
the keypad has its own props and its own tests rather than being markup inside
this screen. `KeypadKey.segment` is the hook it needs: cricket dims the numbers
outside 15–20 and 25 while leaving them enterable, because a dart that lands on
7 still happened. `ScoreCard` now takes `score: number | null` for the same
reason — a cricket team has marks where a score would be.

#### The wake lock is re-acquired, not just requested

`navigator.wakeLock` is typed non-optional in `lib.dom.d.ts` but is absent in
jsdom, on older iOS, and — the one that matters — outside a secure context,
which is what the Pi serves on the LAN. The feature test is load-bearing rather
than defensive.

The browser also releases the lock whenever the page stops being visible and
never gives it back. Without the `visibilitychange` re-acquire the screen would
stay awake until the first time anybody glanced at another app and never again
for the rest of the match — a failure that looks exactly like the feature was
never wired up, and one that a test asserting `request` was called once cannot
see.

#### The 63 throws were measured against the server's own validator

"All 63 legal throws are reachable from the keypad" is a claim about all of
them. It was checked two ways:

1. `play/keypad.test.ts` walks all 69 (key, latch) pairs and asserts the
   distinct darts equal the 63 built from the board; `routes/Play.test.tsx`
   drives the same 69 taps through the real keypad and asserts on the 69 bodies
   that reached the wire. These are the committed tests.
2. The same 69 bodies were dumped and fed through the real
   `DartWrite.model_validate`. All 69 were accepted, 0 rejected, and the set of
   distinct throws was *identical* to `engine.throws.ALL_THROWS` — neither a
   throw missing nor one the board does not have. Positive controls confirmed the
   validator still refused `(25, 3)`, `(0, 1)`, segment 21 and a blank
   `client_dart_id`.

The second is a development-time measurement rather than a committed test, for
#23's reason: a committed one would mean a backend test file for a frontend
ticket.

### Cricket play screen (#25)

The cricket board reuses #24's keypad, `PlayFrame` and the leg-level helpers,
and adds `play/cricket.ts` for the decisions that are cricket's alone: which
glyph a mark count draws, when a target is dead, and whether the points column
exists at all.

#### The ticket and the mockup disagreed, and the ticket won

#4's `CricketMockup.tsx` draws an eight-key keypad — 20, 19, 18, 17, 16, 15,
BULL, MISS. It is the only way seven 44px mark rows fit, and it makes a dart at
12 literally unreachable, which contradicts #25's own scope line ("non-target
numbers dimmed but **still enterable**") and its fifth criterion. Its single
BULL key is a second problem: cricket's outer bull is one mark and the inner is
two, so a board with one bull key cannot record the difference.

The arithmetic was measured in Chrome at 402×781 — the 402×874 device less the
59px Dynamic Island and 34px home-indicator insets, which is the content box the
frame actually gets:

| Row | px |
| --- | --- |
| topbar, and the frame's gaps | 96 |
| multiplier latch | 66 |
| keypad grid, 4 rows at the 56px floor | 248 |
| actions row, with its margin | 64 |
| visit strip | 32 |
| **left for the board** | **271** |

The mockup's board needs 410. So the board was compressed instead of the keypad
being cut: a single-line header rather than the mockup's stacked block, rows at
~30px rather than 44, and a 2px row gap. Correctness over fidelity — a dart at
12 is still a dart.

#### The board stretches and the keypad is pinned

This is the inversion of the x01 board and it is load-bearing. There the keypad
grid is the one `flex: 1 1 auto` and every other row is fixed. Here
`.cricket .keypad__grid` is `flex: 0 0 auto`, pinned to exactly its
56px-per-key minimum, and `.cricket__board` is the row that grows.

They cannot both grow. As siblings in one flex column the keypad's much larger
basis wins every argument, and the board is starved into rows that overlap each
other. Pinning the keys is also what makes the touch floor a floor: an error
strip now costs the seven mark rows a couple of px each instead of pushing the
UNDO key off the bottom of the screen, which is the one key you need when there
is an error on screen.

The mark rows carry `min-height: 0` for the same reason — they are what gives
way. Every grid item also carries an explicit `grid-row: 1`: the target spine
leads in the DOM so a screen reader hears "20" before the two cells on it, but
sits in column 2 on screen, and without the explicit row the first mark cell is
placed *behind* it and sparse auto-flow pushes it on to a second row, silently
doubling the height of all seven.

Both of those were caught by looking at the screen and by nothing else. jsdom
does no layout, so no test here could have seen either.

#### Dead is a drawing decision, not the engine's

`engine/cricket.py` asks whether every *opponent* has closed a target, because
surplus only pays out while somebody is still open to score against. #25 asks
for "closed by every team". For two teams — the only shape #23 creates — the
two coincide; for three they would not. Nothing on the client feeds a scoring
rule, so the ticket's wording is what `rowIsDead` implements, and the difference
is written down rather than papered over in a comment claiming to be the
engine's.

One deliberate divergence from the engine: with no teams at all `_is_dead` is
vacuously true, which is right for a scoring rule and wrong for a drawing one.
A board with no columns has nothing finished on it.

#### The points column is absent in quick, not greyed

`quick` wastes every surplus mark, so every total is zero for the whole leg.
Measured: the same three darts (T20, T20, 19) pay the thrower 60 under
`standard` and nothing under `quick`. A column of zeroes would invite the player
to wonder what moves it, so it is not drawn at all — the same call #23 made
making cricket's in/out rule controls absent rather than disabled.

#### Cut-throat attribution is the totals, plus what moved

`TeamLegResponse` carries each team's running points; `DartResponse` carries
nothing cricket-specific, so there is no per-dart "+60 → Dad" without widening
the backend. It was not widened. The criterion's claim — points accruing to the
opponents and not to the thrower — is already visible in the totals, because the
thrower's demonstrably does not move, and `changesBetween` notices which one did
so the board can draw attention to it.

That diff derives nothing about the rules. A cell closed because the server says
it now holds three marks and said it held fewer a moment ago. Working out who
*should* have been paid is the one cut-throat rule that would be easiest to get
subtly wrong, and the client never attempts it.

The diff is keyed on the payload's identity rather than the view's: react-query
returns the same `MatchState` object until a new response replaces it, so the
effect fires exactly once per payload. The first payload reports nothing, or a
board opened mid-match would announce every already-closed number at once.

#### What was verified, and how

The preview tooling worked for the first time in this repo, so #25's two
browser-shaped criteria were checked in a real browser rather than by
construction. The 402×874 fit was measured at 402×781 in Chrome with the insets
modelled rather than emulated — that is stronger than #24's "by construction"
and still not a device, so it joins #32.

The mark rendering was also measured against payloads the server really sends:
120 legs, 40 per variant, driven through the real API with random throws from
`ALL_THROWS`, producing 7,194 `MatchStateResponse` payloads. Every one was fed
through the real `boardView`, giving 100,716 cells covering all four mark counts
and all three cell states, with no disagreement between a cell and the payload
it was built from, and no `quick` leg carrying points. A development-time
measurement rather than a committed test, for #23's reason: a committed one
would mean a backend test file for a frontend ticket.

### Leg/match complete sheets, history and match detail (#26)

Four of #26's six criteria were buildable against the API as it stood. One was
not, and settling that decided the shape of the ticket.

#### The dart-by-dart view needed a route that did not exist

Criterion 3 asks that "the dart-by-dart view renders busted visits struck
through with an explicit bust marker". No endpoint returned darts.
`GET /matches/{id}/state` carries `current_visit` and `previous_visit` of the
current leg only, and both move on as the leg does, so a finished leg's visits
are on no later response — no sequence of reads reconstructs them.
`/stats/matches/{id}` is one line per player per leg, with no visits.
`/export/darts.csv` was the only per-dart data leaving the server.

The alternative was to parse that CSV in the browser. It was rejected for the
reason #24 widened the backend rather than have the client derive a second
three-dart average: it would have meant a second parser and a second
client-side definition of `counted` and `caused_bust`, against a header whose
own docstring calls it a published contract for spreadsheets.

So `GET /api/matches/{match_id}/darts` was added — read-only, no migration,
`services.play.history`. It is `_visits` without the last-two truncation and
reuses `_visit_state`/`_dart_state` verbatim, so there is one projection of a
dart rather than two. The route reuses `VisitResponse`, which means the
generated client gives the history screen the same dart type the play screen
already had. Unpaginated on purpose: the grain is one match, bounded by
`best_of`. It is the match *list* that #26 pages.

#### The sheet branch is read off the payload, not computed

Criterion 1 — the deciding leg shows the match sheet, not the leg sheet — is a
branch the server has already decided, and `play/sheet.ts` reads it:

* a non-deciding leg win is the one response in a match with a non-null
  `active_leg`, because `services.play` opened the next leg on the winning dart;
* a deciding leg win opens no next leg, so `active_leg` is null and
  `is_complete` is true instead.

The two are mutually exclusive on the payload, so nothing counts legs towards
`best_of`. A client that did would have to know that a best-of-11 can end at
leg 6 or at leg 11 depending on how the legs fell.

`legInPlay` was left alone, which #24 asked for. The board behind a leg sheet is
already the new leg, so "continue" is a dismissal rather than a state change:
there is no "start next leg" call and none is needed, and criterion 2's correct
starting team is displayed from `active_leg.next_thrower` rather than alternated
client-side. `components/Sheet.tsx` gets its first consumer since #4.

#### The leg sheet is transient; the match sheet is not

`active_leg` is non-null for exactly one response, so the leg sheet appears on
the winning dart and cannot return after a reload — the visit that finished the
leg is on no later response. That asymmetry is deliberate and was ruled on
rather than worked around: the sheet is a moment, not a destination, and
criterion 5's "survives a page refresh" is about match detail. What survives is
the result itself, in the tally and in `/stats`, and every dart on the detail
screen. The match sheet does come back, because `is_complete` is durable.

Per-player leg averages come from `/stats`, not from `TeamLegResponse` — that
one is per *team*, and #26 asks for per player.

#### Saying "these darts counted but scored nothing" three ways

A strike-through alone reads as deletion — as though the darts were taken back,
which is what an *undo* would have done and the opposite of what a bust means.
So a busted visit carries the darts struck through, an explicit `BUST` marker so
the strike is not the only signal and is not carried by colour, and the dart
count stated beside it and deliberately outside the struck-through run, because
that is the clause the strike would otherwise contradict.

#### Measured at development time

Twelve real matches were driven to completion through the API at every best-of
the schema allows, under both start rules: `fixed`, where one team sweeps and
the match is decided as early as it can be, and `alternate`, where the teams
trade legs and it goes the distance. 855 responses were checked, and on every
one the sheet branch fired on exactly the leg it should — leg sheets on every
completed leg before the decider with distinct leg ids, exactly one match sheet,
never a leg sheet on a completed match and never a match sheet with a next leg
to throw into. A development-time measurement rather than a committed test, for
#23's reason.

#### What the browser caught that jsdom could not

Both were invisible to 544 passing tests. The status filter was four options
wide; at 402px `SegmentedControl` gives each 83px and "Abandoned" needs 98, so
the label overflowed its own button. It is three options now — "Playing" was the
one to lose, since a match in progress is still in the unfiltered list and the
way back into one is the home screen's resume card. And `Sheet`'s body stacks
its children as plain blocks with no rhythm, which left "X throws first in leg 2"
flush against the Continue button, their edges meeting exactly. The gap lives in
`CompletionSheet.css` rather than `Sheet.css`, because it is a fact about what
this screen puts in a sheet rather than about sheets.

Both sheets and both new screens were checked at 402×781 in Chrome: no
horizontal overflow, nothing clipped, both sheets inside the viewport without
scrolling, and both sheet actions on the 56px floor. Verified in a real browser,
not on the device; it joins #32.

#### Two things this touched outside its own screens

`describeMatch` and `opponents` moved out of `Home.tsx` into
`matches/history.ts` rather than being copied, so "what do we call a 501
best-of-5" has one definition. And the home screen gained a History link: #22
left `/history` reachable only by typing the URL, and criterion 5 only asks for
a deep link, but a list nobody can reach by tapping is not a screen.

### Stats screens (#27)

Two screens at `/stats` and `/stats/:playerId`, mirroring `/history` +
`/history/:matchId`: the leaderboard ranks everybody, a row opens that player's
card, and both are addresses somebody can send. The last `Placeholder` went with
them, so the component is gone and `NotFound` keeps its stylesheet.

#### The render path chooses and formats, and never computes

Criterion 1 — "every number on screen maps directly to a field in the
`/api/stats` response" — is the spine of the ticket and the shape of
`stats/stats.ts`. A `Metric` is a label, a field and a formatter; `read` takes a
whole `PlayerStats` and returns a *string*, so there is nowhere to put a
calculation even if somebody wanted one. Checkout percentage is read from
`checkout_percentage`, never divided out of `checkouts_hit` and
`checkout_attempts` — both of which are in the same payload, which is exactly why
the criterion needs a test rather than a promise.

Two tests make the claim, and they fail differently. The **disagreeing payload**
is the sharp one: `statsfixture.ts` sets every authoritative figure to a value its
own ingredients would *not* produce — 5 of 17 checkouts beside a stated 33.3%,
`3 × points_scored / darts_thrown` of 57.13 beside a stated 57.23 — so a screen
that divided would put a number on the page that no field backs. The **DOM walk**
is the broad one: it tokenises every leaf element's text and asserts each number
is reproducible from some field by formatting alone.

Both were checked against a deliberate violation while being written. That
mattered: the first version of the walk *passed* an injected
`(checkouts_hit / checkout_attempts) * 100`, because the fixture's ingredients
happened to agree with its stated percentage and the computed number was
indistinguishable from the real one. Making them disagree is what gave the test
teeth, and it now fails such an injection with `[ '29.4%' ]`.

Tokenising per leaf element rather than over `document.body.textContent` is not
incidental. The latter glues adjacent cells together — two 660s become "660660" —
which is the same concatenation that makes every component here build an explicit
`aria-label`, and it would invent numbers the test then blamed on the render path.

**The bar geometry is not a statistic.** The segment visual sizes each bar with
`darts / max`, written to a `--fraction` custom property and turned into a length
by CSS. It is arithmetic, and it is deliberately outside the criterion: no number
a reader sees comes from it, the walk is over text nodes, and a test asserts the
fraction never reaches the document as text. Jack ruled on this before it was
built; the alternative was a purely categorical visual with no proportional
sizing.

**The rank is not computed either.** Rows arrive in the server's order and are
rendered in an `<ol>`, so the position *is* the rank — conveyed structurally to a
screen reader and drawn for everybody else by a CSS counter. `index + 1` would
have been the one number on the page that no field of the response backs.

#### "Recent" needed a filter that did not exist

Scope asks for a 3-dart average "lifetime and recent", and #19 had no way to say
it: `?since=` is a date, and "my last ten games" is a count. `_PREDICATES` was a
closed set of column comparisons with nothing in it that could express a window.

So the window was added to #19's SQL layer rather than approximated here — a
recent average computed in the client is precisely what criterion 1 forbids. See
[data-model.md](data-model.md#the-one-predicate-that-is-not-a-column-comparison)
for why it is a pair set and why it is ranked once rather than correlated. The
card asks the same endpoint twice, unwindowed and windowed, so both columns are
the server's own calculation over different scopes.

The column heading comes from `matches_played` *inside* the window, not from the
ten that were asked for: a player who has played five sees "Last 5 matches",
which is true, rather than "Last 10", which is not. Measured against a real
database, that is exactly what it says.

The leaderboard takes the same window and becomes a form table. Per player, so
every row covers the same number of that player's own matches and the column
stays a comparison — a window over whichever matches happened most recently would
instead rank whoever turned up to them.

#### Both filters round-trip through the query string

Criterion 2 is a claim about the address, not about the control, so both screens
use `useSearchParams` and the tests either open a filtered address and check the
request that went out, or tap a filter and check the address that came back. A
test that only asserted the control looked right would pass for a `useState`
implementation.

The default is *removed* rather than written, so the plain `/stats` stays clean;
`replace` keeps a filter tap out of the back stack; and an unparseable
`?game_type=x02` renders the unfiltered table rather than sending a request that
would 422. #26's history filter is `useState` and deliberately not linkable —
its criterion only asked for the match-detail deep link — so the two screens
differ on purpose.

#### Two empty states, because they are two different facts

"Nobody has thrown 50 x01 darts yet" is a threshold that has not been met: the
server hides players under `min_darts`, so a new player legitimately has no row,
and the message names the threshold the response actually applied. That is not
the same as a player who has never thrown, who gets a sentence on their own card
instead of a grid of em dashes.

Neither is about guarding a division. The server sends `null` for every average
it has no darts to compute, so "not `NaN`" is satisfied by rendering null as an
em dash — and a zero still renders as `0.00`, because a player who threw and
scored nothing has facts. If a `NaN` ever appears, the render path did arithmetic
it was not supposed to.

#### One small addition to #19's contract

`SegmentResponse` gained a `label`. `engine.throws.Throw` is the one place that
knows the inner bull is 25 doubled and that a miss is not on the board at all,
and `DartResponse` already carries its output; without this the visual would have
had to name segments itself, in a second language, from `segment` and
`multiplier`. `services.play` and #20's `darts.csv` both take the name from
`Throw` rather than spelling it again, and so does this.

#### The visual is a ranked list because the data is

`segment_frequency` is a `GROUP BY` ordered by `count(*) DESC`, so it arrives
already ranked and **sparse** — a segment nobody has hit has no row at all. A
board map would have to invent the missing rows as zeroes and then draw sixty-two
mostly empty cells on a 402px phone. A ranked bar list says the same thing in the
order the query already established, absence means no bar, and it is truncated to
twelve because the tail of a long history is a hundred segments hit once each.

A miss is a row like any other and is kept rather than dropped: "where darts
actually land" includes off the board, and discarding the misses would flatter
every player. It is drawn in a different colour so the visual does not imply it
is part of the board.

#### What the browser caught that jsdom could not

The leaderboard row carried four stats and did not fit. Measured at 402px: the
detail line had 197px and the four-stat version needed 242, so the checkout
percentage rendered as a clipped "100.0…" with its own label cut off entirely —
worse than absent. The row now shows darts thrown and maximums, which fit in
119px and give a ranking position its context; the checkout figures are on the
card, where they have room to be labelled.

Criterion 1 was also measured rather than asserted. Five real 501 matches were
driven through `POST /api/legs/{id}/darts` — 416 darts, dart by dart, exactly as
the play screen records them — and then every figure on both screens was read out
of the DOM in Chrome at 402×781 and compared against the live `/api/stats`
response: 3 leaderboard rows, 16 metrics across two columns, 7 per-target hit
rates, 12 segment bars with their labels, counts and fractions, both filtered
views and the form table. All matched, nothing overflowed the 402px content box,
and no `NaN` appeared. Verified in a real browser, not on the device; it joins
#32.
