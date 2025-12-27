from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict

from mcp.server.fastmcp import FastMCP

from .investigation_report import InvestigationReport
from .ui_playwright_runner import (
    ChadLaunchError,
    PlaywrightUnavailable,
    chad_page_session,
    delete_provider_by_name,
    get_provider_names,
    measure_add_provider_accordion,
    measure_provider_delete_button,
    screenshot_page,
)
from .visual_test_map import VISUAL_TEST_MAP, get_tests_for_file, get_tests_for_files

SERVER = FastMCP("chad-ui-playwright")
ARTIFACT_ROOT = Path(tempfile.gettempdir()) / "chad" / "mcp-playwright"


def _artifact_dir() -> Path:
    run_dir = ARTIFACT_ROOT / datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _viewport(width: int, height: int) -> Dict[str, int]:
    return {"width": width, "height": height}


def _failure(message: str) -> Dict[str, object]:
    return {"success": False, "error": message}


@SERVER.tool()
def run_ui_smoke(headless: bool = True, viewport_width: int = 1280, viewport_height: int = 900) -> Dict[str, object]:
    """Run a UI smoke check with Playwright and return screenshots + measurements."""
    try:
        artifacts = _artifact_dir()
        checks = {}
        screenshots = {}

        with chad_page_session(tab="run", headless=headless, viewport=_viewport(viewport_width, viewport_height)) as (
            page,
            _instance,
        ):
            checks["run_tab_visible"] = page.get_by_role("tab", name="🚀 Run Task").is_visible()
            checks["project_path_field"] = page.get_by_label("Project Path").is_visible()
            checks["start_button"] = page.locator("#start-task-btn").is_visible()
            screenshots["run_tab"] = str(screenshot_page(page, artifacts / "run_tab.png"))

            # Switch to providers tab and capture measurement + screenshot
            measurement = measure_provider_delete_button(page)
            checks["provider_delete_ratio"] = measurement.get("ratio")
            checks["provider_delete_fills_height"] = measurement.get("ratio", 0) >= 0.95
            screenshots["providers_tab"] = str(screenshot_page(page, artifacts / "providers_tab.png"))

        return {
            "success": True,
            "checks": checks,
            "measurements": {
                "provider_delete": {
                    **measurement,
                    "fills_height": measurement.get("ratio", 0) >= 0.95,
                }
            },
            "screenshots": screenshots,
            "artifacts_dir": str(artifacts),
        }
    except PlaywrightUnavailable as exc:
        return _failure(str(exc))
    except ChadLaunchError as exc:
        return _failure(str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        return _failure(f"Unexpected error: {exc}")


@SERVER.tool()
def screenshot(
    tab: str = "run",
    headless: bool = True,
    viewport_width: int = 1280,
    viewport_height: int = 900,
) -> Dict[str, object]:  # noqa: A002
    """Capture a screenshot of the requested tab (run/providers)."""
    normalized = tab.lower().strip()
    tab_name = "providers" if normalized.startswith("p") else "run"

    try:
        artifacts = _artifact_dir()
        filename = f"{tab_name}_tab.png"
        with chad_page_session(
            tab=tab_name,
            headless=headless,
            viewport=_viewport(viewport_width, viewport_height),
        ) as (page, _instance):
            path = screenshot_page(page, artifacts / filename)
        return {
            "success": True,
            "tab": tab_name,
            "screenshot": str(path),
            "artifacts_dir": str(artifacts),
        }
    except PlaywrightUnavailable as exc:
        return _failure(str(exc))
    except ChadLaunchError as exc:
        return _failure(str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        return _failure(f"Unexpected error: {exc}")


@SERVER.tool()
def measure_provider_delete(
    headless: bool = True, viewport_width: int = 1280, viewport_height: int = 900
) -> Dict[str, object]:
    """Measure the Providers tab delete button vs. header height."""
    try:
        artifacts = _artifact_dir()
        with chad_page_session(
            tab="providers", headless=headless, viewport=_viewport(viewport_width, viewport_height)
        ) as (page, _instance):
            measurement = measure_provider_delete_button(page)
            screenshot = screenshot_page(page, artifacts / "providers_measure.png")
        return {
            "success": True,
            "measurement": {**measurement, "fills_height": measurement.get("ratio", 0) >= 0.95},
            "screenshot": str(screenshot),
            "artifacts_dir": str(artifacts),
        }
    except PlaywrightUnavailable as exc:
        return _failure(str(exc))
    except ChadLaunchError as exc:
        return _failure(str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        return _failure(f"Unexpected error: {exc}")


@SERVER.tool()
def test_add_provider_accordion_gap(
    max_gap_px: int = 16,
    headless: bool = True,
    viewport_width: int = 1280,
    viewport_height: int = 900,
) -> Dict[str, object]:
    """Test the gap between the last provider card and Add New Provider accordion.

    The Add New Provider accordion should sit tight to the provider cards,
    not have a large gap between them. A gap larger than max_gap_px indicates
    a layout issue.

    Args:
        max_gap_px: Maximum acceptable gap in pixels (default 16px)
        headless: Run browser in headless mode
        viewport_width: Viewport width in pixels
        viewport_height: Viewport height in pixels

    Returns:
        Test results including actual gap measurement and pass/fail status
    """
    try:
        artifacts = _artifact_dir()
        with chad_page_session(
            tab="providers", headless=headless, viewport=_viewport(viewport_width, viewport_height)
        ) as (page, _instance):
            measurement = measure_add_provider_accordion(page)
            screenshot = screenshot_page(page, artifacts / "accordion_gap.png")

        gap = measurement.get("gap", 0)
        passed = gap <= max_gap_px

        return {
            "success": True,
            "test_passed": passed,
            "gap_px": gap,
            "max_allowed_px": max_gap_px,
            "font_size": measurement.get("fontSize"),
            "font_weight": measurement.get("fontWeight"),
            "message": (
                f"Gap is {gap}px (max allowed: {max_gap_px}px)"
                if passed else f"FAIL: Gap is {gap}px, exceeds max of {max_gap_px}px"
            ),
            "screenshot": str(screenshot),
            "artifacts_dir": str(artifacts),
        }
    except PlaywrightUnavailable as exc:
        return _failure(str(exc))
    except ChadLaunchError as exc:
        return _failure(str(exc))
    except Exception as exc:
        return _failure(f"Unexpected error: {exc}")


@SERVER.tool()
def list_providers(
    headless: bool = True, viewport_width: int = 1280, viewport_height: int = 900
) -> Dict[str, object]:
    """List all provider names visible in the Providers tab."""
    try:
        with chad_page_session(
            tab="providers", headless=headless, viewport=_viewport(viewport_width, viewport_height)
        ) as (page, _instance):
            names = get_provider_names(page)
        return {"success": True, "providers": names, "count": len(names)}
    except PlaywrightUnavailable as exc:
        return _failure(str(exc))
    except ChadLaunchError as exc:
        return _failure(str(exc))
    except Exception as exc:
        return _failure(f"Unexpected error: {exc}")


@SERVER.tool()
def test_delete_provider(
    provider_name: str = "mock-coding",
    headless: bool = True,
    viewport_width: int = 1280,
    viewport_height: int = 900,
) -> Dict[str, object]:
    """Test deleting a provider and report detailed results.

    This tool is used to verify the delete provider functionality works correctly.
    It will:
    1. Check if the provider exists
    2. Click the delete button (first click shows 'Confirm?')
    3. Click the 'Confirm?' button (second click deletes)
    4. Check if the provider was actually deleted
    """
    try:
        artifacts = _artifact_dir()
        with chad_page_session(
            tab="providers", headless=headless, viewport=_viewport(viewport_width, viewport_height)
        ) as (page, _instance):
            # Take screenshot before deletion
            before_screenshot = screenshot_page(page, artifacts / "before_delete.png")

            # Get providers before
            providers_before = get_provider_names(page)

            # Attempt deletion
            result = delete_provider_by_name(page, provider_name)

            # Take screenshot after deletion attempt
            after_screenshot = screenshot_page(page, artifacts / "after_delete.png")

            # Get providers after
            providers_after = get_provider_names(page)

        return {
            "success": True,
            "provider_name": result.provider_name,
            "existed_before": result.existed_before,
            "confirm_button_appeared": result.confirm_button_appeared,
            "confirm_clicked": result.confirm_clicked,
            "exists_after": result.exists_after,
            "deleted": result.deleted,
            "feedback_message": result.feedback_message,
            "providers_before": providers_before,
            "providers_after": providers_after,
            "screenshots": {
                "before": str(before_screenshot),
                "after": str(after_screenshot),
            },
            "artifacts_dir": str(artifacts),
        }
    except PlaywrightUnavailable as exc:
        return _failure(str(exc))
    except ChadLaunchError as exc:
        return _failure(str(exc))
    except Exception as exc:
        return _failure(f"Unexpected error: {exc}")


@SERVER.tool()
def test_add_provider(
    provider_type: str = "anthropic",
    headless: bool = True,
    viewport_width: int = 1280,
    viewport_height: int = 900,
) -> Dict[str, object]:
    """Test adding a new provider and check if details appear without manual refresh.

    Verifies that after adding a provider:
    1. The new provider card is visible
    2. The role dropdown is populated
    3. The model dropdown has choices
    4. No manual refresh is needed
    """
    import time
    try:
        artifacts = _artifact_dir()
        with chad_page_session(
            tab="providers", headless=headless, viewport=_viewport(viewport_width, viewport_height)
        ) as (page, _instance):
            # Get providers before
            providers_before = get_provider_names(page)
            screenshot_page(page, artifacts / "01_before_add.png")

            # Open the "Add New Provider" accordion
            accordion = page.locator("text=Add New Provider")
            accordion.click()
            time.sleep(0.5)
            screenshot_page(page, artifacts / "02_accordion_open.png")

            # Enter provider name
            name_field = page.get_by_label("Provider Name")
            test_name = f"test-{provider_type}"
            name_field.fill(test_name)
            time.sleep(0.3)

            # Select provider type (Gradio dropdown - click to open, then click option)
            type_dropdown = page.get_by_label("Provider Type")
            type_dropdown.click()
            time.sleep(0.3)
            # Click the option by text in the dropdown list
            page.locator(f"li:has-text('{provider_type}'), [role='option']:has-text('{provider_type}')").first.click()
            time.sleep(0.3)
            screenshot_page(page, artifacts / "03_form_filled.png")

            # Click Add Provider button
            add_btn = page.locator("button", has_text="Add Provider")
            add_btn.click()

            # Wait for the provider to be added (may involve browser auth popup for some)
            time.sleep(2.0)
            screenshot_page(page, artifacts / "04_after_add.png")

            # Get providers after
            providers_after = get_provider_names(page)

            # Check if the new provider card has visible details
            new_provider_card = page.locator(f"text={test_name}")
            card_visible = new_provider_card.is_visible() if new_provider_card.count() > 0 else False

            # Look for role dropdown in new card (should have CODING/MANAGEMENT options)
            role_dropdowns = page.locator("label:has-text('Role') + div select, [aria-label='Role']")
            role_dropdown_count = role_dropdowns.count()

            # Check if model dropdown has choices
            model_dropdowns = page.locator("label:has-text('Preferred Model')")
            model_dropdown_count = model_dropdowns.count()

            screenshot_page(page, artifacts / "05_final_state.png")

        return {
            "success": True,
            "test_provider_name": test_name,
            "providers_before": providers_before,
            "providers_after": providers_after,
            "new_provider_added": test_name in str(providers_after) or len(providers_after) > len(providers_before),
            "card_visible": card_visible,
            "role_dropdown_count": role_dropdown_count,
            "model_dropdown_count": model_dropdown_count,
            "details_visible_without_refresh": card_visible and role_dropdown_count > 0,
            "artifacts_dir": str(artifacts),
        }
    except PlaywrightUnavailable as exc:
        return _failure(str(exc))
    except ChadLaunchError as exc:
        return _failure(str(exc))
    except Exception as exc:
        import traceback
        return _failure(f"Unexpected error: {exc}\n{traceback.format_exc()}")


@SERVER.tool()
def test_live_stream_colors(
    headless: bool = True,
    viewport_width: int = 1280,
    viewport_height: int = 900,
) -> Dict[str, object]:
    """Inject test content into live stream to verify text colors are readable.

    This test injects mock content with various ANSI color codes to verify
    text is readable against the dark background.
    """
    try:
        artifacts = _artifact_dir()
        with chad_page_session(
            tab="run", headless=headless, viewport=_viewport(viewport_width, viewport_height)
        ) as (page, _instance):
            # Take initial screenshot
            screenshot_page(page, artifacts / "live_stream_initial.png")

            # Inject mock content with various ANSI colors using JavaScript
            # This simulates what the live stream would look like with agent output
            test_html = """
            <div class="live-output-header">▶ CODING AI (Live Stream)</div>
            <div class="live-output-content">
                <span style="color:#9ca3af">Dim grey text (ANSI 90) - should be readable</span><br>
                <span style="color:#d4d4d4">Light grey text (ANSI 37) - should be readable</span><br>
                <span style="color:#e06c75">Red text (ANSI 31)</span><br>
                <span style="color:#98c379">Green text (ANSI 32)</span><br>
                <span style="color:#e5c07b">Yellow text (ANSI 33)</span><br>
                <span style="color:#61afef">Blue text (ANSI 34)</span><br>
                <span style="color:#c678dd">Magenta text (ANSI 35)</span><br>
                <span style="color:#56b6c2">Cyan text (ANSI 36)</span><br>
                <span style="font-weight:bold">Bold text</span><br>
                <span>Default text without color</span><br>
                Regular unformatted text line<br>
                <span style="color:#6b7280">Black text (ANSI 30) - now grey for visibility</span><br>
                <span style="color:rgb(92, 99, 112)">Legacy dark grey - CSS should override this!</span>
            </div>
            """

            # Inject the test content into the live stream box
            page.evaluate(f'''
                const liveBox = document.querySelector('#live-stream-box');
                if (liveBox) {{
                    liveBox.innerHTML = `{test_html}`;
                }}
            ''')

            import time
            time.sleep(0.5)
            screenshot_page(page, artifacts / "live_stream_with_colors.png")

            # Check computed styles for readability
            dim_grey_readable = page.evaluate('''
                () => {
                    const el = document.querySelector('#live-stream-box .live-output-content span');
                    if (!el) return null;
                    const style = window.getComputedStyle(el);
                    return style.color;
                }
            ''')

        return {
            "success": True,
            "dim_grey_color": dim_grey_readable,
            "screenshot": str(artifacts / "live_stream_with_colors.png"),
            "artifacts_dir": str(artifacts),
            "note": "Check screenshot to verify all colors are readable on dark background",
        }
    except PlaywrightUnavailable as exc:
        return _failure(str(exc))
    except ChadLaunchError as exc:
        return _failure(str(exc))
    except Exception as exc:
        import traceback
        return _failure(f"Unexpected error: {exc}\n{traceback.format_exc()}")


@SERVER.tool()
def test_scroll_preservation(
    headless: bool = True,
    viewport_width: int = 1280,
    viewport_height: int = 900,
) -> Dict[str, object]:
    """Test that scroll position is preserved when new content is added.

    This test:
    1. Injects content into the live stream
    2. Scrolls up to a specific position
    3. Injects more content (simulating streaming updates)
    4. Checks if scroll position was preserved
    """
    import time
    try:
        artifacts = _artifact_dir()
        with chad_page_session(
            tab="run", headless=headless, viewport=_viewport(viewport_width, viewport_height)
        ) as (page, _instance):
            # Inject initial content to make scrollable
            initial_lines = "<br>".join([f"Line {i}: Initial content for scroll testing" for i in range(50)])
            page.evaluate(f'''
                const liveBox = document.querySelector('#live-stream-box');
                if (liveBox) {{
                    liveBox.innerHTML = `
                        <div class="live-output-header">▶ SCROLL TEST</div>
                        <div class="live-output-content" style="max-height: 300px; overflow-y: auto;">
                            {initial_lines}
                        </div>
                    `;
                }}
            ''')
            time.sleep(0.5)

            # Get the scroll container and scroll to middle
            scroll_info_before = page.evaluate('''
                () => {
                    const container = document.querySelector('#live-stream-box .live-output-content');
                    if (!container) return { error: "Container not found" };
                    // Scroll to ~middle
                    container.scrollTop = 200;
                    return {
                        scrollTop: container.scrollTop,
                        scrollHeight: container.scrollHeight,
                        clientHeight: container.clientHeight
                    };
                }
            ''')
            time.sleep(0.3)
            screenshot_page(page, artifacts / "01_scrolled_to_middle.png")

            # Now inject more content (simulating streaming update)
            more_lines = "<br>".join([f"Line {i}: NEW content added after scroll" for i in range(60, 70)])
            page.evaluate(f'''
                const container = document.querySelector('#live-stream-box .live-output-content');
                if (container) {{
                    container.innerHTML += `<br>{more_lines}`;
                }}
            ''')
            time.sleep(0.3)

            # Check scroll position after content update
            scroll_info_after = page.evaluate('''
                () => {
                    const container = document.querySelector('#live-stream-box .live-output-content');
                    if (!container) return { error: "Container not found" };
                    return {
                        scrollTop: container.scrollTop,
                        scrollHeight: container.scrollHeight,
                        clientHeight: container.clientHeight
                    };
                }
            ''')
            screenshot_page(page, artifacts / "02_after_content_added.png")

            # Calculate if position was preserved
            scroll_preserved = abs(scroll_info_after.get("scrollTop", 0) - scroll_info_before.get("scrollTop", 0)) < 50

        return {
            "success": True,
            "scroll_before": scroll_info_before,
            "scroll_after": scroll_info_after,
            "scroll_preserved": scroll_preserved,
            "artifacts_dir": str(artifacts),
        }
    except PlaywrightUnavailable as exc:
        return _failure(str(exc))
    except ChadLaunchError as exc:
        return _failure(str(exc))
    except Exception as exc:
        import traceback
        return _failure(f"Unexpected error: {exc}\n{traceback.format_exc()}")


def _project_root() -> Path:
    """Get the project root directory."""
    return Path(__file__).parents[2]


@SERVER.tool()
def run_tests_for_file(file_path: str, headless: bool = True) -> Dict[str, object]:
    """Run visual tests that cover a specific source file.

    Looks up which visual tests are associated with the given source file
    using the annotation system, then runs those tests.

    Args:
        file_path: Path to the modified source file (e.g., 'src/chad/provider_ui.py')
        headless: Run browser in headless mode
    """
    try:
        tests = get_tests_for_file(file_path)
        if not tests:
            return {
                "success": True,
                "message": f"No visual tests mapped to {file_path}",
                "tests_run": [],
                "file": file_path,
            }

        test_filter = " or ".join(tests)
        env = {**os.environ, "PYTHONPATH": str(_project_root() / "src")}
        if not headless:
            env["HEADED"] = "1"

        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_ui_integration.py", "-v", "--tb=short", "-k", test_filter],
            capture_output=True,
            text=True,
            cwd=str(_project_root()),
            env=env,
        )

        return {
            "success": result.returncode == 0,
            "file": file_path,
            "tests_run": tests,
            "test_filter": test_filter,
            "stdout": result.stdout[-5000:] if len(result.stdout) > 5000 else result.stdout,
            "stderr": result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr,
            "return_code": result.returncode,
        }
    except Exception as exc:
        return _failure(f"Error running tests: {exc}")


@SERVER.tool()
def run_tests_for_modified_files(headless: bool = True) -> Dict[str, object]:
    """Run visual tests for all files modified in the current git working tree.

    Uses `git status` to find modified files, looks up their associated
    visual tests, and runs them.
    """
    try:
        git_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=str(_project_root()),
        )

        if git_result.returncode != 0:
            return _failure("Failed to get git status")

        modified_files = []
        for line in git_result.stdout.split("\n"):
            line = line.rstrip()  # Only strip trailing whitespace, preserve leading status chars
            if line and len(line) > 3:
                filename = line[3:].strip()
                if filename.startswith("src/chad/") and filename.endswith(".py"):
                    modified_files.append(filename)

        if not modified_files:
            return {
                "success": True,
                "message": "No modified Python files in src/chad/",
                "modified_files": [],
                "tests_run": [],
            }

        tests = get_tests_for_files(modified_files)
        if not tests:
            return {
                "success": True,
                "message": "No visual tests mapped to modified files",
                "modified_files": modified_files,
                "tests_run": [],
            }

        test_filter = " or ".join(tests)
        env = {**os.environ, "PYTHONPATH": str(_project_root() / "src")}

        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_ui_integration.py", "-v", "--tb=short", "-k", test_filter],
            capture_output=True,
            text=True,
            cwd=str(_project_root()),
            env=env,
        )

        return {
            "success": result.returncode == 0,
            "modified_files": modified_files,
            "tests_run": tests,
            "test_filter": test_filter,
            "stdout": result.stdout[-5000:] if len(result.stdout) > 5000 else result.stdout,
            "stderr": result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr,
            "return_code": result.returncode,
        }
    except Exception as exc:
        return _failure(f"Error: {exc}")


