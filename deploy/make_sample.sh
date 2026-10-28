#!/bin/sh
# Build the sample city anyone can run from a clone: the frozen demo city (penn_demo, Saturday Oct 17, 2026) cut
# to Greater Center City, with 15 made-up Ticketmaster events and 15 made-up visitphilly picks in place of the
# real listings. Run from the repo root, in Git Bash, with the local ClickHouse up (like export_db.sh).
#
#   sh deploy/make_sample.sh        writes dumps/penn-sample-city.tar.gz, the asset on the GitHub release
#
# The archive unpacks at the repo root: dumps/penn_sample (loaded by deploy/restore_db.sh), the data/ files the
# code reads (areas, landmarks, street centerline, event catalog), and the eval questions and saved runs.
set -eu
CONTAINER=${PENN_CH_CONTAINER:-penn-ch}
OUT_FILE=dumps/penn-sample-city.tar.gz

ch() { docker exec -i "$CONTAINER" clickhouse-client "$@"; }

# Greater Center City: river to river, from Fairmount and the Art Museum down to Passyunk Square.
BOX="centroid_lat BETWEEN 39.925 AND 39.975 AND centroid_lon BETWEEN -75.195 AND -75.135"

ch -q "DROP DATABASE IF EXISTS penn_sample"
ch -q "CREATE DATABASE penn_sample"
for t in $(ch -q "SELECT name FROM system.tables WHERE database = 'penn_demo' ORDER BY name"); do
  ch -q "CREATE TABLE penn_sample.$t AS penn_demo.$t"
done

ch --multiquery <<SQL
INSERT INTO penn_sample.demo_info SELECT * FROM penn_demo.demo_info;
INSERT INTO penn_sample.ingest_runs SELECT * FROM penn_demo.ingest_runs;

-- City records only (street permits and announced closures), inside the box.
INSERT INTO penn_sample.events SELECT * FROM penn_demo.events FINAL
WHERE source IN ('lane_closures', 'event_closures') AND $BOX;

INSERT INTO penn_sample.permit_snapshots SELECT * FROM penn_demo.permit_snapshots
WHERE permit_number IN (SELECT source_record_id FROM penn_sample.events WHERE source = 'lane_closures');

-- Closures on the map inside the box, plus the unplaced rows of the same events (find_event reads them).
INSERT INTO penn_sample.event_closures SELECT * FROM penn_demo.event_closures FINAL
WHERE closure_id IN (SELECT source_record_id FROM penn_sample.events WHERE source = 'event_closures');
INSERT INTO penn_sample.event_closures SELECT * FROM penn_demo.event_closures FINAL
WHERE closure_id NOT IN (SELECT source_record_id FROM penn_demo.events FINAL WHERE source = 'event_closures')
  AND series IN (SELECT DISTINCT series FROM penn_sample.event_closures);

-- Made-up listings at real venues. The Delancey Strings is on both sources, so the map shows one flag for it.
INSERT INTO penn_sample.events (event_id, source, source_record_id, event_type, title, description, organization,
    occupancy, source_url, source_authority, claim_certainty, geometry_wkt, centroid_lat, centroid_lon, h3_r9,
    h3_cells, start_date, end_date, start_ts, end_ts, open_ended, status, raw_status, updated_at)
SELECT concat(src, ':sample-', slug), src, concat('sample-', slug), 'event', title, descr,
       if(src = 'ticketmaster', 'Ticketmaster (sample data)', 'the city''s official tourism guide (sample data)'), 'venue',
       concat('https://example.com/penn-sample/', slug), 'curated', 'scheduled',
       concat('POINT (', toString(lon), ' ', toString(lat), ')'), lat, lon,
       geoToH3(lat, lon, 9), [geoToH3(lat, lon, 9)], d0, d1,
       if(t0 = '', NULL, toDateTime64(concat(toString(d0), ' ', t0), 3, 'America/New_York')),
       if(t1 = '', NULL, toDateTime64(concat(toString(d1), ' ', t1), 3, 'America/New_York')),
       false, if(src = 'ticketmaster', 'onsale', 'listed'), '',
       toDateTime64('2026-10-17 10:00:00', 3, 'America/New_York')
