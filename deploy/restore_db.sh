#!/bin/sh
# Load dumps from deploy/export_db.sh into a ClickHouse. Each table is dropped and recreated, so running it
# again replaces the data rather than doubling it. penn.events can come back with fewer rows than the export:
# it is a ReplacingMergeTree, and older versions of a re-ingested record merge away. Compare
# uniqExact(event_id), not count().
#   sh deploy/restore_db.sh [db ...]      default: every database under dumps/
# Run from the repo root. By default it loads into the local container (PENN_CH_CONTAINER, default penn-ch), like
# export_db.sh reads from it; inside the image build the server is local: PENN_CH_CLIENT="clickhouse client"
# (deploy/endpoint/bake.sh).
set -eu
IN=${PENN_DUMPS:-dumps}
DBS=${*:-$(ls "$IN")}
CONTAINER=${PENN_CH_CONTAINER:-penn-ch}

ch() { docker exec -i "$CONTAINER" clickhouse-client "$@"; }
if [ -n "${PENN_CH_CLIENT:-}" ]; then ch() { $PENN_CH_CLIENT "$@"; }; fi

for db in $DBS; do
  ch -q "CREATE DATABASE IF NOT EXISTS $db"
  for sql in "$IN/$db"/*.sql; do
    t=$(basename "$sql" .sql)
    ch -q "DROP TABLE IF EXISTS $db.$t"
    ch --multiquery < "$sql"
    if [ "$(gunzip -c "$IN/$db/$t.native.gz" | head -c 1 | wc -c)" -gt 0 ]; then   # an empty table has no rows to insert
      gunzip -c "$IN/$db/$t.native.gz" | ch -q "INSERT INTO $db.$t FORMAT Native"
    fi
    echo "$db.$t: $(ch -q "SELECT count() FROM $db.$t") rows"
  done
done
