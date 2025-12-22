"""Tests for the screenshot utility."""

import subprocess
import sys
import time
from pathlib import Path

import pytest

# Skip if playwright not installed
pytest.importorskip("playwright")


@pytest.fixture
def mini_gradio_server():
    """Start a minimal Gradio server for testing screenshots."""
    import threading
    import gradio as gr

    # Create a minimal interface that mimics Chad's structure
    with gr.Blocks(title="Test Chad") as demo:
        with gr.Tabs():
            with gr.Tab("Run Task"):
                gr.Markdown("## Test Task Tab")
                gr.Textbox(label="Project Path", value="/test/path")
                gr.Button("Start Task")
            with gr.Tab("Providers"):
                gr.Markdown("## Test Providers Tab")
                gr.Markdown("Provider list would go here")

    # Start server in background thread
    server_thread = threading.Thread(
        target=lambda: demo.launch(
            server_name="127.0.0.1",
            server_port=7860,
            share=False,
            quiet=True,
            prevent_thread_lock=True
        ),
        daemon=True
    )
    server_thread.start()

    # Wait for server to be ready
    import socket
    for _ in range(30):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            if sock.connect_ex(("127.0.0.1", 7860)) == 0:
                sock.close()
                time.sleep(1)  # Extra time for Gradio to initialize
                break
            sock.close()
        except socket.error:
            pass
        time.sleep(0.5)
    else:
        pytest.fail("Gradio server failed to start")

    yield demo

    # Cleanup
    demo.close()


def test_screenshot_utility_runs(mini_gradio_server, tmp_path):
    """Test that the screenshot utility can capture the UI."""
    from playwright.sync_api import sync_playwright

    output_path = tmp_path / "screenshot.png"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})

        page.goto("http://127.0.0.1:7860", wait_until="networkidle", timeout=30000)
        page.wait_for_selector("gradio-app", timeout=10000)
        time.sleep(1)

        page.screenshot(path=str(output_path))
        browser.close()

    assert output_path.exists(), "Screenshot was not created"
    assert output_path.stat().st_size > 1000, "Screenshot file is too small"


def test_screenshot_utility_tabs(tmp_path):
    """Test that we can screenshot different tabs with fresh server."""
    import threading
    import gradio as gr
    from playwright.sync_api import sync_playwright

    # Create a minimal interface that mimics Chad's structure
    with gr.Blocks(title="Test Chad Tabs") as demo:
        with gr.Tabs():
            with gr.Tab("Run Task"):
                gr.Markdown("## Test Task Tab Content")
                gr.Textbox(label="Project Path", value="/test/path")
            with gr.Tab("Providers"):
                gr.Markdown("## Test Providers Tab Content")
                gr.Markdown("This is different from Task tab")

    # Start server
    server_thread = threading.Thread(
        target=lambda: demo.launch(
            server_name="127.0.0.1",
            server_port=7861,  # Different port to avoid conflicts
            share=False,
            quiet=True,
            prevent_thread_lock=True
        ),
        daemon=True
    )
    server_thread.start()

    # Wait for server
    import socket
    for _ in range(30):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            if sock.connect_ex(("127.0.0.1", 7861)) == 0:
                sock.close()
                time.sleep(1)
                break
            sock.close()
        except socket.error:
            pass
        time.sleep(0.5)
    else:
        pytest.fail("Gradio server failed to start")

    try:
        task_screenshot = tmp_path / "task.png"
        providers_screenshot = tmp_path / "providers.png"

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})

            page.goto("http://127.0.0.1:7861", wait_until="networkidle", timeout=30000)
            page.wait_for_selector("gradio-app", timeout=10000)
            time.sleep(1)

            # Screenshot task tab (should be default)
            page.screenshot(path=str(task_screenshot))

            # Switch to providers tab and screenshot (use JS click to avoid interception)
            providers_btn = page.locator('button:has-text("Providers")').first
            providers_btn.evaluate("el => el.click()")
            time.sleep(0.5)
            page.screenshot(path=str(providers_screenshot))

            browser.close()

        assert task_screenshot.exists()
        assert providers_screenshot.exists()
    finally:
        demo.close()


def test_screenshot_script_help():
    """Test that the screenshot script shows help."""
    result = subprocess.run(
        [sys.executable, "scripts/screenshot_ui.py", "--help"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent
    )
    assert result.returncode == 0
    assert "screenshot" in result.stdout.lower()
    assert "--output" in result.stdout
    assert "--tab" in result.stdout
