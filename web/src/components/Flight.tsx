import { useCallback, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

/**
 * The player travels from the board to your team.
 *
 * A double-click that only flashes tells you something happened. It does not
 * tell you WHAT, and in a list of ninety names where the row you clicked is
 * about to grey out and the roster is in a different column, "what" is the
 * whole question. So the face detaches and flies to the panel it landed in.
 *
 * The restraint that keeps this from being decoration: it carries one fact and
 * no more. There is no confetti, no bounce past the target, no colour it has
 * not earned — the arc is a spring from where he was to where he now is, and
 * the target ring is the receipt. Motion here is a sentence, not a flourish.
 *
 * It is also cheap to be wrong about: purely visual, portalled, pointer-events
 * off, and skipped entirely under prefers-reduced-motion. Nothing about the
 * draft depends on it having played.
 */
export interface Flying {
  id: number;
  name: string;
  headshot: string | null;
  from: DOMRect;
  to: DOMRect;
  into: string;
}

/** Where a player should land. Put this on the panel he is joining.
 *  Trade mode has two of them, so the value names which. */
export const LANDING = "data-landing";

export function useFlight() {
  const [flights, setFlights] = useState<Flying[]>([]);
  const reduce = useReducedMotion();

  /** Call with the row element that was activated. */
  const launch = useCallback((
    el: Element | null,
    name: string,
    headshot: string | null,
    into = "",
  ) => {
    if (reduce || !el) return;
    const target = document.querySelector(
      into ? `[${LANDING}="${into}"]` : `[${LANDING}]`);
    if (!target) return;
    const id = Date.now() + Math.random();
    setFlights((f) => [...f, {
      id, name, headshot, into,
      from: el.getBoundingClientRect(),
      to: target.getBoundingClientRect(),
    }]);
    window.setTimeout(
      () => setFlights((f) => f.filter((x) => x.id !== id)), 900);
  }, [reduce]);

  const layer = createPortal(
    <AnimatePresence>
      {flights.map((f) => (
        <motion.div
          key={f.id}
          aria-hidden
          className="pointer-events-none fixed z-[95] flex items-center gap-2
                     rounded-md border border-turf/50 bg-raised px-2 py-1 shadow-2xl"
          initial={{
            top: f.from.top, left: f.from.left,
            width: Math.min(f.from.width, 210), opacity: 0.95, scale: 1,
          }}
          animate={{
            // Land just inside the panel rather than on its corner: the
            // roster is a list and the top of the list is where he joins it.
            top: f.to.top + 12,
            left: f.to.left + 12,
            width: 150,
            opacity: 0,
            scale: 0.82,
          }}
          exit={{ opacity: 0 }}
          transition={{
            type: "spring", stiffness: 210, damping: 26, mass: 0.7,
            opacity: { duration: 0.55, times: [0, 1], ease: "easeIn" },
          }}
        >
          {f.headshot ? (
            <img src={f.headshot} alt=""
                 className="h-6 w-6 shrink-0 rounded object-cover object-top" />
          ) : <span className="h-6 w-6 shrink-0 rounded bg-panel" />}
          <span className="truncate text-[11px] font-medium text-chalk">
            {f.name}
          </span>
        </motion.div>
      ))}
    </AnimatePresence>,
    document.body,
  );

  return {
    launch,
    layer,
    /** Anything in flight at all — for the single-target case. */
    arriving: flights.length > 0,
    /** WHICH panel is receiving. Trade mode has two landing sites, and a bare
     *  boolean rang both of them for a player joining one; a receipt that
     *  confirms the wrong thing is worse than no receipt. */
    landingIn: (side: string) => flights.some((f) => f.into === side),
  };
}

/** The receipt: a ring on the panel, once, as he lands. */
export function Landed({ on }: { on: boolean }) {
  const reduce = useReducedMotion();
  if (reduce) return null;
  return (
    <AnimatePresence>
      {on && (
        <motion.span
          aria-hidden
          className="pointer-events-none absolute inset-0 rounded-lg ring-2 ring-turf"
          initial={{ opacity: 0 }}
          animate={{ opacity: [0, 0.9, 0] }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.7, times: [0, 0.35, 1], ease: "easeOut" }}
        />
      )}
    </AnimatePresence>
  );
}
