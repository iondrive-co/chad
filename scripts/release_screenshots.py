#!/usr/bin/env python3
"""Generate release screenshots for README documentation.

This script captures the three main views for the README carousel:
1. providers-tab.png - Full providers view with multiple accounts
2. run-task-input.png - Task input panel (top section)
3. settings.png - Settings pane with action rules

Usage:
    python scripts/release_screenshots.py

The screenshots are saved to docs/ and should be committed to the repo.
"""

import os
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chad.util.verification.ui_runner import (  # noqa: E402
    ChadLaunchError,
    PlaywrightUnavailable,
    create_temp_env,
    open_playwright_page,
    start_chad,
    stop_chad,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = PROJECT_ROOT / "docs"


def screenshot_element(page, selector: str, output_path: Path) -> Path:
    """Screenshot a specific element by CSS selector."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    element = page.locator(selector)
    element.wait_for(state="visible", timeout=10000)
    element.screenshot(path=os.fspath(output_path))
    return output_path


def screenshot_page(page, output_path: Path) -> Path:
    """Screenshot the full page."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=os.fspath(output_path))
    return output_path


def inject_followup_visible(page):
    """Make the follow-up input row visible for the conversation screenshot."""
    page.evaluate(
        """
    () => {
        // Show the follow-up row - handle visibility
        const followupRow = document.getElementById('followup-row');
        if (followupRow) {
            followupRow.style.setProperty('display', 'flex', 'important');
            followupRow.style.visibility = 'visible';
            followupRow.style.opacity = '1';
            followupRow.classList.remove('hidden', 'hide', 'invisible');

            // Show parent wrappers that might be hidden
            let parent = followupRow.parentElement;
            while (parent && parent.id !== 'component-0') {
                parent.style.display = '';
                parent.classList.remove('hidden', 'hide');
                parent = parent.parentElement;
            }
        }

        // Add text to follow-up input
        const followupInput = document.getElementById('followup-input');
        if (followupInput) {
            followupInput.style.display = 'block';
            const textarea = followupInput.querySelector('textarea');
            if (textarea) {
                textarea.value = 'Now add unit tests for the new capacity tracking feature';
                textarea.dispatchEvent(new Event('input', { bubbles: true }));
            }
        }

        // Enable send button
        const sendBtn = document.getElementById('send-followup-btn');
        if (sendBtn) {
            sendBtn.disabled = false;
            sendBtn.classList.remove('disabled');
            sendBtn.style.display = 'block';
        }

        // Scroll to show follow-up
        if (followupRow) {
            followupRow.scrollIntoView({ behavior: 'instant', block: 'end' });
        }
    }
    """
    )


def fill_task_form(page):
    """Fill the task form with realistic data for the screenshot."""
    # Create a session first — the task form only appears when one is selected
    page.locator("button:has-text('New Session')").click()
    page.wait_for_timeout(2000)

    # The AccountPicker auto-selects the CODING-role account (claude-pro).
    # Wait for the coding agent select to have a selected value.
    page.wait_for_timeout(1000)

    # Fill task description
    page.locator(".task-form textarea").fill(
        "Add a REST API endpoint for user profile updates with "
        "validation, rate limiting, and comprehensive test coverage"
    )

    # Wait for model override dropdown to appear (async fetch)
    page.wait_for_timeout(1000)

    # Select model override for coding agent
    model_select = page.locator(".task-form > label:has-text('Model Override') select")
    if model_select.count() > 0:
        model_select.select_option(label="claude-opus-4-20250514")

    # Enable verification
    page.locator(".verification-section input[type='checkbox']").check()
    page.wait_for_timeout(500)

    # Select verification agent (codex-work)
    verification_select = page.locator(
        ".verification-section label:has-text('Verification Agent') select"
    )
    if verification_select.count() > 0:
        verification_select.select_option(label="codex-work (openai / o3-pro)")

    # Wait for verification models and reasoning to appear
    page.wait_for_timeout(1000)

    # Select verification reasoning
    reasoning_select = page.locator(
        ".verification-section label:has-text('Verification Reasoning') select"
    )
    if reasoning_select.count() > 0:
        reasoning_select.select_option(value="high")


def main():
    print("=" * 60)
    print("Generating Release Screenshots")
    print("=" * 60)

    print("\nCreating temporary Chad environment with fixtures...")
    env = create_temp_env(screenshot_mode=True)

    try:
        print("Starting Chad server...")
        instance = start_chad(env)
        print(f"Chad running on port {instance.port}")

        viewport_large = {"width": 1280, "height": 900}
        viewport_medium = {"width": 1280, "height": 800}

        # Screenshot 1: Providers tab
        print("\n[1/3] Capturing providers tab...")
        output_path = DOCS_DIR / "screenshot-providers.png"
        with open_playwright_page(
            instance.port,
            tab="providers",
            headless=True,
            viewport=viewport_large,
            color_scheme="light",
            render_delay=2.0,
        ) as page:
            screenshot_page(page, output_path)
            print(f"  Saved: {output_path}")

        # Screenshot 2: Run task input (top section)
        print("\n[2/3] Capturing task input panel...")
        output_path = DOCS_DIR / "screenshot-task-input.png"
        with open_playwright_page(
            instance.port,
            tab="chat",
            headless=True,
            viewport=viewport_medium,
            color_scheme="light",
            render_delay=2.0,
        ) as page:
            fill_task_form(page)
            page.wait_for_timeout(500)
            screenshot_page(page, output_path)
            print(f"  Saved: {output_path}")

        # Screenshot 3: Settings tab
        print("\n[3/3] Capturing settings tab...")
        output_path = DOCS_DIR / "screenshot-settings.png"
        with open_playwright_page(
            instance.port,
            tab="settings",
            headless=True,
            viewport=viewport_medium,
            color_scheme="light",
            render_delay=2.0,
        ) as page:
            screenshot_page(page, output_path)
            print(f"  Saved: {output_path}")

        print("\n" + "=" * 60)
        print("Release screenshots saved to docs/")
        print("=" * 60)
        print("\nFiles created:")
        for f in ["screenshot-providers.png", "screenshot-task-input.png", "screenshot-settings.png"]:
            path = DOCS_DIR / f
            if path.exists():
                size = path.stat().st_size
                print(f"  {f} ({size:,} bytes)")

    except PlaywrightUnavailable as e:
        print(f"Playwright error: {e}", file=sys.stderr)
        sys.exit(1)
    except ChadLaunchError as e:
        print(f"Chad launch error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        if "instance" in locals():
            stop_chad(instance)
        env.cleanup()


if __name__ == "__main__":
    main()
