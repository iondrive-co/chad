#!/usr/bin/env python3
"""Show the interrupt feature in the Chat tab."""

import os
import sys
import time
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

            # Click the New tab to get to chat view
            page.locator('button:has-text("New")').click()
            page.wait_for_timeout(1000)

            # Wait for chat view to load
            page.wait_for_selector('.chat-view', timeout=5000)

            # Take a screenshot of the chat UI in normal state
            page.screenshot(path="/tmp/chad/chat_normal_state.png", full_page=True)
            print("Normal state screenshot saved to /tmp/chad/chat_normal_state.png")

            # Now simulate the task active state by injecting styles and content
            page.evaluate("""
                // Find the textarea and update it
                const textarea = document.querySelector('.chat-composer textarea');
                if (textarea) {
                    textarea.placeholder = 'Type a clarification or additional context for the agent…';
                    textarea.value = 'Please provide more details about the error handling approach!';
                }

                // Update the button
                const button = document.querySelector('.chat-composer button');
                if (button) {
                    button.textContent = 'Send Interrupt';
                }

                // Update status
                const status = document.querySelector('.chat-status');
                if (status) {
                    status.textContent = 'Running…';
                }

                // Add running indicator
                const composerRight = document.querySelector('.composer-right');
                if (composerRight && !document.querySelector('.running-indicator')) {
                    const indicator = document.createElement('span');
                    indicator.className = 'running-indicator';
                    indicator.textContent = 'Running…';
                    composerRight.insertBefore(indicator, composerRight.firstChild);
                }

                // Add task description bar
                if (!document.querySelector('.task-description-bar')) {
                    const taskBar = document.createElement('div');
                    taskBar.className = 'task-description-bar';
                    taskBar.innerHTML = '<span class="task-description-label">Task:</span><span class="task-description-text">Implement user authentication system with JWT tokens</span>';
                    const chatBody = document.querySelector('.chat-body');
                    if (chatBody && chatBody.parentNode) {
                        chatBody.parentNode.insertBefore(taskBar, chatBody);
                    }
                }

                // Add some conversation items
                const messages = document.querySelector('.chat-messages');
                if (messages && messages.children.length === 0) {
                    // Add user message
                    const userMsg = document.createElement('div');
                    userMsg.className = 'chat-item end';
                    userMsg.innerHTML = `
                        <div class="chat-bubble user">
                            <div class="chat-bubble-label">Pleb</div>
                            <div class="chat-bubble-text">Implement user authentication system with JWT tokens</div>
                        </div>
                    `;
                    messages.appendChild(userMsg);

                    // Add assistant response
                    const assistantMsg = document.createElement('div');
                    assistantMsg.className = 'chat-item start';
                    assistantMsg.innerHTML = `
                        <div class="chat-bubble assistant">
                            <div class="chat-bubble-label">Agent</div>
                            <div class="chat-bubble-text">I'll help you implement a user authentication system with JWT tokens. Let me start by exploring the codebase to understand the current structure...</div>
                        </div>
                    `;
                    messages.appendChild(assistantMsg);
                }
            """)

            # Take screenshot showing the interrupt-ready state
            page.screenshot(path="/tmp/chad/interrupt_ready_state.png", full_page=True)
            print("Interrupt-ready state screenshot saved to /tmp/chad/interrupt_ready_state.png")

    finally:
        stop_chad(instance)
        env.cleanup()

if __name__ == "__main__":
    main()