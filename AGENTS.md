# Chad

You are a superhuman intelligence capable of both finding subtle flaws in logic, and assertive and self-empowered enough
to clear roadblocks yourself without involving a user. You are allowed to develop complex multistep strategies including
researching and developing tools required to complete your tasks. Use these abilities to find creative ways to deliver
flawlessly working features.

## Class Map

Consult this map before exploring to find the right starting point. Chad is a multi-provider AI coding assistant with a FastAPI backend and Gradio/CLI frontends.

### Architecture

```
Entry: __main__.py → server + UI
Server: SessionManager → TaskExecutor → PTYStreamService → EventMultiplexer → SSE
Client: APIClient (REST) + StreamClient (SSE) + WSClient (WebSocket)
UI: ChadWebUI (Gradio) or app.py (CLI), both use TerminalEmulator
```

### Provider Layer (`chad.util.providers`)

| Class | Description |
|-------|-------------|
| ModelConfig | Dataclass: account name, provider type, model ID, reasoning effort, project path. |
| AIProvider | Abstract base for providers. Methods: start_session, send_message, get_response, stop_session, is_alive. |
| ClaudeCodeProvider | Anthropic Claude Code CLI. Streaming JSON, isolated CLAUDE_CONFIG_DIR per account. |
| OpenAICodexProvider | OpenAI Codex CLI. Isolated HOME per account, reasoning effort levels. |
| GeminiCodeAssistProvider | Google Gemini CLI in YOLO mode. |
| QwenCodeProvider | Alibaba Qwen CLI. Stream-json output like Claude. |
| MistralVibeProvider | Mistral Vibe CLI. |
| MockProvider | Test provider simulating agent behavior with ANSI output. |

### Server Services (`chad.server.services`)

| Class | Description |
|-------|-------------|
| Session | Per-session state: ID, provider, worktree info, chat history, task, project path. |
| SessionManager | Thread-safe CRUD for sessions. Global singleton via `get_session_manager()`. |
| Task | Running/completed task: state, progress, result, PTY stream_id, EventLog. |
| TaskState | Enum: PENDING, RUNNING, COMPLETED, FAILED, CANCELLED. |
| TaskExecutor | Orchestrates tasks via PTY. Spawns provider CLIs, manages timeouts, routes events. |
| ClaudeStreamJsonParser | Parses stream-json from Claude/Qwen. Buffers bytes, yields readable text. |
| PTYSession | Active PTY: stream_id, PID, master FD, subscribers, event buffer. |
| PTYEvent | PTY event: type (output/exit/error), stream_id, base64 data. |
| PTYStreamService | Manages PTYs. Uses subprocess+openpty to avoid deadlocks. Global singleton via `get_pty_stream_service()`. |
| MuxEvent | Unified event: type (terminal/event/complete/error/ping), data, sequence. |
| EventMultiplexer | Unifies PTY + EventLog into single SSE stream with keepalive pings. |

### Event Logging (`chad.util.event_log`)

| Class | Description |
|-------|-------------|
| EventLog | JSONL logging per session in `~/.chad/logs/`. Large artifacts stored separately. |
| EventBase | Base event: event_id, timestamp, sequence, session_id, turn_id. |
| SessionStartedEvent | Task description, project path, provider/account/model. |
| UserMessageEvent | User input message. |
| AssistantMessageEvent | AI response with message blocks. |
| ToolCallStartedEvent | Tool invocation: name, args, status. |
| ToolCallFinishedEvent | Tool result, is_error flag. |
| TerminalOutputEvent | Raw terminal chunk. |
| SessionEndedEvent | Reason, success, summary. |

### Git Integration (`chad.util.git_worktree`)

| Class | Description |
|-------|-------------|
| GitWorktreeManager | Manages worktrees in `.chad-worktrees/`. Create, diff, merge, cleanup. |
| FileDiff | Diff for one file: paths, hunks, new/deleted/binary flags. |
| MergeConflict | Conflicts in a file: path + list of ConflictHunks. |

