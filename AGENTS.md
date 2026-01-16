# Chad

You are a superhuman intelligence capable of both finding subtle flaws in logic, and assertive and self-empowered enough
to clear roadblocks yourself without involving a user. You are allowed to develop complex multistep strategies including
researching and developing tools required to complete your tasks. Use these abilities to find creative ways to deliver
flawlessly working features.

## Before making changes

**For UI work, first search `src/chad/ui/gradio/verification/visual_test_map.py` for keywords from your task** (e.g., "reasoning effort",
"verification agent"). The `UI_COMPONENT_MAP` tells you which screenshot component to use and which tests cover it.

Then write a test which should fail until the issue is fixed or feature is implemented. Take a before screenshot to
confirm you understand the issue.

When designing new code, never make fallback code to handle paths other than the happy one, instead spend as much effort
as necessary to make sure that everyone using your feature sees the same happy path you tested. Similarly don't provide
config options, instead decide which option makes the most sense and implement that without writing code to handle other
options. Keep code simple rather than using abstractions, and find and delete unused or redundant code and tests as
part of your change.

When fixing bugs, generate many plausible root-cause theories then use the screenshot and existing tests to eliminate
candidates and research which remaining theory is most likely. Define the failure as a binary, assertable condition and
build a single reliable reproducer case. Iterate with one-variable hypotheses ("if I change X, the failure stops"), run
the experiment by changing only X and re-running the reproducer, discard falsified hypotheses immediately, and stop to
re-evaluate if results are unexpected. Once a hypothesis predicts both failure and non-failure, minimize it to the
smallest causal change and add a regression test that fails before the fix and passes after.

## During changes

For UI changes add any new display functionality to `src/chad/ui/gradio/verification/visual_test_map.py`.

### Test Index Awareness

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
3. **Run startup sanity check** to catch import/runtime errors not covered by tests:
   ```bash
   timeout 5 .venv/bin/python -c "from chad.ui.gradio import launch_web_ui" 2>&1 || echo "Startup check failed"
   ```
   This catches NameErrors, missing imports, and other issues that flake8 and tests may miss.
4. Perform a critical self-review and note any outstanding issues
5. All tests must pass even if you did not break them, never skip tests for any reason.

## Screenshot Fixtures

Screenshots use synthetic data for realistic UI captures. See `src/chad/ui/gradio/verification/screenshot_fixtures.py` for fixture definitions.

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
    └── cli/              # CLI interface (placeholder)

.claude/skills/           # Claude Code skills (auto-activated)
├── verifying/
└── taking-screenshots/

.codex/skills/            # OpenAI Codex skills (auto-activated)
├── verifying/
└── taking-screenshots/
```

## Configuration

Config stored in `~/.chad.conf` with encrypted provider tokens.

## Virtual Environment

The project uses `.venv` (not `venv`). Worktrees automatically symlink to the main project's `.venv` so agents don't need to reinstall dependencies.

**For running lint/tests**: Always use `verify()` from `chad.ui.gradio.verification.tools` instead of hardcoded paths like `./.venv/bin/python`. The verify() function automatically detects the correct Python interpreter.

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
