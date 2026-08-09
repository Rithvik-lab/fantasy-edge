import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ChevronDown, ChevronUp } from "lucide-react";
import type { AdpLadder } from "@/lib/api";
import { POS_HUE } from "@/components/Charts";
import { PlayerHover } from "@/components/PlayerHover";
import { cn } from "@/lib/utils";

/**
 * The board, in the order the room will take it.
 *
 * Everything else in this app is analysis. This is the thing you glance at:
 * who is next, who is already gone, and where your pick falls in the line.
 * Struck-through means off the board — seeing that is often the whole question.
 *
 * TEN AT A TIME, not ninety in a scroller. A ninety-row list is a list you
 * scroll rather than read, and on the clock you do not scroll — you look, and
 * the answer is either on screen or it is not. Ten rows fit in one glance;
 * the rest are a button away.
 *
 * It also opens where YOUR pick is rather than at the top. The top of the
 * board is the least useful part of it once a draft is underway: those men are
 * gone. The rows worth seeing are the ones around the pick you are about to
 * make, and landing there saves the one interaction nobody has time for.
 */
const PAGE = 10;

export function Ladder({ data, myTurn }: { data: AdpLadder | null; myTurn: boolean }) {
  const [start, setStart] = useState(0);
  const reduce = useReducedMotion();

  // Keyed off `data.players` itself, not a `?? []` fallback — that fallback is
  // a fresh array on every render, which quietly turns both memos into plain
  // function calls and makes the effect below re-run for no reason.
  const source = data?.players;
  const players = useMemo(() => source ?? [], [source]);
  const gone = useMemo(() => players.filter((p) => p.drafted).length, [players]);

  /** First row worth looking at: the best player still on the board. */
  const firstLive = useMemo(() => {
    const i = players.findIndex((p) => !p.drafted);
    return i < 0 ? 0 : Math.max(0, i - 1);
  }, [players]);

  // Follow the draft. As names come off the board the window walks down with
  // them, so the top of the list is never a column of struck-through picks --
  // unless you have deliberately paged somewhere else, which is respected.
  const [pinned, setPinned] = useState(false);
  useEffect(() => {
    if (!pinned) setStart(firstLive);
  }, [firstLive, pinned]);

  if (!data) return null;

  const max = Math.max(0, players.length - PAGE);
  const at = Math.min(start, max);
  const page = players.slice(at, at + PAGE);
  const move = (d: number) => {
    setPinned(true);
    setStart((s) => Math.min(max, Math.max(0, s + d * PAGE)));
  };

  return (
    <section className="flex min-h-0 flex-col rounded-lg border border-line bg-panel">
      <header className="flex items-center gap-2 border-b border-line px-3 py-2">
        <span className="eyebrow">Draft board</span>
        <span className="text-[10px] uppercase tracking-wider text-muted">
          {data.platform} ADP
        </span>
        <span className="num ml-auto text-[11px] text-muted">
          {gone} gone · pick {data.overall}
        </span>
      </header>

      {/* Fixed height so paging never resizes the column under the pointer. */}
      <div className="relative overflow-hidden" style={{ height: PAGE * 33 }}>
        <AnimatePresence initial={false} mode="wait">
          <motion.ul
            key={at}
            initial={reduce ? undefined : { opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduce ? undefined : { opacity: 0, y: -14 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            className="absolute inset-x-0 top-0"
          >
            {page.map((p, i) => {
              const hue = POS_HUE[p.position] ?? "#8CA096";
              const isNext = data.my_next != null
                && Math.round(p.ecr ?? 0) === data.my_next;
              return (
                <motion.li
                  key={p.player_id}
                  initial={reduce ? undefined : { opacity: 0, x: -6 }}
                  animate={{ opacity: 1, x: 0 }}
                  // A short stagger so the page arrives as a cascade rather
                  // than a flash. Long enough to read as motion, short enough
                  // that the tenth row is there before you look at it.
                  transition={{ delay: reduce ? 0 : i * 0.022, duration: 0.16 }}
                  className={cn(
                    "flex h-[33px] items-center gap-2 border-b border-line/50 px-3 transition-colors last:border-0",
                    p.drafted ? "opacity-35" : "hover:bg-raised/60",
                    isNext && !p.drafted && "bg-turf/8"
                  )}
                >
                  <span className="num w-7 shrink-0 text-[10.5px] text-muted">
                    {p.ecr ? Math.round(p.ecr) : "—"}
                  </span>
                  <PlayerHover playerId={p.player_id} className="shrink-0">
                    {p.headshot ? (
                      <img src={p.headshot} alt="" loading="lazy"
                           className="h-6 w-6 shrink-0 cursor-help rounded object-cover object-top" />
                    ) : (
                      <span className="h-6 w-6 shrink-0 rounded bg-raised" />
                    )}
                  </PlayerHover>
                  <PlayerHover playerId={p.player_id} className="min-w-0 flex-1">
                    <span className={cn("block cursor-help truncate text-xs",
                      p.drafted && "line-through decoration-muted/60")}>
                      {p.player_name}
                    </span>
                  </PlayerHover>
                  {p.rookie && (
                    <span className="rounded bg-clock/15 px-1 text-[9px] font-bold text-clock">
                      R
                    </span>
                  )}
                  <span className="w-7 text-right text-[10px] font-bold" style={{ color: hue }}>
                    {p.position}
                  </span>
                  <span className={cn("num w-9 shrink-0 text-right text-[10.5px]",
                    (p.vor ?? 0) > 0 ? "text-turf/80" : "text-muted")}>
                    {p.vor != null ? Math.round(p.vor) : "—"}
                  </span>
                </motion.li>
              );
            })}
          </motion.ul>
        </AnimatePresence>
      </div>

      <footer className="flex items-center gap-2 border-t border-line px-3 py-1.5">
        <span className="num text-[10.5px] text-muted">
          {at + 1}–{Math.min(at + PAGE, players.length)} of {players.length}
        </span>

        {pinned && at !== firstLive && (
          <button
            onClick={() => { setPinned(false); setStart(firstLive); }}
            className="text-[10.5px] text-turf underline-offset-2 hover:underline"
          >
            back to the board
          </button>
        )}

        {data.my_next != null && (
          <span className={cn("ml-auto text-[11px]", myTurn ? "text-turf" : "text-muted")}>
            {myTurn ? "Your pick is now." : `Next pick #${data.my_next}`}
          </span>
        )}

        <div className={cn("flex gap-1", data.my_next == null && "ml-auto")}>
          {([[-1, ChevronUp], [1, ChevronDown]] as const).map(([d, Icon]) => {
            const off = d < 0 ? at === 0 : at >= max;
            return (
              <button
                key={d}
                onClick={() => move(d)}
                disabled={off}
                aria-label={d < 0 ? "previous ten" : "next ten"}
                title={d < 0 ? "previous ten" : "next ten"}
                className={cn(
                  "flex h-6 w-6 items-center justify-center rounded border border-line",
                  "text-muted transition-[transform,color,border-color] duration-150",
                  off ? "cursor-default opacity-25"
                      : "hover:scale-110 hover:border-turf/50 hover:text-chalk active:scale-95"
                )}
              >
                <Icon size={13} />
              </button>
            );
          })}
        </div>
      </footer>
    </section>
  );
}
