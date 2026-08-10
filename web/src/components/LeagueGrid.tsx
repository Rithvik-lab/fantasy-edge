import { motion } from "motion/react";
import type { TeamReport } from "@/lib/api";
import { POS_HUE } from "@/components/Charts";
import { Floating, useHover } from "@/components/Floating";
import { cn } from "@/lib/utils";

/**
 * Every team in the league, side by side, once the draft is over.
 *
 * Before the draft "the room" meant what everyone still needs — a forecast of
 * what leaves the board next. Afterwards nothing leaves the board, and the
 * same panel should answer the question that replaced it: who did everyone end
 * up with, and how does that compare to me.
 *
 * Hovering a team gives its shape against yours rather than in the abstract.
 * "They are strong at receiver" is trivia; "they are 40 points better than you
 * at receiver and 60 worse at back" is a trade.
 */
function shape(t: NonNullable<TeamReport["league"]>[0]) {
  const by: Record<string, number> = {};
  for (const p of t.lineup ?? []) {
    by[p.position] = (by[p.position] ?? 0) + (p.projected_points ?? 0);
  }
  return by;
}

function Card({ t, mine, name, max }: {
  t: NonNullable<TeamReport["league"]>[0];
  mine: NonNullable<TeamReport["league"]>[0] | undefined;
  name: string;
  max: number;
}) {
  const { ref, anchor, show, hide, keep } =
    useHover<HTMLDivElement>({ delay: 110, grace: 160 });
  const them = shape(t);
  const us = mine ? shape(mine) : {};
  const positions = [...new Set([...Object.keys(them), ...Object.keys(us)])]
    .sort((a, b) => (them[b] ?? 0) - (them[a] ?? 0));

  return (
    <div
      ref={ref}
      onMouseEnter={show}
      onMouseLeave={hide}
      className={cn(
        "cursor-help rounded-lg border p-2.5 transition-colors",
        t.mine ? "border-turf/45 bg-turf/[0.06]" : "border-line bg-panel hover:border-line/80"
      )}
    >
      <div className="flex items-baseline gap-2">
        <span className={cn("truncate text-[11.5px] font-medium",
          t.mine ? "text-turf" : "text-chalk")}>
          {t.mine ? "You" : name}
        </span>
        <span className="num ml-auto shrink-0 text-[10px] text-muted">
          #{t.rank}
        </span>
      </div>
      <div className="mt-1.5 h-2 rounded-[2px] bg-ink">
        <motion.div
          className={cn("h-2 rounded-[2px]", t.mine ? "bg-turf" : "bg-muted/40")}
          initial={{ width: 0 }}
          animate={{ width: `${(t.starters / max) * 100}%` }}
          transition={{ type: "spring", stiffness: 200, damping: 26 }}
        />
      </div>
      <div className="num mt-1 flex items-baseline justify-between text-[10px] text-muted">
        <span>pick {t.slot}</span>
        <span className="text-chalk">{Math.round(t.starters)}</span>
      </div>

      {anchor && (
        <Floating anchor={anchor} width={264} interactive onEnter={keep} onLeave={hide}>
          <div className="overflow-hidden rounded-lg border border-line bg-raised shadow-2xl">
            <div className="border-b border-line px-2.5 py-1.5">
              <span className="text-[11.5px] font-semibold">
                {t.mine ? "Your team" : name}
              </span>
              <span className="num ml-1.5 text-[10px] text-muted">
                #{t.rank} · {Math.round(t.starters)} projected
              </span>
            </div>

            {!t.mine && mine && (
              <div className="space-y-1 border-b border-line p-2.5">
                <span className="eyebrow">against you, by slot</span>
                {positions.map((pos) => {
                  const d = (them[pos] ?? 0) - (us[pos] ?? 0);
                  if (Math.abs(d) < 5) return null;
                  const w = Math.min(46, Math.abs(d) / 4);
                  return (
                    <div key={pos} className="flex items-center gap-1.5">
                      <span className="w-7 shrink-0 text-[9.5px] font-bold"
                            style={{ color: POS_HUE[pos] ?? "#8CA096" }}>
                        {pos}
                      </span>
                      <div className="relative h-2 flex-1">
                        <div className="absolute inset-y-0 left-1/2 w-px bg-line" />
                        <div className={cn("absolute top-0 h-2 rounded-[2px]",
                          d > 0 ? "bg-alarm/70" : "bg-turf/70")}
                          style={d > 0 ? { left: "50%", width: `${w}%` }
                                       : { right: "50%", width: `${w}%` }} />
                      </div>
                      <span className={cn("num w-9 shrink-0 text-right text-[9.5px]",
                        d > 0 ? "text-alarm" : "text-turf")}>
                        {d > 0 ? "+" : ""}{Math.round(d)}
                      </span>
                    </div>
                  );
                })}
                <p className="pt-1 text-[9.5px] leading-snug text-muted">
                  Red is where they beat you — what to ask for. Green is where
                  you beat them — what they might want.
                </p>
              </div>
            )}

            <ul className="p-1.5">
              {(t.lineup ?? []).map((p) => (
                <li key={p.player_id} className="flex items-center gap-1.5 px-1 py-0.5">
                  <span className="w-6 shrink-0 text-[9.5px] font-bold"
                        style={{ color: POS_HUE[p.position] ?? "#8CA096" }}>
                    {p.position}
                  </span>
                  <span className="flex-1 truncate text-[11px]">{p.player_name}</span>
                  <span className="num text-[10px] text-muted">
                    {Math.round(p.projected_points ?? 0)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </Floating>
      )}
    </div>
  );
}

export function LeagueGrid({ rows, names }: {
  rows: NonNullable<TeamReport["league"]>;
  names?: Record<string, string>;
}) {
  if (!rows.length) return null;
  const max = Math.max(...rows.map((r) => r.starters), 1);
  const mine = rows.find((r) => r.mine);
  const bySeat = [...rows].sort((a, b) => a.slot - b.slot);

  return (
    <section className="space-y-2">
      <header>
        <span className="eyebrow">The room, after the draft</span>
        <p className="mt-0.5 text-[11px] leading-snug text-muted">
          Every roster in draft order. Hover one to see how it lines up against
          yours, slot by slot — which is where a trade comes from.
        </p>
      </header>
      <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-4">
        {bySeat.map((t) => (
          <Card key={t.slot} t={t} mine={mine} max={max}
                name={names?.[String(t.slot)] ?? `Team ${t.slot}`} />
        ))}
      </div>
    </section>
  );
}
