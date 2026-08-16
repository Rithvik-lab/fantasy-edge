import { AnimatePresence, motion } from "motion/react";
import type { LeagueRoster, TradePlayer } from "@/lib/api";
import { POS_HUE } from "@/components/Charts";
import { PlayerHover } from "@/components/PlayerHover";
import { Term } from "@/components/Explain";
import { LANDING, Landed } from "@/components/Flight";
import { cn } from "@/lib/utils";

/**
 * The two roster panels and the two trade piles between them.
 *
 * Laid out the way the question is actually shaped: my team on the far left,
 * their team on the far right, and what is crossing the table in the middle
 * where both sides can be read at once. It mirrors the roster view every
 * fantasy platform already uses, so nobody has to learn where anything is.
 *
 * Names move by drag or by click. Faces are large in the piles on purpose —
 * a trade is four or five players and you should be able to see all of them
 * without reading.
 */

// The order a lineup card is written in — flexes after the named slots,
// defence and kicker last, the way every platform prints it.
const SLOT_ORDER = ["QB", "RB", "RB", "WR", "WR", "WR", "TE",
                    "FLEX", "FLEX", "OP", "D/ST", "K"];

/** ESPN-style starters table: slot on the left, the man filling it beside. */
export function RosterPanel({
  title, roster, selected, onToggle, onAdd, side,
}: {
  title: string;
  roster: LeagueRoster | null;
  selected: string[];
  onToggle: (id: string) => void;
  /** Double-click forces him IN. Single-click toggles, so a double-click would
   *  otherwise fire the toggle twice and net to nothing — which reads exactly
   *  like a dead control. Same gesture as the draft board, same meaning. */
  onAdd?: (id: string, el?: Element | null) => void;
  side: "mine" | "theirs";
}) {
  if (!roster) {
    return (
      <section className="rounded-lg border border-line bg-panel p-4 text-center">
        <p className="text-[11px] text-muted">no roster</p>
      </section>
    );
  }

  const starters = roster.players.filter((p) => p.starting);
  const bench = roster.players.filter((p) => !p.starting);

  const Line = ({ p, slot }: { p: LeagueRoster["players"][0]; slot?: string }) => {
    const on = selected.includes(p.player_id);
    // SAY IT BEFORE HE CLICKS. Ten men on live rosters in a twelve-team league
    // sit outside the ADP board the projections are built from, so they cannot
    // be priced -- and finding that out from an error after building a trade
    // around one is the worst possible moment.
    const unpriced = p.projected_points == null;
    return (
      <li
        draggable
        onDragStart={(e) => {
          e.dataTransfer.setData("text/plain", p.player_id);
          e.dataTransfer.setData("application/x-side", side);
          e.dataTransfer.effectAllowed = "copy";
        }}
        onClick={() => !unpriced && onToggle(p.player_id)}
        onDoubleClick={(e) => !unpriced && onAdd?.(p.player_id, e.currentTarget as Element)}
        title={unpriced
          ? `${p.player_name} is outside the projection board — no number to trade on`
          : on ? "in the trade — click to take him out"
               : "click or double-click to put him in the trade"}
        className={cn(
          "group flex select-none items-center gap-2 border-b border-line/40 px-2 py-1.5 transition-colors last:border-0",
          unpriced ? "cursor-not-allowed opacity-40" : "cursor-pointer",
          on ? "bg-turf/12" : !unpriced && "hover:bg-raised/60"
        )}
      >
        <span className="num w-9 shrink-0 text-[9.5px] uppercase tracking-wider text-muted">
          {slot ?? "BE"}
        </span>
        {p.headshot ? (
          <PlayerHover playerId={p.player_id} className="shrink-0">
            <img src={p.headshot} alt="" loading="lazy"
                 className="h-7 w-7 shrink-0 cursor-help rounded bg-raised object-cover object-top" />
          </PlayerHover>
        ) : <span className="h-7 w-7 shrink-0 rounded bg-raised" />}
        <div className="min-w-0 flex-1">
          <PlayerHover playerId={p.player_id}>
            <span className="block cursor-help truncate text-[11.5px] leading-tight">
              {p.player_name}
            </span>
          </PlayerHover>
          <span className="text-[9.5px] font-bold"
                style={{ color: POS_HUE[p.position] ?? "#8CA096" }}>
            {p.position}
          </span>
          {p.injury_status && p.injury_status !== "ACTIVE" && (
            <span className="ml-1 text-[9px] uppercase text-alarm">
              {p.injury_status.slice(0, 1)}
            </span>
          )}
        </div>
        {unpriced ? (
          <span className="shrink-0 text-[9px] uppercase tracking-wider text-muted">
            unpriced
          </span>
        ) : (
          <Term k="projected">
            <span className="num shrink-0 text-[10px] text-muted">
              {Math.round(p.projected_points ?? 0)}
            </span>
          </Term>
        )}
        <span className={cn("w-3 shrink-0 text-[13px] leading-none transition-opacity",
          on ? "text-turf" : "text-muted opacity-0 group-hover:opacity-100")}>
          {on ? "\u2713" : "+"}
        </span>
      </li>
    );
  };

  // Fill the slot column in the order a lineup is actually written.
  const used = new Set<string>();
  const rows: { p: LeagueRoster["players"][0]; slot: string }[] = [];
  for (const slot of SLOT_ORDER) {
    const hit = starters.find(
      (p) => !used.has(p.player_id) &&
        (p.lineup_slot === slot || (slot === "D/ST" && p.position === "DST")));
    if (hit) { used.add(hit.player_id); rows.push({ p: hit, slot }); }
  }
  for (const p of starters) if (!used.has(p.player_id)) rows.push({ p, slot: p.lineup_slot ?? "" });

  return (
    // NO INNER SCROLLER. A roster is sixteen or eighteen names and the whole
    // question is which of them to move -- half a team behind a scrollbar
    // means comparing two rosters is a memory test. The page scrolls; the
    // panel does not.
    <section className="flex flex-col rounded-lg border border-line bg-panel">
      <header className="flex items-center gap-2 border-b border-line px-2.5 py-1.5">
        <span className="eyebrow">{title}</span>
        <span className="num ml-auto text-[9.5px] text-muted">
          {roster.players.length} players
        </span>
      </header>
      <ul>
        {rows.map(({ p, slot }) => <Line key={p.player_id} p={p} slot={slot} />)}
        {bench.length > 0 && (
          <li className="border-y border-line bg-raised/40 px-2.5 py-1">
            <span className="eyebrow">Bench</span>
          </li>
        )}
        {bench.map((p) => <Line key={p.player_id} p={p} />)}
      </ul>
    </section>
  );
}

