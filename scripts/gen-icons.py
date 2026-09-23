#!/usr/bin/env python3
"""Draw the home-screen icons, deterministically, with nothing installed.

#21 needs three rasters that no SVG in this repository could supply: maskable
PNGs at 192 and 512, and a 180px `apple-touch-icon`. iOS will not take an SVG
for the last of those, and a maskable icon has geometry requirements -- its
art has to survive being cropped to a circle -- that are a property of the
drawing, not of the format.

Committed PNGs rather than a build step, so `npm ci && npm run build` stays
free of a rasteriser and the bytes on the Pi are the bytes in git. This script
is how they were made and the only way they should be remade: it writes PNGs
straight from `zlib` and `struct`, so re-running it on any machine with a
Python 3.11 and no third-party packages reproduces them byte for byte.

    python3 scripts/gen-icons.py

The mark is a dartboard reduced until it still reads at 48 pixels: a field,
a triple ring, a double ring, and a bull. Colours are #4's tokens, so the icon
cannot drift from the app it launches. It is deliberately plain -- if a real
one is ever drawn, replace the constants here and re-run.
"""

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "frontend" / "public"

#: Straight from frontend/src/styles/tokens.css.
BACKGROUND = (0x0B, 0x0E, 0x13)  # --color-bg
FIELD = (0x15, 0x1A, 0x22)  # --color-surface
WIRE = (0x4D, 0xA3, 0xFF)  # --color-primary
BULL = (0xFF, 0x72, 0x72)  # --color-danger
OUTER_BULL = (0xFF, 0xFF, 0xFF)  # --color-text

#: Rings as (outer radius, inner radius, colour), largest first, in fractions
#: of the icon's width. Everything stays inside 0.4, which keeps the whole
#: mark within the central 80% a maskable icon may be cropped to -- Android
#: crops to a circle of exactly that size, and anything outside it is gone.
RINGS = (
    (0.400, 0.330, FIELD),
    (0.330, 0.285, WIRE),
    (0.285, 0.175, FIELD),
    (0.175, 0.130, WIRE),
    (0.130, 0.075, FIELD),
    (0.075, 0.038, OUTER_BULL),
    (0.038, 0.000, BULL),
)

#: Each pixel is averaged over SUPERSAMPLE**2 samples. The circles are the
#: whole design, so an aliased edge is the whole design looking broken.
SUPERSAMPLE = 4


def colour_at(x: float, y: float) -> tuple[int, int, int]:
    """The colour at a point, in fractions of the icon's width from centre."""
    distance = (x * x + y * y) ** 0.5
    for outer, inner, colour in RINGS:
        if inner <= distance < outer:
            return colour
    return BACKGROUND


def render(size: int) -> bytes:
    """Raw RGB rows, supersampled and box-filtered down."""
    rows = bytearray()
    step = 1.0 / (size * SUPERSAMPLE)
    samples = SUPERSAMPLE * SUPERSAMPLE

    for row in range(size):
        rows.append(0)  # PNG filter type 0 (None) for this scanline
        for column in range(size):
            totals = [0, 0, 0]
            for sub_y in range(SUPERSAMPLE):
                y = (row * SUPERSAMPLE + sub_y + 0.5) * step - 0.5
                for sub_x in range(SUPERSAMPLE):
                    x = (column * SUPERSAMPLE + sub_x + 0.5) * step - 0.5
                    red, green, blue = colour_at(x, y)
                    totals[0] += red
                    totals[1] += green
                    totals[2] += blue
            rows.extend(total // samples for total in totals)
    return bytes(rows)


def chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def png(size: int) -> bytes:
    """A minimal 8-bit truecolour PNG. No alpha: a maskable icon is opaque."""
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    # Level 9 and a fixed strategy so the compressed bytes are reproducible.
    body = zlib.compress(render(size), 9)
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", body) + chunk(b"IEND", b"")
    )


def main() -> None:
    for name, size in (("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180)):
        path = PUBLIC / name
        path.write_bytes(png(size))
        print(f"wrote {path.relative_to(ROOT)} ({size}x{size}, {path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
