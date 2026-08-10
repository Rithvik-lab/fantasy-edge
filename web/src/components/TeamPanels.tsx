import { motion } from "motion/react";
import type { TeamReport } from "@/lib/api";
import { POS_HUE } from "@/components/Charts";
import { Floating, useHover } from "@/components/Floating";
import { PlayerHover } from "@/components/PlayerHover";
import { cn } from "@/lib/utils";

/**
 * One team in the standings.
 *
 * Ranked best to worst, because the question is who is ahead of me. Named,
 * because "pick 4" is a seat and a league is people. And the score explains
 * itself from the model rather than from prose someone wrote: hovering it
 * gives the slots that put the team where it is, measured against what the
 * league starts at each.
 */
function TeamRow({ t, max, name }: {
  t: NonNullable<TeamReport["league"]>[0]; max: number; name: string;
}) {
  const lineup = useHover<HTMLSpanElement>({ delay: 120, grace: 160 });
  const why = useHover<HTMLSpanElement>({ delay: 120, grace: 160 });
  const shape = t.shape ?? [];
  const best = shape.filter((x) => x.edge > 8).slice(0, 2);
  const worst = shape.filter((x) => x.edge < -8).slice(-2).reverse();

  return (
    <div className={cn("flex items-center gap-2.5 px-3 py-1.5",
      t.mine && "bg-turf/[0.07]")}>
      <span className={cn("num w-4 shrink-0 text-[10.5px]",
        t.mine ? "font-semibold text-turf" : "text-muted")}>
        {t.rank}
      </span>

      <span ref={lineup.ref} onMouseEnter={lineup.show} onMouseLeave={lineup.hide}
            className={cn("w-[92px] shrink-0 cursor-help truncate text-[11.5px]",
              t.mine ? "font-semibold text-turf" : "text-chalk/85")}>
        {t.mine ? "You" : name}
      </span>

      <span className="relative h-2.5 flex-1 overflow-hidden rounded-full bg-ink">
        <motion.span
          className={cn("absolute inset-y-0 left-0 block rounded-full",
            t.mine ? "bg-turf" : "bg-muted/35")}
          initial={{ width: 0 }}
          animate={{ width: `${(t.starters / max) * 100}%` }}
          transition={{ type: "spring", stiffness: 190, damping: 26 }}
        />
      </span>

      <span ref={why.ref} onMouseEnter={why.show} onMouseLeave={why.hide}
            className={cn("num w-11 shrink-0 cursor-help text-right text-[11px]",
              t.mine ? "font-semibold text-chalk" : "text-muted")}>
        {Math.round(t.starters)}
      </span>

      {lineup.anchor && (
        <Floating anchor={lineup.anchor} width={246} interactive
                  onEnter={lineup.keep} onLeave={lineup.hide}>
          <div className="overflow-hidden rounded-lg border border-line bg-raised shadow-2xl">
            <div className="border-b border-line px-2.5 py-1.5 text-[11.5px] font-semibold">
              {t.mine ? "Your team" : name}
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
          </div>
        </Floating>
      )}

      {why.anchor && (
        <Floating anchor={why.anchor} width={268} interactive
                  onEnter={why.keep} onLeave={why.hide}>
          <div className="rounded-lg border border-line bg-raised p-2.5 shadow-2xl">
            <p className="text-[11px] leading-snug text-chalk/85">
              <span className="num font-semibold text-chalk">
                {Math.round(t.starters)}
              </span>{" "}
              projected points from their best legal lineup, {t.rank}
              {t.rank === 1 ? "st" : t.rank === 2 ? "nd"
                : t.rank === 3 ? "rd" : "th"} in the league.
            </p>
            {(best.length > 0 || worst.length > 0) && (
              <div className="mt-2 space-y-1 border-t border-line pt-2">
                <span className="eyebrow">against what the league starts</span>
                {[...best, ...worst].map((x) => (
                  <div key={x.position} className="flex items-baseline gap-2">
                    <span className="w-7 shrink-0 text-[10px] font-bold"
                          style={{ color: POS_HUE[x.position] ?? "#8CA096" }}>
                      {x.position}
                    </span>
                    <span className={cn("num w-11 text-right text-[10.5px]",
                      x.edge > 0 ? "text-turf" : "text-alarm")}>
                      {x.edge > 0 ? "+" : ""}{Math.round(x.edge)}
                    </span>
                    <span className="num text-[10px] text-muted">
                      {Math.round(x.points)} vs {Math.round(x.league)}
                    </span>
                  </div>
                ))}
              </div>
            )}
            {best.length === 0 && worst.length === 0 && (
              <p className="mt-1.5 text-[10.5px] text-muted">
                No slot is far from the league average — this team is where it
                is on balance rather than on any one position.
              </p>
            )}
          </div>
        </Floating>
      )}
    </div>
  );
}

export function Standing({ rows, names }: {
  rows: NonNullable<TeamReport["league"]>;
  names?: Record<string, string>;
}) {
  if (rows.length < 2) return null;
  const max = Math.max(...rows.map((r) => r.starters), 1);
  const me = rows.find((r) => r.mine);

  return (
    <section className="rounded-lg border border-line bg-panel">
      <header className="border-b border-line px-3 py-2">
        <div className="flex items-baseline gap-2">
          <span className="eyebrow">Where you stand</span>
          {me && (
            <span className="num ml-auto text-[11px] text-muted">
              <span className="font-semibold text-chalk">{me.rank}</span>
              {" of "}{rows.length}
            </span>
          )}
        </div>
      </header>

      <ol className="divide-y divide-line/40">
        {rows.map((t) => (
          <li key={t.slot}>
            <TeamRow t={t} max={max}
                     name={names?.[String(t.slot)] ?? `Team ${t.slot}`} />
          </li>
        ))}
      </ol>

      <p className="border-t border-line px-3 py-2 text-[10px] leading-snug text-muted">
        Projected, not played. Neighbouring teams here are usually closer than
        one week&rsquo;s swing — read the order as a guide, not a table. Hover a
        name for their lineup, or a score for why it lands there.
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
