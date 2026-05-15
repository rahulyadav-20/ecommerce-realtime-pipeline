import { cn } from "@/lib/utils";
import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "ghost" | "outline" | "danger";
type Size    = "sm" | "md" | "icon";

const VCls: Record<Variant, string> = {
  primary:   "bg-accent-500 hover:bg-accent-600 text-white shadow-glow",
  secondary: "bg-ink-700 hover:bg-ink-600 text-slate-200 border border-ink-500",
  ghost:     "hover:bg-ink-700 text-slate-400 hover:text-slate-200",
  outline:   "border border-ink-500 hover:border-ink-400 hover:bg-ink-700 text-slate-300",
  danger:    "bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/20",
};

const SCls: Record<Size, string> = {
  sm:   "h-7 px-3 text-xs",
  md:   "h-9 px-4 text-sm",
  icon: "h-8 w-8",
};

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?:    Size;
  children: ReactNode;
}

export function Button({ variant = "secondary", size = "md", className, children, ...props }: Props) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-all",
        "focus:outline-none focus:ring-2 focus:ring-accent-500/40",
        "disabled:opacity-50 disabled:cursor-not-allowed",
        VCls[variant], SCls[size], className
      )}
      {...props}
    >
      {children}
    </button>
  );
}
