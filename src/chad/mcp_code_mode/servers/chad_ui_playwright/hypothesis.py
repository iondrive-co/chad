from __future__ import annotations

from collections.abc import Iterable

from ...registry import DEFAULT_SERVER, call_tool


def record_hypothesis(
    description: str,
    checks: Iterable[str] | str,
    tracker_id: str | None = None,
) -> dict[str, object]:
    """Record a hypothesis with binary rejection checks via code-mode."""
    return call_tool(
        "hypothesis",
        server=DEFAULT_SERVER,
        description=description,
        checks=_normalize_checks(checks),
        tracker_id=tracker_id,
    )


def _normalize_checks(checks: Iterable[str] | str) -> str:
    if isinstance(checks, str):
        return checks
    normalized = [c.strip() for c in checks if c.strip()]
    return ",".join(normalized)
