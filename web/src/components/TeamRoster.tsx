import { useState } from "react";
import { LayoutGroup, motion } from "motion/react";
import type { TeamReport } from "@/lib/api";
import { POS_HUE } from "@/components/Charts";
import { PlayerHover } from "@/components/PlayerHover";
import { AddByName } from "@/components/TradeDeck";
import { Term } from "@/components/Explain";
import { SuggestLineup } from "@/components/SuggestLineup";
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
               "TE", "FLEX", "FLEX1", "FLEX2", "DST", "D/ST", "K"];

function rank(slot: string | undefined, position: string): number {
  const i = ORDER.indexOf(slot ?? "");
  if (i >= 0) return i;
  const j = ORDER.indexOf(position);
  return j >= 0 ? j : ORDER.length;
}

/**
 * One man on the card.
 *
 * MODULE SCOPE ON PURPOSE. Declared inside `TeamRoster` this was a new
 * component type on every render, so React threw away all sixteen rows and
 * rebuilt them on any state change -- a fresh mount cannot animate, which is
 * why a man moving from the bench into the lineup used to blink rather than
 * travel.
 */
function Row({ p, slot, bench, over, setOver, pinned, onSwap, onDrop, busy }: {
  p: TeamReport["starters"][0];
  slot?: string;
  bench?: boolean;
  over: string | null;
  setOver: (f: (o: string | null) => string | null) => void;
  pinned: Record<string, string>;
  onSwap: (a: string, b: string) => void;
  onDrop?: (playerId: string, name: string) => void;
  busy: boolean;
}) {
  return (
    <motion.li
      layout
      draggable
      onDragStart={(e) => (e as unknown as React.DragEvent)
        .dataTransfer.setData("text/plain", p.player_id)}
      onDragOver={(e) => { e.preventDefault(); setOver(() => p.player_id); }}
      onDragLeave={() => setOver((o) => (o === p.player_id ? null : o))}
      onDrop={(e) => {
        e.preventDefault();
        setOver(() => null);
        const from = (e as unknown as React.DragEvent)
          .dataTransfer.getData("text/plain");
        if (from && from !== p.player_id) onSwap(from, p.player_id);
      }}
      title="drag onto another of your players to swap where they play"
      className={cn(
        "group flex cursor-grab select-none items-center gap-2.5 border-b border-line/40 px-3 py-2 transition-colors last:border-0 active:cursor-grabbing",
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
      <Term k="projected">
        <span className="num shrink-0 text-[11px] text-muted">
          {Math.round(p.projected_points ?? 0)}
        </span>
      </Term>
      {onDrop && (
        <button
          onClick={(e) => { e.stopPropagation(); onDrop(p.player_id, p.player_name); }}
          disabled={busy}
          title={`Drop ${p.player_name}`}
          className="shrink-0 px-1 text-[13px] leading-none text-muted opacity-0 transition-opacity hover:text-alarm group-hover:opacity-100 disabled:opacity-30"
        >
          &times;
        </button>
      )}
    </motion.li>
  );
}

export function TeamRoster({ d, onSwap, onReset, onAdd, onDrop, onRefresh,
                             busy }: {
  d: TeamReport;
  onSwap: (a: string, b: string) => void;
  onReset: () => void;
  /** Re-read the roster after the lineup is set from a suggestion. */
  onRefresh?: () => void;
  /** Type a name to put him on your team. Dragging is for rearranging what is
   *  already here; typing is how something gets here in the first place, and
   *  a roster you can only reorder is not a roster you can fix. */
  onAdd?: (playerId: string) => void;
  onDrop?: (playerId: string, name: string) => void;
  busy: boolean;
}) {
  const [over, setOver] = useState<string | null>(null);
  const pinned = d.pinned ?? {};

  const starters = [...d.starters].sort(
    (a, b) => rank(a.slot, a.position) - rank(b.slot, b.position));
  const row = { over, setOver, pinned, onSwap, onDrop, busy };

  return (
    <LayoutGroup>
    <section className="rounded-lg border border-line bg-panel">
      <header className="border-b border-line px-3 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="eyebrow">Your team</span>
          <span className="num ml-auto text-[10.5px] text-muted">
            {d.starters.length + d.bench.length} players
          </span>
        </div>
        <div className="mt-1.5">
          <SuggestLineup onApplied={() => onRefresh?.()} />
        </div>
        {Object.keys(pinned).length > 0 && (
          <button onClick={onReset} disabled={busy}
                  className="mt-1 text-[10.5px] text-clock underline-offset-2 hover:underline">
            {Object.keys(pinned).length} set by hand — back to the season lineup
          </button>
        )}
      </header>

      <ul>
        {starters.map((p) => (
          <Row key={p.player_id} p={p} slot={p.slot} {...row} />
        ))}
        {/* An unfilled slot is a fact about your team and the only honest
            place to show it is the lineup card, where the hole is. It used to
            surface as "K: −143 against the league", which is a strange way to
            say you have not got a kicker. */}
        {(d.gaps ?? []).map((g) => (
          <li key={g.slot}
              className="flex items-center gap-2.5 border-b border-line/40 px-3 py-2 last:border-0">
            <span className="num w-10 shrink-0 text-[10px] uppercase tracking-wider text-clock">
              {g.slot}
            </span>
            <span className="h-9 w-9 shrink-0 rounded-md border border-dashed border-line" />
            <span className="flex-1 text-[11.5px] italic text-muted">
              nobody here — you start this slot empty
            </span>
          </li>
        ))}
      </ul>

      {d.bench.length > 0 && (
        <>
          <div className="border-y border-line bg-raised/40 px-3 py-1">
            <span className="eyebrow">Bench</span>
          </div>
          <ul>
            {d.bench.map((p) => (
              <Row key={p.player_id} p={p} bench {...row} />
            ))}
          </ul>
        </>
      )}

      {onAdd && (
        <div className="space-y-1 border-t border-line p-2">
          <AddByName placeholder="add a player by name…" restrictTo={null}
                     onAdd={(h) => onAdd(h.player_id)} />
          <p className="text-[10px] leading-snug text-muted">
            Drag one onto another to swap where they play.
          </p>
        </div>
      )}
    </section>
    </LayoutGroup>
  );
}
