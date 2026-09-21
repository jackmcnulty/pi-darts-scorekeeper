"""The `darts-gen-checkouts` console script.

Regenerates `darts/engine/checkout_table.py` from the exhaustive search in
`darts.engine.checkout_gen`, or with `--check` verifies that the committed file
is what the generator would produce right now. CI runs the `--check` form, so
the table can never drift from the code that built it.
"""

import argparse
import sys
from pathlib import Path

from darts.engine.checkout_gen import generate_table, render_module

TABLE_PATH = Path(__file__).resolve().parents[1] / "engine" / "checkout_table.py"

_STALE_MESSAGE = (
    "checkout_table.py does not match the generator.\n"
    "Regenerate it with:  uv run darts-gen-checkouts"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="darts-gen-checkouts", description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed table is up to date instead of rewriting it",
    )
    args = parser.parse_args(argv)

    source = render_module(generate_table())

    if args.check:
        committed = TABLE_PATH.read_text(encoding="utf-8") if TABLE_PATH.exists() else ""
        if committed != source:
            print(_STALE_MESSAGE, file=sys.stderr)
            return 1
        print(f"{TABLE_PATH.name} is up to date")
        return 0

    TABLE_PATH.write_text(source, encoding="utf-8")
    print(f"wrote {TABLE_PATH} ({len(source.splitlines())} lines)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
