#!/usr/bin/env python3
"""Capture screenshot showing interrupt UI during task execution."""

import time
from chad.util.verification.ui_runner import create_temp_env, start_chad, stop_chad, open_playwright_page
from chad.ui.client import APIClient

env = create_temp_env()
instance = start_chad(env)

try:
    # Create a session and start a task via API
    api = APIClient("http://localhost:" + str(instance.port))

    # Create a mock account first
    accounts_response = api.list_accounts()
    accounts = accounts_response.accounts if hasattr(accounts_response, 'accounts') else accounts_response
    if not any(a.name == "mock-agent" if hasattr(a, 'name') else a.get("name") == "mock-agent" for a in accounts):
        api.create_account(
            name="mock-agent",
            provider="mock",
            model_id="mock-model",
            project_path=None
        )

    # Create session
    session = api.create_session({"name": "Interrupt Demo"})
    session_id = session["id"]

    # Start a task
    api.start_task(session_id, {
        "project_path": "/tmp/test_project",
        "task_description": "Count slowly from 1 to 20",
        "coding_agent": "mock-agent"
    })

    # Now open UI and capture screenshot
    with open_playwright_page(instance.port, tab="chat", headless=True) as page:
        # Navigate to the session
        page.wait_for_selector('.chat-view', timeout=10000)

        # Click on our session in the sidebar
        page.locator('.session-item:has-text("Interrupt Demo")').click()
        time.sleep(1)  # Wait for session to load

        # Type an interrupt message to show the UI state
        textarea = page.locator('.chat-composer textarea')
        textarea.fill("Please count by 2s instead of 1s!")

        # Take screenshot showing the interrupt UI
        page.screenshot(path="/tmp/chad/interrupt_ui_enabled.png")
        print("Screenshot saved to /tmp/chad/interrupt_ui_enabled.png")

        # Click Send Interrupt
        page.locator('button:has-text("Send Interrupt")').click()
        time.sleep(0.5)

        # Take another screenshot showing the interrupt in chat
        page.screenshot(path="/tmp/chad/interrupt_sent_view.png")
        print("Screenshot saved to /tmp/chad/interrupt_sent_view.png")

finally:
    stop_chad(instance)
    env.cleanup()
