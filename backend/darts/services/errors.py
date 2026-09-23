"""Failures of the *rules*, as opposed to failures of the rows.

`darts.repo.errors` answers "is there such a row"; these answer "is that a legal
thing to do". They are a separate hierarchy on purpose: a caller of the
repository layer should never be in a position to catch a game-rule error,
because the repository layer never raises one.

#18 gets two stable bases to map onto status codes -- `RepoError` for
addressing, `ServiceError` for legality -- and each subclass here names one
409-shaped situation rather than being distinguished by its message.
"""


class ServiceError(Exception):
    """Base for every error the service layer raises deliberately."""


class LegCompleteError(ServiceError):
    """The leg has already been won, so nothing more can be thrown into it."""


class MatchCompleteError(ServiceError):
    """The match has already been won, so none of its legs accept darts."""


class IdempotencyConflictError(ServiceError):
    """A `client_dart_id` already in use describes a different dart.

    A retry of the same throw is not an error -- it is the whole point of the
    key. This is the other case: the same key arriving with a different target
    or on a different leg, which is a client that has reused a key it should
    have minted fresh.
    """


class NothingToUndoError(ServiceError):
    """Undo was asked for on a leg that has no darts in it."""


class MatchAbandonedError(ServiceError):
    """An abandoned match no longer accepts play or undo."""
