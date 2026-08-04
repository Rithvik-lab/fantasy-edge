import type { Shortlist as ShortlistData } from "@/lib/api";
import { PlayerCard } from "@/components/PlayerCard";
import { Button } from "@/components/ui/button";
import { Term } from "@/components/Explain";
import { cn } from "@/lib/utils";

/**
 * The bar that is always there. Three names, ranked, with faces.
 *
 * It is pinned to the bottom on purpose. The failure this whole app exists to
 * fix is advice arriving after the pick, so the answer must never be more than
 * a glance away and must never require scrolling to find.
 */
export function Shortlist({
  data, onDraft, onSkip, onRefresh, busy, myTurn, stale,
}: {
  data: ShortlistData | null;
  onDraft: (id: string) => void;
  onSkip: (id: string, name: string) => void;
  onRefresh: () => void;
  busy: boolean;
  myTurn: boolean;
  stale: boolean;
}) {
  if (!data || !data.suggestions.length) {
    return (
      <div className="border-t border-line bg-panel/95 px-4 py-6 text-center text-sm text-muted">
        {data?.note ?? "No board yet."}
      </div>
    );
  }

  // One scale across all three, so the range bars are actually comparable.
  const max = Math.max(...data.suggestions.map((s) => s.ceiling ?? 0), 1);

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
          <Button size="sm" variant="ghost" className="ml-auto" onClick={onRefresh} disabled={busy}>
            Re-roll
          </Button>
        </div>

        <div className="grid gap-3 md:grid-cols-3">
          {data.suggestions.map((s, i) => (
            <PlayerCard
              key={s.player_id}
              s={s}
              rank={i + 1}
              max={max}
              busy={busy}
              onDraft={() => onDraft(s.player_id)}
              onSkip={() => onSkip(s.player_id, s.player_name)}
            />
          ))}
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