@SERVER.tool()
def run_ci_tests(include_visual: bool = False) -> Dict[str, object]:
    """Run the full GitHub Actions test suite.

    Args:
        include_visual: If True, include visual tests (slow).
                       If False (default), exclude visual tests.
    """
    try:
        cmd = [sys.executable, "-m", "pytest", "-v", "--tb=short"]
        if not include_visual:
            cmd.extend(["-m", "not visual"])

        env = {**os.environ, "PYTHONPATH": str(_project_root() / "src")}

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(_project_root()),
            env=env,
        )

        return {
            "success": result.returncode == 0,
            "include_visual": include_visual,
            "stdout": result.stdout[-8000:] if len(result.stdout) > 8000 else result.stdout,
            "stderr": result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr,
            "return_code": result.returncode,
        }
    except Exception as exc:
        return _failure(f"Error: {exc}")


@SERVER.tool()
def verify_all_tests_pass() -> Dict[str, object]:
    """Run complete verification before completing an issue.

    Runs:
    1. Linting (flake8)
    2. Unit tests (excluding visual)
    3. Visual tests for modified files only
    """
    try:
        results: Dict[str, object] = {"phases": {}}
        project_root = _project_root()
        env = {**os.environ, "PYTHONPATH": str(project_root / "src")}

        # Phase 1: Lint
        lint_result = subprocess.run(
            [sys.executable, "-m", "flake8", "."],
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        results["phases"]["lint"] = {  # type: ignore[index]
            "success": lint_result.returncode == 0,
            "output": lint_result.stdout[-2000:] if lint_result.stdout else "",
            "errors": lint_result.stderr[-1000:] if lint_result.stderr else "",
        }

        if lint_result.returncode != 0:
            results["success"] = False
            results["failed_phase"] = "lint"
            return results

        # Phase 2: Unit tests (excluding visual)
        unit_result = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "--tb=short", "-m", "not visual"],
            capture_output=True,
            text=True,
            cwd=str(project_root),
            env=env,
        )
        results["phases"]["unit_tests"] = {  # type: ignore[index]
            "success": unit_result.returncode == 0,
            "output": unit_result.stdout[-4000:] if len(unit_result.stdout) > 4000 else unit_result.stdout,
        }

        if unit_result.returncode != 0:
            results["success"] = False
            results["failed_phase"] = "unit_tests"
            return results

        # Phase 3: Visual tests for modified files
        visual_result = run_tests_for_modified_files(headless=True)
        results["phases"]["visual_tests"] = visual_result  # type: ignore[index]

        if not visual_result.get("success", False):
            results["success"] = False
            results["failed_phase"] = "visual_tests"
            return results

        results["success"] = True
        results["message"] = "All verification phases passed"
        return results

    except Exception as exc:
        return _failure(f"Verification error: {exc}")


