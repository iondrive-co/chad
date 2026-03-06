#!/usr/bin/env python3
"""Simple screenshot showing interrupt UI state."""

import time
from chad.util.verification.ui_runner import create_temp_env, start_chad, stop_chad, open_playwright_page

# Start server with temp environment
env = create_temp_env()
instance = start_chad(env)

try:
    with open_playwright_page(instance.port, headless=True) as page:
        # Wait for page to load
        page.wait_for_selector('.chat-view', timeout=10000)

        # Click new session button
        page.locator('.sidebar button:has-text("New Session")').click()
        time.sleep(0.5)

        # First, take a "before" screenshot showing disabled state (no task running)
        page.screenshot(path="/tmp/chad/interrupt_before.png")
        print("Before screenshot saved to /tmp/chad/interrupt_before.png")

        # Now simulate task running by modifying the UI state directly
        # Add some dummy conversation items and set task active
        page.evaluate("""
            // Get React fiber to access component state
            const chatView = document.querySelector('.chat-view');
            const fiber = chatView._reactInternalFiber ||
                         chatView._reactRootContainer._internalRoot.current ||
                         Object.values(chatView).find(k => k && k.memoizedProps);

            // Find the ChatView component in the fiber tree
            let node = fiber;
            while (node && !node.memoizedState?.taskActive === undefined) {
                node = node.return || node.child;
            }

            // Simulate running state by adding classes
            document.querySelector('.chat-status').textContent = 'Running…';
            const textarea = document.querySelector('.chat-composer textarea');
            textarea.placeholder = 'Type a clarification or additional context for the agent…';
            textarea.disabled = false;

            const button = document.querySelector('.chat-composer button');
            button.textContent = 'Send Interrupt';
            button.disabled = false;

            // Add running indicator
            const runningSpan = document.createElement('span');
            runningSpan.className = 'running-indicator';
            runningSpan.textContent = 'Running…';
            document.querySelector('.composer-right').prepend(runningSpan);
        """)

        # Type an interrupt message
        textarea = page.locator('.chat-composer textarea')
        textarea.fill("Please provide more details about the implementation approach!")

        # Take screenshot showing interrupt UI enabled
        page.screenshot(path="/tmp/chad/interrupt_ui_active.png")
        print("Active state screenshot saved to /tmp/chad/interrupt_ui_active.png")

finally:
    stop_chad(instance)
    env.cleanup()

print("\nScreenshots captured successfully!")
print("- Before (disabled): /tmp/chad/interrupt_before.png")
print("- During task (enabled): /tmp/chad/interrupt_ui_active.png")