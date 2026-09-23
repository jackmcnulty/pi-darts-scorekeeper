-- Facts about raw darts that hold whatever game was being played.
--
-- Unlike the x01 and cricket families these are not implicitly scoped to one
-- game type: a dart thrown is a dart thrown, and where on the board it landed
-- is the same fact in a 501 leg and a cricket leg. :game_type narrows them when
-- the caller asks; nothing narrows them by default.

-- name: darts_thrown
SELECT
    d.player_id AS player_id,
    count(*)    AS darts_thrown
FROM v_darts d
WHERE TRUE  -- every dart, then whatever the caller narrowed to
  -- scope: d
GROUP BY d.player_id
ORDER BY d.player_id;

-- Segment frequency, free from the schema: segment and multiplier are stored
-- per dart, so this needs no new column and no migration. A miss is segment 0
-- with multiplier 0 and is a row like any other.

-- name: segment_frequency
SELECT
    d.player_id  AS player_id,
    d.segment    AS segment,
    d.multiplier AS multiplier,
    count(*)     AS darts
FROM v_darts d
WHERE TRUE  -- every dart, then whatever the caller narrowed to
  -- scope: d
GROUP BY d.player_id, d.segment, d.multiplier
ORDER BY d.player_id, count(*) DESC, d.segment DESC, d.multiplier DESC;
