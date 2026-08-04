import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import type { Shortlist as ShortlistData } from "@/lib/api";
import { PlayerCard } from "@/components/PlayerCard";
import { Term } from "@/components/Explain";
import { cn } from "@/lib/utils";

/** Three across, and however many pages that comes to. */
export const PER_PAGE = 3;
/** How deep the ranking is fetched. Four pages is further than anyone reaches. */
export const NAMES = 12;

const GAP = 12;                    // matches gap-3, in px — the maths needs it exact
const RAIL = 28;                   // room reserved at each edge for an arrow

/** Weighty rather than bouncy. This slides a board, not a toy. */
const SPRING = { type: "spring" as const, stiffness: 300, damping: 34, mass: 0.9 };

/**
 * The bar that is always there. Three names, ranked, with faces — and the next
 * three one arrow away.
 *
 * It is pinned to the bottom on purpose. The failure this whole app exists to
 * fix is advice arriving after the pick, so the answer must never be more than
 * a glance away and must never require scrolling to find.
 *
 * Paging sideways rather than stacking downward is deliberate: the top three
 * are the answer, and four through twelve are the argument. Pushing them into
 * a second row would trade board space for names you mostly do not read, so
 * they live off-screen and slide in when asked. Nothing here scrolls — the
 * viewport is clipped and the track is driven by the buttons.
 */
export function Shortlist({
  data, onDraft, onSkip, busy, myTurn, stale,
}: {
  data: ShortlistData | null;
  onDraft: (id: string) => void;
  onSkip: (id: string, name: string) => void;
  busy: boolean;
  myTurn: boolean;
  stale: boolean;
}) {
  const [page, setPage] = useState(0);
  const [vw, setVw] = useState(0);
  const view = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();

  const all = data?.suggestions ?? [];
  const pages: typeof all[] = [];
  for (let i = 0; i < all.length; i += PER_PAGE) pages.push(all.slice(i, i + PER_PAGE));
  const last = Math.max(0, pages.length - 1);
  const at = Math.min(page, last);

  // The slide is a pixel translate off the measured viewport, not a percentage
  // of the track — the track is several pages wide plus gaps, so a percentage
  // would drift further out of true with every page.
  useLayoutEffect(() => {
    const el = view.current;
    if (!el) return;
    const measure = () => setVw(el.clientWidth);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [data?.suggestions.length]);

  // A pick landed, or a name was skipped: the top three are different now and
  // that is what you need to see. Snap home rather than leaving someone
  // staring at page three of a ranking that no longer exists.
  const head = all[0]?.player_id;
  useEffect(() => { setPage(0); }, [data?.overall, head]);

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const el = document.activeElement;
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return;
      if (e.key === "ArrowRight") setPage((p) => Math.min(last, p + 1));
      if (e.key === "ArrowLeft") setPage((p) => Math.max(0, p - 1));
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [last]);

  if (!data || !all.length) {
    return (
      <div className="border-t border-line bg-panel/95 px-4 py-6 text-center text-sm text-muted">
        {data?.note ?? "No board yet."}
      </div>
    );
  }

  // One scale across every page, so a range bar means the same thing on page
  // three as it does on page one.
  const max = Math.max(...all.map((s) => s.ceiling ?? 0), 1);
  const from = at * PER_PAGE + 1;
  const to = Math.min(all.length, from + PER_PAGE - 1);

  const Arrow = ({ dir }: { dir: -1 | 1 }) => {
    const off = dir < 0 ? at === 0 : at === last;
    return (
      <button
        onClick={() => setPage(Math.min(last, Math.max(0, at + dir)))}
        disabled={off}
        aria-label={dir < 0 ? "Previous three" : "Next three"}
        title={dir < 0 ? `Back to ${from - PER_PAGE}–${from - 1}` : `See ${to + 1}–${Math.min(all.length, to + PER_PAGE)}`}
        className={cn(
          "absolute top-1/2 z-20 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full",
          "border border-line bg-raised text-muted shadow-lg",
          "transition-[transform,color,border-color] duration-150",
          dir < 0 ? "left-0" : "right-0",
          off
            ? "cursor-default opacity-25"
            : "hover:scale-110 hover:border-turf/50 hover:text-chalk active:scale-95"
        )}
      >
        {dir < 0 ? <ChevronLeft size={17} /> : <ChevronRight size={17} />}
      </button>
    );
  };

  return (
    <div
      className={cn(
        "border-t bg-panel/95 backdrop-blur transition-colors",
        myTurn ? "border-turf/50" : "border-line"
      )}
    >
      <div className="mx-auto max-w-[1400px] px-4 py-3">
        <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1">
          <span className="eyebrow">
            {myTurn ? "You are on the clock" : "Take these when it is your turn"}
          </span>
          <span className="text-[11px] text-muted num">
            Round {data.round} · Pick {data.pick} · Overall {data.overall}
          </span>
          {data.picks_until_next != null && (
            <Term k="survive">
              <span className="num text-[11px] text-muted">
                {data.picks_until_next} picks until your next turn
              </span>
            </Term>
          )}
          {stale && (
            <span className="text-[11px] font-medium text-clock">
              board moved — refreshing
            </span>
          )}

          {/* Where you are in the ranking, and how far it goes. */}
          <div className="ml-auto flex items-center gap-2">
            <span className="num text-[11px] text-muted">
              {from}–{to} of {all.length}
            </span>
            <div className="flex gap-1">
              {pages.map((_, i) => (
                <button
                  key={i}
                  onClick={() => setPage(i)}
                  aria-label={`Names ${i * PER_PAGE + 1} onward`}
                  className={cn(
                    "h-1.5 rounded-full transition-all duration-200",
                    i === at ? "w-4 bg-turf" : "w-1.5 bg-line hover:bg-muted"
                  )}
                />
              ))}
            </div>
          </div>
        </div>

        <div className="relative" style={{ paddingLeft: RAIL, paddingRight: RAIL }}>
          <Arrow dir={-1} />
          <Arrow dir={1} />

          {/* Clipped, never scrollable. The arrows are the only way across —
              a stray trackpad swipe must not be able to desync the track from
              the page indicator. */}
          <div ref={view} className="overflow-hidden">
            <motion.div
              className="flex"
              style={{ gap: GAP }}
              animate={{ x: -at * (vw + GAP) }}
              transition={reduce ? { duration: 0 } : SPRING}
            >
              {pages.map((group, i) => (
                <motion.div
                  key={i}
                  className="grid shrink-0 grid-cols-3"
                  style={{ width: vw, gap: GAP }}
                  // The page you are not on recedes, so the one arriving reads
                  // as arriving rather than as three more cards in a row.
                  animate={{ opacity: i === at ? 1 : 0.3 }}
                  transition={{ duration: reduce ? 0 : 0.22 }}
                >
                  {group.map((s, j) => (
                    <PlayerCard
                      key={s.player_id}
                      s={s}
                      rank={i * PER_PAGE + j + 1}
                      max={max}
                      busy={busy}
                      onDraft={() => onDraft(s.player_id)}
                      onSkip={() => onSkip(s.player_id, s.player_name)}
                    />
                  ))}
                </motion.div>
              ))}
            </motion.div>
          </div>
        </div>

        {data.compare && (
          <p className="mt-2 text-[11.5px] leading-snug text-muted">
            <Term k="score"><span className="text-turf/80">Why this order: </span></Term>
            {data.compare}
          </p>
        )}
      </div>
    </div>
  );
}
