"""Commit darts, announce each commit, then hold one transaction open and wait.

Run as a subprocess by test_durability.py, which reads the announcements and
sends SIGKILL only after an explicit acknowledgement. Nothing here is timed;
the parent kills this process at a point it knows the database state of.
"""

import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dbfixtures import add_visit  # noqa: E402

from darts.db.connection import connection, transaction  # noqa: E402


def main() -> None:
    database, commits = Path(sys.argv[1]), int(sys.argv[2])
    with connection(database) as conn:
        for index in range(commits):
            with transaction(conn):
                add_visit(conn, index)
            print(f"committed {index}", flush=True)

        # Leave this one uncommitted. It is the partial write that must not
        # survive, and announcing it is what tells the parent to pull the plug.
        conn.execute("BEGIN IMMEDIATE")
        add_visit(conn, commits)
        print(f"in-flight {commits}", flush=True)
        signal.pause()


if __name__ == "__main__":
    main()
