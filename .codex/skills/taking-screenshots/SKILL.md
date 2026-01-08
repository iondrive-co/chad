---
name: taking-screenshots
description: Captures UI screenshots with Playwright. Use for before/after visual verification or debugging UI.
metadata:
  short-description: Capture UI screenshots
---

# Taking Screenshots

```bash
./venv/bin/python scripts/screenshot_ui.py --tab run --headless
```

## Options

- `--tab run|providers` - Which tab
- `--selector "CSS"` - Specific element
- `--output path` - Save location

## Selectors

- `#run-top-row` - Dropdowns
- `#agent-chatbot` - Chat
- `#live-stream-box` - Activity
- `#provider-summary-panel` - Providers
