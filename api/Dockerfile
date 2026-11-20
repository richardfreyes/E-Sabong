# syntax=docker/dockerfile:1.7
# Build context: repository root (the image needs api/ and db/migrations/).

FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY api/pyproject.toml ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install .
COPY api/app ./app
RUN /opt/venv/bin/pip install --no-deps .

FROM python:3.12-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEDIA_ROOT=/data/media
# uid/gid 999 (what --system has always assigned here) is pinned: MediaMTX runs as the same ids so
# the API and worker can expire the recordings it writes (docker-compose.yml, `recordings`).
RUN groupadd --system --gid 999 app && useradd --system --uid 999 --gid app --home /app app \
 && mkdir -p /data/media /data/recordings && chown app:app /data/media /data/recordings
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY --chown=app:app api/alembic.ini ./
COPY --chown=app:app api/app ./app
COPY --chown=app:app db/migrations /db/migrations
USER app
EXPOSE 8000
# Caddy is the only client; trust its X-Forwarded-For so audit logs record real IPs.
# A busy worker on a throttled CPU can miss uvicorn's 5 s supervisor ping and be killed with its requests
# and sockets; 30 s still replaces a hung worker. Keep-alive outlives Caddy's 2-minute idle upstream
# connections so Caddy closes them first (otherwise a POST can meet a closing connection: 502).
# WebSocket frames from the portals are tiny; 1 MiB (not uvicorn's 16 MiB) bounds per-socket memory.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", \
     "--timeout-worker-healthcheck", "30", "--timeout-keep-alive", "130", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header", "--ws-max-size", "1048576"]