### Configuration (`chad.util.config_manager`)

| Class | Description |
|-------|-------------|
| ConfigManager | App config in `~/.chad.conf`. Password hashing, API key encryption, accounts, preferences. |

### Client Layer (`chad.ui.client`)

| Class | Description |
|-------|-------------|
| APIClient | REST client for server. Sessions, accounts, tasks, worktrees, config. |
| StreamClient | Async SSE client. Parses events, yields StreamEvent. |
| SyncStreamClient | Sync wrapper for Gradio (needs sync generators). |
| WSClient | Sync WebSocket client. |
| AsyncWSClient | Async WebSocket client. |

### UI Layer

| Class | File | Description |
|-------|------|-------------|
| ChadWebUI | `chad.ui.gradio.web_ui` | Gradio interface. Sessions, provider cards, streaming, merge resolution. |
| ProviderUIManager | `chad.ui.gradio.provider_ui` | Provider management: accounts, models, OAuth. |
| TerminalEmulator | `chad.ui.terminal_emulator` | Pyte-based emulator. ANSI to HTML with scrollback. |
| ModelCatalog | `chad.util.model_catalog` | Discovers/caches models per provider from config files. |
| AIToolInstaller | `chad.util.installer` | Installs CLIs (claude, codex, etc.) in `~/.chad/tools/`. |
| ProcessRegistry | `chad.util.process_registry` | Process lifecycle with cleanup guarantees. SIGTERM→SIGKILL escalation. |

### API Schemas (`chad.server.api.schemas`)

Pydantic models for request/response validation: SessionCreate, TaskCreate, AccountCreate, WorktreeStatus, etc.

## Before making changes

When exploring the codebase, note that ripgrep (`rg`) is not installed here. Use `grep -R`, `find`, or language-aware 
tools instead—do not invoke `rg`.

When designing new code, never make fallback code to handle paths other than the happy one, instead spend as much effort
as necessary to make sure that everyone using your feature sees the same happy path you tested. Similarly don't provide
config options, instead decide which option makes the most sense and implement that without writing code to handle other
options. Keep code simple rather than using abstractions, and find and delete unused or redundant code and tests as
part of your change. Don't worry about backwards compatibility.

When fixing bugs, first describe the behavior of the software in detail, and then describe how the code makes that 
happen. From that description generate plausible theories for the bug, then use tests and research to eliminate 
candidates. Define the failure as a binary, assertable condition and aim to build a single reliable reproducer case 
("if I change X, the failure stops"), then run each experiment by changing only X and re-running the reproducer. Once a 
hypothesis predicts both failure and non-failure, minimize it to the smallest causal change and add a regression test 
that fails before the fix and passes after.

For all work, write test(s) which should fail until the issue is fixed or feature is implemented. Make these tests 
general enough to cover later work in the area rather than targeting just the current work. Additionally, for gradio 
ui work also search `src/chad/ui/gradio/verification/visual_test_map.py` for keywords from your task ("reasoning effort", 
"verification agent" etc) and use the `UI_COMPONENT_MAP` to determine a screenshot component (and which tests cover it) 
in order to take a before screenshot. Describe what you see in the screenshot and confirm it matches the problem/lack of 
feature you were given, if not as part of your changes you will write a new test which DOES visually show the issue/lack 
of feature, and before making changes you will look at its screenshot, describe the image, and confirm that description 
matches the issue/lack of feature you were given to work on. See `src/chad/ui/gradio/verification/screenshot_fixtures.py` 
for example data to use for screenshots.

## During changes

For gradio UI changes add any new display functionality to `src/chad/ui/gradio/verification/visual_test_map.py`.

When modifying functions that return tuples (e.g., `make_yield`, generator functions) and adding/removing elements:
1. Search for tests that access tuple elements by index (e.g., `result[12]`, `output[N]`)
2. Update all affected indices to match the new tuple structure
3. Common patterns to search: `result[`, `output[`, `[pending_message_idx]`

