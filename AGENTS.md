# Chad

## Before making changes

If the request is to fix an issue, write a test which should fail until the issue is fixed. If the issue has a visual
component then **you MUST take a "before" screenshot** using the MCP tool:
```
capture_visual_change(label="before", tab="providers", issue_id="your-issue-name")
```
This saves screenshots to `/tmp/chad/visual-changes/<timestamp>/` with descriptive filenames.
See the Visual Inspection section for more details.

Then, come up with as many theories as possible about potential causes of the issue, then examine your screenshot and 
the existing test coverage and see if this rules any of them out. Research and find out which of your theories is most
likely to be the cause. Build a plan for fixing which includes hypothesis testing, i.e. if this really is the cause,
then when I change X I expect to see Y. Make sure you run and verify these during development, if any of them fail or
have unexpected results then STOP and re-evaluate your theories to see if they are still correct.

## After making changes

Re-run your new test, ensure it passes and then:
- if the issue has a visual component, **you MUST take an "after" screenshot** using the MCP tool:
  ```
  capture_visual_change(label="after", tab="providers", issue_id="your-issue-name")
  ```
  Examine the screenshot critically to make sure it has changed from the before screenshot and actually demonstrates
  the fix has worked.
- regardless of whether there is a visual component, perform a critical code review of your changes and note any issues

If there are major issues with either of the above then consider whether you should instead pursue another one of your
theories and create some new ones. If there are only minor issues then you can just correct them here.

Next, run the verification script with the --quick option, and also run visual tests on areas you changed. Here are
some possible options for running verification:
```bash
python scripts/verify.py --quick      # Lint + unit tests only (fast)
python scripts/verify.py --lint       # Lint only
python scripts/verify.py --unit       # Unit tests only
python scripts/verify.py --ui         # UI tests only
python scripts/verify.py -k "pattern" # Tests matching pattern
python scripts/verify.py --file tests/test_web_ui.py  # Specific file
```
Fix any lint issues you discover regardless of whether your change caused them.

Finally, once you are satisfied, report back to the user with:
- A short summary of the issue
- How you proved that your fix worked and was the only explanation that made sense
- Before and after screenshot paths (from `/tmp/chad/visual-changes/`)
- Any remaining issues or failing tests

## Visual Inspection

Chad provides multiple ways to verify UI changes, listed from most to least rigorous:

### 1. Pytest Integration Tests

Run the Playwright-based UI integration tests:
```bash
PYTHONPATH=src python3 -m pytest tests/test_ui_integration.py -v
```
These tests:
- Start Chad with mock providers in a temporary environment
- Use Playwright to verify all UI elements render correctly
- Test the delete provider two-step confirmation flow
- Validate element sizing and visibility
- Take screenshots to pytest's `tmp_path` for inspection

### 2. MCP Tools

The MCP server is auto-configured via `.mcp.json` in the project root for Claude Code.

Codex does not support project-level MCP config. Users must add to `~/.codex/config.toml`:
```toml
[mcp_servers.chad-ui-playwright]
command = "python"
args = ["-m", "chad.mcp_playwright"]
cwd = "/path/to/chad"
env = { PYTHONPATH = "/path/to/chad/src" }
```
Verify with `/mcp` command in Claude Code or `codex mcp list` in Codex.

**Manual Start (if needed):**
```bash
PYTHONPATH=src python -m chad.mcp_playwright
```

**Available Tools:**

| Tool | Description |
|------|-------------|
| `capture_visual_change` | **REQUIRED** for before/after screenshots - saves to `/tmp/chad/visual-changes/` |
| `run_ui_smoke` | Full UI smoke test with screenshots of both tabs |
| `screenshot` | Capture screenshot of a specific tab |
| `run_tests_for_file` | Run visual tests covering a source file |
| `run_tests_for_modified_files` | Run tests for all git-modified files |
| `run_ci_tests` | Run full CI test suite (excludes visual by default) |
| `verify_all_tests_pass` | Complete verification before issue completion |
| `list_visual_test_mappings` | Show source file to test mappings |
| `test_add_provider_accordion_gap` | Test gap between cards and Add Provider button |
| `measure_provider_delete` | Verify delete button sizing |
| `list_providers` | Get visible provider names |
| `test_delete_provider` | Test the delete flow end-to-end |

