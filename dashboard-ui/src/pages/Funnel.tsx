import { useState } from "react";
import { ArrowRight, TrendingDown } from "lucide-react";
import { Skeleton } from "@/components/ui/Skeleton";
import { Select }   from "@/components/ui/Select";
import { Badge }    from "@/components/ui/Badge";
import { useFunnel, useGlobalFunnel } from "@/hooks/useMetrics";
import { fmtCount, fmtPct } from "@/lib/utils";
import type { FunnelStep } from "@/types";

// ── Stage config ──────────────────────────────────────────────────────────────
const STAGES = [
  { key: "views" as const,    label: "Product Views",  color: "#3b82f6", bg: "bg-blue-500/10",   text: "text-blue-400"   },
  { key: "carts" as const,    label: "Add to Cart",    color: "#8b5cf6", bg: "bg-purple-500/10", text: "text-purple-400" },
  { key: "orders" as const,   label: "Checkout",       color: "#eab308", bg: "bg-yellow-500/10", text: "text-yellow-400" },
  { key: "payments" as const, label: "Payment",        color: "#22c55e", bg: "bg-green-500/10",  text: "text-green-400"  },
];

// ── Global funnel visualization ───────────────────────────────────────────────
function FunnelViz({ period }: { period: string }) {
  const { totals, isLoading } = useGlobalFunnel(period);

  if (isLoading) return <Skeleton className="h-64 w-full bg-ink-700" />;
  if (!totals) return (
    <div className="h-40 flex items-center justify-center text-sm text-slate-600">
      No funnel data available
    </div>
  );

  const values = [totals.views, totals.carts, totals.orders, totals.payments];
  const maxVal  = values[0] || 1;
  const drops   = [null, totals.cart_rate, totals.order_rate, totals.payment_rate];

  return (
    <div className="space-y-2">
      {STAGES.map(({ key, label, color, bg, text }, i) => {
        const val = totals[key];
        const pct = (val / maxVal) * 100;
        const dropRate = drops[i];

        return (
          <div key={key}>
            {/* Drop arrow between stages */}
            {dropRate !== null && i > 0 && (
              <div className="flex items-center gap-2 py-1.5 ml-4">
                <TrendingDown className="w-3 h-3 text-slate-600" />
                <span className="text-xs text-slate-600">
                  {fmtPct(dropRate)} converted  ·  {fmtPct(1 - dropRate)} dropped
                </span>
              </div>
            )}

            <div className="flex items-center gap-4">
              {/* Stage icon */}
              <div className={`w-10 h-10 rounded-lg ${bg} flex items-center justify-center shrink-0`}>
                <span className={`text-sm font-bold ${text}`}>{i + 1}</span>
              </div>

              {/* Bar + label */}
              <div className="flex-1">
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-sm font-medium text-slate-300">{label}</span>
                  <div className="flex items-center gap-3">
                    <span className="text-sm font-bold text-white tabular-nums">{fmtCount(val)}</span>
                    <span className={`text-xs tabular-nums ${text}`}>{pct.toFixed(1)}%</span>
                  </div>
                </div>
                <div className="h-2.5 bg-ink-600 rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-1000"
                    style={{ width: `${Math.max(pct, 0.5)}%`, background: color }}
                  />
                </div>
              </div>
            </div>
          </div>
        );
      })}

      {/* Overall conversion */}
      <div className="mt-6 pt-4 border-t border-ink-600 flex items-center justify-between">
        <span className="text-sm text-slate-400">Overall conversion (View → Payment)</span>
        <span className="text-lg font-bold text-green-400">{fmtPct(totals.overall_rate)}</span>
      </div>
    </div>
  );
}

// ── Category table ────────────────────────────────────────────────────────────
type SortKey = keyof Pick<FunnelStep, "views" | "carts" | "orders" | "payments" | "conversion_rate">;

