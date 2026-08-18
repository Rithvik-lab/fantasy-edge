import { motion } from "motion/react";
import { cn } from "@/lib/utils";

/**
 * Draft, trade, waivers — one app, three jobs.
 *
 * These were never meant to be separate tools. It is one roster, one board and
 * one model; what changes is the question you are asking of them. So the mode
 * switch lives in the header and carries no state of its own: the league,
 * the sync and the projections are the same underneath, and switching is a
 * change of view rather than a change of program.
 *
 * The sliding pill is a `layoutId`, so the highlight physically travels to the
 * mode you picked instead of blinking out of one box and into another. It is a
 * small thing that makes three tabs feel like one surface.
 */
export type Mode = "draft" | "trade" | "waiver";

const MODES: { id: Mode; label: string; done?: string; hint: string }[] = [
  // The first mode changes NAME once the draft is done, because it changes
  // question. "Draft" answers who to take; there is nobody left to take. What
  // remains is the team you ended up with.
  { id: "draft", label: "Draft", done: "My team",
    hint: "who to take, and when" },
  { id: "trade", label: "Trade", hint: "what a deal does to your lineup" },
  { id: "waiver", label: "Waivers", hint: "who to add this week" },
];

export function ModeSwitch({ mode, onMode, disabled, complete }: {
  mode: Mode;
  onMode: (m: Mode) => void;
  /** Modes that need a season in progress are dead until there is one. */
  disabled?: Mode[];
  /** The draft is over, so the first mode is no longer about drafting. */
  complete?: boolean;
}) {
  return (
    <div className="flex rounded-md border border-line bg-ink/40 p-0.5">
      {MODES.map((m) => {
        const off = disabled?.includes(m.id);
        const on = mode === m.id;
        return (
          <button
            key={m.id}
            onClick={() => !off && onMode(m.id)}
            disabled={off}
            title={off ? "not until the draft is over — until then everyone unowned is still a pick" : m.hint}
            className={cn(
              "relative rounded px-3 py-1 text-[11px] font-medium transition-colors",
              on ? "text-ink" : off ? "text-muted/40" : "text-muted hover:text-chalk"
            )}
          >
            {on && (
              <motion.span
                layoutId="mode-pill"
                className="absolute inset-0 rounded bg-turf"
                transition={{ type: "spring", stiffness: 400, damping: 32 }}
              />
            )}
            <span className="relative z-10">{(complete && m.done) || m.label}</span>
          </button>
        );
      })}
    </div>
  );
}
