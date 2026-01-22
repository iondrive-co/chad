---
name: codex-smoke
description: Run a minimal Codex CLI smoke test (opt-in, uses real tokens). Not part of the normal test suite.
metadata:
  short-description: Codex CLI smoke test
---

# Codex Smoke Test (opt-in)

This skill runs a tiny smoke test against the Codex CLI using the currently selected account/config on disk.

## What it does
- Runs `codex --dangerously-bypass-approvals-and-sandbox -C . "ping"` with HOME set to the account’s codex home.
- Exits 0 on success; non-zero on CLI failure. Captures stdout/stderr to a temp file and echoes a short summary.

## Usage
```bash
codex skill codex-smoke/run.sh
```

## Notes / Safety
- Uses real OpenAI/Codex tokens; **do not** add to automated CI or verify().
- Uses `HOME=${CODEX_HOME:-$HOME/.chad/codex-homes/default}` by default.
- Keeps output minimal (`"ping"` prompt).
