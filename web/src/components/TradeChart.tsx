import { motion } from "motion/react";
import type { TradeVerdict } from "@/lib/api";
import { Term } from "@/components/Explain";

/**
 * Two seasons, drawn on one scale.
 *
 * The range bars said the same thing, but a bar makes you compare two numbers;
 * a curve lets you watch the whole distribution move. That matters because a
 * trade can be good in three different ways — shifting right (more points),
 * narrowing (safer), or fattening the right tail (more upside) — and only the
 * drawn shape distinguishes them.
 *
 * Both curves share an axis, so overlap is the honest picture: heavy overlap
 * IS a fair trade, and no amount of colour should hide it.
 *
 * Every number on it explains itself. A chart whose axis you have to guess at
 * is decoration — "220" means nothing until you are told it is a season total
 * for a whole starting lineup rather than a player, a week, or an average.
 */

const W = 460;
const H = 104;

function density(v: number, floor: number, median: number, ceiling: number): number {
  const sd = v < median
    ? Math.max((median - floor) / 0.84, 1)
    : Math.max((ceiling - median) / 0.84, 1);
  return Math.exp(-0.5 * ((v - median) / sd) ** 2);
}

function path(floor: number, median: number, ceiling: number,
              lo: number, hi: number): string {
  const pts: string[] = [];
  const N = 72;
  for (let i = 0; i <= N; i++) {
    const v = lo + ((hi - lo) * i) / N;
    const x = ((v - lo) / Math.max(hi - lo, 1)) * W;
    const y = H - density(v, floor, median, ceiling) * (H - 6);
    pts.push(`${x.toFixed(1)},${y.toFixed(1)}`);
  }
  return `M0,${H} L${pts.join(" L")} L${W},${H} Z`;
}

/**
 * Where the two curves cross: left of it the old team was ahead, right of it
 * the new one is. Scanned rather than solved — the two half-normals do not
 * have a clean closed form once their spreads differ, and 200 samples across
 * the visible range is accurate to a pixel.
 */
function crossover(v: TradeVerdict, lo: number, hi: number): number | null {
  let prev: number | null = null;
  for (let i = 0; i <= 200; i++) {
    const x = lo + ((hi - lo) * i) / 200;
    const d = density(x, v.after.floor, v.after.median, v.after.ceiling)
            - density(x, v.before.floor, v.before.median, v.before.ceiling);
    if (prev !== null && Math.sign(d) !== Math.sign(prev) && Math.abs(d) > 1e-6) {
      // Only interesting if it lands inside the plausible range; a crossing
      // out in a tail is arithmetic, not a decision point.
      if (x > lo + (hi - lo) * 0.08 && x < hi - (hi - lo) * 0.08) return x;
    }
    prev = d;
  }
  return null;
}

export function TradeChart({ v }: { v: TradeVerdict }) {
  const lo = Math.min(v.before.floor, v.after.floor) * 0.94;
  const hi = Math.max(v.before.ceiling, v.after.ceiling) * 1.06;
  const x = (val: number) => ((val - lo) / Math.max(hi - lo, 1)) * W;
  const cross = crossover(v, lo, hi);

  return (
    <figure className="mx-auto w-full max-w-[560px] rounded-lg border border-line bg-panel p-3">
      <figcaption className="mb-2 flex flex-wrap items-baseline justify-center gap-x-4 gap-y-1">
        <Term k="overlap">
          <span className="eyebrow">
            {v.roster_priced ? "your season, before and after" : "the two sides"}
          </span>
        </Term>
        <span className="flex items-center gap-3 text-[10px]">
          <Term k={v.roster_priced ? "curve_now" : "range"}>
            <span className="flex items-center gap-1 text-muted">
              <span className="h-2 w-2 rounded-full bg-muted/60" />
              {v.roster_priced ? "now" : "you give"}
            </span>
          </Term>
          <Term k={v.roster_priced ? "curve_after" : "range"}>
            <span className="flex items-center gap-1 text-turf">
              <span className="h-2 w-2 rounded-full bg-turf" />
              {v.roster_priced ? "after" : "you get"}
            </span>
          </Term>
        </span>
      </figcaption>

      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img"
           aria-label="season distributions before and after the trade">
        <motion.path
          d={path(v.before.floor, v.before.median, v.before.ceiling, lo, hi)}
          fill="color-mix(in oklab, var(--color-muted) 20%, transparent)"
          stroke="var(--color-muted)" strokeWidth="1.5"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }}
          transition={{ duration: 0.35 }}
        />
        <motion.path
          d={path(v.after.floor, v.after.median, v.after.ceiling, lo, hi)}
          fill="color-mix(in oklab, var(--color-turf) 24%, transparent)"
          stroke="var(--color-turf)" strokeWidth="2"
          initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
          transition={{ type: "spring", stiffness: 200, damping: 26, delay: 0.08 }}
        />
        {cross != null && (
          <line x1={x(cross)} x2={x(cross)} y1={4} y2={H}
                stroke="var(--color-clock)" strokeWidth="1.5"
                strokeDasharray="2 3" opacity={0.85} />
        )}
        <line x1={x(v.before.median)} x2={x(v.before.median)} y1={12} y2={H}
              stroke="var(--color-muted)" strokeWidth="1" strokeDasharray="3 3" />
        <line x1={x(v.after.median)} x2={x(v.after.median)} y1={0} y2={H}
              stroke="var(--color-chalk)" strokeWidth="1.5" />
      </svg>

      <div className="num mt-1 flex items-baseline justify-between text-[10px] text-muted">
        <Term k="season_total"><span>{Math.round(lo)}</span></Term>
        <Term k="season_total">
          <span className="text-chalk">
            {Math.round(v.before.median)} &rarr; {Math.round(v.after.median)}
          </span>
        </Term>
        <Term k="season_total"><span>{Math.round(hi)}</span></Term>
      </div>

      {cross != null && (
        <p className="mt-1.5 text-center text-[10.5px] text-muted">
          <Term k="crossover">
            <span className="text-clock">break-even {Math.round(cross)}</span>
          </Term>{" "}
          — below that season the old team was better, above it this one is.
        </p>
      )}
    </figure>
  );
}
