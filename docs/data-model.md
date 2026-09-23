# Data model

## Principles

Schema version 1 is defined by `backend/darts/db/migrations/0001_init.sql`.
Tables use SQLite `STRICT` types (SQLite 3.37+); the JSON config check also needs
JSON functions. SQLite 3.38+ includes these by default. All boolean columns are
INTEGER with explicit 0/1 checks. IDs are INTEGER primary keys allocated by SQLite;
client request IDs are opaque TEXT. All ordering fields are zero-based.

Every player participates through a team, including a solo player (`is_solo=1`).
Each dart stores its player and team and references the matching visit identity.
Composite foreign keys prevent joining entities from different matches. Historical
players are archived, not deleted. Team membership and member order belong to a
specific match; later matches create new team rows.

Raw darts, visit facts and cricket effects/events are the recorded history.
`leg_team_state` and `cricket_leg_state` are disposable replay caches. No source
table references a cache. Undo hard-deletes a dart and cascades its effects/events;
there is no undo flag or log. Busts preserve their real darts with `counted=0`, and
the visit's score returns to its starting value.

Cross-row game rules (solo team has exactly one member, all darts of a busted
visit are uncounted, config JSON matches promoted fields, valid rotation and
completion) are written atomically by the upcoming repositories/play service
(#14/#15). CHECKs enforce local legality and FKs enforce identity here; triggers
do not duplicate the pure game engine.

## Connections and transactions

`darts.db.connection.connect(path)` applies these settings on **every connection**:

| Setting | Value |
| --- | --- |
| journal_mode | WAL (factory rejects databases such as `:memory:` that cannot use WAL) |
| synchronous | FULL, reported as 2 |
| foreign_keys | ON, reported as 1 |
| busy_timeout | 5000 milliseconds |
| row_factory | `sqlite3.Row` for named columns |

`connection(path)` additionally closes on exit. Connections use autocommit;
`with transaction(conn):` defines the complete write, using `BEGIN IMMEDIATE`,
commit on success and rollback on any error, including a commit failure. Nested
transactions raise rather than committing an outer caller's work. Use
`transaction(conn, immediate=False)` for an explicit deferred/read transaction.
Keep request transactions short; the factory retains SQLite's same-thread check.

WAL with FULL requests synchronization at each commit. Power-loss behavior still
depends on the OS/storage honoring flushes; physical failure testing and DR
tooling are #12. See [SQLite's synchronous documentation](https://www.sqlite.org/pragma.html#pragma_synchronous).

## Migrations and CLI

```sh
uv run darts-migrate /absolute/path/to/darts.db
```

The parent directory must exist. Expected on a fresh file:
`schema version 1; applied 1 migration(s)`; running again prints
`schema version 1; applied 0 migration(s)` and changes no schema or ledger rows.
Failure prints a reason to stderr and returns exit status 1.

The runner reads packaged `db/migrations/NNNN_name.sql` files in contiguous
ascending order, starting at 0001. Each file's DDL, ledger row and `user_version`
change share one transaction. A failed migration rolls back that file completely;
earlier successful files remain committed. History is checked under the writer
lock, so simultaneous startups serialize. A changed, missing, renamed or reordered
applied migration, or version/ledger disagreement, aborts startup.

Files contain semicolon-terminated additive DDL only: `CREATE TABLE`,
`CREATE [UNIQUE] INDEX`, or `ALTER TABLE <name> ADD [COLUMN] ...`. For ALTER,
names may be plain identifiers or double-quoted. Drops, renames, DML, explicit
transaction control and PRAGMAs are rejected. Comments and quoted semicolons work.
Statements are executed individually: Python 3.11's `executescript` implicitly
commits a pending transaction and is unsuitable here. See the
[Python sqlite3 transaction documentation](https://docs.python.org/3.11/library/sqlite3.html#transaction-control).

`schema_migrations` records SHA-256 of exact file bytes, including whitespace.
CI's **schema** job applies the schema to temporary file-backed databases and runs
the DB tests. `tests/db/migration_checksums.txt` is the checked-in golden list.
The checksum test also reads the golden list from the PR base/push predecessor
(`MIGRATION_BASE_REF`); old entries must remain identical even if SQL and the
current golden list are edited together. New migrations append an entry. An old
code version that lacks applied migrations refuses migration; rolling deployment
code back must not rewrite the schema. Views are maintained separately in #13.

## Tables and columns

`?` means nullable. TEXT timestamps default to UTC
`YYYY-MM-DDTHH:MM:SS.sssZ` when created; completion timestamps remain NULL until
completion and are supplied by the future service. Primary IDs are internal keys,
not required to be contiguous. FK actions below describe deletion, not undo logic.

### schema_migrations

| Column | Type | Meaning |
| --- | --- | --- |
| `version` | INTEGER PK | Positive migration number. |
| `name` | TEXT UNIQUE | Exact filename, such as `0001_init.sql`. |
| `sha256` | TEXT | 64 lowercase hexadecimal characters: hash of applied file bytes. |
| `applied_at` | TEXT | UTC application timestamp, default now. |

### players

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | INTEGER PK | Player identity retained across matches. |
| `display_name` | TEXT | Nonblank display name; need not be unique. |
| `is_archived` | INTEGER | 0 by default; 1 hides the player from future pickers. |
| `created_at` | TEXT | UTC creation timestamp, default now. |

Referenced players cannot be deleted; archiving preserves history.

### matches

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | INTEGER PK | Match identity. |
| `config_json` | TEXT | Valid JSON object containing the full config, including starter settings. |
| `game_type` | TEXT | `x01` or `cricket`. |
| `variant` | TEXT? | Cricket: `standard`, `cutthroat`, `quick`; NULL for x01. |
| `start_score` | INTEGER? | Positive x01 starting score; NULL for cricket. |
| `in_rule` | TEXT? | x01: `straight`, `double`, `master`; NULL for cricket. |
| `out_rule` | TEXT? | x01: `straight`, `double`, `master`; NULL for cricket. |
| `best_of` | INTEGER | Positive odd number of legs, winning threshold `(best_of+1)//2`. |
| `created_at` | TEXT | UTC creation timestamp, default now; match-level stats date filter. |
| `completed_at` | TEXT? | UTC completion time. |
| `winner_team_id` | INTEGER? FK | Winning team in this match; NULL until won. |

The promoted config's x01/cricket field combinations are checked. JSON/promoted
agreement is #14's write-time validation. Deleting a match deletes its teams and
legs, cascading through visits, darts, cricket records and caches.

### teams

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | INTEGER PK | Match-specific team identity. |
| `match_id` | INTEGER FK | Owning match; CASCADE. |
| `team_index` | INTEGER | Nonnegative team rotation order, unique within the match. |
| `name` | TEXT? | Optional team display label. |
| `is_solo` | INTEGER | 1 for a team of one, otherwise 0. |

`(id, match_id)` is also unique for composite ownership FKs. Referenced active or
historical teams are not individually deleted; delete their match instead.

### team_members

| Column | Type | Meaning |
| --- | --- | --- |
| `team_id` | INTEGER FK, PK part | Owning team; CASCADE. |
| `player_id` | INTEGER FK, PK part | Player identity; deletion is restricted by references. |
| `member_index` | INTEGER | Nonnegative within-team visit order, unique per team. |

Primary key `(team_id, player_id)` prevents duplicate membership in a team.

### legs

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | INTEGER PK | Leg identity. |
| `match_id` | INTEGER FK | Owning match; CASCADE. |
| `leg_index` | INTEGER | Nonnegative leg order, unique per match. |
| `starting_team_id` | INTEGER FK | Starting team, constrained to this match. |
| `winner_team_id` | INTEGER? FK | Winning team, constrained to this match; NULL while active. |
| `started_at` | TEXT | UTC start timestamp, default now. |
| `completed_at` | TEXT? | UTC completion time. |

`(id, match_id)` is unique for ownership FKs.

### visits

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | INTEGER PK | Visit identity (one member, at most three actual darts). |
| `leg_id` | INTEGER FK | Owning leg; composite with match_id, CASCADE. |
| `match_id` | INTEGER FK part | Ownership assertion for leg/team consistency. |
| `team_id` | INTEGER FK | Throwing team in this match. |
| `player_id` | INTEGER FK part | Throwing player, required to belong to team_id. |
| `visit_index` | INTEGER | Nonnegative global visit order, unique per leg. |
| `team_visit_index` | INTEGER | Nonnegative per-team visit count, unique per leg/team. |
| `score_before` | INTEGER | Nonnegative x01 remaining or cricket thrower points before visit. |
| `score_after` | INTEGER | Same measure after the current dart or completed visit. |
| `is_bust` | INTEGER | Default 0; 1 requires complete visit and equal before/after scores. |
| `is_complete` | INTEGER | Default 0; 1 after three darts, bust or win. |

`(id, leg_id, team_id, player_id)` is a unique dart-attribution key. The service
preserves bust visits and updates all their darts to uncounted in one transaction.

### darts

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | INTEGER PK | Recorded actual dart identity. |
| `visit_id` | INTEGER FK part | Owning visit; composite attribution FK, CASCADE. |
| `leg_id` | INTEGER FK part | Must match the visit's leg. |
| `team_id` | INTEGER FK part | Must match the visit's team. |
| `player_id` | INTEGER FK part | Must match the visit's throwing player, for individual stats. |
| `seq_in_leg` | INTEGER | Nonnegative actual dart order, unique within the leg. |
| `dart_index` | INTEGER | Position 0–2 in the visit, unique within the visit. |
| `segment` | INTEGER | 0 for miss, 1–20 or 25 for bull. |
| `multiplier` | INTEGER | 0–3; 0 exactly for a miss; triple bull prohibited. |
| `counted` | INTEGER | 0/1 scoring inclusion; busted/unopened x01 darts use 0. Cricket detail is in effects. |
| `caused_bust` | INTEGER | Default 0; offending bust dart uses 1 and must be uncounted. |
| `was_checkout_attempt` | INTEGER | Default 0; #7 one-dart finish possible before throwing. |
| `client_dart_id` | TEXT UNIQUE | Nonblank idempotency token, globally unique across matches. |
| `thrown_at` | TEXT | UTC recorded timestamp, default now. |

CHECKs mirror every `Throw` segment/multiplier combination. `(id, leg_id)` is
unique for event ownership. No cached score column is needed: x01 scoring derives
from segment × multiplier and counted. Cricket scoring uses recipient events.

### cricket_dart_effects

| Column | Type | Meaning |
| --- | --- | --- |
| `dart_id` | INTEGER PK, FK | One effect row per cricket dart, including non-targets; CASCADE. |
| `target` | INTEGER? | 20/19/18/17/16/15/25, NULL for non-target or miss. |
| `counted_marks` | INTEGER | 0–3 marks used toward closing. |
| `surplus_marks` | INTEGER | 0–3 marks beyond closure. |
| `wasted` | INTEGER | 1 when surplus pays nobody (dead target or quick); else 0. |

Total marks cannot exceed 3 (2 for bull). NULL target requires zero marks and
not wasted; wasted requires positive surplus. Exact agreement with the source
throw and game rules is computed by replay in #15.

### cricket_point_events

| Column | Type | Meaning |
| --- | --- | --- |
| `dart_id` | INTEGER FK, PK part | Source dart; composite with leg_id, CASCADE. |
| `leg_id` | INTEGER FK | Source leg; composite with match_id, CASCADE. |
| `match_id` | INTEGER FK part | Ownership assertion tying recipient and leg to the same match. |
| `recipient_team_id` | INTEGER FK, PK part | Receiving team, not the thrower unless standard scoring. |
| `points` | INTEGER | Strictly positive points awarded to this recipient in full. |

Primary key `(dart_id, recipient_team_id)` gives one event per recipient. Standard
may emit one self-award, cut-throat may emit many opponent awards, quick none.
Recipients use stable team IDs, not engine-relative opponent indices. Thrower,
player and target are available from the source dart; source attribution is not
lost when points go to opponents. Hard-delete undo removes all recipient events.

### leg_team_state

| Column | Type | Meaning |
| --- | --- | --- |
| `leg_id` | INTEGER FK, PK part | Cached leg; composite with match_id, CASCADE. |
| `team_id` | INTEGER FK, PK part | Cached team; composite with match_id, CASCADE. |
| `match_id` | INTEGER FK part | Ownership assertion, preventing a cache for another match's team. |
| `remaining` | INTEGER? | Nonnegative x01 remaining; NULL for cricket. |
| `is_open` | INTEGER? | x01 opening status; NULL exactly when remaining is NULL. |
| `darts_thrown` | INTEGER | Nonnegative actual dart count, including busted/wasted darts. |
| `points` | INTEGER | Nonnegative cricket points, default 0; unused/zero for x01. |

Primary key `(leg_id, team_id)`. These values are rebuilt from raw history; they
are not player statistics or a second source of truth.

### cricket_leg_state

| Column | Type | Meaning |
| --- | --- | --- |
| `leg_id` | INTEGER FK, PK part | Part of owning leg_team_state key; CASCADE. |
| `team_id` | INTEGER FK, PK part | Part of owning leg_team_state key; CASCADE. |
| `target` | INTEGER PK part | One of the seven cricket targets. |
| `marks` | INTEGER | Closed-mark cache, 0–3; never includes surplus. |

Primary key `(leg_id, team_id, target)` stores seven rows per initialized cricket
team. Points live once in leg_team_state, not repeated across targets.

## Indexes

Unique constraints supply indexes for match team order, member order, leg order,
global/team visit order, `(leg_id, seq_in_leg)`, `(visit_id, dart_index)`, idempotency,
effect/event keys and cache keys. Explicit indexes additionally support:

| Index | Query purpose |
| --- | --- |
| `matches_game_created` | Game/variant/date filtering. |
| `matches_winner`, `legs_winner` | Win counts and winner-reference lookup. |
| `team_members_player` | Player participation across matches. |
| `visits_player_leg` | Per-player visit statistics. |
| `visits_team_player`, `darts_team_player` | Membership FK lookup and team attribution. |
| `darts_player_time` | Player history, date filters, total actual darts (includes busts). |
| `darts_counted_player` | Partial `counted=1`: scoring and segment-frequency aggregates. |
| `darts_checkout_player` | Partial `was_checkout_attempt=1`: checkout denominators. |
| `cricket_events_recipient` | Recipient points per team/leg. |
| `leg_state_team` | Team-cache ownership lookup. |

Constraint tests assert representative counted/checkout queries select the partial
indexes. Full stats query plans and 50,000-dart timing belong to #19.

## Views

Not yet installed; #13 adds the replaceable `v_darts` and `v_visits` query surface.

## Statistics queries

Implemented in #19 over views. Derive per-player statistics from player-attributed
raw darts, not team totals or replay caches. Busts contribute actual dart count
with zero score; undone darts do not exist.

## Verify this foundation

```sh
uv run pytest tests/db -v
```

Tests apply fresh migrations to real temporary files, check repeat no-op behavior,
reject modified migrations, roll back failed DDL and failed commits, exercise
concurrent startup, reject 24 distinct illegal dart INSERTs via SQLite CHECKs,
accept all 63 engine throws, reject duplicate request IDs and cross-match FKs,
and exercise cascades through complete matches and individual darts. An automated
documentation test compares every actual table/column against this document.

The same directory covers crash durability, the boot integrity check, and the
backup/restore CLIs; see [durability](durability.md).
