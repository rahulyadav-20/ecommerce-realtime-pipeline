import { useState } from "react";
import { format } from "date-fns";
import {
  AlertTriangle, CheckCircle, Clock, Cpu,
  Database, RefreshCw, Server,
} from "lucide-react";
import { Badge }  from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { useFlinkMetrics, useHealth } from "@/hooks/useMetrics";
import { clamp } from "@/lib/utils";
import type { ComponentHealth, HealthStatus } from "@/types";

// ── Service metadata ──────────────────────────────────────────────────────────
const SVC: Record<string, { label: string; icon: React.ReactNode; desc: string }> = {
  druid:      { label: "Apache Druid",  icon: <Database className="w-4 h-4" />, desc: "Real-time OLAP · Kafka supervisor" },
  clickhouse: { label: "ClickHouse",    icon: <Database className="w-4 h-4" />, desc: "Historical OLAP · Kafka Engine"   },
  flink:      { label: "Apache Flink",  icon: <Cpu      className="w-4 h-4" />, desc: "Stream ETL · exactly-once"         },
  kafka:      { label: "Apache Kafka",  icon: <Server   className="w-4 h-4" />, desc: "Event backbone · 2 brokers"        },
};

const STATUS_BADGE: Record<HealthStatus, "success" | "warning" | "error"> = {
  up: "success", degraded: "warning", down: "error",
};
const STATUS_DOT: Record<HealthStatus, string> = {
  up: "status-up", degraded: "status-degraded", down: "status-down",
};

// ── Service card ──────────────────────────────────────────────────────────────
function ServiceCard({ name, h }: { name: string; h: ComponentHealth }) {
  const meta   = SVC[name] ?? { label: name, icon: <Server className="w-4 h-4" />, desc: "" };
  const status = h.status as HealthStatus;

  const iconColor: Record<string, string> = {
    druid: "text-orange-400", clickhouse: "text-yellow-400",
    flink: "text-accent-400", kafka: "text-blue-400",
  };
  const bgColor: Record<string, string> = {
    druid: "bg-orange-500/10", clickhouse: "bg-yellow-500/10",
    flink: "bg-accent-500/10", kafka: "bg-blue-500/10",
  };

  return (
    <div className="bg-ink-800 border border-ink-600 rounded-xl p-5 shadow-card hover:border-ink-500 transition-all">
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className={`w-9 h-9 rounded-lg ${bgColor[name] ?? "bg-ink-700"} flex items-center justify-center`}>
            <span className={iconColor[name] ?? "text-slate-400"}>{meta.icon}</span>
          </div>
          <div>
            <p className="text-sm font-semibold text-slate-200">{meta.label}</p>
            <p className="text-[11px] text-slate-500 mt-0.5">{meta.desc}</p>
          </div>
        </div>
        <Badge variant={STATUS_BADGE[status]}>{status.toUpperCase()}</Badge>
      </div>

      <div className="flex items-center justify-between pt-3 border-t border-ink-600">
        <div className="flex items-center gap-2">
          <span className={STATUS_DOT[status]} />
          <span className="text-xs text-slate-500 truncate max-w-[180px]">
            {h.detail ?? "Operating normally"}
          </span>
        </div>
        {h.response_time_ms != null && (
          <span className="text-xs text-slate-500 tabular-nums shrink-0">{h.response_time_ms} ms</span>
        )}
      </div>
    </div>
  );
}

