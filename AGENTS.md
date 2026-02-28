# Chad

You are a superhuman intelligence capable of both finding subtle flaws in logic, and assertive and self-empowered enough
to clear roadblocks yourself without involving a user. You are allowed to develop complex multistep strategies including
researching and developing tools required to complete your tasks. Use these abilities to find creative ways to deliver
flawlessly working features.

## Class Map

Consult this map before exploring to find the right starting point. Chad is a multi-provider AI coding assistant with a FastAPI backend, React web UI, and CLI frontend.

### Architecture

```
Entry: __main__.py → server + UI
Server: SessionManager → TaskExecutor → PTYStreamService → EventMultiplexer → SSE
Client: APIClient (REST) + StreamClient (SSE) + WSClient (WebSocket)
UI: React (ui/) or app.py (CLI), both use server API
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
| SyncStreamClient | Sync wrapper (needs sync generators). |
| WSClient | Sync WebSocket client. |
| AsyncWSClient | Async WebSocket client. |

### React UI (`ui/`)

| Component | File | Description |
|-----------|------|-------------|
| App | `ui/src/App.tsx` | Root component. Tabs: Chat, Providers, Settings. |
| ChatView | `ui/src/components/ChatView.tsx` | Main task interface. Terminal output, events, worktree merging. |
| TaskForm | `ui/src/components/TaskForm.tsx` | Task input form. |
| ProvidersPanel | `ui/src/components/ProvidersPanel.tsx` | Provider/account management. |
| SettingsPanel | `ui/src/components/SettingsPanel.tsx` | Settings management. |
| SessionList | `ui/src/components/SessionList.tsx` | Session selection/creation sidebar. |
| DiffViewer | `ui/src/components/DiffViewer.tsx` | Git diff viewing. |
| MergePanel | `ui/src/components/MergePanel.tsx` | Merge conflict resolution. |
| useStream | `ui/src/hooks/useStream.ts` | SSE streaming hook. Terminal output decoding. |
| useSessions | `ui/src/hooks/useSessions.ts` | Session management hook. |

TypeScript client library: `client/src/` (ChadAPI, ChadStream, ChadWebSocket).
Dev server: `cd ui && bash dev.sh` (starts API + Vite on port 5173).
Built output: `ui/dist/` (served by API server at `/`).

### Other UI / Utilities

| Class | File | Description |
|-------|------|-------------|
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

**Provider Prompt Parity**: All providers MUST use the same prompts. Never create provider-specific prompt variants or
customize prompts based on provider type. If a provider has different execution characteristics (e.g., terminates on
certain output), handle that by restructuring the task execution phases rather than modifying prompts.

**Dev-only Logging**: Diagnostic output (timing, internal state) must only appear when `--dev` is passed. Never add
unconditional `print()` for diagnostic info — user-visible output should be limited to essential status messages.

When fixing bugs, first describe the behavior of the software in detail, and then describe how the code makes that 
happen. From that description generate plausible theories for the bug, then use tests and research to eliminate 
candidates. Define the failure as a binary, assertable condition and aim to build a single reliable reproducer case 
("if I change X, the failure stops"), then run each experiment by changing only X and re-running the reproducer. Once a 
hypothesis predicts both failure and non-failure, minimize it to the smallest causal change and add a regression test 
that fails before the fix and passes after.

For all work, write test(s) which should fail until the issue is fixed or feature is implemented. Make these tests
general enough to cover later work in the area rather than targeting just the current work.

## Windows compatibility

- Treat Windows as a first-class platform. Avoid adding Linux/macOS-only dependencies (pty/fcntl/tty/termios/bash-only scripts) unless guarded and tested for Windows.
- Prefer cross-platform Python/stdlib for process handling; when in doubt, add a Windows-specific regression test in `tests/test_windows_compat.py`.
- If you add or modify tool installers, ensure they resolve Windows `.exe`/`.cmd` binaries and include a Windows test case.
- Don’t ship features that “only work on Unix”; rework them or add a Windows-safe path before merging.

## During changes

When modifying functions that return tuples (e.g., `make_yield`, generator functions) and adding/removing elements:
1. Search for tests that access tuple elements by index (e.g., `result[12]`, `output[N]`)
2. Update all affected indices to match the new tuple structure
3. Common patterns to search: `result[`, `output[`, `[pending_message_idx]`

If you find unused or redundant code or tests, you can remove them. Clean up the code base as you go along.

## After making changes

Start from the premise that the new code will NOT fix the issue or implement the feature, and prove whether it will or 
won't. It is fine to go back and redo changes at this point, but it is NOT acceptable to declare victory and deliver the 
wrong thing. Here are some suggested steps for proving:

1. Run verification using the `verify()` function which handles Python detection automatically:
   ```python
   from chad.util.verification.tools import verify
   result = verify()  # Runs flake8 + all tests
   # Or: verify(lint_only=True)  # Just flake8
   ```
2. **Run startup sanity checks** to catch import/runtime errors not covered by tests:
   ```bash
   # CLI UI
   timeout 5 .venv/bin/python -c "from chad.ui.cli import launch_cli_ui" 2>&1 || echo "CLI startup failed"
   ```
4. **Run provider integration tests** when changing prompts, PTY handling, or task execution:
   ```bash
   # Changes to these files require provider testing:
   # - src/chad/util/prompts.py
   # - src/chad/server/services/task_executor.py
   # - src/chad/server/services/pty_stream.py
   CHAD_RUN_PROVIDER_TESTS=1 .venv/bin/python -m pytest tests/provider_integration/ -v -k codex
   ```
   See the `provider-testing` skill for the full guide. Unit tests mock providers and cannot
   catch CLI-specific behaviors like early exit on certain output formats.
   This catches NameErrors, missing imports, and other issues that flake8 and tests may miss.
4. Perform a critical self-review, note down all the issues you find, and then output them one by one noting whether
each one is a problem that will require rework of your changes. If any do, then go back and rework and then go through
this process again
5. All tests must pass even if you did not break them, never skip tests for any reason.

## Screenshots

For UI changes, take before/after screenshots to verify visual correctness.

### React Web UI Screenshots
Use `chad.util.verification.ui_runner` to launch the API server and capture screenshots with Playwright:
```python
from chad.util.verification.ui_runner import create_temp_env, start_chad, stop_chad, open_playwright_page
env = create_temp_env()
instance = start_chad(env)
with open_playwright_page(instance.port, tab="chat", headless=True) as page:
    page.screenshot(path="/tmp/chad/screenshot.png")
stop_chad(instance)
env.cleanup()
```
Release screenshots: `python scripts/release_screenshots.py`

### CLI Terminal Screenshots
- Use `scripts/screenshot_cli.py` for CLI/terminal screenshots
- Example: `./.venv/bin/python scripts/screenshot_cli.py --command "chad --help" --output /tmp/chad/cli.png`

## Configuration

Config stored in `~/.chad.conf` with encrypted provider tokens.

### UI Modes

- **React web UI**: Run `cd ui && bash dev.sh` for development (Vite + API server).
  The API server also serves the built React UI from `ui/dist/` at `/`.
- **CLI**: `chad` or `chad --ui cli` for terminal interface.

### Connecting to an Existing Server

```bash
chad --server-url http://localhost:8000
```

### Adding New Config Options

**IMPORTANT**: All user-editable config options MUST be exposed in the CLI UI.
Tests in `test_config_manager.py::TestConfigUIParity` will FAIL if you add a new config key
without proper UI support.

When adding a new config option:
1. Add the key to `CONFIG_BASE_KEYS` in `src/chad/util/config_manager.py`
2. Add getter/setter methods to `ConfigManager`
3. Add API endpoint in `src/chad/server/api/routes/config.py`
4. Add `APIClient` method in `src/chad/ui/client/api_client.py`
5. Add React UI element in `ui/src/components/SettingsPanel.tsx`
6. Add menu option in `src/chad/ui/cli/app.py` (in `run_settings_menu`)
7. Update `tests/test_config_manager.py`:
   - Add to `REQUIRED_UI_CONFIG_KEYS` if user-editable
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
| `test_unified_streaming.py` | 53 | PTY/SSE streaming, EventLog | ~25s |
| `test_git_worktree.py` | 43 | Git operations | ~3s |
| `test_config_manager.py` | 38 | Config persistence | ~4s |
| `test_cli_ui.py` | — | CLI UI logic | ~5s |

### Running Tests Efficiently

```bash
# Fast: Run non-visual tests only (~51s)
.venv/bin/python -m pytest tests/ -m "not visual" -q

# Target specific module
.venv/bin/python -m pytest tests/test_providers.py -q

# Target specific class
.venv/bin/python -m pytest tests/test_cli_ui.py::TestCLIImports -q

# Target specific test
.venv/bin/python -m pytest tests/test_providers.py::TestCreateProvider::test_create_anthropic_provider -q

# Run with test durations to find slow tests
.venv/bin/python -m pytest tests/ -m "not visual" --durations=10 -q

# Visual tests only (requires Playwright browser)
.venv/bin/python -m pytest tests/ -m "visual" -q
```

### Pytest Markers

- `api`: API endpoint tests

## Test Utility Tools

Reusable helpers in `tests/test_helpers.py` for bug reproduction and feature verification.
Import them in any test file or use interactively via `pytest` fixtures.

| Tool | Signature | Description |
|------|-----------|-------------|
| `collect_stream_events` | `(client, session_id, timeout=10, poll_interval=0.3, wait_for_completion=True) → CollectedEvents` | Polls `GET /api/v1/sessions/{id}/events` until `session_ended` or timeout. Returns `.all_events`, `.terminal_events`, `.structured_events`, `.decoded_output`. |
| `ProviderOutputSimulator` | `(monkeypatch, scenario)` | Monkeypatches `build_agent_command()` to emit canned byte sequences. Scenarios: `qwen_duplicate`, `codex_system_prompt`, `codex_tool_calls_only`, `codex_garbled_binary`. |
| `TaskPhaseMonitor` | `(events) → .phases, .phase_names(), .terminal_counts_by_phase()` | Scans events for phase transitions (coding → verification → continuation). Detects structured markers and text-based "Phase N:" markers. |
| `capture_provider_command` | `(provider, account_name, project_path, ...) → CapturedCommand` | Calls `build_agent_command()` directly. Returns `.cmd`, `.env`, `.initial_input`. No monkeypatching needed. |
| `cli_config_parity_check` | `() → ConfigParityResult` | Checks which user-editable config keys are missing from `cli/app.py`. Returns `.api_keys`, `.cli_keys`, `.missing_from_cli`. |
| `inspect_stream_output` | `(decoded_output) → StreamInspection` | Scans decoded terminal text for raw JSON patterns and binary garbage. Returns `.has_raw_json`, `.json_fragments`, `.has_binary_data`, `.binary_fragments`. |

### Usage Examples

```python
from test_helpers import collect_stream_events, inspect_stream_output, ProviderOutputSimulator

# Collect events from a running task
events = collect_stream_events(client, session_id, timeout=15)
assert len(events.terminal_events) > 0

# Check for raw JSON leaking into terminal output
inspection = inspect_stream_output(events.decoded_output)
assert not inspection.has_raw_json, f"Raw JSON in output: {inspection.json_fragments}"

# Simulate a provider scenario
sim = ProviderOutputSimulator(monkeypatch, "qwen_duplicate")
# Now start a task via the API — it will run the simulated output
```

## Virtual Environment

The project uses `.venv` (not `venv`). Worktrees automatically symlink to the main project's `.venv` so agents don't
need to reinstall dependencies.

**For running lint/tests**: Always use `verify()` from `chad.util.verification.tools` instead of hardcoded paths
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
