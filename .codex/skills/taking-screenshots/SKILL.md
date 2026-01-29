---
name: taking-screenshots
description: Captures UI screenshots with Playwright. Use for before/after visual verification or debugging UI. Works for both Codex and Claude runs; screenshots are silent unless you explicitly opt in to opening them.
metadata:
  short-description: Capture UI screenshots
---

# Taking Screenshots

```bash
# Capture silently (default – light mode only, does not open browser)
./.venv/bin/python scripts/screenshot_ui.py --tab run --headless

# Capture both color schemes - this is only needed if changing something dark mode related
./.venv/bin/python scripts/screenshot_ui.py --tab run --headless --color-scheme both

# If you want the saved image to pop open after capture, this is only needed if showing the user something
./.venv/bin/python scripts/screenshot_ui.py --tab run --headless --open
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
