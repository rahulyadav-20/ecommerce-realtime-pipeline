import { useQuery } from "@tanstack/react-query";
import { format } from "date-fns";
import { Activity, RefreshCw, ShoppingCart } from "lucide-react";
import { api } from "@/api/client";
import FlinkMetrics from "@/components/FlinkMetrics";
import FunnelChart from "@/components/FunnelChart";
import HealthPanel from "@/components/HealthPanel";
import KPICard from "@/components/KPICard";
import LiveEventFeed from "@/components/LiveEventFeed";
import RevenueChart from "@/components/RevenueChart";
import TopProductsTable from "@/components/TopProductsTable";

const fmtUSD = (v: number) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(v);

const fmtCount = (v: number) =>
  v >= 1_000_000 ? `${(v / 1_000_000).toFixed(1)}M` : v >= 1_000 ? `${(v / 1_000).toFixed(1)}k` : String(v);

export default function Dashboard() {
  const { data: kpis, isLoading: kpisLoading, dataUpdatedAt } = useQuery({
    queryKey: ["kpis", "1h"],
    queryFn: () => api.getRealtimeKPIs("1h"),
    refetchInterval: 30_000,
  });

  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: api.getHealth,
    refetchInterval: 15_000,
  });

  const overallStatus = health?.status ?? "unknown";
  const statusColor: Record<string, string> = {
    healthy: "text-brand-400",
    degraded: "text-yellow-400",
    unhealthy: "text-red-400",
    unknown: "text-slate-400",
  };

  const lastUpdated = dataUpdatedAt ? format(new Date(dataUpdatedAt), "HH:mm:ss") : "—";

  return (
    <div className="min-h-screen bg-surface-900 text-slate-100">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header className="sticky top-0 z-10 bg-surface-900/80 backdrop-blur-sm border-b border-surface-700 px-6 py-3">
        <div className="max-w-screen-2xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <ShoppingCart className="w-5 h-5 text-brand-500" />
            <span className="text-base font-bold text-slate-100">E-Commerce Analytics</span>
            <span className="hidden sm:inline text-xs text-slate-500 font-mono">Realtime Pipeline</span>
          </div>

          <div className="flex items-center gap-4">
            <div className="flex items-center gap-1.5">
              <Activity className="w-3.5 h-3.5 text-slate-500" />
              <span className={`text-xs font-semibold capitalize ${statusColor[overallStatus]}`}>
                {overallStatus}
              </span>
            </div>
            <div className="flex items-center gap-1.5 text-xs text-slate-500">
              <RefreshCw className="w-3 h-3" />
              {lastUpdated}
            </div>
          </div>
        </div>
      </header>

      {/* ── Main ────────────────────────────────────────────────────────────── */}
      <main className="max-w-screen-2xl mx-auto px-6 py-6 space-y-6">

        {/* KPI row */}
        <section>
          <p className="text-xs text-slate-500 mb-3 uppercase tracking-wider font-medium">
            Last Hour KPIs — auto-refreshes every 30 s
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <KPICard
              label="Revenue"
              value={kpis ? fmtUSD(kpis.revenue) : "—"}
              sub="from completed orders"
              loading={kpisLoading}
            />
            <KPICard
              label="Orders"
              value={kpis ? fmtCount(kpis.orders) : "—"}
              sub="PAYMENT_SUCCESS events"
              loading={kpisLoading}
            />
            <KPICard
              label="Active Users"
              value={kpis ? fmtCount(kpis.active_users) : "—"}
              sub="distinct user IDs"
              loading={kpisLoading}
            />
            <KPICard
              label="Cart Conversion"
              value={kpis ? `${((kpis.cart_conversion_rate) * 100).toFixed(1)}%` : "—"}
              sub={kpis ? `AOV ${fmtUSD(kpis.avg_order_value)}` : "avg order value"}
              loading={kpisLoading}
            />
          </div>
        </section>

        {/* Charts row */}
        <section className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          <RevenueChart />
          <FunnelChart />
        </section>

        {/* Products + Side metrics */}
        <section className="grid grid-cols-1 xl:grid-cols-3 gap-4">
          <div className="xl:col-span-2">
            <TopProductsTable />
          </div>
          <div className="flex flex-col gap-4">
            <FlinkMetrics />
            <HealthPanel />
          </div>
        </section>

        {/* Live feed */}
        <section>
          <LiveEventFeed />
        </section>
      </main>

      {/* ── Footer ─────────────────────────────────────────────────────────── */}
      <footer className="border-t border-surface-700 px-6 py-3 mt-8">
        <div className="max-w-screen-2xl mx-auto flex items-center justify-between text-xs text-slate-600">
          <span>E-Commerce Realtime Analytics Pipeline</span>
          <span>
            Druid · ClickHouse · Flink · Kafka · Iceberg ·{" "}
            <a href="/docs" className="hover:text-slate-400 transition-colors">API Docs</a>
          </span>
        </div>
      </footer>
    </div>
  );
}
