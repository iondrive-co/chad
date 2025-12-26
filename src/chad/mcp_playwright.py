from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict

from mcp.server.fastmcp import FastMCPServer

from .ui_playwright_runner import (
    ChadLaunchError,
    PlaywrightUnavailable,
    chad_page_session,
    delete_provider_by_name,
    get_provider_names,
    measure_provider_delete_button,
    screenshot_page,
)

SERVER = FastMCPServer("chad-ui-playwright")
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


if __name__ == "__main__":
    SERVER.run()
