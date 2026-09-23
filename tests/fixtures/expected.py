"""#19's golden values, derived independently of the queries they check.

Why this exists at all
----------------------
`expected_stats.json` is worthless if it is produced by running the code it is
meant to check: the test then asserts that the implementation equals itself and
passes however wrong both are. So nothing in this module runs a packaged
statistics query, imports `darts.stats`, or aggregates in SQL. It reads the
seeded rows with `SELECT *`, sorts them in Python and counts them in Python --
a second implementation of every metric, written from the definitions rather
than from `sql/`.

That leaves one shared assumption: that the rows themselves are right. They are
the engine's own output -- `seed.py` drives every dart through `darts.engine`
and stores whatever the rules produced -- and #13's tests already hold them to
that. What is being checked here is the step after: that the SQL reads those
rows into the right numbers.

It still cannot catch a metric *defined* wrongly in both places, so
`tests/stats/test_golden_stats.py` additionally pins a sample of these numbers
to arithmetic worked out by hand and written into the assertion.

Reading the base tables here is deliberate. `tests/db/test_view_bypass.py`
scans `backend/darts/stats/sql/` and nothing else; a test that re-derived the
answer through the same views would share one more assumption than it needs to.
"""

import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

#: The seven cricket numbers, in the order the API reports them. Written out
#: rather than imported from the engine so that a change to the engine's tuple
#: shows up here as a failure instead of silently reordering the golden file.
TARGETS: tuple[int, ...] = (20, 19, 18, 17, 16, 15, 25)

#: Where the checked-in golden file lives.
GOLDEN = Path(__file__).with_name("expected_stats.json")


def _mean(total: float, count: int) -> float | None:
    """An average over nothing does not exist; a total over nothing is 0."""
    return None if count == 0 else total / count


