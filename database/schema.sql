-- PENN ClickHouse schema. Run once: clickhouse-client --multiquery < database/schema.sql

CREATE DATABASE IF NOT EXISTS penn;

-- Original payloads, for debugging when a source changes its schema.
CREATE TABLE IF NOT EXISTS penn.raw_records
(
    source           LowCardinality(String),
    source_record_id String,
    payload          String,                 -- JSON as received
    retrieved_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(retrieved_at)
ORDER BY (source, retrieved_at);

-- One row per real-world item. Re-ingesting a record replaces it (by updated_at).
-- Read with FINAL (or argMax) because merges happen in the background.
CREATE TABLE IF NOT EXISTS penn.events
(
    event_id          String,                -- "<source>:<source_record_id>"
    source            LowCardinality(String),
    source_record_id  String,
    event_type        LowCardinality(String),
    title             String,
    description       String,
    organization      String,
    occupancy         LowCardinality(String),
    source_url        String,
    source_authority  Enum8('official' = 1, 'curated' = 2, 'reported' = 3, 'derived' = 4),
    claim_certainty   Enum8('scheduled' = 1, 'observed' = 2, 'inferred' = 3, 'planned' = 4),
    geometry_wkt      String,
    centroid_lat      Float64,
    centroid_lon      Float64,
    h3_r9             UInt64,
    h3_cells          Array(UInt64),
    seg_ids           Array(UInt32) DEFAULT [],   -- street centerline segments; the exact legs of an intersection
    start_date        Nullable(Date),        -- date-only sources (permits, curated events)
    end_date          Nullable(Date),
    start_ts          Nullable(DateTime64(3, 'UTC')),  -- timestamped sources (event closure windows, ticketed events)
    end_ts            Nullable(DateTime64(3, 'UTC')),
    open_ended        Bool,
    status            LowCardinality(String),
    raw_status        LowCardinality(String),
    updated_at        DateTime64(3, 'UTC') DEFAULT now64(3),
    INDEX idx_cells h3_cells TYPE bloom_filter GRANULARITY 4
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (source, source_record_id);

-- Daily copy of the Lane Closures layer. The City drops expired permits,
-- so this table IS the closure history. first_seen / last_seen come from it.
CREATE TABLE IF NOT EXISTS penn.permit_snapshots
(
    snapshot_date    Date,
    permit_number    String,
    permit_type      LowCardinality(String),
    occupancy        LowCardinality(String),
    purpose          String,
    status           LowCardinality(String),
    effective_date   Nullable(Date),
    expiration_date  Nullable(Date),
    segment_count    UInt16
)
ENGINE = ReplacingMergeTree
ORDER BY (snapshot_date, permit_number);

-- Per-source freshness for the "last updated" stamps on the map.
CREATE TABLE IF NOT EXISTS penn.ingest_runs
(
    source       LowCardinality(String),
    started_at   DateTime64(3, 'UTC'),
    finished_at  DateTime64(3, 'UTC'),
    rows_written UInt32,
    ok           Bool,
    message      String
)
ENGINE = MergeTree
ORDER BY (source, finished_at);

-- Every model call, for the routing log shown in the demo.
CREATE TABLE IF NOT EXISTS penn.model_calls
(
    request_id    String,
    at            DateTime64(3, 'UTC') DEFAULT now64(3),
    model         LowCardinality(String),
    route_reason  String,
    prompt_tokens UInt32,
    output_tokens UInt32,
    latency_ms    UInt32,
    tools_called  Array(String)
)
ENGINE = MergeTree
ORDER BY (at);

-- Announced street closures for one-off and annual events (data/event_closures/).
CREATE TABLE IF NOT EXISTS penn.event_closures
(
    closure_id     String,                  -- series-date-hash of street/from/to
    series         LowCardinality(String),
    year           UInt16,
    start_date     Date,
    end_date       Date,
    street         String,
    from_street    String,
    to_street      String,
    start_ts       Nullable(DateTime64(3, 'UTC')),
    end_ts         Nullable(DateTime64(3, 'UTC')),
    conditional    Bool,
    notes          String,
    source_url     String,
    geocoded       Bool DEFAULT false,      -- set by stage 2
    updated_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (series, start_date, closure_id);
