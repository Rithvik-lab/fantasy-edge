import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
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
               "volatility", "rookie", "adp", "board_rank", "shares",
               "pos_rank", "grade", "off_board", "season_total", "curve_now",
               "curve_after", "crossover", "overlap"] as const;

const LABEL: Record<string, string> = {
  vor: "VOR", survive: "Survive %", range: "Season range", score: "Score",
  dropoff: "Falls off", volatility: "Volatility", rookie: "Rookie",
  adp: "ADP", board_rank: "Board rank", shares: "Catches / TDs",
  pos_rank: "Position rank", grade: "Roster grade",
  off_board: "Off the board", season_total: "Season points", curve_now: "Grey curve",
  curve_after: "Green curve", crossover: "Break-even", overlap: "Overlap",
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

/**
 * Slide-over so the reference never competes with the board for space.
 *
 * It renders at the body, and it has to. The button lives in the header, and
 * the header carries `backdrop-blur` — a backdrop-filter makes an element the
 * containing block for its fixed-position descendants, so `fixed inset-0` and
 * `h-full` were resolving against a 44px-tall header instead of the window.
 * The drawer was opening the whole time, as a sliver behind the toolbar.
 */
export function GlossaryDrawer() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [open]);

  return (
    <>
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)}
              className="h-7 px-2 text-[11px]">
        What do these mean?
      </Button>
      {createPortal(
        <>
          <div
            className={cn(
              "fixed inset-0 z-[90] bg-ink/70 backdrop-blur-sm transition-opacity duration-200",
              open ? "opacity-100" : "pointer-events-none opacity-0"
            )}
            onClick={() => setOpen(false)}
          />
          <aside
            aria-hidden={!open}
            className={cn(
              "fixed right-0 top-0 z-[91] flex h-full w-full max-w-md flex-col border-l border-line bg-ink transition-transform duration-300 ease-out",
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
        </>,
        document.body,
      )}
    </>
  );
}
