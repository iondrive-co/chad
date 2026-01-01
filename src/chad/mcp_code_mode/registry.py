from __future__ import annotations

"""Route code-executed MCP calls without loading tool schemas into the model context."""

from typing import Any

from chad import mcp_playwright

DEFAULT_SERVER = "chad-ui-playwright"

# Maps server -> tool name -> mcp_playwright function attribute
_SERVER_TOOL_MAP: dict[str, dict[str, str]] = {
    DEFAULT_SERVER: {
        "verify": "verify",
        "screenshot": "screenshot",
        "hypothesis": "hypothesis",
        "check_result": "check_result",
        "report": "report",
    },
}


def list_servers() -> list[str]:
    """List available MCP servers exposed via code-mode wrappers."""
    return list(_SERVER_TOOL_MAP.keys())


def list_tools(server: str = DEFAULT_SERVER) -> list[str]:
    """List tools for a given MCP server."""
    tools = _SERVER_TOOL_MAP.get(server)
    if tools is None:
        raise ValueError(f"Unknown MCP server '{server}'")
    return sorted(tools.keys())


def call_tool(tool: str, *, server: str = DEFAULT_SERVER, **kwargs: Any) -> dict[str, object]:
    """Call an MCP tool via code execution instead of direct tool invocation."""
    tools = _SERVER_TOOL_MAP.get(server)
    if tools is None:
        raise ValueError(f"Unknown MCP server '{server}'")

    attr = tools.get(tool)
    if attr is None:
        raise ValueError(f"Unknown MCP tool '{tool}' for server '{server}'")

    fn = getattr(mcp_playwright, attr)
    return fn(**kwargs)
