-- ============================================================================
-- DRUID SQL QUERIES FOR E-COMMERCE ANALYTICS
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. REAL-TIME SALES DASHBOARD (Last 24 hours)
-- ----------------------------------------------------------------------------

-- Total revenue in last 24 hours
SELECT 
    SUM(total_revenue) as total_revenue_24h,
    SUM(event_count) as total_events_24h,
    SUM(unique_users) as active_users_24h
FROM ecommerce_events_realtime
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '24' HOUR
    AND event_type = 'payment_success';

-- Revenue by hour (last 24 hours)
SELECT 
    TIME_FLOOR(__time, 'PT1H') as hour,
    SUM(total_revenue) as revenue,
    SUM(event_count) as successful_payments,
    AVG(total_revenue / NULLIF(event_count, 0)) as avg_order_value
FROM ecommerce_events_realtime
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '24' HOUR
    AND event_type = 'payment_success'
GROUP BY 1
ORDER BY 1 DESC;

-- ----------------------------------------------------------------------------
-- 2. PRODUCT PERFORMANCE BY CATEGORY
-- ----------------------------------------------------------------------------

-- Top performing categories by revenue (last 1 hour)
SELECT 
    category,
    SUM(total_revenue) as revenue,
    SUM(event_count) as transactions,
    SUM(unique_users) as unique_customers,
    SUM(unique_products) as products_sold,
    AVG(total_revenue / NULLIF(event_count, 0)) as avg_transaction_value
FROM ecommerce_events_realtime
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '1' HOUR
    AND event_type = 'payment_success'
GROUP BY category
ORDER BY revenue DESC
LIMIT 10;

-- Category performance comparison (current vs previous hour)
WITH current_hour AS (
    SELECT 
        category,
        SUM(total_revenue) as revenue
    FROM ecommerce_events_realtime
    WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'PT1H')
        AND event_type = 'payment_success'
    GROUP BY category
),
previous_hour AS (
    SELECT 
        category,
        SUM(total_revenue) as revenue
    FROM ecommerce_events_realtime
    WHERE __time >= TIME_FLOOR(CURRENT_TIMESTAMP, 'PT1H') - INTERVAL '1' HOUR
        AND __time < TIME_FLOOR(CURRENT_TIMESTAMP, 'PT1H')
        AND event_type = 'payment_success'
    GROUP BY category
)
SELECT 
    COALESCE(c.category, p.category) as category,
    COALESCE(c.revenue, 0) as current_revenue,
    COALESCE(p.revenue, 0) as previous_revenue,
    (COALESCE(c.revenue, 0) - COALESCE(p.revenue, 0)) / NULLIF(p.revenue, 0) * 100 as growth_pct
FROM current_hour c
FULL OUTER JOIN previous_hour p ON c.category = p.category
ORDER BY current_revenue DESC;

-- ----------------------------------------------------------------------------
-- 3. PAYMENT METHOD ANALYSIS
-- ----------------------------------------------------------------------------

-- Payment method distribution (last 6 hours)
SELECT 
    payment_mode,
    SUM(event_count) as payment_count,
    SUM(total_revenue) as total_revenue,
    AVG(total_revenue / NULLIF(event_count, 0)) as avg_transaction_value,
    SUM(event_count) * 100.0 / SUM(SUM(event_count)) OVER () as percentage
FROM ecommerce_events_realtime
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '6' HOUR
    AND event_type = 'payment_success'
GROUP BY payment_mode
ORDER BY payment_count DESC;

-- Payment success rate by method (requires both success and failure events)
WITH payment_attempts AS (
    SELECT 
        payment_mode,
        event_type,
        SUM(event_count) as count
    FROM ecommerce_events_realtime
    WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '24' HOUR
        AND event_type IN ('payment_success', 'payment_failed')
    GROUP BY payment_mode, event_type
)
SELECT 
    payment_mode,
    SUM(CASE WHEN event_type = 'payment_success' THEN count ELSE 0 END) as successful,
    SUM(CASE WHEN event_type = 'payment_failed' THEN count ELSE 0 END) as failed,
    SUM(count) as total_attempts,
    SUM(CASE WHEN event_type = 'payment_success' THEN count ELSE 0 END) * 100.0 / 
        NULLIF(SUM(count), 0) as success_rate_pct
