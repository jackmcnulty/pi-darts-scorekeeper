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

### API

### Frontend

## Deployment

## Durability and disaster recovery
