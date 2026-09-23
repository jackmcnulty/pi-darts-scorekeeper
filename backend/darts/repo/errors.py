"""Failures a caller is expected to handle, as opposed to programming errors.

An invalid `GameConfig` raises pydantic's own `ValidationError` rather than one
of these; it is caught before a repository function is ever entered.
"""


class RepoError(Exception):
    """Base for every error this layer raises deliberately."""


class NotFoundError(RepoError):
    """A row was addressed by id and does not exist."""


class DuplicateNameError(RepoError):
    """A player's display name is already held by another active player."""


class InvalidMatchError(RepoError):
    """The teams handed to `create_match` do not describe a playable match."""
