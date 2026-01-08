from __future__ import annotations

"""Route code-executed tool calls without loading schemas into the model context."""

from typing import Any

from chad import tools

DEFAULT_SERVER = "chad-ui-playwright"

# Maps server -> tool name -> tools module function attribute
_SERVER_TOOL_MAP: dict[str, dict[str, str]] = {
    DEFAULT_SERVER: {
        "verify": "verify",
        "screenshot": "screenshot",
    },
}


def list_servers() -> list[str]:
    """List available tool servers exposed via code-mode wrappers."""
    return list(_SERVER_TOOL_MAP.keys())


def list_tools(server: str = DEFAULT_SERVER) -> list[str]:
    """List tools for a given server."""
    server_tools = _SERVER_TOOL_MAP.get(server)
    if server_tools is None:
        raise ValueError(f"Unknown server '{server}'")
    return sorted(server_tools.keys())


def call_tool(tool: str, *, server: str = DEFAULT_SERVER, **kwargs: Any) -> dict[str, object]:
    """Call a tool via code execution instead of direct tool invocation."""
    server_tools = _SERVER_TOOL_MAP.get(server)
    if server_tools is None:
        raise ValueError(f"Unknown server '{server}'")

    attr = server_tools.get(tool)
    if attr is None:
        raise ValueError(f"Unknown tool '{tool}' for server '{server}'")

    fn = getattr(tools, attr)
    return fn(**kwargs)
