"""UI integration tests using Playwright to verify UI behavior with mock providers."""

import time

import pytest

try:
    from playwright.sync_api import Page, expect
except Exception:  # pragma: no cover - handled by pytest skip
    pytest.skip("playwright not available", allow_module_level=True)

from chad.ui_playwright_runner import (
    ChadLaunchError,
    create_temp_env,
    start_chad,
    stop_chad,
    open_playwright_page,
    measure_provider_delete_button,
)


@pytest.fixture(scope="module")
def temp_env():
    """Create a temporary Chad environment for UI testing."""
    env = create_temp_env()
    yield env
    env.cleanup()


@pytest.fixture(scope="module")
def chad_server(temp_env):
    """Start Chad server with mock providers."""
    try:
        instance = start_chad(temp_env)
    except ChadLaunchError as exc:
        pytest.skip(f"Chad server launch failed: {exc}", allow_module_level=True)
    else:
        try:
            yield instance.port
        finally:
            stop_chad(instance)


@pytest.fixture
def page(chad_server):
    """Create a Playwright page connected to Chad."""
    with open_playwright_page(
        chad_server,
        viewport={"width": 1280, "height": 900},
    ) as page:
        yield page


class TestUIElements:
    """Test that UI elements are present and correctly configured."""

    def test_run_task_tab_visible(self, page: Page):
        """Run Task tab should be visible by default."""
        # Use role=tab to get the actual tab button
        tab = page.get_by_role("tab", name="🚀 Run Task")
        expect(tab).to_be_visible()

    def test_providers_tab_visible(self, page: Page):
        """Providers tab should be visible."""
        tab = page.get_by_role("tab", name="⚙️ Providers")
        expect(tab).to_be_visible()

    def test_project_path_field(self, page: Page):
        """Project path field should be present."""
        # Use label to find the field
        field = page.get_by_label("Project Path")
        expect(field).to_be_visible()

    def test_task_description_field(self, page: Page):
        """Task description field should be present."""
        textarea = page.locator('textarea').first
        expect(textarea).to_be_visible()

    def test_start_button_present(self, page: Page):
        """Start Task button should be present."""
        button = page.locator('#start-task-btn')
        expect(button).to_be_visible()


class TestReadyStatus:
    """Test the Ready status display with model assignments."""

    def test_ready_status_shows_model_info(self, page: Page):
        """Ready status should include model assignment info."""
        # Look for the ready status text
        status = page.locator('#role-config-status')
        expect(status).to_be_visible()

        # Should contain model assignment info
        text = status.text_content()
        assert "Ready" in text or "Missing" in text


class TestProvidersTab:
    """Test the Providers tab functionality."""

    def test_can_switch_to_providers_tab(self, page: Page):
        """Should be able to switch to Providers tab."""
        page.get_by_role("tab", name="⚙️ Providers").click()
        time.sleep(0.5)

        # Should see provider heading
        expect(page.get_by_role("heading", name="Providers")).to_be_visible()

    def test_provider_delete_button_fills_header(self, page: Page):
        """Delete button should fill the header height."""
        measurement = measure_provider_delete_button(page)
        assert measurement["ratio"] >= 0.95, f"Expected ratio >= 0.95, got {measurement['ratio']}"


class TestSubtaskTabs:
    """Test subtask tab filtering (integration with mock provider)."""

    def test_subtask_tabs_hidden_initially(self, page: Page):
        """Subtask tabs should be hidden before a task starts."""
        tabs = page.locator('#subtask-tabs')
        # Should either not exist or be hidden
        if tabs.count() > 0:
            expect(tabs).to_be_hidden()


class TestLiveActivityFormat:
    """Test that live activity uses Claude Code format."""

    def test_live_stream_box_exists(self, page: Page):
        """Live stream box should exist (may be hidden when empty)."""
        box = page.locator('#live-stream-box')
        # Box exists but may be hidden when empty - check it exists in DOM
        assert box.count() > 0, "live-stream-box should exist in DOM"


class TestNoStatusBox:
    """Verify status box has been removed."""

    def test_no_status_box(self, page: Page):
        """Status box should not exist in the DOM."""
        status_box = page.locator('#status-box')
        assert status_box.count() == 0, "status_box should be completely removed"


class TestTaskStatusHeader:
    """Test task status header component."""

    def test_task_status_header_hidden_initially(self, page: Page):
        """Task status header should be hidden before task starts."""
        header = page.locator('#task-status-header')
        # Should either not exist or be hidden
        if header.count() > 0:
            expect(header).to_be_hidden()


# Screenshot tests for visual verification
class TestScreenshots:
    """Take screenshots for visual verification."""

    def test_screenshot_run_task_tab(self, page: Page, tmp_path):
        """Take screenshot of Run Task tab."""
        output = tmp_path / "run_task.png"
        page.screenshot(path=str(output))
        assert output.exists()
        print(f"Screenshot saved: {output}")

    def test_screenshot_providers_tab(self, page: Page, tmp_path):
        """Take screenshot of Providers tab."""
        page.get_by_role("tab", name="⚙️ Providers").click()
        time.sleep(0.5)
        output = tmp_path / "providers.png"
        page.screenshot(path=str(output))
        assert output.exists()
        print(f"Screenshot saved: {output}")
