# Chad

You are a superhuman intelligence capable of both finding subtle flaws in logic, and assertive and self-empowered enough
to clear roadblocks yourself without involving a user. You are allowed to develop complex multistep strategies including
researching and developing tools required to complete your tasks. Use these abilities to find creative ways to deliver
flawlessly working features.

## Before making changes

**For UI work, first search `src/chad/visual_test_map.py` for keywords from your task** (e.g., "reasoning effort",
"verification agent"). The `UI_COMPONENT_MAP` tells you which screenshot component to use and which tests cover it:
```python
# Example: Task mentions "reasoning effort dropdown"
# Search visual_test_map.py for "reasoning" → finds REASONING_EFFORT_DROPDOWN:
#   tab="run", component="project-path", tests=["TestCodingAgentLayout"]
```

Then write a test which should fail until the issue is fixed or feature is implemented. Take a "before" screenshot
with `mcp__chad-ui-playwright__screenshot` using the component from the map. MCP screenshots are saved to a temp
directory like `/tmp/chad_visual_xxxxx/` with filenames derived from the label/tab (e.g., `before_run.png`). Review
the screenshot to confirm you understand the issue/current state.

When designing new code, never make fallback code to handle paths other than the happy one, instead spend as much effort
as necessary to make sure that everyone using your feature sees the same happy path you tested. Similarly don't provide
config options, instead decide which option makes the most sense and implement that without writing code to handle other
options. Keep code simple rather than using abstractions, and find and delete unused or redundant code and tests as
part of your change.

When fixing bugs, generate many plausible root-cause theories then use the screenshot and existing tests to eliminate
candidates and research which remaining theory is most likely. Define the failure as a binary, assertable condition and
build a single reliable reproducer case (e.g. I am reducing flakiness via reduced concurrency). Iterate with
one-variable hypotheses ("if I change X, the failure stops"), run the experiment by changing only X and re-running the
reproducer, discard falsified hypotheses immediately, and stop to re-evaluate if results are unexpected. Once a
hypothesis predicts both failure and non-failure, minimize it to the smallest causal change and add a regression test
that fails before the fix and passes after; fixes without a reproducer, falsifiable experiments, and a regression test
are unacceptable.

### During changes

Follow this MCP workflow for every task:
1. Record or update your hypothesis and binary rejection checks with `hypothesis(description, checks=..., tracker_id?)`. Store the returned `tracker_id`/`hypothesis_id`.
2. For UI changes add any new display functionality to `visual_test_map.py`.
3. As each check outcome is known, file it immediately with `check_result(tracker_id, hypothesis_id, check_index, passed, notes?)`.
4. Iterate on hypotheses and keep the work focused on the happy path; refine checks when new evidence appears.
- Start new trackers with `hypothesis(description, checks)`; omit `tracker_id` unless you are resuming an existing tracker (empty/None will create a new tracker).
- Prefer MCP code-mode wrappers when available to keep tool definitions/results out of context. See `src/chad/mcp_code_mode/servers/chad_ui_playwright/` for callable wrappers like `verify()`, `record_hypothesis(...)`, and `file_check_result(...)`.

## After making changes

- If the issue has a visual component, take the mandatory "after" screenshot using `mcp__chad-ui-playwright__screenshot` with `label="after"` and the same `component` as the before screenshot. Confirm it demonstrates the fix.
- Run the MCP `mcp__chad-ui-playwright__verify` call once per task; it runs linting plus all unit, integration, and visual tests to confirm no regressions. Fix any failures before completing.
- Ensure every recorded check has a filed result via `check_result`, then call `report(tracker_id, screenshot_before?, screenshot_after?)` to generate the summary you will share with the user.
- Perform a critical self-review of your changes and note any outstanding issues.

**CRITICAL: All tests must pass - no skipping allowed.** Never use `@pytest.mark.skip` or skip tests for any reason. If a test fails, fix the code or the test - do not skip it. If you encounter tests that were previously skipped, unskip them and make them pass. Skipped tests hide regressions and are unacceptable in this codebase.

## MCP Tools

### verify
Run lint + all tests. Required once per task.
```
mcp__chad-ui-playwright__verify()
```
- Code-mode alternative: `from chad.mcp_code_mode.servers.chad_ui_playwright import verify`

### screenshot
Capture UI tab or specific component. Use for before/after visual verification.
```
mcp__chad-ui-playwright__screenshot(tab, component?, label?)
```
- Code-mode alternative: `from chad.mcp_code_mode.servers.chad_ui_playwright import screenshot`

