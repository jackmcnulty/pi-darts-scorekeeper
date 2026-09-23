-- Checkout statistics, grouped by player.
--
-- Denominator: #7's `was_checkout_attempt`, a stored column with a partial
-- index on it. A dart is an attempt iff the score immediately before it was a
-- genuine one-dart finish -- aim is not observable, so this is the objective
-- measure, and the engine decided it at write time. Nothing is re-derived here.
--
-- Numerator: the dart that actually won the leg. A visit that reaches 0 has
-- checked out, that visit's last dart is the one that did it, and the leg is
-- over. `visit_score_after = 0` cannot happen any other way: a bust restores
-- score_before, and a leg never starts at 0.
--
-- Best checkout is the highest one of those. It needs no separate definition:
-- the winning visit's score_after is 0, so the visit total and the remaining it
-- cleared are the same number, and `visit_score_before` is both.

-- name: checkout_totals
WITH scoped AS (
    SELECT
        d.player_id            AS player_id,
        d.was_checkout_attempt AS was_checkout_attempt,
        d.visit_score_before   AS visit_score_before,
        d.visit_score_after    AS visit_score_after,
        d.leg_winner_team_id   AS leg_winner_team_id,
        d.team_id              AS team_id,
        ROW_NUMBER() OVER (
            PARTITION BY d.visit_id ORDER BY d.dart_index DESC
        )                      AS from_end_of_visit
    FROM v_darts d
    WHERE d.game_type = 'x01'
      -- scope: d
),
flagged AS (
    SELECT
        player_id            AS player_id,
        was_checkout_attempt AS was_checkout_attempt,
        visit_score_before   AS visit_score_before,
        CASE WHEN from_end_of_visit = 1
              AND visit_score_after = 0
              AND leg_winner_team_id = team_id
             THEN 1 ELSE 0 END AS won_the_leg
    FROM scoped
)
SELECT
    player_id                                     AS player_id,
    sum(was_checkout_attempt)                     AS checkout_attempts,
    sum(won_the_leg)                              AS checkouts_hit,
    max(CASE WHEN won_the_leg = 1 THEN visit_score_before END)
                                                  AS best_checkout,
    100.0 * sum(won_the_leg) / nullif(sum(was_checkout_attempt), 0)
                                                  AS checkout_percentage
FROM flagged
GROUP BY player_id
ORDER BY player_id;
