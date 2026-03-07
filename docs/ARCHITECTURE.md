# Chad Architecture

## Overview
Chad pairs a FastAPI backend with a CLI interface. Coding "agents" are external provider CLIs launched in PTYs; the backend streams their output to the UI over SSE or WebSocket.

## Backend (FastAPI)
- Entry point: `src/chad/server/main.py:create_app` — health lives at `GET /status`; all other endpoints are under `/api/v1`.
- Routers (`src/chad/server/api/routes`): `health`, `sessions`, `providers`, `worktree`, `config`, `ws`, `slack`.
- Services:
  - `task_executor.py` builds provider commands, creates per-task git worktrees, logs events, and drives PTY streaming.
  - `pty_stream.py` manages PTY lifecycle and subscriber fan‑out.
  - `event_mux.py` merges PTY output with EventLog entries into an ordered SSE/WS stream.
  - `session_manager.py` holds in-memory session state; `state.py` exposes singletons (ConfigManager, ModelCatalog, uptime).
  - `session_event_loop.py` per-session orchestration loop for coding → verification → revision with milestone detection.
  - `verification.py` runs automated (flake8/tests) and LLM-based verification of coding agent work.
  - `slack_service.py` posts milestone notifications to Slack and forwards incoming messages to sessions.
- Domain exports: `src/chad/server/domain` re-exports utilities (providers, git_worktree, prompts, event_log, model_catalog, cleanup, process_registry) for UI consumption.

## API Surface
Base path `/api/v1` (except `/status`).

**Status**
- `GET /status` — health, version, uptime_seconds

**Sessions**
- `POST /sessions` — create session (optional `project_path`, `name`)
- `GET /sessions` — list sessions
- `GET /sessions/{id}` — session details
- `DELETE /sessions/{id}` — delete session
- `POST /sessions/{id}/cancel` — request cancel
- `POST /sessions/{id}/tasks` — start task (coding_agent, optional model/reasoning, terminal_rows/cols)
- `GET /sessions/{id}/tasks/{task_id}` — task status
- `GET /sessions/{id}/stream` — SSE stream (query: `since_seq`, `include_terminal`, `include_events`)
- `POST /sessions/{id}/input` — base64 `data` to PTY
- `POST /sessions/{id}/resize` — resize PTY (`rows`, `cols`)
- `GET /sessions/{id}/events` — fetch EventLog (query: `since_seq`, `event_types`)
- `GET /sessions/{id}/conversation` — task-scoped conversation timeline (latest task only; user messages, milestones, assistant messages)

**Worktree**
- `POST /sessions/{id}/worktree` — create worktree
- `GET /sessions/{id}/worktree` — status
- `GET /sessions/{id}/worktree/diff` — summary stats
- `GET /sessions/{id}/worktree/diff/full` — parsed diff
- `POST /sessions/{id}/worktree/merge` — merge to target branch (returns conflicts when present)
- `POST /sessions/{id}/worktree/reset` — reset worktree to base commit
- `DELETE /sessions/{id}/worktree` — delete worktree

**Accounts & Providers**
- `GET /providers` — supported provider types
- `GET /accounts` — list accounts
- `POST /accounts` — create account
- `GET /accounts/{name}` — account detail
- `DELETE /accounts/{name}` — delete
- `PUT /accounts/{name}/model`
- `PUT /accounts/{name}/reasoning`
- `PUT /accounts/{name}/role`
- `GET /accounts/{name}/models`
- `GET /accounts/{name}/usage` — not implemented (501)

**Configuration**
- `GET/PUT /config/verification` — enabled flag
- `GET/PUT /config/cleanup` — `cleanup_days`, `auto_cleanup`
- `GET/PUT /config/preferences` — `last_project_path`, `ui_mode`
- `GET/PUT /config/verification-agent`
- `GET/PUT /config/preferred-verification-model`
- `GET/PUT /config/action-settings` — per-event-type usage actions (thresholds + notify/switch behavior)
- `GET/PUT /config/max-verification-attempts` — max LLM verification rounds
- `GET/PUT /config/slack` — Slack integration (enabled, channel, bot token, signing secret)
- `GET/PUT /config/mock-remaining-usage/{account_name}` — mock provider simulated usage
- `GET/PUT /config/mock-run-duration/{account_name}` — mock provider simulated run time

**Slack**
- `POST /slack/test` — send a test message to verify bot token and channel
- `POST /slack/webhook` — Slack Events API webhook (message forwarding to sessions)

**Streaming**
- SSE: `GET /sessions/{id}/stream`
- WebSocket: `GET /ws/{session_id}` (input, resize, cancel, ping; server sends terminal/event/complete/error)

## Provider Execution
- Accounts are stored encrypted in `~/.chad.conf` via `ConfigManager`.
- Supported providers: Anthropic (Claude Code), OpenAI (Codex), Google (Gemini), Alibaba (Qwen Code), Mistral (Vibe), Mock.
- CLI resolution/installation lives in `chad.util.providers` + `chad.util.installer`; `task_executor.build_agent_command` assembles the command/env and builds the coding prompt (with doc references and verification instructions).
- Claude/Qwen stream‑json is parsed by `ClaudeStreamJsonParser`; PTY output is streamed through EventLog and EventMultiplexer.

