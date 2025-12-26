from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict

from mcp.server.fastmcp import FastMCP

from .ui_playwright_runner import (
    ChadLaunchError,
    PlaywrightUnavailable,
    chad_page_session,
    delete_provider_by_name,
    get_provider_names,
    measure_provider_delete_button,
    screenshot_page,
)

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


if __name__ == "__main__":
    SERVER.run()
