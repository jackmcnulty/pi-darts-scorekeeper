-- Cricket statistics, grouped by player.
--
-- A mark is a mark: MPR counts every mark the dart put on a cricket target,
-- `counted_marks + surplus_marks`, whether or not it advanced a close and
-- whether or not the surplus was wasted. That is the conventional marks-per-
-- round other scoring apps report, so the number is comparable; counting only
-- `counted_marks` would make a player's MPR fall as they closed numbers out.
--
-- A dart with no effects row cannot happen for a recorded cricket dart, but the
-- join in v_darts is a LEFT JOIN on a primary key, so coalesce keeps the
-- arithmetic total rather than letting one NULL erase a player's whole sum.

-- name: cricket_totals
WITH scoped AS (
    SELECT
        d.player_id     AS player_id,
        d.cricket_target AS target,
        coalesce(d.cricket_counted_marks, 0)
            + coalesce(d.cricket_surplus_marks, 0) AS marks,
        coalesce(d.cricket_wasted, 0)              AS wasted
    FROM v_darts d
    WHERE d.game_type = 'cricket'
      -- scope: d
)
SELECT
    player_id                                 AS player_id,
    count(*)                                  AS darts_thrown,
    sum(marks)                                AS marks,
    sum(target IS NOT NULL)                   AS darts_on_target,
    sum(wasted)                               AS wasted_darts,
    -- A round is three darts, so MPR is marks per three of the player's own
    -- darts. A part-round at the end of a leg is counted by its real darts.
    3.0 * sum(marks) / nullif(count(*), 0)    AS marks_per_round
FROM scoped
GROUP BY player_id
ORDER BY player_id;

-- Per-target hit rate. The denominator is every cricket dart the player threw
-- in scope, misses included -- "how often does a dart of yours land on the 20"
-- is only meaningful against all the darts, so the grouping keeps the untargeted
-- ones in and the outer query drops them after the window has counted them.

-- name: cricket_targets
WITH scoped AS (
    SELECT
        d.player_id      AS player_id,
        d.cricket_target AS target,
        coalesce(d.cricket_counted_marks, 0)
            + coalesce(d.cricket_surplus_marks, 0) AS marks
    FROM v_darts d
    WHERE d.game_type = 'cricket'
      -- scope: d
),
per_target AS (
    SELECT
        player_id                                  AS player_id,
        target                                     AS target,
        count(*)                                   AS hits,
        sum(marks)                                 AS marks,
        sum(count(*)) OVER (PARTITION BY player_id) AS player_darts
    FROM scoped
    GROUP BY player_id, target
)
SELECT
    player_id                                   AS player_id,
    target                                      AS target,
    hits                                        AS hits,
    marks                                       AS marks,
    100.0 * hits / nullif(player_darts, 0)      AS hit_rate
FROM per_target
WHERE target IS NOT NULL
ORDER BY player_id, target;
