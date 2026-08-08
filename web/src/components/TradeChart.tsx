import { useRef, useState } from "react";
import { motion } from "motion/react";
import type { TradeVerdict } from "@/lib/api";
import { Term } from "@/components/Explain";

/**
 * Two seasons, drawn on one scale, and readable at any point on it.
 *
 * A range bar makes you compare two numbers. A curve lets you watch the whole
 * distribution move — which matters because a trade can be good three ways:
 * shifting right (more points), narrowing (safer), or fattening the right tail
 * (more upside). Only the drawn shape distinguishes them.
 *
 * Hovering reads the CUMULATIVE probability off both curves, integrated from
 * the simulation itself rather than from a fitted shape. "How often does the
 * season come in under 1,850, before and after" is the question a manager
 * actually has, and the difference between those two percentages is the thing
 * the trade bought or sold at that threshold.
 *
 * Both curves share an axis, so overlap is the honest picture: heavy overlap
 * IS a fair trade and no colour choice should hide it.
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

/** Where the curves cross — the season at which this trade starts paying. */
function crossover(v: TradeVerdict, lo: number, hi: number): number | null {
  let prev: number | null = null;
  for (let i = 0; i <= 200; i++) {
    const x = lo + ((hi - lo) * i) / 200;
    const d = density(x, v.after.floor, v.after.median, v.after.ceiling)
            - density(x, v.before.floor, v.before.median, v.before.ceiling);
    if (prev !== null && Math.sign(d) !== Math.sign(prev) && Math.abs(d) > 1e-6) {
      if (x > lo + (hi - lo) * 0.08 && x < hi - (hi - lo) * 0.08) return x;
    }
    prev = d;
  }
  return null;
}

export function TradeChart({ v }: { v: TradeVerdict }) {
  const svg = useRef<SVGSVGElement>(null);
  const [at, setAt] = useState<number | null>(null);

  const lo = Math.min(v.before.floor, v.after.floor) * 0.94;
  const hi = Math.max(v.before.ceiling, v.after.ceiling) * 1.06;
  const x = (val: number) => ((val - lo) / Math.max(hi - lo, 1)) * W;
  const cross = crossover(v, lo, hi);
  const o = v.overlap;

  /** Read both cumulative curves at the pointer, off the simulated grid. */
  function readout(value: number) {
    if (!o?.grid?.length) return null;
    let i = 0;
    for (let j = 1; j < o.grid.length; j++) {
      if (Math.abs(o.grid[j] - value) < Math.abs(o.grid[i] - value)) i = j;
    }
    return {
      at: o.grid[i],
      before: o.cdf_before?.[i] ?? 0,
      after: o.cdf_after?.[i] ?? 0,
    };
  }

  const r = at != null ? readout(at) : null;

  return (
    <figure className="mx-auto w-full max-w-[560px] rounded-lg border border-line bg-panel p-3">
      <figcaption className="mb-2 flex flex-wrap items-baseline justify-center gap-x-4 gap-y-1">
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
        {o?.overlap != null && (
          <Term k="overlap">
            <span className="num text-[10px] text-muted">
              {Math.round(o.overlap * 100)}% the same season
            </span>
          </Term>
        )}
      </figcaption>

      <svg
        ref={svg}
        viewBox={`0 0 ${W} ${H}`}
        className="w-full cursor-crosshair"
        role="img"
        aria-label="season distributions before and after the trade"
        onMouseMove={(e) => {
          const box = svg.current?.getBoundingClientRect();
          if (!box) return;
          const frac = (e.clientX - box.left) / box.width;
          setAt(lo + (hi - lo) * Math.min(1, Math.max(0, frac)));
        }}
        onMouseLeave={() => setAt(null)}
      >
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
        {at != null && (
          <line x1={x(at)} x2={x(at)} y1={0} y2={H}
                stroke="var(--color-chalk)" strokeWidth="1" opacity={0.45} />
        )}
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

      {/* Fixed-height so the figure never jumps as the pointer moves across. */}
      <div className="mt-1.5 min-h-[34px] border-t border-line pt-1.5 text-center">
        {r ? (
          <p className="num text-[10.5px] leading-snug text-muted">
            under <span className="text-chalk">{Math.round(r.at)}</span> points:{" "}
            <span className="text-muted">{Math.round(r.before * 100)}%</span> of
            seasons now &rarr;{" "}
            <span className="text-turf">{Math.round(r.after * 100)}%</span> after
            <span className={
              r.after - r.before <= 0 ? "ml-1.5 text-turf" : "ml-1.5 text-alarm"}>
              ({r.after - r.before <= 0 ? "−" : "+"}
              {Math.abs(Math.round((r.after - r.before) * 100))} pts of risk)
            </span>
          </p>
        ) : cross != null ? (
          <p className="text-[10.5px] text-muted">
            <Term k="crossover">
              <span className="text-clock">break-even {Math.round(cross)}</span>
            </Term>{" "}
            — hover the chart to read any point
          </p>
        ) : (
          <p className="text-[10.5px] text-muted">hover the chart to read any point</p>
        )}
      </div>
    </figure>
  );
}
