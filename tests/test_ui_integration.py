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
    delete_provider_by_name,
    get_card_visibility_debug,
    get_provider_names,
    measure_provider_delete_button,
    open_playwright_page,
    start_chad,
    stop_chad,
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

    def test_cancel_button_disabled_initially(self, page: Page):
        """Cancel button should be disabled before task starts."""
        # The cancel button should exist but not be interactive/enabled
        cancel_btn = page.locator('#cancel-task-btn')
        expect(cancel_btn).to_be_visible()
        # Check that button is disabled (has disabled attribute or class)
        is_disabled = page.evaluate(
            """
            () => {
              const btn = document.querySelector('#cancel-task-btn');
              if (!btn) return true;
              // Check various ways Gradio might disable a button
              return btn.disabled ||
                     btn.classList.contains('disabled') ||
                     btn.getAttribute('aria-disabled') === 'true' ||
                     btn.hasAttribute('disabled');
            }
            """
        )
        assert is_disabled, "Cancel button should be disabled before task starts"


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


class TestDeleteProvider:
    """Test delete provider functionality.

    Note: These tests share a server, so each test uses a different provider
    to avoid interference between tests.
    """

    def test_mock_providers_exist(self, page: Page):
        """Mock providers should be present before any deletion tests."""
        providers = get_provider_names(page)
        # At least one mock provider should exist
        assert len(providers) > 0, f"Expected at least one provider, got {providers}"

    def test_delete_provider_two_step_flow(self, page: Page):
        """Clicking delete should show confirm icon and second click should delete.

        This is the key test - it verifies the bug is fixed.
        The bug was that clicking OK on the JS confirmation dialog
        did not actually delete the provider because Gradio's fn=None
        doesn't route JS return values to state components.

        The fix uses a two-step flow: first click shows confirm icon,
        second click actually deletes.
        """
        # Get available providers before deletion
        providers_before = get_provider_names(page)
        assert len(providers_before) > 0, "Need at least one provider to test deletion"

        # Pick the first provider to delete
        provider_to_delete = providers_before[0]
        other_providers = [p for p in providers_before if p != provider_to_delete]

        # Delete the provider
        result = delete_provider_by_name(page, provider_to_delete)

        # Verify the two-step flow worked
        assert result.existed_before, f"Provider '{provider_to_delete}' should exist before deletion"
        assert result.confirm_button_appeared, (
            f"Confirm button should appear after first click. "
            f"feedback='{result.feedback_message}'"
        )
        assert result.confirm_clicked, "Confirm button should be clickable"

        # This is the critical assertion - the provider should be gone
        assert result.deleted, (
            f"Provider should be deleted after confirming. "
            f"existed_before={result.existed_before}, "
            f"exists_after={result.exists_after}, "
            f"confirm_button_appeared={result.confirm_button_appeared}, "
            f"confirm_clicked={result.confirm_clicked}, "
            f"feedback='{result.feedback_message}'"
        )
        assert not result.exists_after, f"Provider '{provider_to_delete}' should not exist after deletion"

        # Verify remaining providers are still visible and correct
        providers_after = get_provider_names(page)
        for other in other_providers:
            assert other in providers_after, (
                f"Other provider '{other}' should still exist after deleting '{provider_to_delete}'. "
                f"Before: {providers_before}, After: {providers_after}"
            )

    def test_deleted_card_container_is_hidden(self, page: Page):
        """Card container should be hidden after provider deletion, not just header blanked.

        This verifies the UI actually hides the card's dropdowns and controls,
        not just the header text.
        """
        # Get card visibility before any deletion
        cards_before = get_card_visibility_debug(page)
        visible_cards_before = [c for c in cards_before if c['hasHeaderSpan']]

        if len(visible_cards_before) < 1:
            pytest.skip("No visible provider cards to test deletion")

        # Pick a provider to delete
        providers = get_provider_names(page)
        if not providers:
            pytest.skip("No providers to test deletion")
        provider_to_delete = providers[0]

        # Delete the provider
        delete_provider_by_name(page, provider_to_delete)

        # Check card visibility after deletion
        cards_after = get_card_visibility_debug(page)

        # Count visible vs empty cards
        visible_cards_after = [c for c in cards_after if c['hasHeaderSpan']]
        empty_cards_after = [c for c in cards_after if not c['hasHeaderSpan']]

        # Verify there's one less visible card
        assert len(visible_cards_after) == len(visible_cards_before) - 1, (
            f"Should have one less visible card after deletion. "
            f"Before: {len(visible_cards_before)}, After: {len(visible_cards_after)}"
        )

        # Verify empty cards are actually hidden (display: none)
        for empty_card in empty_cards_after:
            assert empty_card['cardDisplay'] == 'none' or empty_card['columnDisplay'] == 'none', (
                f"Empty card should be hidden but has cardDisplay={empty_card['cardDisplay']}, "
                f"columnDisplay={empty_card['columnDisplay']}. Card: {empty_card}"
            )


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
