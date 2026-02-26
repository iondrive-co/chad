"""Playwright helpers for launching the React UI and capturing screenshots.

Provides the minimal surface needed by scripts/release_screenshots.py and
any future visual tests:

    from chad.util.verification.ui_runner import (
        ChadLaunchError, PlaywrightUnavailable,
        create_temp_env, start_chad, stop_chad, open_playwright_page,
    )
"""

from __future__ import annotations

import base64
import contextlib
import os
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, Optional

import bcrypt

from chad.util.config_manager import ConfigManager
from chad.util.process_registry import ProcessRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[4]  # src/chad/util/verification -> project root
SHARED_BROWSERS_PATH = Path.home() / ".cache" / "ms-playwright"

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PlaywrightUnavailable(RuntimeError):
    """Raised when Playwright or Chromium are missing."""


class ChadLaunchError(RuntimeError):
    """Raised when the Chad server cannot be started or reached."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TempChadEnv:
    """Temporary environment for running Chad + Playwright."""

    config_path: Path
    project_dir: Path
    temp_dir: Path
    password: str = ""
    env_vars: dict = field(default_factory=dict)

    def cleanup(self) -> None:
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


# ---------------------------------------------------------------------------
# Registry (singleton per-process)
# ---------------------------------------------------------------------------

_test_registry: ProcessRegistry | None = None


def _get_test_registry() -> ProcessRegistry:
    global _test_registry
    if _test_registry is None:
        _test_registry = ProcessRegistry()
    return _test_registry


# ---------------------------------------------------------------------------
# Playwright helpers
# ---------------------------------------------------------------------------


def _has_chromium_cache(browsers_path: Path) -> bool:
    chromium_dirs = list(browsers_path.glob("chromium-*"))
    return any((d / "chrome-linux").is_dir() or (d / "chrome-win").is_dir() for d in chromium_dirs)


def ensure_playwright_browsers() -> None:
    browsers_path = SHARED_BROWSERS_PATH
    browsers_path.mkdir(parents=True, exist_ok=True)
    if _has_chromium_cache(browsers_path):
        return
    env = os.environ.copy()
    env["PLAYWRIGHT_BROWSERS_PATH"] = os.fspath(browsers_path)
    env["NODE_OPTIONS"] = env.get("NODE_OPTIONS", "") + " --dns-result-order=ipv4first"
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    if result.returncode != 0:
        detail = (result.stderr.strip() or result.stdout.strip())
        raise PlaywrightUnavailable(f"Failed to install Playwright browsers: {detail}")


def ensure_playwright():
    try:
        from playwright.sync_api import sync_playwright
        ensure_playwright_browsers()
        return sync_playwright
    except ImportError as exc:
        raise PlaywrightUnavailable(
            "Playwright is not installed. Install with `pip install playwright` "
            "and run `playwright install chromium`."
        ) from exc


# ---------------------------------------------------------------------------
# Temp environment
# ---------------------------------------------------------------------------


def create_temp_env(screenshot_mode: bool = True) -> TempChadEnv:
    """Create a temporary Chad config and project for UI testing."""
    temp_dir = Path(tempfile.mkdtemp(prefix="chad_ui_runner_"))
    config_path = temp_dir / "config.json"
    project_dir = temp_dir / "project"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "README.md").write_text("# Test Project\n")

    security_mgr = ConfigManager(config_path)
    password = ""
    password_hash = security_mgr.hash_password(password)
    encryption_salt = base64.urlsafe_b64encode(bcrypt.gensalt()).decode()

    config = {
        "password_hash": password_hash,
        "encryption_salt": encryption_salt,
        "accounts": {},
    }
    security_mgr.save_config(config)

    if not screenshot_mode:
        security_mgr.store_account("mock-coding", "mock", "", password, "mock-model")
        security_mgr.assign_role("mock-coding", "CODING")

    return TempChadEnv(
        config_path=config_path,
        project_dir=project_dir,
        temp_dir=temp_dir,
        password=password,
        env_vars={"CHAD_SCREENSHOT_MODE": "1"} if screenshot_mode else {},
    )


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_ready(port: int, timeout: int = 30, process: subprocess.Popen[str] | None = None) -> None:
    """Wait until the API /status endpoint responds."""
    import urllib.request
    url = f"http://127.0.0.1:{port}/status"
    start = time.time()
    while time.time() - start < timeout:
        if process is not None and process.poll() is not None:
            raise ChadLaunchError("Chad server exited before becoming ready")
        try:
            urllib.request.urlopen(url, timeout=5)
            return
        except Exception:
            time.sleep(0.5)
    raise ChadLaunchError("Timed out waiting for Chad API to become ready")


def start_chad(env: TempChadEnv) -> ChadInstance:
    """Start Chad API server with an ephemeral port and return the running instance."""
    registry = _get_test_registry()
    registry.cleanup_stale()

    chad_env = {
        **os.environ,
        "CHAD_CONFIG": os.fspath(env.config_path),
        "CHAD_PASSWORD": env.password,
        "CHAD_PROJECT_PATH": os.fspath(env.project_dir),
        "PYTHONPATH": os.fspath(PROJECT_ROOT / "src"),
        "CHAD_PARENT_PID": str(os.getpid()),
    }
    if env.env_vars:
        chad_env.update(env.env_vars)

    popen_kwargs: Dict[str, object] = {}
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True

    requested_port = _find_free_port()
    process = subprocess.Popen(
        [
            os.fspath(Path(sys.executable)),
            "-m", "chad",
            "--mode", "server",
            "--api-port", str(requested_port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        env=chad_env,
        cwd=os.fspath(PROJECT_ROOT),
        **popen_kwargs,
    )
    registry.register(process, description=f"chad test server (port {requested_port})")
    _wait_for_ready(requested_port, process=process)
    return ChadInstance(process=process, port=requested_port, env=env)


def stop_chad(instance: ChadInstance) -> None:
    """Terminate a running Chad instance."""
    registry = _get_test_registry()
    registry.terminate(instance.process.pid, timeout=5.0)


# ---------------------------------------------------------------------------
# Playwright page context
# ---------------------------------------------------------------------------


def _select_tab(page, tab: str) -> None:
    """Click a tab button in the React UI header nav."""
    buttons = page.locator("nav.tabs button")
    count = buttons.count()
    for i in range(count):
        btn = buttons.nth(i)
        if btn.inner_text().strip().lower() == tab.lower():
            btn.click()
            page.wait_for_timeout(500)
            return
    raise ChadLaunchError(f"Could not find tab matching '{tab}'")


@contextlib.contextmanager
def open_playwright_page(
    port: int,
    *,
    tab: Optional[str] = None,
    headless: bool = True,
    viewport: Optional[Dict[str, int]] = None,
    color_scheme: str | None = "dark",
    render_delay: float = 1.0,
) -> Iterator:
    """Open a Playwright page pointed at the React UI (served via Vite or static)."""
    sync_playwright = ensure_playwright()
    if viewport is None:
        viewport = {"width": 1280, "height": 900}

    with sync_playwright() as p:
        launch_args = ["--disable-gpu"]
        browser = p.chromium.launch(headless=headless, args=launch_args)
        context = browser.new_context(viewport=viewport, color_scheme=color_scheme)
        page = context.new_page()
        try:
            page.goto(f"http://127.0.0.1:{port}", wait_until="domcontentloaded", timeout=30000)
            # Wait for the React app to render
            page.wait_for_selector(".app-header h1", timeout=15000)
            if tab:
                _select_tab(page, tab)
            if render_delay > 0:
                page.wait_for_timeout(int(render_delay * 1000))
            yield page
        finally:
            context.close()
            browser.close()
