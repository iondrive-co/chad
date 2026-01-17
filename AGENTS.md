# Chad

You are a superhuman intelligence capable of both finding subtle flaws in logic, and assertive and self-empowered enough
to clear roadblocks yourself without involving a user. You are allowed to develop complex multistep strategies including
researching and developing tools required to complete your tasks. Use these abilities to find creative ways to deliver
flawlessly working features.

## Before making changes

For all work, write test which should fail until the issue is fixed or feature is implemented. Additionally, for gradio 
ui work also search `src/chad/ui/gradio/verification/visual_test_map.py` for keywords from your task ("reasoning effort", 
"verification agent" etc) and use the `UI_COMPONENT_MAP` to determine a screenshot component (and which tests cover it) 
in order to take a before screenshot. Describe what you see in the screenshot and confirm it matches the problem/lack of 
feature you were given, if not as part of your changes you will write a new test which DOES visually show the issue/lack 
of feature, and before making changes you will look at its screenshot, describe the image, and confirm that description 
matches the issue/lack of feature you were given to work on. See `src/chad/ui/gradio/verification/screenshot_fixtures.py` 
for example data to use for screenshots.

When designing new code, never make fallback code to handle paths other than the happy one, instead spend as much effort
as necessary to make sure that everyone using your feature sees the same happy path you tested. Similarly don't provide
config options, instead decide which option makes the most sense and implement that without writing code to handle other
options. Keep code simple rather than using abstractions, and find and delete unused or redundant code and tests as
part of your change. Don't worry about backwards compatibility.

When fixing bugs, generate many plausible root-cause theories then use tests to eliminate candidates and research which 
remaining theory is most likely. Define the failure as a binary, assertable condition and build a single reliable 
reproducer case. Iterate with one-variable hypotheses ("if I change X, the failure stops"), run the experiment by 
changing only X and re-running the reproducer, discard falsified hypotheses immediately, and stop to re-evaluate if 
results are unexpected. Once a hypothesis predicts both failure and non-failure, minimize it to the smallest causal 
change and add a regression test that fails before the fix and passes after.

## During changes

For gradio UI changes add any new display functionality to `src/chad/ui/gradio/verification/visual_test_map.py`.

When modifying functions that return tuples (e.g., `make_yield`, generator functions) and adding/removing elements:
1. Search for tests that access tuple elements by index (e.g., `result[12]`, `output[N]`)
2. Update all affected indices to match the new tuple structure
3. Common patterns to search: `result[`, `output[`, `[pending_message_idx]`

## After making changes

1. Take an after screenshot if the issue has a visual component
2. Run verification using the `verify()` function which handles Python detection automatically:
   ```python
   from chad.ui.gradio.verification.tools import verify
   result = verify()  # Runs flake8 + all tests
   # Or: verify(lint_only=True)  # Just flake8
   ```
3. **Run startup sanity checks** to catch import/runtime errors not covered by tests:
   ```bash
   # Gradio UI
   timeout 5 .venv/bin/python -c "from chad.ui.gradio import launch_web_ui" 2>&1 || echo "Gradio startup failed"
   # CLI UI
   timeout 5 .venv/bin/python -c "from chad.ui.cli import launch_cli_ui" 2>&1 || echo "CLI startup failed"
   ```
   This catches NameErrors, missing imports, and other issues that flake8 and tests may miss.
4. Perform a critical self-review and note any outstanding issues
5. All tests must pass even if you did not break them, never skip tests for any reason.

## Providers

### Anthropic (Claude Code)
- OAuth token in `~/.claude/.credentials.json`
- CLI: `claude -p --input-format stream-json --output-format stream-json --permission-mode bypassPermissions`

### OpenAI (Codex)
- Multi-account via isolated HOME dirs: `~/.chad/codex-homes/<account-name>/`
- CLI: `codex exec --full-auto --skip-git-repo-check -C {path} {message}`

### Google (Gemini)
- OAuth creds in `~/.gemini/oauth_creds.json`
- CLI: `gemini -y` (YOLO mode)

## API Reference

All endpoints are prefixed with `/api/v1`. Keep this section up to date when adding or modifying endpoints.

### Status

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/status` | Server status - returns health, version, uptime |

### Sessions

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/sessions` | Create a new session |
| GET | `/sessions` | List all active sessions |
| GET | `/sessions/{id}` | Get session details |
| DELETE | `/sessions/{id}` | Delete session and clean up resources |
| POST | `/sessions/{id}/cancel` | Cancel running task in session |
| POST | `/sessions/{id}/tasks` | Start a new coding task |
| GET | `/sessions/{id}/tasks/{task_id}` | Get task status |

