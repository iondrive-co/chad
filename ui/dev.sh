#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "$0")/.." && pwd)"
API_PORT=8000

cleanup() {
    if [ -n "$API_PID" ]; then
        kill "$API_PID" 2>/dev/null
        wait "$API_PID" 2>/dev/null
    fi
}
trap cleanup EXIT

# Start Chad API server in background
"$DIR/.venv/bin/python" -m chad --mode server --api-port "$API_PORT" &
API_PID=$!

# Wait until the API is reachable
echo "Waiting for Chad API on port $API_PORT..."
until curl -sf "http://localhost:$API_PORT/status" >/dev/null 2>&1; do
    if ! kill -0 "$API_PID" 2>/dev/null; then
        echo "API server exited unexpectedly"
        exit 1
    fi
    sleep 0.3
done
echo "Chad API ready"

# Start Vite dev server (foreground)
cd "$DIR/ui"
exec npx vite --open
