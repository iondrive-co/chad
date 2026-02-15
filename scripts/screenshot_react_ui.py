#!/usr/bin/env python3
"""Screenshot utility for the React UI.

Starts the Chad API server, starts the Vite dev server, waits for both,
then takes a Playwright screenshot.

Usage:
    .venv/bin/python scripts/screenshot_react_ui.py
    .venv/bin/python scripts/screenshot_react_ui.py --output /tmp/react-ui.png
    .venv/bin/python scripts/screenshot_react_ui.py --tab providers
    .venv/bin/python scripts/screenshot_react_ui.py --open
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def wait_for_url(url: str, timeout: float = 30) -> bool:
    import urllib.request
    import urllib.error
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


def main():
    parser = argparse.ArgumentParser(description="Screenshot the React UI")
    parser.add_argument("--output", default="/tmp/chad/react-ui.png")
    parser.add_argument("--tab", default="chat", choices=["chat", "providers", "settings"])
    parser.add_argument("--open", action="store_true", help="Open screenshot after capture")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=800)
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    procs = []

    try:
        # Start API server
        print("Starting Chad API server...")
        api_proc = subprocess.Popen(
            [str(PROJECT_ROOT / ".venv" / "bin" / "python"), "-m", "chad",
             "--mode", "server", "--api-port", "8000"],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        procs.append(api_proc)

        if not wait_for_url("http://localhost:8000/status", timeout=15):
            print("ERROR: API server did not start")
            return 1

        print("API server ready")

        # Start Vite dev server
        print("Starting Vite dev server...")
        vite_proc = subprocess.Popen(
            ["npx", "vite", "--port", "5173"],
            cwd=str(PROJECT_ROOT / "ui"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        procs.append(vite_proc)

        if not wait_for_url("http://localhost:5173", timeout=15):
            print("ERROR: Vite dev server did not start")
            return 1

        print("Vite dev server ready")

        # Give React a moment to render
        time.sleep(2)

        # Take screenshot with Playwright
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
            return 1

        print("Taking screenshot...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": args.width, "height": args.height})
            page.goto("http://localhost:5173")

            # Wait for connection
            page.wait_for_selector(".status-dot.connected", timeout=10000)

            # Click tab if not chat
            if args.tab != "chat":
                page.click(f"button:text('{args.tab.title()}')")
                page.wait_for_timeout(500)

            page.screenshot(path=args.output, full_page=False)
            browser.close()

        print(f"Screenshot saved to {args.output}")

        if args.open:
            import webbrowser
            webbrowser.open(f"file://{os.path.abspath(args.output)}")

        return 0

    finally:
        for proc in procs:
            try:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=5)
            except Exception:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
