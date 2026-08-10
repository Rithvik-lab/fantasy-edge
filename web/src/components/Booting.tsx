import { useEffect, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

/**
 * The wait before the app knows which app it is.
 *
 * Opening a saved league means reading its state, its board, its roster and
 * the room, and only THEN knowing whether you are mid-draft or looking at a
 * finished team. Rendering before that answer arrives showed three seconds of
 * live-draft interface — shortlist, board, "take these when it is your turn" —
 * to someone whose draft ended in August. The flash was not slow loading; it
 * was the app guessing and then correcting itself in public.
 *
 * So it holds, and says what it is doing. The steps are the real sequence in
 * the order it happens, which is why the last one is the question the screen
 * exists to answer.
 */
const STEPS = [
  "opening your league",
  "pricing the board",
  "reading your roster",
  "working out where the draft is",
];

export function Booting({ name }: { name?: string }) {
  const [i, setI] = useState(0);
  const reduce = useReducedMotion();

  useEffect(() => {
    // Advances on a timer rather than on real progress, and stops at the last
    // step instead of looping. A caption that cycles forever reads as stuck;
    // one that arrives and waits reads as nearly done — which it is.
    const t = setInterval(
      () => setI((n) => Math.min(STEPS.length - 1, n + 1)), 520);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="flex h-full items-center justify-center px-6">
      <div className="w-full max-w-sm">
        <div className="flex items-baseline gap-2">
          <span className="text-lg font-bold tracking-tight">FantasyEdge</span>
          {name && <span className="truncate text-xs text-muted">{name}</span>}
        </div>

        {/* A board filling in, left to right. It is the thing being loaded, so
            it may as well be the thing on screen. */}
        <div className="mt-4 space-y-1.5">
          {Array.from({ length: 6 }).map((_, row) => (
            <div key={row} className="flex items-center gap-2">
              <motion.span
                className="h-1.5 w-5 rounded-full bg-line"
                animate={reduce ? undefined : { opacity: [0.35, 1, 0.35] }}
                transition={{ duration: 1.6, repeat: Infinity, delay: row * 0.09 }}
              />
              <motion.span
                className="h-6 w-6 rounded bg-line/70"
                animate={reduce ? undefined : { opacity: [0.3, 0.8, 0.3] }}
                transition={{ duration: 1.6, repeat: Infinity, delay: row * 0.09 + 0.05 }}
              />
              <motion.span
                className="h-1.5 flex-1 rounded-full bg-line/60"
                initial={{ scaleX: 0.2, originX: 0 }}
                animate={reduce ? undefined
                  : { scaleX: [0.2, 1, 0.2], opacity: [0.4, 0.9, 0.4] }}
                transition={{ duration: 1.6, repeat: Infinity, delay: row * 0.09 + 0.1 }}
              />
            </div>
          ))}
        </div>

        <div className="mt-4 flex items-center gap-2">
          <span className="relative flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping
                             rounded-full bg-turf opacity-60" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-turf" />
          </span>
          <AnimatePresence mode="wait">
            <motion.span
              key={i}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.18 }}
              className="text-[11.5px] text-muted"
            >
              {STEPS[i]}…
            </motion.span>
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}