@SERVER.tool()
def list_visual_test_mappings() -> Dict[str, object]:
    """List all source file to visual test mappings.

    Returns the complete annotation registry showing which source files
    are covered by which visual tests.
    """
    return {
        "success": True,
        "mappings": VISUAL_TEST_MAP,
        "total_mappings": len(VISUAL_TEST_MAP),
    }


@SERVER.tool()
def capture_visual_change(
    label: str,
    tab: str = "providers",
    issue_id: str = "",
    headless: bool = True,
    viewport_width: int = 1280,
    viewport_height: int = 900,
) -> Dict[str, object]:
    """Capture a screenshot documenting a visual change (before or after).

    IMPORTANT: Agents working on visual issues MUST use this tool to:
    1. Take a "before" screenshot BEFORE making any changes
    2. Take an "after" screenshot AFTER making changes
    3. Report both screenshot paths to the user

    The screenshots are saved to a timestamped directory in /tmp/chad/visual-changes/
    with descriptive filenames including the label and issue ID.

    Args:
        label: Descriptive label like "before" or "after" (required)
        tab: Which tab to screenshot ("run" or "providers")
        issue_id: Optional issue/ticket ID for organization
        headless: Run browser in headless mode
        viewport_width: Viewport width in pixels
        viewport_height: Viewport height in pixels

    Returns:
        Screenshot path and artifacts directory for reporting to user

    Example workflow:
        1. capture_visual_change(label="before", tab="providers", issue_id="gap-fix")
        2. Make code changes to fix the issue
        3. capture_visual_change(label="after", tab="providers", issue_id="gap-fix")
        4. Report both paths to user in summary
    """
    try:
        # Create directory structure: /tmp/chad/visual-changes/YYYYMMDD_HHMMSS/
        base_dir = Path(tempfile.gettempdir()) / "chad" / "visual-changes"
        run_dir = base_dir / datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)

        # Build filename: {issue_id}_{label}_{tab}.png or {label}_{tab}.png
        parts = []
        if issue_id:
            parts.append(issue_id.replace(" ", "-").replace("/", "-"))
        parts.append(label.replace(" ", "-"))
        parts.append(tab)
        filename = "_".join(parts) + ".png"

        normalized_tab = "providers" if tab.lower().startswith("p") else "run"

        with chad_page_session(
            tab=normalized_tab,
            headless=headless,
            viewport=_viewport(viewport_width, viewport_height),
        ) as (page, _instance):
            screenshot_path = screenshot_page(page, run_dir / filename)

        return {
            "success": True,
            "label": label,
            "tab": normalized_tab,
            "issue_id": issue_id or "(none)",
            "screenshot": str(screenshot_path),
            "artifacts_dir": str(run_dir),
            "message": f"Screenshot saved: {screenshot_path}",
            "reminder": "Remember to take both 'before' and 'after' screenshots and report paths to user!",
        }
    except PlaywrightUnavailable as exc:
        return _failure(str(exc))
    except ChadLaunchError as exc:
        return _failure(str(exc))
    except Exception as exc:
        return _failure(f"Unexpected error: {exc}")


