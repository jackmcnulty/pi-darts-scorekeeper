"""The palette is defined once, in CSS, and the database only counts it.

`players.accent_index` is validated against `ACCENT_COUNT` in three places -- a
CHECK in `0003_player_identity.sql`, a bound on the API field, and the range
`next_accent_index` chooses from -- and none of them can see the colours. If #4's
palette ever grew a ninth accent, every one of those would quietly keep refusing
it. This is the test that notices.
"""

import re
from pathlib import Path

from darts.repo.players import ACCENT_COUNT

ROOT = Path(__file__).resolve().parents[2]
TOKENS = ROOT / "frontend/src/styles/tokens.css"
MIGRATION = ROOT / "backend/darts/db/migrations/0003_player_identity.sql"


def test_accent_count_matches_the_css_palette() -> None:
    declared = sorted(int(n) for n in re.findall(r"^\s*--accent-(\d+):", TOKENS.read_text(), re.M))
    assert declared == list(range(1, ACCENT_COUNT + 1))


def test_the_migrations_check_covers_exactly_that_range() -> None:
    sql = MIGRATION.read_text()
    assert f"accent_index BETWEEN 1 AND {ACCENT_COUNT}" in sql
