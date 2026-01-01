#!/usr/bin/env python3
"""Screenshot utility for visual verification of Chad's Gradio UI.

This script uses the shared ui_playwright_runner utilities to launch Chad,
take a screenshot, and save it for visual inspection.

Usage:
    python scripts/screenshot_ui.py [--output /path/to/screenshot.png] [--tab providers]

Examples:
    # Take screenshot of default (Run Task) tab
    python scripts/screenshot_ui.py

    # Screenshot specific tab
    python scripts/screenshot_ui.py --tab providers

    # Screenshot a specific component using CSS selector
    python scripts/screenshot_ui.py --tab run --selector "#run-top-inputs"
    python scripts/screenshot_ui.py --tab providers --selector "#provider-card-0"

    # Capture both dark and light variants and open them when finished (handy in PyCharm)
    python scripts/screenshot_ui.py

    # Custom output path
    python scripts/screenshot_ui.py --output /tmp/chad-ui.png

    # Headless mode (no browser window)
    python scripts/screenshot_ui.py --headless
"""

import argparse
import os
import sys
from pathlib import Path
import webbrowser

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from chad.ui_playwright_runner import (
        ChadLaunchError,
        PlaywrightUnavailable,
        create_temp_env,
        open_playwright_page,
        resolve_screenshot_output,
        start_chad,
        stop_chad,
    )
except ImportError as e:
    print(f"Error importing ui_playwright_runner: {e}", file=sys.stderr)
    print("Ensure playwright is installed: pip install playwright && playwright install chromium")
    sys.exit(1)


DEFAULT_OUTPUT = Path("/tmp/chad/screenshot.png")


def screenshot_element(page, selector: str, output_path: Path) -> Path:
    """Screenshot a specific element by CSS selector."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    page.wait_for_selector(selector, state="visible", timeout=10000)
    element = page.query_selector(selector)
    if element is None:
        raise RuntimeError(f"Selector did not resolve to an element: {selector}")
    element.screenshot(path=os.fspath(output_path))
    return output_path


def screenshot_page(page, output_path: Path) -> Path:
    """Screenshot the full page."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=os.fspath(output_path))
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Take a screenshot of Chad's Gradio UI"
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output path (default: {DEFAULT_OUTPUT})"
    )
    parser.add_argument(
        "--tab", "-t",
        choices=["run", "providers"],
        default=None,
        help="Tab to screenshot (default: run)"
    )
    parser.add_argument(
        "--selector", "-s",
        type=str,
        default=None,
        help="CSS selector to capture a specific element (e.g., '#run-top-inputs')"
    )
    parser.add_argument(
        "--width", type=int, default=1280,
        help="Viewport width (default: 1280)"
    )
    parser.add_argument(
        "--height", type=int, default=900,
        help="Viewport height (default: 900)"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode (no window)"
    )
    parser.add_argument(
        "--color-scheme",
        choices=["dark", "light", "both"],
        default="both",
        help=argparse.SUPPRESS,
    )

    args = parser.parse_args()

    print("Creating temporary Chad environment...")
    env = create_temp_env()

    try:
        print("Starting Chad server...")
        instance = start_chad(env)
        print(f"Chad running on port {instance.port}")

        viewport = {"width": args.width, "height": args.height}
        schemes = ["dark", "light"]
        multi = True
        outputs = []

        for scheme in schemes:
            target_path = resolve_screenshot_output(args.output, scheme, multi)
            target_desc = args.selector if args.selector else (args.tab or 'run') + " tab"
            print(f"Taking screenshot of {target_desc} ({scheme} mode)...")
            with open_playwright_page(
                instance.port,
                tab=args.tab,
                headless=args.headless,
                viewport=viewport,
                color_scheme=scheme,
                render_delay=2.0,
            ) as page:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                if args.selector:
                    screenshot_element(page, args.selector, target_path)
                else:
                    screenshot_page(page, target_path)
                outputs.append(target_path)
                print(f"Saved: {target_path}")

        for path in outputs:
            webbrowser.open(path.as_uri())

    except PlaywrightUnavailable as e:
        print(f"Playwright error: {e}", file=sys.stderr)
        sys.exit(1)
    except ChadLaunchError as e:
        print(f"Chad launch error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if 'instance' in locals():
            stop_chad(instance)
        env.cleanup()


if __name__ == "__main__":
    main()
