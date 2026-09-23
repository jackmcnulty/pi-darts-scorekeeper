"""The play API: the fat read, the two writes, and the hints they carry.

Named `test_play_api` rather than `test_play` because pytest imports test
modules by basename and `tests/services/test_play_x01.py` and friends already
live in the suite; two modules called `test_play` would collide on import.
"""

import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient
from playfixtures import (
    CRICKET_CLOSE_OUT,
    X01_301,
    X01_501,
    X01_501_DOUBLE_IN,
    alternating,
    cricket,
    dart,
    new_match,
    no_gets,
    throw_all,
    throw_match,
    throw_one,
)

from darts.config import Settings
from darts.db.connection import connection

#: 501 down to 170 in three complete visits -- 180, 100, then 51 -- with the
#: trailing blank visit that hands the turn back, so the team under test is on
#: 170 with a full three darts.
TO_170 = alternating(["T20", "T20", "T20", "T20", "D20", "MISS", "T17", "MISS", "MISS"]) + [
    "MISS",
    "MISS",
    "MISS",
]

#: 301 won in six darts: 180, then 121 checked out T20, T11, D14.
FINISH_301 = alternating(["T20", "T20", "T20", "T20", "T11", "D14"])


def leg_of(match: dict[str, Any]) -> int:
    leg_id = match["current_leg_id"]
    assert leg_id is not None
    return int(leg_id)


def team_state(leg: dict[str, Any], team_id: int) -> dict[str, Any]:
    """One team's row of a leg, matched by id rather than by position."""
    return next(row for row in leg["teams"] if row["team_id"] == team_id)


def rows(settings: Settings, table: str) -> int:
    with connection(settings.db_path) as conn:
        count: int = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        return count


# --------------------------------------------------------------------------
# The fat read
# --------------------------------------------------------------------------


def test_state_carries_everything_the_play_screen_draws(client: TestClient) -> None:
    match = new_match(client, X01_501)
    body = client.get(f"/api/matches/{match['id']}/state").json()

    assert body["match_id"] == match["id"]
    assert body["status"] == "in_progress"
    assert body["config"]["start_score"] == 501
    assert [team["id"] for team in body["teams"]] == [t["id"] for t in match["teams"]]
    assert body["legs_won"] == [0, 0]
    assert body["winner_team_id"] is None
    assert body["is_complete"] is False

    leg = body["current_leg"]
    assert leg["leg_id"] == leg_of(match)
    assert leg["darts_thrown"] == 0
    assert leg["darts_left"] == 3
    assert leg["next_thrower"]["team_id"] == match["teams"][0]["id"]
    assert [row["remaining"] for row in leg["teams"]] == [501, 501]
    assert leg["current_visit"] is None
    assert leg["previous_visit"] is None
    assert leg["checkout"]["reason"] == "not_checkable"

    # An untouched match is already on its only leg, so there is no second one.
    assert body["active_leg_id"] == leg["leg_id"]
    assert body["active_leg"] is None


def test_state_is_addressed_by_match_and_resolves_the_leg_itself(client: TestClient) -> None:
    """`play.state` takes a leg id; the route takes a match id and must not confuse them.

    A leg is played out in an earlier match first. Each match opens exactly one
    leg, so the two id sequences otherwise advance in lockstep and match 1's leg
    is also 1 -- an endpoint that passed the match id straight through to
    `play.state` would sail through a test built on that. Winning a leg opens a
    second one, which puts the sequences permanently out of step.
    """
    first = new_match(client, X01_301)
    throw_all(client, leg_of(first), FINISH_301)

    match = new_match(client, X01_501)
    assert match["id"] != leg_of(match)

    body = client.get(f"/api/matches/{match['id']}/state").json()

    assert body["current_leg"]["leg_id"] == leg_of(match)
    assert body["match_id"] == match["id"]


def test_an_unknown_match_is_a_404(client: TestClient) -> None:
    body = client.get("/api/matches/9999/state")
    assert body.status_code == 404
    assert body.json()["error"]["code"] == "not_found"


