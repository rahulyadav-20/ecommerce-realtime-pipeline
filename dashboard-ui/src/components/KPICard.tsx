import clsx from "clsx";
import { TrendingDown, TrendingUp } from "lucide-react";

interface KPICardProps {
  label: string;
  value: string;
  trend?: number;    // percentage change (positive = up, negative = down)
  sub?: string;      // secondary line below value
  loading?: boolean;
}

export default function KPICard({ label, value, trend, sub, loading }: KPICardProps) {
  if (loading) {
    return (
      <div className="card flex flex-col gap-3 animate-pulse">
        <div className="h-3 w-24 bg-surface-700 rounded" />
        <div className="h-8 w-32 bg-surface-700 rounded" />
        <div className="h-3 w-16 bg-surface-700 rounded" />
      </div>
    );
  }

  const isUp = trend !== undefined && trend >= 0;

  return (
    <div className="card flex flex-col gap-1.5 hover:border-surface-600 transition-colors">
      <p className="text-xs font-medium text-slate-400 uppercase tracking-wider">{label}</p>
      <p className="text-3xl font-bold text-slate-100 tabular-nums">{value}</p>

      <div className="flex items-center gap-2 mt-1">
        {trend !== undefined && (
          <span
            className={clsx(
              "inline-flex items-center gap-0.5 text-xs font-semibold",
              isUp ? "text-brand-400" : "text-red-400"
            )}
          >
            {isUp ? <TrendingUp className="w-3.5 h-3.5" /> : <TrendingDown className="w-3.5 h-3.5" />}

            {Math.abs(trend).toFixed(1)}%
          </span>
        )}
        {sub && <span className="text-xs text-slate-500">{sub}</span>}
      </div>
    </div>
  );
}
