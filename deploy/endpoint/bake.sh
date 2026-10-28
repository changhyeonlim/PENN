#!/bin/sh
# Build-time step of deploy/endpoint.Dockerfile: start ClickHouse, load the dumps, load the visitphilly snapshots
# in data/ (the weekly picks saved since the dumps were made), and stop, leaving the data in the image.
set -eu
cd /app
clickhouse server --config-file=/etc/clickhouse-server/config.xml > /tmp/clickhouse.log 2>&1 &
CH=$!
i=0
until clickhouse client -q "SELECT 1" > /dev/null 2>&1; do
  i=$((i + 1)); [ "$i" -lt 120 ] || { cat /tmp/clickhouse.log; exit 1; }; sleep 1
done
PENN_CH_CLIENT="clickhouse client" PENN_DUMPS=/tmp/dumps sh deploy/restore_db.sh
CLICKHOUSE_HOST=127.0.0.1 CLICKHOUSE_DB=penn python jobs/run_job.py visitphilly
clickhouse client -q "SELECT database, count() AS parts FROM system.parts WHERE active GROUP BY database"
kill "$CH"
wait "$CH" || true
