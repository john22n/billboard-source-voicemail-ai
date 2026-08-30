#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-7860}"
SCENARIO="${SCENARIO:-$SCRIPT_DIR/scenarios/inquire_question.yml}"
RUNNER_BODY="${RUNNER_BODY:-}"
APP_PID=""

cleanup() {
    trap - EXIT INT TERM

    if [[ -n "$APP_PID" ]] && kill -0 "$APP_PID" 2>/dev/null; then
        kill "$APP_PID" 2>/dev/null || true
        wait "$APP_PID" 2>/dev/null || true
    fi
}

trap cleanup EXIT INT TERM

for command in uv lsof; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Error: '$command' is required but was not found." >&2
        exit 1
    fi
done

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Error: environment file not found: $ENV_FILE" >&2
    exit 1
fi

if ! uv run dotenv -f "$ENV_FILE" run -- sh -c 'test -n "$OPENAI_API_KEY"'; then
    echo "Error: OPENAI_API_KEY must be set in $ENV_FILE." >&2
    exit 1
fi

if [[ ! -f "$SCENARIO" ]]; then
    echo "Error: eval scenario not found: $SCENARIO" >&2
    exit 1
fi

if [[ -n "$RUNNER_BODY" && ! -f "$RUNNER_BODY" ]]; then
    echo "Error: eval runner body not found: $RUNNER_BODY" >&2
    exit 1
fi

if lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null; then
    echo "Error: port $PORT is already in use." >&2
    exit 1
fi

APP_ARGS=(
    --host "$HOST"
    --port "$PORT"
    --transport eval
)
if [[ -n "$RUNNER_BODY" ]]; then
    APP_ARGS+=(--runner-body "$RUNNER_BODY")
fi

echo "Starting the app with the eval transport on http://$HOST:$PORT ..."
NUTSHELL_LEAD_SUBMISSION_ENABLED=false uv run dotenv -f "$ENV_FILE" run -- \
    uv run python "$SCRIPT_DIR/main.py" "${APP_ARGS[@]}" &
APP_PID=$!

for _ in {1..80}; do
    if ! kill -0 "$APP_PID" 2>/dev/null; then
        echo "Error: the app exited before becoming ready." >&2
        wait "$APP_PID"
    fi

    if lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null; then
        break
    fi

    sleep 0.25
done

if ! lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null; then
    echo "Error: the app did not become ready on port $PORT." >&2
    exit 1
fi

echo "App is ready. Running eval scenario: $SCENARIO"
uv run dotenv -f "$ENV_FILE" run -- \
    uv run pipecat eval run \
        "$SCENARIO" \
        --bot-url "ws://127.0.0.1:$PORT" \
        "$@"
