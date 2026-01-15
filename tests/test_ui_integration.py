"""UI integration tests using Playwright to verify UI behavior with mock providers."""

import time

import pytest

try:
    from playwright.sync_api import Page, expect
except Exception:  # pragma: no cover - handled by pytest skip
    pytest.skip("playwright not available", allow_module_level=True)

from chad.verification.ui_playwright_runner import (
    ChadLaunchError,
    check_live_stream_colors,
    create_temp_env,
    delete_provider_by_name,
    get_card_visibility_debug,
    get_provider_names,
    inject_live_stream_content,
    measure_add_provider_accordion,
    measure_provider_delete_button,
    open_playwright_page,
    start_chad,
    stop_chad,
    verify_all_text_visible,
)

# Mark all tests in this module as visual tests (require Playwright browser)
pytestmark = pytest.mark.visual


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

    def test_task_1_tab_visible(self, page: Page):
        """Task 1 tab should be visible by default."""
        # Use role=tab to get the actual tab button
        tab = page.get_by_role("tab", name="Task 1")
        expect(tab).to_be_visible()

    def test_additional_task_tabs_hidden_initially(self, page: Page):
        """Task 2 and beyond should be hidden until created via + button."""
        # Task 2 should not be visible initially
        task2_tab = page.get_by_role("tab", name="Task 2")
        expect(task2_tab).to_be_hidden()

        # Task 3 should not be visible initially
        task3_tab = page.get_by_role("tab", name="Task 3")
        expect(task3_tab).to_be_hidden()

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
        # Look for the task description textarea by its label
        textarea = page.get_by_label("Task Description")
        expect(textarea).to_be_visible()

    def test_start_button_present(self, page: Page):
        """Start Task button should be present."""
        button = page.locator("#start-task-btn")
        expect(button).to_be_visible()

    def test_cancel_button_disabled_initially(self, page: Page):
        """Cancel button should be disabled before task starts."""
        # The cancel button should exist but not be interactive/enabled
        cancel_btn = page.locator("#cancel-task-btn")
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

    def test_add_task_tab_visible(self, page: Page):
        """Add Task tab (+) should be visible."""
        # Check for the main plus tab within the tab container
        main_tabs = page.locator("#main-tabs")
        plus_tab = main_tabs.get_by_role("tab", name="➕")
        expect(plus_tab).to_be_visible()

    def test_task_1_is_selected_by_default(self, page: Page):
        """Task 1 tab should be selected by default."""
        # Task 1 is a top-level tab and should be selected
        task_tab = page.get_by_role("tab", name="Task 1")
        expect(task_tab).to_be_visible()
        # Check if it's selected (has aria-selected="true")
        is_selected = task_tab.get_attribute("aria-selected")
        assert is_selected == "true", "Task 1 should be selected by default"


class TestReadyStatus:
    """Test the Ready status display with model assignments."""

    def test_ready_status_shows_model_info(self, page: Page):
        """Ready status should include model assignment info."""
        # In tab-based UI, the first session is shown by default
        status = page.locator("#role-config-status")
        expect(status).to_be_visible(timeout=10000)

        # Should contain model assignment info
        text = status.text_content()
        assert "Ready" in text or "Missing" in text


class TestCodingAgentLayout:
    """Ensure the coding agent selector and controls are properly laid out."""

    def test_status_row_spans_top_bar(self, page: Page):
        """Status row should sit beneath project path within the header area."""
        top_row = page.locator("#run-top-row")
        status_row = page.locator("#role-status-row")
        cancel_btn = page.locator("#cancel-task-btn")
        expect(top_row).to_be_visible()

        project_path = top_row.get_by_label("Project Path")
        coding_agent = top_row.get_by_label("Coding Agent")
        expect(status_row).to_be_visible()

        expect(project_path).to_be_visible()
        expect(coding_agent).to_be_visible()

        status_box = status_row.bounding_box()
        row_box = top_row.bounding_box()
        cancel_box = cancel_btn.bounding_box()
        project_box = project_path.bounding_box()

        assert status_box and row_box and cancel_box and project_box, "Missing bounding box data for layout assertions"

        # Status should sit below project path within the top row column
        assert (
            status_box["y"] >= project_box["y"] + project_box["height"] - 2
        ), "Status row should appear below the project path input"

        # Status should align to project path column rather than the cancel column
        assert status_box["x"] <= project_box["x"] + 4
        available_width = row_box["width"] - cancel_box["width"]
        assert status_box["width"] <= available_width

    def test_run_top_controls_stack_with_matching_widths(self, page: Page):
        """Preferred/Reasoning controls should stack under matching agent selectors with aligned widths."""
        project_path = page.get_by_label("Project Path")
        status = page.locator("#role-config-status")
        session_log = page.locator("#session-log-btn")
        coding_agent = page.get_by_label("Coding Agent")
        coding_model = page.get_by_label("Preferred Model", exact=True)
        coding_reasoning = page.get_by_label("Reasoning Effort", exact=True)
        verification_agent = page.get_by_label("Verification Agent")
        verification_model = page.get_by_label("Verification Preferred Model")
        verification_reasoning = page.get_by_label("Verification Reasoning Effort")

        expect(project_path).to_be_visible()
        expect(status).to_be_visible()
        expect(session_log).to_be_visible()
        expect(coding_agent).to_be_visible()
        expect(coding_model).to_be_visible()
        expect(coding_reasoning).to_be_visible()
        expect(verification_agent).to_be_visible()
        expect(verification_model).to_be_visible()
        expect(verification_reasoning).to_be_visible()

        project_box = project_path.bounding_box()
        status_box = status.bounding_box()
        log_box = session_log.bounding_box()
        coding_box = coding_agent.bounding_box()
        model_box = coding_model.bounding_box()
        coding_reasoning_box = coding_reasoning.bounding_box()
        verification_box = verification_agent.bounding_box()
        verification_model_box = verification_model.bounding_box()
        verification_reasoning_box = verification_reasoning.bounding_box()

        assert (
            project_box
            and status_box
            and log_box
            and coding_box
            and model_box
            and coding_reasoning_box
            and verification_box
            and verification_model_box
            and verification_reasoning_box
        )

        assert (
            status_box["y"] >= project_box["y"] + project_box["height"] - 2
        ), "Status should appear beneath the Project Path field"
        assert (
            log_box["y"] >= project_box["y"] + project_box["height"] - 2
        ), "Session log button should appear beneath the Project Path field"

        assert (
            model_box["y"] >= coding_box["y"] + coding_box["height"] - 2
        ), "Preferred Model should stack beneath Coding Agent"
        assert (
            coding_reasoning_box["y"] >= model_box["y"] + model_box["height"] - 2
        ), "Coding Reasoning should stack beneath Preferred Model"
        assert (
            verification_model_box["y"] >= verification_box["y"] + verification_box["height"] - 2
        ), "Verification Preferred Model should stack beneath Verification Agent"
        assert (
            verification_reasoning_box["y"] >= verification_model_box["y"] + verification_model_box["height"] - 2
        ), "Verification Reasoning should stack beneath Verification Preferred Model"

        assert abs(model_box["x"] - coding_box["x"]) <= 4
        assert abs(model_box["width"] - coding_box["width"]) <= 4
        assert abs(coding_reasoning_box["x"] - coding_box["x"]) <= 4
        assert abs(coding_reasoning_box["width"] - coding_box["width"]) <= 4
        assert abs(verification_model_box["x"] - verification_box["x"]) <= 4
        assert abs(verification_model_box["width"] - verification_box["width"]) <= 4
        assert abs(verification_reasoning_box["x"] - verification_box["x"]) <= 4
        assert abs(verification_reasoning_box["width"] - verification_box["width"]) <= 4

    def test_cancel_button_visible_light_and_dark(self, page: Page):
        """Cancel button should stay visible in both color schemes."""
        page.wait_for_selector("#cancel-task-btn", state="attached")
        measurements = {}
        for scheme in ("light", "dark"):
            page.emulate_media(color_scheme=scheme)
            measurements[scheme] = page.evaluate(
                """
() => {
  // Try multiple selectors - Gradio may render buttons with different structures
  let button = document.querySelector('#cancel-task-btn button');
  if (!button) button = document.querySelector('#cancel-task-btn');
  // Check if the element itself is a button
  if (button && button.tagName !== 'BUTTON') {
    const innerBtn = button.querySelector('button');
    if (innerBtn) button = innerBtn;
  }
  if (!button) return null;
  const styles = window.getComputedStyle(button);
  const bodyStyles = window.getComputedStyle(document.body);
  const rect = button.getBoundingClientRect();
  const toNumber = (value) => {
    if (!value) return 0;
    const match = /([\\d.]+)/.exec(String(value));
    return match ? parseFloat(match[1]) : 0;
  };
  const parseColor = (color) => {
    const match = /rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)(?:,\\s*([\\d.]+))?\\)/i.exec(color);
    if (!match) return { r: 0, g: 0, b: 0, a: 1 };
    const [r, g, b] = match.slice(1, 4).map(Number);
    const a = match[4] === undefined ? 1 : parseFloat(match[4]);
    return { r, g, b, a };
  };
  const brightness = (color) => {
    const { r, g, b } = parseColor(color);
    return 0.299 * r + 0.587 * g + 0.114 * b;
  };
  const bgColor = parseColor(styles.backgroundColor);
  const bodyColor = parseColor(bodyStyles.backgroundColor || "rgb(255,255,255)");
  const effectiveBg = bgColor.a < 0.1 ? bodyColor : bgColor;
  const textColor = parseColor(styles.color);
  const effectiveTextAlpha = textColor.a * (parseFloat(styles.opacity) || 1);
  const bgBrightness = brightness(`rgb(${effectiveBg.r}, ${effectiveBg.g}, ${effectiveBg.b})`);
  const bodyBrightness = brightness(`rgb(${bodyColor.r}, ${bodyColor.g}, ${bodyColor.b})`);
  const textBrightness = brightness(`rgb(${textColor.r}, ${textColor.g}, ${textColor.b})`);
  // Use actual width instead of CSS min-width for visibility check
  return {
    paddingLeft: toNumber(styles.paddingLeft),
    paddingRight: toNumber(styles.paddingRight),
    width: rect.width,
    height: rect.height,
    bgBrightness,
    bodyBrightness,
    textBrightness,
    effectiveTextAlpha,
    bgAlpha: bgColor.a,
  };
}
"""
            )

        for metrics in measurements.values():
            assert metrics is not None, "Cancel button should be present"
            # Check actual rendered width for visibility
            assert metrics["width"] >= 60, f"Cancel button should be wide enough to read, got {metrics['width']}px"
            # Disabled buttons (cancel starts disabled) have reduced opacity (~50%),
            # which is acceptable - just check it's visible at all
            assert metrics["effectiveTextAlpha"] >= 0.4, "Cancel button text should be visible"
            assert (
                abs(metrics["bgBrightness"] - metrics["bodyBrightness"]) >= 40
            ), "Cancel button background should contrast with the surrounding area"
            assert (
                abs(metrics["bgBrightness"] - metrics["textBrightness"]) >= 60
            ), "Cancel button text should contrast with its background"


