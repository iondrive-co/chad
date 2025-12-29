from __future__ import annotations

import base64
import contextlib
import os
import re
import subprocess
import sys
import tempfile
import time
import pwd
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, Optional, TYPE_CHECKING

import bcrypt

from .security import SecurityManager

if TYPE_CHECKING:
    from playwright.sync_api import Page

# Repository root; used for locating scripts and setting PYTHONPATH.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SHARED_BROWSERS_PATH = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".cache" / "ms-playwright"

# Ensure Playwright browsers are read from a shared cache even if HOME is overridden (e.g., Codex isolated homes).
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.fspath(SHARED_BROWSERS_PATH))


# Shared helper to keep screenshot naming consistent between the runner and the CLI script.
def resolve_screenshot_output(base: Path, scheme: str, multi: bool = False) -> Path:
    """Return the screenshot path for a given color scheme.

    If multiple screenshots are being captured, suffix the stem with the scheme name
    (e.g., screenshot_light.png) while keeping the provided path for the first scheme.
    """
    if not multi or scheme == "dark":
        return base
    return base.with_name(f"{base.stem}_{scheme}{base.suffix}")


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
    env_vars: dict = None  # Additional environment variables for screenshot mode

    def __post_init__(self):
        if self.env_vars is None:
            self.env_vars = {}

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


def create_temp_env(screenshot_mode: bool = True) -> TempChadEnv:
    """Create a temporary Chad config and project for UI testing.

    Args:
        screenshot_mode: If True, populate with rich synthetic data for screenshots.
                        If False, use minimal mock data for functional tests.
    """
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

    if screenshot_mode:
        # Use rich synthetic data for realistic screenshots
        from .screenshot_fixtures import (
            MOCK_ACCOUNTS,
            setup_mock_accounts,
            create_mock_codex_auth,
            create_mock_claude_creds,
            create_mock_gemini_creds,
            create_mock_mistral_config,
        )

        setup_mock_accounts(security_mgr, password)

        # Create mock credential files for each provider type
        chad_dir = temp_dir / ".chad"

        for account_name, account_data in MOCK_ACCOUNTS.items():
            provider = account_data["provider"]
            if provider == "openai":
                codex_home = chad_dir / "codex-homes" / account_name
                create_mock_codex_auth(codex_home, account_data)
            elif provider == "anthropic":
                claude_config = chad_dir / "claude-configs" / account_name
                create_mock_claude_creds(claude_config, account_data)

        # Gemini and Mistral use global config locations, create in temp
        create_mock_gemini_creds(temp_dir / ".gemini")
        create_mock_mistral_config(temp_dir / ".vibe")

        # Store paths for provider lookups
        env_vars = {
            "CHAD_SCREENSHOT_MODE": "1",
            "CHAD_TEMP_HOME": str(temp_dir),
        }
    else:
        # Minimal mock for functional tests
        security_mgr.store_account("mock-coding", "mock", "", password, "mock-model")
        security_mgr.assign_role("mock-coding", "CODING")
        env_vars = {}

    return TempChadEnv(
        config_path=config_path,
        project_dir=project_dir,
        temp_dir=temp_dir,
        password=password,
        env_vars=env_vars if screenshot_mode else {},
    )


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
    # Build environment with screenshot mode vars if present
    chad_env = {
        **os.environ,
        "CHAD_CONFIG": os.fspath(env.config_path),
        "CHAD_PASSWORD": env.password,
        "CHAD_PROJECT_PATH": os.fspath(env.project_dir),
        "PYTHONPATH": os.fspath(PROJECT_ROOT / "src"),
    }
    # Add any additional env vars (e.g., CHAD_SCREENSHOT_MODE, CHAD_TEMP_HOME)
    if env.env_vars:
        chad_env.update(env.env_vars)

    process = subprocess.Popen(
        [os.fspath(Path(sys.executable)), "-m", "chad", "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        env=chad_env,
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
    color_scheme: str | None = "dark",
    render_delay: float = 1.0,
) -> Iterator["Page"]:
    """Open a Playwright page for the given Chad server port."""
    sync_playwright = ensure_playwright()
    if viewport is None:
        viewport = {"width": 1280, "height": 900}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(viewport=viewport, color_scheme=color_scheme)
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


def run_screenshot_subprocess(
    *,
    tab: str = "run",
    headless: bool = True,
    viewport: Optional[Dict[str, int]] = None,
    label: str | None = None,
    issue_id: str = "",
    selector: str | None = None,
) -> Dict[str, object]:
    """Run screenshot_ui.py in a subprocess to avoid event loop conflicts.

    Args:
        tab: Which tab to screenshot ("run" or "providers")
        headless: Whether to run browser in headless mode
        viewport: Browser viewport dimensions
        label: Optional label for the screenshot filename
        issue_id: Optional issue ID for the screenshot filename
        selector: Optional CSS selector to capture a specific element instead of full page
    """
    viewport = viewport or {"width": 1280, "height": 900}
    artifacts_dir = Path(tempfile.mkdtemp(prefix="chad_visual_"))
    parts = []
    if issue_id:
        parts.append(issue_id.replace(" ", "-"))
    if label:
        parts.append(label.replace(" ", "-"))
    parts.append(tab)
    filename = "_".join(parts) + ".png"
    output_path = artifacts_dir / filename
    python_exec = PROJECT_ROOT / "venv" / "bin" / "python"
    if not python_exec.exists():
        python_exec = Path(sys.executable)

    schemes = ["dark", "light"]
    expected_paths = [resolve_screenshot_output(output_path, scheme, True) for scheme in schemes]

    cmd = [
        os.fspath(python_exec),
        os.fspath(PROJECT_ROOT / "scripts" / "screenshot_ui.py"),
        "--tab",
        tab,
        "--output",
        os.fspath(output_path),
        "--width",
        str(viewport.get("width", 1280)),
        "--height",
        str(viewport.get("height", 900)),
    ]
    if headless:
        cmd.append("--headless")
    if selector:
        cmd.extend(["--selector", selector])

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=os.fspath(PROJECT_ROOT),
        env={
            **os.environ,
            "PYTHONPATH": os.fspath(PROJECT_ROOT / "src"),
            "PLAYWRIGHT_BROWSERS_PATH": os.environ.get(
                "PLAYWRIGHT_BROWSERS_PATH",
                os.fspath(SHARED_BROWSERS_PATH),
            ),
        },
    )

    all_exist = all(path.exists() for path in expected_paths)
    return {
        "success": result.returncode == 0 and all_exist,
        "screenshot": os.fspath(expected_paths[0]),
        "screenshots": [os.fspath(p) for p in expected_paths],
        "artifacts_dir": os.fspath(artifacts_dir),
        "stdout": result.stdout[-3000:],
        "stderr": result.stderr[-3000:],
        "return_code": result.returncode,
    }


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