# =============================================================================
# Investigation Report Tools
# =============================================================================
# These tools help agents track their debugging process in a structured way.
# Agents MUST use these tools when working on issues.


@SERVER.tool()
def create_investigation(request: str, issue_id: str = "") -> Dict[str, object]:
    """Create a new investigation report. REQUIRED at the start of any debugging task.

    This initializes a structured report file that tracks your entire investigation
    process. The report is automatically saved to /tmp/chad/investigations/.

    Args:
        request: Description of what you're investigating/fixing
        issue_id: Optional GitHub issue number or identifier

    Returns:
        investigation_id: Use this ID for all subsequent investigation tool calls

    IMPORTANT: After calling this, you MUST:
    - Call add_finding() for EVERY discovery (test results, web searches, etc.)
    - Call record_fix() when you implement a solution
    - Call add_post_incident_analysis() before completing the task
    """
    try:
        report = InvestigationReport()
        report.set_request(request, issue_id)
        return {
            "success": True,
            "investigation_id": report.id,
            "file_path": str(report.file_path),
            "message": "Investigation created. Use this ID for all subsequent calls.",
            "next_steps": [
                "Call add_hypothesis() for each theory about the cause",
                "Call add_finding() after EVERY discovery (tests, searches, code review)",
                "For visual issues: call capture_visual_change() then set_screenshots()",
            ],
        }
    except Exception as exc:
        return _failure(f"Failed to create investigation: {exc}")