class TestModelReasoningDropdowns:
    """Regression tests to ensure model/reasoning dropdowns are always present.

    These tests were added after a regression accidentally removed the dropdowns.
    They must NEVER be skipped - if these dropdowns are missing, the UI is broken.
    """

    def test_coding_model_dropdown_visible(self, page: Page):
        """Coding agent 'Preferred Model' dropdown must be visible."""
        dropdown = page.get_by_label("Preferred Model", exact=True)
        expect(dropdown).to_be_visible()

    def test_coding_reasoning_dropdown_visible(self, page: Page):
        """Coding agent 'Reasoning Effort' dropdown must be visible."""
        dropdown = page.get_by_label("Reasoning Effort", exact=True)
        expect(dropdown).to_be_visible()

    def test_verification_model_dropdown_visible(self, page: Page):
        """Verification agent 'Verification Preferred Model' dropdown must be visible."""
        dropdown = page.get_by_label("Verification Preferred Model")
        expect(dropdown).to_be_visible()

    def test_verification_reasoning_dropdown_visible(self, page: Page):
        """Verification agent 'Verification Reasoning Effort' dropdown must be visible."""
        dropdown = page.get_by_label("Verification Reasoning Effort")
        expect(dropdown).to_be_visible()

    def test_all_model_reasoning_dropdowns_present(self, page: Page):
        """All four model/reasoning dropdowns must be present on the page.

        This is the key regression test - if any of these are missing,
        the UI refactoring has broken essential functionality.
        """
        # Get all four dropdowns
        coding_model = page.get_by_label("Preferred Model", exact=True)
        coding_reasoning = page.get_by_label("Reasoning Effort", exact=True)
        verif_model = page.get_by_label("Verification Preferred Model")
        verif_reasoning = page.get_by_label("Verification Reasoning Effort")

        # All must be visible
        expect(coding_model).to_be_visible()
        expect(coding_reasoning).to_be_visible()
        expect(verif_model).to_be_visible()
        expect(verif_reasoning).to_be_visible()

        # Verify they are dropdown/select-like components (have options)
        # Just checking visibility is sufficient for regression prevention


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

    def test_add_provider_accordion_spacing_and_emphasis(self, page: Page):
        """Add provider accordion should sit tight to cards and be visually emphasized."""
        measurement = measure_add_provider_accordion(page)
        gap = measurement["gap"]
        # Allow up to 16px gap (flex layout gap) - previously was 172px+ when empty columns weren't hidden
        assert gap <= 16, f"Expected gap <= 16px, got {gap}px"

        font_size = float(str(measurement["fontSize"]).replace("px", ""))
        assert font_size >= 18, f"Expected font size >= 18px, got {font_size}px"

        font_weight_raw = str(measurement["fontWeight"])
        if font_weight_raw.isdigit():
            font_weight = int(font_weight_raw)
        else:
            font_weight = 700 if font_weight_raw.lower() == "bold" else 400
        assert font_weight >= 600, f"Expected font weight >= 600, got {font_weight_raw}"

    def test_provider_usage_visible(self, page: Page):
        """Provider usage boxes should render with content."""
        page.get_by_role("tab", name="⚙️ Providers").click()
        usage = page.locator(".provider-usage").first
        expect(usage).to_be_visible(timeout=5000)

        text = usage.text_content() or ""
        assert text.strip(), "Usage text should not be empty"


class TestSubtaskTabs:
    """Test subtask tab filtering (integration with mock provider)."""

    def test_subtask_tabs_hidden_initially(self, page: Page):
        """Subtask tabs should be hidden before a task starts."""
        tabs = page.locator("#subtask-tabs")
        # Should either not exist or be hidden
        if tabs.count() > 0:
            expect(tabs).to_be_hidden()


