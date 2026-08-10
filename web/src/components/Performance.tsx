import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { api, type Performance as Perf } from "@/lib/api";
import { POS_HUE } from "@/components/Charts";
import { PlayerHover } from "@/components/PlayerHover";
import { cn } from "@/lib/utils";

/**
 * Projected against actual, once games exist.
 *
 * Two scopes because they are two questions. MY TEAM is whether the men I
 * drafted are doing what I drafted them to do — the only version that changes
 * a lineup. THE LEAGUE is who is beating their price anywhere, which is where
 * a waiver claim or a buy-low comes from.
 *
 * Measured PER GAME. A man who missed three weeks is judged on the football he
 * played rather than punished twice for the injury — the missed games are
 * availability and already priced elsewhere.
 *
 * Before week one it says so and shows nothing. A performance panel inventing
 * numbers out of an unplayed season is worse than an empty one.
 */
export function Performance() {
  const [scope, setScope] = useState<"mine" | "league">("mine");
  const [d, setD] = useState<Perf | null>(null);

  useEffect(() => {
    let alive = true;
    setD(null);
    api.performance(scope).then((r) => { if (alive) setD(r); })
       .catch(() => { if (alive) setD(null); });
    return () => { alive = false; };
  }, [scope]);

  const max = Math.max(...(d?.rows ?? []).map((r) => Math.abs(r.delta)), 3);

  return (
    <section className="rounded-lg border border-line bg-panel">
      <header className="flex items-center gap-2 border-b border-line px-3 py-2">
        <span className="eyebrow">Performance</span>
        {/* Always say WHEN. A performance panel with no period on it is a
            number floating in nothing — and before kickoff the honest label is
            not "week 0", it is that the season has not started. */}
        <span className={cn("num rounded px-1.5 py-0.5 text-[10px]",
          d?.ready ? "bg-raised text-chalk" : "bg-raised text-muted")}>
          {d?.ready && d.week ? `week ${d.week}` : "pre-season"}
        </span>
        <div className="ml-auto flex rounded border border-line p-0.5">
          {(["mine", "league"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setScope(s)}
              className={cn("rounded px-2 py-0.5 text-[10.5px] transition-colors",
                scope === s ? "bg-raised font-medium text-chalk"
                            : "text-muted hover:text-chalk")}
            >
              {s === "mine" ? "My team" : "League"}
            </button>
          ))}
        </div>
      </header>

      {d && !d.ready ? (
        <div className="px-3 py-6 text-center">
          <p className="text-[11.5px] text-muted">{d.note}</p>
          {d.kickoff && (
            <p className="num mt-1 text-[10.5px] text-muted">
              week one · {d.kickoff}
            </p>
          )}
        </div>
      ) : !d ? (
        <div className="space-y-1.5 p-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-3 animate-pulse rounded bg-line/50" />
          ))}
        </div>
      ) : (
        <AnimatePresence mode="wait">
          <motion.ul
            key={scope}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.16 }}
            className="max-h-[46vh] overflow-y-auto"
          >
            {d.rows.map((r) => {
              const good = r.delta >= 0;
              const w = (Math.abs(r.delta) / max) * 46;
              return (
                <li key={r.player_id}
                    className={cn("flex items-center gap-2 border-b border-line/40 px-3 py-1.5 last:border-0",
                      r.mine && scope === "league" && "bg-turf/[0.06]")}>
                  {r.headshot ? (
                    <PlayerHover playerId={r.player_id} className="shrink-0">
                      <img src={r.headshot} alt="" loading="lazy"
                           className="h-6 w-6 shrink-0 cursor-help rounded object-cover object-top" />
                    </PlayerHover>
                  ) : <span className="h-6 w-6 shrink-0 rounded bg-raised" />}
                  <PlayerHover playerId={r.player_id} className="w-[96px] min-w-0 shrink-0">
                    <span className="block cursor-help truncate text-[11.5px]">
                      {r.player_name}
                    </span>
                  </PlayerHover>
                  <span className="w-5 shrink-0 text-[9.5px] font-bold"
                        style={{ color: POS_HUE[r.position] ?? "#8CA096" }}>
                    {r.position}
                  </span>
                  {/* Expected against actual, per game. The bar is the gap. */}
                  <div className="relative h-2.5 flex-1">
                    <div className="absolute inset-y-0 left-1/2 w-px bg-line" />
                    <div className={cn("absolute top-0 h-2.5 rounded-[2px]",
                      good ? "bg-turf/75" : "bg-alarm/75")}
                      style={good ? { left: "50%", width: `${w}%` }
                                  : { right: "50%", width: `${w}%` }} />
                  </div>
                  <span className="num w-16 shrink-0 text-right text-[10px] text-muted">
                    {(r.expected_ppg ?? 0).toFixed(1)}&rarr;{(r.ppg ?? 0).toFixed(1)}
                  </span>
                  <span className={cn("num w-10 shrink-0 text-right text-[10.5px] font-semibold",
                    good ? "text-turf" : "text-alarm")}>
                    {good ? "+" : ""}{r.delta.toFixed(1)}
                  </span>
                </li>
              );
            })}
            {d.rows.length === 0 && (
              <li className="px-3 py-6 text-center text-[11.5px] text-muted">
                Nothing to show yet.
              </li>
            )}
          </motion.ul>
        </AnimatePresence>
      )}

      {d?.ready && (
        <p className="border-t border-line px-3 py-1.5 text-[10px] leading-snug text-muted">
          Points per game against what the pre-season number implied. Per game
          on purpose — a man who missed three weeks is judged on the football he
          played, not punished twice for the injury.
        </p>
      )}
    </section>
  );
}
