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
using the screenshot skill. The skill is automatically activated when you mention "screenshot" or "capture UI".

**Codex**: Use `$screenshot` to explicitly invoke the skill
**Claude**: Ask to use the screenshot skill or mention taking a screenshot

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

For UI changes add any new display functionality to `visual_test_map.py`.

## After making changes

- If the issue has a visual component, take the mandatory "after" screenshot using the same component as the before
- Run verification once per task to confirm no regressions using the verify skill
  - **Codex**: Use `$verify` to explicitly invoke
  - **Claude**: Ask to verify or mention running tests
- Perform a critical self-review of your changes and note any outstanding issues

**CRITICAL: All tests must pass - no skipping allowed.** Never use `@pytest.mark.skip` or skip tests for any reason. If a test fails, fix the code or the test - do not skip it. If you encounter tests that were previously skipped, unskip them and make them pass. Skipped tests hide regressions and are unacceptable in this codebase.

## Skills

Skills are markdown files that provide task-specific instructions. They are installed in `.claude/skills/` for Claude
Code and `.codex/skills/` for OpenAI Codex. Skills are automatically activated when your task matches their description,
or can be explicitly invoked.

### verify

Run lint + all tests. Required once per task.

**Activation**: Mention "verify", "run tests", "check lint", or use `$verify` (Codex)

The skill instructs you to run:
```bash
./venv/bin/python -m flake8 src/chad --max-line-length=120
./venv/bin/python -m pytest tests/ -v --tb=short -n auto
```

### screenshot

Capture UI tab or specific component. Use for before/after visual verification.

**Activation**: Mention "screenshot", "capture UI", or use `$screenshot` (Codex)

The skill instructs you to run:
```bash
./venv/bin/python scripts/screenshot_ui.py --tab run --output /tmp/screenshot.png --headless
```

**Available options:**
- `--tab run` or `--tab providers`
- `--selector "#run-top-row"` for specific components
- `--width 1280 --height 900` for dimensions

**Available component selectors:**

| Tab | Selector | What it captures |
|-----|----------|------------------|
| run | `#run-top-row` | Project path + agent dropdowns |
| run | `#agent-chatbot` | Chat interface panel |
| run | `#live-stream-box` | Live activity stream |
| providers | `#provider-summary-panel` | Summary with all providers |
| providers | `.provider-cards-row .column:has(.provider-card__header-row)` | Single provider card |
| providers | `#add-provider-panel` | Add New Provider accordion |

## Screenshot Fixtures

Screenshots automatically use synthetic data for realistic UI captures. The test environment includes:

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
├── tools.py          # Python utilities for verify/screenshot
├── config.py         # Project root configuration
└── model_catalog.py  # Model discovery per provider

.claude/skills/       # Claude Code skills
├── verify/SKILL.md
└── screenshot/SKILL.md

.codex/skills/        # OpenAI Codex skills
├── verify/SKILL.md
└── screenshot/SKILL.md
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