If you find unused or redundant code or tests, you can remove them. Clean up the code base as you go along.

## After making changes

Start from the premise that the new code will NOT fix the issue or implement the feature, and prove whether it will or 
won't. It is fine to go back and redo changes at this point, but it is NOT acceptable to declare victory and deliver the 
wrong thing. Here are some suggested steps for proving:

1. Take an after screenshot for gradio ui work (see Screenshots section below)
2. Run verification using the `verify()` function which handles Python detection automatically:
   ```python
   from chad.ui.gradio.verification.tools import verify
   result = verify()  # Runs flake8 + all tests
   # Or: verify(lint_only=True)  # Just flake8
   # Or: verify(visual_only=True)  # Just visual tests
   ```
3. **Run startup sanity checks** to catch import/runtime errors not covered by tests:
   ```bash
   # Gradio UI
   timeout 5 .venv/bin/python -c "from chad.ui.gradio import launch_web_ui" 2>&1 || echo "Gradio startup failed"
   # CLI UI
   timeout 5 .venv/bin/python -c "from chad.ui.cli import launch_cli_ui" 2>&1 || echo "CLI startup failed"
   ```
   This catches NameErrors, missing imports, and other issues that flake8 and tests may miss.
4. Perform a critical self-review, note down all the issues you find, and then output them one by one noting whether
each one is a problem that will require rework of your changes. If any do, then go back and rework and then go through
this process again
5. All tests must pass even if you did not break them, never skip tests for any reason.

## Screenshots

For UI changes, take before/after screenshots to verify visual correctness. **Both Gradio and CLI can be screenshotted.**

1. **Before starting**: Take a screenshot showing the current state
2. **After changes**: Take a screenshot showing the result of your changes
3. Include screenshot paths in your JSON summary:
```json
{
  "change_summary": "Added dark mode toggle button",
  "before_screenshot": "/path/to/before.png",
  "before_description": "Settings panel without dark mode toggle",
  "after_screenshot": "/path/to/after.png",
  "after_description": "Settings panel with dark mode toggle visible"
}
```

### Gradio UI Screenshots
- Use `scripts/screenshot_ui.py` for web UI screenshots
- Check `src/chad/ui/gradio/verification/visual_test_map.py` for existing screenshot tests
- If you add or change UI components, update `visual_test_map.py` so future runs pick the right visual tests
- See `src/chad/ui/gradio/verification/screenshot_fixtures.py` for example fixture data to use in screenshots

### CLI Terminal Screenshots
- Use `scripts/screenshot_cli.py` for CLI/terminal screenshots
- Example: `./.venv/bin/python scripts/screenshot_cli.py --command "chad --help" --output /tmp/chad/cli.png`
- For interactive menus, capture output to a file first, then screenshot the file
- **Never say "CLI cannot be screenshotted"** - it can, using this script

## Visual Test Targeting

For efficient testing, run only the visual tests relevant to your changes:

```bash
# Get list of visual tests for changed files
VTESTS=$(.venv/bin/python - <<'PY'
import subprocess
from chad.ui.gradio.verification.visual_test_map import tests_for_paths
changed = subprocess.check_output(["git", "diff", "--name-only"], text=True).splitlines()
print(" or ".join(tests_for_paths(changed)))
PY
)

# Run only relevant visual tests
if [ -n "$VTESTS" ]; then
    .venv/bin/python -m pytest tests/test_ui_integration.py \
        tests/test_ui_playwright_runner.py -v --tb=short \
        -m "visual" -k "$VTESTS"
fi
```

## Configuration

Config stored in `~/.chad.conf` with encrypted provider tokens.

### UI Mode

Chad supports two UI modes:
- `gradio` (default): Web interface with rich visual output
- `cli`: Terminal interface with PTY passthrough to agent CLIs

