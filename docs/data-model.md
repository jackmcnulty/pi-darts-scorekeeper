# Data model

## Principles

Schema version 1 is defined by `backend/darts/db/migrations/0001_init.sql`.
Version 2 adds `abandoned_at` through `0002_abandoned_matches.sql`; version 1 stays
immutable. Tables use SQLite `STRICT` types (SQLite 3.37+); the JSON config check also needs
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
Keep request transactions short. The factory retains SQLite's same-thread check
by default; API requests opt out because FastAPI can hand a request between
workers. Each API connection is used sequentially and belongs to one request.

WAL with FULL requests synchronization at each commit. Power-loss behavior still
depends on the OS/storage honoring flushes; physical failure testing and DR
tooling are #12. See [SQLite's synchronous documentation](https://www.sqlite.org/pragma.html#pragma_synchronous).

## Migrations and CLI

```sh
uv run darts-migrate /absolute/path/to/darts.db
```

The parent directory must exist. Expected on a fresh file:
`schema version 2; applied 2 migration(s); 2 view(s)`; running again prints
`schema version 2; applied 0 migration(s); 2 view(s)` and changes no schema or
ledger rows. Views are rebuilt on every run whether or not a migration applied.
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
code back must not rewrite the schema. Views are maintained separately; see
[Views](#views).

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
| `abandoned_at` | TEXT? | UTC abandonment time; requires no winner or completion. |
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

`(id, leg_id, team_id, player_id)` is a unique dart-attribution key. A visit is
opened by its first dart and grown by the next two, so unlike `darts` these rows
really are updated: `score_after`, `is_bust` and `is_complete` are rewritten
together after every dart, because the CHECK is a statement about all three at
once. `darts.services.play` preserves bust visits and updates all their darts to
uncounted in the same transaction as the dart that busted.

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
not wasted; wasted requires positive surplus. The values come from
`engine.cricket`'s own dart outcome, written by `darts.services.play` in the
same transaction as the dart, and `darts-verify` recomputes them from the raw
throw.

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
are not player statistics or a second source of truth — `darts.services.play`
writes them and never reads them back. A leg holds rows here **exactly when it
has at least one dart and no winner**: a leg nobody has thrown into has nothing
to resume, and a finished leg is reconstructed from its darts.
`play.rebuild_caches(conn, leg_id)` recomputes them and deletes as readily as it
writes; `darts-verify` reports any disagreement without changing anything.

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
| `matches_status_created` | Match status filtering and stable creation ordering. |
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

`backend/darts/db/views.sql` holds the complete, replaceable query surface, and
`darts.db.views.install_views` drops every installed view and recreates it from
that file in one transaction. Views carry no data, so **adding or changing one
needs no migration**: edit the file. `user_version` does not move and no ledger
row is written. Installing twice leaves exactly one copy of each view, and a
view deleted from the file is retired on the next install.

This is deliberately not the migration runner. That runner rejects `CREATE VIEW`
and checksums every file it applies so an applied migration can never change —
the opposite of what a view wants. The two allowlists stay separate: views.sql
accepts `CREATE VIEW` and nothing else, migrations accept additive DDL and not
views.

Views are installed by `darts-migrate` and by `recovery.check_and_recover`, so
a database that was created, migrated or auto-restored at boot always comes back
with its query surface rather than tables alone. The FastAPI lifespan calls
`check_and_recover` and therefore needs no extra wiring.

| View | Grain | Notes |
| --- | --- | --- |
| `v_darts` | One row per row in `darts` | Visit, leg, team, player and the whole match configuration denormalised in, plus the `cricket_dart_effects` row via a LEFT JOIN on its primary key. `score` is the raw board value (segment × multiplier), which is the x01 score only when `counted` is 1. |
| `v_visits` | One row per row in `visits` | Adds `darts_thrown` and `total_scored`, plus the player and team that threw. A visit with no darts yet still appears, with both at 0. |
| `v_leg_players` | One row per (leg, player eligible to throw in it) | Adds `won`, which is 1 exactly when the player's team won that leg. Read off `team_members`, so a partner who threw no darts is still credited. |
| `v_match_players` | One row per (match, player) | The same, one level up: `won` is 1 exactly when the player's team won the match. |

`total_scored` follows what `score_before`/`score_after` already mean per game
type: for x01 the sum of the visit's counted darts, which is 0 for a busted
visit because a bust uncounts every dart in it while the darts themselves remain
in `v_darts`; for cricket the thrower's points gained. Cricket point events are
one row per recipient and would multiply rows, so they are not in `v_darts`.

The two participation views are wider than any one table by design. A leg is won
by a *team*, and in a 2v2 one partner can finish a leg the other never threw in,
so a win cannot be inferred from `v_darts` — it is a fact about membership. `won`
is 0 whenever `winner_team_id` is NULL, which covers both an unfinished leg and
an abandoned match (0002 makes `abandoned_at` and `winner_team_id` mutually
exclusive), so no query needs a status test to keep abandonment out of a win
count. Cricket point events still have no view: #19's metrics read marks from
`cricket_dart_effects`, which `v_darts` already carries, and points from
`v_visits.total_scored`.

## Statistics queries

Implemented in #19 over the views, in `backend/darts/stats/sql/*.sql` — one file
per family, loaded by `darts.stats.queries`. Nothing is a stored counter, so a
new metric is a new query and nothing else: no migration, no backfill, and full
retroactive history over play recorded before the metric existed.
`tests/db/test_view_bypass.py` scans that directory and fails any query reading
`darts` or `visits` directly instead of through a view.

Per-player statistics come from **player-attributed raw darts**, never team
totals or replay caches. That is what makes the sharpest criterion hold: a 2v2
match and four solo matches containing identical darts produce identical
per-player numbers. A visit belongs to exactly one player, so aggregating per
visit is already per player.

| Decision | What it means |
| --- | --- |
| Busts | Their real dart count, zero points. The darts were thrown. |
| Uncounted opening darts | The same: a double-in dart that missed is a dart thrown scoring 0. |
| Undone darts | Absent entirely; #15 deleted the row, so there is nothing to filter. |
| Unfinished legs and matches | Counted. Abandoned matches too — their darts were thrown, and 0002 makes `abandoned_at` and `winner_team_id` exclusive, so no win count needs a status clause. |
| First-9 average | The player's **own** first nine darts in each leg. In a 2v2 a player throws alternate visits, so counting the *leg's* first nine would let a partner's darts into an individual statistic. |
| x01 vs cricket | x01 metrics are always over x01 darts and cricket metrics over cricket darts, whether or not `?game_type=` was given. A 3-dart average mixing the two would be a number about nothing. |
| Checkout percentage | Numerator: the dart that won the leg (a visit reaching 0 is checked out by exactly one of its darts). Denominator: #7's stored `was_checkout_attempt`. |
| Best checkout | The highest such visit. Its `score_after` is 0, so the visit total and the remaining it cleared are one number. |
| Cricket MPR | `counted_marks + surplus_marks` per three of the player's own darts — every mark that landed on a target, which is the conventional figure other apps report. |
| Per-target hit rate | Darts on that target over *every* cricket dart in scope, misses included. |
| Legs and matches won | Team outcomes, credited to every member of the winning team, and the documented exception to the identical-darts criterion. Read from `v_leg_players` / `v_match_players`, so a partner who threw no darts is still credited. |
| `?since=` | Compared against the match's `created_at`, so a match is never split across the boundary. |
| Nothing to report | A count is 0; an average is null. |

### Why the scope is composed rather than bound

Every query accepts the same scope parameters, but a parameter that is not set
contributes **no predicate at all** — the query text is assembled from a closed
set of fragments at `-- scope: <alias>` markers. The obvious spelling,
`WHERE (:player_id IS NULL OR d.player_id = :player_id)`, cannot use an index:
SQLite prepares a statement without knowing what will be bound to it. Measured
over 50,000 darts it turns `SEARCH d USING INDEX darts_player_time (player_id=?)`
into a full scan of `visits` — right answers, two to three times the work, and a
plan that fails #19's own acceptance criterion. The markers are comments, so each
file is still valid SQL that runs unscoped in a shell.

`v_visits` is deliberately not used by any of these queries. It carries a
`GROUP BY`, so it cannot be flattened into a caller and a `WHERE` may not reach
the rows underneath; aggregating `v_darts` per visit gives the same answers and
keeps the index seek.

## Fixture data

`tests/fixtures/seed.py` builds the deterministic dataset #19's golden values are
computed against: six players, a completed solo x01 match containing a bust and
two checkouts, a 2v2 501 double-in/double-out match with uncounted opening darts
and a leg still in progress, and one leg of each cricket variant. Every dart is a
scripted throw driven through `darts.engine`, so `counted`, `caused_bust`,
`is_bust`, the cricket effects and the point events are whatever the real rules
produced rather than arithmetic written out by hand.

Reproducibility is defined over rows, not file bytes: every column the schema
would otherwise default from the clock is supplied explicitly, all IDs are
derived from the match/leg/visit/dart indices, and `seed.dump` emits a canonical
JSON snapshot of every seeded table that is identical on every run.
`schema_migrations` is excluded, since `applied_at` is wall-clock by design.
Seeded `client_dart_id` values are `seed:m<match>:l<leg>:d<seq>` — deterministic,
globally unique, and obviously fixture data rather than a client token.

## Verify this foundation

```sh
uv run pytest tests/db tests/fixtures -v
```

Tests apply fresh migrations to real temporary files, check repeat no-op behavior,
reject modified migrations, roll back failed DDL and failed commits, exercise
concurrent startup, reject 24 distinct illegal dart INSERTs via SQLite CHECKs,
accept all 63 engine throws, reject duplicate request IDs and cross-match FKs,
and exercise cascades through complete matches and individual darts. An automated
documentation test compares every actual table/column against this document.

Views are covered by installing twice and counting copies, replacing and retiring
definitions, rejecting anything that is not a `CREATE VIEW`, asserting
`user_version` and the ledger do not move, row-count parity against `darts` and
`visits`, and reinstallation through both `darts-migrate` and boot recovery. The
fixture is covered by building it twice and comparing canonical dumps, and by
checking it really does contain both x01 shapes, all three cricket variants, a
bust and a checkout.

The same directory covers crash durability, the boot integrity check, and the
backup/restore CLIs; see [durability](durability.md).