@SERVER.tool()
def add_hypothesis(investigation_id: str, description: str) -> Dict[str, object]:
    """Add a theory about the cause of the issue.

    Record your hypotheses so you can systematically test and eliminate them.
    Each hypothesis can be linked to findings that support or reject it.

    Args:
        investigation_id: The ID returned by create_investigation
        description: Your theory about what's causing the issue
    """
    try:
        report = InvestigationReport(investigation_id)
        hypothesis_id = report.add_hypothesis(description)
        summary = report.get_summary()
        return {
            "success": True,
            "hypothesis_id": hypothesis_id,
            "active_hypotheses": summary["active_hypotheses"],
            "message": f"Hypothesis #{hypothesis_id} added. Use add_finding() to record evidence.",
        }
    except FileNotFoundError:
        return _failure(f"Investigation {investigation_id} not found")
    except Exception as exc:
        return _failure(f"Failed to add hypothesis: {exc}")


@SERVER.tool()
def add_finding(
    investigation_id: str,
    source: str,
    content: str,
    hypothesis_id: int = 0,
    verdict: str = "inconclusive",
    notes: str = "",
) -> Dict[str, object]:
    """Record a finding from your investigation. REQUIRED for every discovery.

    Call this after EVERY piece of evidence you gather:
    - After running unit tests
    - After web searches
    - After using any tool
    - After reviewing code

    Args:
        investigation_id: The ID returned by create_investigation
        source: One of: web_search, unit_test, tool_use, code_review, screenshot_analysis
        content: What you discovered
        hypothesis_id: Which hypothesis this relates to (0 if none)
        verdict: Does this support/reject a hypothesis? (supports, rejects, inconclusive)
        notes: Additional context or next steps

    Returns:
        Summary of current investigation state
    """
    try:
        report = InvestigationReport(investigation_id)
        finding_id = report.add_finding(
            source=source,  # type: ignore
            content=content,
            hypothesis_id=hypothesis_id if hypothesis_id > 0 else None,
            verdict=verdict,  # type: ignore
            notes=notes,
        )
        summary = report.get_summary()
        return {
            "success": True,
            "finding_id": finding_id,
            "total_findings": summary["total_findings"],
            "open_findings": summary["open_findings"],
            "active_hypotheses": summary["active_hypotheses"],
            "message": f"Finding #{finding_id} recorded.",
        }
    except FileNotFoundError:
        return _failure(f"Investigation {investigation_id} not found")
    except Exception as exc:
        return _failure(f"Failed to add finding: {exc}")


