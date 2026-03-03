"""QR code generation for tunnel pairing."""

import io
from pathlib import Path

import segno


def print_pairing_qr(url: str) -> None:
    """Print a QR code to the terminal using Unicode half-block characters."""
    qr = segno.make(url)
    buf = io.StringIO()
    qr.terminal(out=buf, compact=True)
    print(buf.getvalue())


def save_pairing_qr(url: str, path: Path) -> None:
    """Save a QR code as a PNG file."""
    qr = segno.make(url)
    qr.save(str(path), scale=8, border=2)