**Workflow for UI Changes:**
1. **BEFORE making changes**: `capture_visual_change(label="before", issue_id="issue-name")`
2. Make code changes to fix the issue
3. **AFTER making changes**: `capture_visual_change(label="after", issue_id="issue-name")`
4. Run `run_tests_for_file` with the modified file path
5. Review screenshots in `/tmp/chad/visual-changes/` and `/tmp/chad/mcp-playwright/`
6. Before completing: run `verify_all_tests_pass`
7. Report screenshot paths to user

**Test Mappings:**
Source files declare their visual tests in `src/chad/visual_test_map.py`.
To add coverage for a new file, add an entry to `VISUAL_TEST_MAP`.

Artifacts are saved to `/tmp/chad/mcp-playwright/<timestamp>/`

Requirements: `pip install playwright mcp && playwright install chromium`

### 3. Quick Visual Checks

For quick visual verification without running full tests:

```bash
python scripts/screenshot_ui.py --tab providers --headless
python scripts/screenshot_ui.py --tab run -o /tmp/run-tab.png
```

Options:
- `--tab run|providers` - Which tab to screenshot
- `--output PATH` - Output path (default: `/tmp/chad/screenshot.png`)
- `--headless` - Run without visible browser window

## Providers

### Anthropic (Claude Code)

**Status:** Fully Implemented with Usage API

**Authentication:**
- OAuth token stored in `~/.claude/.credentials.json`
- Format: `{"claudeAiOauth": {"accessToken": "sk-ant-oat01-...", "subscriptionType": "pro", ...}}`
- Users authenticate by running `claude` in terminal (browser-based OAuth)

**Usage API:**
- Endpoint: `https://api.anthropic.com/api/oauth/usage`
- Headers:
  - `Authorization: Bearer {accessToken}`
  - `anthropic-beta: oauth-2025-04-20`
  - `User-Agent: claude-code/2.0.32`
- Response:
  ```json
  {
    "five_hour": {"utilization": 57.0, "resets_at": "2025-12-08T17:59:59+00:00"},
    "seven_day": {"utilization": 35.0, "resets_at": "2025-12-11T00:00:00+00:00"},
    "extra_usage": {"is_enabled": true, "monthly_limit": 4000, "used_credits": 514.0}
  }
  ```

**CLI Integration:**
- Command: `claude -p --input-format stream-json --output-format stream-json --permission-mode bypassPermissions`
- Uses streaming JSON for multi-turn conversations

---

### OpenAI (Codex)

**Status:** Fully Implemented with Usage via Session Files + Multi-Account Support

**Authentication:**
- OAuth JWT token stored in isolated home directories per account
- Each account gets its own directory: `~/.chad/codex-homes/<account-name>/.codex/auth.json`
- Users authenticate via the web UI "Login to Codex Account" button
- Format: `{"tokens": {"access_token": "eyJ...", ...}, "last_refresh": "..."}`

**Multi-Account Support:**
Chad supports multiple OpenAI/Codex accounts by using isolated HOME directories:
- Each account name maps to `~/.chad/codex-homes/<account-name>/`
- Codex CLI respects the `HOME` environment variable
- Running Codex with a custom HOME creates isolated auth and session data
- This allows work and personal accounts to run simultaneously

**Usage Information:**
The JWT token contains account metadata:
- `chatgpt_plan_type`: "plus", "pro", "team", etc.
- `email`: User's email address
- `exp`: Token expiration timestamp

**Usage API:**
The Codex CLI stores usage data in session files at `<isolated-home>/.codex/sessions/YYYY/MM/DD/*.jsonl`.
Each session file contains JSONL entries with `rate_limits` data:
```json
{
  "type": "event_msg",
  "payload": {
    "type": "token_count",
    "rate_limits": {
      "primary": {"used_percent": 10.0, "window_minutes": 300, "resets_at": 1765012711},
      "secondary": {"used_percent": 46.0, "window_minutes": 10080, "resets_at": 1765439179},
      "credits": {"has_credits": false, "unlimited": false, "balance": null}
    }
  }
}
```

Field mapping:
- `primary`: 5-hour rolling window (window_minutes: 300)
- `secondary`: Weekly rolling window (window_minutes: 10080)
- `resets_at`: Unix timestamp for when limit resets

