"""Settings: defaults, overrides, and the failures worth catching at startup."""

import dataclasses
from pathlib import Path

import pytest

from darts.config import (
    DEFAULT_DB_PATH,
    DEFAULT_PORT,
    DEFAULT_STATIC_DIR,
    ENV_PREFIX,
    UNKNOWN_SHA,
    ConfigError,
    Settings,
)


def test_defaults_when_nothing_is_set() -> None:
    settings = Settings.from_env({})
    assert settings.db_path == DEFAULT_DB_PATH
    assert settings.static_dir == DEFAULT_STATIC_DIR
    assert settings.port == DEFAULT_PORT
    assert settings.git_sha == UNKNOWN_SHA
    assert settings.log_level == "INFO"


def test_every_setting_is_overridable(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {
            f"{ENV_PREFIX}DB_PATH": str(tmp_path / "db" / "darts.db"),
            f"{ENV_PREFIX}BACKUP_DIR": str(tmp_path / "usb" / "backups"),
            f"{ENV_PREFIX}SNAPSHOT_DIR": "/srv/darts-share",
            f"{ENV_PREFIX}STATIC_DIR": str(tmp_path / "dist"),
            f"{ENV_PREFIX}PORT": "9001",
            f"{ENV_PREFIX}GIT_SHA": "0badcafe",
            f"{ENV_PREFIX}LOG_LEVEL": "debug",
        }
    )
    assert settings.db_path == tmp_path / "db" / "darts.db"
    assert settings.backup_dir == tmp_path / "usb" / "backups"
    assert settings.snapshot_dir == Path("/srv/darts-share")
    assert settings.static_dir == tmp_path / "dist"
    assert settings.port == 9001
    assert settings.git_sha == "0badcafe"
    assert settings.log_level == "DEBUG"


def test_moving_the_database_moves_backups_and_snapshots_with_it(tmp_path: Path) -> None:
    """The USB SSD story: one variable, and everything follows."""
    ssd = tmp_path / "mnt" / "ssd" / "darts"
    settings = Settings.from_env({f"{ENV_PREFIX}DB_PATH": str(ssd / "darts.db")})
    assert settings.backup_dir == ssd / "backups"
    assert settings.snapshot_dir == ssd / "snapshots"


def test_backups_can_still_be_sent_somewhere_else(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {
            f"{ENV_PREFIX}DB_PATH": str(tmp_path / "darts.db"),
            f"{ENV_PREFIX}BACKUP_DIR": str(tmp_path / "elsewhere"),
        }
    )
    assert settings.backup_dir == tmp_path / "elsewhere"


def test_an_empty_variable_means_unset() -> None:
    """A Compose env file full of `DARTS_PORT=` must not mean a port of ''."""
    settings = Settings.from_env({f"{ENV_PREFIX}PORT": "  ", f"{ENV_PREFIX}GIT_SHA": ""})
    assert settings.port == DEFAULT_PORT
    assert settings.git_sha == UNKNOWN_SHA


def test_paths_are_absolute_and_expanded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = Settings.from_env(
        {f"{ENV_PREFIX}DB_PATH": "var/darts.db", f"{ENV_PREFIX}STATIC_DIR": "~/dist"}
    )
    assert settings.db_path == (tmp_path / "var" / "darts.db").resolve()
    assert settings.static_dir == (tmp_path / "dist").resolve()


def test_from_env_reads_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(f"{ENV_PREFIX}GIT_SHA", "fromenviron")
    assert Settings.from_env().git_sha == "fromenviron"


@pytest.mark.parametrize("port", ["eight thousand", "0", "-1", "65536"])
def test_an_unusable_port_fails_at_startup(port: str) -> None:
    with pytest.raises(ConfigError, match="PORT"):
        Settings.from_env({f"{ENV_PREFIX}PORT": port})


def test_an_unknown_log_level_fails_at_startup() -> None:
    """Rather than at the first log call, which is likely to be an error."""
    with pytest.raises(ConfigError, match="LOG_LEVEL"):
        Settings.from_env({f"{ENV_PREFIX}LOG_LEVEL": "chatty"})


def test_settings_are_frozen(tmp_path: Path) -> None:
    settings = Settings.from_env({f"{ENV_PREFIX}DB_PATH": str(tmp_path / "darts.db")})
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.db_path = tmp_path / "other.db"  # type: ignore[misc]
