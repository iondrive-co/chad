---
name: taking-screenshots
description: Captures UI screenshots with Playwright. Use for before/after visual verification or debugging UI. Works for both Gradio web UI AND CLI terminal interface.
metadata:
  short-description: Capture UI screenshots
---

# Taking Screenshots

This skill covers screenshots for BOTH the Gradio web UI AND the CLI terminal interface.

## Gradio UI Screenshots

```bash
# Capture silently (default – light mode only, does not open browser)
./.venv/bin/python scripts/screenshot_ui.py --tab run --headless

# Capture both color schemes - this is only needed if changing something dark mode related
./.venv/bin/python scripts/screenshot_ui.py --tab run --headless --color-scheme both

# If you want the saved image to pop open after capture, this is only needed if showing the user something
./.venv/bin/python scripts/screenshot_ui.py --tab run --headless --open
```

### Gradio Options

- `--tab run|providers` - Which tab
- `--selector "CSS"` - Specific element
- `--output path` - Save location

### Gradio Selectors

- `#run-top-row` - Dropdowns
- `#agent-chatbot` - Chat
- `#live-stream-box` - Activity
- `#provider-summary-panel` - Providers

## CLI Terminal Screenshots

CLI interfaces CAN be screenshotted using `screenshot_cli.py`. This renders terminal output as a PNG image.

```bash
# Capture a command's output
./.venv/bin/python scripts/screenshot_cli.py --command "chad --help" --output /tmp/chad/cli.png

# Capture from a text file (useful for menu captures)
./.venv/bin/python scripts/screenshot_cli.py --input menu_output.txt --output /tmp/chad/cli_menu.png

# Set terminal width for wider output
./.venv/bin/python scripts/screenshot_cli.py --command "chad --help" --width 120
```

### CLI Options

- `--command`, `-c` - Command to run and capture
- `--input`, `-i` - Read output from file instead of running command
- `--output`, `-o` - Save location (default: /tmp/chad/cli_screenshot.png)
- `--width`, `-w` - Terminal width in columns (default: 100)
- `--title`, `-t` - Title for the screenshot
- `--open` - Open screenshot after capturing

## General Workflow

1. Take a screenshot of the part of the app you will be modifying before making any changes
2. Examine the screenshot image file and describe what it shows
   - Quick console peek (no GUI):
     ```bash
     ./.venv/bin/python - <<'PY'
     from PIL import Image
     img = Image.open("/tmp/chad/screenshot.png")
     print(img.size, img.mode)
     # Example: inspect a specific element height (y-range with non-white pixels)
     pix = img.convert("RGB").load()
     w,h = img.size
     rows = [any(pix[x,y] != (255,255,255) for x in range(w)) for y in range(h)]
     first = rows.index(True); last = len(rows)-1-rows[::-1].index(True)
     print("content rows:", first, last, "height:", last-first+1)
     PY
     ```
3. Check if this description matches what you expect. If not either adjust your plan, or work out if you need to write a
new test to capture what you expect to see, and do these until the description matches what you expect.
4. Make changes
5. Screenshot after with same options
6. Compare visually
