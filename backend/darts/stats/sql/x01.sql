-- x01 visit statistics, grouped by player.
--
-- One query serves every scope: bind :player_id for one player, :match_id for
-- one match and leave both NULL for everybody. A player with no darts in scope
-- produces no row rather than a row of zeroes; the caller supplies the empty
-- answer, because "no darts" and "0 points from 30 darts" are different facts.
--
-- Everything here is computed from the player's OWN darts. A visit belongs to
-- exactly one player, so grouping by visit_id is already per-player, and the
-- same darts thrown in a 2v2 and in a solo match produce the same rows. That is
-- the acceptance criterion the whole shape exists to satisfy.

-- name: x01_totals
WITH scoped AS (
    SELECT
        d.player_id                                  AS player_id,
        d.visit_id                                   AS visit_id,
        -- `score` is the raw board value; multiplying by `counted` is what
        -- makes a busted visit and an uncounted double-in dart score 0 while
        -- still costing their real dart from the denominator.
        d.counted * d.score                          AS scored,
        ROW_NUMBER() OVER (
            PARTITION BY d.leg_id, d.player_id ORDER BY d.seq_in_leg
        )                                            AS own_dart_in_leg
    FROM v_darts d
    WHERE d.game_type = 'x01'
      -- scope: d
),
per_visit AS (
    SELECT
        player_id          AS player_id,
        visit_id           AS visit_id,
        count(*)           AS darts,
        sum(scored)        AS scored
    FROM scoped
    GROUP BY visit_id
),
-- The player's own first nine darts of each leg. In a 2v2 a player throws only
-- alternate visits, so "the first nine darts of the leg" would count a partner's
-- darts and make team play change an individual statistic.
first_nine AS (
    SELECT
        player_id   AS player_id,
        count(*)    AS darts,
        sum(scored) AS scored
    FROM scoped
    WHERE own_dart_in_leg <= 9
    GROUP BY player_id
)
SELECT
    pv.player_id                                    AS player_id,
    sum(pv.darts)                                   AS darts_thrown,
    count(*)                                        AS visits,
    sum(pv.scored)                                  AS points_scored,
    3.0 * sum(pv.scored) / nullif(sum(pv.darts), 0) AS three_dart_average,
    max(pv.scored)                                  AS highest_visit,
    1.0 * sum(pv.scored) / nullif(count(*), 0)      AS average_visit,
    -- Bands are cumulative and count visits, not darts: a 180 is also a 140+.
    sum(pv.scored = 180)                            AS one_eighties,
    sum(pv.scored >= 140)                           AS one_forty_plus,
    sum(pv.scored >= 100)                           AS hundred_plus,
    sum(pv.scored >= 60)                            AS sixty_plus,
    (SELECT f.darts FROM first_nine f
      WHERE f.player_id = pv.player_id)             AS first_nine_darts,
    (SELECT 3.0 * f.scored / nullif(f.darts, 0) FROM first_nine f
      WHERE f.player_id = pv.player_id)             AS first_nine_average
FROM per_visit pv
GROUP BY pv.player_id
ORDER BY pv.player_id;
