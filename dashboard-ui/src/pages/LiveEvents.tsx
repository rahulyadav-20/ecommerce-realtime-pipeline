import { useEffect, useRef, useState } from "react";
import { format } from "date-fns";
import {
  AlertCircle, Pause, Play, Radio, RefreshCw,
  Trash2,
} from "lucide-react";
import { Badge }  from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import { useWebSocket }  from "@/hooks/useWebSocket";
import { useLiveEvents } from "@/hooks/useMetrics";
import type { LiveEvent, WsStatus } from "@/types";

// ── Badge variant map ─────────────────────────────────────────────────────────
type BV = "success" | "error" | "info" | "warning" | "default";

const BADGE_V: Record<string, BV> = {
  PAYMENT_SUCCESS:  "success",
  PAYMENT_FAILED:   "error",
  ADD_TO_CART:      "info",
  CHECKOUT_START:   "warning",
  REMOVE_FROM_CART: "warning",
  PRODUCT_VIEW:     "default",
  PAGE_VIEW:        "default",
  SEARCH:           "default",
};

// ── Row highlight colours ─────────────────────────────────────────────────────
const ROW_ACCENT: Record<string, string> = {
  PAYMENT_SUCCESS: "border-l-2 border-l-green-500/60",
  PAYMENT_FAILED:  "border-l-2 border-l-red-500/60",
  ADD_TO_CART:     "border-l-2 border-l-blue-500/40",
  CHECKOUT_START:  "border-l-2 border-l-yellow-500/40",
};

// ── Status configs ────────────────────────────────────────────────────────────
const STATUS_CFG: Record<WsStatus, { dot: string; label: string; cls: string }> = {
  connecting:   { dot: "bg-yellow-400 animate-pulse", label: "Connecting…",   cls: "border-yellow-500/20 bg-yellow-500/5 text-yellow-300"  },
  connected:    { dot: "live-dot",                    label: "WebSocket Live", cls: "border-green-500/20 bg-green-500/5 text-green-400"     },
  disconnected: { dot: "bg-slate-500",               label: "Polling 5 s",   cls: "border-ink-500 bg-ink-800 text-slate-400"              },
};

// ── Event row ─────────────────────────────────────────────────────────────────
function EventRow({ event, flash }: { event: LiveEvent; flash: boolean }) {
  const ts = (() => {
    try { return format(new Date(event.timestamp), "HH:mm:ss.SSS"); }
    catch { return event.timestamp.slice(11, 23); }
  })();

  return (
    <tr className={`border-b border-ink-600/40 hover:bg-ink-700/50 transition-colors text-sm ${ROW_ACCENT[event.event_type] ?? ""} ${flash ? "bg-accent-500/5" : ""}`}>
      <td className="py-2.5 pl-4 pr-3 font-mono text-[11px] text-slate-500 whitespace-nowrap">{ts}</td>
      <td className="py-2.5 px-3">
        <Badge variant={BADGE_V[event.event_type] ?? "default"}>{event.event_type}</Badge>
      </td>
      <td className="py-2.5 px-3 font-mono text-[11px] text-slate-400 whitespace-nowrap">{event.user_id.slice(0, 18)}</td>
      <td className="py-2.5 px-3 font-mono text-[11px] text-slate-500 whitespace-nowrap">{event.product_id?.slice(0, 18) ?? "—"}</td>
      <td className="py-2.5 pl-3 pr-4 text-[11px] text-slate-300 tabular-nums text-right font-semibold whitespace-nowrap">
        {event.price != null ? `$${Number(event.price).toFixed(2)}` : "—"}
      </td>
    </tr>
  );
}

// ── Event type selector ───────────────────────────────────────────────────────
const TYPES = ["ALL","PAGE_VIEW","PRODUCT_VIEW","ADD_TO_CART","REMOVE_FROM_CART","CHECKOUT_START","PAYMENT_SUCCESS","PAYMENT_FAILED","SEARCH"];

