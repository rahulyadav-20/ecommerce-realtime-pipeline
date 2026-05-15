/**
 * useWebSocket — production-grade WebSocket hook for the live event stream.
 *
 * Protocol (matches dashboard-api/app/routers/websocket.py)
 * ─────────────────────────────────────────────────────────
 * Server → client:
 *   {"type":"connected","clients":N,"topic":"…","auth":false}  (handshake)
 *   {"type":"ping","ts":1234567890}                            (server heartbeat)
 *   {"event_id":…,"event_type":…,…}                           (live event)
 *
 * Client → server:
 *   "ping"                          → server replies "pong"
 *   "pong"                          → reply to server {"type":"ping"}
 *
 * Reconnection strategy
 * ─────────────────────
 * On disconnect, back-off sleeps: 1 s → 2 s → 4 s → 8 s … capped at 30 s.
 * The counter resets to 0 after a successful connection lasts > STABLE_MS.
 * MAX_RECONNECT_ATTEMPTS (default: unlimited) can be used to give up.
 *
 * Heartbeat
 * ─────────
 * Client sends a plain "ping" every CLIENT_HEARTBEAT_MS (23 s by default)
 * to keep NAT/proxy connections alive.  This is independent of — and
 * slightly shorter than — the server's 25 s server-ping interval, so both
 * sides know the other is alive before any proxy kills the connection.
 *
 * Authentication
 * ──────────────
 * Pass `token` in options.  It is appended as `?token=<value>`.
 * Leave empty when WS_SECRET_TOKEN is not set on the server.
 *
 * Backpressure / event buffer
 * ───────────────────────────
 * The hook keeps at most `maxEvents` events in a FIFO circular buffer.
 * Old events fall off the tail automatically.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { LiveEvent, WsStatus } from "@/types";

// ── Timing constants ──────────────────────────────────────────────────────────

/** Initial reconnect delay in ms */
const BACKOFF_BASE_MS = 1_000;

/** Maximum reconnect delay in ms */
const BACKOFF_CAP_MS = 30_000;

/** A connection that lasts this long resets the attempt counter */
const STABLE_MS = 10_000;

/** How often the client sends a keep-alive "ping" to the server */
const CLIENT_HEARTBEAT_MS = 23_000;

/** Timeout before giving up on a new connection attempt */
const CONNECT_TIMEOUT_MS = 8_000;

// ── Types ─────────────────────────────────────────────────────────────────────

export interface UseWebSocketOptions {
  /** Optional auth token appended as ?token=<value> */
  token?: string;
  /** Maximum events to keep in the buffer (default: 200) */
  maxEvents?: number;
  /** Maximum reconnect attempts before giving up (default: Infinity) */
  maxAttempts?: number;
}

export interface UseWebSocketResult {
  /** Live event buffer, newest first */
  events: LiveEvent[];
  /** Current connection state */
  status: WsStatus;
  /** How many reconnect attempts have been made since last stable connection */
  reconnectCount: number;
  /** ms until the next reconnect attempt (0 when connected or not reconnecting) */
  nextReconnectMs: number;
  /** Number of events received this session */
  totalReceived: number;
  /** Clear the event buffer */
  clearEvents: () => void;
  /** Manually trigger a reconnect (resets backoff) */
  reconnect: () => void;
  /** Connected client count reported by the server */
  serverClients: number;
}

// ═════════════════════════════════════════════════════════════════════════════
// Hook
// ═════════════════════════════════════════════════════════════════════════════

