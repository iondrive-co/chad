from __future__ import annotations

from ...registry import DEFAULT_SERVER, call_tool


def file_check_result(
    tracker_id: str,
    hypothesis_id: int,
    check_index: int,
    passed: bool,
    notes: str = "",
) -> dict[str, object]:
    """File a binary check result using the MCP tool via code execution."""
    return call_tool(
        "check_result",
        server=DEFAULT_SERVER,
        tracker_id=tracker_id,
        hypothesis_id=hypothesis_id,
        check_index=check_index,
        passed=passed,
        notes=notes,
    )
