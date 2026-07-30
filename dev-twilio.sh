#!/usr/bin/env bash

set -Eeuo pipefail

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-7860}"
NGROK_PID=""
APP_PID=""
NGROK_LOG="$(mktemp -t voicemail-ngrok.XXXXXX)"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${LOG_DIR:-$SCRIPT_DIR/logs}"
AUDIT_LOG="$LOG_DIR/voicemail-audit.jsonl"

mkdir -p "$LOG_DIR"
touch "$AUDIT_LOG"
chmod 600 "$AUDIT_LOG"
export VOICEMAIL_AUDIT_LOG="$AUDIT_LOG"

cleanup() {
    trap - EXIT INT TERM

    if [[ -n "$APP_PID" ]] && kill -0 "$APP_PID" 2>/dev/null; then
        kill "$APP_PID" 2>/dev/null || true
    fi
    if [[ -n "$NGROK_PID" ]] && kill -0 "$NGROK_PID" 2>/dev/null; then
        kill "$NGROK_PID" 2>/dev/null || true
    fi

    [[ -z "$APP_PID" ]] || wait "$APP_PID" 2>/dev/null || true
    [[ -z "$NGROK_PID" ]] || wait "$NGROK_PID" 2>/dev/null || true
    rm -f "$NGROK_LOG"
}

trap cleanup EXIT INT TERM

for command in uv ngrok curl; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Error: '$command' is required but was not found." >&2
        exit 1
    fi
done

if command -v lsof >/dev/null 2>&1 && lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null; then
    echo "Error: port $PORT is already in use." >&2
    exit 1
fi

echo "Starting ngrok tunnel for http://localhost:$PORT ..."
ngrok http "http://localhost:$PORT" \
    --log="$NGROK_LOG" \
    --log-format=json &
NGROK_PID=$!

PUBLIC_URL=""
for _ in {1..40}; do
    if ! kill -0 "$NGROK_PID" 2>/dev/null; then
        echo "Error: ngrok exited before creating a tunnel. Recent output:" >&2
        tail -20 "$NGROK_LOG" >&2
        exit 1
    fi

    PUBLIC_URL="$(uv run python -c '
import json
import sys

port = sys.argv[1]
log_path = sys.argv[2]
try:
    with open(log_path) as log:
        for line in log:
            event = json.loads(line)
            address = str(event.get("addr", ""))
            public_url = str(event.get("url", ""))
            if (
                event.get("msg") == "started tunnel"
                and address.rstrip("/").endswith(f":{port}")
                and public_url.startswith("https://")
            ):
                print(public_url)
                break
except (FileNotFoundError, json.JSONDecodeError):
    pass
' "$PORT" "$NGROK_LOG" 2>/dev/null || true)"

    [[ -z "$PUBLIC_URL" ]] || break
    sleep 0.25
done

if [[ -z "$PUBLIC_URL" ]]; then
    echo "Error: ngrok did not create an HTTPS tunnel. Recent ngrok output:" >&2
    tail -20 "$NGROK_LOG" >&2
    exit 1
fi

PROXY_HOST="${PUBLIC_URL#https://}"
PROXY_HOST="${PROXY_HOST#http://}"
PROXY_HOST="${PROXY_HOST%/}"

uv run python main.py \
    --host "$HOST" \
    --port "$PORT" \
    --transport twilio \
    --proxy "$PROXY_HOST" \
    "$@" &
APP_PID=$!

for _ in {1..80}; do
    if ! kill -0 "$APP_PID" 2>/dev/null; then
        echo "Error: Pipecat exited before becoming ready." >&2
        wait "$APP_PID"
    fi
    curl -fsS "http://localhost:$PORT/status" >/dev/null 2>&1 && break
    sleep 0.25
done

if ! curl -fsS "http://localhost:$PORT/status" >/dev/null 2>&1; then
    echo "Error: Pipecat did not become ready on port $PORT." >&2
    exit 1
fi

echo
echo "================================================================"
echo " TWILIO DEVELOPMENT ENDPOINT READY"
echo "================================================================"
echo " PUBLIC URL:    $PUBLIC_URL/"
echo " VOICE WEBHOOK: $PUBLIC_URL/"
echo " WEBSOCKET:     wss://$PROXY_HOST/ws"
echo " LOCAL STATUS:  http://localhost:$PORT/status"
echo " AUDIT LOG:     $AUDIT_LOG"
echo "================================================================"
echo
echo "Set Twilio's incoming-call webhook to the VOICE WEBHOOK URL above."
echo "Press Ctrl+C to stop both Pipecat and ngrok."
echo

wait "$APP_PID"
