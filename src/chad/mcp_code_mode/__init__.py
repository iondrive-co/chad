"""Code-mode wrappers for MCP servers to keep tool usage out of the prompt context."""

from .registry import DEFAULT_SERVER, call_tool, list_servers, list_tools
from .servers import chad_ui_playwright

__all__ = ["DEFAULT_SERVER", "call_tool", "list_servers", "list_tools", "chad_ui_playwright"]
