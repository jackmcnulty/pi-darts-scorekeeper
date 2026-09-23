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
members visible. Colour and scoreboard short name are deferred to #22.

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
| `GET /api/stats/players/{id}` | `PlayerReportResponse` — lifetime, within the filter |
| `GET /api/stats/leaderboard` | `LeaderboardResponse` — ranked, plus `?min_darts=` |
| `GET /api/stats/matches/{id}` | `MatchReportResponse` — per player and per leg |

The metric definitions and the reason the scope is composed rather than bound
live in [data-model.md](data-model.md#statistics-queries); this is the wiring.

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
