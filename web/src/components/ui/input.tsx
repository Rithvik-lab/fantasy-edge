import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        "h-9 w-full rounded-md border border-line bg-ink px-3 text-sm text-chalk placeholder:text-muted/60",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-turf/50",
        className
      )}
      {...props}
    />
  )
);
Input.displayName = "Input";

export const Field = ({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) => (
  <label className="block space-y-1.5">
    <span className="eyebrow block">{label}</span>
    {children}
    {hint && <span className="block text-[11px] text-muted">{hint}</span>}
  </label>
);
