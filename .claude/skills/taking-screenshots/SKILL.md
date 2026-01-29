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
