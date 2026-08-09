import { useEffect, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ChevronDown, ChevronUp } from "lucide-react";
import type { Status, TeamRow } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { LANDING, Landed } from "@/components/Flight";
import { Term } from "@/components/Explain";
import { cn } from "@/lib/utils";

/**
 * Picks as they land, a round at a time.
 *
 * A flat feed of the last eight answers "what just happened" and nothing else.
 * The question people actually ask between turns is "what went in round three"
 * — because a round is the unit a draft is made of, and comparing rounds is
 * how you see a run at a position forming.
 *
 * It opens on the round in progress and pages BACKWARD, because the draft only
 * goes one way: everything worth reviewing is behind you.
 */
export function Ticker({ status, onUndo, busy, landing }: {
  status: Status; onUndo: () => void; busy: boolean; landing?: boolean;
}) {
  const all = status.recent ?? [];
  const live = Math.max(1, status.round ?? 1);
  const [round, setRound] = useState(live);
  const [pinned, setPinned] = useState(false);
  const reduce = useReducedMotion();

  // Follow the draft unless you have deliberately gone looking backward.
  useEffect(() => { if (!pinned) setRound(live); }, [live, pinned]);

  const shown = all.filter((p) => p.round === round);
  const move = (d: number) => {
    setPinned(true);
    setRound((r) => Math.min(live, Math.max(1, r + d)));
  };

  return (
    <section {...{ [LANDING]: "gone" }}
             className="relative rounded-lg border border-line bg-panel">
      <Landed on={!!landing} />
      <header className="flex items-center gap-2 border-b border-line px-3 py-2">
        <Term k="off_board"><span className="eyebrow">Off the board</span></Term>
        <span className="num text-[11px] text-muted">
          {status.picks_made ?? 0} picks
        </span>
        <Button size="sm" variant="ghost" className="ml-auto h-6 px-2 text-[11px]"
                onClick={onUndo} disabled={busy || !all.length}>
          Undo
        </Button>
      </header>

      <div className="flex items-center gap-2 border-b border-line px-3 py-1.5">
        <span className={cn("num text-[11px] font-semibold",
          round === live ? "text-turf" : "text-chalk")}>
          Round {round}
        </span>
        <span className="num text-[10.5px] text-muted">
          {shown.length} {shown.length === 1 ? "pick" : "picks"}
        </span>
        {pinned && round !== live && (
          <button onClick={() => { setPinned(false); setRound(live); }}
                  className="text-[10.5px] text-turf underline-offset-2 hover:underline">
            back to now
          </button>
        )}
        <div className="ml-auto flex gap-1">
          {([[-1, ChevronUp], [1, ChevronDown]] as const).map(([d, Icon]) => {
            const off = d < 0 ? round <= 1 : round >= live;
            return (
              <button
                key={d}
                onClick={() => move(d)}
                disabled={off}
                aria-label={d < 0 ? "earlier round" : "later round"}
                title={d < 0 ? `round ${round - 1}` : `round ${round + 1}`}
                className={cn(
                  "flex h-5 w-5 items-center justify-center rounded border border-line",
                  "text-muted transition-[transform,color,border-color] duration-150",
                  off ? "cursor-default opacity-25"
                      : "hover:scale-110 hover:border-turf/50 hover:text-chalk active:scale-95"
                )}
              >
                <Icon size={12} />
              </button>
            );
          })}
        </div>
      </div>

      <div className="min-h-[120px]">
        <AnimatePresence initial={false} mode="wait">
          <motion.ul
            key={round}
            initial={reduce ? undefined : { opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduce ? undefined : { opacity: 0, y: -10 }}
            transition={{ duration: 0.16, ease: "easeOut" }}
            className="max-h-[34vh] overflow-y-auto"
          >
            {shown.length === 0 && (
              <li className="px-3 py-4 text-center text-xs leading-snug text-muted">
                Nothing off the board in round {round}.
                <span className="mt-1 block text-[10.5px]">
                  <span className="kbd">shift</span> + double-click a name on the
                  board when someone else takes him.
                </span>
              </li>
            )}
            {shown.map((p, i) => (
              <motion.li
                key={p.overall}
                initial={reduce ? undefined : { opacity: 0, x: -5 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: reduce ? 0 : i * 0.02, duration: 0.15 }}
                className={cn(
                  "flex items-center gap-2 border-b border-line/60 px-3 py-1.5 text-xs last:border-0",
                  p.mine && "bg-turf/5"
                )}
              >
                <span className="num w-8 shrink-0 text-[11px] text-muted">
                  {p.overall}
                </span>
                <span className={cn("truncate",
                  p.mine ? "font-medium text-turf" : "text-chalk/85")}>
                  {p.name}
                </span>
                {p.mine && <Badge tone="turf" className="ml-auto">You</Badge>}
              </motion.li>
            ))}
          </motion.ul>
        </AnimatePresence>
      </div>
    </section>
  );
}

/**
 * What every other roster still needs. Their holes are the best predictor of
 * what leaves the board before your next turn.
 */
export function Teams({ teams, mySlot }: { teams: TeamRow[]; mySlot: number }) {
  if (!teams.length) return null;
  return (
    <section className="rounded-lg border border-line bg-panel">
      <header className="border-b border-line px-3 py-2">
        <span className="eyebrow">The room</span>
      </header>
      <ul className="max-h-[32vh] divide-y divide-line/60 overflow-y-auto">
        {teams.map((t) => (
          <li key={t.slot} className={cn("px-3 py-2", t.slot === mySlot && "bg-turf/5")}>
            <div className="flex items-center gap-2">
              <span className="num w-5 text-[11px] text-muted">{t.slot}</span>
              <span className={cn("truncate text-xs",
                t.slot === mySlot ? "font-medium text-turf" : "text-chalk/85")}>
                {t.slot === mySlot ? "You" : t.name}
              </span>
              <span className="num ml-auto text-[11px] text-muted">
                {t.players.length}
              </span>
            </div>
            {Object.keys(t.unfilled).length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1 pl-7">
                {Object.entries(t.unfilled).map(([pos, n]) => (
                  <Badge key={pos} tone="neutral">
                    {n > 1 ? `${n}×` : ""}{pos}
                  </Badge>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