def measure_add_provider_accordion(page: "Page") -> Dict[str, float | str]:
    """Measure spacing and typography for the Add New Provider accordion."""
    _select_tab(page, "providers")
    measurement = page.evaluate(
        """
() => {
  const accordion = document.querySelector('.add-provider-accordion');
  if (!accordion) return null;
  const summary = accordion.querySelector('summary') ||
    accordion.querySelector('.label') || accordion.querySelector('.label-wrap');
  const summaryBox = summary ? summary.getBoundingClientRect() : accordion.getBoundingClientRect();

  // Find provider card groups by looking for gr-groups that contain header rows
  // (elem_classes on gr.Group doesn't apply in Gradio, so we can't use .provider-card)
  const groups = Array.from(document.querySelectorAll('.gr-group'));
  const cardGroups = groups.filter(g => g.querySelector('.provider-card__header-row'));

  const visibleCards = cardGroups.filter((card) => {
    const style = window.getComputedStyle(card);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    const rect = card.getBoundingClientRect();
    return rect.height > 0;
  });

  let lastCardBox = null;
  for (const card of visibleCards) {
    const rect = card.getBoundingClientRect();
    if (!lastCardBox || rect.bottom > lastCardBox.bottom) {
      lastCardBox = rect;
    }
  }

  if (!lastCardBox) return null;

  const computed = summary ? window.getComputedStyle(summary) : window.getComputedStyle(accordion);

  return {
    gap: summaryBox.top - lastCardBox.bottom,
    fontSize: computed.fontSize,
    fontWeight: computed.fontWeight
  };
}
"""
    )
    if not measurement:
        raise ChadLaunchError("Could not locate provider cards or add provider accordion")
    return measurement