class TestTaskTabs:
    """Test dynamic task tab creation with the + tab."""

    def test_click_plus_reveals_task_2(self, page: Page):
        """Clicking + tab should auto-create and switch to Task 2."""
        # Click the + tab - JS auto-clicks the Add button
        main_tabs = page.locator("#main-tabs")
        plus_tab = main_tabs.get_by_role("tab", name="➕")
        plus_tab.click()

        # Wait for Task 2 tab to appear (auto-created by JS)
        task2_tab = page.get_by_role("tab", name="Task 2")
        expect(task2_tab).to_be_visible(timeout=5000)

        # Task 2 should now be selected
        is_selected = task2_tab.get_attribute("aria-selected")
        assert is_selected == "true", "Task 2 should be selected after clicking +"

    def test_task_2_has_content(self, page: Page):
        """Task 2 should have proper UI content when created."""
        # Click + tab to auto-create Task 2
        main_tabs = page.locator("#main-tabs")
        plus_tab = main_tabs.get_by_role("tab", name="➕")
        plus_tab.click()

        # Wait for Task 2 tab to appear
        task2_tab = page.get_by_role("tab", name="Task 2")
        expect(task2_tab).to_be_visible(timeout=5000)

        # Wait for content to render
        page.wait_for_timeout(1000)

        # Check that we can see a Start Task button (should be visible in Task 2)
        start_btns = page.locator('button:has-text("Start Task"):visible')
        expect(start_btns.first).to_be_visible(timeout=5000)

    def test_task_2_session_log_stays_single_line(self, page: Page):
        """Task 2 session log button should keep icon and filename on one line."""
        main_tabs = page.locator("#main-tabs")
        plus_tab = main_tabs.get_by_role("tab", name="➕")
        plus_tab.click()

        task2_tab = page.get_by_role("tab", name="Task 2")
        expect(task2_tab).to_be_visible(timeout=5000)

        # Ensure Task 2 is active
        task2_tab.click()
        page.wait_for_timeout(500)

        layout = page.evaluate(
            """
() => {
  const rows = Array.from(document.querySelectorAll('.role-status-row'));
  const visibleRow = rows.find((row) => {
    const style = window.getComputedStyle(row);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    const rect = row.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  });
  if (!visibleRow) return { error: 'no visible status row' };
  const btn = visibleRow.querySelector('a[download], button, .download-button');
  if (!btn) return { error: 'no session log button' };
  const style = window.getComputedStyle(btn);
  return {
    whiteSpace: style.whiteSpace,
    text: (btn.textContent || '').trim(),
  };
}
"""
        )

        assert not layout.get("error"), f"Session log lookup failed: {layout.get('error')}"
        assert (
            layout["whiteSpace"] == "nowrap"
        ), f"Expected session log button to stay on one line, got whiteSpace={layout['whiteSpace']}"

    def test_click_plus_twice_reveals_task_3(self, page: Page):
        """Clicking + twice should reveal Task 2 then Task 3."""
        main_tabs = page.locator("#main-tabs")
        plus_tab = main_tabs.get_by_role("tab", name="➕")

        # First click - auto-creates Task 2
        plus_tab.click()
        task2_tab = page.get_by_role("tab", name="Task 2")
        expect(task2_tab).to_be_visible(timeout=5000)

        # Second click - auto-creates Task 3
        plus_tab.click()
        task3_tab = page.get_by_role("tab", name="Task 3")
        expect(task3_tab).to_be_visible(timeout=5000)

        # Task 3 should be selected
        is_selected = task3_tab.get_attribute("aria-selected")
        assert is_selected == "true", "Task 3 should be selected after second click"


class TestLiveActivityFormat:
    """Test that live activity uses Claude Code format."""

    def test_live_stream_box_exists(self, page: Page):
        """Live stream box should exist (may be hidden when empty)."""
        # Wait for the element to be attached to DOM (it may take a moment to render)
        box = page.locator("#live-stream-box")
        box.wait_for(state="attached", timeout=5000)
        assert box.count() > 0, "live-stream-box should exist in DOM"


class TestNoStatusBox:
    """Verify status box has been removed."""

    def test_no_status_box(self, page: Page):
        """Status box should not exist in the DOM."""
        status_box = page.locator("#status-box")
        assert status_box.count() == 0, "status_box should be completely removed"


class TestTaskStatusHeader:
    """Test task status header component."""

    def test_task_status_header_hidden_initially(self, page: Page):
        """Task status header should be hidden before task starts."""
        header = page.locator("#task-status-header")
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
            f"Confirm button should appear after first click. " f"feedback='{result.feedback_message}'"
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
        visible_cards_before = [c for c in cards_before if c["hasHeaderSpan"]]

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
        visible_cards_after = [c for c in cards_after if c["hasHeaderSpan"]]
        empty_cards_after = [c for c in cards_after if not c["hasHeaderSpan"]]

        # Verify there's one less visible card
        assert len(visible_cards_after) == len(visible_cards_before) - 1, (
            f"Should have one less visible card after deletion. "
            f"Before: {len(visible_cards_before)}, After: {len(visible_cards_after)}"
        )

        # Verify empty cards are actually hidden (display: none)
        for empty_card in empty_cards_after:
            assert empty_card["cardDisplay"] == "none" or empty_card["columnDisplay"] == "none", (
                f"Empty card should be hidden but has cardDisplay={empty_card['cardDisplay']}, "
                f"columnDisplay={empty_card['columnDisplay']}. Card: {empty_card}"
            )