def test_state_survives_a_refresh_mid_visit(client: TestClient) -> None:
    """Resume: a reload returns the same position the last write reported."""
    match = new_match(client, X01_501)
    written = throw_all(client, leg_of(match), ["T20", "T19"]).json()
    reloaded = client.get(f"/api/matches/{match['id']}/state").json()

    assert reloaded == written


# --------------------------------------------------------------------------
# Playing a leg with no GETs
# --------------------------------------------------------------------------


def test_a_whole_x01_leg_is_played_without_a_single_get(client: TestClient) -> None:
    match = new_match(client, X01_301)
    winner = match["teams"][0]["id"]

    with no_gets(client) as gets:
        body = throw_all(client, leg_of(match), FINISH_301).json()

    assert gets == []
    assert body["current_leg"]["is_complete"] is True
    assert body["current_leg"]["winner_team_id"] == winner
    assert body["legs_won"] == [1, 0]
    assert team_state(body["current_leg"], winner)["remaining"] == 0


@pytest.mark.parametrize("variant", ["standard", "cutthroat", "quick"])
def test_a_whole_cricket_leg_is_played_without_a_single_get(
    client: TestClient, variant: str
) -> None:
    match = new_match(client, cricket(variant))
    winner = match["teams"][0]["id"]

    with no_gets(client) as gets:
        body = throw_all(client, leg_of(match), alternating(CRICKET_CLOSE_OUT)).json()

    assert gets == []
    assert body["current_leg"]["is_complete"] is True
    assert body["current_leg"]["winner_team_id"] == winner
    assert body["is_complete"] is True
    assert body["winner_team_id"] == winner

    marks = team_state(body["current_leg"], winner)["marks"]
    assert {target: marks[target] for target in ("15", "16", "17", "18", "19", "20", "25")} == {
        target: 3 for target in ("15", "16", "17", "18", "19", "20", "25")
    }
    # Cricket fills marks and points; the x01 columns stay empty.
    assert team_state(body["current_leg"], winner)["remaining"] is None


def test_every_dart_response_carries_the_hint_for_the_new_position(client: TestClient) -> None:
    """The hint repaints off the write, which is why no GET is needed."""
    match = new_match(client, X01_501)
    leg = leg_of(match)

    throw_all(client, leg, TO_170)
    on_170 = client.get(f"/api/matches/{match['id']}/state").json()["current_leg"]
    assert on_170["checkout"]["paths"][0] == ["T20", "T20", "BULL"]

    after = throw_one(client, leg, "T20", "hint-1").json()["current_leg"]
    assert after["checkout"]["remaining"] == 110
    assert after["checkout"]["darts_left"] == 2
    assert after["checkout"]["paths"] == [["T20", "BULL"]]
    assert after["checkout"]["reason"] is None


# --------------------------------------------------------------------------
# Checkout hints
# --------------------------------------------------------------------------


def test_a_501_double_out_leg_on_170_offers_the_classic_finish(client: TestClient) -> None:
    """#18's named criterion, read out of the fat read and the standalone route alike."""
    match = new_match(client, X01_501)
    leg = leg_of(match)
    throw_all(client, leg, TO_170)

    embedded = client.get(f"/api/matches/{match['id']}/state").json()["current_leg"]["checkout"]
    standalone = client.get(f"/api/legs/{leg}/checkout").json()

    assert embedded == standalone
    assert ["T20", "T20", "BULL"] in standalone["paths"]
    assert standalone["paths"][0] == ["T20", "T20", "BULL"]
    assert standalone["remaining"] == 170
    assert standalone["darts_left"] == 3
    assert standalone["team_id"] == match["teams"][0]["id"]
    assert standalone["reason"] is None


def test_hints_shrink_with_the_darts_left_in_the_visit(client: TestClient) -> None:
    """170 is a three-dart finish and nothing less, so a dart spent empties it."""
    match = new_match(client, X01_501)
    leg = leg_of(match)
    throw_all(client, leg, TO_170)

    assert client.get(f"/api/legs/{leg}/checkout").json()["darts_left"] == 3
    throw_one(client, leg, "MISS", "shrink-1")

    after = client.get(f"/api/legs/{leg}/checkout").json()
    assert after["darts_left"] == 2
    assert after["remaining"] == 170
    assert after["paths"] == []
    assert after["reason"] == "not_checkable"