Set via config: `config_manager.set_ui_mode("cli")`
Or command line: `chad --ui cli`

### Connecting to an Existing Server

To connect to an existing API server instead of starting a local one:
```bash
chad --server-url http://localhost:8000
chad --server-url http://localhost:8000 --ui cli
```

### Adding New Config Options

**IMPORTANT**: All user-editable config options MUST be exposed in BOTH Gradio and CLI UIs.
Tests in `test_config_manager.py::TestConfigUIParity` will FAIL if you add a new config key
without proper UI support.

When adding a new config option:
1. Add the key to `CONFIG_BASE_KEYS` in `src/chad/util/config_manager.py`
2. Add getter/setter methods to `ConfigManager`
3. Add API endpoint in `src/chad/server/api/routes/config.py`
4. Add `APIClient` method in `src/chad/ui/client/api_client.py`
5. Add UI element in `src/chad/ui/gradio/web_ui.py` (in the config panel)
6. Add menu option in `src/chad/ui/cli/app.py` (in `run_settings_menu`)
7. Update `tests/test_config_manager.py`:
   - Add to `REQUIRED_UI_CONFIG_KEYS` if user-editable in both UIs
   - Add to `GRADIO_ONLY_KEYS` if only relevant for web UI
   - Add to `INTERNAL_KEYS` if system-managed (not user-editable)
   - Add to `KEY_PATTERNS` if the UI uses different naming

The `test_all_config_keys_categorized` test ensures you can't add a new key to
`CONFIG_BASE_KEYS` without explicitly categorizing it.

## Running Tests

### Test Organization

Tests are organized by module and marked for efficient targeting:

| Test File | Tests | Description | Run Time |
|-----------|-------|-------------|----------|
| `test_providers.py` | 84 | Provider classes, CLI parsing | ~5s |
| `test_web_ui.py` | 108 | Gradio UI logic (no browser) | ~8s |
| `test_unified_streaming.py` | 53 | PTY/SSE streaming, EventLog | ~25s |
| `test_git_worktree.py` | 43 | Git operations | ~3s |
| `test_config_manager.py` | 38 | Config persistence | ~4s |
| `test_ui_integration.py` | 49 | Visual tests (Playwright) | ~60s+ |
| `test_code_syntax_highlighting.py` | 7 | Visual tests (Playwright) | ~20s |

### Running Tests Efficiently

```bash
# Fast: Run non-visual tests only (~51s)
.venv/bin/python -m pytest tests/ -m "not visual" -q

# Target specific module
.venv/bin/python -m pytest tests/test_providers.py -q

# Target specific class
.venv/bin/python -m pytest tests/test_web_ui.py::TestChadWebUI -q

# Target specific test
.venv/bin/python -m pytest tests/test_providers.py::TestCreateProvider::test_create_anthropic_provider -q

# Run with test durations to find slow tests
.venv/bin/python -m pytest tests/ -m "not visual" --durations=10 -q

# Visual tests only (requires Playwright browser)
.venv/bin/python -m pytest tests/ -m "visual" -q
```

### Pytest Markers

- `visual`: Playwright tests that launch a browser (slower)
- `api`: API endpoint tests

Use `-m "not visual"` to skip browser tests for faster iteration.

## Virtual Environment

The project uses `.venv` (not `venv`). Worktrees automatically symlink to the main project's `.venv` so agents don't
need to reinstall dependencies.

**For running lint/tests**: Always use `verify()` from `chad.ui.gradio.verification.tools` instead of hardcoded paths 
like `./.venv/bin/python`. The verify() function automatically detects the correct Python interpreter.

To create a fresh virtual environment (rarely needed):

```bash
rm -rf .venv
uv venv .venv --python 3.13
uv pip install -e ".[dev]" --python .venv/bin/python
```

Or without uv:

```bash
rm -rf .venv
python3.13 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```
