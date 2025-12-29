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

    # Capture both dark and light variants and open them when finished (handy in PyCharm)
    python scripts/screenshot_ui.py

    # Custom output path
    python scripts/screenshot_ui.py --output /tmp/chad-ui.png

    # Headless mode (no browser window)
    python scripts/screenshot_ui.py --headless
"""

import argparse
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
        screenshot_page,
        start_chad,
        stop_chad,
    )
except ImportError as e:
    print(f"Error importing ui_playwright_runner: {e}", file=sys.stderr)
    print("Ensure playwright is installed: pip install playwright && playwright install chromium")
    sys.exit(1)


DEFAULT_OUTPUT = Path("/tmp/chad/screenshot.png")


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
            print(f"Taking screenshot of {args.tab or 'run'} tab ({scheme} mode)...")
            with open_playwright_page(
                instance.port,
                tab=args.tab,
                headless=args.headless,
                viewport=viewport,
                color_scheme=scheme,
                render_delay=2.0,
            ) as page:
                target_path.parent.mkdir(parents=True, exist_ok=True)
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