def get_provider_names(page: "Page") -> list[str]:
    """Get a list of all visible provider names from the providers tab."""
    _select_tab(page, "providers")
    names = page.evaluate(
        """
() => {
  const headers = document.querySelectorAll('.provider-card__header-text');
  const visibleNames = [];
  for (const header of headers) {
    // Check if the header is visible
    const style = window.getComputedStyle(header);
    if (style.display === 'none' || style.visibility === 'hidden') {
      continue;
    }
    // Walk up the DOM to check if any parent is hidden
    let parent = header.parentElement;
    let isHidden = false;
    while (parent && parent !== document.body) {
      const parentStyle = window.getComputedStyle(parent);
      if (parentStyle.display === 'none' || parentStyle.visibility === 'hidden') {
        isHidden = true;
        break;
      }
      parent = parent.parentElement;
    }
    if (isHidden) continue;

    const text = header.textContent || '';
    const match = text.match(/^([^(]+)/);
    const name = match ? match[1].trim() : text.trim();
    if (name.length > 0) {
      visibleNames.push(name);
    }
  }
  return visibleNames;
}
"""
    )
    return names or []


def provider_exists(page: "Page", provider_name: str) -> bool:
    """Check if a provider with the given name exists in the UI."""
    return provider_name in get_provider_names(page)


def get_card_visibility_debug(page: "Page") -> list[dict]:
    """Get detailed visibility info for all provider card containers.

    Returns list of dicts with cardDisplay, columnDisplay, hasHeaderSpan, headerText for each card.
    """
    _select_tab(page, "providers")
    return page.evaluate(
        """
() => {
  const groups = document.querySelectorAll('.gr-group');
  const results = [];
  for (const group of groups) {
    // Only include groups that have a provider card header row
    const headerRow = group.querySelector('.provider-card__header-row');
    if (!headerRow) continue;

    const headerText = group.querySelector('.provider-card__header-text');
    const header = headerText ? headerText.textContent.trim() : '';

    // Get group's computed style
    const groupStyle = window.getComputedStyle(group);

    // Walk up to find Column container
    let parent = group.parentElement;
    let columnDisplay = 'unknown';
    while (parent && parent !== document.body) {
      if (parent.classList.contains('column')) {
        columnDisplay = window.getComputedStyle(parent).display;
        break;
      }
      parent = parent.parentElement;
    }

    results.push({
      headerText: header,
      cardDisplay: groupStyle.display,
      columnDisplay: columnDisplay,
      hasHeaderSpan: !!headerText
    });
  }
  return results;
}
"""
    )


@dataclass
class DeleteProviderResult:
    """Result of a delete provider operation."""
    provider_name: str
    existed_before: bool
    confirm_button_appeared: bool
    confirm_clicked: bool
    exists_after: bool
    deleted: bool
    feedback_message: str