// ── Page ──────────────────────────────────────────────────────────────────────
export default function LiveEvents() {
  const [paused,     setPaused]     = useState(false);
  const [filterType, setFilterType] = useState("ALL");
  const [flashIds,   setFlashIds]   = useState<Set<string>>(new Set());
  const tableRef = useRef<HTMLDivElement>(null);

  const { events: wsEvents, status, reconnectCount, nextReconnectMs, totalReceived, clearEvents, reconnect } = useWebSocket({ maxEvents: 300 });
  const { data: polled } = useLiveEvents(100);

  const allEvents = status === "connected" && wsEvents.length > 0 ? wsEvents : (polled?.data ?? []);
  const events    = allEvents.filter(e => filterType === "ALL" || e.event_type === filterType).slice(0, 200);

  // Flash newest row
  const prevTop = useRef<string | null>(null);
  useEffect(() => {
    if (!events[0] || events[0].event_id === prevTop.current) return;
    prevTop.current = events[0].event_id;
    setFlashIds(new Set([events[0].event_id]));
    const t = setTimeout(() => setFlashIds(new Set()), 600);
    return () => clearTimeout(t);
  }, [events]);

  // Auto-scroll to top
  const prevLen = useRef(events.length);
  useEffect(() => {
    if (!paused && events.length !== prevLen.current && tableRef.current) {
      tableRef.current.scrollTop = 0;
    }
    prevLen.current = events.length;
  }, [events.length, paused]);

  const cfg    = STATUS_CFG[status];
  const counts = events.reduce<Record<string, number>>((a, e) => ({ ...a, [e.event_type]: (a[e.event_type] || 0) + 1 }), {});

  return (
    <div className="p-6 space-y-5 animate-fade-in">

      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-lg font-bold text-white">Live Event Stream</h1>
          <p className="text-xs text-slate-500 mt-0.5 font-mono">ecommerce.events.clean.v1</p>
        </div>
        <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs ${cfg.cls}`}>
          <span className={`w-2 h-2 rounded-full shrink-0 ${cfg.dot}`} />
          {cfg.label}
          {totalReceived > 0 && (
            <span className="ml-1 text-slate-500">· {totalReceived.toLocaleString()} total</span>
          )}
        </div>
      </div>

      {/* Reconnect notice */}
      {reconnectCount > 0 && status === "disconnected" && (
        <div className="flex items-center gap-3 px-4 py-2.5 rounded-lg bg-yellow-500/5 border border-yellow-500/20 text-sm">
          <AlertCircle className="w-4 h-4 text-yellow-400 shrink-0" />
          <span className="text-yellow-300 flex-1">
            Reconnecting · attempt #{reconnectCount}
            {nextReconnectMs > 0 && ` · next in ${Math.ceil(nextReconnectMs / 1000)} s`}
          </span>
          <Button variant="ghost" size="sm" onClick={reconnect}>
            <RefreshCw className="w-3.5 h-3.5" /> Now
          </Button>
        </div>
      )}

      {/* Controls */}
      <div className="flex items-center gap-3 flex-wrap">
        <Select label="Type" value={filterType} onChange={e => setFilterType(e.target.value)}>
          {TYPES.map(t => <option key={t} value={t}>{t === "ALL" ? "All Types" : t}</option>)}
        </Select>

        <Button variant={paused ? "primary" : "secondary"} size="sm" onClick={() => setPaused(p => !p)}>
          {paused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
          {paused ? "Resume" : "Pause"}
        </Button>

        <Button variant="secondary" size="sm" onClick={clearEvents}>
          <Trash2 className="w-3.5 h-3.5" /> Clear
        </Button>

        {status === "disconnected" && (
          <Button variant="outline" size="sm" onClick={reconnect}>
            <Radio className="w-3.5 h-3.5" /> Reconnect
          </Button>
        )}

        <div className="ml-auto flex items-center gap-4 text-xs text-slate-500">
          <span>{events.length.toLocaleString()} events</span>
        </div>
      </div>

      {/* Type distribution chips */}
      <div className="flex gap-2 flex-wrap">
        {Object.entries(counts).sort(([,a],[,b]) => b - a).slice(0, 7).map(([type, count]) => (
          <button
            key={type}
            onClick={() => setFilterType(type === filterType ? "ALL" : type)}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs transition-all ${filterType === type ? "border-accent-500/40 bg-accent-500/10 text-accent-300" : "border-ink-600 bg-ink-800 text-slate-400 hover:border-ink-500 hover:text-slate-300"}`}
          >
            <Badge variant={BADGE_V[type] ?? "default"}>{type}</Badge>
            <span className="font-semibold tabular-nums">{count}</span>
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="bg-ink-800 border border-ink-600 rounded-xl shadow-card overflow-hidden">
        <div ref={tableRef} className="overflow-auto" style={{ maxHeight: "calc(100vh - 340px)" }}>
          <table className="data-table">
            <thead>
              <tr>
                {[
                  { l: "Timestamp",  a: "left"  },
                  { l: "Event",      a: "left"  },
                  { l: "User ID",    a: "left"  },
                  { l: "Product ID", a: "left"  },
                  { l: "Price",      a: "right" },
                ].map(({ l, a }) => (
                  <th key={l} className={`first:pl-4 last:pr-4`} style={{ textAlign: a as "left" | "right" }}>{l}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {events.length === 0 ? (
                <tr><td colSpan={5} className="py-20 text-center text-sm text-slate-600">
                  {status === "connecting" ? "Connecting to stream…" : "No events — waiting for data…"}
                </td></tr>
              ) : (
                events.map(ev => <EventRow key={ev.event_id} event={ev} flash={flashIds.has(ev.event_id)} />)
              )}
            </tbody>
          </table>
        </div>
      </div>

      {paused && (
        <div className="fixed bottom-6 right-6 z-50 flex items-center gap-2.5 bg-yellow-500/10 border border-yellow-500/30 text-yellow-400 px-4 py-2.5 rounded-xl text-sm font-medium shadow-lg backdrop-blur-sm">
          <Pause className="w-4 h-4" />
          Paused · {events.length} buffered
          <Button variant="ghost" size="sm" onClick={() => setPaused(false)}>
            <Play className="w-3.5 h-3.5 text-yellow-400" />
          </Button>
        </div>
      )}
    </div>
  );
}