@SERVER.tool()
def update_finding_status(
    investigation_id: str, finding_id: int, status: str
) -> Dict[str, object]:
    """Update a finding's status (open, resolved, rejected_approach).

    Use this to mark findings as resolved when you've addressed them,
    or rejected_approach when the finding led to a dead end.

    Args:
        investigation_id: The ID returned by create_investigation
        finding_id: The finding to update
        status: New status (open, resolved, rejected_approach)
    """
    try:
        report = InvestigationReport(investigation_id)
        if report.update_finding_status(finding_id, status):  # type: ignore
            return {"success": True, "message": f"Finding #{finding_id} marked as {status}"}
        return _failure(f"Finding #{finding_id} not found")
    except FileNotFoundError:
        return _failure(f"Investigation {investigation_id} not found")
    except Exception as exc:
        return _failure(f"Failed to update finding: {exc}")


@SERVER.tool()
def update_hypothesis_status(
    investigation_id: str, hypothesis_id: int, status: str
) -> Dict[str, object]:
    """Update a hypothesis status (active, confirmed, rejected).

    Mark hypotheses as confirmed when evidence proves them correct,
    or rejected when evidence disproves them.

    Args:
        investigation_id: The ID returned by create_investigation
        hypothesis_id: The hypothesis to update
        status: New status (active, confirmed, rejected)
    """
    try:
        report = InvestigationReport(investigation_id)
        if report.update_hypothesis_status(hypothesis_id, status):  # type: ignore
            return {"success": True, "message": f"Hypothesis #{hypothesis_id} marked as {status}"}
        return _failure(f"Hypothesis #{hypothesis_id} not found")
    except FileNotFoundError:
        return _failure(f"Investigation {investigation_id} not found")
    except Exception as exc:
        return _failure(f"Failed to update hypothesis: {exc}")


