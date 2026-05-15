import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { CheckCircle, Clock, Cpu, Database, XCircle } from "lucide-react";
import { api } from "@/api/client";

function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="flex items-center gap-3 py-2.5 border-b border-surface-700 last:border-0">
      <span className="text-slate-500 shrink-0">{icon}</span>
      <span className="text-xs text-slate-400 flex-1">{label}</span>
      <span className="text-sm font-semibold text-slate-200 tabular-nums">{value}</span>
    </div>
  );
}

export default function FlinkMetrics() {
  const { data, isLoading } = useQuery({
    queryKey: ["flink-metrics"],
    queryFn: api.getFlinkMetrics,
    refetchInterval: 15_000,
  });

  const isRunning = data?.status === "RUNNING";
  const cpDuration = data?.last_checkpoint?.duration_ms;

  return (
    <div className="card flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-300">Flink ETL Job</h2>
        {data && (
          <span
            className={clsx(
              "flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full",
              isRunning ? "text-brand-400 bg-brand-400/10" : "text-red-400 bg-red-400/10"
            )}
          >
            {isRunning ? <CheckCircle className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
            {data.status}
          </span>
        )}
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {[1, 2, 3, 4].map((i) => <div key={i} className="h-9 bg-surface-700 rounded animate-pulse" />)}
        </div>
      ) : (
        <div>
          <Metric
            icon={<Clock className="w-4 h-4" />}
            label="Uptime"
            value={`${data?.uptime_hours?.toFixed(1) ?? "—"} h`}
          />
          <Metric
            icon={<Cpu className="w-4 h-4" />}
            label="Throughput"
            value={`${(data?.throughput_per_sec ?? 0).toLocaleString()} rec/s`}
          />
          <Metric
            icon={<Database className="w-4 h-4" />}
            label="Records Processed"
            value={(data?.records_processed ?? 0).toLocaleString()}
          />
          <Metric
            icon={<CheckCircle className="w-4 h-4" />}
            label="Last Checkpoint"
            value={
              data?.last_checkpoint
                ? `${data.last_checkpoint.status} · ${(cpDuration! / 1000).toFixed(1)} s`
                : "—"
            }
          />
        </div>
      )}

      {data?.job_id && (
        <p className="text-[10px] text-slate-600 font-mono truncate mt-1">
          Job ID: {data.job_id}
        </p>
      )}
    </div>
  );
}
