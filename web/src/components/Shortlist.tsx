import type { Shortlist as ShortlistData } from "@/lib/api";
import { PlayerCard } from "@/components/PlayerCard";
import { Term } from "@/components/Explain";
import { cn } from "@/lib/utils";

/**
 * The bar that is always there. Three names, ranked, with faces.
 *
 * It is pinned to the bottom on purpose. The failure this whole app exists to
 * fix is advice arriving after the pick, so the answer must never be more than
 * a glance away and must never require scrolling to find.
 */
export const MIN_NAMES = 3;
export const MAX_NAMES = 8;

/**
 * How many names the list shows.
 *
 * This replaced a Re-roll button, which was the wrong verb: re-rolling threw
 * the ranking away and asked for a different one, when what you actually want
 * at pick time is to see FURTHER DOWN the same ranking. Down adds the next
 * name, up takes one back. The order never changes underneath you.
 */
function Arrows({ count, onCount, busy }: {
  count: number; onCount: (n: number) => void; busy: boolean;
}) {
  const btn = "flex h-5 w-5 items-center justify-center rounded border border-line text-[9px] leading-none text-muted transition-colors hover:border-turf/50 hover:text-chalk disabled:opacity-30 disabled:hover:border-line disabled:hover:text-muted";
  return (
    <div className="ml-auto flex items-center gap-1.5">
      <span className="text-[10px] uppercase tracking-wider text-muted">showing</span>
      <span className="num text-[11px] font-semibold text-chalk">{count}</span>
      <div className="flex gap-1">
        <button
          className={btn}
          onClick={() => onCount(count - 1)}
          disabled={busy || count <= MIN_NAMES}
          title="One fewer name"
          aria-label="Show one fewer name"
        >
          ▲
        </button>
        <button
          className={btn}
          onClick={() => onCount(count + 1)}
          disabled={busy || count >= MAX_NAMES}
          title="Add the next name down the board"
          aria-label="Add another name"
        >
          ▼
        </button>
      </div>
    </div>
  );
}

export function Shortlist({
  data, onDraft, onSkip, count, onCount, busy, myTurn, stale,
}: {
  data: ShortlistData | null;
  onDraft: (id: string) => void;
  onSkip: (id: string, name: string) => void;
  count: number;
  onCount: (n: number) => void;
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

  // One scale across all of them, so the range bars are actually comparable.
  const max = Math.max(...data.suggestions.map((s) => s.ceiling ?? 0), 1);
  const top = data.suggestions.slice(0, MIN_NAMES);
  const rest = data.suggestions.slice(MIN_NAMES);

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
          <Arrows count={count} onCount={onCount} busy={busy} />
        </div>

        <div className="grid gap-3 md:grid-cols-3">
          {top.map((s, i) => (
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

        {rest.length > 0 && (
          <div className="mt-2 space-y-1.5">
            {rest.map((s, i) => (
              <PlayerCard
                key={s.player_id}
                s={s}
                rank={i + MIN_NAMES + 1}
                max={max}
                busy={busy}
                compact
                onDraft={() => onDraft(s.player_id)}
                onSkip={() => onSkip(s.player_id, s.player_name)}
              />
            ))}
          </div>
        )}

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
