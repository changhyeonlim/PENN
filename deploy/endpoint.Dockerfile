# PENN in ONE container: ClickHouse with the data baked in, both APIs, the map, and a daily refresh. Only port
# 3000 (the map) is exposed; everything else listens on 127.0.0.1.
# Built by deploy/release.sh, which assembles the context: code from a git ref, plus data/ and dumps/ from your
# checkout.

FROM node:22-slim AS web
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# The browser calls /api/live and /api/demo; the map's server forwards them to the APIs inside this container.
ENV NEXT_PUBLIC_PENN_API=/api/live PENN_API_LIVE=http://127.0.0.1:8001 PENN_API_DEMO=http://127.0.0.1:8002 \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM clickhouse/clickhouse-server:26.9.23.15 AS clickhouse

FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY --from=clickhouse /usr/bin/clickhouse /usr/bin/clickhouse
COPY --from=clickhouse /etc/clickhouse-server/config.xml /etc/clickhouse-server/users.xml /etc/clickhouse-server/
COPY deploy/endpoint/clickhouse.xml /etc/clickhouse-server/config.d/penn.xml
COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node

# Editable, so penn's __file__ stays under /app/backend and finds /app/data.
COPY backend/pyproject.toml backend/
COPY backend/penn backend/penn
RUN pip install --no-cache-dir -e "./backend[bucket]"
COPY jobs jobs
COPY deploy/restore_db.sh deploy/data_bucket.py deploy/endpoint/ deploy/
RUN chmod +x deploy/*.sh

# The database, loaded at build time so the container starts in seconds and its data can't be lost.
COPY data data
COPY dumps /tmp/dumps
RUN sh deploy/bake.sh && rm -rf /tmp/dumps

COPY --from=web /app/.next/standalone web/
COPY --from=web /app/.next/static web/.next/static
COPY --from=web /app/public web/public

EXPOSE 3000
CMD ["bash", "deploy/start.sh"]
