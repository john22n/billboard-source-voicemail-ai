FROM ghcr.io/astral-sh/uv:0.8.14 AS uv

FROM python:3.13-slim-bookworm AS builder

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.13-slim-bookworm

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VOICEMAIL_AUDIT_LOG=/app/logs/voicemail-audit.jsonl

RUN addgroup --system app \
    && adduser --system --ingroup app --home /app app \
    && mkdir -p /app/logs \
    && chown app:app /app/logs

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app bot.py main.py ./
COPY --chown=app:app src ./src
COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

USER app

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/status', timeout=3)"]

ENTRYPOINT ["docker-entrypoint.sh"]
