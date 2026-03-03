---
name: taking-screenshots
description: Captures UI screenshots for visual verification. Triggers on "screenshot", "capture UI", "before/after", "visual check", or when debugging UI issues.
allowed-tools: Bash, Read
---

# Taking Screenshots

This skill covers screenshots for the React web UI and the CLI terminal interface.

**IMPORTANT**: ALWAYS use the project's screenshot infrastructure. DO NOT write custom screenshot code using PIL, Pillow,
pyautogui, or any other library. The project tools handle all the complexity including headless browser automation,
proper rendering, and terminal emulation.

## React UI Screenshots

Use the `ui_runner` module to start the API server (which serves the React UI from `ui/dist/`) and capture with Playwright:

```python
from chad.util.verification.ui_runner import create_temp_env, start_chad, stop_chad, open_playwright_page

env = create_temp_env()
instance = start_chad(env)
with open_playwright_page(instance.port, tab="chat", headless=True) as page:
    page.screenshot(path="/tmp/chad/screenshot.png")
stop_chad(instance)
env.cleanup()
```

Or use `scripts/release_screenshots.py` for the standard release screenshots:
```bash
./.venv/bin/python scripts/release_screenshots.py
```

### React Tab Names

- `chat` - Main chat/task interface (default)
- `providers` - Provider/account management
- `settings` - Settings panel

### React CSS Selectors

- `.app-header` - Top header with tabs
- `.sidebar` - Session list sidebar
- `.main` - Main content area
- `.chat-view` - Chat view container

## CLI Terminal Screenshots

CLI interfaces CAN be screenshotted using `screenshot_cli.py`. This script captures terminal output and renders it as a PNG image.

```bash
# Capture a command's output
./.venv/bin/python scripts/screenshot_cli.py --command "chad --help" --output /tmp/chad/cli.png

# Capture from a text file (useful for menu captures)
./.venv/bin/python scripts/screenshot_cli.py --input menu_output.txt --output /tmp/chad/cli_menu.png

# Set terminal width for wider output
./.venv/bin/python scripts/screenshot_cli.py --command "chad --help" --width 120
```

### CLI Options

| Flag | Values | Purpose |
|------|--------|---------|
| `--command`, `-c` | shell command | Command to run and capture |
| `--input`, `-i` | path | Read output from file instead of running command |
| `--output`, `-o` | path | Save location (default: /tmp/chad/cli_screenshot.png) |
| `--width`, `-w` | number | Terminal width in columns (default: 100) |
| `--title`, `-t` | string | Title for the screenshot |
| `--open` | flag | Open screenshot after capturing |

## General Workflow

1. Take a screenshot of the part of the app you will be modifying before making any changes
2. Examine the screenshot image file and describe what it shows
3. Check if this description matches what you expect. If not, adjust your plan.
4. Make changes
5. Screenshot after with same options
6. Compare visually