@SERVER.tool()
def mark_approach_rejected(
    investigation_id: str,
    description: str,
    why_rejected: str,
    finding_ids: str = "",
) -> Dict[str, object]:
    """Mark a failed approach as rejected to reduce context pollution.

    When an approach doesn't work, call this to archive it. This helps
    future agents (or yourself after context refresh) avoid repeating
    the same failed attempts.

    Args:
        investigation_id: The ID returned by create_investigation
        description: What approach was tried
        why_rejected: Why it didn't work
        finding_ids: Comma-separated list of related finding IDs (e.g., "1,2,3")
    """
    try:
        report = InvestigationReport(investigation_id)
        ids = [int(x.strip()) for x in finding_ids.split(",") if x.strip()]
        report.mark_approach_rejected(description, why_rejected, ids)
        summary = report.get_summary()
        return {
            "success": True,
            "rejected_approaches": summary["rejected_approaches"],
            "message": "Approach marked as rejected. Context cleaned for future reference.",
        }
    except FileNotFoundError:
        return _failure(f"Investigation {investigation_id} not found")
    except Exception as exc:
        return _failure(f"Failed to mark approach rejected: {exc}")


@SERVER.tool()
def set_screenshots(
    investigation_id: str,
    before: str = "",
    after: str = "",
) -> Dict[str, object]:
    """Set before/after screenshot paths in the investigation report.

    Call this after using capture_visual_change() to link the screenshots
    to your investigation.

    Args:
        investigation_id: The ID returned by create_investigation
        before: Path to "before" screenshot (from capture_visual_change)
        after: Path to "after" screenshot (from capture_visual_change)
    """
    try:
        report = InvestigationReport(investigation_id)
        report.set_screenshots(
            before=before if before else None,
            after=after if after else None,
        )
        return {
            "success": True,
            "screenshots": report.get_summary()["screenshots"],
            "message": "Screenshot paths recorded in investigation.",
        }
    except FileNotFoundError:
        return _failure(f"Investigation {investigation_id} not found")
    except Exception as exc:
        return _failure(f"Failed to set screenshots: {exc}")


