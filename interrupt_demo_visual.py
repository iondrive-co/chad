#!/usr/bin/env python3
"""Create a visual demonstration of the interrupt feature."""

import os
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[0] / "src"))

from chad.util.verification.ui_runner import (
    create_temp_env,
    open_playwright_page,
    start_chad,
    stop_chad,
)

def main():
    env = create_temp_env()
    instance = start_chad(env)

    try:
        with open_playwright_page(instance.port, headless=True) as page:
            # Let the page fully load
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)  # Extra wait for React to mount

            # Take a screenshot of the default state
            page.screenshot(path="/tmp/chad/interrupt_demo.png", full_page=True)
            print(f"Screenshot saved to /tmp/chad/interrupt_demo.png")

            # Print page content for debugging
            print("Page title:", page.title())

    finally:
        stop_chad(instance)
        env.cleanup()

if __name__ == "__main__":
    main()