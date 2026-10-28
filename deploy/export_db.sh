#!/bin/sh
# Dump PENN's ClickHouse databases to files that deploy/restore_db.sh loads into the Docker stack.
# Run where the data lives (your machine):   sh deploy/export_db.sh [db ...]      default: penn penn_demo
# Writes dumps/<db>/<table>.sql (the CREATE) and dumps/<db>/<table>.native.gz (the rows).
#
# It talks to ClickHouse through `docker exec` on the local container; set PENN_CH_CONTAINER (default penn-ch)
# and PENN_CH_ARGS (default: none, i.e. user "default" with no password) to point it elsewhere.
set -eu
CONTAINER=${PENN_CH_CONTAINER:-penn-ch}
ARGS=${PENN_CH_ARGS:-}
OUT=${PENN_DUMPS:-dumps}
DBS=${*:-penn penn_demo}

ch() { docker exec -i "$CONTAINER" clickhouse-client $ARGS "$@"; }

for db in $DBS; do
  mkdir -p "$OUT/$db"
  for t in $(ch -q "SELECT name FROM system.tables WHERE database = '$db' AND NOT is_temporary ORDER BY name"); do
    ch -q "SHOW CREATE TABLE $db.$t FORMAT TSVRaw" > "$OUT/$db/$t.sql"
    ch -q "SELECT * FROM $db.$t FORMAT Native" | gzip > "$OUT/$db/$t.native.gz"
    echo "$db.$t: $(ch -q "SELECT count() FROM $db.$t") rows"
  done
done
du -sh "$OUT"