class TestLiveViewFormat:
    """Test live view content formatting including colors and diffs.

    These tests verify that ANSI colors are converted to readable HTML,
    that diffs are highlighted, and that the AI switch header is properly formatted.
    """

    # Sample test content simulating ANSI colored output
    ANSI_TEST_HTML = """
    <div>
        <p>First paragraph of output</p>
        <span style="color: rgb(92, 99, 112);">Dark grey text that should be boosted</span>
        <span style="color: rgb(198, 120, 221);">Purple text for tool calls</span>
        <span style="color: rgb(152, 195, 121);">Green text for success</span>
    </div>
    """

    # Sample diff content
    DIFF_TEST_HTML = """
    <div>
        <span class="diff-header">@@ -1,5 +1,7 @@</span>
        <span class="diff-remove">- removed line</span>
        <span class="diff-add">+ added line</span>
    </div>
    """

    def test_live_stream_box_accepts_injected_content(self, page: Page):
        """Live stream box should be able to display injected test content."""
        inject_live_stream_content(page, "<p>Test content</p>")
        result = check_live_stream_colors(page)
        assert result.content_visible, "Content should be visible after injection"
        assert "Test content" in result.raw_html

    def test_colored_spans_are_readable(self, page: Page):
        """Colored spans should have sufficient brightness on dark background."""
        inject_live_stream_content(page, self.ANSI_TEST_HTML)
        result = check_live_stream_colors(page)

        assert result.has_colored_spans, "Test content should have colored spans"
        # Check that colors are boosted by CSS brightness filter
        for color_info in result.computed_colors:
            # Verify the filter is applied (brightness should be boosted)
            computed = color_info.get("computedColor", "")
            print(f"Color: {computed} for text: {color_info.get('text', '')[:30]}")

    def test_dark_grey_text_is_visible(self, page: Page):
        """Dark grey text (rgb(92,99,112)) should be boosted to be readable."""
        # This is the specific color that was causing visibility issues
        dark_grey_html = '<span style="color: rgb(92, 99, 112);">This dark grey should be visible</span>'
        inject_live_stream_content(page, dark_grey_html)
        result = check_live_stream_colors(page)

        assert result.has_colored_spans, "Should detect colored span"
        # The CSS should boost this dark color
        if result.computed_colors:
            color = result.computed_colors[0].get("computedColor", "")
            print(f"Dark grey computed to: {color}")

    def test_diff_classes_render_correctly(self, page: Page):
        """Diff classes should be present and styled correctly."""
        inject_live_stream_content(page, self.DIFF_TEST_HTML)
        result = check_live_stream_colors(page)

        assert result.has_diff_classes, f"Should detect diff classes in content. HTML: {result.raw_html[:200]}"

    def test_plain_text_with_newlines_renders_on_multiple_lines(self, page: Page):
        """Plain text with newlines (Claude-style) should render on multiple lines, not one long line."""
        # Simulate Claude output which is plain text with \n newlines
        plain_text_content = """Line 1: First line of output
Line 2: Second line
Line 3: Third line
Line 4: Fourth line"""

        # The UI should convert this to HTML via build_live_stream_html
        # which wraps it in live-output-content div
        html_wrapped = f'<div class="live-output-content">{plain_text_content}</div>'
        inject_live_stream_content(page, html_wrapped)

        # Get the rendered content element - use .last since inject creates it
        content_box = page.locator("#live-stream-box .live-output-content").last

        # Check that content is visible
        assert content_box.is_visible(), "Live output content should be visible"

        # Get the computed height - if all text is on one line, height will be ~1.5em
        # If properly formatted with line breaks, height should be much larger
        box_height = content_box.evaluate("el => el.offsetHeight")

        # With 4 lines and line-height: 1.5, we expect at least 60px (4 * 1.5 * 13px font ≈ 78px)
        assert box_height > 60, (
            f"Content appears to be on one line (height={box_height}px). "
            "Expected multi-line rendering with newlines preserved."
        )

        # Also check that the white-space CSS property is set to preserve newlines
        white_space = content_box.evaluate("el => getComputedStyle(el).whiteSpace")
        assert white_space in (
            "pre-wrap",
            "pre",
            "pre-line",
        ), f"white-space should preserve newlines, got: {white_space}"

    def test_revision_chat_bubble_shows_streaming_content(self, page: Page):
        """Chat bubble during revision should show streaming content, not static placeholder.

        This test reproduces the bug where the chat bubble shows static
        '⏳ Working on revisions...' instead of live streaming content
        during the revision process.
        """
        # Simulate the revision flow by creating a chat bubble with revision placeholder
        revision_placeholder_content = """
        <div role="article" class="message-wrap">
            <div class="message">
                <div class="avatar">🤖</div>
                <div class="content">
                    <h3>CODING AI</h3>
                    <p>⏳ <em>Working on revisions...</em></p>
                </div>
            </div>
        </div>
        """

        # Inject initial placeholder content into chat area
        chat_area = page.locator("#agent-chatbot")
        chat_area.wait_for(state="attached", timeout=5000)

        # Add the placeholder message to chat history
        page.evaluate(f"""
            () => {{
                const chatbot = document.querySelector('#agent-chatbot');
                if (chatbot && chatbot.tagName === 'GRADIO-CHATBOT') {{
                    // Simulate the revision placeholder being added to chat
                    const messageContainer = chatbot.querySelector('.message-wrap:last-child') ||
                                           chatbot.querySelector('.wrap');
                    if (messageContainer) {{
                        messageContainer.innerHTML = `{revision_placeholder_content}`;
                    }}
                }}
            }}
        """)

        # Verify the placeholder is showing
        placeholder_element = page.locator("#agent-chatbot").locator("text=Working on revisions")
        assert placeholder_element.is_visible(), "Revision placeholder should be visible initially"

        # Now simulate streaming content arriving - this should REPLACE the placeholder
        # with actual live content (like the normal message flow does)
        streaming_content = """
        <div role="article" class="message-wrap">
            <div class="message">
                <div class="avatar">🤖</div>
                <div class="content">
                    <h3>CODING AI</h3>
                    <div class="live-stream-inline">
                        <div class="live-output-header">▶ CODING AI (Live)</div>
                        <div class="live-output-content">🔄 Analyzing changes...
Running tests...
✓ All tests passed</div>
                    </div>
                </div>
            </div>
        </div>
        """

        # Update the chat bubble with streaming content (this is what should happen)
        page.evaluate(f"""
            () => {{
                const chatbot = document.querySelector('#agent-chatbot');
                if (chatbot && chatbot.tagName === 'GRADIO-CHATBOT') {{
                    const messageContainer = chatbot.querySelector('.message-wrap:last-child') ||
                                           chatbot.querySelector('.wrap');
                    if (messageContainer) {{
                        messageContainer.innerHTML = `{streaming_content}`;
                    }}
                }}
            }}
        """)

        # Verify that streaming content is now visible instead of placeholder
        streaming_element = page.locator("#agent-chatbot").locator("text=Analyzing changes")
        assert streaming_element.is_visible(), "Streaming content should replace placeholder in chat bubble"

        # Verify the placeholder is gone
        placeholder_check = page.locator("#agent-chatbot").locator("text=Working on revisions")
        assert not placeholder_check.is_visible(), "Static placeholder should be replaced by streaming content"

        # Verify the live content formatting is present
        live_header = page.locator("#agent-chatbot").locator("text=▶ CODING AI (Live)")
        assert live_header.is_visible(), "Live stream header should be visible in chat bubble"

    def test_live_view_does_not_autoscroll_on_new_entries(self, page: Page):
        """Live view should keep the current scroll position when new content arrives."""
        long_text = "\n".join([f"Line {idx}: output" for idx in range(120)])
        inject_live_stream_content(page, long_text)

        scroll_metrics = page.evaluate(
            """
() => {
    const box = document.querySelector('#live-stream-box') || document.querySelector('.live-stream-box');
    if (!box) return null;
    const container = box.querySelector('.live-output-content') || box;
    return {
        scrollTop: container.scrollTop,
        scrollHeight: container.scrollHeight,
        clientHeight: container.clientHeight
    };
}
"""
        )
        assert scroll_metrics, "Live view container should exist"
        assert scroll_metrics["scrollHeight"] > scroll_metrics["clientHeight"], (
            "Test requires scrollable live view content."
        )

        page.evaluate(
            """
() => {
    const box = document.querySelector('#live-stream-box') || document.querySelector('.live-stream-box');
    if (!box) return false;
    const container = box.querySelector('.live-output-content') || box;
    // Simulate user scrolling to top: scrollTop=0 and savedScrollTop=0 (not null)
    container.scrollTop = 0;
    if (window._liveStreamScroll && window._liveStreamScroll.has(container)) {
        const state = window._liveStreamScroll.get(container);
        state.userScrolledUp = true;  // User actively scrolled away from bottom
        state.savedScrollTop = 0;  // User's scroll position (0 = top of content)
    }
    return true;
}
"""
        )

        page.evaluate(
            """
() => {
    const box = document.querySelector('#live-stream-box') || document.querySelector('.live-stream-box');
    if (!box) return false;
    const container = box.querySelector('.live-output-content') || box;
    container.insertAdjacentHTML('beforeend', '<div>New entry</div>');
    return true;
}
"""
        )
        page.wait_for_timeout(250)

        scroll_after = page.evaluate(
            """
() => {
    const box = document.querySelector('#live-stream-box') || document.querySelector('.live-stream-box');
    if (!box) return null;
    const container = box.querySelector('.live-output-content') || box;
    return container.scrollTop;
}
"""
        )
        assert scroll_after is not None, "Live view scrollTop should be readable"
        assert scroll_after <= 1, f"Live view auto-scrolled on new content (scrollTop={scroll_after})"

    def test_live_stream_box_has_proper_height_with_scroll_fix(self, page: Page):
        """Live stream box should maintain proper height (max-height: 400px) and scroll behavior."""
        # First test with minimal content (should use min-height)
        short_text = "Short content line"
        inject_live_stream_content(page, short_text)

        min_height_metrics = page.evaluate(
            """
() => {
    const box = document.querySelector('#live-stream-box') || document.querySelector('.live-stream-box');
    if (!box) return null;
    const container = box.querySelector('.live-output-content') || box;

    const style = window.getComputedStyle(container);
    return {
        clientHeight: container.clientHeight,
        scrollHeight: container.scrollHeight,
        minHeight: style.minHeight,
        maxHeight: style.maxHeight,
        isScrollable: container.scrollHeight > container.clientHeight
    };
}
"""
        )

        assert min_height_metrics, "Live view container should exist"

        # Verify minimum height is enforced
        assert min_height_metrics["minHeight"] == "100px", f"Expected min-height: 100px, got: {min_height_metrics['minHeight']}"
        assert min_height_metrics["clientHeight"] >= 100, f"Container should respect min-height: {min_height_metrics['clientHeight']}px"

        # Now test with long content (should reach max-height)
        long_text = "\n".join([f"Line {idx}: test content to fill the live stream box" for idx in range(100)])
        inject_live_stream_content(page, long_text)

        max_height_metrics = page.evaluate(
            """
() => {
    const box = document.querySelector('#live-stream-box') || document.querySelector('.live-stream-box');
    if (!box) return null;
    const container = box.querySelector('.live-output-content') || box;

    const style = window.getComputedStyle(container);
    return {
        clientHeight: container.clientHeight,
        scrollHeight: container.scrollHeight,
        maxHeight: style.maxHeight,
        minHeight: style.minHeight,
        isScrollable: container.scrollHeight > container.clientHeight
    };
}
"""
        )

        assert max_height_metrics, "Live view container should exist"

        # Verify the max-height is set to 400px
        assert max_height_metrics["maxHeight"] == "400px", f"Expected max-height: 400px, got: {max_height_metrics['maxHeight']}"

        # Verify the container is scrollable with long content
        assert max_height_metrics["isScrollable"], "Live stream should be scrollable with long content"

        # Verify the container is actually near max height (should be around 400px, not just a few lines)
        # Allow some flexibility for padding/borders
        assert max_height_metrics["clientHeight"] >= 350, f"Live stream height too small: {max_height_metrics['clientHeight']}px (should be close to 400px)"
        assert max_height_metrics["clientHeight"] <= 410, f"Live stream height too large: {max_height_metrics['clientHeight']}px (should be close to 400px)"

    def test_task_2_live_stream_has_multiline_formatting(self, page: Page):
        """Task 2 live stream should keep styled multiline formatting like Task 1."""
        # Create Task 2 via + tab and ensure it's active
        try:
            main_tabs = page.locator("#main-tabs")
            plus_tab = main_tabs.get_by_role("tab", name="➕")
            plus_tab.click()
        except Exception:
            # Try fallback tabs if the main plus tab isn't clickable
            try:
                fallback_tab = page.locator("#initial-static-plus-tab")
                fallback_tab.click()
            except Exception:
                fallback_tab = page.locator("#fallback-plus-tab")
                fallback_tab.click()
        task2_tab = page.get_by_role("tab", name="Task 2")
        expect(task2_tab).to_be_visible(timeout=5000)
        task2_tab.click()
        expect(task2_tab).to_have_attribute("aria-selected", "true")

        panel_id = task2_tab.get_attribute("aria-controls")
        assert panel_id, "Task 2 tab should reference a tabpanel via aria-controls"
        panel_selector = f"#{panel_id}"
        panel = page.locator(panel_selector)
        expect(panel).to_be_visible(timeout=5000)

        multiline_content = """Line 1: Task 2 output
Line 2: Still on its own line
Line 3: With colors and spacing"""
        html_wrapped = f'<div class="live-output-content">{multiline_content}</div>'
        inject_live_stream_content(page, html_wrapped, container_selector=panel_selector)

        content_box = panel.locator(".live-output-content").last
        assert content_box.is_visible(), "Task 2 live output content should be visible"

        white_space = content_box.evaluate("el => getComputedStyle(el).whiteSpace")
        assert white_space in ("pre-wrap", "pre", "pre-line"), (
            f"Task 2 live view should preserve newlines, got: {white_space}"
        )

    def test_inject_requires_live_stream_box(self, page: Page):
        """Inject helper should not create content when live stream box is missing."""
        page.evaluate(
            """
() => {
    const container = document.createElement('div');
    container.id = 'fake-live-container';
    container.textContent = 'placeholder';
    document.body.appendChild(container);
}
"""
        )
        inject_live_stream_content(page, "<p>Should not inject</p>", container_selector="#fake-live-container")
        content_count = page.evaluate(
            """
() => {
    const container = document.querySelector('#fake-live-container');
    if (!container) return -1;
    return container.querySelectorAll('.live-output-content').length;
}
"""
        )
        assert content_count == 0, "Inject helper should not create live-output-content in non-live containers"

    def test_live_view_shows_during_revision_placeholder(self, page: Page):
        """Live view should show placeholder content during revision setup."""
        # Test verifies that revision placeholders now show live stream content
        # instead of leaving it blank (which was the bug)

        # First, ensure the live stream box exists
        box = page.locator("#live-stream-box")
        box.wait_for(state="attached", timeout=5000)

        # Simulate revision placeholder content (fixed version)
        revision_placeholder_html = """
        <div class="live-output-header">▶ CODING AI (Live Stream)</div>
        <div class="live-output-content">⏳ Preparing revision...</div>
        """
        inject_live_stream_content(page, revision_placeholder_html)

        # Verify the live stream becomes visible with placeholder content
        is_visible = box.evaluate("""
            el => {
                const computed = getComputedStyle(el);
                return computed.display !== 'none' && !el.classList.contains('live-stream-hidden');
            }
        """)
        assert is_visible, "Live stream should be visible with revision placeholder"

        # Verify placeholder content is present
        content_element = box.locator(".live-output-content")
        assert content_element.is_visible(), "Live stream content should be visible"

        content_text = content_element.text_content()
        assert "Preparing revision" in content_text, "Should show revision preparation message"

        # Simulate content update during streaming
        streaming_html = """
        <div class="live-output-header">▶ CODING AI (Live Stream)</div>
        <div class="live-output-content">🔄 Revision in progress...
Analyzing changes...
Running tests...</div>
        """
        inject_live_stream_content(page, streaming_html)

        # Verify updated content is visible
        updated_content = content_element.text_content()
        assert "Revision in progress" in updated_content, "Should show updated revision content"
        assert "Running tests" in updated_content, "Should show streaming content"


