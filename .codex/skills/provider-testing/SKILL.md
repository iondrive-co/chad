---
name: provider-testing
description: Run provider integration tests that verify real CLI behaviors. Use when changing prompts, PTY handling, or task execution code.
metadata:
  short-description: Provider integration tests
---

# Provider Integration Testing

Run these tests when modifying files that affect how Chad interacts with provider CLIs.

## When to Use This Skill

Run provider integration tests when changing:

| File | Why |
|------|-----|
| `src/chad/util/prompts.py` | Prompt format changes can break providers |
| `src/chad/server/services/task_executor.py` | PTY/stdin handling affects all providers |
| `src/chad/server/services/pty_stream.py` | PTY lifecycle affects output capture |
| `build_agent_command()` in task_executor | CLI argument changes |

## Running the Tests

```bash
# Run all provider integration tests
CHAD_RUN_PROVIDER_TESTS=1 .venv/bin/python -m pytest tests/provider_integration/ -v

# Run for specific provider
CHAD_RUN_PROVIDER_TESTS=1 .venv/bin/python -m pytest tests/provider_integration/ -v -k codex

# Quick smoke test (single provider)
bash .claude/skills/codex-smoke/run.sh
```

## Why These Tests Exist

Unit tests mock providers, so they can't catch:
- CLI tools interpreting certain output formats as completion signals
- Stdin/stdout handling differences between providers
- Prompt format compatibility issues

**Example regression (Feb 2026):** Codex CLI was exiting immediately after outputting JSON progress updates because it interpreted bare JSON as a completion signal. Multiple fix attempts failed because they treated symptoms instead of the root cause. The fix was to use markdown format for progress updates.

## Critical Behaviors Matrix

| Behavior | Test | What It Catches |
|----------|------|-----------------|
| Multi-step completion | `test_codex_completes_task_with_markdown_progress` | Early exit bugs |
| Progress format | `test_codex_json_progress_causes_early_exit` | Documents broken JSON behavior |
| Output parsing | `test_parse_markdown_progress_from_codex_output` | Parser compatibility |

## Debugging Provider Issues

1. **Reproduce manually** - Run the CLI directly:
   ```bash
   echo "your prompt" | HOME=~/.chad/codex-homes/default codex exec --dangerously-bypass-approvals-and-sandbox -
   ```

2. **Check session logs** - Look at `~/.chad/logs/{session_id}.jsonl`

3. **Compare providers** - If Claude works but Codex doesn't, it's provider-specific

4. **Add a regression test** - Before fixing, add a test that fails

## Provider-Specific Notes

### Codex (OpenAI)
- Uses `exec` mode for non-interactive execution
- Stdin closed after sending prompt
- **Known issue:** Bare JSON output triggers early exit
- **Workaround:** Use markdown for progress (see `docs/ARCHITECTURE.md`)

### Claude Code (Anthropic)
- Uses stream-json format
- Prompt passed as positional argument
- Generally robust with various output formats

### Qwen (Alibaba)
- Uses stream-json format similar to Claude
- Prompt passed via `-p` flag

## Cost Warning

These tests consume real API tokens. Run sparingly:
- Before releases
- After major changes to prompts/streaming/PTY code
- When debugging provider-specific issues
