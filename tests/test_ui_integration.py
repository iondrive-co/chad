"""UI integration tests using Playwright to verify UI behavior with mock providers."""

import json
import time

import pytest

try:
    from playwright.sync_api import Page, expect
except Exception:  # pragma: no cover - handled by pytest skip
    pytest.skip("playwright not available", allow_module_level=True)

from chad.ui.gradio.verification.ui_playwright_runner import (
    ChadLaunchError,
    check_live_stream_colors,
    create_temp_env,
    delete_provider_by_name,
    get_card_visibility_debug,
    get_provider_names,
    inject_live_stream_content,
    inject_merge_diff_content,
    measure_diff_scrollbars,
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


def _expand_project_information(page: Page) -> None:
    """Expand the Project Information accordion if it is collapsed."""
    project_path = page.get_by_label("Project Path")
    if project_path.is_visible():
        return
    page.get_by_text("Project Information", exact=True).first.click()
    expect(project_path).to_be_visible()


def _collapse_project_information(page: Page) -> None:
    """Collapse the Project Information accordion if it is expanded."""
    project_path = page.get_by_label("Project Path")
    if project_path.is_visible():
        page.get_by_text("Project Information", exact=True).first.click()
    expect(project_path).to_be_hidden()


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

    def test_setup_tab_visible(self, page: Page):
        """Setup tab should be visible."""
        tab = page.get_by_role("tab", name="⚙️ Setup")
        expect(tab).to_be_visible()

    def test_project_path_field(self, page: Page):
        """Project path field should be visible after opening project information."""
        _expand_project_information(page)
        field = page.get_by_label("Project Path")
        expect(field).to_be_visible()

    def test_project_information_collapsed_by_default(self, page: Page):
        """Project Information should be collapsed at startup."""
        field = page.get_by_label("Project Path")
        expect(field).to_be_hidden()

    def test_project_setup_commands_visible(self, page: Page):
        """Project setup lint and test commands should be visible after expanding project info."""
        _expand_project_information(page)

        lint_cmd = page.get_by_label("Lint Command")
        expect(lint_cmd).to_be_visible()

        test_cmd = page.get_by_label("Test Command")
        expect(test_cmd).to_be_visible()

        # There should be no accordion for project setup - these should be part of the
        # project path panel directly
        accordion = page.locator(".project-setup-accordion")
        expect(accordion).to_have_count(0)

    def test_project_type_shown_in_label(self, page: Page):
        """Project type should sit inline with the project path label to save space."""
        _expand_project_information(page)
        label = page.locator("#project-path-input label")
        expect(label).to_be_visible()
        text = label.text_content()
        assert "Type:" in text

    def test_project_commands_share_row(self, page: Page):
        """Lint and test commands should sit on the same horizontal row."""
        _expand_project_information(page)
        lint_cmd = page.get_by_label("Lint Command")
        test_cmd = page.get_by_label("Test Command")
        expect(lint_cmd).to_be_visible()
        expect(test_cmd).to_be_visible()

        lint_box = lint_cmd.bounding_box()
        test_box = test_cmd.bounding_box()
        assert lint_box and test_box
        assert abs(lint_box["y"] - test_box["y"]) <= 24, "Commands should align horizontally"

    def test_command_test_buttons_align_with_headers(self, page: Page):
        """Test buttons should sit beside their corresponding command headers."""
        _expand_project_information(page)
        lint_label = page.locator(".lint-command-label")
        lint_btn = page.locator(".lint-test-btn")
        test_label = page.locator(".test-command-label")
        test_btn = page.locator(".test-command-btn")

        for element in (lint_label, lint_btn, test_label, test_btn):
            expect(element).to_be_visible()

        lint_label_box = lint_label.bounding_box()
        lint_btn_box = lint_btn.bounding_box()
        test_label_box = test_label.bounding_box()
        test_btn_box = test_btn.bounding_box()

        for label_box, btn_box in ((lint_label_box, lint_btn_box), (test_label_box, test_btn_box)):
            assert label_box and btn_box
            assert btn_box["y"] <= label_box["y"] + label_box["height"] + 4
            assert btn_box["y"] + btn_box["height"] >= label_box["y"] - 4
            assert btn_box["x"] > label_box["x"]

    def test_project_doc_paths_visible(self, page: Page):
        """Project doc path inputs should be visible for editing."""
        _expand_project_information(page)
        instructions = page.get_by_label("Agent Instructions Path")
        architecture = page.get_by_label("Architecture Doc Path")
        expect(instructions).to_be_visible()
        expect(architecture).to_be_visible()

    def test_task_description_field(self, page: Page):
        """Task description field should be present."""
        # Look for the task description multimodal textbox by its class
        # MultimodalTextbox uses a different structure than TextArea
        task_input = page.locator(".task-desc-input")
        expect(task_input).to_be_visible()

    def test_start_button_hidden(self, page: Page):
        """Standalone Start Task button should remain hidden (submit arrow starts task)."""
        button = page.locator("#start-task-btn")
        expect(button).to_be_hidden()

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
    """Test that the role_status row was removed (milestone info is now in chatbot)."""

    def test_role_config_status_removed(self, page: Page):
        """The #role-config-status element should no longer exist."""
        assert page.locator("#role-config-status").count() == 0


class TestCodingAgentLayout:
    """Ensure the coding agent selector and controls are properly laid out."""

    def test_action_row_visible_when_project_info_collapsed(self, page: Page):
        """Action row should stay visible even when project info is collapsed."""
        _collapse_project_information(page)
        status_row = page.locator("#role-status-row")
        cancel_btn = page.locator("#cancel-task-btn")
        save_btn = page.locator(".project-save-btn")
        expect(status_row).to_be_visible()
        expect(cancel_btn).to_be_visible()
        expect(save_btn).to_be_hidden()

        inside_project_accordion = page.evaluate(
            """
() => {
  const status = document.querySelector("#role-status-row");
  const accordion = document.querySelector(".project-info-accordion");
  if (!status || !accordion) return null;
  return accordion.contains(status);
}
            """
        )
        assert inside_project_accordion is False

    def test_status_row_below_config_panel(self, page: Page):
        """Action row should sit under project info, not in the right-side agent columns."""
        _expand_project_information(page)
        top_row = page.locator("#run-top-row")
        status_row = page.locator("#role-status-row")
        project_path = page.get_by_label("Project Path")
        coding_agent = page.get_by_label("Coding Agent")
        verification_agent = page.get_by_label("Verification Agent")
        cancel_btn = page.locator("#cancel-task-btn")
        expect(top_row).to_be_visible()
        expect(status_row).to_be_visible()
        expect(project_path).to_be_visible()
        expect(coding_agent).to_be_visible()
        expect(verification_agent).to_be_visible()
        expect(cancel_btn).to_be_visible()

        row_box = top_row.bounding_box()
        button_row_box = status_row.bounding_box()
        project_box = project_path.bounding_box()
        coding_box = coding_agent.bounding_box()
        verification_box = verification_agent.bounding_box()

        assert row_box and button_row_box, (
            "Missing bounding box data for layout assertions"
        )
        assert project_box and coding_box and verification_box

        # Action row should remain inside the top panel.
        assert (
            button_row_box["y"] >= row_box["y"] - 2
        ), "Button row should remain inside the top panel"
        assert (
            button_row_box["y"] <= row_box["y"] + row_box["height"] + 2
        ), "Button row should remain inside the top panel"

        # Action row should live under project info (left section), not the right columns.
        assert (
            button_row_box["y"] >= project_box["y"] + project_box["height"] - 4
        ), "Action row should be below the project info section"
        assert (
            button_row_box["x"] < coding_box["x"] - 60
        ), "Action row should be left of the coding/verification columns"
        assert (
            button_row_box["x"] < verification_box["x"] - 60
        ), "Action row should not drift into the verification column"

    def test_run_top_controls_stack_with_matching_widths(self, page: Page):
        """Model/Reasoning controls should stack under agents and action row stays with project info."""
        _expand_project_information(page)
        project_path = page.get_by_label("Project Path")
        session_log = page.locator("#session-log-btn")
        workspace = page.locator("#workspace-display")
        status_row = page.locator("#role-status-row")
        coding_agent = page.get_by_label("Coding Agent")
        coding_model = page.get_by_label("Model", exact=True)
        coding_reasoning = page.get_by_label("Reasoning Effort", exact=True)
        verification_agent = page.get_by_label("Verification Agent")
        verification_model = page.get_by_label("Verification Model")
        verification_reasoning = page.get_by_label("Verification Reasoning Effort")

        expect(project_path).to_be_visible()
        expect(session_log).to_be_visible()
        expect(workspace).to_be_visible()
        expect(status_row).to_be_visible()
        expect(coding_agent).to_be_visible()
        expect(coding_model).to_be_visible()
        expect(coding_reasoning).to_be_visible()
        expect(verification_agent).to_be_visible()
        expect(verification_model).to_be_visible()
        expect(verification_reasoning).to_be_visible()

        log_box = session_log.bounding_box()
        workspace_box = workspace.bounding_box()
        status_row_box = status_row.bounding_box()
        coding_box = coding_agent.bounding_box()
        model_box = coding_model.bounding_box()
        coding_reasoning_box = coding_reasoning.bounding_box()
        verification_box = verification_agent.bounding_box()
        verification_model_box = verification_model.bounding_box()
        verification_reasoning_box = verification_reasoning.bounding_box()

        assert (
            log_box
            and workspace_box
            and status_row_box
            and coding_box
            and model_box
            and coding_reasoning_box
            and verification_box
            and verification_model_box
            and verification_reasoning_box
        )

        assert (
            log_box["y"] >= status_row_box["y"] - 4
        ), "Session log button should align with the action row"
        assert (
            log_box["x"] >= status_row_box["x"] - 10
        ), "Session log button should sit in the action row cluster"
        assert (
            log_box["x"] < coding_box["x"] - 60
        ), "Action row cluster should be left of coding/verification columns"
        assert (
            workspace_box["x"] >= log_box["x"] - 4
        ), "Workspace label should sit with the action cluster next to session log"

        assert (
            model_box["y"] >= coding_box["y"] + coding_box["height"] - 2
        ), "Model should stack beneath Coding Agent"
        assert (
            coding_reasoning_box["y"] >= model_box["y"] + model_box["height"] - 2
        ), "Coding Reasoning should stack beneath Model"
        assert (
            verification_model_box["y"] >= verification_box["y"] + verification_box["height"] - 2
        ), "Verification Model should stack beneath Verification Agent"
        assert (
            verification_reasoning_box["y"] >= verification_model_box["y"] + verification_model_box["height"] - 2
        ), "Verification Reasoning should stack beneath Verification Model"

        assert abs(model_box["x"] - coding_box["x"]) <= 4
        assert abs(model_box["width"] - coding_box["width"]) <= 4
        assert abs(coding_reasoning_box["x"] - coding_box["x"]) <= 4
        assert abs(coding_reasoning_box["width"] - coding_box["width"]) <= 4
        assert abs(verification_model_box["x"] - verification_box["x"]) <= 4
        assert abs(verification_model_box["width"] - verification_box["width"]) <= 4
        assert abs(verification_reasoning_box["x"] - verification_box["x"]) <= 4
        assert abs(verification_reasoning_box["width"] - verification_box["width"]) <= 4

    def test_doc_paths_visible_and_action_row_under_project_info(self, page: Page):
        """Doc paths should be visible and action row should remain in the project-info area."""
        _expand_project_information(page)
        instructions = page.get_by_label("Agent Instructions Path")
        architecture = page.get_by_label("Architecture Doc Path")
        doc_paths_row = page.locator(".doc-paths-row")
        status_row = page.locator("#role-status-row")
        coding_agent = page.get_by_label("Coding Agent")

        expect(instructions).to_be_visible()
        expect(architecture).to_be_visible()
        expect(doc_paths_row).to_be_visible()
        expect(status_row).to_be_visible()
        expect(coding_agent).to_be_visible()

        action_box = status_row.bounding_box()
        coding_box = coding_agent.bounding_box()

        assert action_box and coding_box

        assert (
            action_box["x"] < coding_box["x"] - 60
        ), "Action row should stay under project info, not in the right agent panel"

    def test_cancel_button_in_status_row(self, page: Page):
        """Cancel button should remain in status row, with session log to its right."""
        cancel_btn = page.locator("#cancel-task-btn")
        session_log_btn = page.locator("#session-log-btn")
        status_row = page.locator("#role-status-row")

        expect(cancel_btn).to_be_visible()
        expect(session_log_btn).to_be_visible()
        expect(status_row).to_be_visible()

        cancel_box = cancel_btn.bounding_box()
        session_log_box = session_log_btn.bounding_box()
        status_box = status_row.bounding_box()

        assert cancel_box and session_log_box and status_box

        # Cancel and Session Log should share the same action-row baseline.
        assert abs(cancel_box["y"] - session_log_box["y"]) < 40, (
            f"Cancel button (y={cancel_box['y']}) should align with session log (y={session_log_box['y']})"
        )

        # Cancel button should be to the left of session log
        assert cancel_box["x"] < session_log_box["x"], (
            f"Cancel button (x={cancel_box['x']}) should be to the left of session log (x={session_log_box['x']})"
        )

    def test_project_save_button_in_project_info_and_dirty_only(self, page: Page):
        """Save button should live in Project Information and enable only after edits."""
        save_btn = page.locator(".project-save-btn")

        _expand_project_information(page)
        expect(save_btn).to_be_visible()
        expect(save_btn).to_be_disabled()

        lint_cmd = page.get_by_label("Lint Command")
        original = lint_cmd.input_value()
        lint_cmd.fill(f"{original} --changed")
        expect(save_btn).to_be_enabled()

        lint_cmd.fill(original)
        expect(save_btn).to_be_disabled()

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
        """Coding agent 'Model' dropdown must be visible."""
        dropdown = page.get_by_label("Model", exact=True)
        expect(dropdown).to_be_visible()

    def test_coding_reasoning_dropdown_visible(self, page: Page):
        """Coding agent 'Reasoning Effort' dropdown must be visible."""
        dropdown = page.get_by_label("Reasoning Effort", exact=True)
        expect(dropdown).to_be_visible()

    def test_verification_model_dropdown_visible(self, page: Page):
        """Verification agent 'Verification Model' dropdown must be visible."""
        dropdown = page.get_by_label("Verification Model")
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
        coding_model = page.get_by_label("Model", exact=True)
        coding_reasoning = page.get_by_label("Reasoning Effort", exact=True)
        verif_model = page.get_by_label("Verification Model")
        verif_reasoning = page.get_by_label("Verification Reasoning Effort")

        # All must be visible
        expect(coding_model).to_be_visible()
        expect(coding_reasoning).to_be_visible()
        expect(verif_model).to_be_visible()
        expect(verif_reasoning).to_be_visible()

        # Verify they are dropdown/select-like components (have options)
        # Just checking visibility is sufficient for regression prevention


class TestSetupTab:
    """Test the Setup tab functionality."""

    def test_can_switch_to_providers_tab(self, page: Page):
        """Should be able to switch to Setup tab."""
        page.get_by_role("tab", name="⚙️ Setup").click()
        time.sleep(0.5)

        # Should see setup heading
        expect(page.get_by_role("heading", name="Setup")).to_be_visible()

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
        page.get_by_role("tab", name="⚙️ Setup").click()
        usage = page.locator(".provider-usage").first
        expect(usage).to_be_visible(timeout=5000)

        text = usage.text_content() or ""
        assert text.strip(), "Usage text should not be empty"

    def test_config_panel_persists_settings(self, page: Page, temp_env):
        """Config panel should save changes immediately to config file."""
        page.get_by_role("tab", name="⚙️ Setup").click()
        config_toggle = page.get_by_role("button", name="Config")
        config_toggle.click()

        retention = page.get_by_label("Retention Days")
        retention.fill("9")
        retention.press("Enter")

        coding_dropdown = page.get_by_label("Preferred Coding Agent")
        coding_dropdown.click()
        page.get_by_role("option", name="codex-work").click()

        page.wait_for_timeout(800)
        with open(temp_env.config_path, encoding="utf-8") as f:
            config = json.load(f)

        assert config.get("cleanup_days") == 9
        assert config.get("role_assignments", {}).get("CODING") == "codex-work"

    def test_verification_model_config_shows_when_agent_selected(self, page: Page):
        """Verification model dropdown should appear once a verifier is chosen."""
        page.get_by_role("tab", name="⚙️ Setup").click()
        config_toggle = page.get_by_role("button", name="Config")
        config_toggle.click()

        verification_dropdown = page.get_by_label("Preferred Verification Agent")
        verification_dropdown.click()
        page.get_by_role("option", name="codex-work").click()

        config_panel = page.locator("#config-panel")
        model_dropdown = config_panel.get_by_label("Preferred Verification Model")
        expect(model_dropdown).to_be_visible(timeout=5000)

    def test_coding_model_config_shows_and_saves(self, page: Page, temp_env):
        """Coding model dropdown should be visible and persist the selected value."""
        page.get_by_role("tab", name="⚙️ Setup").click()
        config_toggle = page.get_by_role("button", name="Config")
        config_toggle.click()

        coding_model_dropdown = page.get_by_label("Preferred Coding Model")
        expect(coding_model_dropdown).to_be_visible(timeout=5000)

        coding_model_dropdown.click()
        page.get_by_role("option", name="claude-opus-4-20250514").click()

        page.wait_for_timeout(800)
        with open(temp_env.config_path, encoding="utf-8") as f:
            config = json.load(f)

        assert config.get("accounts", {}).get("claude-pro", {}).get("model") == "claude-opus-4-20250514"

    def test_verification_agent_none_option_available(self, page: Page):
        """Config panel 'Preferred Verification Agent' should include 'None' option."""
        page.get_by_role("tab", name="⚙️ Setup").click()
        config_toggle = page.get_by_role("button", name="Config")
        config_toggle.click()

        verification_dropdown = page.get_by_label("Preferred Verification Agent")
        verification_dropdown.click()

        # Should see the "None" option to disable verification
        none_option = page.get_by_role("option", name="None")
        expect(none_option).to_be_visible(timeout=3000)

    def test_verification_agent_none_persists_to_config(self, page: Page, temp_env):
        """Selecting 'None' for verification agent should persist to config file."""
        page.get_by_role("tab", name="⚙️ Setup").click()
        config_toggle = page.get_by_role("button", name="Config")
        config_toggle.click()

        verification_dropdown = page.get_by_label("Preferred Verification Agent")
        verification_dropdown.click()
        page.get_by_role("option", name="None").click()

        page.wait_for_timeout(800)
        with open(temp_env.config_path, encoding="utf-8") as f:
            config = json.load(f)

        # Should store the special marker "__verification_none__" to indicate no verification
        assert config.get("verification_agent") == "__verification_none__"

    def test_verification_agent_none_survives_page_reload(self, page: Page, temp_env):
        """After page reload, 'None' verification agent should still be selected."""
        # Ensure config has the VERIFICATION_NONE marker (from previous test or set directly)
        with open(temp_env.config_path, encoding="utf-8") as f:
            config = json.load(f)
        if config.get("verification_agent") != "__verification_none__":
            config["verification_agent"] = "__verification_none__"
            with open(temp_env.config_path, "w", encoding="utf-8") as f:
                json.dump(config, f)

        # Reload the page (simulating restart)
        page.reload()
        page.wait_for_timeout(2000)  # Wait for page to stabilize

        # Navigate to the config panel
        page.get_by_role("tab", name="⚙️ Setup").click()
        config_toggle = page.get_by_role("button", name="Config")
        config_toggle.click()

        # The dropdown should show "None" (not "Same as Coding Agent")
        verification_dropdown = page.get_by_label("Preferred Verification Agent")
        expect(verification_dropdown).to_be_visible(timeout=5000)

        # Get the dropdown's current text content
        dropdown_text = verification_dropdown.input_value()

        # Should be "None" (label) or "__verification_none__" (value), not "Same as Coding Agent"
        # Gradio returns the display label from input_value() for dropdowns
        assert dropdown_text in ("None", "__verification_none__"), (
            f"Expected dropdown to show 'None' but got '{dropdown_text}'. "
            "Bug: Verification agent 'None' reverted to default after page reload."
        )


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

        # Check task input content is visible for Task 2 (start uses submit arrow).
        task_inputs = page.locator(".task-desc-input")
        expect(task_inputs.last).to_be_visible(timeout=5000)

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
    // Simulate user scrolling to top: scrollTop=0
    container.scrollTop = 0;
    // Update the scroll state using the current API (keyed by parent ID)
    const parentId = box.id || box.dataset.scrollTrackId;
    if (parentId && window._liveStreamScrollState && window._liveStreamScrollState[parentId]) {
        const state = window._liveStreamScrollState[parentId];
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

    def test_no_inline_live_in_chatbot_when_dedicated_panel_active(self, page: Page):
        """Chatbot should NOT have inline-live elements when dedicated live stream panel has content.

        This test verifies the fix for the duplicate live view issue where both:
        1. An inline "CODING AI (Live)" placeholder in the chatbot, AND
        2. The dedicated live stream panel below

        were visible simultaneously during streaming. The fix removes the inline
        placeholder entirely, using only the dedicated panel for live output.
        """
        # Inject content into the dedicated live stream panel
        inject_live_stream_content(page, "<p>Active streaming content</p>")

        # Verify dedicated panel has content and is visible
        dedicated_visible = page.evaluate(
            """
() => {
    const box = document.querySelector('#live-stream-box') || document.querySelector('.live-stream-box');
    if (!box) return { found: false };
    const content = box.querySelector('.live-output-content');
    if (!content) return { found: true, hasContent: false };
    return {
        found: true,
        hasContent: content.innerHTML.includes('Active streaming'),
        visible: content.offsetParent !== null
    };
}
"""
        )
        assert dedicated_visible.get("found"), "Dedicated live stream box should exist"
        assert dedicated_visible.get("hasContent"), "Dedicated panel should have injected content"

        # Verify chatbot does NOT have inline-live elements
        inline_live_in_chatbot = page.evaluate(
            """
() => {
    const chatbot = document.querySelector('#agent-chatbot');
    if (!chatbot) return { chatbotFound: false };
    const inlineLive = chatbot.querySelectorAll('.inline-live-content, .inline-live-header');
    return {
        chatbotFound: true,
        inlineLiveCount: inlineLive.length,
        inlineLiveTexts: [...inlineLive].map(el => el.textContent.substring(0, 50))
    };
}
"""
        )
        assert inline_live_in_chatbot.get("chatbotFound"), "Chatbot should exist"
        assert inline_live_in_chatbot.get("inlineLiveCount", 0) == 0, (
            f"Chatbot should NOT have inline-live elements during streaming. "
            f"Found {inline_live_in_chatbot.get('inlineLiveCount')} elements: "
            f"{inline_live_in_chatbot.get('inlineLiveTexts')}"
        )


class TestLiveStreamScrollPreservation:
    """Ensure live view DOM patching preserves user scroll position."""

    def test_live_patch_preserves_user_scroll(self, page: Page):
        """Updating via live patch should not reset scroll when user is mid-scroll."""
        live_id = "live-scroll-test"

        trigger = page.locator(".live-patch-trigger")
        trigger.first.wait_for(state="attached", timeout=5000)
        assert trigger.count() >= 1, "Live patch trigger should be rendered in the DOM"

        before = page.evaluate(
            """
({ liveId }) => {
  const box = document.querySelector('#live-stream-box') || document.querySelector('.live-stream-box');
  const lines = Array.from({length: 200}, (_, i) => `Line ${i + 1}`).join('\\n');
  box.innerHTML = `<div class="live-output-wrapper" data-live-id="${liveId}"><div class="live-output-header">▶ CODING AI (Live Stream)</div><div class="live-output-content" style="height:420px; overflow:auto; white-space:pre">${lines}</div></div>`;
  const content = box.querySelector('.live-output-content');
  content.scrollTop = 650;
  return { top: content.scrollTop, height: content.scrollHeight };
}
""",
            {"liveId": live_id},
        )

        page.evaluate(
            """
({ liveId }) => {
  const trigger = document.querySelector('.live-patch-trigger');
  if (!trigger) return false;
  const newLines = Array.from({length: 220}, (_, i) => `Patched ${i + 1}`).join('\\n');
  const newHtml = `<div class="live-output-wrapper" data-live-id="${liveId}"><div class="live-output-header">▶ CODING AI (Live Stream)</div><div class="live-output-content" style="height:420px; overflow:auto; white-space:pre">${newLines}</div></div>`;
  const escapeHtml = (str) => str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
  trigger.innerHTML = `<div data-live-patch="${liveId}" style="display:none">${escapeHtml(newHtml)}</div>`;
  return true;
}
""",
            {"liveId": live_id},
        )

        page.wait_for_timeout(400)

        after = page.evaluate(
            """
({ liveId }) => {
  const content = document.querySelector(`[data-live-id="${liveId}"] .live-output-content`);
  if (!content) return null;
  return {
    top: content.scrollTop,
    height: content.scrollHeight,
    sample: content.innerText.slice(0, 30),
  };
}
""",
            {"liveId": live_id},
        )

        assert after is not None, "Live stream content should exist after patch"
        assert after["sample"].startswith("Patched"), f"Patched content missing: {after['sample']}"
        assert after["height"] > before["height"], "Patch should increase scrollable content height"
        assert abs(after["top"] - before["top"]) <= 5, (
            f"Scroll position should be preserved. Before={before['top']}, After={after['top']}"
        )

    def test_no_javascript_errors_during_live_patching(self, page: Page):
        """Live patching should not throw ReferenceErrors or other JS errors."""
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))

        live_id = "live-jserror-test"

        # Set up content and trigger a patch
        page.evaluate(
            """
({ liveId }) => {
  const box = document.querySelector('#live-stream-box') || document.querySelector('.live-stream-box');
  if (!box) return;
  box.innerHTML = `<div class="live-output-wrapper" data-live-id="${liveId}"><div class="live-output-content" style="height:200px; overflow:auto; white-space:pre">Initial content</div></div>`;
}
""",
            {"liveId": live_id},
        )

        page.evaluate(
            """
({ liveId }) => {
  const trigger = document.querySelector('.live-patch-trigger');
  if (!trigger) return;
  const newHtml = `<div class="live-output-wrapper" data-live-id="${liveId}"><div class="live-output-content" style="height:200px; overflow:auto; white-space:pre">Patched content line 1\\nPatched content line 2</div></div>`;
  const escapeHtml = (str) => str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  trigger.innerHTML = `<div data-live-patch="${liveId}" style="display:none">${escapeHtml(newHtml)}</div>`;
}
""",
            {"liveId": live_id},
        )

        page.wait_for_timeout(500)

        assert len(errors) == 0, (
            f"JavaScript errors during live patching: {errors}"
        )

    def test_rapid_patches_respect_user_scroll_up(self, page: Page):
        """User scroll-up should be sticky across rapid patches."""
        live_id = "live-rapid-test"

        # Create initial content and scroll to middle
        setup = page.evaluate(
            """
({ liveId }) => {
  const box = document.querySelector('#live-stream-box') || document.querySelector('.live-stream-box');
  if (!box) return null;
  const lines = Array.from({length: 200}, (_, i) => `Line ${i + 1}`).join('\\n');
  box.innerHTML = `<div class="live-output-wrapper" data-live-id="${liveId}"><div class="live-output-content" style="height:420px; overflow:auto; white-space:pre">${lines}</div></div>`;
  const content = box.querySelector('.live-output-content');
  content.scrollTop = 500;
  // Set shared state: user has scrolled up
  const parentId = box.id || box.dataset.scrollTrackId;
  if (parentId && window._liveStreamScrollState && window._liveStreamScrollState[parentId]) {
    const state = window._liveStreamScrollState[parentId];
    state.userScrolledUp = true;
    state.savedScrollTop = 500;
  }
  return { top: content.scrollTop, height: content.scrollHeight };
}
""",
            {"liveId": live_id},
        )
        assert setup is not None, "Setup should succeed"

        # Fire 10 rapid patches at ~100ms intervals
        for i in range(10):
            page.evaluate(
                """
({ liveId, iteration }) => {
  const trigger = document.querySelector('.live-patch-trigger');
  if (!trigger) return;
  const lines = Array.from({length: 200 + iteration * 5}, (_, j) => `Rapid ${iteration} line ${j + 1}`).join('\\n');
  const newHtml = `<div class="live-output-wrapper" data-live-id="${liveId}"><div class="live-output-content" style="height:420px; overflow:auto; white-space:pre">${lines}</div></div>`;
  const escapeHtml = (str) => str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  trigger.innerHTML = `<div data-live-patch="${liveId}" style="display:none">${escapeHtml(newHtml)}</div>`;
}
""",
                {"liveId": live_id, "iteration": i},
            )
            page.wait_for_timeout(100)

        page.wait_for_timeout(300)

        after = page.evaluate(
            """
({ liveId }) => {
  const content = document.querySelector(`[data-live-id="${liveId}"] .live-output-content`);
  if (!content) return null;
  return { top: content.scrollTop, height: content.scrollHeight };
}
""",
            {"liveId": live_id},
        )

        assert after is not None, "Content should exist after rapid patches"
        assert after["height"] > setup["height"], "Content should have grown"
        assert abs(after["top"] - setup["top"]) <= 20, (
            f"Scroll should stay near original position during rapid patches. "
            f"Before={setup['top']}, After={after['top']}"
        )

    def test_auto_scroll_works_when_user_at_bottom(self, page: Page):
        """Auto-scroll should follow new content when user is at the bottom."""
        live_id = "live-autoscroll-test"

        # Create content and scroll to bottom
        setup = page.evaluate(
            """
({ liveId }) => {
  const box = document.querySelector('#live-stream-box') || document.querySelector('.live-stream-box');
  if (!box) return null;
  const lines = Array.from({length: 100}, (_, i) => `Line ${i + 1}`).join('\\n');
  box.innerHTML = `<div class="live-output-wrapper" data-live-id="${liveId}"><div class="live-output-content" style="height:420px; overflow:auto; white-space:pre">${lines}</div></div>`;
  const content = box.querySelector('.live-output-content');
  content.scrollTop = content.scrollHeight;
  // Ensure state reflects at-bottom
  const parentId = box.id || box.dataset.scrollTrackId;
  if (parentId && window._liveStreamScrollState && window._liveStreamScrollState[parentId]) {
    const state = window._liveStreamScrollState[parentId];
    state.userScrolledUp = false;
    state.savedScrollTop = content.scrollTop;
  }
  return { top: content.scrollTop, height: content.scrollHeight };
}
""",
            {"liveId": live_id},
        )
        assert setup is not None, "Setup should succeed"

        # Patch with more content
        page.evaluate(
            """
({ liveId }) => {
  const trigger = document.querySelector('.live-patch-trigger');
  if (!trigger) return;
  const lines = Array.from({length: 150}, (_, i) => `Extended ${i + 1}`).join('\\n');
  const newHtml = `<div class="live-output-wrapper" data-live-id="${liveId}"><div class="live-output-content" style="height:420px; overflow:auto; white-space:pre">${lines}</div></div>`;
  const escapeHtml = (str) => str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  trigger.innerHTML = `<div data-live-patch="${liveId}" style="display:none">${escapeHtml(newHtml)}</div>`;
}
""",
            {"liveId": live_id},
        )

        page.wait_for_timeout(400)

        after = page.evaluate(
            """
({ liveId }) => {
  const content = document.querySelector(`[data-live-id="${liveId}"] .live-output-content`);
  if (!content) return null;
  const isAtBottom = content.scrollTop + content.clientHeight >= content.scrollHeight - 10;
  return { top: content.scrollTop, height: content.scrollHeight, atBottom: isAtBottom };
}
""",
            {"liveId": live_id},
        )

        assert after is not None, "Content should exist after patch"
        assert after["height"] > setup["height"], "Content should have grown"
        assert after["atBottom"], (
            f"Should auto-scroll to bottom when user was at bottom. "
            f"scrollTop={after['top']}, scrollHeight={after['height']}"
        )


