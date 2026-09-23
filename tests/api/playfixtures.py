"""Helpers the play-API tests share, as plain functions rather than fixtures.

Follows `tests/services/servicefixtures.py`: conftest.py holds the fixtures and
these are ordinary functions that read better imported by name.

Everything here goes over HTTP. The point of #18's tests is that a client with
nothing but a base URL can play a leg, so a helper that reached into the
database to set something up would be testing a different claim.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from itertools import count
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from darts.engine.throws import Throw

X01_501 = {
    "game_type": "x01",
    "start_score": 501,
    "in_rule": "straight",
    "out_rule": "double",
    "best_of": 3,
}
X01_301 = {**X01_501, "start_score": 301}

#: 501 that must be opened on a double, for the hint-suppression case.
X01_501_DOUBLE_IN = {**X01_501, "in_rule": "double"}

#: Six triples and two bulls close all seven targets with no surplus, so every
#: team finishes on nought points and the same script wins under all three
#: variants -- `standard` wants the most points, `cutthroat` the fewest, and
#: nought satisfies both against an opponent who has also scored nought.
CRICKET_CLOSE_OUT = ["T20", "T19", "T18", "T17", "T16", "T15", "BULL", "25"]

#: A visit that scores nothing and cannot win, for the team not under test.
BLANK_VISIT = ["MISS", "MISS", "MISS"]

#: Keeps `new_match` from colliding with its own players; see below.
_PLAYER_SEQ = count(1)


def cricket(variant: str, best_of: int = 1) -> dict[str, Any]:
    return {"game_type": "cricket", "variant": variant, "best_of": best_of}


def new_match(
    client: TestClient, config: dict[str, Any], *, teams: Sequence[Sequence[str]] | None = None
) -> dict[str, Any]:
    """Create the players and the match, and return the match body.

    Two solo teams unless `teams` names the members, which is what nearly every
    test wants; `current_leg_id` on the result is leg 0.

    Player names are suffixed with a per-process counter because display names
    are unique: a test that builds two matches would otherwise get a 409 on the
    second one's players and fail somewhere confusing.
    """
    names = teams if teams is not None else (("Ana",), ("Ben",))
    squads = []
    for squad in names:
        ids = []
        for name in squad:
            created = client.post(
                "/api/players", json={"display_name": f"{name} {next(_PLAYER_SEQ)}"}
            )
            assert created.status_code == 201, created.text
            ids.append(created.json()["id"])
        squads.append({"player_ids": ids})

    response = client.post("/api/matches", json={"config": config, "teams": squads})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def dart(label: str, key: str) -> dict[str, Any]:
    """A `POST /darts` body for a throw label, keyed for idempotency."""
    throw = Throw.parse(label)
    return {"segment": throw.segment, "multiplier": throw.multiplier, "client_dart_id": key}


def throw_one(client: TestClient, leg_id: int, label: str, key: str) -> Response:
    return client.post(f"/api/legs/{leg_id}/darts", json=dart(label, key))


def throw_all(
    client: TestClient, leg_id: int, labels: Sequence[str], *, prefix: str = "k"
) -> Response:
    """Throw every label into one leg, returning the last response.

    Asserts each dart was accepted, so a test that meant to set up a position
    fails where it went wrong rather than three assertions later.
    """
    response = None
    for index, label in enumerate(labels):
        response = throw_one(client, leg_id, label, f"{prefix}-{leg_id}-{index}")
        assert response.status_code == 200, response.text
    assert response is not None, "throw_all needs at least one label"
    return response


def throw_match(
    client: TestClient, leg_id: int, labels: Sequence[str], *, prefix: str = "m"
) -> Response:
    """Throw labels into whichever leg is active, following leg changes.

    The leg to use comes out of the previous *response*, never from a fresh
    GET -- which is the whole claim #18 makes about `POST /darts`.
    """
    response = None
    active = leg_id
    for index, label in enumerate(labels):
        assert active is not None, "the match ended before the script did"
        response = throw_one(client, active, label, f"{prefix}-{index}")
        assert response.status_code == 200, response.text
        active = response.json()["active_leg_id"]
    assert response is not None, "throw_match needs at least one label"
    return response


def alternating(
    under_test: Sequence[str], *, filler: Sequence[str] = tuple(BLANK_VISIT)
) -> list[str]:
    """Interleave one team's visits with blank visits from the other.

    `under_test` is read three darts at a time -- the caller writes the script
    for the team they care about, and the opponent throws misses in between.
    Two teams alternate, so a flat script of one team's darts would otherwise
    hand two thirds of them to the wrong player.

    No filler after the last chunk: that chunk is often the one that wins the
    leg, and a further dart into a won leg is a 409. A caller who wants the
    turn to come back round appends `BLANK_VISIT` itself.
    """
    script: list[str] = []
    for start in range(0, len(under_test), 3):
        if script:
            script.extend(filler)
        script.extend(under_test[start : start + 3])
    return script


@contextmanager
def no_gets(client: TestClient) -> Iterator[list[str]]:
    """Record every GET made inside the block, so a test can prove there were none.

    #18's first acceptance criterion is that a full leg needs no GET after the
    initial load. Asserting the recorded list is empty proves the responses
    really did carry everything, rather than the test merely happening not to
    look.
    """
    original = client.get
    seen: list[str] = []

    def guard(url: str, *args: Any, **kwargs: Any) -> Response:
        seen.append(url)
        return original(url, *args, **kwargs)

    client.get = guard  # type: ignore[method-assign]
    try:
        yield seen
    finally:
        client.get = original  # type: ignore[method-assign]
