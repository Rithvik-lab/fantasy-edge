import { useState } from "react";
import { LayoutGroup, motion } from "motion/react";
import type { TeamReport } from "@/lib/api";
import { POS_HUE } from "@/components/Charts";
import { PlayerHover } from "@/components/PlayerHover";
import { AddByName } from "@/components/TradeDeck";
import { Term } from "@/components/Explain";
import { SuggestLineup } from "@/components/SuggestLineup";
import { cn, perGame } from "@/lib/utils";

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

/**
 * Can this man legally fill this slot?
 *
 * The same rule the engine enforces, applied before the drop rather than
 * after it — dragging a receiver onto the defence used to put him there, and
 * the pin outlived the mistake: every lineup solved afterwards had somebody in
 * a slot he cannot fill, and no further drag could repair it because the
 * repair was illegal too.
 */
const FLEXES = ["RB", "WR", "TE"];

function fits(position: string, slot?: string): boolean {
  if (!slot) return true;                    // the bench takes anybody
  const base = slot.replace(/[0-9]+$/, "");
  if (base === "FLEX") return FLEXES.includes(position);
  if (base === "SUPERFLEX" || base === "OP")
    return FLEXES.includes(position) || position === "QB";
  return position === base;
}

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
function Row({ p, slot, bench, over, setOver, pinned, onSwap, onDrop, busy,
               held, setHeld }: {
  p: TeamReport["starters"][0];
  slot?: string;
  bench?: boolean;
  over: string | null;
  setOver: (f: (o: string | null) => string | null) => void;
  pinned: Record<string, string>;
  onSwap: (a: string, b: string) => void;
  onDrop?: (playerId: string, name: string) => void;
  busy: boolean;
  /** The man currently being dragged, so a slot can refuse him on the way in. */
  held: { id: string; position: string } | null;
  setHeld: (h: { id: string; position: string } | null) => void;
}) {
  const legal = !held || held.id === p.player_id
    || (fits(held.position, slot) && fits(p.position, pinned[held.id] ??
        (bench ? undefined : slot)));
  return (
    <motion.li
      layout
      draggable
      onDragStart={(e) => {
        (e as unknown as React.DragEvent)
          .dataTransfer.setData("text/plain", p.player_id);
        setHeld({ id: p.player_id, position: p.position });
      }}
      onDragEnd={() => { setHeld(null); setOver(() => null); }}
      onDragOver={(e) => {
        // Not calling preventDefault is what makes a drop impossible, so an
        // illegal target simply will not take him.
        if (!legal) return;
        e.preventDefault();
        setOver(() => p.player_id);
      }}
      onDragLeave={() => setOver((o) => (o === p.player_id ? null : o))}
      onDrop={(e) => {
        e.preventDefault();
        setOver(() => null);
        const from = (e as unknown as React.DragEvent)
          .dataTransfer.getData("text/plain");
        if (from && from !== p.player_id) onSwap(from, p.player_id);
      }}
      title={held && !legal
        ? `${held.position} cannot play ${slot ?? "there"}`
        : "drag onto another of your players to swap where they play"}
      className={cn(
        "group flex cursor-grab select-none items-center gap-2.5 border-b border-line/40 px-3 py-1.5 transition-colors last:border-0 active:cursor-grabbing",
        over === p.player_id && "bg-turf/12",
        held && !legal && "opacity-40",
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
               className="h-7 w-7 shrink-0 cursor-help rounded-md bg-raised object-cover object-top ring-1 ring-line" />
        </PlayerHover>
      ) : <span className="h-7 w-7 shrink-0 rounded-md bg-raised" />}
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
      {/* PER WEEK, NOT PER SEASON. A lineup card is a comparison between men
          for one Sunday, and a season total answers a different question --
          it folds in how many games he is expected to be there for, so a good
          player who misses a month and a lesser one who does not read the
          same. The season figure is a hover away and is still what the solver
          adds up; it just is not the number to put beside a name here. */}
      <Term k="expected_ppg">
        <span className="num shrink-0 text-[11px] text-muted"
              title={`${Math.round(p.projected_points ?? 0)} across the season, `
                     + `over ${Math.round(p.expected_games ?? 17)} expected games`}>
          {perGame(p)}
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
  const [held, setHeld] = useState<{ id: string; position: string } | null>(null);
  const pinned = d.pinned ?? {};

  const starters = [...d.starters].sort(
    (a, b) => rank(a.slot, a.position) - rank(b.slot, b.position));
  const row = { over, setOver, pinned, onSwap, onDrop, busy, held, setHeld };

  return (
    <LayoutGroup>
    <section className="rounded-lg border border-line bg-panel">
      <header className="border-b border-line px-3 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="eyebrow">Your team</span>
          <span className="num ml-auto text-[10.5px] text-muted">
            {d.starters.length + d.bench.length} players
          </span>
          {/* A COLUMN OF BARE NUMBERS NEEDS A UNIT. The same 14 is an
              excellent week and a catastrophic season, and the only thing
              telling them apart used to be knowing which one the app had
              picked. */}
          <span className="w-full text-right text-[9.5px] uppercase tracking-wider text-muted/70">
            points per game
          </span>
        </div>
        <div className="mt-1.5">
          <SuggestLineup onApplied={() => onRefresh?.()} />
        </div>
        {Object.keys(pinned).length > 0 && (
          <button onClick={onReset} disabled={busy}
                  className="mt-1 text-[10.5px] text-clock underline-offset-2 hover:underline">
            {d.pinned_week
              ? `lineup set for week ${d.pinned_week} — back to the season lineup`
              : `${Object.keys(pinned).length} set by hand — solve it`}
          </button>
        )}
      </header>

      {/* WAITING ON ESPN. These are moves you told the app about and made in
          your league yourself; ESPN confirms them on its own schedule, and
          until it does they are the app disagreeing with the source of truth.
          Said out loud with a clock, because the alternative is a roster that
          quietly changes back two days later. */}
      {!!d.pending?.length && (
        <div className="border-b border-clock/30 bg-clock/[0.08] px-3 py-1.5">
          <span className="block text-[10px] uppercase tracking-wider text-clock">
            waiting on ESPN
          </span>
          {d.pending.map((x) => (
            <span key={x.player_id + x.kind}
                  className="block text-[10.5px] leading-snug text-chalk">
              <span className={x.kind === "in" ? "text-turf" : "text-alarm"}>
                {x.kind === "in" ? "+" : "−"}
              </span>{" "}
              {x.player_name}
              {x.hours_left != null && (
                <span className="num ml-1 text-muted">
                  {x.hours_left > 0
                    ? `· ${Math.round(x.hours_left)}h to confirm`
                    : "· giving up on this"}
                </span>
              )}
            </span>
          ))}
        </div>
      )}

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
            <span className="h-7 w-7 shrink-0 rounded-md border border-dashed border-line" />
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

      {/* INJURED RESERVE. Shown even when empty, because an empty IR seat is a
          thing you can use and an invisible one is a thing you forget you
          have. It is also the reason the roster limit is what it is: ESPN
          reports IR in the same map as the bench, and counting the two
          together invented a spare bench spot the app then spent. */}
      {!!d.ir_slots && (
        <>
          <div className="flex items-center gap-2 border-y border-line bg-raised/40 px-3 py-1">
            <span className="eyebrow">Injured reserve</span>
            <span className="num ml-auto text-[10px] text-muted">
              {(d.ir ?? []).length}/{d.ir_slots}
            </span>
          </div>
          <ul>
            {Array.from({ length: d.ir_slots }).map((_, i) => {
              const p = (d.ir ?? [])[i];
              return (
                <li key={i}
                    className="flex items-center gap-2.5 border-b border-line/40 px-3 py-1.5 last:border-0">
                  <span className="num w-10 shrink-0 text-[10px] uppercase tracking-wider text-muted/60">
                    IR
                  </span>
                  {p ? (
                    <>
                      <span className="h-7 w-7 shrink-0 rounded-md bg-raised" />
                      <PlayerHover playerId={p.player_id} className="min-w-0 flex-1">
                        <span className="block cursor-help truncate text-[12px] leading-tight">
                          {p.player_name}
                        </span>
                      </PlayerHover>
                      <span className="shrink-0 text-[9px] uppercase tracking-wider text-alarm">
                        {(p.status ?? "").toLowerCase().replace(/_/g, " ")}
                      </span>
                    </>
                  ) : (
                    <>
                      <span className="h-7 w-7 shrink-0 rounded-md border border-dashed border-line" />
                      <span className="flex-1 text-[11px] italic text-muted">
                        empty — holds a man ESPN lists out, and only him
                      </span>
                    </>
                  )}
                </li>
              );
            })}
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
