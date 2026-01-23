---
name: claude-smoke
description: Run a minimal Claude CLI smoke test (opt-in, uses real tokens). Not part of the normal test suite.
metadata:
  short-description: Claude CLI smoke test
---

# Claude Smoke Test (opt-in)

This skill runs a tiny, paid-call smoke test against the Claude CLI using the currently selected account/config on disk.

## What it does
- Runs `claude -p --verbose --output-format stream-json --permission-mode bypassPermissions "ping"` with `CLAUDE_CONFIG_DIR` set.
- Exits 0 on success; non-zero on CLI failure. Captures stdout/stderr to a temp file and echoes a short summary.

## Usage
```bash
bash .claude/skills/claude-smoke/run.sh
```

## Notes / Safety
- Uses real Anthropic tokens; **do not** add to automated CI or verify().
- Respects your local `CLAUDE_CONFIG_DIR`; defaults to `~/.chad/claude-configs/claude-2` if unset.
- Keeps output small (`"ping"` prompt).
