import { useEffect, useState, type ReactNode } from "react";
import { api, type PlayerProfile } from "@/lib/api";
import { POS_HUE } from "@/components/Charts";
import { Term } from "@/components/Explain";
import { Floating, useHover } from "@/components/Floating";
import { cn } from "@/lib/utils";

/**
 * A profile, not a sentence — and one you can reach.
 *
 * The first version put a browser `title` on each bar, which gave you
 * "Caleb Williams — VOR 23.5, ADP 77.5, 95% to last" as one run-on line. The
 * second drew a real card but made it `pointer-events-none` and closed it the
 * instant the pointer left the name, so the card was visible and untouchable:
 * every label in it was dead, and moving toward it killed it.
 *
 * Now the card is live. Entering it cancels the close, and each number inside
 * carries the same explanation it has everywhere else in the app — a profile
 * you can read INTO, which is the whole point of putting numbers on it.
 */
const CACHE = new Map<string, PlayerProfile>();

function Stat({ term, label, value, tone }: {
  term: string; label: string; value: ReactNode; tone?: "turf" | "alarm" | "muted";
}) {
  return (
    <div className="flex flex-col items-start">
      <Term k={term}>
        <span className="text-[9px] uppercase tracking-wider text-muted">{label}</span>
      </Term>
      <span className={cn("num text-[12px] font-semibold",
        tone === "turf" ? "text-turf" : tone === "alarm" ? "text-alarm" : "text-chalk")}>
        {value}
      </span>
    </div>
  );
}

function Card({ p }: { p: PlayerProfile }) {
  const hue = POS_HUE[p.position] ?? "#8CA096";
  const span = (p.ceiling ?? 0) - (p.floor ?? 0);
  const med = span > 0 ? (((p.median ?? 0) - (p.floor ?? 0)) / span) * 100 : 50;

  return (
    <div className="overflow-hidden rounded-lg border border-line bg-raised shadow-2xl">
      <div className="flex items-center gap-2.5 border-b border-line p-2.5">
        {p.headshot ? (
          <img src={p.headshot} alt="" className="h-12 w-12 shrink-0 rounded-md bg-panel object-cover object-top" />
        ) : (
          <div className="h-12 w-12 shrink-0 rounded-md bg-panel" />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-semibold">{p.player_name}</span>
            {p.rookie && (
              <Term k="rookie">
                <span className="rounded bg-clock/15 px-1 text-[9px] font-bold text-clock">R</span>
              </Term>
            )}
          </div>
          <div className="mt-0.5 flex items-center gap-1.5 text-[10.5px]">
            <span className="font-bold" style={{ color: hue }}>{p.position}</span>
            {p.pos_rank && (
              <Term k="pos_rank">
                <span className="num text-muted">#{p.pos_rank} at the spot</span>
              </Term>
            )}
          </div>
        </div>
        {p.drafted && (
          <span className="shrink-0 text-[9px] uppercase tracking-wider text-alarm">gone</span>
        )}
      </div>

      <div className="grid grid-cols-3 gap-2 border-b border-line p-2.5">
        <Stat term="vor" label="Value"
              value={`${(p.vor ?? 0) > 0 ? "+" : ""}${Math.round(p.vor ?? 0)}`}
              tone={(p.vor ?? 0) >= 0 ? "turf" : "alarm"} />
        <Stat term="adp" label={`${p.platform} adp`} value={p.adp ? Math.round(p.adp) : "—"} />
        <Stat
          term="survive"
          label="Lasts?"
          value={p.survives != null ? `${Math.round(p.survives * 100)}%` : "—"}
          tone={p.survives != null && p.survives < 0.25 ? "alarm" : undefined}
        />
      </div>

      {p.floor != null && p.ceiling != null && (
        <div className="border-b border-line p-2.5">
          <div className="mb-1 flex items-baseline justify-between text-[9px] uppercase tracking-wider text-muted">
            <Term k="range"><span>season range</span></Term>
            {p.expected_games != null && (
              <span className="num normal-case tracking-normal">
                ~{p.expected_games} games
              </span>
            )}
          </div>
          <div className="relative h-2 rounded-full bg-panel">
            <div className="absolute inset-y-0 rounded-full bg-gradient-to-r from-turf/30 to-turf/70"
                 style={{ left: 0, right: 0 }} />
            {/* Median tick: the difference between a wide band and a good bet. */}
            <div className="absolute top-1/2 h-3 w-[2px] -translate-y-1/2 rounded bg-chalk"
                 style={{ left: `${Math.min(97, Math.max(1, med))}%` }} />
          </div>
          <div className="num mt-1 flex justify-between text-[10px] text-muted">
            <span>{p.floor}</span>
            <span className="text-chalk">{p.median}</span>
            <span>{p.ceiling}</span>
          </div>
        </div>
      )}

      {(p.rec_share != null || p.draft_rank != null) && (
        <div className="space-y-0.5 p-2.5 text-[10.5px] text-muted">
          {p.rec_share != null && (
            <div className="flex items-baseline justify-between gap-2">
              <Term k="shares"><span>from catches / touchdowns</span></Term>
              <span className="num text-chalk">
                {Math.round((p.rec_share ?? 0) * 100)}% / {Math.round((p.td_share ?? 0) * 100)}%
              </span>
            </div>
          )}
          {p.draft_rank != null && (
            <div className="flex items-baseline justify-between gap-2">
              <Term k="board_rank"><span>ESPN board rank</span></Term>
              <span className="num text-chalk">#{Math.round(p.draft_rank)}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function PlayerHover({ playerId, children, className }: {
  playerId: string; children: ReactNode; className?: string;
}) {
  const [p, setP] = useState<PlayerProfile | null>(CACHE.get(playerId) ?? null);
  const { ref, anchor, show, hide, keep } = useHover();
  const open = anchor != null;

  // Fetched once per player and cached — the same name gets hovered over and
  // over while you deliberate, and a second request per hover would show as lag
  // in exactly the moment the app exists to remove.
  useEffect(() => {
    if (!open) return;
    const hit = CACHE.get(playerId);
    if (hit) { setP(hit); return; }
    let alive = true;
    api.player(playerId)
      .then((d) => { CACHE.set(playerId, d); if (alive) setP(d); })
      .catch(() => undefined);
    return () => { alive = false; };
  }, [open, playerId]);

  return (
    <span
      ref={ref}
      className={cn("inline-flex min-w-0", className)}
      onMouseEnter={show}
      onMouseLeave={hide}
    >
      {children}
      {anchor && p && (
        <Floating anchor={anchor} width={276} interactive onEnter={keep} onLeave={hide}>
          <Card p={p} />
        </Floating>
      )}
    </span>
  );
}