class TestLiveStreamSearch:
    """Test the live stream search field functionality."""

    SEARCH_CONTENT = (
        '<p>The quick brown fox jumps over the lazy dog.</p>'
        '<p>Another line with some fox content here.</p>'
        '<p>Final line of output text.</p>'
    )

    def _inject_with_header(self, page: Page):
        """Inject content with the search bar header into the live stream box."""
        from chad.ui.gradio.web_ui import build_live_stream_html_from_pyte
        full_html = build_live_stream_html_from_pyte(self.SEARCH_CONTENT, "TEST AI")
        page.evaluate(
            """
(html) => {
    const box = document.querySelector('#live-stream-box') || document.querySelector('.live-stream-box');
    if (!box) return false;
    box.classList.add('live-stream-box');
    let node = box;
    while (node) {
        if (node.classList) {
            node.classList.remove('hide-container');
            node.classList.remove('live-stream-hidden');
        }
        if (node.style) {
            node.style.setProperty('display', 'block', 'important');
            node.style.setProperty('visibility', 'visible', 'important');
            node.style.setProperty('opacity', '1', 'important');
            node.style.setProperty('height', 'auto', 'important');
        }
        node = node.parentElement;
    }
    box.style.minHeight = '300px';
    box.innerHTML = html;
    box.scrollIntoView({ behavior: 'instant', block: 'center' });
    return true;
}
""",
            full_html,
        )
        page.wait_for_timeout(600)

    def test_search_input_visible_in_header(self, page: Page):
        """Search input should be visible when live content is present."""
        self._inject_with_header(page)
        search_input = page.locator(".live-search-input").last
        expect(search_input).to_be_visible()

    def test_search_highlights_matches(self, page: Page):
        """Typing a search term should highlight matching text."""
        self._inject_with_header(page)
        search_input = page.locator(".live-search-input").last
        search_input.fill("fox")
        page.wait_for_timeout(400)
        marks = page.locator("mark.live-search-match")
        assert marks.count() == 2, f"Expected 2 'fox' matches, got {marks.count()}"

    def test_search_count_display(self, page: Page):
        """Match count should display correctly."""
        self._inject_with_header(page)
        search_input = page.locator(".live-search-input").last
        search_input.fill("fox")
        page.wait_for_timeout(400)
        count_el = page.locator(".live-search-count").last
        assert count_el.text_content() == "1/2"

    def test_search_navigation(self, page: Page):
        """Next button should advance the current match index."""
        self._inject_with_header(page)
        search_input = page.locator(".live-search-input").last
        search_input.fill("fox")
        page.wait_for_timeout(400)
        # First match should be current
        current = page.locator("mark.live-search-match.current")
        assert current.count() == 1
        count_el = page.locator(".live-search-count").last
        assert count_el.text_content() == "1/2"
        # Click next
        next_btn = page.locator(".live-search-nav").last
        next_btn.click()
        page.wait_for_timeout(200)
        assert count_el.text_content() == "2/2"

    def test_escape_clears_search(self, page: Page):
        """Pressing Escape should clear highlights."""
        self._inject_with_header(page)
        search_input = page.locator(".live-search-input").last
        search_input.fill("fox")
        page.wait_for_timeout(400)
        marks = page.locator("mark.live-search-match")
        assert marks.count() == 2
        search_input.press("Escape")
        page.wait_for_timeout(300)
        marks_after = page.locator("mark.live-search-match")
        assert marks_after.count() == 0

    def test_navigation_scrolls_to_offscreen_match(self, page: Page):
        """Navigating to a match that is off-screen should scroll it into view."""
        # Build content with many lines so the second "NEEDLE" is far below the fold
        lines = ['<p>The NEEDLE is here on line 1.</p>']
        for i in range(2, 80):
            lines.append(f'<p>Filler line {i} with nothing interesting.</p>')
        lines.append('<p>Another NEEDLE hidden way down at the bottom.</p>')
        tall_content = '\n'.join(lines)
        from chad.ui.gradio.web_ui import build_live_stream_html_from_pyte
        full_html = build_live_stream_html_from_pyte(tall_content, "TEST AI")
        page.evaluate(
            """
(html) => {
    const box = document.querySelector('#live-stream-box') || document.querySelector('.live-stream-box');
    if (!box) return false;
    box.classList.add('live-stream-box');
    let node = box;
    while (node) {
        if (node.classList) {
            node.classList.remove('hide-container');
            node.classList.remove('live-stream-hidden');
        }
        if (node.style) {
            node.style.setProperty('display', 'block', 'important');
            node.style.setProperty('visibility', 'visible', 'important');
            node.style.setProperty('opacity', '1', 'important');
            node.style.setProperty('height', 'auto', 'important');
        }
        node = node.parentElement;
    }
    box.style.minHeight = '300px';
    box.innerHTML = html;
    box.scrollIntoView({ behavior: 'instant', block: 'center' });
    return true;
}
""",
            full_html,
        )
        page.wait_for_timeout(600)

        search_input = page.locator(".live-search-input").last
        search_input.fill("NEEDLE")
        page.wait_for_timeout(400)

        # First match should be current and near the top
        count_el = page.locator(".live-search-count").last
        assert count_el.text_content() == "1/2"

        # Record scroll position before navigating
        scroll_before = page.evaluate("""
() => {
    const box = document.querySelector('#live-stream-box') || document.querySelector('.live-stream-box');
    const content = box && box.querySelector('.live-output-content');
    return content ? content.scrollTop : null;
}
""")

        # Navigate to the second match (far below)
        next_btn = page.locator(".live-search-nav").last
        next_btn.click()
        page.wait_for_timeout(300)
        assert count_el.text_content() == "2/2"

        scroll_after = page.evaluate("""
() => {
    const box = document.querySelector('#live-stream-box') || document.querySelector('.live-stream-box');
    const content = box && box.querySelector('.live-output-content');
    return content ? content.scrollTop : null;
}
""")

        assert scroll_after is not None
        assert scroll_after > scroll_before, (
            f"Scrollbar should move down to off-screen match. "
            f"Before={scroll_before}, After={scroll_after}"
        )