FROM values('src String, slug String, title String, descr String, lat Float64, lon Float64,
             d0 Date, d1 Date, t0 String, t1 String',
  ('ticketmaster', 'delancey-strings', 'The Delancey Strings: Autumn Folk Night', 'Christ Church Neighborhood House, 20 N American Street, Philadelphia, PA; Music', 39.9512, -75.1442, '2026-10-17', '2026-10-17', '20:00:00', ''),
  ('ticketmaster', 'lamplighters-daughter', 'The Lamplighter''s Daughter', 'Arden Theatre, 40 N 2nd Street, Philadelphia, PA; Arts & Theatre', 39.9512, -75.1435, '2026-10-18', '2026-10-18', '14:00:00', ''),
  ('ticketmaster', 'neon-harbor', 'Neon Harbor with Static Bloom', 'Union Transfer, 1026 Spring Garden Street, Philadelphia, PA; Music', 39.9614, -75.1553, '2026-10-17', '2026-10-17', '20:00:00', ''),
  ('ticketmaster', 'sansom-street-quartet', 'Sansom Street Quartet', 'Chris'' Jazz Cafe, 1421 Sansom Street, Philadelphia, PA; Music', 39.9504, -75.1653, '2026-10-17', '2026-10-17', '21:00:00', ''),
  ('ticketmaster', 'juniper-row', 'Juniper Row: Acoustic Set', 'World Cafe Live, 3025 Walnut Street, Philadelphia, PA; Music', 39.9522, -75.1854, '2026-10-19', '2026-10-19', '19:30:00', ''),
  ('ticketmaster', 'skyline-musical', 'Skyline: A New Musical', 'Forrest Theatre, 1114 Walnut Street, Philadelphia, PA; Arts & Theatre', 39.9485, -75.1596, '2026-10-20', '2026-10-20', '19:30:00', ''),
  ('ticketmaster', 'brass-lantern', 'The Brass Lantern', 'Walnut Street Theatre, 825 Walnut Street, Philadelphia, PA; Arts & Theatre', 39.9486, -75.1555, '2026-10-22', '2026-10-22', '19:00:00', ''),
  ('ticketmaster', 'velvet-tramlines', 'The Velvet Tramlines', 'Theatre of Living Arts, 334 South Street, Philadelphia, PA; Music', 39.9415, -75.1489, '2026-10-23', '2026-10-23', '20:00:00', ''),
  ('ticketmaster', 'riverward-chamber', 'Riverward Chamber Ensemble: Fall Overture', 'Kimmel Center, 300 S Broad Street, Philadelphia, PA; Music', 39.9465, -75.1653, '2026-10-24', '2026-10-24', '19:30:00', ''),
  ('ticketmaster', 'late-show-comedy', 'Late Show Comedy Hour', 'Helium Comedy Club, 2031 Sansom Street, Philadelphia, PA; Arts & Theatre', 39.9515, -75.1747, '2026-10-24', '2026-10-24', '21:30:00', ''),
  ('ticketmaster', 'basement-showcase', 'Basement Showcase: Five New Bands', 'Underground Arts, 1200 Callowhill Street, Philadelphia, PA; Music', 39.9591, -75.1588, '2026-10-25', '2026-10-25', '21:00:00', ''),
  ('ticketmaster', 'second-act-improv', 'Second Act: An Improv Revue', 'Suzanne Roberts Theatre, 480 S Broad Street, Philadelphia, PA; Arts & Theatre', 39.9447, -75.1655, '2026-10-28', '2026-10-28', '19:30:00', ''),
  ('ticketmaster', 'copperline', 'Copperline', 'Franklin Music Hall, 421 N 7th Street, Philadelphia, PA; Music', 39.9593, -75.1497, '2026-10-30', '2026-10-30', '20:00:00', ''),
  ('ticketmaster', 'clockmaker-ballet', 'Moonlight Ballet: The Clockmaker', 'Academy of Music, 240 S Broad Street, Philadelphia, PA; Arts & Theatre', 39.9475, -75.1645, '2026-10-31', '2026-10-31', '19:00:00', ''),
  ('ticketmaster', 'northern-skies', 'Northern Skies Orchestra', 'The Met Philadelphia, 858 N Broad Street, Philadelphia, PA; Music', 39.9705, -75.1592, '2026-11-07', '2026-11-07', '20:00:00', ''),
  ('visitphilly', 'delancey-strings', 'The Delancey Strings: Autumn Folk Night', 'Christ Church Neighborhood House, 20 N American Street, Philadelphia, PA', 39.9512, -75.1442, '2026-10-17', '2026-10-17', '20:00:00', '22:00:00'),
  ('visitphilly', 'rittenhouse-art-fair', 'Rittenhouse Fall Art Fair', 'Rittenhouse Square, 18th and Walnut Streets, Philadelphia, PA', 39.9496, -75.1718, '2026-10-17', '2026-10-18', '', ''),
  ('visitphilly', 'colonial-crafts-day', 'Colonial Crafts Day', 'Independence Mall, 6th and Market Streets, Philadelphia, PA', 39.9514, -75.1505, '2026-10-18', '2026-10-18', '11:00:00', '16:00:00'),
  ('visitphilly', 'headhouse-fall-festival', 'Headhouse Square Fall Festival', 'Headhouse Square, 2nd and Lombard Streets, Philadelphia, PA', 39.9414, -75.1452, '2026-10-18', '2026-10-18', '10:00:00', '15:00:00'),
  ('visitphilly', 'love-park-lunchtime', 'Lunchtime Music in LOVE Park', 'LOVE Park, 1599 John F Kennedy Boulevard, Philadelphia, PA', 39.9541, -75.1653, '2026-10-21', '2026-10-21', '12:00:00', '13:30:00'),
  ('visitphilly', 'schuylkill-sunset-run', 'Schuylkill Banks Sunset Run', 'Schuylkill River Trail, Locust Street entrance, Philadelphia, PA', 39.9493, -75.1820, '2026-10-22', '2026-10-22', '18:00:00', '19:30:00'),
  ('visitphilly', 'art-after-hours', 'Art After Hours on the Steps', 'Philadelphia Museum of Art, 2600 Benjamin Franklin Parkway, Philadelphia, PA', 39.9656, -75.1810, '2026-10-23', '2026-10-23', '17:00:00', '20:45:00'),
  ('visitphilly', 'logan-lantern-walk', 'Logan Square Lantern Walk', 'Logan Square, 19th Street and Benjamin Franklin Parkway, Philadelphia, PA', 39.9582, -75.1709, '2026-10-24', '2026-10-24', '18:00:00', '21:00:00'),
  ('visitphilly', 'storytime-washington-square', 'Storytime Under the Trees', 'Washington Square, 210 W Washington Square, Philadelphia, PA', 39.9468, -75.1524, '2026-10-25', '2026-10-25', '10:30:00', '12:00:00'),
  ('visitphilly', 'harbor-glow', 'Harbor Glow Weekend', 'Spruce Street Harbor Park, 301 S Christopher Columbus Boulevard, Philadelphia, PA', 39.9445, -75.1416, '2026-10-30', '2026-11-01', '', ''),
  ('visitphilly', 'chinatown-night-market', 'Chinatown Night Market', 'Chinatown Friendship Gate, 10th and Arch Streets, Philadelphia, PA', 39.9536, -75.1561, '2026-10-31', '2026-10-31', '17:00:00', '22:00:00'),
  ('visitphilly', 'fairmount-halloween-walk', 'Fairmount Halloween Block Walk', 'Eastern State Penitentiary, 2027 Fairmount Avenue, Philadelphia, PA', 39.9683, -75.1727, '2026-10-31', '2026-10-31', '16:00:00', '19:00:00'),
  ('visitphilly', 'reading-terminal-pie-tour', 'Pie Tasting Tour', 'Reading Terminal Market, 1136 Arch Street, Philadelphia, PA', 39.9533, -75.1590, '2026-11-07', '2026-11-07', '10:00:00', '12:00:00'),
  ('visitphilly', 'south-9th-food-crawl', 'South 9th Street Food Crawl', 'South 9th Street, 9th and Christian Streets, Philadelphia, PA', 39.9385, -75.1577, '2026-11-08', '2026-11-08', '11:00:00', '16:00:00'),
  ('visitphilly', 'dilworth-harvest-market', 'Dilworth Park Harvest Market', 'Dilworth Park, 1 S 15th Street, Philadelphia, PA', 39.9524, -75.1647, '2026-11-13', '2026-11-15', '', '')
);
SQL

