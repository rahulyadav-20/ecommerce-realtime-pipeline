-- =============================================================================
-- Step 4.3 — ClickHouse Kafka Engine + Materialized View
-- =============================================================================
--
-- Execution (run after the stack is up and events_local exists):
--
--   docker exec -i clickhouse clickhouse-client --multiquery \
--     < clickhouse/02_kafka_engine.sql
--
-- NOTE: the container name in docker-compose is `clickhouse`, not
--       `clickhouse-server`.  The validation snippet in the task spec uses
--       the older default name — adjust if yours differs.
--
-- HOW THE THREE-OBJECT PIPELINE WORKS
-- ─────────────────────────────────────
--
--   [Kafka topic]
--        │
--        │  AvroConfluent deserialization
--        │  (schema fetched from Schema Registry per message magic byte)
--        ▼
--   ecom.events_kafka          ← Kafka Engine virtual table
--        │                       reads continuously, no data stored here
--        │  Materialized View trigger (fires on every block read from Kafka)
--        ▼
--   ecom.events_mv             ← MV applies column projection + type casts
--        │
--        ▼
--   ecom.events_local          ← ReplacingMergeTree — durable storage
--
-- This is the canonical ClickHouse pattern for Kafka ingestion.  The Kafka
-- Engine table is a virtual cursor: it never stores rows, it only deserializes
-- each Kafka message and exposes it as a block that the MV reads exactly once.
-- The MV insert into events_local is atomic per block.
--
-- OFFSET MANAGEMENT
-- ─────────────────
-- ClickHouse commits Kafka offsets only after the MV INSERT has been flushed
-- to disk (post-quorum in replicated setups).  This gives at-least-once
-- semantics; duplicates are handled by ReplacingMergeTree(ingest_ts).
-- =============================================================================

-- =============================================================================
-- 1. KAFKA ENGINE TABLE  —  ecom.events_kafka
-- =============================================================================
--
-- SCHEMA NOTES
-- ─────────────
-- Types here must match what AvroConfluent actually deserialises from the wire,
-- not what events_local stores after encoding:
--
--   • No CODEC clauses — the Kafka engine never writes to disk; codecs are
--     ignored silently but add noise.
--
--   • No LowCardinality — the Kafka engine does not benefit from dictionary
--     encoding (data flows through in memory, never persisted).  LowCardinality
--     in the target table (events_local) is what matters for storage.
--
--   • quantity: Avro `int` is a signed 32-bit integer → Int32 here.
--     The MV casts it to UInt32 on the way into events_local (Flink has
--     already validated quantity >= 0 upstream so no data is lost).
--
--   • event_type: Avro `enum` deserialises as a plain string in ClickHouse's
--     AvroConfluent reader.
--
--   • _error / _raw_message: virtual columns exposed by the Kafka engine when
--     kafka_handle_error_mode = 'stream'.  They do not appear in the column
--     list but are accessible in SELECT and MV definitions.

CREATE TABLE IF NOT EXISTS ecom.events_kafka
(
    event_id        String,
    event_time      DateTime64(3, 'UTC'),
    user_id         Nullable(String),
    session_id      String,
    product_id      Nullable(String),
    category        Nullable(String),
    event_type      String,
    quantity        Nullable(Int32),    -- Avro int is signed 32-bit
    price           Nullable(Float64),
    payment_mode    Nullable(String),
    device          String,
    ip              String
)
ENGINE = Kafka
SETTINGS
    -- -----------------------------------------------------------------------
    -- Connectivity
    -- -----------------------------------------------------------------------
    kafka_broker_list            = 'kafka-1:29092,kafka-2:29093',

    -- The clean topic produced by the Flink validation job.
    -- events_local's TTL covers 2 years; read from earliest on first boot,
    -- then track offsets in the consumer group.
    kafka_topic_list             = 'ecommerce.events.clean.v1',

    -- Consumer group.  One group per downstream system keeps offsets
    -- independent — Druid, ClickHouse, and any future consumers all read
    -- the same topic without stepping on each other.
    kafka_group_name             = 'clickhouse-ecom-events',

    -- -----------------------------------------------------------------------
    -- Serialisation
    -- -----------------------------------------------------------------------

    -- AvroConfluent: each message starts with the Confluent magic byte (0x00)
    -- followed by a 4-byte schema ID.  ClickHouse fetches the schema from the
    -- registry on first encounter of a new ID and caches it.
    kafka_format                 = 'AvroConfluent',
    format_avro_schema_registry_url = 'http://schema-registry:8081',

    -- -----------------------------------------------------------------------
    -- Parallelism
    -- -----------------------------------------------------------------------

    -- 4 consumer threads × up to 12 partitions on the topic.
    -- Each thread owns a disjoint set of partition assignments.
    -- The server-level default (set in config.xml) is also 4; this setting
    -- is explicit here so it survives a config.xml change.
    kafka_num_consumers          = 4,

    -- How many Kafka messages are batched into one ClickHouse block before
    -- the MV INSERT fires.  Larger blocks → fewer merges, better compression.
    -- 65 536 = ~64 K messages per flush cycle.
    kafka_max_block_size         = 65536,

    -- -----------------------------------------------------------------------
    -- Error handling
    -- -----------------------------------------------------------------------

    -- 'stream' mode: malformed or schema-mismatch messages do NOT crash the
    -- consumer.  Instead the raw bytes land in the virtual `_raw_message`
    -- column and the error description in `_error`.  The dead-letter MV below
    -- routes these to ecom.kafka_errors for inspection.
    --
    -- Alternative: 'default' (log and skip, no DLQ).
    kafka_handle_error_mode      = 'stream',

    -- -----------------------------------------------------------------------
    -- Offset / commit behaviour
    -- -----------------------------------------------------------------------

    -- Commit offsets to Kafka after every block flush (matches kafka_max_block_size).
    -- Setting to 0 commits after each individual message — much higher overhead.
    kafka_commit_every_batch     = 0,

    -- Milliseconds between forced flushes even if kafka_max_block_size is not
    -- reached.  7.5 s keeps end-to-end latency reasonable for dashboards that
    -- refresh every 10–30 s.
    kafka_flush_interval_ms      = 7500;


