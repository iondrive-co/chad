from __future__ import annotations

import base64
import contextlib
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, Optional, TYPE_CHECKING

import bcrypt

from .security import SecurityManager

if TYPE_CHECKING:
    from playwright.sync_api import Page

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PlaywrightUnavailable(RuntimeError):
    """Raised when Playwright or Chromium are missing."""


class ChadLaunchError(RuntimeError):
    """Raised when the Chad server cannot be started or reached."""


@dataclass
class TempChadEnv:
    """Temporary environment for running Chad + Playwright."""

    config_path: Path
    project_dir: Path
    temp_dir: Path
    password: str = ""

    def cleanup(self) -> None:
        """Remove temporary directories and unset overrides."""
        if os.environ.get("CHAD_CONFIG") == str(self.config_path):
            os.environ.pop("CHAD_CONFIG")
        try:
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass


@dataclass
class ChadInstance:
    """Running Chad process details."""

    process: subprocess.Popen[str]
    port: int
    env: TempChadEnv


def ensure_playwright():
    """Import Playwright, raising a clear error if unavailable."""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
        return sync_playwright
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise PlaywrightUnavailable(
            "Playwright is not installed. Install with `pip install playwright` and run `playwright install chromium`."
        ) from exc


def create_temp_env() -> TempChadEnv:
    """Create a temporary Chad config and project for UI testing."""
    temp_dir = Path(tempfile.mkdtemp(prefix="chad_ui_runner_"))
    config_path = temp_dir / "config.json"
    project_dir = temp_dir / "project"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "README.md").write_text("# Test Project\n")

    security_mgr = SecurityManager(config_path)
    password = ""
    password_hash = security_mgr.hash_password(password)
    encryption_salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()

    config = {
        "password_hash": password_hash,
        "encryption_salt": encryption_salt,
        "accounts": {},
    }
    security_mgr.save_config(config)

    # Store mock accounts for automation
    security_mgr.store_account("mock-coding", "mock", "", password, "mock-model")
    security_mgr.store_account("mock-mgmt", "mock", "", password, "mock-model")
    security_mgr.assign_role("mock-coding", "CODING")
    security_mgr.assign_role("mock-mgmt", "MANAGEMENT")

    return TempChadEnv(config_path=config_path, project_dir=project_dir, temp_dir=temp_dir, password=password)


def _wait_for_port(process: subprocess.Popen[str], timeout: int = 30) -> int:
    """Wait for the Chad process to announce its port."""
    start = time.time()
    while time.time() - start < timeout:
        line = process.stdout.readline()
        if not line and process.poll() is not None:
            raise ChadLaunchError("Chad server exited unexpectedly while waiting for port")
        match = re.search(r"CHAD_PORT=(\d+)", line)
        if match:
            return int(match.group(1))
    raise ChadLaunchError("Timed out waiting for CHAD_PORT announcement")


def _wait_for_ready(port: int, timeout: int = 30) -> None:
    """Wait until the web UI responds with Gradio content."""
    import urllib.request

    url = f"http://127.0.0.1:{port}/"
    start = time.time()
    while time.time() - start < timeout:
        try:
            response = urllib.request.urlopen(url, timeout=5)
            content = response.read().decode("utf-8", errors="ignore")
            if "gradio" in content.lower():
                return
        except Exception:
            time.sleep(0.5)
    raise ChadLaunchError("Timed out waiting for Chad web UI to become ready")


def start_chad(env: TempChadEnv) -> ChadInstance:
    """Start Chad with an ephemeral port and return the running instance."""
    process = subprocess.Popen(
        [os.fspath(Path(sys.executable)), "-m", "chad", "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        env={
            **os.environ,
            "CHAD_CONFIG": os.fspath(env.config_path),
            "CHAD_PASSWORD": env.password,
            "CHAD_PROJECT_PATH": os.fspath(env.project_dir),
        },
        cwd=os.fspath(PROJECT_ROOT),
    )
    port = _wait_for_port(process)
    _wait_for_ready(port)
    return ChadInstance(process=process, port=port, env=env)


def stop_chad(instance: ChadInstance) -> None:
    """Terminate a running Chad instance."""
    process = instance.process
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


@contextlib.contextmanager
def open_playwright_page(
    port: int,
    *,
    tab: Optional[str] = None,
    headless: bool = True,
    viewport: Optional[Dict[str, int]] = None,
    render_delay: float = 1.0,
) -> Iterator["Page"]:
    """Open a Playwright page for the given Chad server port."""
    sync_playwright = ensure_playwright()
    if viewport is None:
        viewport = {"width": 1280, "height": 900}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(viewport=viewport, color_scheme="dark")
        page = context.new_page()
        try:
            page.goto(f"http://127.0.0.1:{port}", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("gradio-app", timeout=30000)
            time.sleep(render_delay)
            if tab:
                _select_tab(page, tab)
            yield page
        finally:
            browser.close()


def _select_tab(page: "Page", tab: str) -> None:
    """Select a UI tab by friendly name."""
    normalized = tab.strip().lower()
    label = "🚀 Run Task" if normalized in {"run", "task", "default"} else "⚙️ Providers"
    page.get_by_role("tab", name=label).click()
    page.wait_for_timeout(500)


def screenshot_page(page: "Page", output_path: Path) -> Path:
    """Capture a screenshot of the current page."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=os.fspath(output_path))
    return output_path


def measure_provider_delete_button(page: "Page") -> Dict[str, float]:
    """Measure the provider header row and delete button heights."""
    _select_tab(page, "providers")
    measurement = page.evaluate(
        """
() => {
  const row = document.querySelector('.provider-card__header-row');
  const btn = row ? row.querySelector('.provider-delete') : null;
  if (!row || !btn) return null;
  const rowBox = row.getBoundingClientRect();
  const btnBox = btn.getBoundingClientRect();
  return {
    rowHeight: rowBox.height,
    buttonHeight: btnBox.height,
    rowWidth: rowBox.width,
    buttonWidth: btnBox.width,
    ratio: btnBox.height / rowBox.height
  };
}
"""
    )
    if not measurement:
        raise ChadLaunchError("Could not locate provider header or delete button")
    return measurement


@contextlib.contextmanager
def chad_page_session(
    *,
    tab: Optional[str] = None,
    headless: bool = True,
    viewport: Optional[Dict[str, int]] = None,
) -> Iterator[tuple["Page", ChadInstance]]:
    """Start Chad and open a Playwright page; cleanup when done."""
    env = create_temp_env()
    instance = start_chad(env)
    try:
        with open_playwright_page(instance.port, tab=tab, headless=headless, viewport=viewport) as page:
            yield page, instance
    finally:
        stop_chad(instance)
        env.cleanup()
