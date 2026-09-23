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

### Frontend

## Deployment

## Durability and disaster recovery