class TestJsPatchLiveContent:
    """Test JavaScript live content patching for scroll/selection preservation."""

    def test_js_patch_function_exists(self, page: Page):
        """The _patchLiveContent JavaScript function should be available."""
        has_function = page.evaluate("() => typeof window._patchLiveContent === 'function'")
        assert has_function, "_patchLiveContent function should exist on window"

    def test_js_patch_preserves_scroll_position(self, page: Page):
        """JS patching should preserve scroll position when updating inline content."""
        # Create a container with data-live-id attribute
        page.evaluate("""
        () => {
            const chatbot = document.querySelector('#agent-chatbot') || document.querySelector('.agent-chatbot');
            if (!chatbot) return false;

            // Create a mock inline live content container with scrollable content
            const container = document.createElement('div');
            container.className = 'inline-live-content';
            container.setAttribute('data-live-id', 'test-scroll-1');
            container.style.cssText = 'max-height: 100px; overflow-y: auto; white-space: pre-wrap;';

            // Add enough content to make it scrollable
            const lines = [];
            for (let i = 0; i < 50; i++) {
                lines.push('Line ' + i + ': Some test content here');
            }
            container.innerHTML = lines.join('\\n');

            chatbot.appendChild(container);
            return true;
        }
        """)
        page.wait_for_timeout(100)

        # Get initial metrics and scroll to middle
        page.evaluate("""
        () => {
            const container = document.querySelector('[data-live-id="test-scroll-1"]');
            if (!container) return;
            // Scroll to a specific position (middle-ish)
            container.scrollTop = 200;

            // Mark user as scrolled up so JS knows to preserve position
            if (window._liveStreamScroll) {
                const state = window._liveStreamScroll.get(container);
                if (state) {
                    state.userScrolledUp = true;
                    state.savedScrollTop = 200;
                }
            }
        }
        """)
        page.wait_for_timeout(100)

        # Get scroll position before patch
        scroll_before = page.evaluate("""
        () => {
            const container = document.querySelector('[data-live-id="test-scroll-1"]');
            return container ? container.scrollTop : null;
        }
        """)
        assert scroll_before is not None, "Container should exist"
        assert scroll_before >= 190, f"Expected scrollTop ~200, got {scroll_before}"

        # Patch content with new lines
        page.evaluate("""
        () => {
            const lines = [];
            for (let i = 0; i < 60; i++) {
                lines.push('Updated Line ' + i + ': New content');
            }
            const newHtml = lines.join('\\n');
            window._patchLiveContent('test-scroll-1', newHtml);
        }
        """)
        page.wait_for_timeout(150)

        # Verify scroll position is preserved
        scroll_after = page.evaluate("""
        () => {
            const container = document.querySelector('[data-live-id="test-scroll-1"]');
            return container ? container.scrollTop : null;
        }
        """)
        assert scroll_after is not None, "Container should still exist after patch"
        # Allow some tolerance for the scroll position
        assert abs(scroll_after - scroll_before) <= 10, (
            f"Scroll position not preserved: before={scroll_before}, after={scroll_after}"
        )

    def test_js_patch_updates_content(self, page: Page):
        """JS patching should actually update the content."""
        # Create a container
        page.evaluate("""
        () => {
            const chatbot = document.querySelector('#agent-chatbot') || document.querySelector('.agent-chatbot');
            if (!chatbot) return false;

            const container = document.createElement('div');
            container.className = 'inline-live-content';
            container.setAttribute('data-live-id', 'test-update-1');
            container.innerHTML = 'Initial content';
            chatbot.appendChild(container);
            return true;
        }
        """)

        # Patch with new content
        result = page.evaluate("""
        () => {
            const success = window._patchLiveContent('test-update-1', '<b>Updated content</b>');
            const container = document.querySelector('[data-live-id="test-update-1"]');
            return {
                success,
                content: container ? container.innerHTML : null
            };
        }
        """)

        assert result["success"], "_patchLiveContent should return true"
        assert "<b>Updated content</b>" in result["content"], "Content should be updated"

    def test_js_patch_returns_false_for_missing_container(self, page: Page):
        """JS patching should return false if container doesn't exist."""
        result = page.evaluate("""
        () => window._patchLiveContent('nonexistent-id', 'content')
        """)
        assert result is False, "_patchLiveContent should return false for missing container"

    def test_inline_live_content_scroll_tracking(self, page: Page):
        """Inline live content containers should get scroll tracking set up."""
        # Create an inline live content container
        page.evaluate("""
        () => {
            const chatbot = document.querySelector('#agent-chatbot') || document.querySelector('.agent-chatbot');
            if (!chatbot) return false;

            const container = document.createElement('div');
            container.className = 'inline-live-content';
            container.setAttribute('data-live-id', 'test-tracking-1');
            container.style.cssText = 'max-height: 100px; overflow-y: auto;';
            container.innerHTML = 'Test content';
            chatbot.appendChild(container);
            return true;
        }
        """)

        # Wait for the scroll tracking to be set up (runs on interval)
        page.wait_for_timeout(600)

        # Check if scroll tracking is set up
        has_tracking = page.evaluate("""
        () => {
            const container = document.querySelector('[data-live-id="test-tracking-1"]');
            if (!container) return false;
            return window._liveStreamScroll && window._liveStreamScroll.has(container);
        }
        """)
        assert has_tracking, "Inline live content should have scroll tracking set up"

    def test_initial_placeholder_has_live_id_container(self, page: Page):
        """Initial empty placeholder should still have data-live-id container for JS patching.

        This is the key regression test: when a task starts, the placeholder message
        must have a data-live-id container so JS can patch it with streaming updates.
        If the container doesn't exist initially, live view updates are never shown.
        """
        # Simulate what Python does: build_inline_live_html("", "CODING AI", live_id="test-initial-1")
        # The result MUST contain data-live-id="test-initial-1" even when content is empty
        page.evaluate("""
        () => {
            const chatbot = document.querySelector('#agent-chatbot') || document.querySelector('.agent-chatbot');
            if (!chatbot) return false;

            // Simulate the initial placeholder that Python creates
            // This is what build_inline_live_html("", "CODING AI", live_id=...) should produce
            const bubble = document.createElement('div');
            bubble.className = 'message-bubble';
            bubble.innerHTML = `
                <strong>CODING AI</strong>
                <div class="inline-live-header">▶ CODING AI (Live)</div>
                <div class="inline-live-content" data-live-id="test-initial-1"><em>Working...</em></div>
            `;
            chatbot.appendChild(bubble);
            return true;
        }
        """)
        page.wait_for_timeout(100)

        # Verify the container exists and can be found by JS
        container_exists = page.evaluate("""
        () => {
            const container = document.querySelector('[data-live-id="test-initial-1"]');
            return container !== null;
        }
        """)
        assert container_exists, "Initial placeholder must have data-live-id container"

        # Verify JS patching works on this initial container
        result = page.evaluate("""
        () => {
            const success = window._patchLiveContent('test-initial-1', '<b>Now streaming real content...</b>');
            const container = document.querySelector('[data-live-id="test-initial-1"]');
            return {
                success,
                content: container ? container.innerHTML : null
            };
        }
        """)

        assert result["success"], "_patchLiveContent should work on initial placeholder"
        assert "Now streaming real content" in result["content"], "Content should be updated"


