import { NavLink, Outlet, useLocation } from "react-router-dom";
import {
  Activity,
  BarChart3,
  GitBranch,
  Radio,
  Zap,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useHealth } from "@/hooks/useMetrics";

const NAV = [
  { to: "/",       label: "Overview",      icon: BarChart3, desc: "KPIs & charts"   },
  { to: "/funnel", label: "Funnel",        icon: GitBranch, desc: "Conversion flow"  },
  { to: "/events", label: "Live Events",   icon: Radio,     desc: "Real-time stream" },
  { to: "/health", label: "System Health", icon: Activity,  desc: "Infrastructure"  },
];

const PAGE_TITLE: Record<string, string> = {
  "/":       "Overview",
  "/funnel": "Funnel Analysis",
  "/events": "Live Event Stream",
  "/health": "System Health",
};

export default function Layout() {
  const { data: health } = useHealth();
  const { pathname }     = useLocation();
  const status           = health?.status ?? "unknown";

  const dotClass =
    status === "healthy"   ? "status-up"       :
    status === "degraded"  ? "status-degraded" :
    status === "unhealthy" ? "status-down"      : "bg-slate-600 w-2 h-2 rounded-full";

  const healthLabel =
    status === "healthy"   ? "All systems operational" :
    status === "degraded"  ? "Degraded performance"    :
    status === "unhealthy" ? "Service disruption"      : "Checking…";

  return (
    <div className="flex h-screen bg-ink-950 text-slate-200 overflow-hidden">

      {/* ══ Sidebar ════════════════════════════════════════════════════════════ */}
      <aside className="w-60 shrink-0 flex flex-col bg-ink-900 border-r border-ink-600">

        {/* Logo */}
        <div className="px-5 pt-6 pb-5">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-gradient-accent flex items-center justify-center shadow-glow shrink-0">
              <Zap className="w-4 h-4 text-white" />
            </div>
            <div>
              <p className="text-[13px] font-bold text-white leading-tight tracking-tight">
                EcomAnalytics
              </p>
              <p className="text-[10px] text-slate-500 leading-tight">Realtime Pipeline</p>
            </div>
          </div>
        </div>

        {/* Section label */}
        <p className="px-5 mb-1.5 text-[10px] font-semibold text-slate-600 uppercase tracking-widest">
          Navigation
        </p>

        {/* Nav */}
        <nav className="flex-1 px-3 space-y-0.5">
          {NAV.map(({ to, label, icon: Icon, desc }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                cn(
                  "group flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-all duration-150 relative",
                  isActive
                    ? "bg-accent-500/15 text-accent-300 font-medium"
                    : "text-slate-400 hover:text-slate-200 hover:bg-ink-700"
                )
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <span className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 bg-accent-500 rounded-r-full" />
                  )}
                  <Icon className={cn("w-4 h-4 shrink-0", isActive ? "text-accent-400" : "text-slate-500 group-hover:text-slate-300")} />
                  <div className="min-w-0">
                    <p className="leading-tight">{label}</p>
                    <p className="text-[10px] text-slate-600 group-hover:text-slate-500 transition-colors leading-tight mt-0.5">{desc}</p>
                  </div>
                </>
              )}
            </NavLink>
          ))}
        </nav>

        {/* Divider */}
        <div className="mx-5 border-t border-ink-600 my-3" />

        {/* System status */}
        <div className="px-5 pb-5">
          <div className="flex items-center gap-2 mb-1">
            <span className={dotClass} />
            <p className="text-xs text-slate-400 font-medium">{healthLabel}</p>
          </div>
          <p className="text-[10px] text-slate-600">
            {Object.values(health?.components ?? {}).filter(c => c.status === "up").length} / {Object.keys(health?.components ?? {}).length} services up
          </p>
        </div>
      </aside>

      {/* ══ Main ══════════════════════════════════════════════════════════════ */}
      <div className="flex-1 flex flex-col overflow-hidden">

        {/* Top bar */}
        <header className="h-12 shrink-0 flex items-center justify-between px-6 border-b border-ink-600 bg-ink-900/50 backdrop-blur-sm">
          <div className="flex items-center gap-2 text-sm">
            <span className="text-slate-600">Dashboard</span>
            <span className="text-slate-700">/</span>
            <span className="text-slate-300 font-medium">
              {PAGE_TITLE[pathname] ?? "Overview"}
            </span>
          </div>

          <div className="flex items-center gap-3">
            {/* Live indicator */}
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <span className="live-dot" />
              Live
            </div>
            {/* Version badge */}
            <span className="px-2 py-0.5 bg-ink-700 rounded text-[10px] text-slate-500 font-mono border border-ink-500">
              v2.0
            </span>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto bg-ink-950">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
