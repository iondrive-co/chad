#!/usr/bin/env python3
"""Screenshot utility for the React UI.

Starts the Chad API server with mock fixture data (via create_temp_env),
starts the Vite dev server, waits for both, then takes a Playwright screenshot.

Uses ephemeral ports to avoid conflicting with the dev launcher on 8000/5173.

Usage:
    .venv/bin/python scripts/screenshot_react_ui.py
    .venv/bin/python scripts/screenshot_react_ui.py --output /tmp/react-ui.png
    .venv/bin/python scripts/screenshot_react_ui.py --tab providers
    .venv/bin/python scripts/screenshot_react_ui.py --open
"""

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def find_free_port() -> int:
    """Find a free TCP port by binding to port 0."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


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

    # Rebuild chad-client so Vite picks up latest API methods
    print("Building chad-client...")
    subprocess.run(
        ["npm", "run", "build"],
        cwd=str(PROJECT_ROOT / "client"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )

    # Clear Vite dep cache to pick up fresh client build
    import shutil
    vite_cache = PROJECT_ROOT / "ui" / "node_modules" / ".vite"
    if vite_cache.exists():
        shutil.rmtree(vite_cache)

    # Use ephemeral ports so we never collide with the dev launcher
    api_port = find_free_port()
    vite_port = find_free_port()

    # Create temp environment with mock fixture data
    from chad.ui.gradio.verification.ui_playwright_runner import create_temp_env
    env = create_temp_env(screenshot_mode=True)

    procs = []

    try:
        # Build environment for the API server subprocess
        server_env = {
            **os.environ,
            "CHAD_CONFIG": str(env.config_path),
            "CHAD_PASSWORD": env.password,
            "CHAD_PROJECT_PATH": str(env.project_dir),
            "PYTHONPATH": str(PROJECT_ROOT / "src"),
        }
        server_env.update(env.env_vars)

        # Start API server with mock data on ephemeral port
        print(f"Starting Chad API server (screenshot mode) on port {api_port}...")
        api_proc = subprocess.Popen(
            [str(PROJECT_ROOT / ".venv" / "bin" / "python"), "-m", "chad",
             "--mode", "server", "--api-port", str(api_port)],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=server_env,
            start_new_session=True,
        )
        procs.append(api_proc)

        if not wait_for_url(f"http://localhost:{api_port}/status", timeout=15):
            print("ERROR: API server did not start")
            return 1

        print("API server ready")

        # Start Vite dev server on ephemeral port, proxying to our API port
        vite_env = {**os.environ, "CHAD_API_PORT": str(api_port)}
        print(f"Starting Vite dev server on port {vite_port}...")
        vite_proc = subprocess.Popen(
            ["npx", "vite", "--port", str(vite_port)],
            cwd=str(PROJECT_ROOT / "ui"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=vite_env,
            start_new_session=True,
        )
        procs.append(vite_proc)

        if not wait_for_url(f"http://localhost:{vite_port}", timeout=15):
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
            page.goto(f"http://localhost:{vite_port}")

            # Wait for connection
            page.wait_for_selector(".status-dot.connected", timeout=10000)

            # Click tab if not chat
            if args.tab != "chat":
                page.click(f"button:text('{args.tab.title()}')")
                page.wait_for_timeout(3000)

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
                # Kill the entire process group so child processes are cleaned up
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    pass
        env.cleanup()


if __name__ == "__main__":
    sys.exit(main())
