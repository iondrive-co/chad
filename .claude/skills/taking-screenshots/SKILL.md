---
name: taking-screenshots
description: Captures UI screenshots for visual verification. Triggers on "screenshot", "capture UI", "before/after", "visual check", or when debugging UI issues.
allowed-tools: Bash, Read
---

# Taking Screenshots

```bash
python scripts/screenshot_ui.py --tab run --headless
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

## Before/after workflow

1. Screenshot before changes
2. Make changes
3. Screenshot after with same options
4. Compare visually
