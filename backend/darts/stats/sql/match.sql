-- Shapes that only the per-match endpoint wants: who took part, and the
-- per-leg line the ticket asks for alongside the per-match and lifetime ones.
--
-- Both are bound with :match_id set to the match in the path. The endpoint
-- refuses a `?match_id=` that names a different match during request parsing,
-- so there is no second match parameter to reconcile down here.

-- name: match_roster
SELECT
    m.player_id    AS player_id,
    m.player_name  AS player_name,
    m.team_id      AS team_id,
    m.team_index   AS team_index,
    m.team_name    AS team_name,
    m.is_solo      AS is_solo,
    m.member_index AS member_index,
    m.player_is_archived AS player_is_archived,
    m.won          AS won
FROM v_match_players m
WHERE TRUE  -- every participation, then whatever the caller narrowed to
  -- scope: m
ORDER BY m.team_index, m.member_index;

-- The 3-dart average per leg, and its cricket counterpart. Only one of the two
-- is ever non-NULL for a given leg, because a match is one game type
-- throughout; game_type is in the GROUP BY so the CASE reads a grouped column
-- rather than an arbitrary row's.

-- name: leg_lines
WITH scoped AS (
    SELECT
        d.leg_id            AS leg_id,
        d.leg_index         AS leg_index,
        d.player_id         AS player_id,
        d.game_type         AS game_type,
        d.counted * d.score AS scored,
        coalesce(d.cricket_counted_marks, 0)
            + coalesce(d.cricket_surplus_marks, 0) AS marks,
        CASE WHEN d.leg_winner_team_id IS NOT NULL
              AND d.leg_winner_team_id = d.team_id
             THEN 1 ELSE 0 END AS won
    FROM v_darts d
    WHERE TRUE  -- every dart, then whatever the caller narrowed to
      -- scope: d
)
SELECT
    leg_id                  AS leg_id,
    leg_index               AS leg_index,
    player_id               AS player_id,
    count(*)                AS darts_thrown,
    max(won)                AS won,
    CASE WHEN game_type = 'x01'
         THEN 3.0 * sum(scored) / nullif(count(*), 0) END AS three_dart_average,
    CASE WHEN game_type = 'cricket'
         THEN 3.0 * sum(marks) / nullif(count(*), 0) END  AS marks_per_round
FROM scoped
GROUP BY leg_id, player_id, game_type
ORDER BY leg_index, player_id;
