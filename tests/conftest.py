"""Put the shared, non-collected fixture modules on the import path.

`tests/db/dbfixtures.py` is importable only because pytest prepends each test
file's own directory; `tests/fixtures/seed.py` is imported from `tests/db` too,
so it has to be said explicitly here rather than depending on which directory
pytest happens to collect first.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
