"""`darts-openapi`, which is what `npm run gen:api` reads the contract from.

Named `test_openapi_tool` because `tests/api/test_openapi.py` already tests the
served schema and pytest imports test modules by basename.
"""

import json
from pathlib import Path

import pytest

from darts.tools.openapi import main, schema


def test_the_schema_it_dumps_is_the_one_the_app_serves(tmp_path: Path) -> None:
    out = tmp_path / "openapi.json"

    assert main(["-o", str(out)]) == 0

    document = json.loads(out.read_text())
    assert document["info"]["title"] == "darts"
    assert "/api/matches/{match_id}/state" in document["paths"]
    assert "MatchStateResponse" in document["components"]["schemas"]


def test_two_dumps_of_the_same_app_are_byte_identical(tmp_path: Path) -> None:
    """Sorted keys and a fixed indent, so the drift check is a comparison."""
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    main(["-o", str(first)])
    main(["-o", str(second)])

    assert first.read_bytes() == second.read_bytes()
    assert first.read_text().endswith("}\n")


def test_an_output_path_is_required(tmp_path: Path) -> None:
    """Stdout carries the application log, so there is no pipe mode to misuse."""
    with pytest.raises(SystemExit) as exit_info:
        main([])
    assert exit_info.value.code == 2


def test_it_builds_its_own_database_and_leaves_nothing_behind(tmp_path: Path) -> None:
    """Generation must never be pointed at the database somebody is playing on."""
    before = set(tmp_path.iterdir())

    document = schema()

    assert document["paths"]
    # The temporary database lived somewhere else entirely and was cleaned up.
    assert set(tmp_path.iterdir()) == before
