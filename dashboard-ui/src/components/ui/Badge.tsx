import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

type Variant = "default" | "success" | "error" | "warning" | "info" | "accent" | "outline";

const V: Record<Variant, string> = {
  default: "bg-ink-600 text-slate-300 border-ink-500",
  success: "bg-green-500/10 text-green-400 border-green-500/20",
  error:   "bg-red-500/10 text-red-400 border-red-500/20",
  warning: "bg-yellow-500/10 text-yellow-400 border-yellow-500/20",
  info:    "bg-blue-500/10 text-blue-400 border-blue-500/20",
  accent:  "bg-accent-500/10 text-accent-300 border-accent-500/20",
  outline: "bg-transparent text-slate-400 border-ink-500",
};

interface Props { variant?: Variant; className?: string; children: ReactNode }

export function Badge({ variant = "default", className, children }: Props) {
  return (
    <span className={cn(
      "inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full border",
      V[variant], className
    )}>
      {children}
    </span>
  );
}
