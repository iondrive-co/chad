# Chad Project Instructions

## Before Making Changes

Read the `README.md` and `Architecture.md` to understand the project.

## After Making Changes

1. Run flake8 linting and fix any issues:
   ```bash
   flake8 .
   ```

2. Run tests and ensure they pass:
   ```bash
   pytest
   ```

3. **For visual/UI changes**: You MUST verify changes visually using the screenshot utility:
   ```bash
   # Ensure Chad is running in another terminal first
   python scripts/screenshot_ui.py --tab task      # For Run Task tab changes
   python scripts/screenshot_ui.py --tab providers # For Providers tab changes
   ```
   Then use the Read tool to view `/tmp/chad_screenshot.png` and confirm the UI looks correct.

## Code Style

- Max line length: 120 characters
- C901 complexity warnings are ignored in flake8 config

## Visual Verification

When modifying `web_ui.py` or any UI-related code, you MUST:

1. Have Chad running locally (ask user to start it if needed)
2. Take screenshots of affected tabs using `python scripts/screenshot_ui.py`
3. View the screenshots with the Read tool to verify:
   - Layout renders correctly
   - Text is visible and properly formatted
   - Buttons and controls appear as expected
   - No visual glitches or broken styling

See `Architecture.md` section "Visual Inspection" for full details.