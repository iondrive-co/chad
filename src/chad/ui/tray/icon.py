"""The Chad tray icon, drawn in pure Python.

A tray icon has to exist before any UI toolkit does, so it is rendered here
from arithmetic rather than loaded from an image library: a 32x32 accent
coloured "C" ring, supersampled for smooth edges, emitted as PNG (macOS),
ARGB32 (the D-Bus StatusNotifierItem spec) or ICO (Windows).
"""

from __future__ import annotations

import struct
import zlib

SIZE = 32

# --accent from the React UI's dark theme; it reads on light and dark trays.
ACCENT = (0xE9, 0x45, 0x60)

_CENTRE = SIZE / 2.0
_OUTER = 13.5
_INNER = 8.0
_GAP_HALF_HEIGHT = 4.2  # half-height of the mouth cut out of the ring's right
_SUBSAMPLES = 4


def _covers(x: float, y: float) -> bool:
    """True when this point is inside the C."""
    dx = x - _CENTRE
    dy = y - _CENTRE
    distance = (dx * dx + dy * dy) ** 0.5
    if not (_INNER <= distance <= _OUTER):
        return False
    # Cut the mouth: everything to the right of centre within the gap band.
    return not (dx > 0 and abs(dy) < _GAP_HALF_HEIGHT)


def _alpha(px: int, py: int) -> int:
    """Coverage of one pixel, sampled on a _SUBSAMPLES x _SUBSAMPLES grid."""
    hits = 0
    step = 1.0 / _SUBSAMPLES
    for sy in range(_SUBSAMPLES):
        for sx in range(_SUBSAMPLES):
            if _covers(px + (sx + 0.5) * step, py + (sy + 0.5) * step):
                hits += 1
    return round(255 * hits / (_SUBSAMPLES * _SUBSAMPLES))


def rgba() -> list[list[tuple[int, int, int, int]]]:
    """The icon as rows of (r, g, b, a) pixels, top row first."""
    rows = []
    for y in range(SIZE):
        row = []
        for x in range(SIZE):
            alpha = _alpha(x, y)
            row.append((*ACCENT, alpha) if alpha else (0, 0, 0, 0))
        rows.append(row)
    return rows


def render() -> bytes:
    """The icon as a PNG."""
    raw = bytearray()
    for row in rgba():
        raw.append(0)  # filter type: none
        for r, g, b, a in row:
            raw += bytes((r, g, b, a))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def argb_for_dbus() -> tuple[int, int, bytes]:
    """(width, height, ARGB32) as org.kde.StatusNotifierItem wants it."""
    out = bytearray()
    for row in rgba():
        for r, g, b, a in row:
            out += bytes((a, r, g, b))
    return SIZE, SIZE, bytes(out)


def ico() -> bytes:
    """The icon as a Windows .ico holding one 32-bit BMP image.

    A BMP-in-ICO (rather than PNG-in-ICO) is used because every Windows
    version reads it, and the AND mask is written even though the alpha
    channel makes it redundant — LoadImage rejects an icon without one.
    """
    pixels = rgba()
    xor = bytearray()
    for row in reversed(pixels):  # BMP rows run bottom-up
        for r, g, b, a in row:
            xor += bytes((b, g, r, a))

    mask_row = bytes(SIZE // 8)  # all zero: nothing masked out
    and_mask = mask_row * SIZE

    header = struct.pack(
        "<IiiHHIIiiII",
        40,             # biSize
        SIZE,           # biWidth
        SIZE * 2,       # biHeight: XOR image plus AND mask
        1,              # biPlanes
        32,             # biBitCount
        0,              # biCompression: BI_RGB
        len(xor) + len(and_mask),
        0, 0, 0, 0,
    )
    image = header + bytes(xor) + and_mask

    directory = struct.pack(
        "<BBBBHHII",
        SIZE, SIZE, 0, 0, 1, 32, len(image), 6 + 16,
    )
    return struct.pack("<HHH", 0, 1, 1) + directory + image
