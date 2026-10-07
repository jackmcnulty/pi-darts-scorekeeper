"""#68: a single-sided match, over HTTP, from setup to the stats it leaves behind.

A single-sided match has one team: practice, with nobody to beat. Jack decided
on #68 that it is never a win -- not a leg or match won, nor one played -- and
that its darts are real darts, counted toward every scoring stat. Those two
halves are tested together here, because the risk is one leaking into the
other: a filter broad enough to drop the solo match from "played" could just
as easily drop its darts from the average.

"Single-sided" is not `is_solo`. `is_solo` is a team with one member, and
`test_one_member_and_two_member_sides` keeps the two apart on purpose.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from playfixtures import (
    CRICKET_CLOSE_OUT,
    X01_301,
    alternating,
    cricket,
    new_match,
    throw_match,
)

#: 301 in six darts, the second visit the checkout.
CHECKOUT_301 = ["T20", "T20", "T20", "T20", "T7", "D20"]


def player_id(match: dict[str, Any], team: int = 0, member: int = 0) -> int:
    return int(match["teams"][team]["members"][member]["player_id"])


def player_stats(client: TestClient, pid: int) -> dict[str, Any]:
    response = client.get(f"/api/stats/players/{pid}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()["player"]
    return body


def tallies(stats: dict[str, Any]) -> tuple[int, int, int, int]:
    return (
        stats["legs_played"],
        stats["legs_won"],
        stats["matches_played"],
        stats["matches_won"],
    )


@pytest.mark.parametrize(
    "config", [X01_301, *(cricket(v) for v in ("standard", "cutthroat", "quick"))]
)
def test_one_team_match_is_created(client: TestClient, config: dict[str, Any]) -> None:
    match = new_match(client, config, teams=[("Ana",)])
    assert match["status"] == "in_progress"
    assert len(match["teams"]) == 1
    assert match["current_leg_id"] is not None


def test_one_member_and_two_member_sides(client: TestClient) -> None:
    """Both are single-sided. Only the first is `is_solo`, which is about members."""
    alone = new_match(client, X01_301, teams=[("Ana",)])
    pair = new_match(client, X01_301, teams=[("Ana", "Ben")])
    assert [t["is_solo"] for t in alone["teams"]] == [True]
    assert [t["is_solo"] for t in pair["teams"]] == [False]


@pytest.mark.parametrize("start_rule", ["alternate", "loser_starts", "winner_starts", "fixed"])
def test_every_start_rule_is_accepted_with_one_team(client: TestClient, start_rule: str) -> None:
    config = {**X01_301, "start_rule": start_rule, "fixed_team": 0}
    match = new_match(client, config, teams=[("Ana",)])
    assert match["config"]["start_rule"] == start_rule


def test_second_team_cannot_start_a_one_team_match(client: TestClient) -> None:
    """The one start choice that names a team that is not there is a clear 422."""
    pid = client.post("/api/players", json={"display_name": "Lone"}).json()["id"]
    config = {**X01_301, "start_rule": "fixed", "fixed_team": 1}
    response = client.post(
        "/api/matches", json={"config": config, "teams": [{"player_ids": [pid]}]}
    )
    assert response.status_code == 422
    detail = response.json()["error"]["detail"]
    assert any(
        e["loc"] == ["body", "teams"] and "fixed_team must name an existing team" in e["msg"]
        for e in detail
    )
    assert client.get("/api/matches").json()["total"] == 0


def test_no_teams_is_still_refused(client: TestClient) -> None:
    response = client.post("/api/matches", json={"config": X01_301, "teams": []})
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("config", "leg"),
    [
        (X01_301, CHECKOUT_301),
        (cricket("standard"), CRICKET_CLOSE_OUT),
        (cricket("cutthroat"), CRICKET_CLOSE_OUT),
        (cricket("quick"), CRICKET_CLOSE_OUT),
    ],
)
def test_solo_match_plays_to_the_end(
    client: TestClient, config: dict[str, Any], leg: list[str]
) -> None:
    """Best of 3 is first to 2: two finished legs, every visit thrown by the one team."""
    match = new_match(client, {**config, "best_of": 3}, teams=[("Ana",)])
    first = throw_match(client, match["current_leg_id"], leg, prefix="one").json()
    assert first["is_complete"] is False
    assert first["legs_won"] == [1]
    final = throw_match(client, first["active_leg_id"], leg, prefix="two").json()
    assert final["is_complete"] is True
    assert final["active_leg_id"] is None
    assert final["legs_won"] == [2]

    stored = client.get(f"/api/matches/{match['id']}").json()
    assert stored["status"] == "complete"
    # The finisher, which in a single-sided match means "finished", not "won".
    assert stored["winner_team_id"] == match["teams"][0]["id"]


def test_solo_cricket_scores_no_points(client: TestClient) -> None:
    """Surplus on every target, which against an open opponent would pay out."""
    match = new_match(client, cricket("standard"), teams=[("Ana",)])
    script = ["T20", "T20", "T19", "T19", "T18", "T18", "T17", "T17"]
    script += ["T16", "T16", "T15", "T15", "BULL", "BULL"]
    body = throw_match(client, match["current_leg_id"], script).json()
    assert body["is_complete"] is True
    leg = body["current_leg"]
    assert [team["points"] for team in leg["teams"]] == [0]


def test_solo_match_is_never_won_or_played_but_its_darts_count(client: TestClient) -> None:
    """The two halves of #68's stats decision, read off every endpoint at once."""
    match = new_match(client, {**X01_301, "best_of": 3}, teams=[("Ana",)])
    throw_match(client, match["current_leg_id"], CHECKOUT_301 * 2)
    pid = player_id(match)

    stats = player_stats(client, pid)
    assert tallies(stats) == (0, 0, 0, 0)
    assert stats["single_sided_matches"] == 1
    # The darts are real darts.
    assert stats["darts_thrown"] == 12
    assert stats["x01"]["three_dart_average"] == pytest.approx(150.5)
    assert stats["x01"]["checkouts_hit"] == 2
    assert stats["x01"]["checkout_percentage"] is not None

    report = client.get(f"/api/stats/matches/{match['id']}").json()["match"]
    assert [tallies(p) for p in report["players"]] == [(0, 0, 0, 0)]
    assert len(report["legs"]) == 2
    assert all(line["won"] is False for line in report["legs"])
    assert all(line["darts_thrown"] == 6 for line in report["legs"])

    board = client.get("/api/stats/leaderboard", params={"min_darts": 0}).json()
    [row] = [r for r in board["rows"] if r["player_id"] == pid]
    assert row["darts_thrown"] == 12
    assert row["checkouts_hit"] == 2
    # The leaderboard carries no win tally at all, so there is nothing else to leak.
    assert not {"legs_won", "matches_won", "legs_played", "matches_played"} & row.keys()


