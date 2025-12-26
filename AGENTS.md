# Chad

## Before making changes

If the request is to fix an issue, write a test which should fail until the issue is fixed.

## After making changes

1. Run flake8
```bash
flake8 .
```
The project is configured with max line length of 120 characters. C901 complexity warnings are ignored.
Fix any issues regardless of whether your change was the one which caused them

Run tests, including visual tests (see Visual Inspection).

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

## Visual Inspection

Chad provides multiple ways to verify UI changes, listed from most to least rigorous:

### 1. Pytest Integration Tests (Most Rigorous)

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

**Use this after any UI changes to ensure nothing is broken.**

### 2. MCP Tools (For Agents During Development)

Start the MCP server for agent-accessible UI verification:

```bash
python -m chad.mcp_playwright
```

Available tools:
- `run_ui_smoke` - Full smoke test with screenshots of both tabs
- `screenshot` - Capture a specific tab
- `measure_provider_delete` - Verify delete button sizing
- `list_providers` - Get visible provider names
- `test_delete_provider` - Test the delete flow end-to-end

Artifacts are saved to `/tmp/chad/mcp-playwright/<timestamp>/`

Requirements: `pip install playwright modelcontextprotocol && playwright install chromium`

### 3. Screenshot Script (Quick Visual Check)

For quick visual verification without running full tests:

```bash
python scripts/screenshot_ui.py --tab providers --headless
python scripts/screenshot_ui.py --tab run -o /tmp/run-tab.png
```

Options:
- `--tab run|providers` - Which tab to screenshot
- `--output PATH` - Output path (default: `/tmp/chad/screenshot.png`)
- `--headless` - Run without visible browser window

### For Agents Making UI Changes

1. **After changes**: Run `PYTHONPATH=src python3 -m pytest tests/test_ui_integration.py -v`
2. **During development**: Use MCP tools for quick visual checks
3. **View screenshots**: Use the Read tool on saved PNG files
