#!/usr/bin/env python3
"""Screenshot utility for visual verification of Chad's Gradio UI.

This script launches Chad in the background, waits for it to be ready,
takes a screenshot of the UI, and saves it for visual inspection.

Usage:
    python scripts/screenshot_ui.py [--output /path/to/screenshot.png] [--tab providers]

Examples:
    # Take screenshot of default (Run Task) tab
    python scripts/screenshot_ui.py

    # Screenshot specific tab
    python scripts/screenshot_ui.py --tab providers

    # Custom output path
    python scripts/screenshot_ui.py --output /tmp/chad-ui.png
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Error: playwright not installed. Install with:")
    print("  pip install playwright")
    print("  playwright install chromium")
    sys.exit(1)


DEFAULT_OUTPUT = Path("/tmp/chad_screenshot.png")
CHAD_URL = "http://127.0.0.1:7860"
STARTUP_TIMEOUT = 30  # seconds to wait for Chad to start
TAB_SELECTORS = {
    "task": 'button:has-text("Run Task")',
    "run": 'button:has-text("Run Task")',
    "providers": 'button:has-text("Providers")',
}


def wait_for_chad(timeout: int = STARTUP_TIMEOUT) -> bool:
    """Wait for Chad's Gradio server to be ready."""
    import socket

    start = time.time()
    while time.time() - start < timeout:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(("127.0.0.1", 7860))
            sock.close()
            if result == 0:
                # Give Gradio a moment to fully initialize
                time.sleep(1)
                return True
        except socket.error:
            pass
        time.sleep(0.5)
    return False


def is_chad_running() -> bool:
    """Check if Chad is already running on port 7860."""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    result = sock.connect_ex(("127.0.0.1", 7860))
    sock.close()
    return result == 0


def take_screenshot(
    output_path: Path,
    tab: str | None = None,
    viewport_width: int = 1280,
    viewport_height: int = 900
) -> bool:
    """Take a screenshot of Chad's UI using Playwright.

    Args:
        output_path: Where to save the screenshot
        tab: Which tab to screenshot ('task', 'providers', or None for current)
        viewport_width: Browser viewport width
        viewport_height: Browser viewport height

    Returns:
        True if screenshot was taken successfully
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": viewport_width, "height": viewport_height}
        )
        page = context.new_page()

        try:
            # Navigate to Chad
            page.goto(CHAD_URL, wait_until="networkidle", timeout=30000)

            # Wait for Gradio to fully render
            page.wait_for_selector("gradio-app", timeout=10000)
            time.sleep(1)  # Extra time for dynamic content

            # Switch to requested tab if specified
            if tab and tab.lower() in TAB_SELECTORS:
                selector = TAB_SELECTORS[tab.lower()]
                tab_button = page.locator(selector).first
                if tab_button:
                    # Use JS click to avoid Gradio element interception issues
                    tab_button.evaluate("el => el.click()")
                    time.sleep(0.5)  # Wait for tab content to load

            # Take screenshot
            page.screenshot(path=str(output_path), full_page=False)
            return True

        except Exception as e:
            print(f"Error taking screenshot: {e}", file=sys.stderr)
            return False
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(
        description="Take a screenshot of Chad's Gradio UI for visual verification"
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output path for screenshot (default: {DEFAULT_OUTPUT})"
    )
    parser.add_argument(
        "--tab", "-t",
        choices=["task", "run", "providers"],
        default=None,
        help="Which tab to screenshot (default: current/first tab)"
    )
    parser.add_argument(
        "--width",
        type=int,
        default=1280,
        help="Viewport width (default: 1280)"
    )
    parser.add_argument(
        "--height",
        type=int,
        default=900,
        help="Viewport height (default: 900)"
    )
    parser.add_argument(
        "--start-chad",
        action="store_true",
        help="Start Chad if not already running (requires password env var CHAD_PASSWORD)"
    )

    args = parser.parse_args()

    chad_process = None

    # Check if Chad is running
    if not is_chad_running():
        if args.start_chad:
            import os
            password = os.environ.get("CHAD_PASSWORD")
            if not password:
                print("Error: CHAD_PASSWORD environment variable required when using --start-chad",
                      file=sys.stderr)
                sys.exit(1)

            print("Starting Chad...")
            # Start Chad in background
            chad_process = subprocess.Popen(
                ["python", "-m", "chad"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "CHAD_PASSWORD": password}
            )

            if not wait_for_chad():
                print("Error: Chad failed to start within timeout", file=sys.stderr)
                if chad_process:
                    chad_process.terminate()
                sys.exit(1)
            print("Chad started successfully")
        else:
            print("Error: Chad is not running on port 7860", file=sys.stderr)
            print("Either start Chad manually or use --start-chad flag", file=sys.stderr)
            sys.exit(1)

    try:
        # Ensure output directory exists
        args.output.parent.mkdir(parents=True, exist_ok=True)

        print("Taking screenshot of Chad UI...")
        if args.tab:
            print(f"  Tab: {args.tab}")
        print(f"  Output: {args.output}")

        if take_screenshot(args.output, args.tab, args.width, args.height):
            print(f"Screenshot saved to: {args.output}")
            print("\nTo view the screenshot, use:")
            print(f"  Read tool with file_path: {args.output}")
            sys.exit(0)
        else:
            print("Failed to take screenshot", file=sys.stderr)
            sys.exit(1)

    finally:
        if chad_process:
            print("Stopping Chad...")
            chad_process.terminate()
            chad_process.wait(timeout=5)


if __name__ == "__main__":
    main()
