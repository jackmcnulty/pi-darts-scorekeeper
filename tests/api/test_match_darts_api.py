"""`GET /api/matches/{id}/darts`: the read `/state` structurally cannot answer.

The claim under test is not that the route returns darts -- it is that it returns
*every* dart, including the ones `/state` has moved past and the ones a bust
voided. #26 renders a busted visit struck through from `is_bust`, `counted` and
`caused_bust`, so each of those three is asserted on a real bust rather than on
a fixture that merely sets the flag.
"""

from fastapi.testclient import TestClient
from playfixtures import (
    BLANK_VISIT,
    CRICKET_CLOSE_OUT,
    X01_501,
    alternating,
    cricket,
    new_match,
    throw_all,
    throw_match,
)

#: 180, 180, then a visit that cannot fit: 141 - 60 - 60 leaves 21, and the
#: third triple twenty would take it to -39. So visit three busts on its last
#: dart, which is the shape the dart-by-dart view has to render.
BUSTING_SCRIPT = ["T20"] * 9


def _leg_of(client: TestClient, match_id: int, leg_index: int = 0) -> dict:
    response = client.get(f"/api/matches/{match_id}/darts")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["match_id"] == match_id
    return next(leg for leg in body["legs"] if leg["leg_index"] == leg_index)


def test_busted_visit_reports_the_bust_and_voids_its_darts(client: TestClient) -> None:
    """The whole of criterion 3's data: a bust, and three darts that scored nothing."""
    match = new_match(client, X01_501)
    throw_all(client, match["current_leg_id"], alternating(BUSTING_SCRIPT))

    leg = _leg_of(client, match["id"])
    busted = [visit for visit in leg["visits"] if visit["is_bust"]]
    assert len(busted) == 1, "exactly one visit of this script busts"

    visit = busted[0]
    # Nothing scored: the bust put the score back where the visit found it.
    assert visit["score_before"] == 141
    assert visit["score_after"] == 141
    # ...but all three darts are present, because they were thrown.
    assert len(visit["darts"]) == 3
    assert [dart["counted"] for dart in visit["darts"]] == [False, False, False]
    assert [dart["caused_bust"] for dart in visit["darts"]] == [False, False, True]
    assert [dart["label"] for dart in visit["darts"]] == ["T20", "T20", "T20"]


def test_darts_thrown_counts_the_voided_visit(client: TestClient) -> None:
    """The point the view has to make: voided darts still count as thrown.

    Asserted against `/state`'s own `darts_thrown` so that the two reads agree --
    a view that dropped busted darts would show fewer than the board does.
    """
    match = new_match(client, X01_501)
    throw_all(client, match["current_leg_id"], alternating(BUSTING_SCRIPT))

    leg = _leg_of(client, match["id"])
    counted_here = sum(len(visit["darts"]) for visit in leg["visits"])
    state = client.get(f"/api/matches/{match['id']}/state")
    assert counted_here == state.json()["current_leg"]["darts_thrown"]


def test_returns_every_visit_not_just_the_last_two(client: TestClient) -> None:
    """Why the route exists. `/state` carries two visits; this carries all of them."""
    match = new_match(client, X01_501)
    throw_all(client, match["current_leg_id"], alternating(BUSTING_SCRIPT))

    leg = _leg_of(client, match["id"])
    # Three visits from the team under test, interleaved with two blank ones.
    assert [visit["visit_index"] for visit in leg["visits"]] == [0, 1, 2, 3, 4]
    assert len(leg["visits"]) > 2


def test_reports_a_visit_still_being_thrown(client: TestClient) -> None:
    """A part-thrown visit reads, with however many darts have landed."""
    match = new_match(client, X01_501)
    throw_all(client, match["current_leg_id"], ["T20", "T20"])

    leg = _leg_of(client, match["id"])
    assert len(leg["visits"]) == 1
    assert leg["visits"][0]["is_complete"] is False
    assert len(leg["visits"][0]["darts"]) == 2
    assert leg["is_complete"] is False
    assert leg["winner_team_id"] is None