**CLI Integration:**
- Command: `codex exec --full-auto --skip-git-repo-check -C {path} {message}`
- One-shot execution mode (no persistent session)
- Provider passes `env={'HOME': isolated_home}` to subprocess

---

### Google (Gemini)

**Status:** Fully Implemented with Usage via Session Files

**Authentication:**
- OAuth via browser when running `gemini` CLI
- Credentials stored in `~/.gemini/oauth_creds.json`

**Usage Information:**
The Gemini CLI stores session data in `~/.gemini/tmp/<project-hash>/chats/session-*.json`.
Each session file contains messages with token usage data:
```json
{
  "type": "gemini",
  "model": "gemini-2.5-pro",
  "tokens": {
    "input": 18732,
    "output": 46,
    "cached": 1818,
    "thoughts": 216,
    "tool": 0,
    "total": 18994
  }
}
```

Chad aggregates this data across all sessions to display:
- Token usage per model (requests, input tokens, output tokens)
- Cache savings (tokens served from cache)

**CLI Integration:**
- Command: `gemini -y` (YOLO mode for auto-approval)
- Requires `@google/gemini-cli` npm package

---

## Session Logs

Chad saves session logs to a dedicated directory in the system temp folder. Logs are created at session start and updated throughout:

```
/tmp/chad/chad_session_YYYYMMDD_HHMMSS.json
```

Each log contains:
- `timestamp`: ISO format timestamp when session started
- `task_description`: The original task
- `project_path`: Working directory
- `managed_mode`: Whether management AI supervision was enabled
- `coding`/`management`: Account and provider info
- `status`: Current status (`running`, `completed`, or `failed`)
- `success`: Whether the task completed successfully (null while running)
- `completion_reason`: Why the task ended
- `conversation`: Full chat history (updated in real-time)

To find session logs:
```bash
ls -la /tmp/chad/
```

Logs are updated in real-time, so you can monitor an ongoing session by watching the file.

## File Structure

```
src/chad/
├── __main__.py      # Entry point, password handling
├── providers.py     # AI provider implementations
├── security.py      # Password hashing, API key encryption
├── session_manager.py # Multi-provider session orchestration
└── web_ui.py        # Gradio web interface
```

## Configuration

Config stored in `~/.chad.conf`:
```json
{
  "password_hash": "bcrypt hash",
  "encryption_salt": "base64 salt",
  "accounts": {
    "account-name": {"provider": "anthropic", "key": "encrypted", "model": "default", "reasoning": "default"}
  },
  "role_assignments": {
    "CODING": "account-name",
    "MANAGEMENT": "account-name"
  }
}
```

### Model Selection

Each account can set its role and preferred model directly from its provider card in the Providers tab.
Model discovery is handled by `src/chad/model_catalog.py`, which merges:
- Fallback lists per provider
- Stored account model (so custom entries persist)
- Provider metadata (e.g., Codex `~/.codex/config.toml` migrations)
- Recent session files (e.g., `~/.codex/sessions/**/*.jsonl`)

Fallback models per provider (merged with discoveries):
- **Anthropic:** claude-sonnet-4-20250514, claude-opus-4-20250514, default
- **OpenAI:** gpt-5.2-codex, gpt-5.1-codex-max, gpt-5.1-codex, gpt-5.1-codex-mini, gpt-5.2, gpt-4.1, gpt-4.1-mini, o3, o4-mini, codex-mini, default
- **Gemini:** gemini-2.5-pro, gemini-2.5-flash, gemini-2.5-flash-lite, default
- **Mistral:** default

Custom models can also be entered manually (`allow_custom_value` is enabled). OpenAI accounts additionally store a `reasoning` preference (default, low, medium, high, xhigh) that is passed to the Codex CLI via `model_reasoning_effort`.

## Adding a New Provider

1. Create provider class in `providers.py` extending `AIProvider`
2. Implement: `start_session`, `send_message`, `get_response`, `stop_session`, `is_alive`
3. Add to `create_provider()` factory function
4. Add provider type to web UI dropdown in `web_ui.py`
5. Implement `_get_{provider}_usage()` method if usage API available
6. Document in this Architecture.md
