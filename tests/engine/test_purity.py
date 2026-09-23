"""Mechanical proof that the engine stays pure.

The engine's value is that it is deterministic and exhaustively testable. That
holds only while it has no clock, no randomness, no I/O, no framework types and
no dependency on the layers above it. This module enforces that by reading every
engine source file off disk and walking its AST.

It deliberately does *not* import the engine modules to inspect them: importing
executes them, and executing them is precisely the thing being constrained. A
banned import would have already happened by the time we could look at it.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_ROOT = REPO_ROOT / "backend" / "darts" / "engine"

#: Standard library and third-party modules the engine may never touch.
#: `datetime`, `random` and `time` would make it non-deterministic; `os` would
#: give it the environment and the filesystem; `sqlite3` is persistence;
#: `fastapi` and `pydantic` are transport and belong at the edges.
BANNED_MODULES = frozenset({"sqlite3", "fastapi", "pydantic", "datetime", "random", "os", "time"})

#: Layers the engine sits underneath. It must not know they exist. `darts.config`
#: is not one of those layers but belongs here for the same reason `os` does:
#: settings are an edge concern, and an engine that read one would stop being a
#: pure function of its arguments.
BANNED_PACKAGES = frozenset(
    {"darts.db", "darts.api", "darts.repo", "darts.services", "darts.config"}
)

BANNED = BANNED_MODULES | BANNED_PACKAGES


def _is_banned(dotted_name: str) -> bool:
    """True if `dotted_name` is a banned module or lives inside one.

    Prefix-matched on dot boundaries, so `os.path` and `darts.db.anything` are
    caught while `ostrich` and `dartsmith` are not.
    """
    return any(dotted_name == banned or dotted_name.startswith(f"{banned}.") for banned in BANNED)


def _package_of(module_path: Path) -> list[str]:
    """The dotted package parts containing `module_path`, e.g. ["darts", "engine"]."""
    relative = module_path.relative_to(REPO_ROOT / "backend")
    return list(relative.parent.parts)


def _imported_names(tree: ast.Module, module_path: Path) -> list[tuple[str, int]]:
    """Every module name the file imports, paired with its line number.

    Relative imports are resolved to absolute names, so `from ...db import x`
    inside the engine is caught rather than skipped. `from darts import db` is
    reported as both `darts` and `darts.db`, since importing a name off a
    package may well be importing a submodule.
    """
    found: list[tuple[str, int]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)

        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # `from . import x` is level 1 and stays in the current package;
                # each extra dot climbs one level further up.
                package = _package_of(module_path)
                base = package[: len(package) - (node.level - 1)]
                parts = base + ([node.module] if node.module else [])
            else:
                parts = [node.module] if node.module else []

            if not parts:
                continue

            module = ".".join(parts)
            found.append((module, node.lineno))
            found.extend((f"{module}.{alias.name}", node.lineno) for alias in node.names)

    return found


def _violations(tree: ast.Module, module_path: Path) -> list[str]:
    """Human-readable violation lines for one parsed module, empty if it is pure."""
    return [
        f"{module_path.relative_to(REPO_ROOT)}:{lineno} imports {name!r}"
        for name, lineno in _imported_names(tree, module_path)
        if _is_banned(name)
    ]


def _engine_modules() -> list[Path]:
    return sorted(ENGINE_ROOT.rglob("*.py"))


def test_engine_package_exists_and_has_modules() -> None:
    """Stops the guard silently passing because it found nothing to check."""
    assert ENGINE_ROOT.is_dir(), f"engine package missing at {ENGINE_ROOT}"
    assert _engine_modules(), f"no Python modules found under {ENGINE_ROOT}"


@pytest.mark.parametrize(
    "module_path", _engine_modules(), ids=lambda p: p.relative_to(ENGINE_ROOT).as_posix()
)
def test_engine_module_is_pure(module_path: Path) -> None:
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(module_path))

    violations = _violations(tree, module_path)

    assert not violations, (
        "the engine must stay pure and dependency-free, but:\n  "
        + "\n  ".join(violations)
        + "\n\nMove this work to a layer above the engine, or inject the value as "
        "an argument. See tests/engine/test_purity.py for the ban list."
    )


# --- the guard's own tests -------------------------------------------------
#
# A guard nobody has watched fail is not a guard. These pin the matching rules
# without needing to edit a real engine module.


@pytest.mark.parametrize(
    "dotted_name",
    [
        "os",
        "os.path",
        "sqlite3",
        "datetime",
        "random",
        "time",
        "fastapi",
        "pydantic",
        "darts.db",
        "darts.db.connection",
        "darts.api.main",
        "darts.repo.games",
        "darts.services.scoring",
    ],
)
def test_banned_names_are_rejected(dotted_name: str) -> None:
    assert _is_banned(dotted_name)


@pytest.mark.parametrize(
    "dotted_name",
    [
        "dataclasses",
        "typing",
        "darts",
        "darts.engine",
        "darts.engine.throws",
        # Prefix matching must respect dot boundaries, not raw string prefixes.
        "ostrich",
        "timeit",
        "randomise",
        "dartsmith.db",
    ],
)
def test_permitted_names_are_accepted(dotted_name: str) -> None:
    assert not _is_banned(dotted_name)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import sqlite3", "sqlite3"),
        ("import os.path", "os.path"),
        ("import sqlite3 as db", "sqlite3"),
        ("from os import path", "os.path"),
        ("from darts import db", "darts.db"),
        ("from darts.db import connection", "darts.db.connection"),
        # Hidden inside a function body rather than at module level.
        ("def f():\n    import random\n", "random"),
        # Relative, resolved against darts.engine: `..api` climbs to `darts`.
        ("from ..api import main", "darts.api"),
        ("from . import types", "darts.engine.types"),
    ],
)
def test_import_forms_are_detected(source: str, expected: str) -> None:
    """Both `import x` and `from x import y`, at any nesting, absolute or relative."""
    tree = ast.parse(source)
    names = [name for name, _ in _imported_names(tree, ENGINE_ROOT / "throws.py")]
    assert expected in names


def test_a_banned_import_in_an_engine_module_fails_the_guard() -> None:
    """The demonstration, run on every build instead of by hand.

    Feeds a real engine module's source plus `import sqlite3` through the exact
    code path `test_engine_module_is_pure` uses, so we know that assertion
    actually fires rather than only that the helpers work in isolation.
    """
    offender = ENGINE_ROOT / "throws.py"
    source = offender.read_text(encoding="utf-8")

    clean = _violations(ast.parse(source), offender)
    assert clean == []

    tainted = _violations(ast.parse(f"import sqlite3\n{source}"), offender)
    assert tainted == ["backend/darts/engine/throws.py:1 imports 'sqlite3'"]
