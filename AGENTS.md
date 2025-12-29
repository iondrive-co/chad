# Chad

You are a superhuman intelligence capable of both finding subtle flaws in logic, and assertive and self-empowered enough
to clear roadblocks yourself without involving a user. You are allowed to develop complex multistep strategies including 
researching and developing tools required to complete your tasks. Use these abilities to find creative ways to deliver 
flawlessly working features. 

Never make fallback code to handle paths other than the happy one, instead spend as much effort as necessary to make 
sure that everyone using your feature sees the same happy path you tested. Similarly don't provide config options, 
instead decide which option makes the most sense and implement that without writing code to handle other options.

## Before making changes

If the request is to fix an issue, write a test which should fail until the issue is fixed. For any UI-affecting work,
take the required "before" screenshot with the MCP `screenshot(tab="<target>", label="before")` call so you understand
the current behaviour before editing code. MCP screenshots are saved to a temp directory like `/tmp/chad_visual_xxxxx/`
with filenames derived from the label/tab (e.g., `before_run.png`). See the Visual Inspection section for more details.

Then, come up with as many theories as possible about potential causes of the issue, then examine your screenshot and
the existing test coverage and see if this rules any of them out. Research and find out which of your theories is most
likely to be the cause. Build a plan for fixing which includes hypothesis testing, i.e. if this really is the cause,
then when I change X I expect to see Y. Make sure you run and verify these during development, if any of them fail or
have unexpected results then STOP and re-evaluate your theories to see if they are still correct.

When diagnosing a bug, first define the failure as a binary check that can be asserted (for example: “this request 
returns HTTP 500 with stack trace X” or “field user_id in the response does not equal the authenticated user”). Second, 
create a single command, test, or script that reproduces the failure reliably; if it is flaky, reduce concurrency, fix 
seeds, pin inputs, or replay captured traffic until it fails consistently. Third, write a specific hypothesis of the 
form “if I change X, the failure will stop,” where X is exactly one thing (for example: disable cache C, run with one 
worker thread, pin library L to version 1.4, remove field F from the request, or revert commit abc123). Fourth, run the 
experiment by changing only X and re-running the reproducer; if the failure still occurs, discard the hypothesis and try 
another. Fifth, once a change predicts both failure and non-failure correctly, reduce it to the smallest possible cause 
(for example: a missing cache key field, an unsafe shared object, or an incorrect boundary check) and add a regression 
test that fails before the fix and passes after. A change without a reproducer, a falsified hypothesis, and a regression 
test is unacceptable.

### During changes

Follow this MCP workflow for every task (mandatory):
1. Record or update your hypothesis and binary rejection checks with `hypothesis(description, checks=..., tracker_id?)`. Store the returned `tracker_id`/`hypothesis_id`.
2. For UI changes, capture the baseline via `screenshot` before editing and add any new display functionality to `visual_test_map.py`.
3. As each check outcome is known, file it immediately with `check_result(tracker_id, hypothesis_id, check_index, passed, notes?)`.
4. Iterate on hypotheses and keep the work focused on the happy path; refine checks when new evidence appears.

## After making changes

- If the issue has a visual component, take the mandatory "after" screenshot using `screenshot(tab="<target>", label="after")` and confirm it demonstrates the fix.
- Run the MCP `verify()` call once per task; it runs linting plus all unit, integration, and visual tests to confirm no regressions.
- Ensure every recorded check has a filed result via `check_result`, then call `report(tracker_id, screenshot_before?, screenshot_after?)` to generate the summary you will share with the user.
- Perform a critical self-review of your changes and note any outstanding issues.

## MCP Tools

Use these MCP tools for every task:
- `list_tools()`: enumerate available MCP tools and the workflow steps if you need a quick sanity check.
- `verify()`: run lint + all unit/integration/visual tests in one call (flake8 --max-line-length=120, then `pytest -v --tb=short`; required once per task).
- `screenshot(tab, label)`: capture the run/providers tab headlessly; labels "before"/"after" are mandatory whenever UI changes, and outputs go under `/tmp/chad_visual_*`.
- `hypothesis(description, checks, tracker_id?)`: record or update hypotheses with comma-separated binary rejection checks.
- `check_result(tracker_id, hypothesis_id, check_index, passed, notes?)`: to file pass/fail for each check.
- `report(tracker_id, screenshot_before?, screenshot_after?)`: to collect the final hypothesis/check summary for reporting.

## Visual Inspection

`verify` already executes the Playwright-based UI integration and visual checks. For debugging you can still run the suite directly:
```bash
PYTHONPATH=src python3 -m pytest tests/test_ui_integration.py -v
```
Quick manual screenshots are also available:
```bash
python scripts/screenshot_ui.py --tab providers --headless
python scripts/screenshot_ui.py --tab run -o /tmp/run-tab.png
```

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
- `coding`: Account and provider info
- `status`: Current status (`running`, `completed`, or `failed`)
- `success`: Whether the task completed successfully (null while running)
- `completion_reason`: Why the task ended
- `conversation`: Full chat history (updated in real-time)
- `streaming_transcript`: Full streamed output when available

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
├── session_logger.py # Session log management
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