def delete_provider_by_name(page: "Page", provider_name: str) -> DeleteProviderResult:
    """Delete a provider using two-step confirmation (click delete, then click Confirm?).

    Returns a DeleteProviderResult with details about what happened.
    """
    _select_tab(page, "providers")

    # Check if provider exists before deletion
    existed_before = provider_exists(page, provider_name)
    if not existed_before:
        return DeleteProviderResult(
            provider_name=provider_name,
            existed_before=False,
            confirm_button_appeared=False,
            confirm_clicked=False,
            exists_after=False,
            deleted=False,
            feedback_message=f"Provider '{provider_name}' not found"
        )

    # Find and click the delete button for this provider (first click)
    first_click = page.evaluate(
        """
(providerName) => {
  const headers = document.querySelectorAll('.provider-card__header-text');
  for (const header of headers) {
    const text = header.textContent || '';
    if (text.includes(providerName)) {
      const row = header.closest('.provider-card__header-row');
      if (row) {
        const deleteBtn = row.querySelector('.provider-delete');
        if (deleteBtn) {
          deleteBtn.click();
          return true;
        }
      }
    }
  }
  return false;
}
""",
        provider_name
    )

    if not first_click:
        return DeleteProviderResult(
            provider_name=provider_name,
            existed_before=existed_before,
            confirm_button_appeared=False,
            confirm_clicked=False,
            exists_after=provider_exists(page, provider_name),
            deleted=False,
            feedback_message=f"Could not find delete button for '{provider_name}'"
        )

    # Wait for button to change to tick symbol
    page.wait_for_timeout(500)

    # Check if any button now shows the confirm symbol (✓) or has stop variant
    confirm_button_appeared = page.evaluate(
        """
() => {
  const buttons = document.querySelectorAll('.provider-delete');
  for (const btn of buttons) {
    // Check for confirm symbol (tick) or stop variant class
    const text = btn.textContent || '';
    const hasConfirmSymbol = text.includes('✓');
    const hasStopVariant = btn.classList.contains('stop');
    if (hasConfirmSymbol || hasStopVariant) {
      return true;
    }
  }
  return false;
}
"""
    )

    if not confirm_button_appeared:
        return DeleteProviderResult(
            provider_name=provider_name,
            existed_before=existed_before,
            confirm_button_appeared=False,
            confirm_clicked=False,
            exists_after=provider_exists(page, provider_name),
            deleted=False,
            feedback_message="Confirm button did not appear after first click"
        )

    # Click the confirm button (second click)
    confirm_clicked = page.evaluate(
        """
() => {
  const buttons = document.querySelectorAll('.provider-delete');
  for (const btn of buttons) {
    const text = btn.textContent || '';
    const hasConfirmSymbol = text.includes('✓');
    const hasStopVariant = btn.classList.contains('stop');
    if (hasConfirmSymbol || hasStopVariant) {
      btn.click();
      return true;
    }
  }
  return false;
}
"""
    )

    # Wait for deletion to process
    page.wait_for_timeout(1000)

    # Check if provider still exists
    exists_after = provider_exists(page, provider_name)

    # Get feedback message
    feedback = page.evaluate(
        """
() => {
  // Look for feedback in the provider panel area
  const feedback = document.querySelector('.provider-summary');
  return feedback ? feedback.textContent : '';
}
"""
    ) or ""

    return DeleteProviderResult(
        provider_name=provider_name,
        existed_before=existed_before,
        confirm_button_appeared=confirm_button_appeared,
        confirm_clicked=confirm_clicked,
        exists_after=exists_after,
        deleted=existed_before and not exists_after,
        feedback_message=feedback.strip()
    )


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


@dataclass
class LiveStreamTestResult:
    """Result of testing live stream content."""
    content_visible: bool
    has_colored_spans: bool
    color_is_readable: bool
    has_diff_classes: bool
    raw_html: str
    computed_colors: list[dict]


def inject_live_stream_content(page: "Page", html_content: str) -> None:
    """Inject test content into the live stream box for testing.

    This makes the live stream box visible and inserts test HTML content.
    """
    page.evaluate(
        """
(htmlContent) => {
    const box = document.querySelector('#live-stream-box');
    if (!box) return false;
    // Make the box visible and prominent
    box.style.display = 'block';
    box.style.minHeight = '300px';
    // Find the markdown content area or create one
    let contentDiv = box.querySelector('.live-output-content');
    if (!contentDiv) {
        contentDiv = document.createElement('div');
        contentDiv.className = 'live-output-content';
        box.appendChild(contentDiv);
    }
    contentDiv.innerHTML = htmlContent;
    contentDiv.style.minHeight = '250px';
    // Scroll into view
    box.scrollIntoView({ behavior: 'instant', block: 'center' });
    return true;
}
""",
        html_content
    )
    page.wait_for_timeout(100)


