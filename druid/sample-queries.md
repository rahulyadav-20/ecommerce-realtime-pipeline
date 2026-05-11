# Druid SQL Query Cookbook — `ecommerce_events`

> **Schema reference**
>
> | Column | Type | Notes |
> |---|---|---|
> | `__time` | TIMESTAMP | Floored to 1-minute bucket (rollup queryGranularity) |
> | `event_type` | STRING dim | `VIEW`, `ADD_TO_CART`, `REMOVE_CART`, `WISHLIST`, `ORDER_CREATED`, `PAYMENT_SUCCESS`, `PAYMENT_FAILED`, `ORDER_CANCELLED`, `REFUND` |
> | `category` | STRING dim | Product category (lowercased) |
> | `payment_mode` | STRING dim | `credit_card`, `debit_card`, `upi`, `wallet`, `net_banking`, `emi`, `cash_on_delivery` — NULL for non-payment events |
> | `device` | STRING dim | `web`, `android`, `ios`, `other` |
> | `events` | LONG metric | Pre-aggregated count of raw events in this 1-minute rollup bucket |
> | `qty` | LONG metric | Pre-aggregated sum of `quantity` |
> | `revenue` | DOUBLE metric | Pre-aggregated sum of `price` |
> | `uniq_users` | HLL SKETCH | HyperLogLog sketch of `user_id`s — use `APPROX_COUNT_DISTINCT_DS_HLL()` |
>
> **Critical:** Because rollup is enabled, `COUNT(*)` counts rollup *buckets* (not raw events).
> Always use `SUM(events)` to count actual events.

---

## Query 1 — Revenue by Category (Last 24 Hours)

**Dashboard context:** Revenue leaderboard panel on the live ops dashboard.
Refreshed every 5 minutes; used by the merchandising team to spot underperforming
categories and trigger promotions in real time.

```sql
SELECT
  COALESCE(category, '(uncategorised)') AS category,
  SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN events  ELSE 0 END) AS paid_orders,
  ROUND(
    SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN revenue ELSE 0 END),
    2
  )                                                                        AS total_revenue,
  ROUND(
    SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN revenue ELSE 0 END)
    / NULLIF(SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN events ELSE 0 END), 0),
    2
  )                                                                        AS avg_order_value,
  APPROX_COUNT_DISTINCT_DS_HLL(uniq_users)                                AS approx_buyers
FROM ecommerce_events
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '24' HOUR
GROUP BY COALESCE(category, '(uncategorised)')
ORDER BY total_revenue DESC
```

**Expected output:**

| category | paid_orders | total_revenue | avg_order_value | approx_buyers |
|---|---|---|---|---|
| electronics | 1 240 | 28 642 350.00 | 23 098.67 | 1 187 |
| fashion | 3 810 | 9 204 180.00 | 2 416.32 | 3 640 |
| home_kitchen | 890 | 6 174 320.00 | 6 936.32 | 851 |
| … | … | … | … | … |

---

## Query 2 — Top 10 Event Types by Count (Last Hour)

**Dashboard context:** Pipeline health panel. Shows the event mix arriving in the
last 60 minutes. A sudden drop in VIEW events or spike in PAYMENT_FAILED events
triggers an on-call alert.

```sql
SELECT
  event_type,
  SUM(events)                                                         AS event_count,
  ROUND(
    100.0 * SUM(events)
    / SUM(SUM(events)) OVER (),
    2
  )                                                                    AS pct_of_total,
  ROUND(SUM(events) / 60.0, 1)                                        AS events_per_minute
FROM ecommerce_events
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '1' HOUR
GROUP BY event_type
ORDER BY event_count DESC
LIMIT 10
```

**Expected output:**

| event_type | event_count | pct_of_total | events_per_minute |
|---|---|---|---|
| VIEW | 72 480 | 54.3 | 1 208.0 |
| ADD_TO_CART | 24 300 | 18.2 | 405.0 |
| WISHLIST | 11 040 | 8.3 | 184.0 |
| ORDER_CREATED | 9 120 | 6.8 | 152.0 |
| PAYMENT_SUCCESS | 7 296 | 5.5 | 121.6 |
| … | … | … | … |

---

## Query 3 — Payment Success Rate by Payment Mode (Last 7 Days)

**Dashboard context:** Payments health dashboard, reviewed daily by the payments
engineering team. A success rate below 90% for any mode triggers an investigation
with the payment gateway provider.

