"""Golden bytes plus an immutable baseline: changing SQL AND the list still fails CI."""

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from darts.db.migrate import MIGRATIONS

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests/db/migration_checksums.txt"


def parse_list(text: str) -> dict[str, str]:
    result = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        digest, name = line.split()
        assert name not in result, f"duplicate checksum entry: {name}"
        result[name] = digest
    return result


def assert_checksums(directory: Path, golden: str, baseline: str = "") -> None:
    expected = parse_list(golden)
    actual = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.glob("*.sql")}
    assert actual == expected, "migration bytes do not match the golden list"
    for name, digest in parse_list(baseline).items():
        assert expected.get(name) == digest, f"committed migration is immutable: {name}"


def test_migrations_match_golden_and_committed_baseline() -> None:
    ref = os.environ.get("MIGRATION_BASE_REF", "")
    baseline = ""
    if ref and set(ref) != {"0"}:
        relative = GOLDEN.relative_to(ROOT).as_posix()
        listed = subprocess.run(
            ["git", "ls-tree", ref, "--", relative],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        if listed.stdout:
            baseline = subprocess.run(
                ["git", "show", f"{ref}:{relative}"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
    assert_checksums(MIGRATIONS, GOLDEN.read_text(), baseline)


def test_changed_file_fails_even_if_golden_is_updated(tmp_path: Path) -> None:
    path = tmp_path / "0001_init.sql"
    original = b"CREATE TABLE example(id INTEGER);\n"
    baseline = f"{hashlib.sha256(original).hexdigest()}  {path.name}\n"
    path.write_bytes(original + b"-- tampered\n")
    with pytest.raises(AssertionError, match="golden list"):
        assert_checksums(tmp_path, baseline)
    updated = f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
    with pytest.raises(AssertionError, match="immutable"):
        assert_checksums(tmp_path, updated, baseline)


def test_new_migrations_append_without_changing_old_hashes(tmp_path: Path) -> None:
    first = tmp_path / "0001_one.sql"
    first.write_text("CREATE TABLE one(id INTEGER);", encoding="utf-8")
    baseline = f"{hashlib.sha256(first.read_bytes()).hexdigest()}  {first.name}\n"
    second = tmp_path / "0002_two.sql"
    second.write_text("CREATE TABLE two(id INTEGER);", encoding="utf-8")
    golden = baseline + f"{hashlib.sha256(second.read_bytes()).hexdigest()}  {second.name}\n"
    assert_checksums(tmp_path, golden, baseline)


def test_golden_rejects_duplicate_entries() -> None:
    with pytest.raises(AssertionError, match="duplicate"):
        parse_list("abc file.sql\nabc file.sql\n")
