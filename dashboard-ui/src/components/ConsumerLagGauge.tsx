/**
 * Consumer lag progress bars for each consumer group.
 * In a production system these values would come from a Kafka metrics endpoint.
 * Here we derive approximate lag from Flink throughput metrics.
 */
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { useFlinkMetrics } from "@/hooks/useMetrics";
import { clamp } from "@/lib/utils";

const GROUPS = [
  { id: "flink-ecom-etl",         label: "Flink ETL",         maxLag: 5_000 },
  { id: "clickhouse-ecom-consumer",label: "ClickHouse",        maxLag: 10_000 },
  { id: "druid-ecommerce-events",  label: "Druid Supervisor",  maxLag: 10_000 },
  { id: "connect-iceberg-sink",    label: "Iceberg Connect",   maxLag: 5_000 },
] as const;

function LagBar({ label, lag, maxLag }: { label: string; lag: number; maxLag: number }) {
  const pct   = clamp((lag / maxLag) * 100, 0, 100);
  const color = pct < 20 ? "#22c55e" : pct < 60 ? "#f59e0b" : "#ef4444";

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-slate-400">{label}</span>
        <span className="tabular-nums font-medium" style={{ color }}>
          {lag.toLocaleString()}
        </span>
      </div>
      <div className="h-1.5 bg-surface-700 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
    </div>
  );
}

export default function ConsumerLagGauge() {
  const { data: flink, isLoading } = useFlinkMetrics();

  // Approximate lag: when throughput is high, assume low lag.
  // Real implementation would query Kafka Admin API.
  const tps = flink?.throughput_per_sec ?? 0;
  const estimatedFlinkLag = tps > 0 ? Math.max(0, Math.round(1000 - tps * 0.1)) : 0;

  const lags: Record<string, number> = {
    "flink-ecom-etl":          estimatedFlinkLag,
    "clickhouse-ecom-consumer": Math.round(estimatedFlinkLag * 1.5),
    "druid-ecommerce-events":   Math.round(estimatedFlinkLag * 1.2),
    "connect-iceberg-sink":     Math.round(estimatedFlinkLag * 0.8),
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Consumer Lag</CardTitle>
        <p className="text-xs text-slate-500 mt-0.5">
          Estimated from Flink throughput · max scale per group
        </p>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-3">
            {GROUPS.map((g) => <Skeleton key={g.id} className="h-7" />)}
          </div>
        ) : (
          <div className="space-y-3">
            {GROUPS.map((g) => (
              <LagBar
                key={g.id}
                label={g.label}
                lag={lags[g.id] ?? 0}
                maxLag={g.maxLag}
              />
            ))}
          </div>
        )}
        <p className="text-[10px] text-slate-600 mt-3">
          * Real lag requires a Kafka Admin API endpoint not yet implemented.
        </p>
      </CardContent>
    </Card>
  );
}
