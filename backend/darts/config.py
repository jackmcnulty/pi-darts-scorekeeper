"""Process configuration, resolved once from the environment.

Every path the process touches is a single environment variable, so moving the
database to a USB SSD is `DARTS_DB_PATH=/mnt/ssd/darts/darts.db` plus a file
copy, with no code change. Backups and snapshots follow the database by
default, so the move relocates all three unless they are overridden too.

The engine must never read this module -- settings are an edge concern, and
`tests/engine/test_purity.py` enforces that.
"""

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from darts.db.backup import default_backup_dir

#: Repository root in a source checkout: backend/darts/config.py -> ../../..
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every variable this process reads carries it, so `env | grep DARTS_` is complete.
ENV_PREFIX = "DARTS_"

#: Kept inside the checkout so a development run needs no privileged directory.
#: #28 points this at the `/var/lib/darts` bind mount instead.
DEFAULT_DB_PATH = _REPO_ROOT / "var" / "darts.db"
DEFAULT_STATIC_DIR = _REPO_ROOT / "frontend" / "dist"
DEFAULT_PORT = 8000
DEFAULT_LOG_LEVEL = "INFO"

#: What /api/healthz reports when no build stamped a revision in. #28's image
#: build sets DARTS_GIT_SHA; a source checkout has no reason to claim one.
UNKNOWN_SHA = "unknown"


class ConfigError(ValueError):
    """An environment variable holds something this process cannot use."""


@dataclass(frozen=True)
class Settings:
    """Where everything lives, resolved to absolute paths at startup.

    Frozen because a setting that changes while the process runs would leave
    the boot integrity check and the shutdown checkpoint pointed at different
    databases.
    """

    db_path: Path
    backup_dir: Path
    snapshot_dir: Path
    static_dir: Path
    port: int
    git_sha: str
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Read `DARTS_*` from `env`, falling back to the defaults above.

        An unset variable and one set to the empty string mean the same thing:
        a Compose env file full of `DARTS_BACKUP_DIR=` lines should behave like
        one that omits them, not like one asking for a path of "".
        """
        source = os.environ if env is None else env
        db_path = _path(source, "DB_PATH", DEFAULT_DB_PATH)
        return cls(
            db_path=db_path,
            backup_dir=_path(source, "BACKUP_DIR", default_backup_dir(db_path)),
            snapshot_dir=_path(source, "SNAPSHOT_DIR", db_path.parent / "snapshots"),
            static_dir=_path(source, "STATIC_DIR", DEFAULT_STATIC_DIR),
            port=_port(source),
            git_sha=_text(source, "GIT_SHA", UNKNOWN_SHA),
            log_level=_level(source),
        )


def _raw(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(ENV_PREFIX + name, "").strip()
    return value or None


def _text(env: Mapping[str, str], name: str, default: str) -> str:
    return _raw(env, name) or default


def _path(env: Mapping[str, str], name: str, default: Path) -> Path:
    """Absolute, `~`-expanded, and not required to exist yet.

    Absolute because a relative path would silently follow the working
    directory, and the backup CLIs are run from a different one than the
    server.
    """
    value = _raw(env, name)
    return (Path(value).expanduser() if value else default).resolve()


def _port(env: Mapping[str, str]) -> int:
    value = _raw(env, "PORT")
    if value is None:
        return DEFAULT_PORT
    try:
        port = int(value)
    except ValueError:
        raise ConfigError(f"{ENV_PREFIX}PORT is not a number: {value!r}") from None
    if not 1 <= port <= 65535:
        raise ConfigError(f"{ENV_PREFIX}PORT is outside 1-65535: {port}")
    return port


def _level(env: Mapping[str, str]) -> str:
    """Validate here rather than at the first log call, which may be an error."""
    value = _text(env, "LOG_LEVEL", DEFAULT_LOG_LEVEL).upper()
    if value not in logging.getLevelNamesMapping():
        raise ConfigError(f"{ENV_PREFIX}LOG_LEVEL is not a logging level: {value!r}")
    return value