-- =============================================================================
-- 2. DEAD-LETTER TABLE  —  ecom.kafka_errors
-- =============================================================================
--
-- Stores raw bytes + error description for every message that failed
-- AvroConfluent deserialisation.  Useful for schema evolution debugging
-- and producer-side bug investigation.

CREATE TABLE IF NOT EXISTS ecom.kafka_errors
(
    -- When the error was captured by ClickHouse (not the original event_time,
    -- which cannot be parsed from a malformed message).
    captured_at     DateTime64(3, 'UTC')  DEFAULT now64(3),

    -- Human-readable description from the deserialiser.
    error           String,

    -- Raw Kafka message bytes.  Cast to String for text-format messages,
    -- or keep as a hex dump for binary inspection.
    raw_message     String
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(captured_at)
ORDER BY (captured_at)
TTL toDate(captured_at) + INTERVAL 30 DAY DELETE
SETTINGS
    index_granularity   = 8192,
    ttl_only_drop_parts = 1;


-- =============================================================================
-- 3. DEAD-LETTER MATERIALIZED VIEW  —  ecom.kafka_errors_mv
-- =============================================================================
--
-- Triggered on every block read from events_kafka.
-- Routes only the rows where _error is non-empty into ecom.kafka_errors.
-- Healthy rows (empty _error) are ignored by this MV; the main MV handles them.

CREATE MATERIALIZED VIEW IF NOT EXISTS ecom.kafka_errors_mv
TO ecom.kafka_errors
AS
SELECT
    now64(3)        AS captured_at,
    _error          AS error,
    _raw_message    AS raw_message
FROM ecom.events_kafka
WHERE length(_error) > 0;


-- =============================================================================
-- 4. MAIN MATERIALIZED VIEW  —  ecom.events_mv
-- =============================================================================
--
-- WHY NOT SELECT * ?
-- ───────────────────
-- • The Kafka table has no `ingest_ts` column; SELECT * would omit it and
--   rely on the DEFAULT expression in events_local — which would work, but
--   is implicit and fragile if the schema changes.
-- • `quantity` is Nullable(Int32) in the Kafka table (Avro int is signed
--   32-bit) but Nullable(UInt32) in events_local.  An explicit CAST makes
--   the intention clear and avoids a silent implicit narrowing conversion.
-- • LowCardinality columns in events_local accept plain String inserts;
--   ClickHouse applies the dictionary encoding transparently.
-- • `_error` rows (handled by the DLQ MV above) must be filtered out here
--   so malformed messages are not partially inserted into events_local.
--
-- ATOMICITY
-- ──────────
-- Both this MV and kafka_errors_mv fire on the same block.  ClickHouse
-- processes MV inserts inside the same transaction as the source block read,
-- so a Kafka offset is committed only after both MV targets have been written.

CREATE MATERIALIZED VIEW IF NOT EXISTS ecom.events_mv
TO ecom.events_local
AS
SELECT
    event_id,
    event_time,
    user_id,
    session_id,
    product_id,
    category,
    event_type,
    -- Avro int (Int32) → UInt32; safe because Flink validates quantity >= 0.
    CAST(quantity AS Nullable(UInt32))  AS quantity,
    price,
    payment_mode,
    device,
    ip,
    -- Populate the version column used by ReplacingMergeTree for deduplication.
    -- now64(3) gives the write timestamp of this batch, which is later than
    -- any previous ingest_ts for the same event_id, ensuring retried messages
    -- correctly overwrite stale rows after background merges.
    now64(3)                            AS ingest_ts
FROM ecom.events_kafka
-- Only route clean messages to storage; malformed ones go to kafka_errors via
-- the DLQ MV above.
WHERE length(_error) = 0;


-- =============================================================================
-- VERIFICATION
-- =============================================================================
--
-- Run these queries after executing this script to confirm the pipeline is live.
--
-- 1. Confirm all three objects were created:
--
--    SELECT name, engine
--    FROM system.tables
--    WHERE database = 'ecom'
--    ORDER BY name;
--
--    Expected rows:
--      events_kafka      Kafka
--      events_local      ReplacingMergeTree
--      events_mv         MaterializedView
--      kafka_errors      MergeTree
--      kafka_errors_mv   MaterializedView
--
-- 2. Check consumer group lag (ClickHouse 23.9+):
--
--    SELECT *
--    FROM system.kafka_consumers
--    WHERE database = 'ecom';
--
-- 3. Watch row count grow (run every 10 s):
--
--    SELECT
--        count()                         AS total_rows,
--        max(event_time)                 AS latest_event,
--        max(ingest_ts)                  AS latest_ingest,
--        countIf(event_type='PAYMENT_SUCCESS') AS payments
--    FROM ecom.events_local;
--
-- 4. Check for deserialization errors:
--
--    SELECT captured_at, error, left(raw_message, 120) AS preview
--    FROM ecom.kafka_errors
--    ORDER BY captured_at DESC
--    LIMIT 20;
--
-- 5. Inspect Kafka consumer internals:
--
--    SELECT *
--    FROM system.kafka_consumers
--    WHERE database = 'ecom' AND table = 'events_kafka';
