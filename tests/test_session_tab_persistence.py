"""Visual regression: open session tabs survive a browser refresh.

A session tab (including an unsaved "WIP" session that exists on the server but
has no started task yet) lives only in in-memory React state. Before the fix it
was dropped on a browser reload because nothing persisted which tabs were open.
The fix persists the opened-session ids (and the selection / active tab) to
localStorage and restores them on mount, reconciling against the server list.

This drives the real React app: it creates a session via the "New" button,
reloads the page, and asserts the tab is still there. It would fail before the
fix (the tab vanishes on reload) and passes after.
"""

from __future__ import annotations

import pytest

from chad.util.verification.ui_runner import (
    ChadLaunchError,
    PlaywrightUnavailable,
    create_temp_env,
    open_playwright_page,
    start_chad,
    stop_chad,
)

pytestmark = pytest.mark.visual


def _run() -> tuple[str, str, str | None]:
    """Create a WIP tab, reload, and return (name_before, name_after, storage)."""
    env = create_temp_env(screenshot_mode=False)
    instance = start_chad(env)
    try:
        with open_playwright_page(instance.port, headless=True) as page:
            # Wait until connected (the Chad button gets .connected once /status responds).
            page.wait_for_selector(".chad-btn.connected", timeout=15000)

            # Create a new (WIP) session for the only configured project — it
            # appears as a tab in the header. Project selection happens via
            # this dropdown (index 0 is the disabled "+ New" placeholder).
            page.select_option(".new-session-select", index=1)
            page.wait_for_selector(".session-tab", timeout=10000)
            name_before = page.inner_text(".session-tab .session-tab-name").strip()
            storage = page.evaluate(
                "() => localStorage.getItem('chad.openedSessionIds')"
            )

            # Reload the browser — this is what previously dropped the WIP tab.
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector(".chad-btn.connected", timeout=15000)

            # The tab must still be present after the reload.
            page.wait_for_selector(".session-tab", timeout=10000)
            name_after = page.inner_text(".session-tab .session-tab-name").strip()
            return name_before, name_after, storage
    finally:
        stop_chad(instance)
        env.cleanup()


def test_session_tab_survives_browser_refresh():
    try:
        name_before, name_after, storage = _run()
    except (PlaywrightUnavailable, ChadLaunchError) as exc:
        pytest.skip(f"UI runner unavailable: {exc}")

    assert name_before, "expected a session tab to be created"
    # The opened-session id is persisted so it can be restored after reload.
    assert storage and name_before in storage, (
        f"opened session not persisted to localStorage; got: {storage!r}"
    )
    # The same tab is still present after the browser refresh.
    assert name_after == name_before, (
        f"WIP tab was lost or changed on refresh: {name_before!r} -> {name_after!r}"
    )
