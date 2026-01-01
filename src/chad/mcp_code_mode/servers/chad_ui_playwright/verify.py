from __future__ import annotations

from ...registry import DEFAULT_SERVER, call_tool


def verify() -> dict[str, object]:
    """Run lint + all tests through the chad-ui-playwright MCP server via code-mode."""
    return call_tool("verify", server=DEFAULT_SERVER)
