-- Abandonment is distinct from winning, and never erases recorded play.
ALTER TABLE matches ADD COLUMN abandoned_at TEXT
    CHECK (abandoned_at IS NULL OR (completed_at IS NULL AND winner_team_id IS NULL));
CREATE INDEX matches_status_created ON matches(abandoned_at, completed_at, created_at, id);