def test_hints_respect_the_configured_out_rule(client: TestClient) -> None:
    """Straight out finishes 170 three ways; double out allows only the bull."""
    straight = new_match(client, {**X01_501, "out_rule": "straight"})
    throw_all(client, leg_of(straight), TO_170, prefix="s")
    paths = client.get(f"/api/legs/{leg_of(straight)}/checkout").json()["paths"]

    assert paths[0] == ["T20", "T20", "BULL"]
    assert len(paths) > 1

    double = new_match(client, X01_501)
    throw_all(client, leg_of(double), TO_170, prefix="d")
    assert client.get(f"/api/legs/{leg_of(double)}/checkout").json()["paths"] == [
        ["T20", "T20", "BULL"]
    ]


def test_a_team_that_has_not_opened_is_offered_nothing(client: TestClient) -> None:
    """Under double-in the table's paths would not even score; see services.hints."""
    match = new_match(client, X01_501_DOUBLE_IN)
    body = client.get(f"/api/legs/{leg_of(match)}/checkout").json()

    assert body["reason"] == "not_open"
    assert body["paths"] == []
    assert body["remaining"] is None
    # The thrower is still named: it is the hint that is withheld, not the turn.
    assert body["team_id"] == match["teams"][0]["id"]


def test_cricket_has_no_checkout_to_suggest(client: TestClient) -> None:
    match = new_match(client, cricket("standard"))
    body = client.get(f"/api/legs/{leg_of(match)}/checkout").json()

    assert body["reason"] == "not_x01"
    assert body["paths"] == []


def test_a_won_leg_offers_no_hint(client: TestClient) -> None:
    match = new_match(client, X01_301)
    leg = leg_of(match)
    throw_all(client, leg, FINISH_301)

    body = client.get(f"/api/legs/{leg}/checkout").json()
    assert body["reason"] == "leg_complete"
    assert body["paths"] == []


def test_the_standalone_checkout_route_404s_for_an_unknown_leg(client: TestClient) -> None:
    assert client.get("/api/legs/4242/checkout").status_code == 404


# --------------------------------------------------------------------------
# Current and previous visit
# --------------------------------------------------------------------------


def test_the_visit_fields_track_the_boundaries_of_a_visit(client: TestClient) -> None:
    match = new_match(client, X01_501)
    leg = leg_of(match)

    opening = throw_all(client, leg, ["T20"]).json()["current_leg"]
    assert [d["label"] for d in opening["current_visit"]["darts"]] == ["T20"]
    assert opening["previous_visit"] is None

    mid = throw_all(client, leg, ["T20"], prefix="b").json()["current_leg"]
    assert [d["label"] for d in mid["current_visit"]["darts"]] == ["T20", "T20"]
    assert mid["previous_visit"] is None

    # The third dart closes the visit, which moves it across.
    closed = throw_all(client, leg, ["T20"], prefix="c").json()["current_leg"]
    assert closed["current_visit"] is None
    assert [d["label"] for d in closed["previous_visit"]["darts"]] == ["T20", "T20", "T20"]
    assert closed["previous_visit"]["score_before"] == 501
    assert closed["previous_visit"]["score_after"] == 321
    assert closed["previous_visit"]["is_complete"] is True

    # The next team's first dart opens a new current visit, and the recap stays.
    reopened = throw_all(client, leg, ["T19"], prefix="d").json()["current_leg"]
    assert [d["label"] for d in reopened["current_visit"]["darts"]] == ["T19"]
    assert reopened["previous_visit"]["score_after"] == 321


