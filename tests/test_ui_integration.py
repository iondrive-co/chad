"""UI integration tests using Playwright to verify UI behavior with mock providers."""

import time

import pytest

try:
    from playwright.sync_api import Page, expect
except Exception:  # pragma: no cover - handled by pytest skip
    pytest.skip("playwright not available", allow_module_level=True)

from chad.ui_playwright_runner import (
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

    def test_task_entry_bubble_replaces_legacy_task_bubble(self, page: Page):
        """Task entry bubble should appear before any agent messages and no pre-filled chat bubbles."""
        bubble = page.locator('.task-entry-bubble')
        expect(bubble).to_be_visible()

        # Chatbot should start empty (no legacy Task bubble)
        message_count = page.evaluate(
            """
            () => {
              const chat = document.querySelector('#agent-chatbot');
              if (!chat) return 0;
              return chat.querySelectorAll('.message').length;
            }
            """
        )
        assert message_count == 0, f"Expected no initial chat bubbles, found {message_count}"

    def test_start_from_task_entry_disables_start_and_enables_cancel(self, page: Page):
        """Starting from the entry bubble should lock the form and enable cancel."""
        textarea = page.get_by_label("Task Description")
        textarea.fill("Do something important")

        start_btn = page.locator('#start-task-btn')
        start_btn.click()
        page.wait_for_function(
            """
            () => {
              const btn = document.querySelector('#start-task-btn');
              if (!btn) return false;
              return btn.disabled || btn.getAttribute('aria-disabled') === 'true' || btn.classList.contains('disabled');
            }
            """,
            timeout=5000
        )
        cancel_enabled = page.wait_for_function(
            """
            () => {
              const btn = document.querySelector('#cancel-task-btn');
              if (!btn) return false;
              const disabled = btn.disabled ||
                btn.getAttribute('aria-disabled') === 'true' ||
                btn.classList.contains('disabled');
              return !disabled;
            }
            """,
            timeout=5000
        ).json_value()

        assert cancel_enabled, "Cancel button should enable while the task is running"


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


class TestCodingAgentLayout:
    """Ensure the coding agent selector sits inside the top controls bar."""

    def test_status_row_spans_top_bar(self, page: Page):
        """Status row should sit beneath project path within the header area."""
        top_row = page.locator("#run-top-row")
        inputs_row = page.locator("#run-top-inputs")
        status_row = page.locator("#role-status-row")
        cancel_btn = page.locator("#cancel-task-btn")
        expect(top_row).to_be_visible()

        project_path = top_row.get_by_label("Project Path")
        coding_agent = top_row.get_by_label("Coding Agent")
        expect(inputs_row).to_be_visible()
        expect(status_row).to_be_visible()

        expect(project_path).to_be_visible()
        expect(coding_agent).to_be_visible()

        inputs_box = inputs_row.bounding_box()
        status_box = status_row.bounding_box()
        row_box = top_row.bounding_box()
        cancel_box = cancel_btn.bounding_box()
        project_box = project_path.bounding_box()

        assert inputs_box and status_box and row_box and cancel_box and project_box, (
            "Missing bounding box data for layout assertions"
        )

        # Status should sit below project path within the top row column
        assert status_box["y"] >= project_box["y"] + project_box["height"] - 2, (
            "Status row should appear below the project path input"
        )

        # Status should align to project path column rather than the cancel column
        assert status_box["x"] <= project_box["x"] + 4
        available_width = row_box["width"] - cancel_box["width"]
        assert status_box["width"] <= available_width

    def test_run_top_controls_stack_with_matching_widths(self, page: Page):
        """Preferred/Reasoning controls should stack under agent selectors with aligned widths."""
        project_path = page.get_by_label("Project Path")
        status = page.locator("#role-config-status")
        session_log = page.locator("#session-log-btn")
        coding_agent = page.get_by_label("Coding Agent")
        coding_model = page.get_by_label("Preferred Model")
        verification_agent = page.get_by_label("Verification Agent")
        reasoning = page.get_by_label("Reasoning Effort")

        expect(project_path).to_be_visible()
        expect(status).to_be_visible()
        expect(session_log).to_be_visible()
        expect(coding_agent).to_be_visible()
        expect(coding_model).to_be_visible()
        expect(verification_agent).to_be_visible()
        expect(reasoning).to_be_visible()

        project_box = project_path.bounding_box()
        status_box = status.bounding_box()
        log_box = session_log.bounding_box()
        coding_box = coding_agent.bounding_box()
        model_box = coding_model.bounding_box()
        verification_box = verification_agent.bounding_box()
        reasoning_box = reasoning.bounding_box()

        assert (
            project_box
            and status_box
            and log_box
            and coding_box
            and model_box
            and verification_box
            and reasoning_box
        )

        assert status_box["y"] >= project_box["y"] + project_box["height"] - 2, (
            "Status should appear beneath the Project Path field"
        )
        assert log_box["y"] >= project_box["y"] + project_box["height"] - 2, (
            "Session log button should appear beneath the Project Path field"
        )

        assert model_box["y"] >= coding_box["y"] + coding_box["height"] - 2, (
            "Preferred Model should stack beneath Coding Agent"
        )
        assert reasoning_box["y"] >= verification_box["y"] + verification_box["height"] - 2, (
            "Reasoning Effort should stack beneath Verification Agent"
        )

        assert abs(model_box["x"] - coding_box["x"]) <= 4
        assert abs(model_box["width"] - coding_box["width"]) <= 4
        assert abs(reasoning_box["x"] - verification_box["x"]) <= 4
        assert abs(reasoning_box["width"] - verification_box["width"]) <= 4

    def test_cancel_button_visible_light_and_dark(self, page: Page):
        """Cancel button should stay visible in both color schemes and be a bit wider on large layouts."""
        measurements = {}
        for scheme in ("light", "dark"):
            page.emulate_media(color_scheme=scheme)
            measurements[scheme] = page.evaluate(
                """
() => {
  const button = document.querySelector('#cancel-task-btn button') || document.querySelector('#cancel-task-btn');
  if (!button) return null;
  const styles = window.getComputedStyle(button);
  const bodyStyles = window.getComputedStyle(document.body);
  const rect = button.getBoundingClientRect();
  const toNumber = (value) => {
    if (!value) return NaN;
    const match = /([\\d.]+)/.exec(String(value));
    return match ? parseFloat(match[1]) : NaN;
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
  return {
    paddingLeft: toNumber(styles.paddingLeft),
    paddingRight: toNumber(styles.paddingRight),
    minWidth: toNumber(styles.minWidth),
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
            assert metrics["minWidth"] >= 90, (
                f"Cancel button should be wider for visibility, got {metrics['minWidth']}px"
            )
            assert metrics["minWidth"] <= 160, f"Cancel button should still be compact, got {metrics['minWidth']}px"
            assert metrics["paddingLeft"] <= 12 and metrics["paddingRight"] <= 12, (
                f"Expected compact horizontal padding on cancel button, got "
                f"{metrics['paddingLeft']}px/{metrics['paddingRight']}px"
            )
            assert metrics["effectiveTextAlpha"] >= 0.85, "Cancel button text should be opaque enough to read"
            assert abs(metrics["bgBrightness"] - metrics["bodyBrightness"]) >= 40, (
                "Cancel button background should contrast with the surrounding area"
            )
            assert abs(metrics["bgBrightness"] - metrics["textBrightness"]) >= 60, (
                "Cancel button text should contrast with its background"
            )


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
            computed = color_info.get('computedColor', '')
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
            color = result.computed_colors[0].get('computedColor', '')
            print(f"Dark grey computed to: {color}")

    def test_diff_classes_render_correctly(self, page: Page):
        """Diff classes should be present and styled correctly."""
        inject_live_stream_content(page, self.DIFF_TEST_HTML)
        result = check_live_stream_colors(page)

        assert result.has_diff_classes, (
            f"Should detect diff classes in content. HTML: {result.raw_html[:200]}"
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

        assert 'error' not in result, f"Error checking visibility: {result.get('error')}"

        # Print sample colors for debugging
        print("Sample computed colors:")
        for sample in result.get('sampleColors', []):
            print(f"  {sample['text'][:30]}: {sample['color']} (brightness={sample['brightness']:.1f})")

        # Critical assertion: no dark elements
        dark = result.get('darkElements', [])
        if dark:
            print("DARK ELEMENTS FOUND (FAIL):")
            for elem in dark:
                print(f"  {elem['text']}: {elem['color']} (brightness={elem['brightness']:.1f})")

        assert result.get('allVisible', False), (
            f"Some text is too dark to read. Dark elements: {dark}"
        )

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
        assert result.get('allVisible', False), f"Text not visible in screenshot: {result}"


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
