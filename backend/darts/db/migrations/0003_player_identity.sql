-- Who a player is on the scoreboard: a colour, and a name short enough to fit.
-- The colour is stored as an index into #4's eight-accent palette rather than as
-- a hex string, because those eight colours were found by a colour-blindness
-- search that #4 forbids hand-editing, and a copy of them here would be free to
-- drift from the one in tokens.css.
-- Both columns are nullable: ADD COLUMN cannot invent a per-row value, so every
-- player who predates this migration simply has neither.
ALTER TABLE players ADD COLUMN short_name TEXT
    CHECK (short_name IS NULL OR (
        trim(short_name) = short_name AND length(short_name) BETWEEN 1 AND 8
    ));
ALTER TABLE players ADD COLUMN accent_index INTEGER
    CHECK (accent_index IS NULL OR accent_index BETWEEN 1 AND 8);