FROM payment_attempts
GROUP BY payment_mode
ORDER BY success_rate_pct DESC;

-- ----------------------------------------------------------------------------
-- 4. FUNNEL ANALYSIS (View -> Cart -> Order -> Payment)
-- ----------------------------------------------------------------------------

-- Conversion funnel (last 24 hours)
WITH funnel_data AS (
    SELECT 
        event_type,
        SUM(event_count) as event_count,
        SUM(unique_users) as unique_users
    FROM ecommerce_events_realtime
    WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '24' HOUR
        AND event_type IN ('view_product', 'add_to_cart', 'order_created', 'payment_success')
    GROUP BY event_type
)
SELECT 
    event_type,
    event_count,
    unique_users,
    event_count * 100.0 / FIRST_VALUE(event_count) OVER (ORDER BY 
        CASE event_type
            WHEN 'view_product' THEN 1
            WHEN 'add_to_cart' THEN 2
            WHEN 'order_created' THEN 3
            WHEN 'payment_success' THEN 4
        END) as conversion_from_view_pct,
    LAG(event_count) OVER (ORDER BY 
        CASE event_type
            WHEN 'view_product' THEN 1
            WHEN 'add_to_cart' THEN 2
            WHEN 'order_created' THEN 3
            WHEN 'payment_success' THEN 4
        END) as previous_step_count,
    event_count * 100.0 / NULLIF(LAG(event_count) OVER (ORDER BY 
        CASE event_type
            WHEN 'view_product' THEN 1
            WHEN 'add_to_cart' THEN 2
            WHEN 'order_created' THEN 3
            WHEN 'payment_success' THEN 4
        END), 0) as step_conversion_pct
FROM funnel_data
ORDER BY CASE event_type
    WHEN 'view_product' THEN 1
    WHEN 'add_to_cart' THEN 2
    WHEN 'order_created' THEN 3
    WHEN 'payment_success' THEN 4
END;

-- ----------------------------------------------------------------------------
-- 5. ANOMALY DETECTION - Sudden Spikes/Drops
-- ----------------------------------------------------------------------------

-- Detect revenue anomalies (comparing 5-min windows)
WITH revenue_by_window AS (
    SELECT 
        TIME_FLOOR(__time, 'PT5M') as time_window,
        SUM(total_revenue) as revenue
    FROM ecommerce_events_realtime
    WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '2' HOUR
        AND event_type = 'payment_success'
    GROUP BY 1
),
stats AS (
    SELECT 
        AVG(revenue) as avg_revenue,
        STDDEV(revenue) as stddev_revenue
    FROM revenue_by_window
)
SELECT 
    r.time_window,
    r.revenue,
    s.avg_revenue,
    s.stddev_revenue,
    (r.revenue - s.avg_revenue) / NULLIF(s.stddev_revenue, 0) as z_score,
    CASE 
        WHEN ABS((r.revenue - s.avg_revenue) / NULLIF(s.stddev_revenue, 0)) > 2 THEN 'ANOMALY'
        ELSE 'NORMAL'
    END as status
FROM revenue_by_window r
CROSS JOIN stats s
ORDER BY r.time_window DESC
LIMIT 20;

-- ----------------------------------------------------------------------------
-- 6. CART ABANDONMENT METRICS
-- ----------------------------------------------------------------------------

-- Cart actions summary (last 4 hours)
SELECT 
    TIME_FLOOR(__time, 'PT1H') as hour,
    SUM(CASE WHEN event_type = 'add_to_cart' THEN event_count ELSE 0 END) as cart_additions,
    SUM(CASE WHEN event_type = 'remove_from_cart' THEN event_count ELSE 0 END) as cart_removals,
    SUM(CASE WHEN event_type = 'order_created' THEN event_count ELSE 0 END) as orders_created,
    SUM(CASE WHEN event_type = 'add_to_cart' THEN event_count ELSE 0 END) -
    SUM(CASE WHEN event_type = 'order_created' THEN event_count ELSE 0 END) as potential_abandoned_carts