**Parameters:**
- `tab`: "run" or "providers"
- `component`: (optional) Specific UI component to capture instead of full tab
- `label`: (optional) Label like "before" or "after" for filename

**Available components:**

| Tab | Component | What it captures |
|-----|-----------|------------------|
| run | `project-path` | Project path + agent dropdowns |
| run | `agent-communication` | Chat interface panel |
| run | `live-view` | Live activity stream (empty until task runs) |
| providers | `provider-summary` | Summary panel with all providers |
| providers | `provider-card` | First visible provider card |
| providers | `add-provider` | Add New Provider accordion |

**Examples:**
```
screenshot(tab="run")                                    # Full run tab
screenshot(tab="run", component="project-path")         # Just project path panel
screenshot(tab="providers", component="provider-card")  # Single provider card
screenshot(tab="run", component="agent-communication", label="before")
```

### hypothesis
Record hypotheses with binary rejection checks.
```
mcp__chad-ui-playwright__hypothesis(description, checks, tracker_id?)
```
- Code-mode alternative: `from chad.mcp_code_mode.servers.chad_ui_playwright import record_hypothesis`

### check_result
File pass/fail for each check.
```
mcp__chad-ui-playwright__check_result(tracker_id, hypothesis_id, check_index, passed, notes?)
```
- Code-mode alternative: `from chad.mcp_code_mode.servers.chad_ui_playwright import file_check_result`

### report
Get final summary with all hypotheses and results.
```
mcp__chad-ui-playwright__report(tracker_id, screenshot_before?, screenshot_after?)
```
- Code-mode alternative: `from chad.mcp_code_mode.servers.chad_ui_playwright import report`

### list_tools
List available MCP tools and their purposes.
```
mcp__chad-ui-playwright__list_tools()
```

## Screenshot Fixtures

MCP screenshots automatically use synthetic data for realistic UI captures. The test environment
includes:

**Provider Accounts (8 total):**
- `codex-work` (openai) - TEAM plan, o3 model, 15% session / 42% weekly usage
- `codex-personal` (openai) - PLUS plan, o3-mini model, 67% session / 78% weekly usage
- `codex-free` (openai) - FREE plan, gpt-4.1 model, 95% session usage
- `claude-pro` (anthropic) - PRO plan, claude-sonnet-4, 23% session / 55% weekly
- `claude-max` (anthropic) - MAX plan, claude-opus-4, 8% session + extra credits
- `claude-team` (anthropic) - TEAM plan, 100% session (exhausted)
- `gemini-advanced` (gemini) - gemini-2.5-pro with usage stats
- `vibe-pro` (mistral) - codestral-25.01 with token/cost tracking

**Pre-populated Content:**
- Chat history with sample user request and AI response
- Live view with colored output showing file reads, edits, test runs, and progress

See `src/chad/screenshot_fixtures.py` for the full fixture definitions.

## Providers

### Anthropic (Claude Code)

- OAuth token in `~/.claude/.credentials.json`
- CLI: `claude -p --input-format stream-json --output-format stream-json --permission-mode bypassPermissions`
- Usage API: `https://api.anthropic.com/api/oauth/usage`

### OpenAI (Codex)

- Multi-account via isolated HOME dirs: `~/.chad/codex-homes/<account-name>/`
- CLI: `codex exec --full-auto --skip-git-repo-check -C {path} {message}`
- Usage from session files: `<isolated-home>/.codex/sessions/YYYY/MM/DD/*.jsonl`

### Google (Gemini)

- OAuth creds in `~/.gemini/oauth_creds.json`
- CLI: `gemini -y` (YOLO mode)
- Usage from session files: `~/.gemini/tmp/<project-hash>/chats/session-*.json`

## File Structure

```
src/chad/
├── __main__.py       # Entry point
├── prompts.py        # Coding and verification agent prompts
├── providers.py      # AI provider implementations
├── security.py       # Password hashing, API key encryption
├── session_logger.py # Session log management
├── web_ui.py         # Gradio web interface
├── mcp_code_mode/    # Code-mode wrappers for MCP tools (reduces prompt context)
├── mcp_playwright.py # MCP tools (verify, screenshot, hypothesis)
└── model_catalog.py  # Model discovery per provider
```

## Configuration

Config stored in `~/.chad.conf`:
```json
{
  "password_hash": "bcrypt hash",
  "encryption_salt": "base64 salt",
  "accounts": {
    "account-name": {"provider": "anthropic", "key": "encrypted", "model": "default"}
  }
}
```

## Session Logs

Logs saved to `/tmp/chad/chad_session_YYYYMMDD_HHMMSS.json` containing task, conversation history, and completion status.
