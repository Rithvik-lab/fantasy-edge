import { useState } from "react";
import { motion } from "motion/react";
import type { TeamReport } from "@/lib/api";
import { POS_HUE } from "@/components/Charts";
import { PlayerHover } from "@/components/PlayerHover";
import { cn } from "@/lib/utils";

/**
 * Your roster, in the order a lineup card is written.
 *
 * QB, then the backs, then receivers, tight end, kicker, defence, bench. Not
 * ranked by points and not in draft order — a lineup has a shape, and the
 * shape is how you read it. Sorting by value would put your best flex above
 * your worst starter and quietly stop being a lineup at all.
 *
 * This is the biggest thing on the page on purpose. Everything else here
 * describes the roster; the roster is the thing.
 */
const ORDER = ["QB", "RB1", "RB2", "RB", "WR1", "WR2", "WR3", "WR",
               "TE", "FLEX", "K", "DST", "D/ST"];

function rank(slot: string | undefined, position: string): number {
  const i = ORDER.indexOf(slot ?? "");
  if (i >= 0) return i;
  const j = ORDER.indexOf(position);
  return j >= 0 ? j : ORDER.length;
}

export function TeamRoster({ d, onSwap, onReset, busy }: {
  d: TeamReport;
  onSwap: (a: string, b: string) => void;
  onReset: () => void;
  busy: boolean;
}) {
  const [over, setOver] = useState<string | null>(null);
  const pinned = d.pinned ?? {};

  const starters = [...d.starters].sort(
    (a, b) => rank(a.slot, a.position) - rank(b.slot, b.position));

  const Row = ({ p, slot, bench }: {
    p: TeamReport["starters"][0]; slot?: string; bench?: boolean;
  }) => (
    <motion.li
      layout
      draggable
      onDragStart={(e) => (e as unknown as React.DragEvent)
        .dataTransfer.setData("text/plain", p.player_id)}
      onDragOver={(e) => { e.preventDefault(); setOver(p.player_id); }}
      onDragLeave={() => setOver((o) => (o === p.player_id ? null : o))}
      onDrop={(e) => {
        e.preventDefault();
        setOver(null);
        const from = (e as unknown as React.DragEvent)
          .dataTransfer.getData("text/plain");
        if (from && from !== p.player_id) onSwap(from, p.player_id);
      }}
      title="drag onto another of your players to swap where they play"
      className={cn(
        "flex cursor-grab select-none items-center gap-2.5 border-b border-line/40 px-3 py-2 transition-colors last:border-0 active:cursor-grabbing",
        over === p.player_id && "bg-turf/12",
        bench && "opacity-70",
        pinned[p.player_id] && "border-l-2 border-l-clock"
      )}
    >
      <span className={cn(
        "num w-10 shrink-0 text-[10px] uppercase tracking-wider",
        bench ? "text-muted/60" : "text-muted")}>
        {slot ?? "BE"}
      </span>
      {p.headshot ? (
        <PlayerHover playerId={p.player_id} className="shrink-0">
          <img src={p.headshot} alt="" loading="lazy"
               className="h-9 w-9 shrink-0 cursor-help rounded-md bg-raised object-cover object-top ring-1 ring-line" />
        </PlayerHover>
      ) : <span className="h-9 w-9 shrink-0 rounded-md bg-raised" />}
      <div className="min-w-0 flex-1">
        <PlayerHover playerId={p.player_id}>
          <span className="block cursor-help truncate text-[12.5px] leading-tight">
            {p.player_name}
          </span>
        </PlayerHover>
        <span className="text-[10px] font-bold"
              style={{ color: POS_HUE[p.position] ?? "#8CA096" }}>
          {p.position}
        </span>
      </div>
      <span className="num shrink-0 text-[11px] text-muted">
        {Math.round(p.projected_points ?? 0)}
      </span>
    </motion.li>
  );

  return (
    <section className="rounded-lg border border-line bg-panel">
      <header className="flex items-baseline gap-2 border-b border-line px-3 py-2">
        <span className="eyebrow">Your team</span>
        {Object.keys(pinned).length > 0 && (
          <button onClick={onReset} disabled={busy}
                  className="text-[10.5px] text-clock underline-offset-2 hover:underline">
            {Object.keys(pinned).length} set by hand — solve it
          </button>
        )}
        <span className="num ml-auto text-[10.5px] text-muted">
          {d.starters.length + d.bench.length} players
        </span>
      </header>

      <ul>{starters.map((p) => <Row key={p.player_id} p={p} slot={p.slot} />)}</ul>

      {d.bench.length > 0 && (
        <>
          <div className="border-y border-line bg-raised/40 px-3 py-1">
            <span className="eyebrow">Bench</span>
          </div>
          <ul>{d.bench.map((p) => <Row key={p.player_id} p={p} bench />)}</ul>
        </>
      )}

      <p className="border-t border-line px-3 py-1.5 text-[10px] text-muted">
        Drag one onto another to swap where they play.
      </p>
    </section>
  );
}