/** A pile of faces — what is crossing the table from one side. */
export function TradePile({
  label, ids, players, tone, onDrop, onRemove, side, landing,
}: {
  label: string;
  ids: string[];
  players: Map<string, TradePlayer>;
  tone: "alarm" | "turf";
  onDrop: (id: string) => void;
  onRemove: (id: string) => void;
  /** Names the pile so a flying player knows which one he is joining. */
  side?: string;
  landing?: boolean;
}) {
  return (
    <div
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        const id = e.dataTransfer.getData("text/plain");
        if (id) onDrop(id);
      }}
      {...(side ? { [LANDING]: side } : {})}
      className={cn(
        "relative flex min-h-[190px] flex-col gap-2 rounded-lg border border-dashed p-2 transition-colors",
        tone === "alarm" ? "border-alarm/25" : "border-turf/25"
      )}
    >
      <Landed on={!!landing} />
      <span className={cn("eyebrow", tone === "alarm" ? "text-alarm/70" : "text-turf/70")}>
        {label}
      </span>
      {ids.length === 0 ? (
        <p className="m-auto max-w-[10rem] text-center text-[10.5px] leading-snug text-muted">
click a name on the roster beside this, or drag one in
        </p>
      ) : (
        <AnimatePresence initial={false}>
          {ids.map((id) => {
            const p = players.get(id);
            if (!p) return null;
            return (
              <motion.div
                key={id}
                layout
                initial={{ opacity: 0, scale: 0.85 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.85 }}
                transition={{ type: "spring", stiffness: 380, damping: 30 }}
                className="group relative overflow-hidden rounded-md border border-line bg-raised"
              >
                <div className="flex items-center gap-2 p-1.5">
                  <PlayerHover playerId={p.player_id} className="shrink-0">
                    {p.headshot ? (
                      <img src={p.headshot} alt="" loading="lazy"
                           className="h-12 w-12 shrink-0 cursor-help rounded bg-panel object-cover object-top" />
                    ) : <span className="h-12 w-12 shrink-0 rounded bg-panel" />}
                  </PlayerHover>
                  <div className="min-w-0 flex-1">
                    <PlayerHover playerId={p.player_id}>
                      <span className="block cursor-help truncate text-[12px] font-medium leading-tight">
                        {p.player_name}
                      </span>
                    </PlayerHover>
                    <span className="text-[10px] font-bold"
                          style={{ color: POS_HUE[p.position] ?? "#8CA096" }}>
                      {p.position}
                    </span>
                    <Term k="projected" className="ml-1.5">
                      <span className="num text-[10px] text-muted">
                        {Math.round(p.projected_points ?? 0)} proj
                      </span>
                    </Term>
                  </div>
                  <button
                    onClick={() => onRemove(id)}
                    className="shrink-0 px-1 text-[14px] leading-none text-muted opacity-0 transition-opacity hover:text-alarm group-hover:opacity-100"
                  >
                    &times;
                  </button>
                </div>
              </motion.div>
            );
          })}
        </AnimatePresence>
      )}
    </div>
  );
}