def test_solo_cricket_darts_count_toward_marks_per_round(client: TestClient) -> None:
    match = new_match(client, cricket("quick"), teams=[("Ana",)])
    throw_match(client, match["current_leg_id"], CRICKET_CLOSE_OUT)
    stats = player_stats(client, player_id(match))
    assert tallies(stats) == (0, 0, 0, 0)
    assert stats["cricket"]["darts_thrown"] == 8
    # Six triples, a bull and an outer bull: 21 marks in eight darts.
    assert stats["cricket"]["marks_per_round"] == pytest.approx(3 * 21 / 8)


def test_solo_pair_is_never_won_by_either_member(client: TestClient) -> None:
    match = new_match(client, cricket("quick"), teams=[("Ana", "Ben")])
    throw_match(client, match["current_leg_id"], CRICKET_CLOSE_OUT)
    for member in (0, 1):
        assert tallies(player_stats(client, player_id(match, member=member))) == (0, 0, 0, 0)
    roster = client.get(f"/api/stats/matches/{match['id']}").json()["match"]["players"]
    assert [tallies(p) for p in roster] == [(0, 0, 0, 0)] * 2


def test_contested_matches_still_count_beside_solo_ones(client: TestClient) -> None:
    """A player's 1v1 win survives a solo match, and the solo one adds nothing to it."""
    duel = new_match(client, cricket("quick"), teams=[("Ana",), ("Ben",)])
    throw_match(client, duel["current_leg_id"], alternating(CRICKET_CLOSE_OUT), prefix="duel")
    winner = player_id(duel)
    loser = player_id(duel, team=1)
    before = player_stats(client, winner)
    assert tallies(before) == (1, 1, 1, 1)
    assert tallies(player_stats(client, loser)) == (1, 0, 1, 0)

    # The same person, alone this time.
    pid = client.post("/api/players", json={"display_name": "Practice"}).json()["id"]
    assert pid != winner
    solo = client.post(
        "/api/matches",
        json={"config": cricket("quick"), "teams": [{"player_ids": [winner]}]},
    ).json()
    throw_match(client, solo["current_leg_id"], CRICKET_CLOSE_OUT, prefix="solo")
    after = player_stats(client, winner)
    assert tallies(after) == (1, 1, 1, 1)
    assert after["darts_thrown"] == before["darts_thrown"] + 8
    assert (before["single_sided_matches"], after["single_sided_matches"]) == (0, 1)


def test_recent_window_counts_practice_matches_it_covers(client: TestClient) -> None:
    """`?last_matches=` reaches back over practice too, and says so.

    The card heads its recent column "Last N matches" with
    `matches_played + single_sided_matches`. If practice were in the window but
    in neither count, the heading would say fewer matches than the darts cover.
    """
    duel = new_match(client, cricket("quick"), teams=[("Ana",), ("Ben",)])
    throw_match(client, duel["current_leg_id"], alternating(CRICKET_CLOSE_OUT), prefix="duel")
    pid = player_id(duel)
    for round_ in range(2):
        solo = client.post(
            "/api/matches",
            json={"config": cricket("quick"), "teams": [{"player_ids": [pid]}]},
        ).json()
        throw_match(client, solo["current_leg_id"], CRICKET_CLOSE_OUT, prefix=f"solo{round_}")

    recent = client.get(f"/api/stats/players/{pid}", params={"last_matches": 2}).json()["player"]
    # The two most recent are both practice: no tallies, and their 16 darts.
    assert tallies(recent) == (0, 0, 0, 0)
    assert recent["single_sided_matches"] == 2
    assert recent["darts_thrown"] == 16
    everything = player_stats(client, pid)
    assert everything["matches_played"] + everything["single_sided_matches"] == 3
