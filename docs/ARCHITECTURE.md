# Chad Architecture

## Overview
Chad is a two-layer system: a FastAPI backend that orchestrates coding tasks and a set of UIs (Gradio web and CLI) that drive the backend through the same streaming API. Agents are external provider CLIs (Claude, Codex, Gemini, Qwen, etc.) launched as subprocesses.

## Backend (FastAPI)
- Entry point: `src/chad/server/main.py`
- Routes: `src/chad/server/api/routes` expose REST + SSE + WebSocket endpoints under `/api/v1`.
- Services: `src/chad/server/services/task_executor.py` manages PTY-based agent processes, event logging, and worktree handling.
- Domain models: `src/chad/server/api/schemas` define request/response payloads; `src/chad/server/domain` re-exports helpers for the UIs.
- Worktrees: `GitWorktreeManager` creates per-session branches for safe changes; merge helpers live in `src/chad/util/git_worktree.py`.

## UI Layers
- Gradio UI: `src/chad/ui/gradio/web_ui.py` builds the Run/Setup tabs and streams PTY output; visual tests use Playwright via `tests/test_ui_integration.py`.
- CLI UI: `src/chad/ui/cli/app.py` is a thin TUI that streams from the backend.
- Shared verification utilities for the UI live in `src/chad/ui/gradio/verification/`.

## Provider Execution
- Providers are configured accounts stored in `~/.chad.conf` (encrypted). Each account maps to a CLI binary resolved by `src/chad/util/providers.py`.
- `task_executor.build_agent_command` constructs the subprocess command and environment, then streams output over PTY/SSE back to the UI.

## Project Configuration
- Per-project settings live in `.chad/project.json` (created via `setup_project` in `src/chad/util/project_setup.py`). It records lint/test commands and doc paths so agents know where to read instructions from disk.

## Logging & Artifacts
- Structured JSONL event logs are written to `~/.chad/logs/{session}.jsonl`; large outputs go to `~/.chad/logs/artifacts/{session}/`.
- `src/chad/util/event_log.py` provides reading/writing helpers and is used by both backend and UI.

## Testing & Verification
- `verify()` wrapper (see `src/chad/ui/gradio/verification/tools.py`) runs flake8 + pytest; visual tests are Playwright-based.
- Startup sanity checks import `chad.ui.gradio.launch_web_ui` and `chad.ui.cli.launch_cli_ui` to catch import-time errors quickly.


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
| GET | `/config/verification-agent` | Get verification agent account |
| PUT | `/config/verification-agent` | Set verification agent account |
| GET | `/config/preferred-verification-model` | Get preferred verification model |
| PUT | `/config/preferred-verification-model` | Set preferred verification model |

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
| `terminal_output` | Terminal screen content | `data` (human-readable text) |
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