class TestTUIContentRendering:
    """Test that TUI-style content (boxes, cursor positioning) renders correctly.

    These tests verify that agent CLI output with box drawing characters and
    status panels doesn't get garbled when displayed in the live stream panel.
    """

    # Simulated TUI content with box drawing - typical of codex/claude status panels
    TUI_BOX_HTML = """
<p>┌────────────────────────────────────────────────────────────────────────┐</p>
<p>│ <span style="color: rgb(97, 175, 239);">OpenAI Codex</span>                                                           │</p>
<p>├────────────────────────────────────────────────────────────────────────┤</p>
<p>│ model: gpt-5.1-code    100% context left • ? for shortcuts             │</p>
<p>│ directory: /home/user/project                                          │</p>
<p>└────────────────────────────────────────────────────────────────────────┘</p>
<p></p>
<p><span style="color: rgb(229, 192, 123);">Tip:</span> NEW! Try shell snapshotting to make your Codex faster.</p>
<p></p>
<p><span style="color: rgb(152, 195, 121);">></span> Long lines in the output view should wrap or scroll properly, not</p>
<p>  cause garbled layout with text scattered across the screen.</p>
"""

    def test_tui_box_content_readable(self, page: Page):
        """TUI box content should be readable without horizontal scrolling."""
        inject_live_stream_content(page, self.TUI_BOX_HTML)
        time.sleep(0.2)

        # Check that box drawing characters are visible
        result = verify_all_text_visible(page)
        assert result.get("allVisible", False), f"TUI content not fully visible: {result}"

    def test_tui_content_no_excessive_horizontal_scroll(self, page: Page):
        """TUI content should not require excessive horizontal scrolling."""
        inject_live_stream_content(page, self.TUI_BOX_HTML)
        time.sleep(0.2)

        # Check scroll width vs client width
        scroll_info = page.evaluate(
            """
() => {
    const box = document.querySelector('#live-stream-box') || document.querySelector('.live-stream-box');
    if (!box) return { error: 'no live-stream-box' };
    const content = box.querySelector('.live-output-content');
    if (!content) return { error: 'no live-output-content' };
    return {
        scrollWidth: content.scrollWidth,
        clientWidth: content.clientWidth,
        overflowRatio: content.scrollWidth / content.clientWidth
    };
}
"""
        )

        assert "error" not in scroll_info, f"Error: {scroll_info.get('error')}"

        # Allow some horizontal scroll but not excessive (>1.5x would be garbled)
        overflow_ratio = scroll_info.get("overflowRatio", 1.0)
        assert overflow_ratio < 1.5, (
            f"TUI content requires too much horizontal scrolling: "
            f"scrollWidth={scroll_info['scrollWidth']}, clientWidth={scroll_info['clientWidth']}, "
            f"ratio={overflow_ratio:.2f}"
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

    def test_screenshot_setup_tab(self, page: Page, tmp_path):
        """Take screenshot of Setup tab."""
        page.get_by_role("tab", name="⚙️ Setup").click()
        time.sleep(0.5)
        output = tmp_path / "setup.png"
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


class TestMergeDiffScroll:
    """Ensure merge diff uses a single horizontal scrollbar instead of per-column scrollbars."""

    def test_merge_diff_uses_single_horizontal_scrollbar(self, page: Page):
        """Long lines should scroll together via the container, not per side."""
        injected = inject_merge_diff_content(page)
        assert injected, "Failed to inject sample diff content"

        metrics = measure_diff_scrollbars(page)
        assert metrics.error is None, metrics.error

        assert metrics.container_scrollable is True, "Diff container should be horizontally scrollable"
        assert metrics.container_overflow_x in ("auto", "scroll"), "Container should manage horizontal overflow"

        assert metrics.left_overflow_x not in ("auto", "scroll"), "Left pane should not have its own scrollbar"
        assert metrics.right_overflow_x not in ("auto", "scroll"), "Right pane should not have its own scrollbar"

        assert metrics.left_scrollable is False, "Left pane should not scroll independently"
        assert metrics.right_scrollable is False, "Right pane should not scroll independently"


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
