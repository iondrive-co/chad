from __future__ import annotations

from ...registry import DEFAULT_SERVER, call_tool


def screenshot(tab: str = "run", component: str = "", label: str = "") -> dict[str, object]:
    """Capture a UI screenshot without exposing tool schemas to the model."""
    return call_tool("screenshot", server=DEFAULT_SERVER, tab=tab, component=component, label=label)
