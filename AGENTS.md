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
part of your change.

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

### WebSocket

| Endpoint | Description |
|----------|-------------|
| `/ws/{session_id}` | Real-time task updates |

**WebSocket Message Types (server → client):**
- `stream`: Raw AI output chunk
- `activity`: Tool use or thinking activity
- `status`: Status message
- `message_start`: AI message started
- `message_complete`: AI message completed
- `progress`: Progress update
- `complete`: Task completed
- `error`: Error occurred

**WebSocket Message Types (client → server):**
- `ping`: Heartbeat (receives `pong`)
- `cancel`: Cancel current task

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
│   └── prompts.py        # Coding and verification prompts
├── server/               # FastAPI backend
│   ├── main.py           # Server entry point
│   ├── api/routes/       # REST + WebSocket endpoints
│   └── services/         # Business logic services
└── ui/
    ├── gradio/           # Gradio web interface
    │   ├── web_ui.py     # Main UI implementation
    │   └── verification/ # Visual testing tools
    ├── client/           # API + WebSocket clients
    └── cli/              # Simple CLI interface
        ├── app.py        # Menu-driven CLI
        └── pty_runner.py # PTY passthrough for agent CLIs

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

## Session Logs

Logs saved to `/tmp/chad/chad_session_YYYYMMDD_HHMMSS.json`.
