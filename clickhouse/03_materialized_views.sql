-- =============================================================================
-- Step 4.4 — ClickHouse Aggregating Materialized Views
-- =============================================================================
--
-- Execution:
--   docker exec -i clickhouse clickhouse-client --multiquery \
--     < clickhouse/03_materialized_views.sql
--
-- DESIGN PATTERN: Two-table MV (target table + materialized view)
-- ────────────────────────────────────────────────────────────────
-- ClickHouse MVs cannot change their schema after creation.  Separating the
-- target table from the MV definition lets you:
--   • ALTER the target table independently (add columns, change codecs).
--   • Backfill by INSERT INTO target SELECT … FROM events_local FINAL WHERE …
--   • Attach multiple MVs to the same target (e.g. one for real-time, one for
--     a reprocessed historical batch).
--
-- AGGREGATE STATE vs MERGE — how AggregatingMergeTree works
-- ───────────────────────────────────────────────────────────
-- INSERT path (MV writes):
--   *State() functions serialize a partial aggregate into a binary blob.
--   Multiple blobs for the same ORDER BY key accumulate in parts until a merge.
--
-- QUERY path (you read):
--   *Merge() functions deserialize and combine all partial states for a key,
--   producing the final aggregate value.  A GROUP BY is required because
--   parts may not have merged yet.
--
-- DUPLICATE CAVEAT — ReplacingMergeTree source
-- ─────────────────────────────────────────────
-- MVs on events_local fire on every INSERT block, not on the FINAL
-- (deduplicated) view.  Kafka at-least-once delivery can insert the same
-- event_id twice before ReplacingMergeTree's background merge deduplicates it.
-- Result: aggregate counts may be inflated by ~0–0.5% until the next merge.
-- For live dashboards refreshing every 10–60 s this is acceptable.
-- For exact offline reporting, backfill from:
--   INSERT INTO ecom.kpi_1min SELECT … FROM ecom.events_local FINAL WHERE …
-- =============================================================================

-- =============================================================================
-- 1. ONE-MINUTE KPI AGGREGATE
-- =============================================================================
--
-- Use case: live ops dashboard panels showing events/revenue/unique-users per
-- minute.  Refresh every 10–30 s.  Kept for 30 days (raw events cover 2 years).

-- 1a. Target table ────────────────────────────────────────────────────────────
--
-- AggregateFunction type declarations must exactly match the *State() function
-- signatures used in the MV SELECT.  Mismatches cause silent type errors or
-- CANNOT_PARSE_INPUT_ASSERTION_VIOLATED on INSERT.
--
-- Key type decisions:
--
--   event_count  AggregateFunction(count)
--     countState() needs no type argument; the counter is always UInt64.
--
--   qty  AggregateFunction(sum, Float64)
--     Source column is Nullable(UInt32).  The MV coerces it to Float64 with
--     toFloat64(coalesce(quantity, 0)) so that:
--       (a) NULLs contribute 0 rather than making the whole sum NULL
--       (b) The type is consistent with the revenue column (both Float64),
--           making downstream division (avg items per order) type-safe.
--
--   revenue  AggregateFunction(sum, Float64)
--     price (Nullable Float64) × quantity (Nullable UInt32).  Both NULLs are
--     coalesced to 0 before multiplication.  NULL × anything = NULL in SQL, so
--     coalescing both operands is necessary to avoid losing revenue rows.
--
--   unique_users  AggregateFunction(uniqExact, Nullable(String))
--     uniqExact gives exact distinct counts (not HLL approximation).  For a
--     1-minute window at dev scale this is fine.  On >100 M events/day,
--     switch to AggregateFunction(uniq, String) (HLL with ±2% error).
--     uniqExact automatically skips NULL values, so anonymous sessions
--     (user_id IS NULL) are excluded — this is the desired behaviour.

