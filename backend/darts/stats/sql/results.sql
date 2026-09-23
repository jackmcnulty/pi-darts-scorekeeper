-- Legs and matches won.
--
-- These are the two metrics the 2v2-versus-4-solo criterion deliberately does
-- not cover, and cannot: a leg is won by a team, so four solo matches award
-- four separate wins where one 2v2 match awards one win to two players. Every
-- member of the winning team is credited with the win, which is why they are
-- read from the participation views rather than from darts -- a partner who
-- never threw in a leg still won it.
--
-- Abandonment needs no clause here. 0002 makes `abandoned_at` and
-- `winner_team_id` mutually exclusive, so an abandoned match has no winner and
-- `won` is already 0 for everyone in it, exactly as for a match still running.

-- name: leg_results
SELECT
    l.player_id AS player_id,
    count(*)    AS legs_played,
    sum(l.won)  AS legs_won
FROM v_leg_players l
WHERE TRUE  -- every participation, then whatever the caller narrowed to
  -- scope: l
GROUP BY l.player_id
ORDER BY l.player_id;

-- name: match_results
SELECT
    m.player_id AS player_id,
    count(*)    AS matches_played,
    sum(m.won)  AS matches_won
FROM v_match_players m
WHERE TRUE  -- every participation, then whatever the caller narrowed to
  -- scope: m
GROUP BY m.player_id
ORDER BY m.player_id;