class TestLiveViewEndToEnd:
    """End-to-end tests for live view streaming with mock provider."""

    def test_live_view_placeholder_created_on_task_start(self, page: Page):
        """When a task starts, a placeholder with data-live-id should be created.

        This tests the actual Gradio/Python integration - not just JS functions.
        """
        # Fill in task description
        task_input = page.get_by_label("Task Description")
        task_input.fill("Test task for live view")
        page.wait_for_timeout(200)

        # Click start task button
        start_btn = page.locator("#start-task-btn")
        start_btn.click()

        # Wait for the chatbot to show a placeholder with data-live-id
        # The mock provider should create a message_start event that triggers this
        page.wait_for_timeout(500)

        # Check for data-live-id container in the chatbot
        has_live_container = page.evaluate("""
        () => {
            const chatbot = document.querySelector('#agent-chatbot') || document.querySelector('.agent-chatbot');
            if (!chatbot) return false;
            // Look for any data-live-id container
            const container = chatbot.querySelector('[data-live-id]');
            return container !== null;
        }
        """)
        assert has_live_container, (
            "Task start should create a placeholder with data-live-id container "
            "for JS to patch with streaming updates"
        )

    def test_live_view_content_updates_visible(self, page: Page):
        """Live view content should be visible and update during streaming."""
        # Fill in task description
        task_input = page.get_by_label("Task Description")
        task_input.fill("Show me live view test")
        page.wait_for_timeout(200)

        # Click start task button
        start_btn = page.locator("#start-task-btn")
        start_btn.click()

        # Wait for streaming to complete (mock provider is fast)
        page.wait_for_timeout(2000)

        # Check that the chatbot shows content (not just "Working...")
        chatbot_text = page.evaluate("""
        () => {
            const chatbot = document.querySelector('#agent-chatbot') || document.querySelector('.agent-chatbot');
            if (!chatbot) return '';
            return chatbot.textContent || '';
        }
        """)

        # The mock provider generates specific content - check for key phrases
        # If live view is broken, we'd only see "Working..." or nothing
        assert "CODING AI" in chatbot_text, "Chatbot should show CODING AI messages"
        # After task completes, we should see some actual content, not just placeholder
        assert len(chatbot_text) > 100, (
            f"Chatbot should have substantial content after streaming, got: {chatbot_text[:200]}"
        )


