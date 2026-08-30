#!/bin/sh

set -eu

if [ -z "${PUBLIC_HOST:-}" ]; then
    echo "Error: PUBLIC_HOST must be set to the public Twilio hostname." >&2
    exit 64
fi

case "$PUBLIC_HOST" in
    *://*|*/)
        echo "Error: PUBLIC_HOST must not include a protocol or trailing slash." >&2
        exit 64
        ;;
esac

exec python main.py \
    --host 0.0.0.0 \
    --port 7860 \
    --transport twilio \
    --proxy "$PUBLIC_HOST" \
    "$@"
