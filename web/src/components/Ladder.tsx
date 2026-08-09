import { Fragment, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ChevronDown, ChevronUp } from "lucide-react";
import type { AdpLadder } from "@/lib/api";
import { POS_HUE } from "@/components/Charts";
import { PlayerHover } from "@/components/PlayerHover";
import { Term } from "@/components/Explain";
import { cn } from "@/lib/utils";

/**
 * The board, in the order the room will take it.
 *
 * Everything else in this app is analysis. This is the thing you glance at:
 * who is available, and where your picks fall among them.
 *
 * ONE PAGE IS ONE ROUND. The page size is the league's team count, so a page
 * is exactly the set of players who will come off between now and your next
 * turn. Ten was arbitrary; twelve, in a twelve-team league, is a unit that
 * means something — page down and you are looking at the next round.
 *
 * AVAILABLE ONLY. Drafted names used to sit here struck through, which slowly
 * turned the board into a history of the draft instead of a picture of it: by
 * round six half the rows were men nobody could take. They leave now, and the
 * next name backfills, so the page is always a full round of REAL options. Who
 * went where is a different question and the feed beside this answers it.
 */
const FALLBACK_PAGE = 12;

export function Ladder({ data, myTurn, perPage, onDraft, onGone }: {
  data: AdpLadder | null;
  myTurn: boolean;
  /** One page = one round, so this is the league's team count. */
  perPage?: number;
  /** Double-click a name to take him. Same meaning as dragging him onto your
   *  team, one gesture shorter — which matters when you are on the clock and
   *  the whole point of this app is the seconds. */
  onDraft?: (playerId: string, from?: {
    el: Element | null; name: string; headshot: string | null;
  }) => void;
  /** SHIFT + double-click: he is gone, but not to you. In a manual draft this
   *  is the commoner of the two by eleven to one — most names leaving the
   *  board leave to somebody else, and typing each one was the tax. */
  onGone?: (playerId: string, from?: {
    el: Element | null; name: string; headshot: string | null;
  }) => void;
}) {
  const [start, setStart] = useState(0);
  const reduce = useReducedMotion();

  // Keyed off `data.players` itself, not a `?? []` fallback — that fallback is
  // a fresh array on every render, which quietly turns both memos into plain
  // function calls and makes the effect below re-run for no reason.
  const PAGE = perPage && perPage > 0 ? perPage : FALLBACK_PAGE;
  const source = data?.players;
  const all = useMemo(() => source ?? [], [source]);
  const gone = useMemo(() => all.filter((p) => p.drafted).length, [all]);
  // The server already sends available players only, and reaches deeper into
  // the board to keep the quota full as picks land -- so this is a guard
  // rather than the mechanism. Left in because a stale response mid-pick
  // would otherwise show a name that is already gone.
  const players = useMemo(() => all.filter((p) => !p.drafted), [all]);

  // Back to the top when the pool shrinks under us, so paging never strands
  // you past the end of a list that just got shorter.
  const [pinned, setPinned] = useState(false);
  useEffect(() => {
    if (!pinned) setStart(0);
  }, [players.length, pinned]);

  if (!data) return null;

  /**
   * Your picks, drawn into the board where they land.
   *
   * The board is sorted by ADP, which makes it a forecast of the order names
   * come off. So a line at pick 39 sits exactly between the men expected to go
   * 38th and 40th, and answers the question you have between turns: of these,
   * who is likely to still be here when I am up. Reading that off a separate
   * "next pick #39" label meant holding two numbers in your head and comparing
   * them by hand.
   */
  const marks = (data.my_upcoming ?? []).filter(
    (m) => m.overall >= (players[0]?.ecr ?? 0) - 2);

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
              // Picks that fall between the man above and this one.
              const prev = at + i > 0 ? players[at + i - 1]?.ecr ?? 0 : 0;
              const here = marks.filter(
                (m) => m.overall > prev && m.overall <= (p.ecr ?? 0));
              return (
                <Fragment key={p.player_id}>
                {here.map((m) => (
                  <li key={`mark-${m.overall}`}
                      className="relative flex h-0 items-center">
                    <span className="absolute inset-x-0 flex items-center gap-2 px-3">
                      <span className="h-px flex-1 bg-turf/45" />
                      <span className="num whitespace-nowrap rounded bg-ink px-1.5 text-[9.5px] font-semibold uppercase tracking-wider text-turf">
                        your pick {m.overall} &middot; r{m.round}
                      </span>
                      <span className="h-px w-6 bg-turf/45" />
                    </span>
                  </li>
                ))}
                <motion.li
                  key={p.player_id}
                  draggable
                  onDragStart={(e) => {
                    (e as unknown as React.DragEvent).dataTransfer
                      .setData("text/plain", p.player_id);
                  }}
                  onDoubleClick={(e) => {
                    const from = {
                      el: e.currentTarget as unknown as Element,
                      name: p.player_name,
                      headshot: p.headshot,
                    };
                    if (e.shiftKey) onGone?.(p.player_id, from);
                    else onDraft?.(p.player_id, from);
                  }}
                  title={"double-click to draft him to your team"
                    + "\nshift + double-click if someone else took him"}
                  initial={reduce ? undefined : { opacity: 0, x: -6 }}
                  animate={{ opacity: 1, x: 0 }}
                  // A short stagger so the page arrives as a cascade rather
                  // than a flash. Long enough to read as motion, short enough
                  // that the tenth row is there before you look at it.
                  transition={{ delay: reduce ? 0 : i * 0.022, duration: 0.16 }}
                  className={cn(
                    "flex h-[33px] select-none items-center gap-2 border-b border-line/50 px-3 transition-colors last:border-0",
                    "cursor-grab hover:bg-raised/60 active:cursor-grabbing",
                    isNext && "bg-turf/8"
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
                    <span className="block cursor-help truncate text-xs">
                      {p.player_name}
                    </span>
                  </PlayerHover>
                  {p.rookie && (
                    <Term k="rookie">
                      <span className="rounded bg-clock/15 px-1 text-[9px] font-bold text-clock">
                        R
                      </span>
                    </Term>
                  )}
                  <span className="w-7 text-right text-[10px] font-bold" style={{ color: hue }}>
                    {p.position}
                  </span>
                  <span className={cn("num w-9 shrink-0 text-right text-[10.5px]",
                    (p.vor ?? 0) > 0 ? "text-turf/80" : "text-muted")}>
                    {p.vor != null ? Math.round(p.vor) : "—"}
                  </span>
                </motion.li>
                </Fragment>
              );
            })}
          </motion.ul>
        </AnimatePresence>
      </div>

      <footer className="flex items-center gap-2 border-t border-line px-3 py-1.5">
        <span className="num text-[10.5px] text-muted">
          {at + 1}–{Math.min(at + PAGE, players.length)} of {players.length} left
        </span>

        {pinned && at !== 0 && (
          <button
            onClick={() => { setPinned(false); setStart(0); }}
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
