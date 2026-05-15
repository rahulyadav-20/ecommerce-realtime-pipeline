/**
 * Centralised TanStack Query hooks for all API endpoints.
 *
 * Naming: use<Entity>[Qualifier]
 * Every hook exports `{ data, isLoading, isError, dataUpdatedAt, refetch }`
 * from useQuery — callers consume only what they need.
 */
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type {
  GranularityType,
  GlobalFunnelTotals,
  LiveEvent,
  TimeWindow,
  WsStatus,
} from "@/types";

// ── Real-time KPIs ─────────────────────────────────────────────────────────

export function useRealtimeKPIs(window: TimeWindow = "1h") {
  return useQuery({
    queryKey: ["kpis", window],
    queryFn: () => api.getRealtimeKPIs(window),
    refetchInterval: 5_000,   // 5-second auto-refresh as required
  });
}

// ── Revenue timeseries ────────────────────────────────────────────────────

export function useRevenueTimeseries(
  window: TimeWindow = "24h",
  granularity: GranularityType = "5m"
) {
  return useQuery({
    queryKey: ["revenue-ts", window, granularity],
    queryFn: () => api.getRevenueTimeseries(window, granularity),
    refetchInterval: 30_000,
  });
}

// ── Funnel ────────────────────────────────────────────────────────────────

export function useFunnel(period = "7d", group_by = "category") {
  return useQuery({
    queryKey: ["funnel", period, group_by],
    queryFn: () => api.getFunnel(period, group_by),
    refetchInterval: 120_000,
  });
}

/**
 * Aggregates funnel rows across all categories into global totals
 * and computes stage-to-stage conversion rates.
 */
export function useGlobalFunnel(period = "7d"): {
  totals: GlobalFunnelTotals | null;
  isLoading: boolean;
} {
  const { data, isLoading } = useFunnel(period, "category");

  if (!data?.data?.length) return { totals: null, isLoading };

  const rows = data.data;
  const views    = rows.reduce((s, r) => s + (Number(r.views)    || 0), 0);
  const carts    = rows.reduce((s, r) => s + (Number(r.carts)    || 0), 0);
  const orders   = rows.reduce((s, r) => s + (Number(r.orders)   || 0), 0);
  const payments = rows.reduce((s, r) => s + (Number(r.payments) || 0), 0);

  const totals: GlobalFunnelTotals = {
    views,
    carts,
    orders,
    payments,
    cart_rate:    views    > 0 ? carts    / views    : 0,
    order_rate:   carts    > 0 ? orders   / carts    : 0,
    payment_rate: orders   > 0 ? payments / orders   : 0,
    overall_rate: views    > 0 ? payments / views    : 0,
  };

  return { totals, isLoading };
}

// ── Top products ──────────────────────────────────────────────────────────

export function useTopProducts(limit = 10, period = "30d") {
  return useQuery({
    queryKey: ["top-products", limit, period],
    queryFn: () => api.getTopProducts(limit, period),
    refetchInterval: 120_000,
  });
}

// ── Live events (polling) ─────────────────────────────────────────────────

export function useLiveEvents(limit = 100) {
  return useQuery({
    queryKey: ["live-events", limit],
    queryFn: () => api.getLiveEvents(limit),
    refetchInterval: 5_000,
  });
}

// ── Health ────────────────────────────────────────────────────────────────

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: api.getHealth,
    refetchInterval: 15_000,
  });
}

// ── Flink metrics ─────────────────────────────────────────────────────────

export function useFlinkMetrics() {
  return useQuery({
    queryKey: ["flink-metrics"],
    queryFn: api.getFlinkMetrics,
    refetchInterval: 15_000,
  });
}

// ── WebSocket live events (bonus) ─────────────────────────────────────────

/**
 * Tries to open a WebSocket to /ws/events.
 * Falls back to TanStack Query polling if the server doesn't support WS.
 *
 * Returns:
 *   events  — merged array (WS events prepended to polled baseline)
 *   status  — "connecting" | "connected" | "disconnected"
 */
export function useWebSocketEvents(limit = 100) {
  const [wsEvents, setWsEvents] = useState<LiveEvent[]>([]);
  const [status, setStatus]     = useState<WsStatus>("connecting");
  const wsRef                   = useRef<WebSocket | null>(null);

  useEffect(() => {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url   = `${proto}//${window.location.host}/ws/events`;

    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
      wsRef.current = ws;
    } catch {
      setStatus("disconnected");
      return;
    }

    ws.onopen    = () => setStatus("connected");
    ws.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data) as LiveEvent;
        setWsEvents((prev) => [event, ...prev].slice(0, limit));
      } catch { /* malformed frame — ignore */ }
    };
    ws.onerror = () => setStatus("disconnected");
    ws.onclose = () => setStatus("disconnected");

    // Connection timeout: if not connected in 3 s, stop trying
    const timer = setTimeout(() => {
      if (ws.readyState !== WebSocket.OPEN) {
        ws.close();
        setStatus("disconnected");
      }
    }, 3_000);

    return () => {
      clearTimeout(timer);
      ws.close();
    };
  }, [limit]);

  // Polling fallback — always active; provides baseline when WS is down
  const { data: polled, isLoading } = useLiveEvents(limit);
  const polledEvents = polled?.data ?? [];

  // When WS is connected and has buffered events, lead with them
  const events =
    status === "connected" && wsEvents.length > 0
      ? [...wsEvents, ...polledEvents].slice(0, limit)
      : polledEvents;

  return { events, status, isLoading };
}
