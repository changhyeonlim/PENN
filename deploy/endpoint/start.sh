#!/bin/bash
# The container's main process: ClickHouse, the two APIs, the map, and the daily refresh. If any of them
# exits, the container exits too, so a restart brings everything back rather than leaving a half-working site.
set -uo pipefail
cd /app

clickhouse server --config-file=/etc/clickhouse-server/config.xml &
until clickhouse client -q "SELECT 1" > /dev/null 2>&1; do sleep 1; done

export CLICKHOUSE_HOST=127.0.0.1 CLICKHOUSE_PORT=8123
# The public demo's guardrails (backend/penn/guardrails.py). Visitors' questions spend the deployment's Token
# Factory and Tavily keys, so every layer is on here. Each value can be overridden with docker run -e.
export PENN_PUBLIC=1
# One proxy in front (Caddy, or the host's ingress) appends the visitor to X-Forwarded-For (Next.js adds nothing).
# Check the "guardrails: address headers" log lines on the live site, and change this or set PENN_CLIENT_IP_HEADER.
export PENN_TRUSTED_PROXIES=${PENN_TRUSTED_PROXIES:-1}
export PENN_CLIENT_IP_HEADER=${PENN_CLIENT_IP_HEADER:-}
# Real answers on the eval: median ~20k tokens, largest 75k, so no question may spend more than 100k. At $0.30/M
# input and $0.90/M output a typical answer is about $0.007.
export PENN_TURN_TOKEN_BUDGET=${PENN_TURN_TOKEN_BUDGET:-100000}
export PENN_TURN_SECONDS=${PENN_TURN_SECONDS:-120}
export PENN_WEB_SEARCHES_PER_DAY=${PENN_WEB_SEARCHES_PER_DAY:-60}
# Pools (guardrails.POOLS; defaults there, per API process): visitors, bots included, share 30 fresh answers a
# day (8 an hour); judges, with PENN_JUDGE_CODE (a deployment setting), have their own 100 a day. Bots can reach
# only the visitor pool: about $2 a day at worst across both views.
CLICKHOUSE_DB=penn uvicorn penn.api:app --host 127.0.0.1 --port 8001 &
# The demo's answers are kept (the data is frozen), so most of its questions cost nothing after the first.
CLICKHOUSE_DB=penn_demo PENN_NOW="${PENN_DEMO_NOW:-2026-10-17T10:00:00-04:00}" \
  uvicorn penn.api:app --host 127.0.0.1 --port 8002 &
(cd web && HOSTNAME=0.0.0.0 PORT=3000 exec node server.js) &
bash deploy/refresh.sh &
# The usage summaries (today's so far) to the data bucket every hour; `data_bucket.py usage` reads them.
(while true; do sleep 3600; python deploy/data_bucket.py push-usage || true; done) &

wait -n
echo "start.sh: a PENN process exited; stopping the container so it restarts whole" >&2
exit 1