/**
 * The manual-mode roster: one you type rather than one we read.
 *
 * Manual mode exists for leagues we cannot sync, and the verdict is a LINEUP
 * delta -- so without a roster there is nothing to compute a delta against.
 * The honest fix is to let you supply the lineup, not to quietly answer a
 * different, easier question about which pile of players is bigger.
 *
 * It doubles as the hypothetical: type any roster and price a trade into it,
 * whether or not that team is yours or even exists.
 */
export function ManualRoster({
  title, ids, players, selected, onToggle, onAdd, onRemove, onDropIn, children,
}: {
  title: string;
  ids: string[];
  players: Map<string, TradePlayer>;
  selected: string[];
  onToggle: (id: string) => void;
  onAdd?: (id: string, el?: Element | null) => void;
  onRemove: (id: string) => void;
  onDropIn?: (id: string) => void;
  children?: React.ReactNode;
}) {
  return (
    <section
      onDragOver={(e) => onDropIn && e.preventDefault()}
      onDrop={(e) => {
        if (!onDropIn) return;
        e.preventDefault();
        const id = e.dataTransfer.getData("text/plain");
        if (id) onDropIn(id);
      }}
      className="flex min-h-0 flex-col rounded-lg border border-line bg-panel"
    >
      <header className="flex items-center gap-2 border-b border-line px-2.5 py-1.5">
        <span className="eyebrow">{title}</span>
        <span className="num ml-auto text-[10px] text-muted">{ids.length}</span>
      </header>
      <div className="border-b border-line p-1.5">{children}</div>
      {ids.length === 0 ? (
        <p className="px-3 py-6 text-center text-[10.5px] leading-snug text-muted">
          Type the players on this team. The ruling is what a trade does to the
          lineup, so it needs the lineup.
        </p>
      ) : (
        <ul className="min-h-0 flex-1 overflow-y-auto">
          {ids.map((id) => {
            const p = players.get(id);
            if (!p) return null;
            const on = selected.includes(id);
            return (
              <li
                key={id}
                draggable
                onDragStart={(e) => e.dataTransfer.setData("text/plain", id)}
                onClick={() => onToggle(id)}
                onDoubleClick={(e) => onAdd?.(id, e.currentTarget as Element)}
                title={on ? "in the trade — click to take him out"
                          : "click or double-click to put him in the trade"}
                className={cn(
                  "group flex cursor-pointer select-none items-center gap-2 border-b border-line/40 px-2 py-1.5 last:border-0",
                  on ? "bg-turf/12" : "hover:bg-raised/60"
                )}
              >
                {p.headshot ? (
                  <PlayerHover playerId={id} className="shrink-0">
                    <img src={p.headshot} alt="" loading="lazy"
                         className="h-7 w-7 shrink-0 cursor-help rounded bg-raised object-cover object-top" />
                  </PlayerHover>
                ) : <span className="h-7 w-7 shrink-0 rounded bg-raised" />}
                <span className="w-6 shrink-0 text-[10px] font-bold"
                      style={{ color: POS_HUE[p.position] ?? "#8CA096" }}>
                  {p.position}
                </span>
                <PlayerHover playerId={id} className="min-w-0 flex-1">
                  <span className="block cursor-help truncate text-[11.5px]">
                    {p.player_name}
                  </span>
                </PlayerHover>
                <span className={cn(
                  "shrink-0 text-[13px] leading-none transition-opacity",
                  on ? "text-turf" : "text-muted opacity-0 group-hover:opacity-100")}>
                  {on ? "\u2713" : "+"}
                </span>
                <button
                  onClick={(e) => { e.stopPropagation(); onRemove(id); }}
                  className="shrink-0 px-1 text-[13px] leading-none text-muted opacity-0 transition-opacity hover:text-alarm group-hover:opacity-100"
                >
                  &times;
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
