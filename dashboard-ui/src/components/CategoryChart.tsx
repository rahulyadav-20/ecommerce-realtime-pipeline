/**
 * Horizontal bar chart: top 5 categories by payment count (proxy for revenue rank).
 * Data comes from the funnel endpoint grouped by category.
 */
import {
  Bar,
  BarChart,
  Cell,
  LabelList,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { useFunnel } from "@/hooks/useMetrics";

const BAR_COLORS = ["#22c55e", "#16a34a", "#15803d", "#166534", "#14532d"];

export default function CategoryChart({ period = "7d" }: { period?: string }) {
  const { data, isLoading } = useFunnel(period, "category");

  const chartData = (data?.data ?? [])
    .slice(0, 5)
    .map((row) => ({
      name:     (row.category ?? "unknown").slice(0, 12),
      payments: Number(row.payments) || 0,
      views:    Number(row.views)    || 0,
    }))
    .sort((a, b) => b.payments - a.payments);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Top 5 Categories</CardTitle>
        <p className="text-xs text-slate-500 mt-0.5">By conversions · last {period}</p>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-44 w-full" />
        ) : chartData.length === 0 ? (
          <p className="text-xs text-slate-600 text-center py-10">No data available</p>
        ) : (
          <ResponsiveContainer width="100%" height={180}>
            <BarChart
              data={chartData}
              layout="vertical"
              margin={{ top: 0, right: 48, bottom: 0, left: 0 }}
            >
              <XAxis type="number" hide />
              <YAxis
                type="category"
                dataKey="name"
                tick={{ fill: "#94a3b8", fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                width={72}
              />
              <Bar dataKey="payments" radius={[0, 4, 4, 0]}>
                {chartData.map((_, i) => (
                  <Cell key={i} fill={BAR_COLORS[i % BAR_COLORS.length]} />
                ))}
                <LabelList
                  dataKey="payments"
                  position="right"
                  style={{ fill: "#94a3b8", fontSize: 11 }}
                  formatter={(v: number) => v.toLocaleString()}
                />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