def test_a_bust_lands_in_previous_visit_and_reports_itself(client: TestClient) -> None:
    """A bust ends the visit, so it recaps rather than staying current."""
    match = new_match(client, X01_301)
    leg = leg_of(match)
    # 301 -> 121, then two triple twenties leave 1 and void the whole visit.
    throw_all(client, leg, ["T20", "T20", "T20", "MISS", "MISS", "MISS"])
    body = throw_all(client, leg, ["T20", "T20"], prefix="bust").json()["current_leg"]

    assert body["current_visit"] is None
    bust = body["previous_visit"]
    assert bust["is_bust"] is True
    assert bust["is_complete"] is True
    assert bust["score_before"] == bust["score_after"] == 121
    assert [d["counted"] for d in bust["darts"]] == [False, False]
    assert bust["darts"][-1]["caused_bust"] is True
    # The bust rewound the score, so the team is back where the visit started.
    assert team_state(body, match["teams"][0]["id"])["remaining"] == 121


def test_a_new_leg_does_not_recap_the_leg_before_it(client: TestClient) -> None:
    match = new_match(client, X01_301)
    body = throw_all(client, leg_of(match), FINISH_301).json()

    assert body["active_leg"]["previous_visit"] is None
    assert body["active_leg"]["current_visit"] is None


# --------------------------------------------------------------------------
# Leg and match transitions
# --------------------------------------------------------------------------


def test_a_winning_dart_returns_the_finished_leg_and_the_next_one(client: TestClient) -> None:
    match = new_match(client, X01_301)
    first = leg_of(match)
    winner = match["teams"][0]["id"]

    body = throw_all(client, first, FINISH_301).json()

    current, active = body["current_leg"], body["active_leg"]
    assert current["leg_id"] == first
    assert current["is_complete"] is True
    assert current["winner_team_id"] == winner
    assert current["next_thrower"] is None

    assert body["active_leg_id"] != first
    assert active["leg_id"] == body["active_leg_id"]
    assert active["is_complete"] is False
    assert active["darts_thrown"] == 0
    assert [row["remaining"] for row in active["teams"]] == [301, 301]
    # Alternate start: the team that did not start leg 0 opens leg 1.
    assert active["next_thrower"]["team_id"] == match["teams"][1]["id"]


def test_active_leg_is_absent_while_one_leg_is_simply_in_progress(client: TestClient) -> None:
    match = new_match(client, X01_301)
    body = throw_all(client, leg_of(match), ["T20"]).json()

    assert body["active_leg_id"] == body["current_leg"]["leg_id"]
    assert body["active_leg"] is None


def test_the_deciding_leg_ends_the_match_and_leaves_nowhere_to_throw(
    client: TestClient,
) -> None:
    """A best-of-three played out in full, following the leg each response names.

    `FINISH_301` is written from the point of view of whoever throws first, and
    the alternate start rule hands that to the other team each leg -- so three
    runs of it are won by teams A, B, A, and the decider ends the match 2-1.
    """
    match = new_match(client, X01_301)
    winner = match["teams"][0]["id"]

    body = throw_match(client, leg_of(match), FINISH_301 * 3).json()

    assert body["legs_won"] == [2, 1]
    assert body["is_complete"] is True
    assert body["status"] == "complete"
    assert body["winner_team_id"] == winner
    assert body["active_leg_id"] is None
    assert body["active_leg"] is None
    assert body["current_leg"]["is_complete"] is True
    assert body["current_leg"]["next_thrower"] is None


def test_a_finished_match_still_reads_its_final_leg(client: TestClient) -> None:
    match = new_match(client, X01_301)
    throw_match(client, leg_of(match), FINISH_301 * 3)

    body = client.get(f"/api/matches/{match['id']}/state").json()
    assert body["status"] == "complete"
    assert body["current_leg"]["is_complete"] is True
    assert body["current_leg"]["checkout"]["reason"] == "leg_complete"
    assert body["active_leg_id"] is None


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_throwing_into_a_won_leg_is_a_409(client: TestClient) -> None:
    match = new_match(client, X01_301)
    leg = leg_of(match)
    throw_all(client, leg, FINISH_301)

    refused = throw_one(client, leg, "T20", "after-the-win")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "conflict"
    assert refused.json()["error"]["detail"]["reason"] == "leg_complete"


