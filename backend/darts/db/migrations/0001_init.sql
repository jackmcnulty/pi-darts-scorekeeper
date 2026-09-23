-- Immutable once applied. Extend with a new additive migration.
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    name TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TABLE players (
    id INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL CHECK (length(trim(display_name)) > 0),
    is_archived INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TABLE matches (
    id INTEGER PRIMARY KEY,
    config_json TEXT NOT NULL CHECK (
        CASE WHEN json_valid(config_json) THEN json_type(config_json) = 'object' ELSE 0 END
    ),
    game_type TEXT NOT NULL CHECK (game_type IN ('x01', 'cricket')),
    variant TEXT,
    start_score INTEGER,
    in_rule TEXT,
    out_rule TEXT,
    best_of INTEGER NOT NULL CHECK (best_of > 0 AND best_of % 2 = 1),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    completed_at TEXT,
    winner_team_id INTEGER,
    CHECK (
        (game_type = 'x01' AND variant IS NULL AND start_score IS NOT NULL AND start_score > 0
            AND in_rule IS NOT NULL AND in_rule IN ('straight', 'double', 'master')
            AND out_rule IS NOT NULL AND out_rule IN ('straight', 'double', 'master'))
        OR (game_type = 'cricket' AND variant IS NOT NULL
            AND variant IN ('standard', 'cutthroat', 'quick')
            AND start_score IS NULL AND in_rule IS NULL AND out_rule IS NULL)
    ),
    FOREIGN KEY (winner_team_id, id) REFERENCES teams(id, match_id)
) STRICT;

CREATE TABLE teams (
    id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    team_index INTEGER NOT NULL CHECK (team_index >= 0),
    name TEXT,
    is_solo INTEGER NOT NULL CHECK (is_solo IN (0, 1)),
    UNIQUE (match_id, team_index),
    UNIQUE (id, match_id)
) STRICT;

CREATE TABLE team_members (
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    player_id INTEGER NOT NULL REFERENCES players(id),
    member_index INTEGER NOT NULL CHECK (member_index >= 0),
    PRIMARY KEY (team_id, player_id),
    UNIQUE (team_id, member_index)
) STRICT;

CREATE TABLE legs (
    id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    leg_index INTEGER NOT NULL CHECK (leg_index >= 0),
    starting_team_id INTEGER NOT NULL,
    winner_team_id INTEGER,
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    completed_at TEXT,
    UNIQUE (match_id, leg_index),
    UNIQUE (id, match_id),
    FOREIGN KEY (starting_team_id, match_id) REFERENCES teams(id, match_id),
    FOREIGN KEY (winner_team_id, match_id) REFERENCES teams(id, match_id)
) STRICT;

CREATE TABLE visits (
    id INTEGER PRIMARY KEY,
    leg_id INTEGER NOT NULL,
    match_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    visit_index INTEGER NOT NULL CHECK (visit_index >= 0),
    team_visit_index INTEGER NOT NULL CHECK (team_visit_index >= 0),
    score_before INTEGER NOT NULL CHECK (score_before >= 0),
    score_after INTEGER NOT NULL CHECK (score_after >= 0),
    is_bust INTEGER NOT NULL DEFAULT 0 CHECK (is_bust IN (0, 1)),
    is_complete INTEGER NOT NULL DEFAULT 0 CHECK (is_complete IN (0, 1)),
    CHECK (is_bust = 0 OR (score_after = score_before AND is_complete = 1)),
    UNIQUE (leg_id, visit_index),
    UNIQUE (leg_id, team_id, team_visit_index),
    UNIQUE (id, leg_id, team_id, player_id),
    FOREIGN KEY (leg_id, match_id) REFERENCES legs(id, match_id) ON DELETE CASCADE,
    FOREIGN KEY (team_id, match_id) REFERENCES teams(id, match_id),
    FOREIGN KEY (team_id, player_id) REFERENCES team_members(team_id, player_id)
) STRICT;

CREATE TABLE darts (
    id INTEGER PRIMARY KEY,
    visit_id INTEGER NOT NULL,
    leg_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    seq_in_leg INTEGER NOT NULL CHECK (seq_in_leg >= 0),
    dart_index INTEGER NOT NULL CHECK (dart_index BETWEEN 0 AND 2),
    segment INTEGER NOT NULL CHECK (segment BETWEEN 0 AND 20 OR segment = 25),
    multiplier INTEGER NOT NULL CHECK (multiplier BETWEEN 0 AND 3),
    counted INTEGER NOT NULL CHECK (counted IN (0, 1)),
    caused_bust INTEGER NOT NULL DEFAULT 0 CHECK (caused_bust IN (0, 1)),
    was_checkout_attempt INTEGER NOT NULL DEFAULT 0 CHECK (was_checkout_attempt IN (0, 1)),
    client_dart_id TEXT NOT NULL UNIQUE CHECK (length(trim(client_dart_id)) > 0),
    thrown_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK ((segment = 0 AND multiplier = 0) OR (segment != 0 AND multiplier != 0)),
    CHECK (segment != 25 OR multiplier != 3),
    CHECK (caused_bust = 0 OR counted = 0),
    UNIQUE (leg_id, seq_in_leg),
    UNIQUE (visit_id, dart_index),
    UNIQUE (id, leg_id),
    FOREIGN KEY (visit_id, leg_id, team_id, player_id)
        REFERENCES visits(id, leg_id, team_id, player_id) ON DELETE CASCADE
) STRICT;

CREATE TABLE cricket_dart_effects (
    dart_id INTEGER PRIMARY KEY REFERENCES darts(id) ON DELETE CASCADE,
    target INTEGER CHECK (target IN (20, 19, 18, 17, 16, 15, 25)),
    counted_marks INTEGER NOT NULL CHECK (counted_marks BETWEEN 0 AND 3),
    surplus_marks INTEGER NOT NULL CHECK (surplus_marks BETWEEN 0 AND 3),
    wasted INTEGER NOT NULL CHECK (wasted IN (0, 1)),
    CHECK (counted_marks + surplus_marks <= 3),
    CHECK (target != 25 OR counted_marks + surplus_marks <= 2),
    CHECK (target IS NOT NULL OR (counted_marks = 0 AND surplus_marks = 0 AND wasted = 0)),
    CHECK (wasted = 0 OR surplus_marks > 0)
) STRICT;

CREATE TABLE cricket_point_events (
    dart_id INTEGER NOT NULL,
    leg_id INTEGER NOT NULL,
    match_id INTEGER NOT NULL,
    recipient_team_id INTEGER NOT NULL,
    points INTEGER NOT NULL CHECK (points > 0),
    PRIMARY KEY (dart_id, recipient_team_id),
    FOREIGN KEY (dart_id, leg_id) REFERENCES darts(id, leg_id) ON DELETE CASCADE,
    FOREIGN KEY (leg_id, match_id) REFERENCES legs(id, match_id) ON DELETE CASCADE,
    FOREIGN KEY (recipient_team_id, match_id) REFERENCES teams(id, match_id)
) STRICT;

-- Disposable replay caches. Source rows never reference these tables.
CREATE TABLE leg_team_state (
    leg_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    match_id INTEGER NOT NULL,
    remaining INTEGER CHECK (remaining >= 0),
    is_open INTEGER CHECK (is_open IN (0, 1)),
    darts_thrown INTEGER NOT NULL CHECK (darts_thrown >= 0),
    points INTEGER NOT NULL DEFAULT 0 CHECK (points >= 0),
    PRIMARY KEY (leg_id, team_id),
    CHECK ((remaining IS NULL) = (is_open IS NULL)),
    FOREIGN KEY (leg_id, match_id) REFERENCES legs(id, match_id) ON DELETE CASCADE,
    FOREIGN KEY (team_id, match_id) REFERENCES teams(id, match_id) ON DELETE CASCADE
) STRICT;

CREATE TABLE cricket_leg_state (
    leg_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    target INTEGER NOT NULL CHECK (target IN (20, 19, 18, 17, 16, 15, 25)),
    marks INTEGER NOT NULL CHECK (marks BETWEEN 0 AND 3),
    PRIMARY KEY (leg_id, team_id, target),
    FOREIGN KEY (leg_id, team_id) REFERENCES leg_team_state(leg_id, team_id) ON DELETE CASCADE
) STRICT;

CREATE INDEX matches_game_created ON matches(game_type, variant, created_at);
CREATE INDEX matches_winner ON matches(winner_team_id);
CREATE INDEX team_members_player ON team_members(player_id, team_id);
CREATE INDEX legs_winner ON legs(winner_team_id);
CREATE INDEX visits_player_leg ON visits(player_id, leg_id);
CREATE INDEX visits_team_player ON visits(team_id, player_id);
CREATE INDEX darts_player_time ON darts(player_id, thrown_at, leg_id);
CREATE INDEX darts_team_player ON darts(team_id, player_id);
CREATE INDEX darts_counted_player ON darts(player_id, leg_id, segment, multiplier) WHERE counted = 1;
CREATE INDEX darts_checkout_player ON darts(player_id, leg_id) WHERE was_checkout_attempt = 1;
CREATE INDEX cricket_events_recipient ON cricket_point_events(recipient_team_id, leg_id);
CREATE INDEX leg_state_team ON leg_team_state(team_id, match_id);
