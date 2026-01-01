from __future__ import annotations

from ...registry import DEFAULT_SERVER, call_tool


def report(tracker_id: str, screenshot_before: str = "", screenshot_after: str = "") -> dict[str, object]:
    """Get the final hypothesis report without direct tool calls."""
    return call_tool(
        "report",
        server=DEFAULT_SERVER,
        tracker_id=tracker_id,
        screenshot_before=screenshot_before,
        screenshot_after=screenshot_after,
    )