def test_throwing_into_a_won_match_is_a_409(client: TestClient) -> None:
    match = new_match(client, X01_301)
    body = throw_match(client, leg_of(match), FINISH_301 * 3).json()

    # The final leg is won and there is no leg behind it, so the match refuses.
    refused = throw_one(client, body["current_leg"]["leg_id"], "T20", "after-the-match")
    assert refused.status_code == 409
    assert refused.json()["error"]["detail"]["reason"] == "match_complete"


def test_an_unknown_leg_is_a_404_for_both_writes(client: TestClient) -> None:
    assert throw_one(client, 4242, "T20", "nowhere").status_code == 404
    assert client.post("/api/legs/4242/undo").status_code == 404


# --------------------------------------------------------------------------
# Abandoned matches
# --------------------------------------------------------------------------


def test_an_abandoned_match_reads_but_offers_nothing_to_do(client: TestClient) -> None:
    match = new_match(client, X01_501)
    leg = leg_of(match)
    throw_all(client, leg, ["T20", "T20", "T20"])
    assert client.post(f"/api/matches/{match['id']}/abandon").status_code == 200

    body = client.get(f"/api/matches/{match['id']}/state").json()

    assert body["status"] == "abandoned"
    assert body["is_complete"] is False
    assert body["winner_team_id"] is None
    # Nothing actionable, however unfinished the leg is.
    assert body["active_leg_id"] is None
    assert body["active_leg"] is None
    assert body["current_leg"]["next_thrower"] is None
    assert body["current_leg"]["checkout"]["reason"] == "match_abandoned"
    assert body["current_leg"]["checkout"]["paths"] == []
    # The score it was abandoned at is still readable, which is the point.
    assert team_state(body["current_leg"], match["teams"][0]["id"])["remaining"] == 321
    assert [d["label"] for d in body["current_leg"]["previous_visit"]["darts"]] == [
        "T20",
        "T20",
        "T20",
    ]


def test_an_abandoned_match_refuses_both_writes(client: TestClient) -> None:
    match = new_match(client, X01_501)
    leg = leg_of(match)
    throw_all(client, leg, ["T20"])
    client.post(f"/api/matches/{match['id']}/abandon")

    refused = throw_one(client, leg, "T20", "after-abandon")
    assert refused.status_code == 409
    assert refused.json()["error"]["detail"]["reason"] == "match_abandoned"

    undone = client.post(f"/api/legs/{leg}/undo")
    assert undone.status_code == 409
    assert undone.json()["error"]["detail"]["reason"] == "match_abandoned"


def test_the_standalone_checkout_route_withholds_hints_when_abandoned(
    client: TestClient,
) -> None:
    match = new_match(client, X01_501)
    leg = leg_of(match)
    throw_all(client, leg, TO_170)
    assert client.get(f"/api/legs/{leg}/checkout").json()["paths"] != []

    client.post(f"/api/matches/{match['id']}/abandon")
    assert client.get(f"/api/legs/{leg}/checkout").json() == {
        "team_id": None,
        "player_id": None,
        "remaining": None,
        "darts_left": 3,
        "paths": [],
        "reason": "match_abandoned",
    }


# --------------------------------------------------------------------------
# Payload validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"segment": 21, "multiplier": 1}, "segment"),
        ({"segment": -1, "multiplier": 1}, "segment"),
        ({"segment": 26, "multiplier": 1}, "segment"),
        ({"segment": 20, "multiplier": 4}, "multiplier"),
        ({"segment": 20, "multiplier": -1}, "multiplier"),
        ({"segment": 20, "multiplier": "T"}, "multiplier"),
        ({"segment": "twenty", "multiplier": 1}, "segment"),
    ],
)
def test_an_illegal_segment_or_multiplier_is_a_field_level_422(
    client: TestClient, body: dict[str, Any], field: str
) -> None:
    match = new_match(client, X01_501)
    payload = {**body, "client_dart_id": "bad"}

    response = client.post(f"/api/legs/{leg_of(match)}/darts", json=payload)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert [item["loc"] for item in error["detail"]] == [["body", field]]


