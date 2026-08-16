import { useEffect, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

/**
 * The wait, made into the thing you are waiting for.
 *
 * Four thousand seasons genuinely get played out to answer this, and that is
 * more interesting than a spinner. So the screen shows the shape of it: a
 * season filling week by week, outcomes piling into a distribution, and the
 * running count of seasons played.
 *
 * Everything here is fake in the honest sense — it is an animation, not a
 * readout of the actual simulation, and it never shows a number that could be
 * mistaken for a result. The only real thing is the phase caption, which
 * names what the engine is doing in the order it does it.
 */

const PHASES = [
  "reading both rosters",
  "filling your lineup, week by week",
  "playing the season out",
  "pricing what the roster spots cost",
];

/** A histogram that assembles itself, the way the season band is built. */
function Distribution() {
  const bars = 23;
  return (
    <div className="flex h-16 items-end gap-[3px]">
      {Array.from({ length: bars }).map((_, i) => {
        // Bell-ish: tall in the middle, thin at the tails.
        const x = (i - (bars - 1) / 2) / ((bars - 1) / 2);
        const h = Math.exp(-2.2 * x * x);
        return (
          <motion.span
            key={i}
            className="w-2 rounded-t-[2px]"
            style={{
              background: i < bars * 0.25 || i > bars * 0.78
                ? "color-mix(in oklab, var(--color-turf) 35%, transparent)"
                : "var(--color-turf)",
            }}
            initial={{ height: 2, opacity: 0.25 }}
            animate={{ height: `${Math.max(6, h * 100)}%`, opacity: [0.25, 1, 0.55] }}
            transition={{
              duration: 1.9,
              repeat: Infinity,
              repeatType: "reverse",
              delay: Math.abs(x) * 0.5,
              ease: "easeOut",
            }}
          />
        );
      })}
    </div>
  );
}

/** Seventeen weeks, filling left to right, over and over. */
function Weeks() {
  return (
    <div className="flex gap-1">
      {Array.from({ length: 17 }).map((_, w) => (
        <motion.span
          key={w}
          className="h-5 w-1.5 rounded-full bg-chalk/70"
          initial={{ scaleY: 0.18, opacity: 0.2 }}
          animate={{ scaleY: [0.18, 1, 0.18], opacity: [0.2, 0.9, 0.2] }}
          transition={{ duration: 1.7, repeat: Infinity, delay: w * 0.06,
                        ease: "easeInOut" }}
        />
      ))}
    </div>
  );
}

/** What opening trade mode actually does, in the order it does it. */
const READING = [
  "reading twelve rosters",
  "pricing every man on them",
  "working out who is short of what",
];

/**
 * The league being read, drawn as the league being read.
 *
 * NOT the simulation below it. That one says four thousand seasons are being
 * played, which is true of a trade verdict and false of a roster fetch — and
 * running it for every wait had trade mode open with a racing season counter
 * to do something else entirely.
 *
 * So this shows the real shape of this particular wait: twelve teams filling
 * in, one after another, which is exactly what the engine is doing. Same
 * weight of animation, no claim that is not true.
 */
export function Waiting({ label, teams = 12 }: { label: string; teams?: number }) {
  const [i, setI] = useState(0);
  const reduce = useReducedMotion();

  useEffect(() => {
    const t = setInterval(() => setI((n) => Math.min(READING.length - 1, n + 1)), 700);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="rounded-lg border border-line bg-panel p-4">
      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3 lg:grid-cols-4">
        {Array.from({ length: teams }).map((_, t) => (
          <motion.div
            key={t}
            className="flex items-center gap-1.5 rounded border border-line/60 px-2 py-1.5"
            initial={{ opacity: 0.25 }}
            animate={reduce ? undefined : { opacity: [0.25, 1, 0.25] }}
            transition={{ duration: 1.8, repeat: Infinity, delay: t * 0.11 }}
          >
            <span className="h-4 w-4 shrink-0 rounded-full bg-line" />
            <span className="h-1.5 flex-1 rounded-full bg-line/70" />
          </motion.div>
        ))}
      </div>

      <div className="mt-3 flex items-center gap-2">
        <span className="relative flex h-1.5 w-1.5 shrink-0">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-turf opacity-60" />
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
            {label} — {READING[i]}…
          </motion.span>
        </AnimatePresence>
      </div>
    </div>
  );
}


export function Simulating({ label }: { label?: string }) {
  const [phase, setPhase] = useState(0);
  const [seasons, setSeasons] = useState(0);

  useEffect(() => {
    const p = setInterval(() => setPhase((n) => (n + 1) % PHASES.length), 1100);
    // Counts toward 4,000 and holds there. It is a pace, not a progress bar —
    // claiming to know how far along we are would be a lie with a number on it.
    const c = setInterval(
      () => setSeasons((n) => (n >= 4000 ? 4000 : Math.min(4000, n + 137))), 40);
    return () => { clearInterval(p); clearInterval(c); };
  }, []);

  return (
    <div className="relative overflow-hidden rounded-lg border border-line bg-panel">
      {/* A sweep of light travelling across the panel, once per phase. */}
      <motion.div
        aria-hidden
        className="pointer-events-none absolute inset-y-0 w-1/3"
        style={{
          background:
            "linear-gradient(90deg, transparent, color-mix(in oklab, var(--color-turf) 8%, transparent), transparent)",
        }}
        initial={{ x: "-100%" }}
        animate={{ x: "320%" }}
        transition={{ duration: 2.2, repeat: Infinity, ease: "linear" }}
      />

      <div className="relative flex flex-col items-center gap-4 px-6 py-8">
        <Distribution />
        <Weeks />

        <div className="flex flex-col items-center gap-1">
          <span className="num text-2xl font-bold leading-none text-chalk">
            {seasons.toLocaleString()}
            <span className="ml-1 text-[11px] font-normal text-muted">
              seasons played
            </span>
          </span>
          <AnimatePresence mode="wait">
            <motion.span
              key={label ?? phase}
              initial={{ opacity: 0, y: 5 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -5 }}
              transition={{ duration: 0.22 }}
              className="text-[11.5px] text-muted"
            >
              {label ?? PHASES[phase]}…
            </motion.span>
          </AnimatePresence>
        </div>

        <div className="flex gap-1.5">
          {PHASES.map((_, i) => (
            <motion.span
              key={i}
              className="h-1 rounded-full"
              animate={{
                width: i === phase ? 18 : 6,
                backgroundColor: i === phase
                  ? "var(--color-turf)" : "var(--color-line)",
              }}
              transition={{ type: "spring", stiffness: 350, damping: 30 }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
