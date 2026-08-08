import { motion } from "motion/react";
import type { TradeVerdict } from "@/lib/api";

/**
 * Two seasons, drawn on one scale.
 *
 * The bars said the same thing, but a range bar makes you compare two numbers;
 * a curve lets you see the whole distribution move at once. That matters here
 * because a trade can be good in three different ways -- shifting right (more
 * points), narrowing (safer), or fattening the right tail (more upside) -- and
 * only the drawn shape distinguishes them.
 *
 * Both curves share an axis, so overlap is the honest picture: heavy overlap
 * IS a fair trade, and no amount of colour should hide that.
 */
function curve(floor: number, median: number, ceiling: number,
               lo: number, hi: number, w: number, h: number): string {
  // A skew-normal-ish shape reconstructed from the three quantiles we have.
  const x = (v: number) => ((v - lo) / Math.max(hi - lo, 1)) * w;
  const sdL = Math.max((median - floor) / 0.84, 1);
  const sdR = Math.max((ceiling - median) / 0.84, 1);
  const pts: string[] = [];
  const N = 64;
  for (let i = 0; i <= N; i++) {
    const v = lo + ((hi - lo) * i) / N;
    const sd = v < median ? sdL : sdR;
    const y = Math.exp(-0.5 * ((v - median) / sd) ** 2);
    pts.push(`${x(v).toFixed(1)},${(h - y * (h - 4)).toFixed(1)}`);
  }
  return `M0,${h} L${pts.join(" L")} L${w},${h} Z`;
}

export function TradeChart({ v }: { v: TradeVerdict }) {
  const W = 460, H = 96;
  const lo = Math.min(v.before.floor, v.after.floor) * 0.94;
  const hi = Math.max(v.before.ceiling, v.after.ceiling) * 1.06;
  const tick = (val: number) => ((val - lo) / Math.max(hi - lo, 1)) * W;

  return (
    <figure className="rounded-lg border border-line bg-panel p-3">
      <figcaption className="mb-2 flex items-baseline gap-3">
        <span className="eyebrow">
          {v.roster_priced ? "Your season, before and after" : "The two sides"}
        </span>
        <span className="ml-auto flex items-center gap-3 text-[10px]">
          <span className="flex items-center gap-1 text-muted">
            <span className="h-2 w-2 rounded-full bg-muted/50" />
            {v.roster_priced ? "now" : "you give"}
          </span>
          <span className="flex items-center gap-1 text-turf">
            <span className="h-2 w-2 rounded-full bg-turf" />
            {v.roster_priced ? "after" : "you get"}
          </span>
        </span>
      </figcaption>

      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img"
           aria-label="season distributions before and after the trade">
        <motion.path
          d={curve(v.before.floor, v.before.median, v.before.ceiling, lo, hi, W, H)}
          fill="color-mix(in oklab, var(--color-muted) 22%, transparent)"
          stroke="var(--color-muted)" strokeWidth="1.5"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }}
          transition={{ duration: 0.4 }}
        />
        <motion.path
          d={curve(v.after.floor, v.after.median, v.after.ceiling, lo, hi, W, H)}
          fill="color-mix(in oklab, var(--color-turf) 26%, transparent)"
          stroke="var(--color-turf)" strokeWidth="2"
          initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
          transition={{ type: "spring", stiffness: 200, damping: 26, delay: 0.1 }}
        />
        {/* Medians, so the shift has a number attached to it. */}
        <line x1={tick(v.before.median)} x2={tick(v.before.median)} y1={8} y2={H}
              stroke="var(--color-muted)" strokeWidth="1" strokeDasharray="3 3" />
        <line x1={tick(v.after.median)} x2={tick(v.after.median)} y1={0} y2={H}
              stroke="var(--color-chalk)" strokeWidth="1.5" />
      </svg>

      <div className="num mt-1 flex justify-between text-[10px] text-muted">
        <span>{Math.round(lo)}</span>
        <span className="text-chalk">
          {Math.round(v.before.median)} → {Math.round(v.after.median)}
        </span>
        <span>{Math.round(hi)}</span>
      </div>
    </figure>
  );
}
