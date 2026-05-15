import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { CheckCircle, AlertTriangle, XCircle, RefreshCw } from "lucide-react";
import { api } from "@/api/client";
import type { ComponentHealth, HealthStatus } from "@/types";

const DOT: Record<HealthStatus, string> = {
  up:       "status-dot-up",
  degraded: "status-dot-degraded",
  down:     "status-dot-down",
};

const ICON: Record<HealthStatus, React.ReactNode> = {
  up:       <CheckCircle className="w-4 h-4 text-brand-400" />,
  degraded: <AlertTriangle className="w-4 h-4 text-yellow-400" />,
  down:     <XCircle className="w-4 h-4 text-red-400" />,
};

const SERVICE_LABELS: Record<string, string> = {
  druid:      "Apache Druid",
  clickhouse: "ClickHouse",
  flink:      "Apache Flink",
  kafka:      "Kafka",
};

function ServiceRow({ name, health }: { name: string; health: ComponentHealth }) {
  const status = health.status as HealthStatus;
  return (
    <div className="flex items-center justify-between py-3 border-b border-surface-700 last:border-0">
      <div className="flex items-center gap-3">
        <span className={clsx("shrink-0", DOT[status])} />
        <div>
          <p className="text-sm font-medium text-slate-200">{SERVICE_LABELS[name] ?? name}</p>
          {health.detail && (
            <p className="text-[11px] text-slate-500 mt-0.5 truncate max-w-[240px]">{health.detail}</p>
          )}
        </div>
      </div>
      <div className="flex items-center gap-3">
        {health.response_time_ms != null && (
          <span className="text-xs text-slate-500 tabular-nums">{health.response_time_ms} ms</span>
        )}
        {ICON[status]}
      </div>
    </div>
  );
}

export default function HealthPanel() {
  const { data, isLoading, isRefetching, refetch } = useQuery({
    queryKey: ["health"],
    queryFn: api.getHealth,
    refetchInterval: 15_000,
  });

  const overallColor: Record<string, string> = {
    healthy:   "text-brand-400",
    degraded:  "text-yellow-400",
    unhealthy: "text-red-400",
  };

  return (
    <div className="card flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-slate-300">System Health</h2>
          {data && (
            <span className={clsx("text-xs font-semibold capitalize", overallColor[data.status] ?? "text-slate-400")}>
              {data.status}
            </span>
          )}
        </div>
        <button
          onClick={() => refetch()}
          className="p-1 rounded hover:bg-surface-700 text-slate-500 hover:text-slate-300 transition-colors"
          title="Refresh"
        >
          <RefreshCw className={clsx("w-3.5 h-3.5", isRefetching && "animate-spin")} />
        </button>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-10 bg-surface-700 rounded animate-pulse" />
          ))}
        </div>
      ) : (
        <div>
          {Object.entries(data?.components ?? {}).map(([name, health]) => (
            <ServiceRow key={name} name={name} health={health} />
          ))}
        </div>
      )}
    </div>
  );
}
