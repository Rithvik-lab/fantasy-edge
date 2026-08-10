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

/**
 * A kick through the uprights, on a loop.
 *
 * Drawn rather than animated frame by frame: the ball follows a real arc
 * (`offsetPath` along the same quadratic the goalposts are placed against), so
 * it rises, tumbles, and clears the crossbar instead of sliding along a line
 * and being called a kick. One kick every three seconds, with the ball resting
 * on the tee between them — a waiting screen should breathe.
 */
function FieldGoal() {
  const reduce = useReducedMotion();
  // Tee at the left, uprights at the right, apex above the crossbar.
  const arc = "M 14 92 Q 96 2 178 46";

  return (
    <svg viewBox="0 0 200 110" className="h-24 w-full" aria-hidden>
      {/* Turf line and the hash the kick starts from. */}
      <line x1="0" y1="98" x2="200" y2="98"
            stroke="currentColor" className="text-line" strokeWidth="1.5" />
      {[30, 60, 90, 120, 150].map((x) => (
        <line key={x} x1={x} y1="94" x2={x} y2="98"
              stroke="currentColor" className="text-line" strokeWidth="1" />
      ))}

      {/* The posts. Crossbar at y=62, uprights rising out of it. */}
      <g stroke="currentColor" className="text-clock/70" strokeWidth="2.5"
         strokeLinecap="round" fill="none">
        <path d="M 168 98 L 168 62" />
        <path d="M 152 62 L 184 62" />
        <path d="M 152 62 L 152 22" />
        <path d="M 184 62 L 184 22" />
      </g>

      {/* The flight path, faint, so the arc reads even at the start. */}
      <path d={arc} fill="none" stroke="currentColor"
            className="text-turf/15" strokeWidth="1" strokeDasharray="2 4" />

      <motion.g
        style={reduce ? { offsetDistance: "0%" }
                      : { offsetPath: `path("${arc}")`, offsetRotate: "0deg" }}
        animate={reduce ? undefined
          : { offsetDistance: ["0%", "0%", "100%", "100%"],
              opacity: [1, 1, 1, 0] }}
        transition={{ duration: 3, times: [0, 0.18, 0.78, 1],
                      repeat: Infinity, ease: "easeOut" }}
      >
        {/* End-over-end, which is what a kicked ball does. */}
        <motion.g
          animate={reduce ? undefined : { rotate: [0, 380] }}
          transition={{ duration: 3, times: [0, 1], repeat: Infinity,
                        ease: "linear" }}
        >
          <ellipse rx="5.5" ry="3.6" className="fill-clock" />
          <line x1="-2" y1="0" x2="2" y2="0"
                stroke="currentColor" className="text-ink" strokeWidth="0.8" />
        </motion.g>
      </motion.g>
    </svg>
  );
}

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

        <FieldGoal />

        {/* A board filling in, left to right. It is the thing being loaded, so
            it may as well be the thing on screen. */}
        <div className="mt-2 space-y-1.5">
          {Array.from({ length: 4 }).map((_, row) => (
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
