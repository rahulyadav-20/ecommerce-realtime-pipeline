import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

interface Props { className?: string; children: ReactNode }

export function Card({ className, children }: Props) {
  return (
    <div className={cn("bg-ink-800 border border-ink-600 rounded-xl shadow-card", className)}>
      {children}
    </div>
  );
}

export function CardHeader({ className, children }: Props) {
  return <div className={cn("px-5 pt-5 pb-3", className)}>{children}</div>;
}

export function CardContent({ className, children }: Props) {
  return <div className={cn("px-5 pb-5", className)}>{children}</div>;
}

export function CardTitle({ className, children }: Props) {
  return (
    <h3 className={cn("text-sm font-semibold text-slate-200 tracking-tight", className)}>
      {children}
    </h3>
  );
}

export function CardDescription({ className, children }: Props) {
  return (
    <p className={cn("text-xs text-slate-500 mt-0.5", className)}>{children}</p>
  );
}