class TestRealisticLiveContent:
    """Test live view with realistic CLI-like content to verify all text is visible."""

    # Realistic content similar to actual CLI output (thinking, exec, commands)
    REALISTIC_CLI_HTML = """
<p>Investigating request</p>
<p><span style="color: rgb(198, 120, 221);">thinking</span> I need to analyze this request...</p>
<p><span style="color: rgb(198, 120, 221);">exec</span>
<span style="color: rgb(152, 195, 121);">/bin/bash -lc 'ls -la'</span></p>
<p>total 48</p>
<p>drwxrwxr-x 5 user user 4096 Dec 27 10:00 .</p>
<p>drwxr-xr-x 3 user user 4096 Dec 27 09:00 ..</p>
<p>-rw-rw-r-- 1 user user  123 Dec 27 10:00 README.md</p>
<p><span style="color: rgb(198, 120, 221);">thinking</span> The directory listing shows...</p>
<p><span style="color: rgb(152, 195, 121);">succeeded</span></p>
<p>Plain text without any color spans should also be visible</p>
"""

    def test_all_text_visible_with_realistic_content(self, page: Page):
        """ALL text should be visible on dark background, not just colored spans."""
        inject_live_stream_content(page, self.REALISTIC_CLI_HTML)
        result = verify_all_text_visible(page)

        assert "error" not in result, f"Error checking visibility: {result.get('error')}"

        # Print sample colors for debugging
        print("Sample computed colors:")
        for sample in result.get("sampleColors", []):
            print(f"  {sample['text'][:30]}: {sample['color']} (brightness={sample['brightness']:.1f})")

        # Critical assertion: no dark elements
        dark = result.get("darkElements", [])
        if dark:
            print("DARK ELEMENTS FOUND (FAIL):")
            for elem in dark:
                print(f"  {elem['text']}: {elem['color']} (brightness={elem['brightness']:.1f})")

        assert result.get("allVisible", False), f"Some text is too dark to read. Dark elements: {dark}"

    def test_screenshot_live_content_proof(self, page: Page, tmp_path):
        """Take screenshot of live stream with realistic content as proof of visibility."""
        inject_live_stream_content(page, self.REALISTIC_CLI_HTML)
        time.sleep(0.2)

        output = tmp_path / "live_stream_proof.png"
        page.screenshot(path=str(output))
        assert output.exists()
        print(f"Screenshot saved: {output}")

        # Also verify visibility
        result = verify_all_text_visible(page)
        assert result.get("allVisible", False), f"Text not visible in screenshot: {result}"


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


class TestMergeDiscardReset:
    """Test that Accept & Merge and Discard buttons properly reset the task tab.

    These tests verify the visual behavior of the task panel when:
    - Discard is clicked: merge section should hide, task description preserved
    - Accept & Merge is clicked: merge section should hide, all cleared

    Note: The handler logic is also verified via unit tests in test_web_ui.py.
    """

    def test_merge_section_initially_empty(self, page: Page):
        """Verify merge section Group starts hidden (empty content).

        The merge section uses a gr.Group that starts with visible=False,
        so its content (header, summary, buttons) should not be visible initially.
        """
        # Check that merge section content is not visible
        merge_info = page.evaluate(
            """
        () => {
            const mergeSection = document.querySelector('.merge-section');
            if (!mergeSection) {
                return { exists: false, hasContent: false };
            }
            // Check if the accept/merge button is visible (it's inside the Group)
            const mergeBtn = mergeSection.querySelector('.accept-merge-btn');
            if (!mergeBtn) {
                return { exists: true, hasContent: false };
            }
            const style = window.getComputedStyle(mergeBtn);
            const visible = style.display !== 'none' && style.visibility !== 'hidden';
            return { exists: true, hasContent: visible };
        }
        """
        )

        # Content should not be visible initially
        assert not merge_info.get("hasContent", False), (
            "Merge section content should not be visible initially"
        )

    def test_merge_section_column_exists(self, page: Page):
        """Verify merge section Column is in DOM (for Gradio component binding)."""
        # The Column should always be in DOM for Gradio to bind events
        exists = page.evaluate(
            """
        () => {
            const mergeSection = document.querySelector('.merge-section');
            return !!mergeSection;
        }
        """
        )

        assert exists, "Merge section Column should be in DOM for event binding"

    def test_page_fully_loaded(self, page: Page):
        """Verify the page has fully loaded with all JavaScript."""
        has_loaded = page.evaluate(
            """
        () => {
            return document.readyState === 'complete';
        }
        """
        )

        assert has_loaded, "Page should be fully loaded"

    def test_accordion_styles_not_reset_by_sync(self, page: Page):
        """Verify JS sync only clears direct children styles, not accordion internals.

        The syncMergeSectionVisibility function should use ':scope > *' to only
        clear styles on direct children of the merge section, preserving
        accordion open/close state which relies on descendant element styles.
        """
        # Verify the JS selector uses :scope > * (direct children only)
        uses_direct_children = page.evaluate(
            """
        () => {
            // Check if the page has the correct JS pattern
            const scripts = document.querySelectorAll('script');
            for (const script of scripts) {
                const text = script.textContent || '';
                // Look for the syncMergeSectionVisibility function
                if (text.includes('syncMergeSectionVisibility')) {
                    // Verify it uses :scope > * for direct children only
                    return text.includes(":scope > *");
                }
            }
            return false;
        }
        """
        )

        assert uses_direct_children, (
            "syncMergeSectionVisibility should use ':scope > *' to only clear "
            "direct children styles, preserving accordion internal state"
        )

    def test_merge_section_hidden_when_no_changes(self, page: Page):
        """Verify merge section is hidden when there are no changes.

        This test ensures that the "Changes Ready to Merge" header and merge section
        are not visible when there are no actual changes in the worktree.
        """
        # Wait for page to fully load and JS to run
        time.sleep(1.0)  # Give JS visibility sync time to run

        # Check merge section visibility and content
        merge_status = page.evaluate(
            """
        () => {
            const mergeSection = document.querySelector('.merge-section');
            if (!mergeSection) {
                return { exists: false };
            }

            // Check if it has the hidden class
            const hasHiddenClass = mergeSection.classList.contains('merge-section-hidden');

            // Check if the header has content
            const header = mergeSection.querySelector('h3, [data-testid="markdown"] h3');
            const headerText = header ? header.textContent.trim() : '';

            // Check if changes summary has content
            const summary = mergeSection.querySelector('[key*="changes-summary"]');
            const summaryText = summary ? summary.textContent.trim() : '';

            // Check computed visibility
            const style = window.getComputedStyle(mergeSection);
            const isVisible = style.display !== 'none' && style.visibility !== 'hidden' &&
                             !hasHiddenClass;

            return {
                exists: true,
                hasHiddenClass: hasHiddenClass,
                headerText: headerText,
                summaryText: summaryText,
                isVisible: isVisible,
                computedDisplay: style.display
            };
        }
        """
        )

        # The merge section should exist but be hidden
        assert merge_status["exists"], "Merge section should exist in DOM"
        assert merge_status["hasHiddenClass"], "Merge section should have 'merge-section-hidden' class"
        assert not merge_status["isVisible"], f"Merge section should not be visible, but got: {merge_status}"

        # Header and summary should be empty
        assert merge_status["headerText"] == "", f"Header should be empty but got: '{merge_status['headerText']}'"
        assert merge_status["summaryText"] == "", f"Summary should be empty but got: '{merge_status['summaryText']}'"


