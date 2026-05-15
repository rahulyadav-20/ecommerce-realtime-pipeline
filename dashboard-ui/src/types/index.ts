// ── Enums / literals ──────────────────────────────────────────────────────────

export type TimeWindow = "1h" | "6h" | "12h" | "24h" | "7d" | "30d";
export type GranularityType = "1m" | "5m" | "15m" | "1h" | "1d";
export type EventType =
  | "PAGE_VIEW"
  | "PRODUCT_VIEW"
  | "ADD_TO_CART"
  | "REMOVE_FROM_CART"
  | "CHECKOUT_START"
  | "PAYMENT_SUCCESS"
  | "PAYMENT_FAILED"
  | "SEARCH";

// ── KPIs ─────────────────────────────────────────────────────────────────────

export interface RealtimeKPIs {
  revenue: number;
  orders: number;
  active_users: number;
  avg_order_value: number;
  cart_conversion_rate: number;
  timestamp: string;
}

// ── Timeseries ────────────────────────────────────────────────────────────────

export interface TimeseriesPoint {
  timestamp: string;
  revenue: number;
}

export interface TimeseriesResponse {
  data: TimeseriesPoint[];
  window: string;
  granularity: string;
}

// ── Funnel ────────────────────────────────────────────────────────────────────

export interface FunnelStep {
  category: string;
  views: number;
  carts: number;
  orders: number;
  payments: number;
  conversion_rate: number;
}

export interface FunnelResponse {
  data: FunnelStep[];
  period: string;
  group_by: string;
}

// Global funnel totals derived from FunnelResponse
export interface GlobalFunnelTotals {
  views: number;
  carts: number;
  orders: number;
  payments: number;
  cart_rate: number;     // carts / views
  order_rate: number;    // orders / carts
  payment_rate: number;  // payments / orders
  overall_rate: number;  // payments / views
}

// ── Products ──────────────────────────────────────────────────────────────────

export interface TopProduct {
  product_id: string;
  revenue: number;
  units_sold: number;
  avg_price: number;
}

export interface TopProductsResponse {
  data: TopProduct[];
  period: string;
  limit: number;
}

// ── Live Events ───────────────────────────────────────────────────────────────

export interface LiveEvent {
  event_id: string;
  event_type: string;
  user_id: string;
  product_id?: string;
  price?: number;
  timestamp: string;
}

export interface LiveEventsResponse {
  data: LiveEvent[];
  count: number;
}

// ── Health ────────────────────────────────────────────────────────────────────

export type HealthStatus = "up" | "degraded" | "down";
export type OverallHealth = "healthy" | "degraded" | "unhealthy";

export interface ComponentHealth {
  status: HealthStatus;
  response_time_ms?: number;
  detail?: string;
  lag?: number;
}

export interface HealthResponse {
  status: OverallHealth;
  components: Record<string, ComponentHealth>;
  timestamp: string;
}

// ── Flink ─────────────────────────────────────────────────────────────────────

export interface CheckpointInfo {
  id: number;
  duration_ms: number;
  status: string;
  timestamp?: number;
}

export interface FlinkMetrics {
  job_id: string | null;
  status: string;
  uptime_hours: number;
  records_processed: number;
  throughput_per_sec: number;
  last_checkpoint?: CheckpointInfo;
}

// ── Consumer lag (UI-constructed from Flink metrics) ─────────────────────────

export interface ConsumerGroup {
  name: string;
  label: string;
  lag: number;
  maxLag: number;
}

// ── WebSocket ─────────────────────────────────────────────────────────────────

export type WsStatus = "connecting" | "connected" | "disconnected";
