---
name: screenshot
description: Capture UI screenshots for visual verification. Use for before/after comparisons, debugging UI issues, or documenting changes.
metadata:
  short-description: Capture Chad web UI screenshots with Playwright
---

# Screenshot - UI Capture Tool

Capture screenshots of the Chad web UI for visual verification.

## Quick Start

Capture the run tab:
```bash
cd /home/miles/chad
./venv/bin/python scripts/screenshot_ui.py --tab run --output /tmp/screenshot.png --headless
```

## Available Options

### Tabs
- `--tab run` - Task input and execution panel
- `--tab providers` - Provider management panel

### Components (optional, for focused captures)
Run tab components:
- `--selector "#run-top-row"` - Project path + agent dropdowns
- `--selector "#agent-chatbot"` - Chat interface panel
- `--selector "#live-stream-box"` - Live activity stream

Providers tab components:
- `--selector "#provider-summary-panel"` - Summary with all providers
- `--selector ".provider-cards-row .column:has(.provider-card__header-row)"` - Single provider card
- `--selector "#add-provider-panel"` - Add New Provider accordion

### Full Command
```bash
./venv/bin/python scripts/screenshot_ui.py \
    --tab run \
    --output /tmp/before_run.png \
    --width 1280 \
    --height 900 \
    --headless
```

## Before/After Workflow

1. **Before making changes:**
```bash
./venv/bin/python scripts/screenshot_ui.py --tab run --output /tmp/before.png --headless
```

2. **After making changes:**
```bash
./venv/bin/python scripts/screenshot_ui.py --tab run --output /tmp/after.png --headless
```

3. **Compare the screenshots** to verify the fix is correct

## Shared Browser Cache

Playwright browsers are cached at `~/.cache/ms-playwright` to avoid re-downloading.
If browsers aren't installed:
```bash
./venv/bin/python -m playwright install chromium
```

## Screenshot Output

Screenshots are saved to the specified output path. Both dark and light theme versions are captured automatically with suffixes `_dark.png` and `_light.png`.