def _rows(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return list(conn.execute(f"SELECT * FROM {table}"))


def _visit_score(darts: list[sqlite3.Row]) -> int:
    """What an x01 visit scored: its counted darts, so a bust scores 0."""
    return sum(d["segment"] * d["multiplier"] for d in darts if d["counted"])


def derive(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every golden value, computed from the seeded rows by hand in Python."""
    players = {int(r["id"]): r for r in _rows(conn, "players")}
    matches = {int(r["id"]): r for r in _rows(conn, "matches")}
    legs = {int(r["id"]): r for r in _rows(conn, "legs")}
    visits = {int(r["id"]): r for r in _rows(conn, "visits")}
    teams = {int(r["id"]): r for r in _rows(conn, "teams")}
    effects = {int(r["dart_id"]): r for r in _rows(conn, "cricket_dart_effects")}
    members = _rows(conn, "team_members")
    darts = sorted(_rows(conn, "darts"), key=lambda d: (int(d["leg_id"]), int(d["seq_in_leg"])))

    # Which darts belong to which player, and which visit each one is in.
    by_player: dict[int, list[sqlite3.Row]] = defaultdict(list)
    by_visit: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for dart in darts:
        by_player[int(dart["player_id"])].append(dart)
        by_visit[int(dart["visit_id"])].append(dart)

    def game_type(dart: sqlite3.Row) -> str:
        return str(matches[int(legs[int(dart["leg_id"])]["match_id"])]["game_type"])

    report: dict[str, Any] = {}
    for player_id, player in sorted(players.items()):
        own = by_player.get(player_id, [])
        x01_darts = [d for d in own if game_type(d) == "x01"]
        cricket_darts = [d for d in own if game_type(d) == "cricket"]

        # --- x01, over this player's own visits -----------------------------
        own_visits = sorted({int(d["visit_id"]) for d in x01_darts})
        scores = [_visit_score(by_visit[v]) for v in own_visits]

        # The player's OWN first nine darts of each leg, not the leg's first
        # nine: in a 2v2 they throw alternate visits, and counting the leg's
        # would let a partner's darts into an individual statistic.
        first_nine: list[sqlite3.Row] = []
        per_leg: dict[int, list[sqlite3.Row]] = defaultdict(list)
        for dart in x01_darts:
            per_leg[int(dart["leg_id"])].append(dart)
        for leg_darts in per_leg.values():
            ordered = sorted(leg_darts, key=lambda d: int(d["seq_in_leg"]))
            first_nine.extend(ordered[:9])
        first_nine_points = sum(d["segment"] * d["multiplier"] for d in first_nine if d["counted"])

        # A checkout is a visit that reached 0 in a leg its thrower's team won,
        # and the dart that did it is that visit's last. One per visit, so
        # counting the visits counts the darts.
        checkouts = [
            visits[v]
            for v in own_visits
            if visits[v]["score_after"] == 0
            and legs[int(visits[v]["leg_id"])]["winner_team_id"] == visits[v]["team_id"]
        ]
        attempts = sum(1 for d in x01_darts if d["was_checkout_attempt"])

        # --- cricket --------------------------------------------------------
        def marks(dart: sqlite3.Row) -> int:
            effect = effects.get(int(dart["id"]))
            return 0 if effect is None else int(effect["counted_marks"] + effect["surplus_marks"])

        def target_of(dart: sqlite3.Row) -> int | None:
            effect = effects.get(int(dart["id"]))
            return None if effect is None or effect["target"] is None else int(effect["target"])

        def was_wasted(dart: sqlite3.Row) -> bool:
            effect = effects.get(int(dart["id"]))
            return effect is not None and bool(effect["wasted"])

        total_marks = sum(marks(d) for d in cricket_darts)
        on_target = [d for d in cricket_darts if target_of(d) is not None]
        per_target = []
        for target in TARGETS:
            hits = [d for d in cricket_darts if target_of(d) == target]
            per_target.append(
                {
                    "target": target,
                    "hits": len(hits),
                    "marks": sum(marks(d) for d in hits),
                    "hit_rate": _mean(100.0 * len(hits), len(cricket_darts)) if hits else None,
                }
            )

        # --- team outcomes, from membership rather than from darts ----------
        own_teams = {int(m["team_id"]) for m in members if int(m["player_id"]) == player_id}
        own_matches = {int(teams[t]["match_id"]) for t in own_teams}
        played_legs = [leg for leg in legs.values() if int(leg["match_id"]) in own_matches]

        report[str(player_id)] = {
            "display_name": str(player["display_name"]),
            "darts_thrown": len(own),
            "legs_played": len(played_legs),
            "legs_won": sum(1 for leg in played_legs if leg["winner_team_id"] in own_teams),
            "matches_played": len(own_matches),
            "matches_won": sum(1 for m in own_matches if matches[m]["winner_team_id"] in own_teams),
            "x01": {
                "darts_thrown": len(x01_darts),
                "visits": len(own_visits),
                "points_scored": sum(scores),
                "three_dart_average": _mean(3.0 * sum(scores), len(x01_darts)),
                "first_nine_average": _mean(3.0 * first_nine_points, len(first_nine)),
                "first_nine_darts": len(first_nine),
                "highest_visit": max(scores) if scores else None,
                "average_visit": _mean(float(sum(scores)), len(own_visits)),
                "bands": {
                    "one_eighties": sum(1 for s in scores if s == 180),
                    "one_forty_plus": sum(1 for s in scores if s >= 140),
                    "hundred_plus": sum(1 for s in scores if s >= 100),
                    "sixty_plus": sum(1 for s in scores if s >= 60),
                },
                "checkout_attempts": attempts,
                "checkouts_hit": len(checkouts),
                "checkout_percentage": _mean(100.0 * len(checkouts), attempts),
                "best_checkout": max((int(v["score_before"]) for v in checkouts), default=None),
            },
            "cricket": {
                "darts_thrown": len(cricket_darts),
                "marks": total_marks,
                "darts_on_target": len(on_target),
                "wasted_darts": sum(1 for d in cricket_darts if was_wasted(d)),
                "marks_per_round": _mean(3.0 * total_marks, len(cricket_darts)),
                "targets": per_target,
            },
            "segments": sorted(
                (
                    {"segment": segment, "multiplier": multiplier, "darts": count}
                    for (segment, multiplier), count in _segments(own).items()
                ),
                key=lambda s: (-s["darts"], -s["segment"], -s["multiplier"]),
            ),
        }
    return report


def _segments(darts: list[sqlite3.Row]) -> dict[tuple[int, int], int]:
    counted: dict[tuple[int, int], int] = defaultdict(int)
    for dart in darts:
        counted[(int(dart["segment"]), int(dart["multiplier"]))] += 1
    return counted


def dump(report: dict[str, Any]) -> str:
    """The golden file's canonical text: sorted keys, two-space indent."""
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def load() -> dict[str, Any]:
    """The checked-in golden values."""
    return dict(json.loads(GOLDEN.read_text(encoding="utf-8")))


def main() -> None:
    """Rewrite the golden file from the seed. Run when the seed changes."""
    import tempfile

    from seed import build

    with tempfile.TemporaryDirectory() as directory:
        conn = build(Path(directory) / "seed.db")
        try:
            GOLDEN.write_text(dump(derive(conn)), encoding="utf-8")
        finally:
            conn.close()


if __name__ == "__main__":  # pragma: no cover - a maintenance entry point
    main()
