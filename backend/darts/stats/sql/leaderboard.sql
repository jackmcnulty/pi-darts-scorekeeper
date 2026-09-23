-- The leaderboard: one row per player, ranked by x01 3-dart average.
--
-- :min_darts keeps a player who threw three darts and one 180 off the top of
-- the table; it is the caller's threshold, not a constant, so a household with
-- little history can lower it. Archived players are left out, following #17's
-- pickers: a leaderboard is a thing you are currently on.
--
-- A player who threw no x01 darts in scope has no row here at all. There is no
-- meaningful rank for someone who has not played, and the per-player endpoint
-- is where a zero-dart player gets a well-formed answer.
--
-- This repeats the shape of x01.sql and checkout.sql rather than importing it.
-- The families stay separately readable and separately EXPLAIN-able, which is
-- what the query-plan criterion is asserted against.

-- name: leaderboard
WITH scoped AS (
    SELECT
        d.player_id          AS player_id,
        d.player_name        AS player_name,
        d.visit_id           AS visit_id,
        d.counted * d.score  AS scored,
        d.was_checkout_attempt AS was_checkout_attempt,
        d.visit_score_before AS visit_score_before,
        -- checkout.sql identifies the winning *dart* with a window function,
        -- because that is the definition. Here the grouping is already per
        -- visit, so `max` over the visit says the same thing for a third less
        -- work: a visit that reached 0 was checked out by exactly one of its
        -- darts, so counting such visits counts winning darts. The two agree
        -- exactly, and a test asserts that they do rather than trusting it --
        -- this is the only query in the layer that runs over every player, and
        -- the window function was costing it a third of its time.
        CASE WHEN d.visit_score_after = 0 AND d.leg_winner_team_id = d.team_id
             THEN 1 ELSE 0 END AS checked_out
    FROM v_darts d
    WHERE d.game_type = 'x01'
      AND d.player_is_archived = 0
      -- scope: d
),
per_visit AS (
    SELECT
        player_id            AS player_id,
        player_name          AS player_name,
        count(*)             AS darts,
        sum(scored)          AS scored,
        sum(was_checkout_attempt) AS attempts,
        max(checked_out)     AS checkouts,
        max(CASE WHEN checked_out = 1 THEN visit_score_before END) AS checkout
    FROM scoped
    GROUP BY visit_id, player_id, player_name
)
SELECT
    player_id                                 AS player_id,
    player_name                               AS player_name,
    sum(darts)                                AS darts_thrown,
    3.0 * sum(scored) / nullif(sum(darts), 0) AS three_dart_average,
    max(scored)                               AS highest_visit,
    sum(scored = 180)                         AS one_eighties,
    sum(attempts)                             AS checkout_attempts,
    sum(checkouts)                            AS checkouts_hit,
    max(checkout)                             AS best_checkout,
    100.0 * sum(checkouts) / nullif(sum(attempts), 0) AS checkout_percentage
FROM per_visit
GROUP BY player_id, player_name
HAVING sum(darts) >= :min_darts
ORDER BY three_dart_average DESC, darts_thrown DESC, player_id;
