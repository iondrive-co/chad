from __future__ import annotations

from chad.tools import screenshot as _screenshot


def screenshot(tab: str = "run", component: str = "", label: str = "") -> dict[str, object]:
    """Capture a UI screenshot."""
    return _screenshot(tab=tab, component=component, label=label)
