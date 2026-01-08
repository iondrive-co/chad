#!/usr/bin/env python3
"""Generate release screenshots for README documentation.

This script captures the three main views for the README carousel:
1. providers-tab.png - Full providers view with multiple accounts
2. run-task-input.png - Task input panel (top section)
3. run-task-conversation.png - Completed task with follow-up input visible

Usage:
    python scripts/release_screenshots.py

The screenshots are saved to docs/ and should be committed to the repo.
"""

import os
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chad.verification.ui_playwright_runner import (  # noqa: E402
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
        // Show the follow-up row - handle Gradio's visibility
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

        viewport = {"width": 1280, "height": 900}

        # Screenshot 1: Providers tab (dark mode only for README)
        print("\n[1/3] Capturing providers tab...")
        output_path = DOCS_DIR / "screenshot-providers.png"
        with open_playwright_page(
            instance.port,
            tab="providers",
            headless=True,
            viewport=viewport,
            color_scheme="dark",
            render_delay=2.0,
        ) as page:
            screenshot_page(page, output_path)
            print(f"  Saved: {output_path}")

        # Screenshot 2: Run task input (top section)
        print("\n[2/3] Capturing task input panel...")
        output_path = DOCS_DIR / "screenshot-task-input.png"
        with open_playwright_page(
            instance.port,
            tab="run",
            headless=True,
            viewport=viewport,
            color_scheme="dark",
            render_delay=2.0,
        ) as page:
            # Capture just the top input section
            screenshot_element(page, "#run-top-inputs", output_path)
            print(f"  Saved: {output_path}")

        # Screenshot 3: Conversation with follow-up visible
        print("\n[3/3] Capturing conversation with follow-up...")
        output_path = DOCS_DIR / "screenshot-conversation.png"
        with open_playwright_page(
            instance.port,
            tab="run",
            headless=True,
            viewport={"width": 1280, "height": 800},
            color_scheme="dark",
            render_delay=2.0,
        ) as page:
            # Make follow-up row visible and add sample text
            inject_followup_visible(page)
            page.wait_for_timeout(500)  # Let UI update

            # Capture both chatbot and follow-up row by taking full page
            # then we'll crop to just the relevant area
            # First, scroll to ensure chatbot area is visible
            page.evaluate(
                """
            () => {
                const chatbot = document.getElementById('agent-chatbot');
                if (chatbot) chatbot.scrollIntoView({ behavior: 'instant', block: 'start' });
            }
            """
            )
            page.wait_for_timeout(200)

            # Take full page screenshot then crop to conversation area
            screenshot_page(page, output_path)
            print(f"  Saved: {output_path}")

        print("\n" + "=" * 60)
        print("Release screenshots saved to docs/")
        print("=" * 60)
        print("\nFiles created:")
        for f in ["screenshot-providers.png", "screenshot-task-input.png", "screenshot-conversation.png"]:
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
