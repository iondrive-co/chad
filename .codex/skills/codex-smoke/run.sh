#!/usr/bin/env bash
set -euo pipefail

CODEX_HOME="${CODEX_HOME:-$HOME/.chad/codex-homes/default}"
CODEX_BIN="${CODEX_BIN:-codex}"
PROMPT="ping"

echo "Using HOME=${CODEX_HOME}"
if [ ! -d "$CODEX_HOME" ]; then
  echo "FAIL: CODEX_HOME does not exist" >&2
  exit 1
fi

TMP_OUT="$(mktemp)"
set +e
HOME="$CODEX_HOME" "$CODEX_BIN" --dangerously-bypass-approvals-and-sandbox -C . "$PROMPT" >"$TMP_OUT" 2>&1
status=$?
set -e

if [ $status -ne 0 ]; then
  echo "FAIL: codex exited $status"
  echo "---- output ----"
  sed -n '1,80p' "$TMP_OUT"
  exit $status
fi

echo "PASS: codex responded"
echo "---- output (first 20 lines) ----"
sed -n '1,20p' "$TMP_OUT"
