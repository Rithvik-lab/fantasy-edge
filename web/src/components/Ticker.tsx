import type { Status, TeamRow } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { LANDING, Landed } from "@/components/Flight";
import { Term } from "@/components/Explain";
import { cn } from "@/lib/utils";

/** Picks as they land. This is the app proving it can see your draft. */
export function Ticker({ status, onUndo, busy, landing }: {
  status: Status; onUndo: () => void; busy: boolean; landing?: boolean;
}) {
  const recent = status.recent ?? [];
  return (
    <section {...{ [LANDING]: "gone" }}
             className="relative rounded-lg border border-line bg-panel">
      <Landed on={!!landing} />
      <header className="flex items-center gap-2 border-b border-line px-3 py-2">
        <Term k="off_board"><span className="eyebrow">Off the board</span></Term>
        <span className="num text-[11px] text-muted">{status.picks_made ?? 0} picks</span>
        <Button size="sm" variant="ghost" className="ml-auto h-6 px-2 text-[11px]"
                onClick={onUndo} disabled={busy || !recent.length}>
          Undo
        </Button>
      </header>
      <ul className="max-h-[38vh] overflow-y-auto">
        {recent.length === 0 && (
          <li className="px-3 py-4 text-center text-xs leading-snug text-muted">
            Nothing drafted yet.
            <span className="mt-1 block text-[10.5px]">
              <span className="kbd">shift</span> + double-click a name on the
              board when someone else takes him.
            </span>
          </li>
        )}
        {recent.map((p) => (
          <li
            key={p.overall}
            className={cn(
              "tick-in flex items-center gap-2 border-b border-line/60 px-3 py-1.5 text-xs last:border-0",
              p.mine && "bg-turf/5"
            )}
          >
            <span className="num w-8 shrink-0 text-[11px] text-muted">
              {p.overall}
            </span>
            <span className={cn("truncate", p.mine ? "font-medium text-turf" : "text-chalk/85")}>
              {p.name}
            </span>
            {p.mine && <Badge tone="turf" className="ml-auto">You</Badge>}
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * What every other roster still needs. Their holes are the best predictor of
 * what leaves the board before your next turn.
 */
export function Teams({ teams, mySlot }: { teams: TeamRow[]; mySlot: number }) {
  if (!teams.length) return null;
  return (
    <section className="rounded-lg border border-line bg-panel">
      <header className="border-b border-line px-3 py-2">
        <span className="eyebrow">The room</span>
      </header>
      <ul className="max-h-[32vh] divide-y divide-line/60 overflow-y-auto">
        {teams.map((t) => (
          <li key={t.slot} className={cn("px-3 py-2", t.slot === mySlot && "bg-turf/5")}>
            <div className="flex items-center gap-2">
              <span className="num w-5 text-[11px] text-muted">{t.slot}</span>
              <span className={cn("truncate text-xs",
                t.slot === mySlot ? "font-medium text-turf" : "text-chalk/85")}>
                {t.slot === mySlot ? "You" : t.name}
              </span>
              <span className="num ml-auto text-[11px] text-muted">
                {t.players.length}
              </span>
            </div>
            {Object.keys(t.unfilled).length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1 pl-7">
                {Object.entries(t.unfilled).map(([pos, n]) => (
                  <Badge key={pos} tone="neutral">
                    {n > 1 ? `${n}×` : ""}{pos}
                  </Badge>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