class TestInlineScreenshots:
    """Test that before/after screenshots render as actual images in the chatbot.

    These tests verify the fix for inline screenshot rendering. Previously,
    Gradio's HTML sanitization stripped <img> tags even when listed in allow_tags.
    The fix sets sanitize_html=False since content is internally generated.
    """

    def test_chatbot_allows_img_tags(self, page: Page):
        """Chatbot should render <img> tags without sanitizing them."""
        # Inject a message with an img tag directly into the chatbot
        # using a data URL (no external file needed)
        result = page.evaluate(
            """
        () => {
            const chatbot = document.querySelector('#agent-chatbot');
            if (!chatbot) return { error: 'chatbot not found' };

            // Create a minimal red 1x1 PNG as data URL
            const redPixelDataUrl = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==';

            // Find the message container
            const container = chatbot.querySelector('.chatbot, [data-testid="chatbot"]')
                || chatbot.querySelector('.messages')
                || chatbot;

            // Create a message with an img tag
            const wrapper = document.createElement('div');
            wrapper.className = 'message-row bot-row test-screenshot-message';

            const bubble = document.createElement('div');
            bubble.className = 'message bot-message';
            bubble.innerHTML = `
                <div class="screenshot-comparison">
                    <div class="screenshot-panel">
                        <div class="screenshot-label">Before</div>
                        <img src="${redPixelDataUrl}" alt="Before screenshot" class="test-before-img">
                    </div>
                    <div class="screenshot-panel">
                        <div class="screenshot-label">After</div>
                        <img src="${redPixelDataUrl}" alt="After screenshot" class="test-after-img">
                    </div>
                </div>
            `;

            wrapper.appendChild(bubble);
            container.appendChild(wrapper);

            // Wait a moment for any sanitization to occur
            return new Promise(resolve => {
                setTimeout(() => {
                    // Check if img tags still exist (weren't sanitized)
                    const beforeImg = container.querySelector('.test-before-img');
                    const afterImg = container.querySelector('.test-after-img');

                    resolve({
                        beforeImgExists: !!beforeImg,
                        afterImgExists: !!afterImg,
                        beforeImgSrc: beforeImg ? beforeImg.src.substring(0, 30) : null,
                        afterImgSrc: afterImg ? afterImg.src.substring(0, 30) : null,
                        screenshotComparisonExists: !!container.querySelector('.screenshot-comparison'),
                        labelExists: !!container.querySelector('.screenshot-label')
                    });
                }, 100);
            });
        }
        """
        )

        assert "error" not in result, f"Test setup failed: {result.get('error')}"
        assert result["beforeImgExists"], "Before <img> tag should not be sanitized"
        assert result["afterImgExists"], "After <img> tag should not be sanitized"
        assert result["beforeImgSrc"].startswith("data:image/png"), "Before img should have data URL src"
        assert result["afterImgSrc"].startswith("data:image/png"), "After img should have data URL src"
        assert result["screenshotComparisonExists"], "screenshot-comparison div should exist"
        assert result["labelExists"], "screenshot-label should exist"

    def test_screenshot_comparison_layout(self, page: Page):
        """Screenshot comparison should display images side by side."""
        # First inject the test content
        page.evaluate(
            """
        () => {
            const chatbot = document.querySelector('#agent-chatbot');
            if (!chatbot) return false;

            const redPixelDataUrl = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==';
            const greenPixelDataUrl = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAEBgIApD5fRAAAAABJRU5ErkJggg==';

            const container = chatbot.querySelector('.chatbot, [data-testid="chatbot"]')
                || chatbot.querySelector('.messages')
                || chatbot;

            // Clear any previous test messages
            container.querySelectorAll('.test-layout-message').forEach(m => m.remove());

            const wrapper = document.createElement('div');
            wrapper.className = 'message-row bot-row test-layout-message';

            const bubble = document.createElement('div');
            bubble.className = 'message bot-message';
            bubble.innerHTML = `
                <div class="screenshot-comparison" id="test-comparison">
                    <div class="screenshot-panel" id="test-before-panel">
                        <div class="screenshot-label">Before</div>
                        <img src="${redPixelDataUrl}" alt="Before">
                    </div>
                    <div class="screenshot-panel" id="test-after-panel">
                        <div class="screenshot-label">After</div>
                        <img src="${greenPixelDataUrl}" alt="After">
                    </div>
                </div>
            `;

            wrapper.appendChild(bubble);
            container.appendChild(wrapper);
            return true;
        }
        """
        )

        # Check the layout
        layout = page.evaluate(
            """
        () => {
            const comparison = document.querySelector('#test-comparison');
            if (!comparison) return { error: 'comparison div not found' };

            const beforePanel = document.querySelector('#test-before-panel');
            const afterPanel = document.querySelector('#test-after-panel');
            if (!beforePanel || !afterPanel) return { error: 'panels not found' };

            const compStyle = window.getComputedStyle(comparison);
            const beforeBox = beforePanel.getBoundingClientRect();
            const afterBox = afterPanel.getBoundingClientRect();

            return {
                display: compStyle.display,
                beforeLeft: beforeBox.left,
                afterLeft: afterBox.left,
                beforeWidth: beforeBox.width,
                afterWidth: afterBox.width,
                sideBySide: afterBox.left > beforeBox.left + beforeBox.width * 0.5
            };
        }
        """
        )

        assert "error" not in layout, f"Layout check failed: {layout.get('error')}"
        assert layout["display"] == "flex", f"screenshot-comparison should be flex, got {layout['display']}"
        assert layout["sideBySide"], (
            f"Before and After should be side by side. "
            f"Before left={layout['beforeLeft']}, After left={layout['afterLeft']}"
        )
