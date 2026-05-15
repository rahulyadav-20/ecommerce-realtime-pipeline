import { useQuery } from "@tanstack/react-query";
import { format, parseISO } from "date-fns";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "@/api/client";

const fmtRevenue = (v: number) =>
  v >= 1_000_000
    ? `$${(v / 1_000_000).toFixed(1)}M`
    : v >= 1_000
    ? `$${(v / 1_000).toFixed(1)}k`
    : `$${v.toFixed(0)}`;

export default function RevenueChart() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["revenue-ts", "24h", "5m"],
    queryFn: () => api.getRevenueTimeseries("24h", "5m"),
    refetchInterval: 30_000,
  });

  const chartData = (data?.data ?? []).map((p) => ({
    ts: format(parseISO(p.timestamp), "HH:mm"),
    revenue: p.revenue,
  }));

  return (
    <div className="card flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-300">Revenue — Last 24 h</h2>
        <span className="text-xs text-slate-500">5-min buckets · refreshes 30 s</span>
      </div>

      {isError && (
        <p className="text-xs text-red-400">Failed to load timeseries data.</p>
      )}

      {isLoading ? (
        <div className="h-48 bg-surface-700 rounded animate-pulse" />
      ) : (
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#334155" vertical={false} />
            <XAxis
              dataKey="ts"
              tick={{ fill: "#94a3b8", fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              interval={23}
            />
            <YAxis
              tickFormatter={fmtRevenue}
              tick={{ fill: "#94a3b8", fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={56}
            />
            <Tooltip
              contentStyle={{
                background: "#1e293b",
                border: "1px solid #334155",
                borderRadius: 8,
                fontSize: 12,
              }}
              formatter={(v: number) => [fmtRevenue(v), "Revenue"]}
              labelStyle={{ color: "#94a3b8" }}
            />
            <Line
              type="monotone"
              dataKey="revenue"
              stroke="#22c55e"
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, fill: "#22c55e" }}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
