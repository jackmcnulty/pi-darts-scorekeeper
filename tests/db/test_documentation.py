import re
import sqlite3
from pathlib import Path


def test_every_table_and_column_is_documented(db: sqlite3.Connection) -> None:
    document = (Path(__file__).resolve().parents[2] / "docs/data-model.md").read_text()
    sections = re.split(r"^### (\w+)\s*$", document, flags=re.MULTILINE)
    tables = dict(zip(sections[1::2], sections[2::2], strict=True))
    actual = {r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    assert set(tables) == actual
    for table in sorted(actual):
        columns = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        # The final section also contains the index table: only column names count here.
        section = tables[table].split("\n## ")[0]
        documented = set(re.findall(r"^\| `(\w+)` \|", section, flags=re.MULTILINE))
        assert documented == columns, table
