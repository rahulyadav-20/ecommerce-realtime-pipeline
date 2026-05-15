import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

const fmtUSD = (v: number) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(v);

export default function TopProductsTable() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["top-products", 10, "30d"],
    queryFn: () => api.getTopProducts(10, "30d"),
    refetchInterval: 120_000,
  });

  return (
    <div className="card flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-300">Top 10 Products — Last 30 d</h2>
        <span className="text-xs text-slate-500">by revenue</span>
      </div>

      {isError && <p className="text-xs text-red-400">Failed to load product data.</p>}

      <div className="overflow-auto rounded-lg border border-surface-700">
        {isLoading ? (
          <div className="h-48 bg-surface-700 animate-pulse" />
        ) : (
          <table className="w-full text-left">
            <thead className="bg-surface-700/60 border-b border-surface-700">
              <tr>
                {["#", "Product ID", "Revenue", "Units", "Avg Price"].map((h, i) => (
                  <th
                    key={h}
                    className="py-2 px-3 text-[10px] font-semibold text-slate-500 uppercase tracking-wider"
                    style={{ textAlign: i > 1 ? "right" : "left" }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(data?.data ?? []).map((product, idx) => (
                <tr
                  key={product.product_id}
                  className="border-b border-surface-700 last:border-0 hover:bg-surface-700/40 transition-colors"
                >
                  <td className="py-2 px-3 text-xs text-slate-500 tabular-nums">{idx + 1}</td>
                  <td className="py-2 px-3 text-xs font-mono text-slate-300">{product.product_id}</td>
                  <td className="py-2 px-3 text-xs font-semibold text-brand-400 tabular-nums text-right">
                    {fmtUSD(product.revenue)}
                  </td>
                  <td className="py-2 px-3 text-xs text-slate-400 tabular-nums text-right">
                    {product.units_sold.toLocaleString()}
                  </td>
                  <td className="py-2 px-3 text-xs text-slate-400 tabular-nums text-right">
                    {fmtUSD(product.avg_price)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
