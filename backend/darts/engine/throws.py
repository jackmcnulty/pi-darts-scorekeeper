"""The atomic unit of the system: a single dart landing somewhere on the board.

This module is vocabulary, not rules. It knows what a legal throw *is* and what
it is *worth in isolation*; it knows nothing about games, turns, busts or
checkouts. Those arrive in later tickets and build on this.
"""

from dataclasses import dataclass
from typing import Final

#: The board has no segment 0; it is the sentinel for a dart that scored nothing.
MISS: Final = 0

#: Both bull rings share segment 25. The inner bull is scored as a double of it.
BULL: Final = 25

SINGLE: Final = 1
DOUBLE: Final = 2
TRIPLE: Final = 3

#: The numbered wedges, 1 through 20.
NUMBERED_SEGMENTS: Final = frozenset(range(1, 21))

_LEGAL_SEGMENTS: Final = NUMBERED_SEGMENTS | {MISS, BULL}

_MISS_LABEL: Final = "MISS"
_OUTER_BULL_LABEL: Final = "25"
_INNER_BULL_LABEL: Final = "BULL"

# Parsing looks segments up by their exact digit string rather than calling
# int(), which would happily accept "020", "+20" and non-ASCII digits such as
# "٢٠" — none of which `label` can ever emit.
_SEGMENT_BY_DIGITS: Final[dict[str, int]] = {str(n): n for n in sorted(NUMBERED_SEGMENTS)}

_MULTIPLIER_PREFIXES: Final[dict[str, int]] = {"D": DOUBLE, "T": TRIPLE}


@dataclass(frozen=True, slots=True)
class Throw:
    """One dart. Immutable, hashable, and illegal combinations cannot be built.

    `segment` is 0 (a miss), 1..20, or 25 (either bull ring). `multiplier` is
    0..3, and is 0 exactly when the throw is a miss.
    """

    segment: int
    multiplier: int

    def __post_init__(self) -> None:
        if self.segment not in _LEGAL_SEGMENTS:
            raise ValueError(f"segment must be 0, 1..20 or 25; got {self.segment!r}")
        if not 0 <= self.multiplier <= TRIPLE:
            raise ValueError(f"multiplier must be 0..3; got {self.multiplier!r}")
        # Multiplier 0 and segment 0 are two halves of the same fact: a miss.
        # Neither is meaningful without the other.
        if (self.segment == MISS) != (self.multiplier == 0):
            raise ValueError(
                "a miss is the only throw with multiplier 0; got "
                f"segment={self.segment!r}, multiplier={self.multiplier!r}"
            )
        if self.segment == BULL and self.multiplier == TRIPLE:
            raise ValueError("there is no triple bull")

    @property
    def score(self) -> int:
        """What this dart is worth on its own, ignoring any game's rules."""
        return self.segment * self.multiplier

    @property
    def is_double(self) -> bool:
        """True for the doubles ring, and for the inner bull, which is 25 doubled."""
        return self.multiplier == DOUBLE

    @property
    def is_triple(self) -> bool:
        return self.multiplier == TRIPLE

    @property
    def label(self) -> str:
        """The short human form: "MISS", "20", "D16", "T20", "25", "BULL"."""
        if self.segment == MISS:
            return _MISS_LABEL
        if self.segment == BULL:
            return _INNER_BULL_LABEL if self.multiplier == DOUBLE else _OUTER_BULL_LABEL
        if self.multiplier == DOUBLE:
            return f"D{self.segment}"
        if self.multiplier == TRIPLE:
            return f"T{self.segment}"
        return str(self.segment)

    @classmethod
    def parse(cls, label: str) -> "Throw":
        """The exact inverse of `label`, and deliberately nothing more.

        Strict by design: it accepts only the strings `label` emits. Lowercase
        ("t20"), padded (" T20 "), an explicit single prefix ("S20"), and bull
        spellings the board does not have ("D25", "T25") are all rejected.
        Widening this later is backwards-compatible; narrowing it would not be,
        so callers that need leniency should normalise before calling.
        """
        if label == _MISS_LABEL:
            return cls(MISS, 0)
        if label == _INNER_BULL_LABEL:
            return cls(BULL, DOUBLE)
        if label == _OUTER_BULL_LABEL:
            return cls(BULL, SINGLE)

        multiplier = _MULTIPLIER_PREFIXES.get(label[:1], SINGLE)
        digits = label[1:] if multiplier != SINGLE else label

        segment = _SEGMENT_BY_DIGITS.get(digits)
        if segment is None:
            raise ValueError(f"not a throw label: {label!r}")
        return cls(segment, multiplier)


#: Every legal throw: the miss, 20 segments x 3 multipliers, and the two bulls.
#:
#: 63, not the 62 you may have seen quoted — 62 counts the board's *scoring*
#: segments, which excludes the miss. A miss is a legal thing for a dart to do,
#: so it is a member here.
ALL_THROWS: Final[frozenset[Throw]] = frozenset(
    {Throw(MISS, 0), Throw(BULL, SINGLE), Throw(BULL, DOUBLE)}
    | {
        Throw(segment, multiplier)
        for segment in NUMBERED_SEGMENTS
        for multiplier in (SINGLE, DOUBLE, TRIPLE)
    }
)
