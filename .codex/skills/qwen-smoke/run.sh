#!/usr/bin/env bash
set -euo pipefail

# Try to find qwen in common locations
QWEN_BIN="${QWEN_BIN:-}"
if [ -z "$QWEN_BIN" ]; then
  if command -v qwen &>/dev/null; then
    QWEN_BIN="qwen"
  elif [ -x "$HOME/.chad/tools/node_modules/.bin/qwen" ]; then
    QWEN_BIN="$HOME/.chad/tools/node_modules/.bin/qwen"
  elif [ -x "$HOME/.chad/tools/bin/qwen" ]; then
    QWEN_BIN="$HOME/.chad/tools/bin/qwen"
  else
    echo "FAIL: qwen not found in PATH or ~/.chad/tools" >&2
    echo "Install with: npm install -g @qwen-code/qwen-code" >&2
    exit 1
  fi
fi

PROMPT="ping"

echo "Using QWEN_BIN=${QWEN_BIN}"

TMP_OUT="$(mktemp)"
set +e
# Use -p "" to trigger non-interactive mode, pass prompt via stdin
echo "$PROMPT" | "$QWEN_BIN" --output-format stream-json --yolo -p "" >"$TMP_OUT" 2>&1
status=$?
set -e

if [ $status -ne 0 ]; then
  echo "FAIL: qwen exited $status"
  echo "---- output ----"
  sed -n '1,80p' "$TMP_OUT"
  exit $status
fi

echo "PASS: qwen responded"
echo "---- output (first 20 lines) ----"
sed -n '1,20p' "$TMP_OUT"