function CategoryTable({ rows, loading }: { rows: FunnelStep[]; loading: boolean }) {
  const [sort, setSort] = useState<SortKey>("payments");

  const sorted = [...rows].sort((a, b) => (Number(b[sort]) || 0) - (Number(a[sort]) || 0));

  const cols: { key: SortKey; label: string }[] = [
    { key: "views",           label: "Views"     },
    { key: "carts",           label: "Carts"     },
    { key: "orders",          label: "Checkout"  },
    { key: "payments",        label: "Payments"  },
    { key: "conversion_rate", label: "Conv %"    },
  ];

  return (
    <div className="overflow-auto rounded-xl border border-ink-600">
      {loading ? <Skeleton className="h-48 rounded-none bg-ink-700" /> : (
        <table className="data-table">
          <thead>
            <tr>
              <th className="text-left">Category</th>
              {cols.map(({ key, label }) => (
                <th
                  key={key}
                  onClick={() => setSort(key)}
                  className={`cursor-pointer select-none text-right transition-colors ${sort === key ? "text-accent-400" : "hover:text-slate-300"}`}
                >
                  {label} {sort === key && "↓"}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((row, i) => (
              <tr key={i}>
                <td className="font-medium text-slate-200">{row.category || "unknown"}</td>
                {(["views", "carts", "orders", "payments"] as const).map((k) => (
                  <td key={k} className="text-right tabular-nums text-slate-400">
                    {(Number(row[k]) || 0).toLocaleString()}
                  </td>
                ))}
                <td className="text-right">
                  <Badge variant="success">{fmtPct(Number(row.conversion_rate) || 0)}</Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────
export default function FunnelPage() {
  const [period, setPeriod] = useState("7d");
  const { data, isLoading } = useFunnel(period, "category");

  return (
    <div className="p-6 space-y-6 animate-fade-in">

      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-lg font-bold text-white">Funnel Analysis</h1>
          <p className="text-xs text-slate-500 mt-0.5">Track conversion from product discovery to payment</p>
        </div>
        <div className="flex items-center gap-3">
          <Select label="Period" value={period} onChange={e => setPeriod(e.target.value)}>
            <option value="1d">Last 24 h</option>
            <option value="7d">Last 7 days</option>
            <option value="30d">Last 30 days</option>
          </Select>
        </div>
      </div>

      {/* Two-column layout */}
      <div className="grid grid-cols-1 xl:grid-cols-5 gap-6">

        {/* Funnel viz */}
        <div className="xl:col-span-2 bg-ink-800 border border-ink-600 rounded-xl p-5 shadow-card">
          <p className="section-title mb-1">Conversion Funnel</p>
          <p className="text-xs text-slate-500 mb-5">All categories · last {period}</p>
          <FunnelViz period={period} />
        </div>

        {/* Stage summary cards */}
        <div className="xl:col-span-3 space-y-4">
          {/* Mini stat row */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {STAGES.map(({ key, label, bg, text, color }) => {
              const { totals } = useGlobalFunnel(period);
              const val = totals?.[key] ?? 0;
              return (
                <div key={key} className="bg-ink-800 border border-ink-600 rounded-xl p-4 shadow-card">
                  <div className={`w-8 h-8 ${bg} rounded-lg flex items-center justify-center mb-2`}>
                    <ArrowRight className={`w-3.5 h-3.5 ${text}`} />
                  </div>
                  <p className="text-xl font-bold text-white tabular-nums">{fmtCount(val)}</p>
                  <p className="text-[10px] text-slate-500 mt-0.5 uppercase tracking-wider">{label}</p>
                </div>
              );
            })}
          </div>

          {/* Category table */}
          <div className="bg-ink-800 border border-ink-600 rounded-xl shadow-card">
            <div className="px-5 pt-4 pb-3 border-b border-ink-600 flex items-center justify-between">
              <p className="section-title">Category Breakdown</p>
              <p className="text-xs text-slate-600">Click headers to sort</p>
            </div>
            <div className="p-3">
              <CategoryTable rows={data?.data ?? []} loading={isLoading} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
