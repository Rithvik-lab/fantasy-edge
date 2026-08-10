import { motion } from "motion/react";
import type { TeamReport } from "@/lib/api";
import { POS_HUE } from "@/components/Charts";
import { Floating, useHover } from "@/components/Floating";
import { PlayerHover } from "@/components/PlayerHover";
import { cn } from "@/lib/utils";

/**
 * WHERE YOU STAND, by seat, with the evidence one hover away.
 *
 * Ordered by draft slot rather than by rank, so the column reads as the room
 * you sat in — pick 1 through pick 12 — and your position inside it is spatial
 * rather than something you have to look up. Rank is still stated; it is just
 * not what the list is sorted on.
 *
 * Hovering a team shows who they actually start. A bar saying someone is ahead
 * of you is an assertion; their lineup is the evidence, and it is the next
 * thing you would go looking for anyway.
 */
function TeamBar({ t, max, name }: {
  t: NonNullable<TeamReport["league"]>[0]; max: number; name: string;
}) {
  const { ref, anchor, show, hide, keep } = useHover({ delay: 120, grace: 160 });
  return (
    <span ref={ref} onMouseEnter={show} onMouseLeave={hide}
          className="flex cursor-help items-center gap-2">
      <span className={cn("num w-11 shrink-0 text-[10px]",
        t.mine ? "font-semibold text-turf" : "text-muted")}>
        {t.mine ? "you" : `pick ${t.slot}`}
      </span>
      <span className="relative h-3.5 flex-1 overflow-hidden rounded-[2px] bg-ink">
        <motion.span
          className={cn("absolute inset-y-0 left-0 block rounded-[2px]",
            t.mine ? "bg-turf" : "bg-muted/35")}
          initial={{ width: 0 }}
          animate={{ width: `${(t.starters / max) * 100}%` }}
          transition={{ type: "spring", stiffness: 190, damping: 26 }}
        />
      </span>
      <span className={cn("num w-11 shrink-0 text-right text-[10.5px]",
        t.mine ? "font-semibold text-chalk" : "text-muted")}>
        {Math.round(t.starters)}
      </span>
      {anchor && (
        <Floating anchor={anchor} width={252} interactive onEnter={keep} onLeave={hide}>
          <div className="overflow-hidden rounded-lg border border-line bg-raised shadow-2xl">
            <div className="flex items-baseline gap-2 border-b border-line px-2.5 py-1.5">
              <span className="text-[11.5px] font-semibold">
                {t.mine ? "Your team" : name}
              </span>
              <span className="num ml-auto text-[10px] text-muted">
                #{t.rank} · {Math.round(t.starters)}
              </span>
            </div>
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
            {t.floor != null && (
              <p className="num border-t border-line px-2.5 py-1 text-[10px] text-muted">
                season {Math.round(t.floor)}–{Math.round(t.ceiling ?? 0)}
              </p>
            )}
          </div>
        </Floating>
      )}
    </span>
  );
}

export function Standing({ rows, names }: {
  rows: NonNullable<TeamReport["league"]>;
  names?: Record<string, string>;
}) {
  if (rows.length < 2) return null;
  const max = Math.max(...rows.map((r) => r.starters), 1);
  const me = rows.find((r) => r.mine);
  const bySeat = [...rows].sort((a, b) => a.slot - b.slot);

  return (
    <section className="rounded-lg border border-line bg-panel p-3">
      <header className="mb-2.5">
        <span className="eyebrow">Where you stand</span>
        <p className="mt-0.5 text-[11px] leading-snug text-muted">
          {me && <>You are <span className="font-semibold text-chalk">
            {me.rank}{me.rank === 1 ? "st" : me.rank === 2 ? "nd"
              : me.rank === 3 ? "rd" : "th"}</span> of {rows.length}. </>}
          Hover a team to see who they start.
        </p>
      </header>

      <div className="space-y-1">
        {bySeat.map((t) => (
          <TeamBar key={t.slot} t={t} max={max}
                   name={names?.[String(t.slot)] ?? `Team ${t.slot}`} />
        ))}
      </div>

      {/* The number is a projection, and projections at this range are close
          together. Saying so is the difference between a standing and a
          scoreboard. */}
      <p className="mt-2.5 border-t border-line pt-2 text-[10px] leading-snug text-muted">
        Projected, not played. These are pre-season expectations and the gaps
        between neighbouring teams are usually smaller than a single week&rsquo;s
        swing — treat the order as a guide, not a table.
      </p>
    </section>
  );
}

