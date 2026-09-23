-- Replaceable query surface. Dropped and recreated wholesale by
-- darts.db.views.install_views; never applied through the migration runner.
--
-- This file is the complete set of views: anything installed and later removed
-- from here disappears on the next install. Only CREATE VIEW is accepted, and
-- adding one needs no migration and does not move user_version.
--
-- Rule for the stats layer (#19): query these, never the base tables. Row
-- counts are preserved -- every join below is either to a parent row that the
-- schema guarantees exists, or a LEFT JOIN on a primary key.

-- One row per recorded dart, with its visit, leg, team, player and the match
-- configuration denormalised in, so a stats query needs no further joins.
--
-- `score` is the raw board value (segment x multiplier). It is the x01 score
-- only when `counted` is 1; for cricket it is just what the dart was worth on
-- the board, and the real effect is in the cricket_* columns. Cricket point
-- events are one-per-recipient and would multiply rows, so they are not here.
CREATE VIEW v_darts AS
SELECT
    d.id                     AS dart_id,
    d.visit_id               AS visit_id,
    d.leg_id                 AS leg_id,
    v.match_id               AS match_id,
    d.team_id                AS team_id,
    d.player_id              AS player_id,
    d.seq_in_leg             AS seq_in_leg,
    d.dart_index             AS dart_index,
    d.segment                AS segment,
    d.multiplier             AS multiplier,
    d.segment * d.multiplier AS score,
    d.counted                AS counted,
    d.caused_bust            AS caused_bust,
    d.was_checkout_attempt   AS was_checkout_attempt,
    d.client_dart_id         AS client_dart_id,
    d.thrown_at              AS thrown_at,
    v.visit_index            AS visit_index,
    v.team_visit_index       AS team_visit_index,
    v.score_before           AS visit_score_before,
    v.score_after            AS visit_score_after,
    v.is_bust                AS visit_is_bust,
    v.is_complete            AS visit_is_complete,
    l.leg_index              AS leg_index,
    l.started_at             AS leg_started_at,
    l.completed_at           AS leg_completed_at,
    l.winner_team_id         AS leg_winner_team_id,
    m.game_type              AS game_type,
    m.variant                AS variant,
    m.start_score            AS start_score,
    m.in_rule                AS in_rule,
    m.out_rule               AS out_rule,
    m.best_of                AS best_of,
    m.created_at             AS match_created_at,
    m.completed_at           AS match_completed_at,
    m.winner_team_id         AS match_winner_team_id,
    t.team_index             AS team_index,
    t.name                   AS team_name,
    t.is_solo                AS is_solo,
    p.display_name           AS player_name,
    p.is_archived            AS player_is_archived,
    e.target                 AS cricket_target,
    e.counted_marks          AS cricket_counted_marks,
    e.surplus_marks          AS cricket_surplus_marks,
    e.wasted                 AS cricket_wasted
FROM darts d
JOIN visits v ON v.id = d.visit_id
JOIN legs l ON l.id = d.leg_id
JOIN matches m ON m.id = v.match_id
JOIN teams t ON t.id = d.team_id
JOIN players p ON p.id = d.player_id
LEFT JOIN cricket_dart_effects e ON e.dart_id = d.id;

-- One row per visit, including visits that have no darts yet, with the player
-- and team that threw it.
--
-- `total_scored` follows the meaning score_before/score_after already carry per
-- game type: for x01 it is the sum of the visit's counted darts, which is 0 for
-- a busted visit because a bust uncounts every dart in it; for cricket the
-- columns hold the thrower's points, so it is what this visit gained them.
CREATE VIEW v_visits AS
SELECT
    v.id                 AS visit_id,
    v.leg_id             AS leg_id,
    v.match_id           AS match_id,
    v.team_id            AS team_id,
    v.player_id          AS player_id,
    v.visit_index        AS visit_index,
    v.team_visit_index   AS team_visit_index,
    v.score_before       AS score_before,
    v.score_after        AS score_after,
    v.is_bust            AS is_bust,
    v.is_complete        AS is_complete,
    COUNT(d.id)          AS darts_thrown,
    CASE
        WHEN m.game_type = 'x01'
            THEN COALESCE(SUM(d.counted * d.segment * d.multiplier), 0)
        ELSE v.score_after - v.score_before
    END                  AS total_scored,
    l.leg_index          AS leg_index,
    l.winner_team_id     AS leg_winner_team_id,
    m.game_type          AS game_type,
    m.variant            AS variant,
    m.start_score        AS start_score,
    m.in_rule            AS in_rule,
    m.out_rule           AS out_rule,
    m.best_of            AS best_of,
    t.team_index         AS team_index,
    t.name               AS team_name,
    t.is_solo            AS is_solo,
    p.display_name       AS player_name,
    p.is_archived        AS player_is_archived
FROM visits v
JOIN legs l ON l.id = v.leg_id
JOIN matches m ON m.id = v.match_id
JOIN teams t ON t.id = v.team_id
JOIN players p ON p.id = v.player_id
LEFT JOIN darts d ON d.visit_id = v.id
GROUP BY v.id;
