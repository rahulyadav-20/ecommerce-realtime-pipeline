import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "@/api/client";

const STEP_COLORS = ["#22c55e", "#16a34a", "#15803d", "#166534"];
const STEPS = ["views", "carts", "orders", "payments"] as const;
const STEP_LABELS: Record<string, string> = {
  views: "Views",
  carts: "Add to Cart",
  orders: "Checkout",
  payments: "Payment",
};

export default function FunnelChart() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["funnel", "7d", "category"],
    queryFn: () => api.getFunnel("7d", "category"),
    refetchInterval: 120_000,
  });

  // Aggregate all categories into global funnel steps
  const totals = STEPS.map((step) => ({
    name: STEP_LABELS[step],
    value: (data?.data ?? []).reduce((sum, row) => sum + (Number(row[step]) || 0), 0),
  }));

  // Category breakdown for the bar chart
  const chartData = (data?.data ?? []).slice(0, 8).map((row) => ({
    category: row.category ?? "unknown",
    views: Number(row.views) || 0,
    carts: Number(row.carts) || 0,
    orders: Number(row.orders) || 0,
    payments: Number(row.payments) || 0,
    conversion: ((Number(row.conversion_rate) || 0) * 100).toFixed(1),
  }));

  return (
    <div className="card flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-300">Conversion Funnel — Last 7 d</h2>
        <span className="text-xs text-slate-500">by category</span>
      </div>

      {/* Global funnel steps */}
      <div className="grid grid-cols-4 gap-2">
        {totals.map((step, i) => (
          <div key={step.name} className="flex flex-col items-center gap-1 bg-surface-700/50 rounded-lg p-2">
            <span className="text-[10px] text-slate-400">{step.name}</span>
            <span className="text-lg font-bold" style={{ color: STEP_COLORS[i] }}>
              {step.value.toLocaleString()}
            </span>
          </div>
        ))}
      </div>

      {isError && <p className="text-xs text-red-400">Failed to load funnel data.</p>}
      {isLoading ? (
        <div className="h-36 bg-surface-700 rounded animate-pulse" />
      ) : (
        <ResponsiveContainer width="100%" height={150}>
          <BarChart data={chartData} margin={{ top: 0, right: 4, bottom: 0, left: 0 }}>
            <XAxis dataKey="category" tick={{ fill: "#94a3b8", fontSize: 10 }} tickLine={false} axisLine={false} />
            <YAxis hide />
            <Tooltip
              contentStyle={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 8, fontSize: 11 }}
              labelStyle={{ color: "#94a3b8" }}
              formatter={(v: number, name: string) => [v.toLocaleString(), STEP_LABELS[name] ?? name]}
            />
            {STEPS.map((step, i) => (
              <Bar key={step} dataKey={step} fill={STEP_COLORS[i]} radius={[2, 2, 0, 0]}>
                {chartData.map((_, idx) => (
                  <Cell key={idx} fill={STEP_COLORS[i]} />
                ))}
              </Bar>
            ))}
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
