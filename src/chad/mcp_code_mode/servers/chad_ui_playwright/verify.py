from __future__ import annotations

from chad.tools import verify as _verify


def verify() -> dict[str, object]:
    """Run lint + all tests to verify no regressions."""
    return _verify()
