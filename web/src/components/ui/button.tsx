import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const button = cva(
  "inline-flex items-center justify-center gap-2 rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-turf/60 disabled:pointer-events-none disabled:opacity-40",
  {
    variants: {
      variant: {
        default: "bg-turf text-ink hover:bg-turf/85 font-semibold",
        outline: "border border-line bg-transparent hover:bg-raised text-chalk",
        ghost: "hover:bg-raised text-muted hover:text-chalk",
        danger: "bg-alarm/15 text-alarm border border-alarm/30 hover:bg-alarm/25",
      },
      size: { sm: "h-8 px-3", default: "h-9 px-4", lg: "h-11 px-6 text-base" },
    },
    defaultVariants: { variant: "default", size: "default" },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof button> {}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button ref={ref} className={cn(button({ variant, size }), className)} {...props} />
  )
);
Button.displayName = "Button";
