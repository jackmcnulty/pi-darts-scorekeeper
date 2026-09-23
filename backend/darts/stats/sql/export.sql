-- Flat projections for #20's CSV exports, read by `darts.services.export`.
--
-- These are not statistics -- nothing here aggregates a player -- but they live
-- in this directory on purpose. It buys two things that matter more than the
-- name of the folder: `tests/db/test_view_bypass.py` scans it, so an export can
-- never quietly start reading `darts` or `visits` directly; and they get #19's
-- `-- scope:` composition, so the exports accept exactly the same
-- `?game_type=&variant=&since=&match_id=` filter as the stats endpoints from
-- one closed set of predicates rather than a second string-building scheme.
--
-- Both are ordered, and the order is part of the published format: a diff
-- between two exports of the same data should be empty.

-- One row per recorded dart, which is the grain #20 names. Every column the
-- documented `darts.csv` header carries except `label`, which
-- `darts.engine.throws.Throw.label` derives from `segment` and `multiplier` --
-- the one place in the codebase that knows BULL from D20, and the reason the
-- inner bull survives the round trip.
--
-- The cricket columns are NULL for x01 darts and for cricket darts that missed
-- every target, because `v_darts` reaches `cricket_dart_effects` through a LEFT
-- JOIN. The CSV writes them as empty cells rather than inventing a zero.

-- name: export_darts
SELECT
    d.match_id               AS match_id,
    d.match_created_at       AS match_created_at,
    d.game_type              AS game_type,
    d.variant                AS variant,
    d.leg_id                 AS leg_id,
    d.leg_index              AS leg_index,
    d.team_id                AS team_id,
    d.team_index             AS team_index,
    d.team_name              AS team_name,
    d.player_id              AS player_id,
    d.player_name            AS player_name,
    d.visit_id               AS visit_id,
    d.visit_index            AS visit_index,
    d.team_visit_index       AS team_visit_index,
    d.seq_in_leg             AS seq_in_leg,
    d.dart_index             AS dart_index,
    d.dart_id                AS dart_id,
    d.segment                AS segment,
    d.multiplier             AS multiplier,
    d.score                  AS score,
    d.counted                AS counted,
    d.caused_bust            AS caused_bust,
    d.was_checkout_attempt   AS was_checkout_attempt,
    d.thrown_at              AS thrown_at,
    d.visit_score_before     AS visit_score_before,
    d.visit_score_after      AS visit_score_after,
    d.visit_is_bust          AS visit_is_bust,
    d.cricket_target         AS cricket_target,
    d.cricket_counted_marks  AS cricket_counted_marks,
    d.cricket_surplus_marks  AS cricket_surplus_marks,
    d.cricket_wasted         AS cricket_wasted
FROM v_darts d
WHERE TRUE  -- every dart, then whatever the caller narrowed to
  -- scope: d
ORDER BY d.match_id, d.leg_index, d.seq_in_leg;

-- One row per (match, team, player), ordered so that `darts.services.export`
-- can fold each match's consecutive rows into the single `matches.csv` line
-- #20 asks for. The fold happens in Python because the team and player columns
-- are ordered string joins, and `group_concat` did not accept an `ORDER BY`
-- until SQLite 3.44 -- newer than the 3.40 on Raspberry Pi OS Bookworm.
--
-- The two aggregates are computed once per match in their own scoped subqueries
-- rather than as correlated scalar subqueries per row, so narrowing to one match
-- does not cost a scan of every dart in the database.

-- name: export_matches
WITH leg_totals AS (
    SELECT
        lp.match_id AS match_id,
        count(DISTINCT lp.leg_id) AS legs_played,
        count(DISTINCT CASE WHEN lp.leg_completed_at IS NOT NULL THEN lp.leg_id END)
            AS legs_completed
    FROM v_leg_players lp
    WHERE TRUE
      -- scope: lp
    GROUP BY lp.match_id
),
dart_totals AS (
    SELECT
        d.match_id AS match_id,
        count(*)   AS darts_thrown
    FROM v_darts d
    WHERE TRUE
      -- scope: d
    GROUP BY d.match_id
)
SELECT
    m.match_id                       AS match_id,
    m.match_created_at               AS match_created_at,
    m.match_completed_at             AS match_completed_at,
    m.match_abandoned_at             AS match_abandoned_at,
    m.game_type                      AS game_type,
    m.variant                        AS variant,
    m.start_score                    AS start_score,
    m.in_rule                        AS in_rule,
    m.out_rule                       AS out_rule,
    m.best_of                        AS best_of,
    m.match_winner_team_id           AS match_winner_team_id,
    COALESCE(l.legs_played, 0)       AS legs_played,
    COALESCE(l.legs_completed, 0)    AS legs_completed,
    COALESCE(t.darts_thrown, 0)      AS darts_thrown,
    m.team_id                        AS team_id,
    m.team_index                     AS team_index,
    m.team_name                      AS team_name,
    m.is_solo                        AS is_solo,
    m.player_id                      AS player_id,
    m.player_name                    AS player_name,
    m.member_index                   AS member_index,
    m.won                            AS won
FROM v_match_players m
LEFT JOIN leg_totals l ON l.match_id = m.match_id
LEFT JOIN dart_totals t ON t.match_id = m.match_id
WHERE TRUE  -- every match, then whatever the caller narrowed to
  -- scope: m
ORDER BY m.match_id, m.team_index, m.member_index;