```sql
SELECT
  payment_mode,
  SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN events ELSE 0 END) AS successes,
  SUM(CASE WHEN event_type = 'PAYMENT_FAILED'  THEN events ELSE 0 END) AS failures,
  SUM(
    CASE WHEN event_type IN ('PAYMENT_SUCCESS', 'PAYMENT_FAILED')
         THEN events ELSE 0 END
  )                                                                      AS total_attempts,
  ROUND(
    100.0
    * SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN events ELSE 0 END)
    / NULLIF(
        SUM(CASE WHEN event_type IN ('PAYMENT_SUCCESS', 'PAYMENT_FAILED')
                 THEN events ELSE 0 END),
        0
      ),
    2
  )                                                                      AS success_rate_pct,
  ROUND(
    SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN revenue ELSE 0 END),
    2
  )                                                                      AS total_revenue_collected
FROM ecommerce_events
WHERE __time  >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
  AND payment_mode IS NOT NULL
GROUP BY payment_mode
ORDER BY total_attempts DESC
```

**Expected output:**

| payment_mode | successes | failures | total_attempts | success_rate_pct | total_revenue_collected |
|---|---|---|---|---|---|
| upi | 48 320 | 4 120 | 52 440 | 92.14 | 94 821 400.00 |
| credit_card | 41 890 | 5 640 | 47 530 | 88.13 | 186 340 200.00 |
| wallet | 22 140 | 1 820 | 23 960 | 92.40 | 31 204 800.00 |
| … | … | … | … | … | … |

---

## Query 4 — Hourly Event Count Timeseries (Last 3 Days)

