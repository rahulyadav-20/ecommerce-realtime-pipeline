import { format } from "date-fns";
import {
  ArrowDownRight,
  ArrowUpRight,
  CheckCircle,
  Clock,
  Cpu,
  Database,
  DollarSign,
  Minus,
  ShoppingCart,
  TrendingUp,
  Users,
  XCircle,
  Zap,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import SparkLine from "@/components/SparkLine";
import { Skeleton } from "@/components/ui/Skeleton";
import {
  useFlinkMetrics,
  useGlobalFunnel,
  useFunnel,
  useRealtimeKPIs,
  useRevenueTimeseries,
} from "@/hooks/useMetrics";
import { fmtCount, fmtPct, fmtUSD } from "@/lib/utils";

// ── Tooltip styles ────────────────────────────────────────────────────────────
const TT_STYLE = {
  background: "#111a31",
  border: "1px solid #1a263f",
  borderRadius: 8,
  fontSize: 12,
  boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
};

// ── KPI Card ──────────────────────────────────────────────────────────────────
interface KPIProps {
  label:   string;
  value:   string;
  sub:     string;
  icon:    React.ReactNode;
  color:   string;       // tailwind text colour
  bgColor: string;       // tailwind bg colour
  spark?:  number[];
  trend?:  number;
  loading: boolean;
}

function KPICard({ label, value, sub, icon, color, bgColor, spark, trend, loading }: KPIProps) {
  if (loading) return (
    <div className="card-hover p-5 space-y-3 animate-fade-in">
      <Skeleton className="h-3 w-20 bg-ink-600" />
      <Skeleton className="h-8 w-28 bg-ink-600" />
      <Skeleton className="h-3 w-16 bg-ink-600" />
    </div>
  );

  const TrendIcon = trend === undefined || trend === 0 ? Minus : trend > 0 ? ArrowUpRight : ArrowDownRight;
  const trendClass = trend === undefined || trend === 0 ? "trend-flat" : trend > 0 ? "trend-up" : "trend-down";

  return (
    <div className="card-hover p-5 flex flex-col gap-3 animate-fade-in">
      <div className="flex items-start justify-between">
        <div className={`w-9 h-9 rounded-lg ${bgColor} flex items-center justify-center`}>
          <span className={color}>{icon}</span>
        </div>
        {trend !== undefined && (
          <span className={trendClass}>
            <TrendIcon className="w-3 h-3" />
            {Math.abs(trend).toFixed(1)}%
          </span>
        )}
      </div>

      <div>
        <p className="metric-value">{value}</p>
        <p className="metric-label mt-1">{label}</p>
      </div>

      {spark && spark.length > 1 && (
        <div className="h-9 -mx-1">
          <SparkLine data={spark} color={color.includes("violet") || color.includes("indigo") ? "#6366f1" : color.includes("blue") ? "#3b82f6" : color.includes("green") ? "#22c55e" : "#eab308"} />
        </div>
      )}

      <p className="text-[11px] text-slate-500">{sub}</p>
    </div>
  );
}

// ── Revenue area chart ────────────────────────────────────────────────────────
function RevenueChart() {
  const { data, isLoading } = useRevenueTimeseries("24h", "5m");

  const chartData = (data?.data ?? []).map((p) => ({
    ts:      (() => { try { return format(new Date(p.timestamp), "HH:mm"); } catch { return p.timestamp.slice(11, 16); } })(),
    revenue: p.revenue,
  }));


  return (
    <div className="card-hover p-5 flex flex-col gap-4 col-span-2">
      <div className="flex items-center justify-between">
        <div>
          <p className="section-title">Revenue Over Time</p>
          <p className="text-xs text-slate-500 mt-0.5">Last 24 hours · 5-min buckets</p>
        </div>
        <span className="px-2.5 py-1 bg-accent-500/10 text-accent-300 text-xs font-medium rounded-full border border-accent-500/20">
          Live · 30 s
        </span>
      </div>

      {isLoading ? (
        <Skeleton className="h-56 w-full bg-ink-700" />
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <AreaChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="revGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%"   stopColor="#6366f1" stopOpacity={0.25} />
                <stop offset="100%" stopColor="#6366f1" stopOpacity={0}    />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#1a263f" vertical={false} />
            <XAxis dataKey="ts" tick={{ fill: "#475569", fontSize: 11 }} tickLine={false} axisLine={false} interval={23} />
            <YAxis
              tickFormatter={(v) => v >= 1000 ? `$${(v/1000).toFixed(0)}k` : `$${v}`}
              tick={{ fill: "#475569", fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={52}
            />
            <Tooltip
              contentStyle={TT_STYLE}
              formatter={(v: number) => [fmtUSD(v), "Revenue"]}
              labelStyle={{ color: "#94a3b8", marginBottom: 4 }}
              cursor={{ stroke: "#6366f1", strokeWidth: 1, strokeDasharray: "4 2" }}
            />
            <Area type="monotone" dataKey="revenue" stroke="#6366f1" strokeWidth={2} fill="url(#revGrad)" dot={false} activeDot={{ r: 4, fill: "#6366f1", strokeWidth: 2, stroke: "#0d1225" }} />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

// ── Event type donut ──────────────────────────────────────────────────────────
const DONUT_COLORS = ["#3b82f6", "#8b5cf6", "#eab308", "#22c55e"];
const DONUT_LABELS = ["Product Views", "Add to Cart", "Checkout", "Payments"];

function EventDonut() {
  const { totals, isLoading } = useGlobalFunnel("7d");
  const data = totals
    ? [
        { name: DONUT_LABELS[0], value: totals.views    },
        { name: DONUT_LABELS[1], value: totals.carts    },
        { name: DONUT_LABELS[2], value: totals.orders   },
        { name: DONUT_LABELS[3], value: totals.payments },
      ]
    : [];

  const total = data.reduce((s, d) => s + d.value, 0);

  return (
    <div className="card-hover p-5 flex flex-col gap-4">
      <div>
        <p className="section-title">Event Distribution</p>
        <p className="text-xs text-slate-500 mt-0.5">Last 7 days</p>
      </div>

      {isLoading ? (
        <Skeleton className="h-44 w-full bg-ink-700 rounded-lg" />
      ) : (
        <>
          <div className="relative">
            <ResponsiveContainer width="100%" height={160}>
              <PieChart>
                <Pie data={data} cx="50%" cy="50%" innerRadius={50} outerRadius={75} paddingAngle={2} dataKey="value" strokeWidth={0}>
                  {data.map((_, i) => <Cell key={i} fill={DONUT_COLORS[i]} />)}
                </Pie>
                <Tooltip contentStyle={TT_STYLE} formatter={(v: number, n: string) => [v.toLocaleString(), n]} />
              </PieChart>
            </ResponsiveContainer>
            {/* Centre label */}
            <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
              <p className="text-lg font-bold text-white tabular-nums">{fmtCount(total)}</p>
              <p className="text-[10px] text-slate-500">total events</p>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
            {data.map((d, i) => (
              <div key={i} className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full shrink-0" style={{ background: DONUT_COLORS[i] }} />
                <span className="text-[11px] text-slate-400 truncate">{d.name}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// ── Top categories ────────────────────────────────────────────────────────────
function TopCategories() {
  const { data, isLoading } = useFunnel("7d", "category");
  const rows = (data?.data ?? [])
    .slice(0, 5)
    .map(r => ({ name: String(r.category ?? "unknown").slice(0, 14), value: Number(r.payments) || 0 }))
    .sort((a, b) => b.value - a.value);
  const max = Math.max(...rows.map(r => r.value), 1);

  return (
    <div className="card-hover p-5 flex flex-col gap-4">
      <div>
        <p className="section-title">Top Categories</p>
        <p className="text-xs text-slate-500 mt-0.5">By conversions · last 7 d</p>
      </div>

      {isLoading ? (
        <Skeleton className="h-44 w-full bg-ink-700 rounded-lg" />
      ) : (
        <div className="space-y-3">
          {rows.map((row, i) => (
            <div key={i} className="space-y-1">
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-300 font-medium">{row.name}</span>
                <span className="text-slate-400 tabular-nums">{row.value.toLocaleString()}</span>
              </div>
              <div className="h-1.5 bg-ink-600 rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-700"
                  style={{
                    width: `${(row.value / max) * 100}%`,
                    background: `hsl(${240 - i * 25}, 70%, 60%)`,
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Flink status card ─────────────────────────────────────────────────────────
function FlinkCard() {
  const { data, isLoading } = useFlinkMetrics();
  const running = data?.status === "RUNNING";

  const stats = [
    { label: "Status",        value: data?.status ?? "—",         icon: running ? <CheckCircle className="w-3.5 h-3.5 text-green-400" /> : <XCircle className="w-3.5 h-3.5 text-red-400" /> },
    { label: "Uptime",        value: `${data?.uptime_hours?.toFixed(1) ?? "—"} h`, icon: <Clock className="w-3.5 h-3.5 text-slate-500" /> },
    { label: "Throughput",    value: `${(data?.throughput_per_sec ?? 0).toLocaleString()} /s`, icon: <Cpu className="w-3.5 h-3.5 text-slate-500" /> },
    { label: "Records",       value: fmtCount(data?.records_processed ?? 0), icon: <Database className="w-3.5 h-3.5 text-slate-500" /> },
  ];

  return (
    <div className="card-hover p-5 flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="section-title">Flink ETL</p>
          <p className="text-xs text-slate-500 mt-0.5">Stream processor</p>
        </div>
        {!isLoading && (
          <span className={`flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded-full ${running ? "bg-green-500/10 text-green-400 border border-green-500/20" : "bg-red-500/10 text-red-400 border border-red-500/20"}`}>
            {running ? <Zap className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
            {data?.status ?? "—"}
          </span>
        )}
      </div>

      {isLoading ? (
        <div className="space-y-2"><Skeleton className="h-8 w-full bg-ink-700" /><Skeleton className="h-8 w-full bg-ink-700" /></div>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          {stats.map(({ label, value, icon }) => (
            <div key={label} className="bg-ink-700/50 rounded-lg px-3 py-2.5 flex items-start gap-2">
              <span className="mt-0.5 shrink-0">{icon}</span>
              <div>
                <p className="text-[10px] text-slate-500 uppercase tracking-wider">{label}</p>
                <p className="text-sm font-semibold text-slate-200 tabular-nums mt-0.5">{value}</p>
              </div>
            </div>
          ))}
        </div>
      )}

      {data?.last_checkpoint && (
        <div className="flex items-center gap-2 text-xs text-slate-500 pt-1 border-t border-ink-600">
          <CheckCircle className="w-3 h-3 text-green-500" />
          Last checkpoint in {(data.last_checkpoint.duration_ms / 1000).toFixed(1)} s
        </div>
      )}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────
export default function Overview() {
  const { data: kpis, isLoading, dataUpdatedAt } = useRealtimeKPIs("1h");

  const lastUpdated = dataUpdatedAt
    ? format(new Date(dataUpdatedAt), "HH:mm:ss")
    : "—";

  // Generate sparkline data from KPIs (since we don't have historical data per-metric,
  // use a simulated trend from the timeseries as proxy)
  const { data: ts } = useRevenueTimeseries("1h", "5m");
  const revSpark = (ts?.data ?? []).slice(-12).map(p => p.revenue);

  return (
    <div className="p-6 space-y-6 animate-fade-in">

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-white">Dashboard Overview</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Real-time metrics · Last 1 hour · Auto-refresh every 5 s
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
          Updated {lastUpdated}
        </div>
      </div>

      {/* ── KPI cards ──────────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        <KPICard
          label="Total Revenue"
          value={kpis ? fmtUSD(kpis.revenue) : "—"}
          sub="From completed payments"
          icon={<DollarSign className="w-4 h-4" />}
          color="text-violet-400"
          bgColor="bg-violet-500/10"
          spark={revSpark}
          loading={isLoading}
        />
        <KPICard
          label="Orders"
          value={kpis ? fmtCount(kpis.orders) : "—"}
          sub="PAYMENT_SUCCESS events"
          icon={<ShoppingCart className="w-4 h-4" />}
          color="text-blue-400"
          bgColor="bg-blue-500/10"
          loading={isLoading}
        />
        <KPICard
          label="Active Users"
          value={kpis ? fmtCount(kpis.active_users) : "—"}
          sub="Distinct user IDs seen"
          icon={<Users className="w-4 h-4" />}
          color="text-teal-400"
          bgColor="bg-teal-500/10"
          loading={isLoading}
        />
        <KPICard
          label="Conversion"
          value={kpis ? fmtPct(kpis.cart_conversion_rate) : "—"}
          sub={kpis ? `AOV ${fmtUSD(kpis.avg_order_value)}` : "Cart → Payment"}
          icon={<TrendingUp className="w-4 h-4" />}
          color="text-green-400"
          bgColor="bg-green-500/10"
          loading={isLoading}
        />
      </div>

      {/* ── Charts row 1 ───────────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <RevenueChart />
        <EventDonut />
      </div>

      {/* ── Charts row 2 ───────────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="xl:col-span-2">
          <TopCategories />
        </div>
        <FlinkCard />
      </div>
    </div>
  );
}
