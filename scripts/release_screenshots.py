#!/usr/bin/env python3
"""Generate release screenshots for README documentation.

This script captures the three main views for the README carousel:
1. providers-tab.png - Full providers view with multiple accounts
2. screenshot-task-input.png - Chat view with example conversation and agent output
3. settings.png - Settings pane with action rules

Usage:
    python scripts/release_screenshots.py

The screenshots are saved to docs/ and should be committed to the repo.
"""

import os
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chad.util.verification.ui_runner import (  # noqa: E402
    ChadLaunchError,
    PlaywrightUnavailable,
    create_temp_env,
    open_playwright_page,
    start_chad,
    stop_chad,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = PROJECT_ROOT / "docs"


def screenshot_element(page, selector: str, output_path: Path) -> Path:
    """Screenshot a specific element by CSS selector."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    element = page.locator(selector)
    element.wait_for(state="visible", timeout=10000)
    element.screenshot(path=os.fspath(output_path))
    return output_path


def screenshot_page(page, output_path: Path) -> Path:
    """Screenshot the full page."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=os.fspath(output_path))
    return output_path


def fill_task_form(page):
    """Create a session and seed the task form before injecting the chat view."""
    page.locator("button.new-session-btn").click()
    page.wait_for_timeout(2000)

    task_input = page.locator(".task-form textarea")
    if task_input.count() > 0:
        task_input.fill(
            "Add a REST API endpoint for user profile updates with validation, "
            "rate limiting, and comprehensive test coverage"
        )
        page.wait_for_timeout(300)


def inject_followup_visible(page):
    """Force the follow-up composer to remain visible in the screenshot."""
    page.evaluate(
        r"""
    () => {
        const composer = document.querySelector('.chat-composer');
        if (composer) {
            composer.style.display = 'grid';
            composer.style.visibility = 'visible';
            composer.style.opacity = '1';
        }

        const textarea = document.querySelector('.chat-composer textarea');
        if (textarea) {
            textarea.value = 'Now add pagination support for the user listing endpoint';
            textarea.dispatchEvent(new Event('input', { bubbles: true }));
        }

        const sendButton = document.querySelector('.chat-composer button[type="submit"]');
        if (sendButton) {
            sendButton.disabled = false;
        }
    }
    """
    )


def inject_conversation_view(page):
    """Inject a fake conversation with agent output for the screenshot."""
    # Inject conversation messages, task description bar, terminal output,
    # and status indicators directly into the DOM via JavaScript.
    page.evaluate(
        r"""
    () => {
        // --- Task description bar ---
        const taskBar = document.querySelector('.task-description-bar');
        if (!taskBar) {
            const chatView = document.querySelector('.chat-view');
            if (chatView) {
                const bar = document.createElement('div');
                bar.className = 'task-description-bar';
                bar.innerHTML = '<span class="task-description-label">Task:</span> <span class="task-description-text">Add a REST API endpoint for user profile updates with validation, rate limiting, and comprehensive test coverage</span> <span class="verification-agent-badge">Verification: codex-work</span>';
                const body = chatView.querySelector('.chat-body');
                if (body) chatView.insertBefore(bar, body);
            }
        }

        // --- Conversation messages ---
        // NOTE: .chat-bubble has white-space:pre-wrap, so we must avoid
        // indentation inside the template — every space/newline is rendered.
        const messagesDiv = document.querySelector('.chat-messages');
        if (messagesDiv) {
            messagesDiv.innerHTML =
'<div class="chat-item end"><div class="chat-bubble user">' +
'<div class="chat-bubble-label">Pleb</div>' +
'<div class="chat-bubble-text">Add a REST API endpoint for user profile updates with validation, rate limiting, and test coverage</div>' +
'</div></div>' +
'<div class="chat-item start"><div class="chat-bubble assistant">' +
'<div class="chat-bubble-label">Agent</div>' +
'<div class="chat-bubble-text">I\'ll implement the user profile update endpoint. I\'ve added:\n\n1. PUT /api/v1/users/{user_id}/profile with Pydantic validation\n2. Rate limiting middleware (60 req/min per user)\n3. Input sanitization for all string fields\n4. 14 unit tests covering validation, auth, and rate limits</div>' +
'</div></div>' +
'<div class="chat-item center"><div class="chat-bubble milestone">' +
'<div class="chat-bubble-label">Verification Complete</div>' +
'<div class="chat-bubble-text clamped">All 14 tests passing. Linting clean. No security issues found.</div>' +
'</div></div>' +
'<div class="chat-item end"><div class="chat-bubble user">' +
'<div class="chat-bubble-label">Pleb</div>' +
'<div class="chat-bubble-text">Also add email validation and include updated_at in the response</div>' +
'</div></div>' +
'<div class="chat-item start"><div class="chat-bubble assistant">' +
'<div class="chat-bubble-label">Agent</div>' +
'<div class="chat-bubble-text">Done! Added email format validation and an updated_at ISO timestamp in the response. Both new test cases are passing.</div>' +
'</div></div>';
        }

        // --- Chat status ---
        const chatStatus = document.querySelector('.chat-status');
        if (chatStatus) chatStatus.textContent = 'Ready for follow-up';

        // --- Composer ---
        const composer = document.querySelector('.chat-composer textarea');
        if (composer) {
            composer.value = 'Now add pagination support for the user listing endpoint';
            composer.dispatchEvent(new Event('input', { bubbles: true }));
        }

        // --- Terminal output ---
        const terminalOutput = document.querySelector('.terminal-output');
        if (terminalOutput) {
            terminalOutput.textContent = [
                '$ cd /home/user/my-webapp',
                '',
                'Reading src/api/routes/__init__.py...',
                'Reading src/api/models/user.py...',
                'Reading src/api/middleware/rate_limit.py...',
                '',
                'Writing src/api/routes/profile.py...',
                '  + PUT /api/v1/users/{user_id}/profile',
                '  + ProfileUpdateRequest schema',
                '  + email format validation via regex',
                '  + updated_at timestamp in response',
                '',
                'Writing src/api/middleware/rate_limit.py...',
                '  + RateLimiter class (sliding window)',
                '  + Per-user tracking with cleanup',
                '',
                'Writing tests/test_profile_endpoint.py...',
                '  + test_update_profile_success',
                '  + test_update_profile_invalid_email',
                '  + test_update_profile_name_too_long',
                '  + test_update_profile_unauthorized',
                '  + test_update_profile_rate_limited',
                '  + test_update_profile_not_found',
                '  + test_update_profile_empty_body',
                '  + test_update_profile_xss_sanitization',
                '  + test_update_profile_sql_injection',
                '  + test_update_profile_concurrent',
                '  + test_update_profile_partial',
                '  + test_update_profile_email_format',
                '  + test_update_profile_updated_at',
                '  + test_update_profile_idempotent',
                '',
                'Running: pytest tests/test_profile_endpoint.py -v',
                '======== test session starts ========',
                'collected 14 items',
                '',
                'test_profile_endpoint.py::test_success PASSED',
                'test_profile_endpoint.py::test_invalid_email PASSED',
                'test_profile_endpoint.py::test_name_too_long PASSED',
                'test_profile_endpoint.py::test_unauthorized PASSED',
                'test_profile_endpoint.py::test_rate_limited PASSED',
                'test_profile_endpoint.py::test_not_found PASSED',
                'test_profile_endpoint.py::test_empty_body PASSED',
                'test_profile_endpoint.py::test_xss PASSED',
                'test_profile_endpoint.py::test_sql_injection PASSED',
                'test_profile_endpoint.py::test_concurrent PASSED',
                'test_profile_endpoint.py::test_partial PASSED',
                'test_profile_endpoint.py::test_email_format PASSED',
                'test_profile_endpoint.py::test_updated_at PASSED',
                'test_profile_endpoint.py::test_idempotent PASSED',
                '',
                '======== 14 passed in 2.31s ========',
                '',
                'Running: flake8 src/api/routes/profile.py',
                'All checks passed.',
            ].join('\n');
        }

        // --- Terminal header (show completed) ---
        const terminalHeader = document.querySelector('.terminal-header');
        if (terminalHeader) {
            terminalHeader.innerHTML = '<span class="done-indicator">Completed</span>';
        }

        // --- Hide the task form if visible ---
        const taskForm = document.querySelector('.task-form');
        if (taskForm) taskForm.style.display = 'none';

        // --- Hide project settings ---
        const projectSettings = document.querySelector('.project-settings');
        if (projectSettings) projectSettings.style.display = 'none';

        // --- Hide session info bar to save space ---
        const sessionInfoBar = document.querySelector('.session-info-bar');
        if (sessionInfoBar) sessionInfoBar.style.display = 'none';
    }
    """
    )


def main():
    print("=" * 60)
    print("Generating Release Screenshots")
    print("=" * 60)

    print("\nCreating temporary Chad environment with fixtures...")
    env = create_temp_env(screenshot_mode=True)

    try:
        print("Starting Chad server...")
        instance = start_chad(env)
        print(f"Chad running on port {instance.port}")

        viewport_large = {"width": 1400, "height": 1350}
        viewport_medium = {"width": 1280, "height": 800}

        # Screenshot 1: Providers tab (cropped to content)
        print("\n[1/3] Capturing providers tab...")
        output_path = DOCS_DIR / "screenshot-providers.png"
        with open_playwright_page(
            instance.port,
            tab="providers",
            headless=True,
            viewport=viewport_large,
            color_scheme="light",
            render_delay=2.0,
        ) as page:
            # Full-page screenshot then crop to remove bottom whitespace
            page.screenshot(path=os.fspath(output_path), full_page=True, clip={
                "x": 0, "y": 0,
                "width": viewport_large["width"],
                "height": viewport_large["height"],
            })
            # Use Pillow to crop to actual content if available, otherwise
            # just trim a fixed amount from the bottom
            try:
                from PIL import Image
                img = Image.open(output_path)
                # Find the last row that isn't all-white (background)
                import numpy as np
                arr = np.array(img)
                # Check rows from bottom: find first non-background row
                bg = arr[arr.shape[0] - 1, arr.shape[1] // 2]  # sample bg color
                row_matches = np.all(np.all(arr == bg, axis=2), axis=1)
                last_content = arr.shape[0] - 1
                for i in range(arr.shape[0] - 1, -1, -1):
                    if not row_matches[i]:
                        last_content = i
                        break
                # Add small padding below content
                crop_bottom = min(last_content + 20, arr.shape[0])
                img_cropped = img.crop((0, 0, img.width, crop_bottom))
                img_cropped.save(output_path)
            except ImportError:
                pass  # No Pillow, keep as-is
            print(f"  Saved: {output_path}")

        # Screenshot 2: Chat view with conversation and agent output
        print("\n[2/3] Capturing chat conversation view...")
        output_path = DOCS_DIR / "screenshot-task-input.png"
        with open_playwright_page(
            instance.port,
            headless=True,
            viewport=viewport_large,
            color_scheme="light",
            render_delay=2.0,
        ) as page:
            fill_task_form(page)
            inject_conversation_view(page)
            inject_followup_visible(page)
            page.wait_for_timeout(500)
            screenshot_page(page, output_path)
            print(f"  Saved: {output_path}")

        # Screenshot 3: Settings tab
        print("\n[3/3] Capturing settings tab...")
        output_path = DOCS_DIR / "screenshot-settings.png"
        with open_playwright_page(
            instance.port,
            tab="settings",
            headless=True,
            viewport=viewport_medium,
            color_scheme="light",
            render_delay=2.0,
        ) as page:
            screenshot_page(page, output_path)
            print(f"  Saved: {output_path}")

        print("\n" + "=" * 60)
        print("Release screenshots saved to docs/")
        print("=" * 60)
        print("\nFiles created:")
        for f in ["screenshot-providers.png", "screenshot-task-input.png", "screenshot-settings.png"]:
            path = DOCS_DIR / f
            if path.exists():
                size = path.stat().st_size
                print(f"  {f} ({size:,} bytes)")

    except PlaywrightUnavailable as e:
        print(f"Playwright error: {e}", file=sys.stderr)
        sys.exit(1)
    except ChadLaunchError as e:
        print(f"Chad launch error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        if "instance" in locals():
            stop_chad(instance)
        env.cleanup()


if __name__ == "__main__":
    main()
