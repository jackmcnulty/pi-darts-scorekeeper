"""Tests for the throw vocabulary.

`ALL_THROWS` is small enough (63) to test exhaustively rather than by sampling,
which is the whole point of keeping the engine pure — so where a property holds
for every throw, it is asserted for every throw.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from darts.engine.throws import ALL_THROWS, BULL, DOUBLE, MISS, SINGLE, TRIPLE, Throw

ALL_THROWS_SORTED = sorted(ALL_THROWS, key=lambda t: (t.segment, t.multiplier))


def test_all_throws_has_63_members() -> None:
    """1 miss + (20 segments x 3 multipliers) + outer bull + inner bull.

    The ticket originally said 62, which is the board's *scoring* segment count
    and excludes the miss. `Throw(0, 0)` is legal here, so the miss is a member.
    """
    assert len(ALL_THROWS) == 63


def test_all_throws_is_exactly_the_legal_throws() -> None:
    """Nothing legal is missing and nothing illegal snuck in."""
    expected = {Throw(MISS, 0), Throw(BULL, SINGLE), Throw(BULL, DOUBLE)}
    expected |= {
        Throw(segment, multiplier)
        for segment in range(1, 21)
        for multiplier in (SINGLE, DOUBLE, TRIPLE)
    }
    assert expected == ALL_THROWS


# --- construction ----------------------------------------------------------


@pytest.mark.parametrize(
    ("segment", "multiplier", "reason"),
    [
        (0, 1, "a miss cannot have a multiplier"),
        (0, 2, "a miss cannot have a multiplier"),
        (0, 3, "a miss cannot have a multiplier"),
        (20, 0, "only a miss has multiplier 0"),
        (25, 0, "only a miss has multiplier 0"),
        (25, 3, "there is no triple bull"),
        (21, 1, "no segment 21"),
        (26, 1, "no segment 26"),
        (-1, 1, "negative segment"),
        (-1, -1, "negative segment and multiplier"),
        (20, 4, "multiplier above 3"),
        (20, -1, "negative multiplier"),
    ],
)
def test_illegal_throws_raise_value_error(segment: int, multiplier: int, reason: str) -> None:
    with pytest.raises(ValueError):
        Throw(segment, multiplier)


@pytest.mark.parametrize("throw", ALL_THROWS_SORTED, ids=lambda t: t.label)
def test_every_legal_throw_constructs(throw: Throw) -> None:
    assert Throw(throw.segment, throw.multiplier) == throw


def test_throws_are_frozen() -> None:
    throw = Throw(20, TRIPLE)
    with pytest.raises(AttributeError):
        throw.segment = 19  # type: ignore[misc]


def test_throws_are_hashable_and_compare_by_value() -> None:
    assert Throw(20, TRIPLE) == Throw(20, TRIPLE)
    assert len({Throw(20, TRIPLE), Throw(20, TRIPLE)}) == 1
    assert Throw(20, TRIPLE) != Throw(20, DOUBLE)


# --- scoring ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("throw", "expected"),
    [
        (Throw(MISS, 0), 0),
        (Throw(BULL, SINGLE), 25),
        (Throw(BULL, DOUBLE), 50),
        (Throw(20, SINGLE), 20),
        (Throw(20, DOUBLE), 40),
        (Throw(20, TRIPLE), 60),
        (Throw(1, TRIPLE), 3),
    ],
)
def test_score(throw: Throw, expected: int) -> None:
    assert throw.score == expected


def test_highest_scoring_throw_is_t20() -> None:
    assert max(ALL_THROWS, key=lambda t: t.score).score == 60


@pytest.mark.parametrize("throw", ALL_THROWS_SORTED, ids=lambda t: t.label)
def test_score_is_segment_times_multiplier(throw: Throw) -> None:
    assert throw.score == throw.segment * throw.multiplier
    assert 0 <= throw.score <= 60


def test_is_double_and_is_triple() -> None:
    assert Throw(16, DOUBLE).is_double
    assert not Throw(16, DOUBLE).is_triple
    assert Throw(20, TRIPLE).is_triple
    assert not Throw(20, TRIPLE).is_double
    assert not Throw(20, SINGLE).is_double
    assert not Throw(MISS, 0).is_double
    assert not Throw(MISS, 0).is_triple


def test_inner_bull_counts_as_a_double() -> None:
    """It is 25 doubled, and games that finish on a double accept it."""
    assert Throw(BULL, DOUBLE).is_double
    assert not Throw(BULL, SINGLE).is_double


def test_inner_bull_stays_distinguishable_from_the_doubles_ring() -> None:
    """`is_double` is lossy on its own; the throw it came from never is.

    Because the inner bull reports `is_double is True`, anything that records
    only that flag would flatten BULL into "some double". Every representation
    a Throw actually carries keeps them apart, so an export that includes the
    segment, the label or the score remains interpretable. Pinned here because
    the place this can break is the export in #20, not this module.
    """
    inner_bull = Throw(BULL, DOUBLE)
    other_doubles = [t for t in ALL_THROWS if t.is_double and t != inner_bull]

    assert len(other_doubles) == 20
    assert all(t.segment != inner_bull.segment for t in other_doubles)
    assert all(t.label != inner_bull.label for t in other_doubles)
    assert all(t.score != inner_bull.score for t in other_doubles)


def test_no_throw_is_both_a_double_and_a_triple() -> None:
    assert not any(t.is_double and t.is_triple for t in ALL_THROWS)


# --- labels ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("throw", "expected"),
    [
        (Throw(MISS, 0), "MISS"),
        (Throw(BULL, SINGLE), "25"),
        (Throw(BULL, DOUBLE), "BULL"),
        (Throw(20, SINGLE), "20"),
        (Throw(16, DOUBLE), "D16"),
        (Throw(20, TRIPLE), "T20"),
        (Throw(1, SINGLE), "1"),
    ],
)
def test_label(throw: Throw, expected: str) -> None:
    assert throw.label == expected


def test_labels_are_unique_across_all_throws() -> None:
    """Required for `parse` to be a genuine inverse rather than lossy."""
    labels = [t.label for t in ALL_THROWS]
    assert len(set(labels)) == len(labels) == 63


# --- parsing ---------------------------------------------------------------


@pytest.mark.parametrize("throw", ALL_THROWS_SORTED, ids=lambda t: t.label)
def test_parse_round_trips_every_throw(throw: Throw) -> None:
    """The acceptance criterion: `Throw.parse(t.label) == t` for all of ALL_THROWS."""
    assert Throw.parse(throw.label) == throw


@pytest.mark.parametrize("throw", ALL_THROWS_SORTED, ids=lambda t: t.label)
def test_label_round_trips_from_parse(throw: Throw) -> None:
    """The inverse direction: parsing then re-labelling returns the same string."""
    assert Throw.parse(throw.label).label == throw.label


@pytest.mark.parametrize(
    "text",
    [
        # Case and whitespace are not normalised.
        "t20",
        "d16",
        "miss",
        "bull",
        " T20",
        "T20 ",
        "T 20",
        # There is no explicit single prefix; a single is just its number.
        "S20",
        "s20",
        # Bull spellings the board does not have.
        "D25",
        "T25",
        "S25",
        "BULLSEYE",
        "50",
        # Out of range, and non-canonical spellings of in-range numbers.
        "0",
        "21",
        "D21",
        "T0",
        "020",
        "+20",
        "-1",
        "20.0",
        # Non-ASCII digits, which int() would otherwise accept.
        "٢٠",
        # Structurally wrong.
        "",
        "D",
        "T",
        "X20",
        "20T",
        "TT20",
    ],
)
def test_parse_rejects_anything_label_cannot_emit(text: str) -> None:
    with pytest.raises(ValueError):
        Throw.parse(text)


@given(st.text(max_size=8))
def test_parse_either_round_trips_or_raises(text: str) -> None:
    """No input may crash with anything other than ValueError, and no input may
    parse into a throw that would not have produced it.
    """
    try:
        throw = Throw.parse(text)
    except ValueError:
        return
    assert throw.label == text
    assert throw in ALL_THROWS


@given(st.sampled_from(ALL_THROWS_SORTED))
def test_parse_is_the_inverse_of_label(throw: Throw) -> None:
    assert Throw.parse(throw.label) == throw