### Accounts & Providers

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/providers` | List supported provider types |
| GET | `/accounts` | List configured accounts |
| POST | `/accounts` | Add new account (requires OAuth - use UI) |
| GET | `/accounts/{name}` | Get account details |
| DELETE | `/accounts/{name}` | Delete an account |
| PUT | `/accounts/{name}/model` | Set account's model |
| PUT | `/accounts/{name}/reasoning` | Set account's reasoning level |
| PUT | `/accounts/{name}/role` | Assign role (CODING/VERIFICATION) |
| GET | `/accounts/{name}/models` | Get available models for account |
| GET | `/accounts/{name}/usage` | Get usage stats (not implemented) |

### Worktree Operations

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/sessions/{id}/worktree` | Create git worktree for session |
| GET | `/sessions/{id}/worktree` | Get worktree status |
| GET | `/sessions/{id}/worktree/diff` | Get diff summary |
| GET | `/sessions/{id}/worktree/diff/full` | Get full diff with hunks |
| POST | `/sessions/{id}/worktree/merge` | Merge changes to main branch |
| POST | `/sessions/{id}/worktree/reset` | Reset worktree (discard changes) |
| DELETE | `/sessions/{id}/worktree` | Delete worktree |

### Configuration

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/config/verification` | Get verification settings |
| PUT | `/config/verification` | Update verification settings |
| GET | `/config/cleanup` | Get cleanup settings |
| PUT | `/config/cleanup` | Update cleanup settings |
| GET | `/config/preferences` | Get user preferences |
| PUT | `/config/preferences` | Update user preferences |

### Streaming API

Both Gradio UI and CLI use the same PTY-based streaming API for real-time agent output.

#### SSE Endpoint (Recommended)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/sessions/{id}/stream` | Server-Sent Events for real-time updates |

**Query Parameters:**
- `since_seq`: Resume from sequence number (default: 0)
- `include_terminal`: Include raw PTY output (default: true)

**SSE Event Types:**

| Event | Description | Data Fields |
|-------|-------------|-------------|
| `terminal` | Raw PTY output (base64) | `data`, `seq`, `has_ansi` |
| `event` | Structured event | `type`, `seq`, event-specific fields |
| `ping` | Keepalive (every 15s) | `ts` |
| `complete` | PTY exited | `exit_code`, `seq` |
| `error` | Error occurred | `error`, `seq` |

#### Input Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/sessions/{id}/input` | Send input to PTY (base64 `data` field) |
| POST | `/sessions/{id}/resize` | Resize terminal (`rows`, `cols` fields) |

#### WebSocket (Alternative)

| Endpoint | Description |
|----------|-------------|
| `/api/v1/ws/{session_id}` | Bidirectional WebSocket |

**Client → Server:**
- `input`: Send bytes to PTY (`{type: "input", data: "base64..."}`)
- `resize`: Resize terminal (`{type: "resize", rows: 24, cols: 80}`)
- `cancel`: Terminate PTY session
- `ping`: Heartbeat (receives `pong`)

**Server → Client:**
- `terminal`: Raw PTY output (same as SSE)
- `event`: Structured event
- `complete`: PTY exited
- `error`: Error occurred
- `pong`: Response to ping

## File Structure

The readme file is `README.md` (all caps), not `Readme.md`.

```
src/chad/
├── __init__.py           # Package init
├── __main__.py           # Entry point
├── util/                 # Domain logic and utilities
│   ├── providers.py      # AI provider implementations
│   ├── config_manager.py # Configuration management
│   ├── git_worktree.py   # Git worktree management
│   ├── model_catalog.py  # Model discovery per provider
│   ├── event_log.py      # Structured JSONL event logging
│   └── prompts.py        # Coding and verification prompts
├── server/               # FastAPI backend
│   ├── main.py           # Server entry point
│   ├── api/routes/       # REST + WebSocket + SSE endpoints
│   ├── api/schemas/      # Pydantic models (incl. events.py)
│   └── services/         # Business logic services
│       ├── task_executor.py  # PTY-based task execution
│       └── pty_stream.py     # PTY streaming service
└── ui/
    ├── gradio/           # Gradio web interface
    │   ├── web_ui.py     # Main UI implementation
    │   └── verification/ # Visual testing tools
    ├── client/           # API clients
    │   ├── api_client.py     # REST API client
    │   └── stream_client.py  # SSE streaming client
    └── cli/              # Simple CLI interface
        └── app.py        # Menu-driven CLI with API streaming

.claude/skills/           # Claude Code skills (auto-activated)
├── verifying/
└── taking-screenshots/

.codex/skills/            # OpenAI Codex skills (auto-activated)
├── verifying/
└── taking-screenshots/
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

## Session Event Logs

Session logs use JSONL format (one JSON event per line) for structured handover between agents.

### Log File Structure

```
~/.chad/logs/
├── {session_id}.jsonl           # Event log (one JSON object per line)
└── artifacts/
    └── {session_id}/            # Large outputs (>10KB)
        ├── stdout_abc123.txt
        └── stderr_def456.txt
