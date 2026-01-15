---
name: taking-screenshots
description: Captures UI screenshots for visual verification. Triggers on "screenshot", "capture UI", "before/after", "visual check", or when debugging UI issues.
allowed-tools: Bash, Read
---

# Taking Screenshots

```bash
./.venv/bin/python scripts/screenshot_ui.py --tab run --headless
```

## Options

| Flag | Values | Purpose |
|------|--------|---------|
| `--tab` | `run`, `providers` | Which tab to capture |
| `--selector` | CSS selector | Capture specific element |
| `--output` | path | Save location |

## Common selectors

- `#run-top-row` - Project path + dropdowns
- `#agent-chatbot` - Chat panel
- `#live-stream-box` - Activity stream
- `#provider-summary-panel` - Provider overview

## Workflow

1. Take a screenshot of the part of the app you will be modifying before making any changes
2. Examine the screenshot image file and describe what it shows
3. Check if this description matches what you expect. If not either adjust your plan, or work out if you need to write a 
new test to capture what you expect to see, and do these until the description matches what you expect.
4. Make changes
5. Screenshot after with same options
6. Compare visually