## Agent Prompt Formats

**CRITICAL: Progress updates MUST use markdown format, NOT JSON.**

The coding prompt asks agents to emit progress updates during multi-step tasks. These updates use markdown:

```
**Progress:** Found the authentication handler
**Location:** src/auth.py:45
**Next:** Adding input validation
```

The final completion summary uses JSON (to enforce structured output):

```json
{
  "change_summary": "Added input validation to auth handler",
  "files_changed": ["src/auth.py"],
  "completion_status": "success"
}
```

**Why markdown for progress?** The Codex CLI (`codex exec -`) interprets bare JSON objects in assistant output as completion signals and terminates the session immediately. This caused agents to exit after outputting their first progress update, before doing any actual work. Markdown avoids this issue because it's not parsed as a structured completion message.

This was discovered through debugging where Codex would:
1. Do initial exploration
2. Output `{"type": "progress", ...}`
3. Exit immediately (even saying "Will continue now..." which was ignored)
4. Chad would trigger verification on the incomplete output

The fix (using markdown) was validated by running reproduction tests:
- JSON progress: Codex exits immediately, no files created
- Markdown progress: Codex completes full task

**DO NOT reintroduce JSON format for progress updates** without testing against all providers, especially Codex. Future multi-step progress updates should also use markdown.

Related files:
- `chad.util.prompts.CODING_AGENT_PROMPT`: The prompt template
- `chad.util.prompts.extract_progress_update()`: Parser supporting both formats
- `src/chad/server/services/task_executor.py`: Codex runs in `exec` mode with stdin closed after prompt

## Provider Capabilities

Each provider implements `AIProvider` (in `chad.util.providers`) with these capability methods:

| Provider | Multi-turn | Session ID | Usage Reporting |
|----------|------------|------------|-----------------|
| Claude Code (anthropic) | Yes | No | **Yes** (via Anthropic OAuth API) |
| Codex (openai) | Yes | `thread_id` | **Yes** (via session files) |
| Gemini (gemini) | Yes | `session_id` | No (no quota API) |
| Qwen (qwen) | Yes | `session_id` | No (no quota API) |
| Mistral Vibe (mistral) | Yes | No | No (no quota API) |
| Mock | Yes | No | No |

**Usage Reporting** enables automatic provider switching based on quota consumption:
- `supports_usage_reporting()`: Returns `True` if the provider can report usage percentage
- `get_session_usage_percentage()`: Returns 0-100 session usage percentage, or `None` if unavailable
- `get_weekly_usage_percentage()`: Returns 0-100 weekly usage percentage, or `None` if unavailable
- `is_quota_exhausted(output_tail)`: Checks CLI output for quota errors, returns milestone type or `None`

When a provider's usage exceeds the configured threshold (set via `action_settings`), Chad can automatically notify or switch providers based on the per-event-type action rules.

Quota detection is provider-specific via `is_quota_exhausted()`. Providers that can distinguish session vs weekly limits (Claude, Codex) return `"weekly_limit_reached"` when the weekly limit is hit, allowing the UI to show the correct milestone type.

**Adding Usage Support to a Provider:**
1. Implement a way to fetch usage data (API call, session file parsing, etc.)
2. Override `supports_usage_reporting()` to return `True`
3. Override `get_session_usage_percentage()` to return session usage as 0-100
4. Optionally override `get_weekly_usage_percentage()` and `is_quota_exhausted()`
5. Update this table

## Provider Handoff

When switching providers (due to quota exhaustion or user preference), Chad preserves session context for continuity.

**Session Log Strategy:**
- Terminal output is logged only at session end (not periodically during execution)
- This produces one `terminal_output` event with the final screen state, avoiding log bloat from repeated screen captures
- The final state is most relevant for handoff since it shows what the agent was working on when interrupted

**Handoff Flow:**
1. Quota exhaustion detected via usage threshold (proactive) or error patterns (reactive)
2. `log_handoff_checkpoint()` writes a `ContextCondensedEvent` with `policy="provider_handoff"` containing:
   - Original task description
   - Files changed/created (extracted from `tool_call_started` events)
   - Key commands run (pytest, npm, etc.)
   - Optional remaining work description
   - Provider session ID for native resume (if supported)
3. Old provider stopped, new provider started
4. `build_resume_prompt()` reconstructs context from the checkpoint for the new provider

**Key Files:**
- `chad.util.handoff`: `log_handoff_checkpoint()`, `build_handoff_summary()`, `build_resume_prompt()`, `is_quota_exhaustion_error()`
- `chad.util.event_log`: `ContextCondensedEvent`, `TerminalOutputEvent`
- `chad.server.services.task_executor`: Terminal buffer flushing in `finally` block ensures capture on all exit paths