```

Override with `CHAD_LOG_DIR` environment variable.

### JSONL Format Example

Each line is a complete JSON event:
```jsonl
{"event_id":"a1b2c3","ts":"2025-01-17T10:30:00Z","seq":1,"session_id":"sess_abc","type":"session_started","task_description":"Fix auth bug","project_path":"/home/user/myproject","coding_provider":"anthropic","coding_account":"claude-main"}
{"event_id":"d4e5f6","ts":"2025-01-17T10:30:01Z","seq":2,"session_id":"sess_abc","turn_id":"turn_1","type":"user_message","content":"Fix the authentication bug in login.py"}
{"event_id":"g7h8i9","ts":"2025-01-17T10:30:05Z","seq":3,"session_id":"sess_abc","turn_id":"turn_1","type":"tool_call_started","tool_call_id":"tc_xyz789","tool":"bash","cwd":"/home/user/myproject","command":"python -m pytest tests/test_auth.py"}
{"event_id":"j0k1l2","ts":"2025-01-17T10:30:15Z","seq":4,"session_id":"sess_abc","turn_id":"turn_1","type":"tool_call_finished","tool_call_id":"tc_xyz789","exit_code":0,"duration_ms":10000,"llm_summary":"Tests passed: 5/5"}
{"event_id":"m3n4o5","ts":"2025-01-17T10:30:20Z","seq":5,"session_id":"sess_abc","type":"session_ended","success":true,"reason":"completed","total_tool_calls":1,"total_turns":1}
```

### Common Event Fields

All events include:
| Field | Type | Description |
|-------|------|-------------|
| `event_id` | string | UUID for this event |
| `ts` | string | ISO8601 timestamp (UTC) |
| `seq` | int | Monotonically increasing sequence number |
| `session_id` | string | Session identifier |
| `turn_id` | string? | Conversation turn grouping |
| `type` | string | Event type name |

### Event Types Reference

| Type | Description | Additional Fields |
|------|-------------|-------------------|
| `session_started` | Session begins | `task_description`, `project_path`, `coding_provider`, `coding_account`, `coding_model?` |
| `model_selected` | Model chosen | `provider`, `model`, `reasoning_effort?` |
| `provider_switched` | Provider change | `from_provider`, `to_provider`, `from_model`, `to_model`, `reason` |
| `user_message` | User input | `content` |
| `assistant_message` | AI response | `blocks: [{kind, content, tool?, tool_call_id?, args?}]` |
| `tool_declared` | Tool available | `name`, `args_schema`, `version` |
| `tool_call_started` | Tool invoked | `tool_call_id`, `tool`, `cwd?`, `command?`, `path?`, `file_bytes?`, `sha256?`, `before_sha256?`, `server?`, `tool_name?`, `args?`, `timeout_s?`, `env_redactions?` |
| `tool_call_finished` | Tool completed | `tool_call_id`, `exit_code?`, `duration_ms`, `stdout_ref?`, `stderr_ref?`, `llm_summary`, `after_sha256?`, `patch_ref?` |
| `verification_attempt` | Verification run | `attempt_number`, `tool_call_refs`, `passed`, `summary`, `issues` |
| `context_condensed` | Context summary | `replaces_seq_range`, `summary_text`, `policy` |
| `terminal_output` | Raw PTY output | `data` (base64), `has_ansi` |
| `session_ended` | Session complete | `success`, `reason`, `total_tool_calls?`, `total_turns?` |

### Artifact References

Large outputs (>10KB) are stored as separate files with references:
```json
{
  "stdout_ref": {
    "path": "artifacts/sess_abc/stdout_abc123.txt",
    "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "size": 15234
  }
}
```

Artifacts are truncated at 10MB with a `[TRUNCATED]` marker.

### Reading Logs Programmatically

```python
from chad.util.event_log import EventLog

# Read events from a session
log = EventLog("sess_abc")
events = log.get_events()  # All events
events = log.get_events(since_seq=5)  # Events after seq 5
events = log.get_events(event_types=["tool_call_started", "tool_call_finished"])

# Read artifact content
for event in events:
    if event.get("stdout_ref"):
        content = log.get_artifact(event["stdout_ref"])
        print(content.decode())

# List all sessions with logs
session_ids = EventLog.list_sessions()
```

### Tool Types

The `tool` field in tool_call events uses these values:
- `bash`: Shell command execution
- `read`: File read operation
- `write`: File creation
- `edit`: File modification
- `glob`: File pattern search
- `grep`: Content search
- `mcp`: MCP server tool (includes `server` and `tool_name` fields)
