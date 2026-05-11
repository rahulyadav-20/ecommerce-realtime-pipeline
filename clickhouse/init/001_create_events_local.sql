-- =============================================================================
-- ecom.events_local  —  primary ClickHouse storage table
-- Engine : ReplacingMergeTree
-- =============================================================================
--
-- DESIGN RATIONALE
-- ─────────────────
-- ReplacingMergeTree(ingest_ts)
--   Flink writes the clean Kafka topic to ClickHouse via JDBC or the
--   clickhouse-kafka connector.  Kafka at-least-once delivery means a row
--   with the same event_id can arrive more than once.  ReplacingMergeTree
--   deduplicates rows that share the same ORDER BY key, keeping the one with
--   the highest `ingest_ts` (the version column).  Deduplication is applied
--   lazily during background merges, so always append FINAL to analytical
--   queries that require exact counts, or use a GROUP BY event_id HAVING
--   max(ingest_ts) pattern for high-throughput ingestion paths.
--
-- ORDER BY (event_type, category, event_time, event_id)
--   MergeTree ORDER BY is both the primary index and the deduplication key.
--   Column order follows the "high-cardinality last" rule for index efficiency:
--     1. event_type   — 9 distinct values; the most selective filter in 90 %
--                       of OLAP queries ("WHERE event_type = 'PAYMENT_SUCCESS'")
--     2. category     — O(10–100) distinct values; second most common GROUP BY
--     3. event_time   — timestamp range scans always follow type + category
--     4. event_id     — appended last to make the key unique per event so that
--                       ReplacingMergeTree can identify duplicates correctly
--
-- PARTITION BY toYYYYMMDD(event_time)
--   Daily partitions give the TTL engine a clean unit to drop entire parts
--   once they age out (no row-level deletes needed).  They also enable
--   partition pruning for "WHERE event_time BETWEEN …" queries.
--   At ~1 M events/day a partition is roughly 50–200 MB on disk after
--   compression — well within ClickHouse's optimal 1 GB/part target.
--
-- TTL event_time + INTERVAL 2 YEAR DELETE
--   Raw events are archived to Iceberg/MinIO by the Airflow compaction DAG
--   after 90 days.  The 2-year TTL here is a safety net: it evicts parts
--   that were never archived (e.g. data from failed archive runs) and keeps
--   hot storage costs bounded.  ttl_only_drop_parts = 1 means the engine
--   drops an entire part only when every row in it has expired, avoiding
--   expensive row-level mutations.
--
-- LowCardinality
--   Applied to columns with bounded distinct values (event_type ≤ 9,
--   device ≤ 4, payment_mode ≤ 7, category ≤ ~100).  LowCardinality uses a
--   dictionary encoding that reduces storage by 3–5× and speeds up GROUP BY
--   and filter operations on these columns significantly.
--   LowCardinality(Nullable(T)) is supported since ClickHouse 21.x.
--
-- CODEC choices
--   DoubleDelta + ZSTD  — DateTime64 columns: timestamps are nearly monotonic
--                         so delta-of-deltas compresses to near zero.
--   T64 + ZSTD          — integer metrics (quantity): packs 64 values into
--                         64-byte SIMD-friendly blocks before ZSTD pass.
--   Gorilla + ZSTD      — Float64 (price): Gorilla XOR-delta compression is
--                         purpose-built for floating-point time-series data.
--   ZSTD(1)             — everything else: good ratio, fast decompression.
-- =============================================================================

CREATE DATABASE IF NOT EXISTS ecom;

