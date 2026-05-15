/**
 * Axios-based API client with:
 *  - Automatic base URL (proxied through Vite → nginx → FastAPI)
 *  - Request interceptor: injects X-Request-ID for tracing
 *  - Response interceptor: unwraps `data` field, normalises errors
 *  - 30-second request timeout
 */
import axios, { type AxiosResponse, type InternalAxiosRequestConfig } from "axios";
import type {
  FlinkMetrics,
  FunnelResponse,
  GranularityType,
  HealthResponse,
  LiveEventsResponse,
  RealtimeKPIs,
  TimeWindow,
  TimeseriesResponse,
  TopProductsResponse,
} from "@/types";

// ── Axios instance ────────────────────────────────────────────────────────────

const http = axios.create({
  baseURL: "/api/v1",
  timeout: 30_000,
  headers: { "Content-Type": "application/json" },
});

// ── Request interceptor ───────────────────────────────────────────────────────

http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  config.headers["X-Request-ID"] = crypto.randomUUID();
  return config;
});

// ── Response interceptor — unwrap data, normalise errors ──────────────────────

http.interceptors.response.use(
  (response: AxiosResponse) => response.data,
  (error) => {
    const detail =
      error.response?.data?.detail ??
      error.response?.data?.message ??
      error.message ??
      "Unknown API error";
    const status = error.response?.status ?? 0;
    const msg = status ? `HTTP ${status}: ${detail}` : detail;
    console.error("[API]", msg);
    return Promise.reject(new Error(msg));
  }
);

// ── Typed request helper ──────────────────────────────────────────────────────

function get<T>(path: string): Promise<T> {
  // After the response interceptor, the promise resolves with `response.data`
  return http.get<never, T>(path);
}

// ── Public API ────────────────────────────────────────────────────────────────

export const api = {
  // Real-time KPIs (Druid, last N hours)
  getRealtimeKPIs: (window: TimeWindow = "1h") =>
    get<RealtimeKPIs>(`/kpis/realtime?window=${window}`),

  // Revenue timeseries (Druid, bucketed)
  getRevenueTimeseries: (
    window: TimeWindow = "24h",
    granularity: GranularityType = "5m"
  ) =>
    get<TimeseriesResponse>(
      `/metrics/revenue?window=${window}&granularity=${granularity}`
    ),

  // Conversion funnel (ClickHouse)
  getFunnel: (period = "7d", group_by = "category") =>
    get<FunnelResponse>(`/funnel?period=${period}&group_by=${group_by}`),

  // Top products by revenue (ClickHouse)
  getTopProducts: (limit = 10, period = "30d") =>
    get<TopProductsResponse>(`/products/top?limit=${limit}&period=${period}`),

  // Live event feed (ClickHouse)
  getLiveEvents: (limit = 100) =>
    get<LiveEventsResponse>(`/events/live?limit=${limit}`),

  // Component health check (parallel)
  getHealth: () => get<HealthResponse>("/health"),

  // Flink job metrics
  getFlinkMetrics: () => get<FlinkMetrics>("/flink/metrics"),
};