@pytest.mark.parametrize(
    "body",
    [
        # There is no triple bull.
        {"segment": 25, "multiplier": 3},
        # A miss is the only throw with multiplier 0, and it is the only one
        # on segment 0 -- neither half is legal without the other.
        {"segment": 20, "multiplier": 0},
        {"segment": 0, "multiplier": 1},
    ],
)
def test_an_illegal_combination_is_a_422(client: TestClient, body: dict[str, Any]) -> None:
    match = new_match(client, X01_501)

    response = client.post(
        f"/api/legs/{leg_of(match)}/darts", json={**body, "client_dart_id": "combo"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize(
    "body",
    [
        {"segment": 20, "multiplier": 1},
        {"segment": 20, "multiplier": 1, "client_dart_id": ""},
        {"segment": 20, "multiplier": 1, "client_dart_id": "   "},
        {"segment": 20, "multiplier": 1, "client_dart_id": "k", "extra": True},
        {"multiplier": 1, "client_dart_id": "k"},
        {"segment": 20, "client_dart_id": "k"},
    ],
)
def test_a_malformed_body_is_a_422(client: TestClient, body: dict[str, Any]) -> None:
    match = new_match(client, X01_501)
    response = client.post(f"/api/legs/{leg_of(match)}/darts", json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_a_refused_payload_never_reaches_the_database(
    client: TestClient, settings: Settings
) -> None:
    match = new_match(client, X01_501)
    before = rows(settings, "darts")

    for body in (
        {"segment": 25, "multiplier": 3, "client_dart_id": "never-1"},
        {"segment": 99, "multiplier": 1, "client_dart_id": "never-2"},
        {"segment": 20, "multiplier": 1, "client_dart_id": ""},
    ):
        assert client.post(f"/api/legs/{leg_of(match)}/darts", json=body).status_code == 422

    assert rows(settings, "darts") == before
    assert rows(settings, "visits") == 0
    with connection(settings.db_path) as conn:
        assert _client_ids(conn) == []


def _client_ids(conn: sqlite3.Connection) -> list[str]:
    return [row[0] for row in conn.execute("SELECT client_dart_id FROM darts")]


def test_a_legal_throw_on_an_unplayable_leg_is_refused_before_validation_matters(
    client: TestClient,
) -> None:
    """A 404 for the leg, not a 422 -- the payload here is perfectly legal."""
    response = client.post(
        "/api/legs/4242/darts", json={"segment": 20, "multiplier": 3, "client_dart_id": "x"}
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


def test_an_immediate_retry_returns_the_same_state_and_records_one_dart(
    client: TestClient, settings: Settings
) -> None:
    match = new_match(client, X01_501)
    leg = leg_of(match)
    body = dart("T20", "retry-me")

    first = client.post(f"/api/legs/{leg}/darts", json=body)
    second = client.post(f"/api/legs/{leg}/darts", json=body)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert rows(settings, "darts") == 1
    assert first.json()["current_leg"]["darts_thrown"] == 1


def test_a_reused_key_describing_a_different_dart_is_a_409(client: TestClient) -> None:
    match = new_match(client, X01_501)
    leg = leg_of(match)
    assert throw_one(client, leg, "T20", "same-key").status_code == 200

    refused = throw_one(client, leg, "T19", "same-key")
    assert refused.status_code == 409
    assert refused.json()["error"]["detail"]["reason"] == "idempotency_conflict"


def test_a_reused_key_on_a_different_leg_is_a_409(client: TestClient) -> None:
    first = new_match(client, X01_501)
    second = new_match(client, X01_501)
    assert throw_one(client, leg_of(first), "T20", "shared").status_code == 200

    refused = throw_one(client, leg_of(second), "T20", "shared")
    assert refused.status_code == 409
    assert refused.json()["error"]["detail"]["reason"] == "idempotency_conflict"


def test_undo_frees_the_key_it_deleted(client: TestClient, settings: Settings) -> None:
    """Undo is a hard delete, so the client id it held becomes usable again."""
    match = new_match(client, X01_501)
    leg = leg_of(match)
    throw_one(client, leg, "T20", "reusable")
    assert client.post(f"/api/legs/{leg}/undo").status_code == 200

    assert throw_one(client, leg, "T19", "reusable").status_code == 200
    assert rows(settings, "darts") == 1


# --------------------------------------------------------------------------
# Undo
# --------------------------------------------------------------------------


def test_undo_on_a_leg_with_no_darts_is_a_409_not_a_500(client: TestClient) -> None:
    match = new_match(client, X01_501)

    response = client.post(f"/api/legs/{leg_of(match)}/undo")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert response.json()["error"]["detail"]["reason"] == "nothing_to_undo"


def test_undo_returns_the_whole_state_like_a_throw_does(client: TestClient) -> None:
    match = new_match(client, X01_501)
    leg = leg_of(match)
    throw_all(client, leg, ["T20", "T19"])

    body = client.post(f"/api/legs/{leg}/undo").json()

    assert body["current_leg"]["darts_thrown"] == 1
    assert body["current_leg"]["darts_left"] == 2
    assert [d["label"] for d in body["current_leg"]["current_visit"]["darts"]] == ["T20"]
    assert team_state(body["current_leg"], match["teams"][0]["id"])["remaining"] == 441
    assert body["current_leg"]["checkout"]["darts_left"] == 2


def test_undo_across_a_bust_restores_the_darts_it_voided(client: TestClient) -> None:
    match = new_match(client, X01_301)
    leg = leg_of(match)
    throw_all(client, leg, ["T20", "T20", "T20", "MISS", "MISS", "MISS"])
    throw_all(client, leg, ["T20", "T20"], prefix="bust")

    body = client.post(f"/api/legs/{leg}/undo").json()["current_leg"]

    # The bust is gone, so the surviving dart counts again and the visit reopens.
    assert body["current_visit"] is not None
    assert [d["label"] for d in body["current_visit"]["darts"]] == ["T20"]
    assert [d["counted"] for d in body["current_visit"]["darts"]] == [True]
    assert body["current_visit"]["is_bust"] is False
    assert team_state(body, match["teams"][0]["id"])["remaining"] == 61


def test_undoing_a_winning_dart_reopens_the_leg_and_the_match(client: TestClient) -> None:
    match = new_match(client, X01_301)
    leg = leg_of(match)
    won = throw_all(client, leg, FINISH_301).json()
    assert won["legs_won"] == [1, 0]

    body = client.post(f"/api/legs/{leg}/undo").json()

    assert body["legs_won"] == [0, 0]
    assert body["current_leg"]["is_complete"] is False
    assert body["current_leg"]["winner_team_id"] is None
    # The leg opened behind the win has been closed again.
    assert body["active_leg_id"] == leg
    assert body["active_leg"] is None
    # Back on the dart before the finish: 28 left, one dart of the visit to go.
    assert body["current_leg"]["checkout"]["remaining"] == 28
    assert body["current_leg"]["checkout"]["darts_left"] == 1
    assert body["current_leg"]["checkout"]["paths"] == [["D14"]]


def test_undo_takes_back_a_cricket_mark(client: TestClient) -> None:
    match = new_match(client, cricket("standard"))
    leg = leg_of(match)
    throw_all(client, leg, ["T20", "T19"])
    team = match["teams"][0]["id"]

    body = client.post(f"/api/legs/{leg}/undo").json()["current_leg"]

    marks = team_state(body, team)["marks"]
    assert marks["20"] == 3
    assert marks.get("19", 0) == 0
    assert [d["label"] for d in body["current_visit"]["darts"]] == ["T20"]


def test_undo_does_not_reach_back_across_a_leg_boundary(client: TestClient) -> None:
    match = new_match(client, X01_301)
    first = leg_of(match)
    body = throw_all(client, first, FINISH_301).json()
    throw_one(client, body["active_leg_id"], "T20", "next-leg")

    refused = client.post(f"/api/legs/{first}/undo")
    assert refused.status_code == 409
    assert refused.json()["error"]["detail"]["reason"] == "leg_complete"
