import { useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * What every number on screen means, and — more usefully — what it is FOR.
 *
 * A metric nobody can interpret is decoration. Each entry says what decision
 * the number changes, because that is the only reason it is on the page.
 */
const TERMS: { term: string; short: string; body: string }[] = [
  {
    term: "VOR",
    short: "Value over replacement",
    body:
      "Points above the worst player you could be FORCED to start at that position all season. In a 12-team league starting 2 RB, roughly RB29 is free — so a back is only worth what he produces above that line. This is the only honest way to compare a WR to a TE, because raw projected points are not comparable across positions.",
  },
  {
    term: "Survive %",
    short: "Chance he lasts to your next pick",
    body:
      "Draft position modelled as a bell curve around his ADP, widened to match how much real draft rooms deviate from consensus. Low means take him now or lose him. High means you can spend this pick elsewhere and still get him — which is the single most common way people waste a pick.",
  },
  {
    term: "Season range",
    short: "Floor to ceiling, 20th to 80th percentile",
    body:
      "Where his season plausibly lands, folding in both how well he scores and how many games he plays. Measured from what players ranked here have historically done — including the ones who got hurt or busted. Two players with the same projection can have completely different ranges, and the range is the part that decides whether he is an anchor or a swing.",
  },
  {
    term: "Falls off",
    short: "Positional cliff",
    body:
      "How much value the position loses between now and your next turn. It is what makes taking a tight end early either smart or a reach: only right when the drop at TE is steeper than the drop at whatever else you would have taken.",
  },
  {
    term: "Score",
    short: "What the ranking is actually built on",
    body:
      "Not just his value — his value PLUS what the board still owes you at your next pick. Taking someone who would have lasted barely changes that second part, so you paid a pick for nothing. This is what charges you for reaching instead of only rewarding scarcity.",
  },
  {
    term: "Volatility",
    short: "How swingy he is, against his position",
    body:
      "Spread of his season relative to its own size, so a high scorer is not called volatile just for being big. Early picks want low — those are the anchors. Late picks and bench spots want high, because a bust in round 13 costs nothing and a hit wins the league.",
  },
  {
    term: "Rookie",
    short: "Priced as a gamble at the top of the board",
    body:
      "Measured against veterans at the same projection, rookies are not more volatile overall — the total spread is the same. What differs is the downside, and only near the top: there they return about 12% less than projected and fail to reach half their projection 24.7% of the time against a veteran's 13.7%. Their floor is discounted accordingly; their ceiling is not touched, because the data shows no extra upside paying for it.",
  },
  {
    term: "Roster grade",
    short: "100 = your fair share",
    body:
      "Your starting lineup against an even share of the league's total startable value. Above 100 means you are holding more than one team's worth. It is a running score, not a prediction.",
  },
];

export function Glossary() {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <section className="rounded-lg border border-line bg-panel">
      <header className="border-b border-line px-3 py-2">
        <span className="eyebrow">What the numbers mean</span>
      </header>
      <ul className="divide-y divide-line/60">
        {TERMS.map((t) => {
          const isOpen = open === t.term;
          return (
            <li key={t.term}>
              <button
                onClick={() => setOpen(isOpen ? null : t.term)}
                className="flex w-full items-baseline gap-2 px-3 py-2 text-left transition-colors hover:bg-raised/60"
              >
                <span className="w-[86px] shrink-0 text-xs font-semibold text-chalk">
                  {t.term}
                </span>
                <span className="flex-1 text-[11px] text-muted">{t.short}</span>
                <span className={cn("text-muted transition-transform duration-200",
                  isOpen && "rotate-90")}>›</span>
              </button>
              <div
                className={cn(
                  "grid overflow-hidden transition-[grid-template-rows] duration-250 ease-out",
                  isOpen ? "grid-rows-[1fr]" : "grid-rows-[0fr]"
                )}
              >
                <div className="min-h-0">
                  <p className="px-3 pb-3 text-[11.5px] leading-relaxed text-muted">
                    {t.body}
                  </p>
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** Slide-over panel so the glossary never competes with the board for space. */
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
          <span className="font-semibold">Reading the board</span>
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
