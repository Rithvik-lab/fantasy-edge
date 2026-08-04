import { useState } from "react";
import { Button } from "@/components/ui/button";
import { DEFS } from "@/components/Explain";
import { cn } from "@/lib/utils";

/**
 * The reference list. Everything here also appears on hover next to the number
 * itself — this is for reading through once, not for looking things up mid-pick.
 *
 * It was an accordion. Expanding one pushed the rest down and the collapse
 * animation merged rows into each other, which is exactly the "annoying to
 * look at" that got flagged. Hovering a row now reveals its body in a fixed
 * pane instead, so nothing moves.
 */
const ORDER = ["vor", "survive", "range", "score", "dropoff",
               "volatility", "rookie", "adp", "grade"] as const;

const LABEL: Record<string, string> = {
  vor: "VOR", survive: "Survive %", range: "Season range", score: "Score",
  dropoff: "Falls off", volatility: "Volatility", rookie: "Rookie",
  adp: "ADP", grade: "Roster grade",
};

export function Glossary() {
  const [sel, setSel] = useState<string>(ORDER[0]);
  const def = DEFS[sel];

  return (
    <div className="flex flex-col gap-3">
      <ul className="rounded-lg border border-line bg-panel">
        {ORDER.map((k) => (
          <li key={k}>
            <button
              onMouseEnter={() => setSel(k)}
              onFocus={() => setSel(k)}
              onClick={() => setSel(k)}
              className={cn(
                "flex w-full items-baseline gap-2 border-b border-line/50 px-3 py-2 text-left transition-colors last:border-0",
                sel === k ? "bg-raised" : "hover:bg-raised/50"
              )}
            >
              <span className={cn("w-[92px] shrink-0 text-xs font-semibold transition-colors",
                sel === k ? "text-turf" : "text-chalk")}>
                {LABEL[k]}
              </span>
              <span className="flex-1 text-[11px] text-muted">{DEFS[k].title}</span>
            </button>
          </li>
        ))}
      </ul>

      {/* Fixed pane: the body swaps in place, so no row ever moves. */}
      <div className="min-h-[132px] rounded-lg border border-line bg-panel p-3">
        <p key={sel} className="tick-in text-[11.5px] leading-relaxed text-muted">
          <span className="font-semibold text-chalk">{def.title}. </span>
          {def.body}
        </p>
      </div>
    </div>
  );
}

/** Slide-over so the reference never competes with the board for space. */
export function GlossaryDrawer() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)}
              className="h-7 px-2 text-[11px]">
        What do these mean?
      </Button>
      <div
        className={cn(
          "fixed inset-0 z-40 bg-ink/70 backdrop-blur-sm transition-opacity duration-200",
          open ? "opacity-100" : "pointer-events-none opacity-0"
        )}
        onClick={() => setOpen(false)}
      />
      <aside
        className={cn(
          "fixed right-0 top-0 z-50 flex h-full w-full max-w-md flex-col border-l border-line bg-ink transition-transform duration-300 ease-out",
          open ? "translate-x-0" : "translate-x-full"
        )}
      >
        <header className="flex items-center gap-2 border-b border-line px-4 py-3">
          <div>
            <span className="block font-semibold">Reading the board</span>
            <span className="text-[11px] text-muted">
              Hover any number in the app for the same thing, in place.
            </span>
          </div>
          <Button size="sm" variant="ghost" className="ml-auto"
                  onClick={() => setOpen(false)}>Close</Button>
        </header>
        <div className="flex-1 overflow-y-auto p-4">
          <Glossary />
        </div>
      </aside>
    </>
  );
}
