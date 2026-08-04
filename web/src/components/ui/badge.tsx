import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
import type * as React from "react";

const badge = cva(
  "inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider",
  {
    variants: {
      tone: {
        neutral: "bg-raised text-muted border border-line",
        turf: "bg-turf/15 text-turf border border-turf/30",
        clock: "bg-clock/15 text-clock border border-clock/30",
        alarm: "bg-alarm/15 text-alarm border border-alarm/30",
      },
    },
    defaultVariants: { tone: "neutral" },
  }
);

export const Badge = ({ className, tone, ...p }: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badge>) => (
  <span className={cn(badge({ tone }), className)} {...p} />
);
