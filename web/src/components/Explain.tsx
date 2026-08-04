import { type ReactNode } from "react";
import { Floating, useHover } from "@/components/Floating";
import { cn } from "@/lib/utils";

/**
 * Hover a number, get told what it is.
 *
 * The first version buried this in a click-to-open accordion, which meant the
 * explanation was never where the number was — you had to leave what you were
 * reading, find the term, and come back. A metric explains itself best beside
 * itself, so it lives on hover now and the panel is only a reference.
 */
export const DEFS: Record<string, { title: string; body: string }> = {
  vor: {
    title: "Value over replacement",
    body:
      "Points above the worst player you could be FORCED to start at this position all season. In a 12-team league starting 2 RB, roughly RB29 is free — so a back is only worth what he beats that by. It is the only honest way to compare a WR to a TE.",
  },
  survive: {
    title: "Chance he lasts to your next pick",
    body:
      "Draft position modelled as a bell curve around his ADP, widened to match how far real rooms stray from consensus. Low means take him now or lose him. High means you can spend this pick elsewhere and still get him — the most common way people waste a pick.",
  },
  range: {
    title: "Season floor to ceiling",
    body:
      "The 20th to 80th percentile of his season, folding in both how well he scores and how many games he plays. Measured from what players ranked here have actually done — including the ones who got hurt. Two players with the same projection can have very different ranges, and the range is what decides anchor versus swing.",
  },
  dropoff: {
    title: "Positional cliff",
    body:
      "How much value this position loses between now and your next turn. It is what makes taking a tight end early either smart or a reach: only right when the drop at TE is steeper than the drop at whatever else you would have taken.",
  },
  score: {
    title: "What the ranking is built on",
    body:
      "His value PLUS what the board still owes you at your next pick. Taking someone who would have lasted barely changes that second part — so you paid a pick for nothing. This is what charges you for reaching instead of only rewarding scarcity.",
  },
  volatility: {
    title: "How swingy he is, for his position",
    body:
      "Spread of his season relative to its own size, so a big scorer is not called volatile just for being big. Early picks want low — those are your anchors. Bench spots want high, because a bust in round 13 costs nothing and a hit wins the league.",
  },
  rookie: {
    title: "Priced as a gamble at the top",
    body:
      "Against veterans at the same projection, rookies are not more volatile overall — the total spread matches. The downside differs, and only near the top of the board: there they return about 12% less than projected and miss half their projection 24.7% of the time against a veteran's 13.7%. Their floor is discounted; their ceiling is untouched, because the data shows no extra upside paying for it.",
  },
  adp: {
    title: "Average draft position",
    body:
      "Where this platform's drafters actually take him. Raw ADP is not comparable between ESPN and Yahoo — Yahoo runs about 15 picks lower on average, a scale difference rather than disagreement — which is why everything here works off the rank, not the number.",
  },
  grade: {
    title: "Roster grade",
    body:
      "Your starting lineup against an even share of the league's total startable value. 100 means you hold exactly one team's worth. A running score, not a prediction.",
  },
  board_rank: {
    title: "ESPN board rank",
    body:
      "Where ESPN's own ranking puts him — and that is the order the list in your draft room is sorted by. It is a different number from ADP: ESPN ranks Lamar Jackson 88th while drafters actually take him around 35th. Everything here runs off ADP, because whether he lasts depends on what the room does, not on what ESPN advises. This sits beside it so the two reconcile when your screen disagrees with the model.",
  },
  shares: {
    title: "Where his points come from",
    body:
      "How much of the projection is catches, and how much is touchdowns. The two do not carry forward the same way — target volume is far stickier season to season than end-zone luck — so two players at the same projection are different bets when one of them got there on touchdowns.",
  },
  pos_rank: {
    title: "Rank within his own position",
    body:
      "Where he sits among players at his spot by value over replacement. This is the number that decides a pick, because you are never choosing between all players — you are choosing between the best left at each position and what the drop looks like behind him.",
  },
};

/**
 * Underlined term that explains itself on hover.
 *
 * The panel is a portal, not an absolute child. As an absolute child it was
 * 256px wide anchored at the centre of the term, which on any right-hand
 * number stuck out past the window and gave the whole page a horizontal
 * scrollbar. It also could not escape the profile card, which clips its own
 * corners — so a term inside a card explained nothing.
 *
 * z sits above the profile card on purpose: these nest, and the explanation
 * has to land on top of the thing it is explaining.
 */
export function Term({ k, children, className }: {
  k: keyof typeof DEFS | string;
  children: ReactNode;
  className?: string;
}) {
  const { ref, anchor, show, hide } = useHover({ delay: 80, grace: 40 });
  const def = DEFS[k];
  if (!def) return <>{children}</>;

  return (
    <span
      ref={ref}
      className={cn("inline-flex cursor-help", className)}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      tabIndex={0}
    >
      <span className="underline decoration-dotted decoration-muted/50 underline-offset-[3px]">
        {children}
      </span>
      {anchor && (
        <Floating anchor={anchor} width={264} z={80}>
          <div
            role="tooltip"
            className="rounded-md border border-line bg-raised p-2.5 shadow-2xl"
          >
            <span className="block text-[11px] font-semibold text-chalk">{def.title}</span>
            <span className="mt-1 block text-[11px] leading-relaxed text-muted">
              {def.body}
            </span>
          </div>
        </Floating>
      )}
    </span>
  );
}