**Dashboard context:** Time-series panel in Kibana/Grafana (or Druid's own console)
showing overall pipeline throughput, orders, and payments side-by-side over the
past 72 hours. Used during incident review to correlate traffic dips with
deployment events.

```sql
SELECT
  TIME_FLOOR(__time, 'PT1H')                                                 AS hour_bucket,
  SUM(events)                                                                 AS total_events,
  SUM(CASE WHEN event_type = 'VIEW'            THEN events ELSE 0 END)       AS views,
  SUM(CASE WHEN event_type = 'ADD_TO_CART'     THEN events ELSE 0 END)       AS add_to_carts,
  SUM(CASE WHEN event_type = 'ORDER_CREATED'   THEN events ELSE 0 END)       AS orders_created,
  SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN events ELSE 0 END)       AS payments_succeeded,
  SUM(CASE WHEN event_type = 'PAYMENT_FAILED'  THEN events ELSE 0 END)       AS payments_failed,
  ROUND(
    SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN revenue ELSE 0 END),
    2
  )                                                                            AS hourly_revenue
FROM ecommerce_events
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '3' DAY
GROUP BY TIME_FLOOR(__time, 'PT1H')
ORDER BY hour_bucket ASC
```

**Expected output (sample rows):**

| hour_bucket | total_events | views | add_to_carts | orders_created | payments_succeeded | hourly_revenue |
|---|---|---|---|---|---|---|
| 2026-05-08 00:00:00 | 82 440 | 44 640 | 14 880 | 7 800 | 6 240 | 11 204 880.00 |
| 2026-05-08 01:00:00 | 76 320 | 41 280 | 13 800 | 7 200 | 5 760 | 10 368 000.00 |
| … | … | … | … | … | … | … |

---

## Query 5 — Unique Users per Device Type (Today)

**Dashboard context:** Device split panel on the product analytics dashboard.
Shows how many distinct authenticated users are active today on each device.
Informs A/B test targeting and mobile-app release prioritisation.

> **Note on HLL accuracy:** `APPROX_COUNT_DISTINCT_DS_HLL` has a typical error of
> ±1.6% at lgK=12. For exact counts, replay the raw topic through ClickHouse.

```sql
SELECT
  device,
  SUM(events)                                AS total_events,
  APPROX_COUNT_DISTINCT_DS_HLL(uniq_users)   AS approx_unique_users,
  ROUND(SUM(revenue), 2)                     AS total_revenue,
  ROUND(
    100.0 * SUM(events) / SUM(SUM(events)) OVER (),
    1
  )                                          AS pct_of_traffic
FROM ecommerce_events
WHERE __time >= FLOOR(CURRENT_TIMESTAMP TO DAY)
GROUP BY device
ORDER BY total_events DESC
```

**Expected output:**

| device | total_events | approx_unique_users | total_revenue | pct_of_traffic |
|---|---|---|---|---|
| web | 386 400 | 58 240 | 82 140 200.00 | 45.2 |
| android | 302 400 | 45 360 | 64 218 000.00 | 35.4 |
| ios | 164 160 | 24 624 | 34 874 400.00 | 19.2 |
| other | 1 440 | 216 | 306 000.00 | 0.2 |

---

## Query 6 — Cart Abandonment Rate by Category (Last 24 Hours)

**Dashboard context:** Conversion funnel panel for the category management team.
An abandonment rate above 75% for a category triggers a re-targeting campaign
or price-drop notification workflow in Airflow.

```sql
SELECT
  COALESCE(category, '(uncategorised)')                                AS category,
  SUM(CASE WHEN event_type = 'ADD_TO_CART'     THEN events ELSE 0 END) AS add_to_cart_events,
  SUM(CASE WHEN event_type = 'REMOVE_CART'     THEN events ELSE 0 END) AS remove_cart_events,
  SUM(CASE WHEN event_type = 'ORDER_CREATED'   THEN events ELSE 0 END) AS order_created_events,
  SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN events ELSE 0 END) AS payment_success_events,
  ROUND(
    100.0
    * (1.0 - CAST(
        SUM(CASE WHEN event_type = 'ORDER_CREATED' THEN events ELSE 0 END)
        AS DOUBLE)
      / NULLIF(
          SUM(CASE WHEN event_type = 'ADD_TO_CART' THEN events ELSE 0 END),
          0
        )
    ),
    1
  )                                                                     AS cart_abandonment_pct
FROM ecommerce_events
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '24' HOUR
GROUP BY COALESCE(category, '(uncategorised)')
HAVING SUM(CASE WHEN event_type = 'ADD_TO_CART' THEN events ELSE 0 END) > 50
ORDER BY cart_abandonment_pct DESC
```

**Expected output:**

| category | add_to_cart_events | remove_cart_events | order_created_events | payment_success_events | cart_abandonment_pct |
|---|---|---|---|---|---|
| automotive | 1 240 | 480 | 248 | 198 | 80.0 |
| electronics | 4 800 | 1 680 | 1 200 | 960 | 75.0 |
| fashion | 9 600 | 2 400 | 2 880 | 2 304 | 70.0 |
| food_grocery | 3 360 | 720 | 1 344 | 1 075 | 60.0 |
| … | … | … | … | … | … |

---

## Query 7 — Average Order Value by Hour of Day (Last 7 Days)

**Dashboard context:** Pricing strategy dashboard. Reveals intra-day pricing
patterns — e.g. late-night orders may have higher AOV because users are browsing
premium electronics. Used to time discount campaigns at low-AOV hours.

```sql
SELECT
  EXTRACT(HOUR FROM TIME_FLOOR(__time, 'PT1H'))               AS hour_of_day,
  COUNT(DISTINCT TIME_FLOOR(__time, 'P1D'))                   AS days_observed,
  SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN events  ELSE 0 END) AS total_orders,
  ROUND(
    SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN revenue ELSE 0 END),
    2
  )                                                            AS total_revenue,
  ROUND(
    SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN revenue ELSE 0 END)
    / NULLIF(
        SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN events ELSE 0 END),
        0
      ),
    2
  )                                                            AS avg_order_value,
  ROUND(
    SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN qty ELSE 0 END)
    / NULLIF(
        SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN events ELSE 0 END),
        0
      ),
    2
  )                                                            AS avg_items_per_order
FROM ecommerce_events
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
GROUP BY EXTRACT(HOUR FROM TIME_FLOOR(__time, 'PT1H'))
ORDER BY hour_of_day ASC
```

**Expected output (sample rows):**

| hour_of_day | days_observed | total_orders | total_revenue | avg_order_value | avg_items_per_order |
|---|---|---|---|---|---|
| 0 | 7 | 4 368 | 10 831 824.00 | 2 479.84 | 1.82 |
| 8 | 7 | 7 896 | 19 593 744.00 | 2 481.52 | 1.84 |
| 13 | 7 | 11 760 | 29 174 400.00 | 2 481.00 | 1.83 |
| 22 | 7 | 5 040 | 12 991 680.00 | 2 578.50 | 1.87 |
| … | … | … | … | … | … |

---

## Query 8 — Event Type Distribution (Pie Chart, Last 24 Hours)

**Dashboard context:** Single-stat or pie-chart panel at the top of the main ops
dashboard. Gives an at-a-glance health check: if VIEW % drops below 45% or
PAYMENT_SUCCESS % drops below 5%, the pipeline or upstream service may be degraded.

```sql
SELECT
  event_type,
  SUM(events)                                             AS event_count,
  ROUND(
    100.0 * SUM(events) / SUM(SUM(events)) OVER (),
    2
  )                                                        AS percentage,
  ROUND(
    SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS'
             THEN revenue ELSE 0 END),
    2
  )                                                        AS revenue_contribution
FROM ecommerce_events
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '24' HOUR
GROUP BY event_type
ORDER BY event_count DESC
```

**Expected output:**

| event_type | event_count | percentage | revenue_contribution |
|---|---|---|---|
| VIEW | 1 209 600 | 52.20 | 0.00 |
| ADD_TO_CART | 403 200 | 17.40 | 0.00 |
| WISHLIST | 184 320 | 7.95 | 0.00 |
| ORDER_CREATED | 161 280 | 6.96 | 0.00 |
| PAYMENT_SUCCESS | 129 024 | 5.57 | 285 124 800.00 |
| REMOVE_CART | 119 040 | 5.14 | 0.00 |
| PAYMENT_FAILED | 32 256 | 1.39 | 0.00 |
| ORDER_CANCELLED | 16 128 | 0.70 | 0.00 |
| REFUND | 64 512 | 2.78 | 0.00 |

---

## Query 9 — Peak Traffic Hours (Events per Hour, Last 30 Days)

**Dashboard context:** Infrastructure capacity planning dashboard, reviewed weekly.
The top-3 peak hours determine auto-scaling thresholds for Kafka brokers and
Flink TaskManagers. A shift in peak hours after a new marketing campaign indicates
changed user behaviour.

```sql
SELECT
  EXTRACT(HOUR FROM TIME_FLOOR(__time, 'PT1H'))       AS hour_of_day,
  COUNT(DISTINCT TIME_FLOOR(__time, 'P1D'))           AS days_in_sample,
  SUM(events)                                          AS total_events_30d,
  ROUND(
    SUM(events) * 1.0
    / NULLIF(COUNT(DISTINCT TIME_FLOOR(__time, 'P1D')), 0),
    0
  )                                                    AS avg_events_per_day_at_hour,
  MAX(SUM(events)) OVER (
    ORDER BY SUM(events) DESC
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
  )                                                    AS running_max,
  ROUND(
    100.0 * SUM(events) / SUM(SUM(events)) OVER (),
    2
  )                                                    AS pct_of_daily_traffic
FROM ecommerce_events
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
GROUP BY EXTRACT(HOUR FROM TIME_FLOOR(__time, 'PT1H'))
ORDER BY total_events_30d DESC
```

**Expected output (top 5 rows):**

| hour_of_day | days_in_sample | total_events_30d | avg_events_per_day_at_hour | pct_of_daily_traffic |
|---|---|---|---|---|
| 13 | 30 | 157 248 000 | 5 241 600 | 7.58 |
| 20 | 30 | 152 064 000 | 5 068 800 | 7.33 |
| 12 | 30 | 146 880 000 | 4 896 000 | 7.08 |
| 21 | 30 | 141 696 000 | 4 723 200 | 6.83 |
| 19 | 30 | 136 512 000 | 4 550 400 | 6.58 |

---

## Query 10 — Category Performance Leaderboard (Last 7 Days)

**Dashboard context:** Weekly business review dashboard for the VP of Commerce.
Combines revenue, conversion funnel, and average order value into a single ranked
leaderboard. Categories with high VIEW counts but low conversion trigger content
and UX reviews; categories with high AOV but low volume trigger targeted campaigns.

```sql
WITH
  -- Aggregate all funnel steps and revenue by category
  funnel AS (
    SELECT
      COALESCE(category, '(uncategorised)')                                     AS category,
      SUM(events)                                                                AS total_events,
      SUM(CASE WHEN event_type = 'VIEW'            THEN events ELSE 0 END)      AS views,
      SUM(CASE WHEN event_type = 'ADD_TO_CART'     THEN events ELSE 0 END)      AS add_to_carts,
      SUM(CASE WHEN event_type = 'ORDER_CREATED'   THEN events ELSE 0 END)      AS orders,
      SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN events ELSE 0 END)      AS payments,
      SUM(CASE WHEN event_type = 'REFUND'          THEN events ELSE 0 END)      AS refunds,
      ROUND(
        SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN revenue ELSE 0 END),
        2
      )                                                                           AS gross_revenue,
      APPROX_COUNT_DISTINCT_DS_HLL(uniq_users)                                  AS unique_shoppers
    FROM ecommerce_events
    WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
    GROUP BY COALESCE(category, '(uncategorised)')
  )

SELECT
  category,
  total_events,
  views,
  add_to_carts,
  orders,
  payments,
  unique_shoppers,
  gross_revenue,

  -- View-to-cart conversion
  ROUND(100.0 * add_to_carts / NULLIF(views,      0), 2) AS view_to_cart_pct,

  -- Cart-to-order conversion
  ROUND(100.0 * orders       / NULLIF(add_to_carts,0), 2) AS cart_to_order_pct,

  -- Order-to-payment conversion
  ROUND(100.0 * payments     / NULLIF(orders,     0), 2) AS order_to_payment_pct,

  -- End-to-end funnel: view → paid
  ROUND(100.0 * payments     / NULLIF(views,      0), 2) AS overall_conversion_pct,

  -- Average order value
  ROUND(gross_revenue / NULLIF(payments, 0), 2)          AS avg_order_value,

  -- Refund rate (payments that later became refunds — approximate, same time window)
  ROUND(100.0 * refunds / NULLIF(payments, 0), 2)        AS refund_rate_pct,

  -- Revenue rank (dense_rank not natively in Druid; use ORDER BY below)
  gross_revenue                                           AS _sort_key
FROM funnel
ORDER BY gross_revenue DESC
```

**Expected output:**

| category | views | add_to_carts | orders | payments | unique_shoppers | gross_revenue | view_to_cart_pct | cart_to_order_pct | overall_conversion_pct | avg_order_value | refund_rate_pct |
|---|---|---|---|---|---|---|---|---|---|---|---|
| electronics | 2 419 200 | 725 760 | 338 688 | 270 950 | 382 440 | 6 262 956 650.00 | 30.00 | 46.67 | 11.20 | 23 117.33 | 3.10 |
| fashion | 8 467 200 | 2 540 160 | 1 016 064 | 812 851 | 2 032 128 | 1 964 049 416.00 | 30.00 | 40.00 | 9.60 | 2 416.93 | 2.80 |
| home_kitchen | 1 693 440 | 423 360 | 211 680 | 169 344 | 338 688 | 1 172 786 208.00 | 25.00 | 50.00 | 10.00 | 6 924.48 | 2.50 |
| sports_fitness | 2 116 800 | 634 240 | 253 696 | 202 957 | 507 392 | 2 029 570.00 | 30.00 | 40.00 | 9.59 | 10 000.00 | 3.40 |
| … | … | … | … | … | … | … | … | … | … | … | … |

---

## Quick-reference Cheat Sheet

```sql
-- Count raw events (NOT rollup buckets)
SUM(events)

-- Total revenue from completed payments
SUM(CASE WHEN event_type = 'PAYMENT_SUCCESS' THEN revenue ELSE 0 END)

-- Approximate distinct users (HLL sketch)
APPROX_COUNT_DISTINCT_DS_HLL(uniq_users)

-- Time bucket helpers
TIME_FLOOR(__time, 'PT1M')   -- 1-minute bucket
TIME_FLOOR(__time, 'PT1H')   -- 1-hour  bucket
TIME_FLOOR(__time, 'P1D')    -- 1-day   bucket
FLOOR(CURRENT_TIMESTAMP TO DAY)  -- midnight today

-- Extract hour-of-day for pattern analysis
EXTRACT(HOUR FROM TIME_FLOOR(__time, 'PT1H'))

-- Safe division (avoids div-by-zero)
SUM(a) / NULLIF(SUM(b), 0)

-- Percentage of window total
ROUND(100.0 * SUM(x) / SUM(SUM(x)) OVER (), 2)

-- Time windows
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '1'  HOUR
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '24' HOUR
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '7'  DAY
WHERE __time >= CURRENT_TIMESTAMP - INTERVAL '30' DAY
WHERE __time >= FLOOR(CURRENT_TIMESTAMP TO DAY)   -- today
```

### Running these queries

```bash
# Druid SQL REST endpoint
curl -X POST http://localhost:8888/druid/v2/sql \
  -H "Content-Type: application/json" \
  -d '{"query": "<paste query here>", "context": {"sqlQueryId": "my-query"}}'

# Druid unified console (interactive)
open http://localhost:8888/unified-console.html  # → Query tab
```
