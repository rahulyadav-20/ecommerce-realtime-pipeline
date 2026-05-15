/**
 * Pie chart: event type distribution derived from funnel data.
 * Uses the global funnel totals (views, carts, orders, payments) as pie slices.
 */
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { useGlobalFunnel } from "@/hooks/useMetrics";

const SLICES = [
  { key: "views",    label: "Product Views",   color: "#3b82f6" },
  { key: "carts",    label: "Add to Cart",      color: "#8b5cf6" },
  { key: "orders",   label: "Checkout Start",   color: "#f59e0b" },
  { key: "payments", label: "Payment Success",  color: "#22c55e" },
] as const;

export default function EventTypeChart({ period = "7d" }: { period?: string }) {
  const { totals, isLoading } = useGlobalFunnel(period);

  const data = totals
    ? SLICES.map(({ key, label, color }) => ({
        name: label,
        value: totals[key],
        color,
      }))
    : [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Event Distribution</CardTitle>
        <p className="text-xs text-slate-500 mt-0.5">Last {period}</p>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-44 w-full" />
        ) : (
          <ResponsiveContainer width="100%" height={180}>
            <PieChart>
              <Pie
                data={data}
                cx="50%"
                cy="50%"
                innerRadius={50}
                outerRadius={80}
                paddingAngle={3}
                dataKey="value"
              >
                {data.map((entry, i) => (
                  <Cell key={i} fill={entry.color} strokeWidth={0} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{
                  background: "#1e293b",
                  border: "1px solid #334155",
                  borderRadius: 8,
                  fontSize: 12,
                }}
                formatter={(v: number, name: string) => [
                  v.toLocaleString(),
                  name,
                ]}
              />
            </PieChart>
          </ResponsiveContainer>
        )}

        {/* Legend */}
        {!isLoading && (
          <div className="grid grid-cols-2 gap-y-1.5 mt-2">
            {SLICES.map(({ label, color }, i) => (
              <div key={i} className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: color }} />
                <span className="text-[11px] text-slate-400 truncate">{label}</span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
