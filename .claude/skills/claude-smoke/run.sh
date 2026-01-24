#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.chad/claude-configs/claude-2}"
CLAUDE_BIN="${CLAUDE_BIN:-claude}"
PROMPT="ping"

echo "Using CLAUDE_CONFIG_DIR=${CONFIG_DIR}"
if [ ! -d "$CONFIG_DIR" ]; then
  echo "FAIL: CLAUDE_CONFIG_DIR does not exist" >&2
  exit 1
fi

TMP_OUT="$(mktemp)"
set +e
CLAUDE_CONFIG_DIR="$CONFIG_DIR" "$CLAUDE_BIN" -p --verbose --output-format stream-json --permission-mode bypassPermissions "$PROMPT" >"$TMP_OUT" 2>&1
status=$?
set -e

if [ $status -ne 0 ]; then
  echo "FAIL: claude exited $status"
  echo "---- output ----"
  sed -n '1,80p' "$TMP_OUT"
  exit $status
fi

echo "PASS: claude responded"
echo "---- output (first 20 lines) ----"
sed -n '1,20p' "$TMP_OUT"
