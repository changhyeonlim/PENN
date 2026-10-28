#!/bin/bash
# The daily refresh inside the container: City lane closures and Ticketmaster, once shortly after start and
# then every day at REFRESH_AT_UTC (default 11:30, i.e. 7:30 a.m. in Philadelphia in summer time, 6:30 in
# winter). visitphilly's weekly picks and the City's closure notices are saved by hand in a browser; your PC
# extracts them and uploads the result to the data bucket (deploy/data_bucket.py), and the pull below loads
# whatever is new. A failed job is logged and skipped; it never stops the site.
cd /app
export CLICKHOUSE_DB=penn
AT=${REFRESH_AT_UTC:-11:30}

refresh() {
  timeout 1800 python deploy/data_bucket.py pull || echo "refresh: data bucket pull failed" >&2
  timeout 300 python deploy/data_bucket.py push-usage || echo "refresh: usage summary upload failed" >&2
  for job in lane_closures ticketmaster; do
    timeout 1800 python jobs/run_job.py "$job" || echo "refresh: $job failed" >&2
  done
}

sleep "${REFRESH_DELAY_S:-60}"     # let the APIs come up first
refresh
while true; do
  now=$(date -u +%s)
  next=$(date -u -d "today $AT" +%s)
  [ "$next" -gt "$now" ] || next=$(date -u -d "tomorrow $AT" +%s)
  sleep $((next - now))
  refresh
done