/**
 * WHAT TO DO ABOUT IT.
 *
 * A weakness with no move attached is a complaint. Each one names the men
 * still unowned at that position, because in the week after a draft that is
 * the only question that has an answer.
 */
export function Improve({ rows }: { rows: NonNullable<TeamReport["improve"]> }) {
  if (!rows.length) return null;
  return (
    <section className="rounded-lg border border-clock/25 bg-clock/[0.04] p-3">
      <header className="mb-2">
        <span className="eyebrow text-clock/80">what to fix first</span>
        <p className="mt-0.5 text-[11px] leading-snug text-muted">
          Your weakest slots against the league, and who is still unowned there.
        </p>
      </header>
      <ul className="space-y-2.5">
        {rows.map((r) => (
          <li key={r.position}>
            <div className="flex items-baseline gap-2">
              <span className="text-[11px] font-bold"
                    style={{ color: POS_HUE[r.position] ?? "#8CA096" }}>
                {r.position}
              </span>
              <span className="num text-[10.5px] text-alarm">
                {Math.round(r.edge)}
              </span>
              <span className="text-[10.5px] text-muted">
                {Math.round(r.percentile * 100)}th percentile
              </span>
            </div>
            {r.available.length > 0 && (
              <ul className="mt-1 space-y-0.5 pl-1">
                {r.available.map((p) => (
                  <li key={p.player_id} className="flex items-baseline gap-1.5">
                    <span className="text-turf/70">+</span>
                    <PlayerHover playerId={p.player_id} className="min-w-0 flex-1">
                      <span className="block cursor-help truncate text-[11px] text-chalk/85">
                        {p.player_name}
                      </span>
                    </PlayerHover>
                    <span className="num text-[10px] text-muted">
                      {Math.round(p.projected_points ?? 0)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * WHERE YOUR DRAFT DISAGREED WITH THE ROOM.
 *
 * Not who is best on your roster — that is the roster. This is value above
 * what a player at that ADP normally returns, which is the only part of a
 * draft capable of beating the market rather than matching it.
 */
export function Sleepers({ rows }: { rows: NonNullable<TeamReport["sleepers"]> }) {
  if (!rows.length) return null;
  const max = Math.max(...rows.map((r) => r.market_edge), 1);
  return (
    <section className="rounded-lg border border-line bg-panel p-3">
      <header className="mb-2.5">
        <span className="eyebrow">Where you beat the room</span>
        <p className="mt-0.5 text-[11px] leading-snug text-muted">
          Value above what a player at that ADP normally returns.
        </p>
      </header>
      <ul className="space-y-1.5">
        {rows.map((r) => (
          <li key={r.player_id} className="flex items-center gap-2">
            <PlayerHover playerId={r.player_id} className="w-[112px] min-w-0 shrink-0">
              <span className="block cursor-help truncate text-[11.5px]">
                {r.player_name}
              </span>
            </PlayerHover>
            <span className="w-6 shrink-0 text-[9.5px] font-bold"
                  style={{ color: POS_HUE[r.position] ?? "#8CA096" }}>
              {r.position}
            </span>
            <span className="num w-10 shrink-0 text-[10px] text-muted">
              adp {Math.round(r.ecr ?? 0)}
            </span>
            <span className="h-2 flex-1 rounded-[2px] bg-ink">
              <span className="block h-2 rounded-[2px] bg-turf/70"
                    style={{ width: `${(r.market_edge / max) * 100}%` }} />
            </span>
            <span className="num w-9 shrink-0 text-right text-[10.5px] text-turf">
              +{Math.round(r.market_edge)}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
