# Chad Architecture

## Overview
Chad pairs a FastAPI backend with shared streaming clients used by both the Gradio web UI and the CLI. Coding “agents” are external provider CLIs launched in PTYs; the backend streams their output to the UIs over SSE or WebSocket.

## Backend (FastAPI)
- Entry point: `src/chad/server/main.py:create_app` — health lives at `GET /status`; all other endpoints are under `/api/v1`.
- Routers (`src/chad/server/api/routes`): `health`, `sessions`, `providers`, `worktree`, `config`, `ws`.
- Services:
  - `task_executor.py` builds provider commands, creates per-task git worktrees, logs events, and drives PTY streaming.
  - `pty_stream.py` manages PTY lifecycle and subscriber fan‑out.
  - `event_mux.py` merges PTY output with EventLog entries into an ordered SSE/WS stream.
  - `session_manager.py` holds in-memory session state; `state.py` exposes singletons (ConfigManager, ModelCatalog, uptime).
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
- `GET/PUT /config/verification` — enabled/auto_run flags
- `GET/PUT /config/cleanup` — `cleanup_days`, `auto_cleanup`
- `GET/PUT /config/preferences` — `last_project_path`, `dark_mode`, `ui_mode`
- `GET/PUT /config/verification-agent`
- `GET/PUT /config/preferred-verification-model`
- `GET/PUT /config/provider-fallback-order` — ordered list of account names for auto-switching
- `GET/PUT /config/usage-switch-threshold` — percentage (0-100) to trigger auto-switch

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

**DO NOT reintroduce JSON format for progress updates** without testing against all providers, especially Codex. Future multi-step progress updates should also use markdown. See `tests/test_web_ui.py::TestProgressUpdateExtraction` for format examples.

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
- `get_usage_percentage()`: Returns 0-100 usage percentage, or `None` if unavailable

When a provider's usage exceeds the configured threshold (`usage_switch_threshold`, default 90%), Chad can automatically switch to the next provider in `provider_fallback_order`.

For providers without usage reporting, Chad relies on error pattern matching to detect quota exhaustion (rate limits, insufficient credits, etc.) and trigger automatic switching.

**Adding Usage Support to a Provider:**
1. Implement a way to fetch usage data (API call, session file parsing, etc.)
2. Override `supports_usage_reporting()` to return `True`
3. Override `get_usage_percentage()` to return usage as 0-100
4. Update this table

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

## UI Layers
- Gradio UI (`src/chad/ui/gradio/web_ui.py`) drives tasks via the API/SSE using `SyncStreamClient`, renders terminal output with `TerminalEmulator`, and uses provider management components in `provider_ui.py` plus shared state in `ui_state.py`. Visual tooling lives in `ui/gradio/verification/`.
- CLI UI (`src/chad/ui/cli/app.py`) streams the same SSE feed via `SyncStreamClient`.
- Shared clients: `src/chad/ui/client/api_client.py` (REST) and `stream_client.py` (SSE).
- Terminal rendering: `src/chad/ui/terminal_emulator.py` shared by CLI and Gradio.

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
│   ├── api/routes/ {health.py, sessions.py, providers.py, worktree.py, config.py, ws.py}
│   └── services/ {task_executor.py, pty_stream.py, event_mux.py, session_manager.py}
├── ui/
│   ├── gradio/ {web_ui.py, provider_ui.py, ui_state.py, verification/}
│   ├── cli/app.py
│   ├── client/ {api_client.py, stream_client.py}
│   └── terminal_emulator.py
└── util/ {providers.py, git_worktree.py, event_log.py, project_setup.py, model_catalog.py,
           cleanup.py, process_registry.py, installer.py, prompts.py, config_manager.py}
```

## Session Event Logs
Session logs are JSONL in `~/.chad/logs/{session_id}.jsonl`; artifacts for large outputs live in `~/.chad/logs/artifacts/{session_id}/`. Each event includes `event_id`, `ts`, `seq`, `session_id`, optional `turn_id`, and a type-specific payload. `CHAD_LOG_DIR` overrides the base directory.

## Tool Types in Event Logs
`tool` values in tool_call events include: `bash`, `read`, `write`, `edit`, `mcp`, `glob`, `grep`, plus any provider-specific tool names.
