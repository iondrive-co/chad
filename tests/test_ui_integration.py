"""UI integration tests using Playwright to verify UI behavior with mock providers.

These tests start Chad with mock providers, interact with the UI, and verify
that the implementation works correctly end-to-end.

Requires: pip install playwright && playwright install chromium
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Skip entire module if playwright not available
playwright = pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright, Page, expect  # noqa: E402


@pytest.fixture(scope="module")
def temp_config():
    """Create a temporary config with mock providers configured."""
    import base64
    import bcrypt

    temp_dir = Path(tempfile.mkdtemp(prefix="chad_ui_test_"))
    config_path = temp_dir / "config.json"

    # Use SecurityManager to properly create config
    os.environ['CHAD_CONFIG'] = str(config_path)

    # Import here to avoid circular imports
    from chad.security import SecurityManager

    security_mgr = SecurityManager(config_path)

    # Initialize with empty password
    password = ""
    password_hash = security_mgr.hash_password(password)
    encryption_salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()

    config = {
        'password_hash': password_hash,
        'encryption_salt': encryption_salt,
        'accounts': {}
    }
    security_mgr.save_config(config)

    # Store mock accounts using the security manager
    security_mgr.store_account('mock-coding', 'mock', '', password, 'mock-model')
    security_mgr.store_account('mock-mgmt', 'mock', '', password, 'mock-model')

    # Assign roles
    security_mgr.assign_role('mock-coding', 'CODING')
    security_mgr.assign_role('mock-mgmt', 'MANAGEMENT')

    yield config_path

    # Cleanup
    if 'CHAD_CONFIG' in os.environ:
        del os.environ['CHAD_CONFIG']
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def test_project(temp_config):
    """Create a temporary project directory for testing."""
    project_dir = temp_config.parent / "test_project"
    project_dir.mkdir()
    (project_dir / "README.md").write_text("# Test Project\n")
    return project_dir


@pytest.fixture(scope="module")
def chad_server(temp_config, test_project):
    """Start Chad server with mock providers."""
    env = {
        **os.environ,
        'CHAD_CONFIG': str(temp_config),
        'CHAD_PASSWORD': '',
        'CHAD_PROJECT_PATH': str(test_project)
    }

    process = subprocess.Popen(
        [sys.executable, "-m", "chad", "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        env=env,
        cwd=str(Path(__file__).parent.parent)
    )

    port = None
    try:
        # Wait for port announcement
        start = time.time()
        while time.time() - start < 30:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                pytest.fail("Chad server exited unexpectedly")
            match = re.search(r'CHAD_PORT=(\d+)', line)
            if match:
                port = int(match.group(1))
                break

        if port is None:
            pytest.fail("Could not get server port")

        # Wait for server to be ready
        import urllib.request
        url = f"http://127.0.0.1:{port}/"
        start = time.time()
        while time.time() - start < 30:
            try:
                response = urllib.request.urlopen(url, timeout=5)
                if 'gradio' in response.read().decode().lower():
                    break
            except Exception:
                time.sleep(0.5)

        time.sleep(1)  # Extra time for Gradio to initialize
        yield port

    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture
def page(chad_server):
    """Create a Playwright page connected to Chad."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            color_scheme="dark"
        )
        page = context.new_page()
        page.goto(f"http://127.0.0.1:{chad_server}", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("gradio-app", timeout=30000)
        time.sleep(1)  # Let Gradio fully render
        yield page
        browser.close()


class TestUIElements:
    """Test that UI elements are present and correctly configured."""

    def test_run_task_tab_visible(self, page: Page):
        """Run Task tab should be visible by default."""
        # Use role=tab to get the actual tab button
        tab = page.get_by_role("tab", name="🚀 Run Task")
        expect(tab).to_be_visible()

    def test_providers_tab_visible(self, page: Page):
        """Providers tab should be visible."""
        tab = page.get_by_role("tab", name="⚙️ Providers")
        expect(tab).to_be_visible()

    def test_project_path_field(self, page: Page):
        """Project path field should be present."""
        # Use label to find the field
        field = page.get_by_label("Project Path")
        expect(field).to_be_visible()

    def test_task_description_field(self, page: Page):
        """Task description field should be present."""
        textarea = page.locator('textarea').first
        expect(textarea).to_be_visible()

    def test_start_button_present(self, page: Page):
        """Start Task button should be present."""
        button = page.locator('#start-task-btn')
        expect(button).to_be_visible()


class TestReadyStatus:
    """Test the Ready status display with model assignments."""

    def test_ready_status_shows_model_info(self, page: Page):
        """Ready status should include model assignment info."""
        # Look for the ready status text
        status = page.locator('#role-config-status')
        expect(status).to_be_visible()

        # Should contain model assignment info
        text = status.text_content()
        assert "Ready" in text or "Missing" in text


class TestProvidersTab:
    """Test the Providers tab functionality."""

    def test_can_switch_to_providers_tab(self, page: Page):
        """Should be able to switch to Providers tab."""
        page.get_by_role("tab", name="⚙️ Providers").click()
        time.sleep(0.5)

        # Should see provider heading
        expect(page.get_by_role("heading", name="Providers")).to_be_visible()


class TestSubtaskTabs:
    """Test subtask tab filtering (integration with mock provider)."""

    def test_subtask_tabs_hidden_initially(self, page: Page):
        """Subtask tabs should be hidden before a task starts."""
        tabs = page.locator('#subtask-tabs')
        # Should either not exist or be hidden
        if tabs.count() > 0:
            expect(tabs).to_be_hidden()


class TestLiveActivityFormat:
    """Test that live activity uses Claude Code format."""

    def test_live_stream_box_exists(self, page: Page):
        """Live stream box should exist (may be hidden when empty)."""
        box = page.locator('#live-stream-box')
        # Box exists but may be hidden when empty - check it exists in DOM
        assert box.count() > 0, "live-stream-box should exist in DOM"


class TestNoStatusBox:
    """Verify status box has been removed."""

    def test_no_status_box(self, page: Page):
        """Status box should not exist in the DOM."""
        status_box = page.locator('#status-box')
        assert status_box.count() == 0, "status_box should be completely removed"


class TestTaskStatusHeader:
    """Test task status header component."""

    def test_task_status_header_hidden_initially(self, page: Page):
        """Task status header should be hidden before task starts."""
        header = page.locator('#task-status-header')
        # Should either not exist or be hidden
        if header.count() > 0:
            expect(header).to_be_hidden()


# Screenshot tests for visual verification
class TestScreenshots:
    """Take screenshots for visual verification."""

    def test_screenshot_run_task_tab(self, page: Page, tmp_path):
        """Take screenshot of Run Task tab."""
        output = tmp_path / "run_task.png"
        page.screenshot(path=str(output))
        assert output.exists()
        print(f"Screenshot saved: {output}")

    def test_screenshot_providers_tab(self, page: Page, tmp_path):
        """Take screenshot of Providers tab."""
        page.get_by_role("tab", name="⚙️ Providers").click()
        time.sleep(0.5)
        output = tmp_path / "providers.png"
        page.screenshot(path=str(output))
        assert output.exists()
        print(f"Screenshot saved: {output}")
