---
name: qwen-smoke
description: Run a minimal Qwen CLI smoke test (opt-in, uses real tokens). Not part of the normal test suite.
metadata:
  short-description: Qwen CLI smoke test
---

# Qwen Smoke Test (opt-in)

This skill runs a tiny smoke test against the Qwen Code CLI to verify it's installed and authenticated.

## What it does
- Runs `qwen --output-format stream-json --yolo "ping"` to test basic functionality.
- Exits 0 on success; non-zero on CLI failure. Captures stdout/stderr to a temp file and echoes a short summary.

## Usage
```bash
bash .codex/skills/qwen-smoke/run.sh
```

## Notes / Safety
- Uses real Qwen/OpenAI-compatible API tokens; **do not** add to automated CI or verify().
- Qwen Code authenticates via QwenChat OAuth (2000 free daily requests) or OpenAI-compatible API key.
- Keeps output small (`"ping"` prompt).