def test_every_leg_of_a_multi_leg_match_is_reported(client: TestClient) -> None:
    """One entry per leg, in playing order, each with its own winner."""
    match = new_match(client, X01_501)
    # A flat script, so it does not care which team starts -- which matters,
    # because `alternate` hands leg 1 to the other team. Both sides score 180,
    # 180, and whoever throws the fifth visit checks out 141 as T20 T19 D12.
    # Run twice: one leg each, so a best-of-3 stands at 1-1 with two legs done.
    leg = ["T20"] * 12 + ["T20", "T19", "D12"]
    throw_match(client, match["current_leg_id"], leg * 2)

    response = client.get(f"/api/matches/{match['id']}/darts")
    body = response.json()
    assert [leg["leg_index"] for leg in body["legs"]] == sorted(
        leg["leg_index"] for leg in body["legs"]
    ), "legs come back in playing order"
    complete = [leg for leg in body["legs"] if leg["is_complete"]]
    assert len(complete) == 2
    for leg in complete:
        assert leg["winner_team_id"] is not None
        assert leg["visits"], "a won leg has visits"


def test_a_cricket_match_reads(client: TestClient) -> None:
    """Cricket has no bust, but the visits and darts are the same projection."""
    match = new_match(client, cricket("standard"))
    throw_all(client, match["current_leg_id"], alternating(CRICKET_CLOSE_OUT))

    leg = _leg_of(client, match["id"])
    assert leg["visits"]
    assert all(visit["is_bust"] is False for visit in leg["visits"])
    assert any(dart["counted"] for visit in leg["visits"] for dart in visit["darts"])


def test_an_abandoned_match_still_reads_its_darts(client: TestClient) -> None:
    """Reading is not acting: #17 refuses darts at an abandoned match, not reads."""
    match = new_match(client, X01_501)
    throw_all(client, match["current_leg_id"], ["T20", "T20", "T20"])
    assert client.post(f"/api/matches/{match['id']}/abandon").status_code == 200

    leg = _leg_of(client, match["id"])
    assert len(leg["visits"]) == 1
    assert len(leg["visits"][0]["darts"]) == 3


def test_a_match_with_no_darts_reports_its_empty_leg(client: TestClient) -> None:
    """A freshly created match has leg 0 and no visits, not a 404."""
    match = new_match(client, X01_501)

    response = client.get(f"/api/matches/{match['id']}/darts")
    assert response.status_code == 200
    assert response.json()["legs"] == [
        {
            "leg_id": match["current_leg_id"],
            "leg_index": 0,
            "starting_team_id": response.json()["legs"][0]["starting_team_id"],
            "winner_team_id": None,
            "is_complete": False,
            "visits": [],
        }
    ]


def test_unknown_match_is_a_404(client: TestClient) -> None:
    assert client.get("/api/matches/9999/darts").status_code == 404


def test_match_id_must_be_positive(client: TestClient) -> None:
    """`MatchId` is `gt=0`, so a zero is a 422 during request parsing."""
    assert client.get("/api/matches/0/darts").status_code == 422


def test_the_dart_shape_matches_the_play_endpoint(client: TestClient) -> None:
    """One vocabulary for a dart across both screens.

    The visit `/state` calls `current_visit` and the visit this route reports are
    the same row, so they must serialise identically -- that is the reason the
    route reuses `VisitResponse` rather than declaring its own.
    """
    match = new_match(client, X01_501)
    throw_all(client, match["current_leg_id"], ["T20", "T20"])

    from_state = client.get(f"/api/matches/{match['id']}/state").json()
    current = from_state["current_leg"]["current_visit"]
    leg = _leg_of(client, match["id"])
    assert leg["visits"][0] == current


def test_a_blank_visit_from_the_other_team_is_present(client: TestClient) -> None:
    """A visit that scored nothing without busting is still a visit."""
    match = new_match(client, X01_501)
    throw_all(client, match["current_leg_id"], ["T20", "T20", "T20", *BLANK_VISIT])

    leg = _leg_of(client, match["id"])
    assert len(leg["visits"]) == 2
    blank = leg["visits"][1]
    assert blank["is_bust"] is False
    assert blank["score_before"] == blank["score_after"] == 501
    assert all(dart["label"] == "MISS" for dart in blank["darts"])
