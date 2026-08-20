"""Visual regression: new sessions must pick their project explicitly, and an
unsent composer draft must survive switching session tabs.

Before the fix:
- The header's "New" button always created the session against whichever
  project was configured first, silently ignoring any other configured
  project — every session tab (and the grouping header above it) ended up
  stuck under that one project no matter what.
- ChatView remounts on every session tab switch (`key={selectedSession}` in
  App.tsx), which dropped any typed-but-unsent message (and composer
  settings) the moment you switched to another tab and back.

The fix replaces the "New" button with a project picker that requires an
explicit choice (so the created session's project, and therefore its tab
grouping, is always correct) and persists unsent composer state per session
across the remount.
"""

from __future__ import annotations

import pytest
import requests

from chad.util.verification.ui_runner import (
    ChadLaunchError,
    PlaywrightUnavailable,
    create_temp_env,
    open_playwright_page,
    start_chad,
    stop_chad,
)

pytestmark = pytest.mark.visual


def _add_project(port: int, path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    resp = requests.put(
        f"http://127.0.0.1:{port}/api/v1/config/project",
        json={"project_path": str(path)},
        timeout=10,
    )
    resp.raise_for_status()


def _run():
    env = create_temp_env(screenshot_mode=False)
    instance = start_chad(env)
    try:
        second_project = env.temp_dir / "project-b"
        _add_project(instance.port, second_project)

        with open_playwright_page(instance.port, headless=True) as page:
            page.wait_for_selector(".chad-btn.connected", timeout=15000)

            # The picker offers every configured project, not just the first
            # one, and forces an explicit choice rather than pre-selecting one.
            placeholder_disabled = page.eval_on_selector(
                ".new-session-select", "el => el.options[0].disabled"
            )
            option_values = page.eval_on_selector_all(
                ".new-session-select option", "els => els.map(e => e.value)"
            )

            # Create a session explicitly for the second project.
            page.select_option(".new-session-select", str(second_project))
            page.wait_for_selector(".session-tab", timeout=10000)
            group_header = page.inner_text(".session-group-header").strip()

            # ChatView no longer offers a way to change the session's project.
            project_dropdown_count = page.locator(".project-selector-bar select").count()

            # Type a draft message but don't send it.
            page.fill("textarea", "unsent draft message")

            # Create a second session for the first project — this remounts
            # ChatView for a different session, which used to drop the draft.
            page.select_option(".new-session-select", str(env.project_dir))
            page.wait_for_timeout(500)

            # Switch back to the project-b session tab.
            page.locator(".session-group", has_text="project-b").locator(".session-tab").click()
            page.wait_for_timeout(300)
            restored_draft = page.input_value("textarea")

            return {
                "placeholder_disabled": placeholder_disabled,
                "option_values": option_values,
                "second_project": str(second_project),
                "group_header": group_header,
                "project_dropdown_count": project_dropdown_count,
                "restored_draft": restored_draft,
            }
    finally:
        stop_chad(instance)
        env.cleanup()


def test_new_session_project_picker_and_draft_persistence():
    try:
        result = _run()
    except (PlaywrightUnavailable, ChadLaunchError) as exc:
        pytest.skip(f"UI runner unavailable: {exc}")

    assert result["placeholder_disabled"], (
        "the new-session control's placeholder option must be disabled, "
        "forcing an explicit project choice"
    )
    assert result["second_project"] in result["option_values"], (
        "the new-session picker must offer every configured project, "
        f"got: {result['option_values']!r}"
    )
    # Grouped under the project it was actually created for — not silently
    # defaulted to the first configured project.
    assert result["group_header"] == "project-b", (
        f"session tab grouped under the wrong project: {result['group_header']!r}"
    )
    assert result["project_dropdown_count"] == 0, (
        "ChatView must not offer a dropdown to change a session's project after creation"
    )
    assert result["restored_draft"] == "unsent draft message", (
        "an unsent composer draft was lost after switching session tabs and back: "
        f"got {result['restored_draft']!r}"
    )