@SERVER.tool()
def add_test_design(
    investigation_id: str,
    name: str,
    file_path: str,
    purpose: str,
    framework_gap: str = "",
) -> Dict[str, object]:
    """Record a test that was designed during investigation.

    Document tests you created and any gaps in the existing test framework
    that you discovered.

    Args:
        investigation_id: The ID returned by create_investigation
        name: Test function name (e.g., test_provider_gap)
        file_path: Path to test file (e.g., tests/test_ui_integration.py)
        purpose: Why this test was needed
        framework_gap: Optional - describe any test framework limitation found
    """
    try:
        report = InvestigationReport(investigation_id)
        report.add_test_design(
            name=name,
            file_path=file_path,
            purpose=purpose,
            framework_gap=framework_gap if framework_gap else None,
        )
        summary = report.get_summary()
        return {
            "success": True,
            "tests_designed": summary["tests_designed"],
            "message": f"Test '{name}' recorded in investigation.",
        }
    except FileNotFoundError:
        return _failure(f"Investigation {investigation_id} not found")
    except Exception as exc:
        return _failure(f"Failed to add test design: {exc}")


@SERVER.tool()
def record_fix(
    investigation_id: str,
    description: str,
    files_modified: str,
    test_changes: str = "",
) -> Dict[str, object]:
    """Record the fix that was implemented. REQUIRED before completing task.

    Document your solution so it's clear what was changed and why.

    Args:
        investigation_id: The ID returned by create_investigation
        description: What fix was applied and why it works
        files_modified: Comma-separated list of files changed
        test_changes: Comma-separated list of test changes made

    IMPORTANT: After calling this, you MUST call add_post_incident_analysis()
    """
    try:
        report = InvestigationReport(investigation_id)
        files = [f.strip() for f in files_modified.split(",") if f.strip()]
        tests = [t.strip() for t in test_changes.split(",") if t.strip()]
        report.record_fix(description, files, tests)
        return {
            "success": True,
            "message": "Fix recorded.",
            "next_step": "REQUIRED: Call add_post_incident_analysis() to complete the investigation.",
        }
    except FileNotFoundError:
        return _failure(f"Investigation {investigation_id} not found")
    except Exception as exc:
        return _failure(f"Failed to record fix: {exc}")


@SERVER.tool()
def add_post_incident_analysis(investigation_id: str, analysis: str) -> Dict[str, object]:
    """Add hypothetical failure analysis. REQUIRED before completing task.

    Write as if you failed: what would the next agent need to know to
    avoid repeating your mistakes? This is critical for knowledge transfer.

    Args:
        investigation_id: The ID returned by create_investigation
        analysis: What would need to happen if another agent takes over

    Returns:
        Complete investigation summary for your final report to user
    """
    try:
        report = InvestigationReport(investigation_id)
        report.add_post_incident_analysis(analysis)
        summary = report.get_summary()
        return {
            "success": True,
            "investigation_complete": summary["is_complete"],
            "summary": summary,
            "file_path": str(report.file_path),
            "message": "Investigation complete. Report to user with summary and screenshot paths.",
        }
    except FileNotFoundError:
        return _failure(f"Investigation {investigation_id} not found")
    except Exception as exc:
        return _failure(f"Failed to add analysis: {exc}")


@SERVER.tool()
def get_investigation_summary(investigation_id: str) -> Dict[str, object]:
    """Get a compact summary of the investigation for context refresh.

    Use this when you need to recall the current state of your investigation
    without re-reading all the details.

    Args:
        investigation_id: The ID returned by create_investigation
    """
    try:
        report = InvestigationReport(investigation_id)
        return {
            "success": True,
            **report.get_summary(),
        }
    except FileNotFoundError:
        return _failure(f"Investigation {investigation_id} not found")
    except Exception as exc:
        return _failure(f"Failed to get summary: {exc}")


@SERVER.tool()
def get_investigation_full(investigation_id: str) -> Dict[str, object]:
    """Get the complete investigation report with all details.

    Use this when you need the full context of all hypotheses, findings,
    and rejected approaches.

    Args:
        investigation_id: The ID returned by create_investigation
    """
    try:
        report = InvestigationReport(investigation_id)
        return {
            "success": True,
            "report": report.get_full_report(),
        }
    except FileNotFoundError:
        return _failure(f"Investigation {investigation_id} not found")
    except Exception as exc:
        return _failure(f"Failed to get report: {exc}")


@SERVER.tool()
def list_investigations() -> Dict[str, object]:
    """List all investigation reports.

    Useful for finding previous investigations or resuming work.
    """
    try:
        investigations = InvestigationReport.list_investigations()
        return {
            "success": True,
            "investigations": investigations,
            "count": len(investigations),
        }
    except Exception as exc:
        return _failure(f"Failed to list investigations: {exc}")


if __name__ == "__main__":
    SERVER.run()
