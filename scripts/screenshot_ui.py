#!/usr/bin/env python3
"""Screenshot utility for visual verification of Chad's Gradio UI.

This script launches Chad on an ephemeral port, waits for it to be ready,
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
import re
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


DEFAULT_OUTPUT = Path("/tmp/chad/screenshot.png")
TAB_SELECTORS = {
    "task": 'button:has-text("Run Task")',
    "run": 'button:has-text("Run Task")',
    "providers": 'button:has-text("Providers")',
}


def wait_for_server(port: int, timeout: int = 30) -> bool:
    """Wait for Gradio server to be fully ready."""
    import urllib.request

    url = f"http://127.0.0.1:{port}/"
    start = time.time()

    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(url)
            response = urllib.request.urlopen(req, timeout=5)
            content = response.read().decode('utf-8', errors='ignore')
            if 'gradio' in content.lower():
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def take_screenshot(
    port: int,
    output_path: Path,
    tab: str | None = None,
    viewport_width: int = 1280,
    viewport_height: int = 900
) -> bool:
    """Take a screenshot of Chad's UI using Playwright."""
    url = f"http://127.0.0.1:{port}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": viewport_width, "height": viewport_height},
            color_scheme="dark"  # Match the app's dark theme
        )
        page = context.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("gradio-app", timeout=30000)
            time.sleep(2)  # Let Gradio fully render

            if tab and tab.lower() in TAB_SELECTORS:
                selector = TAB_SELECTORS[tab.lower()]
                tab_button = page.locator(selector).first
                if tab_button:
                    tab_button.evaluate("el => el.click()")
                    time.sleep(0.5)

            page.screenshot(path=str(output_path), full_page=False)
            return True

        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return False
        finally:
            browser.close()


def create_temp_config() -> Path:
    """Create a temporary config file with empty password for screenshot mode."""
    import json
    import tempfile
    import bcrypt
    import base64

    # Create temp directory
    temp_dir = Path(tempfile.mkdtemp(prefix="chad_screenshot_"))
    config_path = temp_dir / "config.json"

    # Create config with empty password (no encryption)
    password_hash = bcrypt.hashpw(b"", bcrypt.gensalt()).decode()
    encryption_salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()

    config = {
        'password_hash': password_hash,
        'encryption_salt': encryption_salt,
        'accounts': {}
    }

    with open(config_path, 'w') as f:
        json.dump(config, f)
    config_path.chmod(0o600)

    return config_path


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
        choices=["task", "run", "providers"],
        default=None,
        help="Tab to screenshot"
    )
    parser.add_argument(
        "--width", type=int, default=1280,
        help="Viewport width"
    )
    parser.add_argument(
        "--height", type=int, default=900,
        help="Viewport height"
    )

    args = parser.parse_args()

    # Create temporary config for blank slate startup
    temp_config = create_temp_config()

    print("Starting Chad for screenshot...")

    # Start Chad with ephemeral port, temp config, and empty password
    env = {
        **subprocess.os.environ,
        'CHAD_CONFIG': str(temp_config),
        'CHAD_PASSWORD': '',  # Empty password for blank slate
        'CHAD_PROJECT_PATH': '/path/to/your/project'  # Placeholder for screenshots
    }
    chad_process = subprocess.Popen(
        [sys.executable, "-m", "chad", "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,  # No password needed
        text=True,
        bufsize=1,
        env=env
    )

    port = None
    try:
        # Read output looking for CHAD_PORT=xxxxx
        while True:
            line = chad_process.stdout.readline()
            if not line:
                if chad_process.poll() is not None:
                    print("Error: Chad exited unexpectedly", file=sys.stderr)
                    sys.exit(1)
                continue

            # Look for port announcement
            match = re.search(r'CHAD_PORT=(\d+)', line)
            if match:
                port = int(match.group(1))
                break

        if not wait_for_server(port):
            print("Error: Server not responding", file=sys.stderr)
            sys.exit(1)

        time.sleep(2)  # Let Gradio fully initialize

        # Ensure output directory exists
        args.output.parent.mkdir(parents=True, exist_ok=True)

        print(f"Taking screenshot of {'tab ' + args.tab if args.tab else 'default view'}...")

        if take_screenshot(port, args.output, args.tab, args.width, args.height):
            print(f"✓ Saved: {args.output}")
        else:
            print("✗ Failed", file=sys.stderr)
            sys.exit(1)

    finally:
        chad_process.terminate()
        try:
            chad_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            chad_process.kill()

        # Clean up temp config
        import shutil
        shutil.rmtree(temp_config.parent, ignore_errors=True)


if __name__ == "__main__":
    main()