def check_live_stream_colors(page: "Page") -> LiveStreamTestResult:
    """Check if colors in the live stream are readable.

    Returns details about color spans and their computed colors.
    """
    result = page.evaluate(
        """
() => {
    const box = document.querySelector('#live-stream-box');
    if (!box) return null;

    const contentDiv = box.querySelector('.live-output-content');
    if (!contentDiv) return null;

    // Get all color spans
    const colorSpans = contentDiv.querySelectorAll('span[style*="color"]');
    const computedColors = [];

    for (const span of colorSpans) {
        const computed = window.getComputedStyle(span);
        const text = span.textContent || '';
        computedColors.push({
            text: text.substring(0, 50),
            inlineStyle: span.getAttribute('style') || '',
            computedColor: computed.color,
            computedBackground: computed.backgroundColor
        });
    }

    // Check for diff classes
    const diffAdds = contentDiv.querySelectorAll('.diff-add');
    const diffRemoves = contentDiv.querySelectorAll('.diff-remove');
    const diffHeaders = contentDiv.querySelectorAll('.diff-header');

    // Get raw HTML
    const rawHtml = contentDiv.innerHTML;

    return {
        hasColoredSpans: colorSpans.length > 0,
        hasDiffClasses: diffAdds.length > 0 || diffRemoves.length > 0 || diffHeaders.length > 0,
        rawHtml: rawHtml,
        computedColors: computedColors
    };
}
"""
    )

    if not result:
        return LiveStreamTestResult(
            content_visible=False,
            has_colored_spans=False,
            color_is_readable=False,
            has_diff_classes=False,
            raw_html="",
            computed_colors=[]
        )

    # Check if colors are readable (not too dark on dark background)
    color_is_readable = True
    for color_info in result.get('computedColors', []):
        computed = color_info.get('computedColor', '')
        # Parse rgb values and check brightness
        if 'rgb' in computed:
            match = re.search(r'rgb\((\d+),\s*(\d+),\s*(\d+)\)', computed)
            if match:
                r, g, b = int(match.group(1)), int(match.group(2)), int(match.group(3))
                # Calculate perceived brightness (ITU-R BT.709)
                brightness = 0.2126 * r + 0.7152 * g + 0.0722 * b
                # If brightness is too low (< 80), text is hard to read on dark background
                if brightness < 80:
                    color_is_readable = False
                    break

    return LiveStreamTestResult(
        content_visible=True,
        has_colored_spans=result.get('hasColoredSpans', False),
        color_is_readable=color_is_readable,
        has_diff_classes=result.get('hasDiffClasses', False),
        raw_html=result.get('rawHtml', ''),
        computed_colors=result.get('computedColors', [])
    )


def verify_all_text_visible(page: "Page", min_brightness: int = 80) -> dict:
    """Verify that ALL text in the live stream box is visible (not too dark).

    This checks every text node, not just colored spans, to ensure Tailwind's
    prose class doesn't override our light text colors.

    Returns a dict with:
        - all_visible: bool - True if all text has sufficient brightness
        - dark_elements: list of dicts with details about dark elements
        - sample_colors: list of computed colors for verification
    """
    result = page.evaluate(
        """
(minBrightness) => {
    const box = document.querySelector('#live-stream-box');
    if (!box) return { error: 'live-stream-box not found' };

    const contentDiv = box.querySelector('.live-output-content');
    if (!contentDiv) return { error: 'live-output-content not found' };

    function parseBrightness(colorStr) {
        const match = colorStr.match(/rgb\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
        if (!match) return 255;  // Assume visible if can't parse
        const r = parseInt(match[1]);
        const g = parseInt(match[2]);
        const b = parseInt(match[3]);
        return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    }

    const darkElements = [];
    const sampleColors = [];

    // Check all elements with text content
    const walker = document.createTreeWalker(contentDiv, NodeFilter.SHOW_TEXT);
    const seen = new Set();

    while (walker.nextNode()) {
        const textNode = walker.currentNode;
        const text = textNode.textContent.trim();
        if (!text) continue;

        const parent = textNode.parentElement;
        if (!parent || seen.has(parent)) continue;
        seen.add(parent);

        const computed = window.getComputedStyle(parent);
        const color = computed.color;
        const brightness = parseBrightness(color);

        sampleColors.push({
            text: text.substring(0, 40),
            color: color,
            brightness: brightness,
            tagName: parent.tagName,
            className: parent.className
        });

        if (brightness < minBrightness) {
            darkElements.push({
                text: text.substring(0, 60),
                color: color,
                brightness: brightness,
                tagName: parent.tagName,
                className: parent.className
            });
        }
    }

    return {
        allVisible: darkElements.length === 0,
        darkElements: darkElements,
        sampleColors: sampleColors.slice(0, 10)  // Limit sample size
    };
}
""",
        min_brightness
    )
    return result or {"error": "evaluation returned null"}