**Error Pattern Detection:**
`is_quota_exhaustion_error()` matches patterns like `insufficient_quota`, `rate_limit_exceeded`, `billing_hard_limit_reached`, `RESOURCE_EXHAUSTED`, etc. across providers.

## Project Configuration
- Per-project settings (lint/test commands, doc paths) are stored in the main `~/.chad.conf` under the `projects` key, keyed by absolute project path. Managed by `ConfigManager.{get,set}_project_config()`.
- `build_doc_reference_text` points agents to AGENTS.md and ARCHITECTURE.md on disk instead of inlining contents.

## Logging & Artifacts
- JSONL logs at `~/.chad/logs/{session}.jsonl`; large outputs under `~/.chad/logs/artifacts/{session}/`. Override with `CHAD_LOG_DIR`.
- `chad.util.event_log.EventLog` manages sequences, artifacts, and typed events.

## UI Architecture Principles

**CRITICAL: These principles are fundamental to Chad's architecture and must be followed:**

1. **View-Only UI Code**: All UI implementations (CLI) contain ONLY view logic. They must not contain business logic, validation, or state management beyond display state.

2. **Business Logic in Server**: ALL business logic, validation, calculations, and persistent state management MUST live in the server (`src/chad/server`). The server is the single source of truth for all operations.

3. **No UI-Specific Server Code**: The server must not contain UI-specific code paths. The server provides a uniform API consumed by all UIs equally.

## UI Flows (current)
- **React Chat tab:** A conversation view (latest task) sits above the live terminal stream. The text area starts the first task; once a task finishes it sends follow-ups as new tasks in the same session. Input is disabled while a task is running. Milestones and assistant messages surface inside the thread; raw PTY output still streams below.
- **CLI UI:** Shows the live terminal stream; after completion it prints the conversation timeline for the task. User input is disabled during runs for now.

4. **Complete Session Logging**: All agent output displayed to users MUST also be recorded in the session log (EventLog). This ensures:
   - Provider handoffs have access to complete context
   - Session history is preserved for debugging
   - All UIs can reconstruct the same session state

Examples of proper separation:
- UI calls API endpoint, server validates and processes
- Server logs all events, UI displays from event stream
- UI manages only display state (collapsed/expanded, theme)

## UI Layers
- CLI UI (`src/chad/ui/cli/app.py`) streams the SSE feed via `SyncStreamClient`.
- Shared clients: `src/chad/ui/client/api_client.py` (REST) and `stream_client.py` (SSE).
- Terminal rendering: `src/chad/ui/terminal_emulator.py` used by CLI.

## Worktrees & Git
- `chad.util.git_worktree.GitWorktreeManager` creates per-session branches, detects changes, parses diffs, merges, resets, and deletes worktrees. TaskExecutor creates a worktree for each task before launching the provider.

## Model Catalog & Cleanup
- `chad.util.model_catalog.ModelCatalog` resolves available models per provider/account and is exposed via server state for the UIs.
- Cleanup helpers in `chad.util.cleanup` prune old worktrees/logs/screenshots/temp files; `chad.util.process_registry` tracks spawned processes with PID files.

## File Structure (high level)
```
src/chad/
├── server/
│   ├── main.py
│   ├── state.py
│   ├── api/routes/ {health.py, sessions.py, providers.py, worktree.py, config.py, ws.py, slack.py}
│   └── services/ {task_executor.py, pty_stream.py, event_mux.py, session_manager.py,
│                   session_event_loop.py, verification.py, slack_service.py}
├── ui/
│   ├── cli/app.py
│   ├── client/ {api_client.py, stream_client.py}
│   └── terminal_emulator.py
└── util/ {providers.py, git_worktree.py, event_log.py, project_setup.py, model_catalog.py,
           cleanup.py, process_registry.py, installer.py, prompts.py, config_manager.py}
```

## TypeScript Client & Browser UI
- `client/` — Zero-dependency TypeScript library (`chad-client`) wrapping Chad's REST, SSE, and WebSocket APIs. Uses native `fetch`, `EventSource`, and `WebSocket`. Built with Vite as an ES module library.
- `ui/` — Vite + React browser UI that imports `chad-client`. Dev server proxies `/api` and `/ws` to Chad's backend. Components: SessionList, ChatView, TaskForm, AccountPicker, SettingsPanel, ProvidersPanel, ActionRules, DiffViewer, MergePanel, ConflictViewer.
- Both packages are independent of the Python codebase and communicate with Chad exclusively through its HTTP API.

## Session Event Logs
Session logs are JSONL in `~/.chad/logs/{session_id}.jsonl`; artifacts for large outputs live in `~/.chad/logs/artifacts/{session_id}/`. Each event includes `event_id`, `ts`, `seq`, `session_id`, optional `turn_id`, and a type-specific payload. `CHAD_LOG_DIR` overrides the base directory.

## Tool Types in Event Logs
`tool` values in tool_call events include: `bash`, `read`, `write`, `edit`, `mcp`, `glob`, `grep`, plus any provider-specific tool names.
