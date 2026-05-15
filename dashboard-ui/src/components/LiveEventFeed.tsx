import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { format, parseISO } from "date-fns";
import { api } from "@/api/client";
import type { LiveEvent } from "@/types";

const TYPE_COLORS: Record<string, string> = {
  PAYMENT_SUCCESS:  "text-brand-400 bg-brand-400/10",
  PAYMENT_FAILED:   "text-red-400 bg-red-400/10",
  ADD_TO_CART:      "text-blue-400 bg-blue-400/10",
  CHECKOUT_START:   "text-purple-400 bg-purple-400/10",
  PRODUCT_VIEW:     "text-slate-300 bg-slate-700",
  PAGE_VIEW:        "text-slate-400 bg-slate-800",
  SEARCH:           "text-yellow-400 bg-yellow-400/10",
  REMOVE_FROM_CART: "text-orange-400 bg-orange-400/10",
};

function EventRow({ event }: { event: LiveEvent }) {
  const colorClass = TYPE_COLORS[event.event_type] ?? "text-slate-300 bg-slate-700";
  const ts = (() => {
    try { return format(parseISO(event.timestamp), "HH:mm:ss"); }
    catch { return event.timestamp.slice(11, 19); }
  })();

  return (
    <tr className="border-b border-surface-700 hover:bg-surface-700/40 transition-colors">
      <td className="py-2 pl-3 pr-2 text-[11px] text-slate-500 tabular-nums whitespace-nowrap">{ts}</td>
      <td className="py-2 px-2">
        <span className={clsx("text-[10px] font-semibold px-2 py-0.5 rounded-full whitespace-nowrap", colorClass)}>
          {event.event_type}
        </span>
      </td>
      <td className="py-2 px-2 text-[11px] text-slate-400 font-mono">{event.user_id.slice(0, 12)}</td>
      <td className="py-2 px-2 text-[11px] text-slate-400 font-mono">{event.product_id?.slice(0, 12) ?? "—"}</td>
      <td className="py-2 pl-2 pr-3 text-[11px] text-slate-300 tabular-nums text-right">
        {event.price != null ? `$${Number(event.price).toFixed(2)}` : "—"}
      </td>
    </tr>
  );
}

export default function LiveEventFeed() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["live-events", 100],
    queryFn: () => api.getLiveEvents(100),
    refetchInterval: 5_000,
  });

  return (
    <div className="card flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-300">Live Event Feed</h2>
        <span className="flex items-center gap-1.5 text-xs text-slate-500">
          <span className="w-1.5 h-1.5 rounded-full bg-brand-500 animate-pulse-slow" />
          {data?.count ?? 0} events · refreshes 5 s
        </span>
      </div>

      {isError && <p className="text-xs text-red-400">Failed to load events.</p>}

      <div className="overflow-auto max-h-64 rounded-lg border border-surface-700">
        {isLoading ? (
          <div className="h-48 bg-surface-700 animate-pulse" />
        ) : (
          <table className="w-full text-left">
            <thead className="sticky top-0 bg-surface-800 border-b border-surface-700">
              <tr>
                {["Time", "Type", "User", "Product", "Price"].map((h) => (
                  <th key={h} className="py-2 px-2 text-[10px] font-semibold text-slate-500 uppercase tracking-wider first:pl-3 last:pr-3 last:text-right">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(data?.data ?? []).map((ev) => (
                <EventRow key={ev.event_id} event={ev} />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
