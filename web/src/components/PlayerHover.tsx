import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, type PlayerProfile } from "@/lib/api";
import { POS_HUE } from "@/components/Charts";
import { cn } from "@/lib/utils";

/**
 * A profile, not a sentence.
 *
 * The first version put a browser `title` on each bar, which gave you
 * "Caleb Williams — VOR 23.5, ADP 77.5, 95% to last" as one run-on line after
 * a second's delay. A player is a face and a shape, not a comma-separated
 * string, so hovering now opens a real card: photo, where he goes, what he is
 * worth, and the season band drawn rather than described.
 *
 * Profiles are fetched once per player and cached — the same name gets hovered
 * repeatedly while you deliberate.
 */
const CACHE = new Map<string, PlayerProfile>();

function Stat({ label, value, tone }: {
  label: string; value: ReactNode; tone?: "turf" | "alarm" | "muted";
}) {
  return (
    <div className="flex flex-col">
      <span className="text-[9px] uppercase tracking-wider text-muted">{label}</span>
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
    <div className="w-[268px] overflow-hidden rounded-lg border border-line bg-raised shadow-2xl">
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
              <span className="rounded bg-clock/15 px-1 text-[9px] font-bold text-clock">R</span>
            )}
          </div>
          <div className="mt-0.5 flex items-center gap-1.5 text-[10.5px]">
            <span className="font-bold" style={{ color: hue }}>{p.position}</span>
            {p.pos_rank && <span className="num text-muted">#{p.pos_rank} at the spot</span>}
          </div>
        </div>
        {p.drafted && (
          <span className="shrink-0 text-[9px] uppercase tracking-wider text-alarm">gone</span>
        )}
      </div>

      <div className="grid grid-cols-3 gap-2 border-b border-line p-2.5">
        <Stat label="Value" value={`${(p.vor ?? 0) > 0 ? "+" : ""}${Math.round(p.vor ?? 0)}`}
              tone={(p.vor ?? 0) >= 0 ? "turf" : "alarm"} />
        <Stat label={`${p.platform} adp`} value={p.adp ? Math.round(p.adp) : "—"} />
        <Stat
          label="Lasts?"
          value={p.survives != null ? `${Math.round(p.survives * 100)}%` : "—"}
          tone={p.survives != null && p.survives < 0.25 ? "alarm" : undefined}
        />
      </div>

      {p.floor != null && p.ceiling != null && (
        <div className="border-b border-line p-2.5">
          <div className="mb-1 flex items-baseline justify-between text-[9px] uppercase tracking-wider text-muted">
            <span>season range</span>
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
            <div className="flex justify-between">
              <span>from catches / touchdowns</span>
              <span className="num text-chalk">
                {Math.round((p.rec_share ?? 0) * 100)}% / {Math.round((p.td_share ?? 0) * 100)}%
              </span>
            </div>
          )}
          {p.draft_rank != null && (
            <div className="flex justify-between">
              <span>ESPN board rank</span>
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
  const [open, setOpen] = useState(false);
  const [p, setP] = useState<PlayerProfile | null>(CACHE.get(playerId) ?? null);
  const [above, setAbove] = useState(true);
  const ref = useRef<HTMLSpanElement>(null);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  function enter() {
    // Flip below when there is no room above, so the card never clips.
    const r = ref.current?.getBoundingClientRect();
    if (r) setAbove(r.top > 300);
    timer.current = window.setTimeout(async () => {
      setOpen(true);
      if (!CACHE.has(playerId)) {
        try {
          const d = await api.player(playerId);
          CACHE.set(playerId, d);
          setP(d);
        } catch { /* leave the trigger inert */ }
      } else {
        setP(CACHE.get(playerId)!);
      }
    }, 120);
  }

  function leave() {
    window.clearTimeout(timer.current);
    setOpen(false);
  }

  return (
    <span
      ref={ref}
      className={cn("relative", className)}
      onMouseEnter={enter}
      onMouseLeave={leave}
    >
      {children}
      {open && p && (
        <span
          className={cn(
            "tick-in pointer-events-none absolute left-0 z-50 block",
            above ? "bottom-full mb-1.5" : "top-full mt-1.5"
          )}
        >
          <Card p={p} />
        </span>
      )}
    </span>
  );
}