CREATE TABLE IF NOT EXISTS ecom.events_local
(
    -- ------------------------------------------------------------------
    -- Identity
    -- ------------------------------------------------------------------

    -- Globally unique event ID produced by the upstream microservice.
    -- Used as the final component of the ORDER BY key so ReplacingMergeTree
    -- can identify duplicates from Kafka at-least-once redelivery.
    event_id        String                  CODEC(ZSTD(1)),

    -- Wall-clock time of the event in milliseconds precision (UTC).
    -- Avro logical type: timestamp-millis (long).
    -- This column drives partitioning, the primary index range scan,
    -- and the TTL expression — it must never be NULL.
    event_time      DateTime64(3, 'UTC')    CODEC(DoubleDelta, ZSTD(1)),

    -- ------------------------------------------------------------------
    -- Session / user
    -- ------------------------------------------------------------------

    -- Authenticated user ID.  NULL for anonymous sessions.
    user_id         Nullable(String)        CODEC(ZSTD(1)),

    -- Browser / app session.  Always present; generated client-side.
    session_id      String                  CODEC(ZSTD(1)),

    -- ------------------------------------------------------------------
    -- Product
    -- ------------------------------------------------------------------

    -- SKU / product identifier.  NULL for non-product events
    -- (e.g. PAYMENT_FAILED at the payment gateway level).
    product_id      Nullable(String)        CODEC(ZSTD(1)),

    -- Product category, lowercased + trimmed by Flink before writing.
    -- NULL for events that have no associated product (e.g. some REFUND flows).
    -- LowCardinality: O(10–100) distinct categories; dictionary encoding
    -- is especially effective here.
    category        LowCardinality(Nullable(String))   CODEC(ZSTD(1)),

    -- ------------------------------------------------------------------
    -- Event classification
    -- ------------------------------------------------------------------

    -- One of: VIEW | ADD_TO_CART | REMOVE_CART | WISHLIST | ORDER_CREATED
    --         | PAYMENT_SUCCESS | PAYMENT_FAILED | ORDER_CANCELLED | REFUND
    -- Avro enum maps to String; Flink validates the value before writing.
    -- Placed FIRST in ORDER BY — the most selective filter in OLAP queries.
    event_type      LowCardinality(String)  CODEC(ZSTD(1)),

    -- ------------------------------------------------------------------
    -- Commerce metrics
    -- ------------------------------------------------------------------

    -- Item count.  NULL for non-purchase events (VIEW, WISHLIST, etc.).
    -- Avro int (signed 32-bit) mapped to UInt32; Flink validates >= 0.
    quantity        Nullable(UInt32)        CODEC(T64, ZSTD(1)),

    -- Unit price in tenant currency.  NULL for non-purchase events.
    -- Avro double; Gorilla codec is purpose-built for float time-series.
    price           Nullable(Float64)       CODEC(Gorilla, ZSTD(1)),

    -- Payment method.  NULL for all non-payment events.
    -- Possible values: credit_card | debit_card | upi | wallet
    --                  | net_banking | emi | cash_on_delivery
    payment_mode    LowCardinality(Nullable(String))   CODEC(ZSTD(1)),

    -- ------------------------------------------------------------------
    -- Client context
    -- ------------------------------------------------------------------

    -- Canonical device class normalised by Flink: web | android | ios | other
    device          LowCardinality(String)  CODEC(ZSTD(1)),

    -- Raw client IP.  Kept for geo-enrichment queries; not used in joins.
    -- In a GDPR context, apply a hash or mask transformation before storing.
    ip              String                  CODEC(ZSTD(1)),

    -- ------------------------------------------------------------------
    -- Pipeline metadata
    -- ------------------------------------------------------------------

    -- Timestamp of when this row was written by the Flink sink.
    -- Serves as the ReplacingMergeTree version column: if the same event_id
    -- is redelivered, the row written later (higher ingest_ts) wins.
    ingest_ts       DateTime64(3, 'UTC')    DEFAULT now64(3)
                                            CODEC(DoubleDelta, ZSTD(1))
)
ENGINE = ReplacingMergeTree(ingest_ts)

-- Daily partitions.  A partition is the minimum unit the TTL engine can drop
-- atomically without rewriting data, so coarser partitions (monthly) would
-- delay TTL cleanup; finer partitions (hourly) would create too many small
-- parts.  Daily is the sweet spot for this event volume.
PARTITION BY toYYYYMMDD(event_time)

-- Primary index order.  The first three columns form the common query
-- predicate pattern: event_type → category → event_time range.
-- event_id is appended solely to make every key unique for deduplication.
ORDER BY (event_type, category, event_time, event_id)

-- Drop rows (and eventually whole parts) older than 2 years.
-- ttl_only_drop_parts = 1 (see SETTINGS below) ensures the engine waits
-- until every row in a part has expired before dropping it, which avoids
-- rewriting parts and is far more I/O-efficient than row-level TTL deletes.
TTL event_time + INTERVAL 2 YEAR DELETE

SETTINGS
    -- Granule size for the sparse primary index.  8192 rows per granule is
    -- the ClickHouse default and is appropriate for this schema width.
    index_granularity = 8192,

    -- Always use wide (per-column file) storage format regardless of part
    -- size.  Compact format (single file) is only faster for tiny inserts;
    -- we always write in bulk from Flink so wide is always better here.
    min_rows_for_wide_part = 0,

    -- Run TTL-triggered merges at most once per day.  TTL cleanup is a
    -- background operation; running it too frequently wastes I/O.
    merge_with_ttl_timeout = 86400,

    -- Drop entire parts that are fully expired rather than rewriting them
    -- with individual rows deleted.  Requires all rows in the part to have
    -- exceeded the TTL, which is guaranteed once the daily partition ages out.
    ttl_only_drop_parts = 1,

    -- Allow deduplication to occur across consecutive merges, not just within
    -- a single merge step.  Required when the same event_id can arrive in
    -- different Flink checkpoints (i.e. across part boundaries).
    replicated_deduplication_window = 0;


-- =============================================================================
-- Verification query (run after startup to confirm the table is ready)
-- =============================================================================
-- SELECT
--     name,
--     engine,
--     partition_key,
--     sorting_key,
--     primary_key,
--     ttl_expression,
--     total_rows,
--     formatReadableSize(total_bytes) AS size_on_disk
-- FROM system.tables
-- WHERE database = 'ecom' AND name = 'events_local';