CREATE TABLE IF NOT EXISTS ecom.kpi_1min
(
    -- Minute bucket — left-closed interval [window_start, window_start + 1min)
    window_start    DateTime('UTC')                     CODEC(DoubleDelta, ZSTD(1)),
    category        LowCardinality(Nullable(String)),
    event_type      LowCardinality(String),

    -- Partial aggregate states
    event_count     AggregateFunction(count),
    qty             AggregateFunction(sum, Float64),
    revenue         AggregateFunction(sum, Float64),
    unique_users    AggregateFunction(uniqExact, Nullable(String))
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMMDD(window_start)
-- Leftmost key (category, event_type) enables fast WHERE category = 'electronics'
-- AND event_type = 'PAYMENT_SUCCESS' panel queries.
-- window_start is rightmost: range scans happen inside a fixed type+category.
ORDER BY (category, event_type, window_start)
-- 30 days: live dashboards rarely need per-minute resolution older than a month.
-- For longer-range trends the 5-minute funnel MV below is more appropriate.
TTL toDate(window_start) + INTERVAL 30 DAY DELETE
SETTINGS
    index_granularity   = 8192,
    ttl_only_drop_parts = 1,
    allow_nullable_key  = 1;


-- 1b. Materialized view ───────────────────────────────────────────────────────
--
-- Triggered on every INSERT into ecom.events_local (which is itself written by
-- the events_mv Kafka materialized view, so the chain is:
--   Kafka topic → events_kafka → events_mv → events_local → kpi_1min_mv → kpi_1min
--
-- GROUP BY is required inside an MV that writes to AggregatingMergeTree so that
-- one INSERT block produces one row per (window_start, category, event_type)
-- rather than one row per raw event.  Without GROUP BY, each row would be
-- stored as a separate partial state, ballooning storage and merge work.

CREATE MATERIALIZED VIEW IF NOT EXISTS ecom.kpi_1min_mv
TO ecom.kpi_1min
AS
SELECT
    -- Truncate millisecond precision to a 1-minute aligned bucket.
    -- toStartOfMinute handles DateTime64 correctly in ClickHouse 24.x.
    toStartOfMinute(event_time)                                 AS window_start,
    category,
    event_type,

    countState()                                                AS event_count,

    -- Nullable(UInt32) → Float64:  NULLs become 0, avoids NULL-propagation
    -- through the multiplication in the revenue expression below.
    sumState(toFloat64(coalesce(quantity, 0)))                  AS qty,

    -- Revenue = price × quantity.  Both operands are nullable:
    --   • price IS NULL for non-purchase events (VIEW, WISHLIST, etc.)
    --   • quantity IS NULL for the same events
    -- Coalescing both to 0 means non-purchase rows contribute 0 revenue,
    -- which is correct.  Payment events always have non-null price + quantity
    -- after Flink's ValidateAndClean operator.
    sumState(
        coalesce(price, 0.0) * toFloat64(coalesce(quantity, 0))
    )                                                           AS revenue,

    -- Count distinct authenticated users.  NULL user_id (anonymous sessions)
    -- are skipped automatically by uniqExact.
    uniqExactState(user_id)                                     AS unique_users

FROM ecom.events_local
GROUP BY
    window_start,
    category,
    event_type;


-- =============================================================================
-- 2. FIVE-MINUTE FUNNEL AGGREGATE
-- =============================================================================
--
-- Use case: conversion funnel panel — VIEW → ADD_TO_CART → ORDER_CREATED →
-- PAYMENT_SUCCESS rates per category over 5-minute windows.
-- Kept for 90 days; 5-minute resolution is appropriate for trend dashboards
-- spanning hours/days rather than the last 30 minutes.

-- 2a. Target table ────────────────────────────────────────────────────────────
--
-- countIfState(predicate) is equivalent to count() WHERE predicate, stored as
-- a partial state.  The AggregateFunction declaration is:
--   AggregateFunction(countIf, UInt8)
-- where UInt8 represents the boolean predicate argument (0 or 1).
--
-- sumIfState(value, predicate) is:
--   AggregateFunction(sumIf, Float64, UInt8)
-- where Float64 is the value type and UInt8 is the predicate type.
--
-- Both If variants are more storage-efficient than filtering in a WHERE clause
-- because they can aggregate all funnel steps in a single pass over the data.

CREATE TABLE IF NOT EXISTS ecom.funnel_5min
(
    -- Five-minute bucket: [window_start, window_start + 5min)
    window_start    DateTime('UTC')                     CODEC(DoubleDelta, ZSTD(1)),
    category        LowCardinality(Nullable(String)),

    -- Funnel step counters — each is a countIf partial state
    views           AggregateFunction(countIf, UInt8),
    carts           AggregateFunction(countIf, UInt8),
    orders          AggregateFunction(countIf, UInt8),
    payments        AggregateFunction(countIf, UInt8),

    -- Revenue from completed payments only.
    -- sumIf(value, condition): UInt8 is the boolean predicate type.
    revenue         AggregateFunction(sumIf, Float64, UInt8)
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMMDD(window_start)
-- category first: the funnel panel always scopes to one category.
-- window_start second: time range scan within a category.
ORDER BY (category, window_start)
TTL toDate(window_start) + INTERVAL 90 DAY DELETE
SETTINGS
    index_granularity   = 8192,
    ttl_only_drop_parts = 1,
    allow_nullable_key  = 1;


-- 2b. Materialized view ───────────────────────────────────────────────────────
--
-- Single-pass over each INSERT block: all five funnel steps and revenue are
-- computed simultaneously.  This is more efficient than five separate MVs
-- (five separate INSERT chains per block).
--
-- toStartOfFiveMinute() is a built-in ClickHouse function that floors to the
-- nearest 5-minute boundary (00, 05, 10, … 55 of each hour).

CREATE MATERIALIZED VIEW IF NOT EXISTS ecom.funnel_5min_mv
TO ecom.funnel_5min
AS
SELECT
    toStartOfFiveMinute(event_time)                             AS window_start,
    category,

    -- Each countIfState call makes one pass and sets its internal counter to
    -- 1 for matching rows and 0 for non-matching rows — no subquery needed.
    countIfState(event_type = 'VIEW')                           AS views,
    countIfState(event_type = 'ADD_TO_CART')                    AS carts,
    countIfState(event_type = 'ORDER_CREATED')                  AS orders,
    countIfState(event_type = 'PAYMENT_SUCCESS')                AS payments,

    -- Revenue: only sum price×qty for payment success rows.
    -- toUInt8() converts the boolean predicate to UInt8 (required by sumIf).
    -- Coalesce handles Nullable operands — same rationale as kpi_1min_mv.
    sumIfState(
        coalesce(price, 0.0) * toFloat64(coalesce(quantity, 0)),
        toUInt8(event_type = 'PAYMENT_SUCCESS')
    )                                                           AS revenue

FROM ecom.events_local
GROUP BY
    window_start,
    category;


-- =============================================================================
-- VALIDATION QUERIES
-- =============================================================================
--
-- 1. Confirm all four objects exist:
--
--    SELECT name, engine
--    FROM system.tables
--    WHERE database = 'ecom'
--      AND name IN ('kpi_1min', 'kpi_1min_mv', 'funnel_5min', 'funnel_5min_mv')
--    ORDER BY name;
--
-- 2. Live KPI panel query — merge partial states (run after ≥1 minute of data):
--
--    SELECT
--        window_start,
--        category,
--        event_type,
--        countMerge(event_count)     AS events,
--        sumMerge(qty)               AS total_qty,
--        round(sumMerge(revenue), 2) AS total_revenue,
--        uniqExactMerge(unique_users) AS unique_users
--    FROM ecom.kpi_1min
--    WHERE window_start >= toStartOfMinute(now()) - INTERVAL 1 HOUR
--    GROUP BY window_start, category, event_type
--    ORDER BY window_start DESC, total_revenue DESC
--    LIMIT 50;
--
-- 3. Funnel conversion rates — run after ≥5 minutes of data:
--
--    SELECT
--        window_start,
--        category,
--        countIfMerge(views)         AS views,
--        countIfMerge(carts)         AS carts,
--        countIfMerge(orders)        AS orders,
--        countIfMerge(payments)      AS payments,
--        round(sumIfMerge(revenue), 2) AS revenue,
--        -- Conversion rates
--        round(100.0 * countIfMerge(carts)    / nullIf(countIfMerge(views),    0), 2) AS view_to_cart_pct,
--        round(100.0 * countIfMerge(orders)   / nullIf(countIfMerge(carts),   0), 2) AS cart_to_order_pct,
--        round(100.0 * countIfMerge(payments) / nullIf(countIfMerge(orders),  0), 2) AS order_to_pay_pct,
--        round(100.0 * countIfMerge(payments) / nullIf(countIfMerge(views),   0), 2) AS overall_cvr_pct
--    FROM ecom.funnel_5min
--    WHERE window_start >= toStartOfFiveMinute(now()) - INTERVAL 6 HOUR
--    GROUP BY window_start, category
--    ORDER BY window_start DESC, revenue DESC;
--
-- 4. Backfill from existing raw events (bypasses the MV trigger):
--    Run this if the MVs are created AFTER events_local already has data.
--
--    INSERT INTO ecom.kpi_1min
--    SELECT
--        toStartOfMinute(event_time)                                 AS window_start,
--        category,
--        event_type,
--        countState()                                                AS event_count,
--        sumState(toFloat64(coalesce(quantity, 0)))                  AS qty,
--        sumState(coalesce(price, 0.0) * toFloat64(coalesce(quantity, 0))) AS revenue,
--        uniqExactState(user_id)                                     AS unique_users
--    FROM ecom.events_local FINAL
--    GROUP BY window_start, category, event_type;
--
--    INSERT INTO ecom.funnel_5min
--    SELECT
--        toStartOfFiveMinute(event_time)                             AS window_start,
--        category,
--        countIfState(event_type = 'VIEW')                           AS views,
--        countIfState(event_type = 'ADD_TO_CART')                    AS carts,
--        countIfState(event_type = 'ORDER_CREATED')                  AS orders,
--        countIfState(event_type = 'PAYMENT_SUCCESS')                AS payments,
--        sumIfState(
--            coalesce(price, 0.0) * toFloat64(coalesce(quantity, 0)),
--            toUInt8(event_type = 'PAYMENT_SUCCESS')
--        )                                                           AS revenue
--    FROM ecom.events_local FINAL
--    GROUP BY window_start, category;