ch -q "SELECT source, count() AS n FROM penn_sample.events GROUP BY source ORDER BY source FORMAT PrettyCompact"

STAGE=$(mktemp -d)
PENN_DUMPS="$STAGE/dumps" sh deploy/export_db.sh penn_sample
cat > "$STAGE/dumps/penn_sample/README.txt" <<'TXT'
PENN sample city: the frozen demo city of Saturday, Oct 17, 2026, 10:00 a.m., cut to Greater Center City.

- Street permits and announced closures: City of Philadelphia open data (Street Lane Closures, Office of Special
  Events notices), as loaded on Oct 17, 2026. Only records inside Greater Center City are kept.
- Ticketmaster and visitphilly listings: made up for this sample (15 each, at real venues). They are not real
  events; their links go to example.com.
- Street centerline and landmarks: City of Philadelphia open data. Neighborhood outlines: (c) Robert Cheetham,
  CC BY 4.0.

Load it with: sh deploy/restore_db.sh penn_sample
TXT

mkdir -p "$STAGE/data/centerline" "$STAGE/eval/subsets" "$STAGE/eval/baselines" "$STAGE/eval/results"
cp -r data/areas "$STAGE/data/"
cp data/centerline/street_centerline.geojson "$STAGE/data/centerline/"
cp data/event_series.csv data/closure_routes.csv "$STAGE/data/"
cp eval/questions.jsonl eval/models.json "$STAGE/eval/"
cp eval/subsets/baseline12.json "$STAGE/eval/subsets/"
cp eval/baselines/super-penn_demo-2026-10-18-fixes.json eval/baselines/deepseek-v4-flash-penn_demo-2026-10-19.json \
   eval/baselines/qwen3.5-397b-penn_demo-2026-10-19.json "$STAGE/eval/baselines/"
# The Nemotron family on the current agent: Super (4 runs), Ultra (4), Lightning (1), all 58 questions.
cp eval/results/run-20261025-145718.json eval/results/run-20261028-*.json "$STAGE/eval/results/"

tar -czf "$OUT_FILE" -C "$STAGE" dumps data eval
rm -rf "$STAGE"
echo "== Wrote $OUT_FILE ($(du -h "$OUT_FILE" | cut -f1))"
