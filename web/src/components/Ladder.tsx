import type { AdpLadder } from "@/lib/api";
import { POS_HUE } from "@/components/Charts";
import { cn } from "@/lib/utils";

/**
 * The board, in the order the room will take it.
 *
 * Everything else in this app is analysis. This is the thing you glance at:
 * who is next, who is already gone, and where your pick falls in the line.
 * Struck-through means off the board — seeing that is often the whole
 * question.
 */
export function Ladder({ data, myTurn }: { data: AdpLadder | null; myTurn: boolean }) {
  if (!data) return null;
  const gone = data.players.filter((p) => p.drafted).length;

  return (
    <section className="flex min-h-0 flex-col rounded-lg border border-line bg-panel">
      <header className="flex items-center gap-2 border-b border-line px-3 py-2">
        <span className="eyebrow">Draft board</span>
        <span className="text-[10px] uppercase tracking-wider text-muted">
          {data.platform} ADP
        </span>
        <span className="num ml-auto text-[11px] text-muted">
          {gone} gone · pick {data.overall}
        </span>
      </header>

      <ul className="min-h-0 flex-1 overflow-y-auto">
        {data.players.map((p) => {
          const hue = POS_HUE[p.position] ?? "#8CA096";
          const isNext = data.my_next != null
            && Math.round(p.ecr ?? 0) === data.my_next;
          return (
            <li
              key={p.player_id}
              className={cn(
                "flex items-center gap-2 border-b border-line/50 px-3 py-1.5 transition-colors last:border-0",
                p.drafted ? "opacity-35" : "hover:bg-raised/60",
                isNext && !p.drafted && "bg-turf/8"
              )}
            >
              <span className="num w-7 shrink-0 text-[10.5px] text-muted">
                {p.ecr ? Math.round(p.ecr) : "—"}
              </span>
              {p.headshot ? (
                <img src={p.headshot} alt="" loading="lazy"
                     className="h-6 w-6 shrink-0 rounded object-cover object-top" />
              ) : (
                <span className="h-6 w-6 shrink-0 rounded bg-raised" />
              )}
              <span className={cn("flex-1 truncate text-xs",
                p.drafted && "line-through decoration-muted/60")}>
                {p.player_name}
              </span>
              {p.rookie && (
                <span className="rounded bg-clock/15 px-1 text-[9px] font-bold text-clock">R</span>
              )}
              <span className="w-7 text-right text-[10px] font-bold" style={{ color: hue }}>
                {p.position}
              </span>
              <span className={cn("num w-9 shrink-0 text-right text-[10.5px]",
                (p.vor ?? 0) > 0 ? "text-turf/80" : "text-muted")}>
                {p.vor != null ? Math.round(p.vor) : "—"}
              </span>
            </li>
          );
        })}
      </ul>

      {data.my_next != null && (
        <footer className={cn("border-t border-line px-3 py-1.5 text-[11px]",
          myTurn ? "text-turf" : "text-muted")}>
          {myTurn ? "Your pick is now." : `Your next pick is #${data.my_next}.`}
        </footer>
      )}
    </section>
  );
}
