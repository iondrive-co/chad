#!/usr/bin/env python3
"""Capture screenshot demonstrating interrupt functionality."""

import time
import asyncio
from chad.util.verification.ui_runner import create_temp_env, start_chad, stop_chad, open_playwright_page

async def capture_interrupt_demo():
    env = create_temp_env()
    instance = start_chad(env)

    try:
        with open_playwright_page(instance.port, tab="chat", headless=False) as page:
            # Wait for page to load
            page.wait_for_selector('.chat-view', timeout=10000)

            # Create a session first
            page.locator('.sidebar button:has-text("New Session")').click()
            time.sleep(0.5)

            # Start a long-running task
            textarea = page.locator('.chat-composer textarea')
            textarea.fill("Please count slowly from 1 to 100, pausing for a second between each number.")

            # Click start task button
            page.locator('button:has-text("Start task")').click()

            # Wait for task to start running
            page.wait_for_selector('.running-indicator:has-text("Running…")', timeout=5000)
            time.sleep(3)  # Let it run for a bit

            # Now type an interrupt message
            textarea = page.locator('.chat-composer textarea')
            textarea.fill("Actually, please count by 5s instead!")

            # Take screenshot showing the interrupt UI state
            page.screenshot(path="/tmp/chad/interrupt_demo.png")
            print("Screenshot saved to /tmp/chad/interrupt_demo.png")

            # Click Send Interrupt button
            page.locator('button:has-text("Send Interrupt")').click()

            # Wait a moment to see the interrupt in conversation
            time.sleep(2)

            # Take second screenshot showing the interrupt message
            page.screenshot(path="/tmp/chad/interrupt_sent.png")
            print("Screenshot saved to /tmp/chad/interrupt_sent.png")

    finally:
        stop_chad(instance)
        env.cleanup()

if __name__ == "__main__":
    asyncio.run(capture_interrupt_demo())