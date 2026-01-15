---
name: taking-screenshots
description: Captures UI screenshots with Playwright. Use for before/after visual verification or debugging UI.
metadata:
  short-description: Capture UI screenshots
---

# Taking Screenshots

```bash
./.venv/bin/python scripts/screenshot_ui.py --tab run --headless
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

## Workflow

1. Take a screenshot of the part of the app you will be modifying before making any changes
2. Examine the screenshot image file and describe what it shows
3. Check if this description matches what you expect. If not either adjust your plan, or work out if you need to write a 
new test to capture what you expect to see, and do these until the description matches what you expect.
4. Make changes
5. Screenshot after with same options
6. Compare visually