FROM ecommerce_events_realtime
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '4' HOUR
    AND event_type IN ('add_to_cart', 'remove_from_cart', 'order_created')
GROUP BY 1
ORDER BY 1 DESC;

-- ----------------------------------------------------------------------------
-- 7. REFUND AND CANCELLATION ANALYSIS
-- ----------------------------------------------------------------------------

-- Refund metrics (last 7 days)
SELECT 
    DATE_TRUNC('day', __time) as day,
    category,
    SUM(CASE WHEN event_type = 'refund_processed' THEN event_count ELSE 0 END) as refund_count,
    SUM(CASE WHEN event_type = 'refund_processed' THEN total_revenue ELSE 0 END) as refund_amount,
    SUM(CASE WHEN event_type = 'payment_success' THEN event_count ELSE 0 END) as successful_orders,
    SUM(CASE WHEN event_type = 'refund_processed' THEN event_count ELSE 0 END) * 100.0 /
        NULLIF(SUM(CASE WHEN event_type = 'payment_success' THEN event_count ELSE 0 END), 0) as refund_rate_pct
FROM ecommerce_events_realtime
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
    AND event_type IN ('payment_success', 'refund_processed')
GROUP BY 1, 2
ORDER BY 1 DESC, refund_amount DESC;

-- ----------------------------------------------------------------------------
-- 8. TOP METRICS SUMMARY (Real-Time Dashboard)
-- ----------------------------------------------------------------------------

-- Real-time KPIs (last 5 minutes)
SELECT 
    SUM(CASE WHEN event_type = 'view_product' THEN event_count ELSE 0 END) as product_views,
    SUM(CASE WHEN event_type = 'add_to_cart' THEN event_count ELSE 0 END) as cart_additions,
    SUM(CASE WHEN event_type = 'order_created' THEN event_count ELSE 0 END) as orders,
    SUM(CASE WHEN event_type = 'payment_success' THEN event_count ELSE 0 END) as successful_payments,
    SUM(CASE WHEN event_type = 'payment_failed' THEN event_count ELSE 0 END) as failed_payments,
    SUM(CASE WHEN event_type = 'payment_success' THEN total_revenue ELSE 0 END) as total_revenue,
    SUM(unique_users) as active_users,
    SUM(CASE WHEN event_type = 'payment_success' THEN total_revenue ELSE 0 END) /
        NULLIF(SUM(CASE WHEN event_type = 'payment_success' THEN event_count ELSE 0 END), 0) as avg_order_value
FROM ecommerce_events_realtime
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '5' MINUTE;

-- ----------------------------------------------------------------------------
-- 9. TIME-SERIES ANALYSIS
-- ----------------------------------------------------------------------------

-- Revenue trend (5-minute granularity, last 2 hours)
SELECT 
    TIME_FLOOR(__time, 'PT5M') as time_bucket,
    SUM(total_revenue) as revenue,
    SUM(event_count) as order_count,
    SUM(unique_users) as unique_customers,
    AVG(total_revenue / NULLIF(event_count, 0)) as avg_order_value
FROM ecommerce_events_realtime
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '2' HOUR
    AND event_type = 'payment_success'
GROUP BY 1
ORDER BY 1;

-- ----------------------------------------------------------------------------
-- 10. CATEGORY DRILL-DOWN
-- ----------------------------------------------------------------------------

-- Detailed category metrics (last hour)
SELECT 
    category,
    event_type,
    SUM(event_count) as event_count,
    SUM(unique_users) as unique_users,
    SUM(total_quantity) as total_quantity,
    SUM(total_revenue) as total_revenue,
    AVG(total_revenue / NULLIF(event_count, 0)) as avg_value_per_event
FROM ecommerce_events_realtime
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '1' HOUR
GROUP BY category, event_type
ORDER BY category, total_revenue DESC;

-- ============================================================================
-- MATERIALIZED VIEWS (for frequently used queries)
-- ============================================================================

-- Note: These would be created as Druid datasources with different rollup configurations

-- Example: Hourly revenue by category (pre-aggregated)
-- This would be a separate ingestion spec with hourly segmentGranularity
