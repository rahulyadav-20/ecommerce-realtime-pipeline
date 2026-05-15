import { cn } from "@/lib/utils";
import type { SelectHTMLAttributes } from "react";

interface Props extends SelectHTMLAttributes<HTMLSelectElement> { label?: string }

export function Select({ label, className, children, ...props }: Props) {
  return (
    <label className="flex items-center gap-2">
      {label && <span className="text-xs text-slate-500 whitespace-nowrap">{label}</span>}
      <select
        className={cn(
          "h-8 rounded-lg bg-ink-700 border border-ink-500 text-xs text-slate-300 px-3",
          "focus:outline-none focus:ring-2 focus:ring-accent-500/40 focus:border-accent-500/50",
          "appearance-none cursor-pointer transition-colors hover:border-ink-400",
          className
        )}
        {...props}
      >
        {children}
      </select>
    </label>
  );
}