// ── Flink detail ──────────────────────────────────────────────────────────────
function FlinkDetail() {
  const { data, isLoading, refetch, isRefetching } = useFlinkMetrics();
  const running = data?.status === "RUNNING";

  const metrics = [
    { icon: <Clock className="w-4 h-4" />,    label: "Job Status",   value: data?.status ?? "—",            accent: running },
    { icon: <Clock className="w-4 h-4" />,    label: "Uptime",       value: `${data?.uptime_hours?.toFixed(2) ?? "—"} h` },
    { icon: <Cpu className="w-4 h-4" />,      label: "Throughput",   value: `${(data?.throughput_per_sec ?? 0).toLocaleString()} rec/s` },
    { icon: <Database className="w-4 h-4" />, label: "Processed",    value: (data?.records_processed ?? 0).toLocaleString() },
    { icon: <CheckCircle className="w-4 h-4" />, label: "Checkpoint", value: data?.last_checkpoint ? `${data.last_checkpoint.status} · ${(data.last_checkpoint.duration_ms / 1000).toFixed(1)} s` : "—" },
    { icon: <Clock className="w-4 h-4" />,    label: "CP #",         value: data?.last_checkpoint?.id?.toString() ?? "—" },
  ];

  return (
    <div className="bg-ink-800 border border-ink-600 rounded-xl shadow-card">
      <div className="px-5 pt-5 pb-4 border-b border-ink-600 flex items-center justify-between">
        <div>
          <p className="section-title">Flink ETL — Metrics</p>
          {data?.job_id && <p className="text-[10px] font-mono text-slate-600 mt-0.5 truncate">{data.job_id}</p>}
        </div>
        <div className="flex items-center gap-2">
          {!isLoading && (
            <Badge variant={running ? "success" : "error"}>{running ? "RUNNING" : data?.status ?? "UNKNOWN"}</Badge>
          )}
          <Button variant="ghost" size="icon" onClick={() => refetch()}>
            <RefreshCw className={`w-3.5 h-3.5 text-slate-500 ${isRefetching ? "animate-spin" : ""}`} />
          </Button>
        </div>
      </div>
      <div className="p-4">
        {isLoading ? (
          <div className="grid grid-cols-2 gap-2">{[1,2,3,4].map(n => <Skeleton key={n} className="h-14 bg-ink-700" />)}</div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {metrics.map(({ icon, label, value, accent }) => (
              <div key={label} className="bg-ink-700/50 rounded-lg px-3 py-2.5 flex items-start gap-2">
                <span className={`mt-0.5 shrink-0 ${accent ? "text-green-400" : "text-slate-500"}`}>{icon}</span>
                <div className="min-w-0">
                  <p className="text-[10px] text-slate-500 uppercase tracking-wider">{label}</p>
                  <p className={`text-sm font-semibold tabular-nums mt-0.5 truncate ${accent ? "text-green-400" : "text-slate-200"}`}>{value}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Consumer lag gauges ───────────────────────────────────────────────────────
const GROUPS = [
  { id: "flink-ecom-etl",          label: "Flink ETL",        max: 5_000  },
  { id: "clickhouse-ecom-consumer", label: "ClickHouse",       max: 10_000 },
  { id: "druid-ecommerce-events",   label: "Druid Supervisor", max: 10_000 },
  { id: "connect-iceberg-sink",     label: "Iceberg Connect",  max: 5_000  },
];

function LagGauges() {
  const { data: flink } = useFlinkMetrics();
  const tps = flink?.throughput_per_sec ?? 0;
  const base = tps > 0 ? Math.max(0, Math.round(500 - tps * 0.05)) : 0;
  const lags: Record<string, number> = {
    "flink-ecom-etl": base,
    "clickhouse-ecom-consumer": Math.round(base * 1.4),
    "druid-ecommerce-events":   Math.round(base * 1.2),
    "connect-iceberg-sink":     Math.round(base * 0.7),
  };

  return (
    <div className="bg-ink-800 border border-ink-600 rounded-xl p-5 shadow-card">
      <p className="section-title mb-1">Consumer Lag</p>
      <p className="text-xs text-slate-500 mb-5">Estimated from Flink throughput</p>
      <div className="space-y-4">
        {GROUPS.map(({ id, label, max }) => {
          const lag = lags[id] ?? 0;
          const pct = clamp((lag / max) * 100, 0, 100);
          const color = pct < 20 ? "#22c55e" : pct < 60 ? "#eab308" : "#ef4444";
          return (
            <div key={id}>
              <div className="flex items-center justify-between text-xs mb-1.5">
                <span className="text-slate-400 font-medium">{label}</span>
                <span className="tabular-nums font-semibold" style={{ color }}>
                  {lag.toLocaleString()}
                </span>
              </div>
              <div className="h-1.5 bg-ink-600 rounded-full overflow-hidden">
                <div className="h-full rounded-full transition-all duration-700" style={{ width: `${pct}%`, background: color }} />
              </div>
            </div>
          );
        })}
      </div>
      <p className="text-[10px] text-slate-600 mt-4">* Requires Kafka Admin API for real values</p>
    </div>
  );
}

// ── Alert history ─────────────────────────────────────────────────────────────
const ALERTS = [
  { id: 1, level: "info",    msg: "Flink checkpoint completed",       time: "Just now",  ok: true  },
  { id: 2, level: "warning", msg: "ClickHouse consumer lag spike",    time: "5 min ago", ok: true  },
  { id: 3, level: "info",    msg: "Druid supervisor restarted",       time: "12 min ago",ok: true  },
  { id: 4, level: "info",    msg: "Avro schema v2 registered",        time: "1 h ago",   ok: true  },
];

function AlertHistory() {
  const [filter, setFilter] = useState<"all" | "active" | "resolved">("all");
  const filtered = ALERTS.filter(a =>
    filter === "all" ? true : filter === "resolved" ? a.ok : !a.ok
  );
  return (
    <div className="bg-ink-800 border border-ink-600 rounded-xl shadow-card">
      <div className="px-5 pt-5 pb-4 border-b border-ink-600 flex items-center justify-between">
        <p className="section-title">Alert History</p>
        <div className="flex gap-1">
          {(["all", "active", "resolved"] as const).map(f => (
            <Button key={f} variant={filter === f ? "secondary" : "ghost"} size="sm" onClick={() => setFilter(f)}>
              {f.charAt(0).toUpperCase() + f.slice(1)}
            </Button>
          ))}
        </div>
      </div>
      <div className="p-3 space-y-1.5">
        {filtered.length === 0 ? (
          <p className="py-8 text-center text-sm text-slate-600">No alerts</p>
        ) : (
          filtered.map(a => (
            <div key={a.id} className="flex items-center gap-3 px-4 py-3 rounded-lg bg-ink-700/40 hover:bg-ink-700/70 transition-colors">
              {a.level === "warning"
                ? <AlertTriangle className="w-4 h-4 text-yellow-400 shrink-0" />
                : <CheckCircle   className="w-4 h-4 text-green-500 shrink-0"  />}
              <p className="flex-1 text-sm text-slate-300">{a.msg}</p>
              <span className="text-xs text-slate-500 whitespace-nowrap">{a.time}</span>
              <Badge variant={a.ok ? "success" : "warning"}>{a.ok ? "Resolved" : "Active"}</Badge>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────
export default function SystemHealth() {
  const { data: health, isLoading, refetch, isRefetching, dataUpdatedAt } = useHealth();
  const lastUpdated = dataUpdatedAt ? format(new Date(dataUpdatedAt), "HH:mm:ss") : "—";

  const oBadge: Record<string, "success" | "warning" | "error"> = {
    healthy: "success", degraded: "warning", unhealthy: "error",
  };

  return (
    <div className="p-6 space-y-6 animate-fade-in">

      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-lg font-bold text-white">System Health</h1>
          <p className="text-xs text-slate-500 mt-0.5">All components · 15 s auto-refresh</p>
        </div>
        <div className="flex items-center gap-3">
          {health && <Badge variant={oBadge[health.status] ?? "default"}>{health.status.toUpperCase()}</Badge>}
          <span className="text-xs text-slate-600">{lastUpdated}</span>
          <Button variant="secondary" size="sm" onClick={() => refetch()}>
            <RefreshCw className={`w-3.5 h-3.5 ${isRefetching ? "animate-spin" : ""}`} /> Refresh
          </Button>
        </div>
      </div>

      {/* Service cards */}
      {isLoading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
          {[1,2,3,4].map(n => <Skeleton key={n} className="h-32 bg-ink-800" />)}
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
          {Object.entries(health?.components ?? {}).map(([name, h]) => (
            <ServiceCard key={name} name={name} h={h} />
          ))}
        </div>
      )}

      {/* Flink + Lag side by side */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <FlinkDetail />
        <LagGauges />
      </div>

      {/* Alert history */}
      <AlertHistory />
    </div>
  );
}