export function useWebSocket(options: UseWebSocketOptions = {}): UseWebSocketResult {
  const { token = "", maxEvents = 200, maxAttempts = Infinity } = options;

  // ── State ─────────────────────────────────────────────────────────────────
  const [events,          setEvents]          = useState<LiveEvent[]>([]);
  const [status,          setStatus]          = useState<WsStatus>("connecting");
  const [reconnectCount,  setReconnectCount]  = useState(0);
  const [nextReconnectMs, setNextReconnectMs] = useState(0);
  const [totalReceived,   setTotalReceived]   = useState(0);
  const [serverClients,   setServerClients]   = useState(0);

  // ── Stable refs (never re-created, safe to use inside callbacks) ──────────
  const wsRef              = useRef<WebSocket | null>(null);
  const reconnectTimerRef  = useRef<ReturnType<typeof setTimeout> | null>(null);
  const heartbeatTimerRef  = useRef<ReturnType<typeof setInterval> | null>(null);
  const countdownTimerRef  = useRef<ReturnType<typeof setInterval> | null>(null);
  const connectTimerRef    = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef         = useRef(0);
  const connectedAtRef     = useRef(0);
  const mountedRef         = useRef(true);
  const manualReconnectRef = useRef(false);

  // ── Helpers ───────────────────────────────────────────────────────────────

  const clearTimers = useCallback(() => {
    if (reconnectTimerRef.current) { clearTimeout(reconnectTimerRef.current);  reconnectTimerRef.current = null; }
    if (heartbeatTimerRef.current) { clearInterval(heartbeatTimerRef.current); heartbeatTimerRef.current = null; }
    if (countdownTimerRef.current) { clearInterval(countdownTimerRef.current); countdownTimerRef.current = null; }
    if (connectTimerRef.current)   { clearTimeout(connectTimerRef.current);    connectTimerRef.current = null; }
  }, []);

  const startCountdown = useCallback((delayMs: number) => {
    const endsAt = Date.now() + delayMs;
    setNextReconnectMs(delayMs);
    if (countdownTimerRef.current) clearInterval(countdownTimerRef.current);
    countdownTimerRef.current = setInterval(() => {
      const remaining = Math.max(0, endsAt - Date.now());
      setNextReconnectMs(remaining);
      if (remaining === 0 && countdownTimerRef.current) {
        clearInterval(countdownTimerRef.current);
        countdownTimerRef.current = null;
      }
    }, 200);
  }, []);

  const startHeartbeat = useCallback((ws: WebSocket) => {
    if (heartbeatTimerRef.current) clearInterval(heartbeatTimerRef.current);
    heartbeatTimerRef.current = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send("ping");
      }
    }, CLIENT_HEARTBEAT_MS);
  }, []);

  // ── Core connect function (uses only refs to avoid stale closures) ─────────

  const connect = useCallback(() => {
    if (!mountedRef.current) return;

    // Give up after maxAttempts
    if (attemptRef.current >= maxAttempts) {
      setStatus("disconnected");
      return;
    }

    setStatus("connecting");
    setNextReconnectMs(0);
    if (countdownTimerRef.current) {
      clearInterval(countdownTimerRef.current);
      countdownTimerRef.current = null;
    }

    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const query = token ? `?token=${encodeURIComponent(token)}` : "";
    const url   = `${proto}//${window.location.host}/ws/events${query}`;

    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch (err) {
      scheduleReconnect();
      return;
    }

    wsRef.current = ws;

    // Hard connect timeout — if the socket doesn't open within CONNECT_TIMEOUT_MS
    // we treat it as a failed attempt
    connectTimerRef.current = setTimeout(() => {
      if (ws.readyState !== WebSocket.OPEN) {
        ws.close();
        scheduleReconnect();
      }
    }, CONNECT_TIMEOUT_MS);

    ws.onopen = () => {
      if (!mountedRef.current) { ws.close(); return; }
      clearTimeout(connectTimerRef.current!);
      connectTimerRef.current = null;

      connectedAtRef.current = Date.now();
      setStatus("connected");
      setNextReconnectMs(0);
      startHeartbeat(ws);
    };

    ws.onmessage = (e: MessageEvent<string>) => {
      if (!mountedRef.current) return;

      // Plain text frames: "pong" (response to our ping)
      if (e.data === "pong") return;

      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(e.data);
      } catch {
        return; // malformed frame — skip
      }

      const frameType = parsed["type"] as string | undefined;

      // Control frames — do not add to the event buffer
      if (frameType === "connected") {
        // Reset attempt counter only after a STABLE_MS-long connection
        setTimeout(() => {
          if (mountedRef.current && wsRef.current?.readyState === WebSocket.OPEN) {
            attemptRef.current = 0;
            setReconnectCount(0);
          }
        }, STABLE_MS);
        return;
      }
      if (frameType === "ping") {
        if (ws.readyState === WebSocket.OPEN) ws.send("pong");
        return;
      }

      // Scalar server-status field (e.g. clients count from handshake)
      if (typeof parsed["clients"] === "number") {
        setServerClients(parsed["clients"] as number);
        if (frameType) return; // still a control frame
      }

      // ── Live event frame ──────────────────────────────────────────────────
      const event = parsed as unknown as LiveEvent;
      if (!event.event_id) return; // guard: must have event_id

      setTotalReceived((n) => n + 1);
      setEvents((prev) => [event, ...prev].slice(0, maxEvents));
    };

    ws.onerror = () => {
      // onclose will fire next — handle reconnect there
      clearTimeout(connectTimerRef.current!);
      connectTimerRef.current = null;
    };

    ws.onclose = (e: CloseEvent) => {
      if (heartbeatTimerRef.current) {
        clearInterval(heartbeatTimerRef.current);
        heartbeatTimerRef.current = null;
      }
      if (!mountedRef.current) return;

      setStatus("disconnected");

      // 1000 = normal, 1001 = going away (server shutdown) — don't reconnect
      const noRetry = (e.code === 1000 || e.code === 1001) && !manualReconnectRef.current;
      // 1008 = policy violation (bad token OR capacity) — wait before retry
      if (!noRetry) {
        scheduleReconnect();
      }
    };
  }, [token, maxAttempts, maxEvents, startHeartbeat]); // eslint-disable-line

  const scheduleReconnect = useCallback(() => {
    if (!mountedRef.current) return;
    attemptRef.current += 1;
    setReconnectCount((n) => n + 1);

    const delay = Math.min(BACKOFF_BASE_MS * 2 ** (attemptRef.current - 1), BACKOFF_CAP_MS);
    startCountdown(delay);

    reconnectTimerRef.current = setTimeout(() => {
      if (mountedRef.current) connect();
    }, delay);
  }, [connect, startCountdown]);

  // ── Public reconnect (resets back-off) ────────────────────────────────────

  const reconnect = useCallback(() => {
    clearTimers();
    wsRef.current?.close(1000, "manual reconnect");
    attemptRef.current = 0;
    manualReconnectRef.current = true;
    setTimeout(() => { manualReconnectRef.current = false; }, 200);
    connect();
  }, [clearTimers, connect]);

  const clearEvents = useCallback(() => setEvents([]), []);

  // ── Mount / unmount ───────────────────────────────────────────────────────

  useEffect(() => {
    mountedRef.current = true;
    connect();

    return () => {
      mountedRef.current = false;
      clearTimers();
      wsRef.current?.close(1000, "component unmounted");
    };
  }, []); // run once on mount

  return {
    events,
    status,
    reconnectCount,
    nextReconnectMs,
    totalReceived,
    clearEvents,
    reconnect,
    serverClients,
  };
}
