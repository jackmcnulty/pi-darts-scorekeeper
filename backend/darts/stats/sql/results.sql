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
--
-- A single-sided match (#68, see views.sql) needs one: it is never a leg or
-- match won *or played*. The views already zero its `won`; `single_sided = 0`
-- keeps it out of the legs' `count(*)` too. Its darts are untouched and still
-- feed every scoring stat -- Jack's call on #68 that practice darts are real
-- darts.
--
-- Matches are counted rather than filtered, because one consumer needs the
-- practice matches too: `?last_matches=` windows over every match a player was
-- in, practice included (its darts are in the figures), so the card's "Last N
-- matches" heading is `matches_played + single_sided_matches`. Without the
-- second count it would say "Last 6" over ten matches' darts.

-- name: leg_results
SELECT
    l.player_id AS player_id,
    count(*)    AS legs_played,
    sum(l.won)  AS legs_won
FROM v_leg_players l
WHERE l.single_sided = 0  -- every contested participation, then the caller's narrowing
  -- scope: l
GROUP BY l.player_id
ORDER BY l.player_id;

-- name: match_results
SELECT
    m.player_id            AS player_id,
    sum(1 - m.single_sided) AS matches_played,
    sum(m.won)             AS matches_won,
    sum(m.single_sided)    AS single_sided_matches
FROM v_match_players m
WHERE TRUE  -- every participation, then whatever the caller narrowed to
  -- scope: m
GROUP BY m.player_id
ORDER BY m.player_id;
