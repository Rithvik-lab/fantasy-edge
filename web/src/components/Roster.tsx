import type { RosterView } from "@/lib/api";
import { POS_HUE } from "@/components/Charts";
import { Term } from "@/components/Explain";
import { LANDING, Landed } from "@/components/Flight";
import { cn } from "@/lib/utils";

/**
 * Your team, with a way out of every pick.
 *
 * Undo only reaches the last pick, and the mistake you notice is rarely the
 * last one — you look at your roster two rounds later and find someone you
 * never meant to take, or a name you fat-fingered into the search box. Each
 * row can be removed on its own, which puts him back on the board and
 * renumbers the picks after him.
 */
export function Roster({ data, onRemove, busy, onDropPlayer, landing }: {
  data: RosterView | null;
  onRemove: (playerId: string, name: string) => void;
  busy: boolean;
  /** Drop a name from the board here to draft him. */
  onDropPlayer?: (playerId: string) => void;
  /** True for the moment a drafted player is landing. */
  landing?: boolean;
}) {
  // Dropping a name from the board onto your team IS drafting him — the
  // gesture and the meaning are the same, which is the whole argument for it.
  const drop = onDropPlayer ? {
    onDragOver: (e: React.DragEvent) => { e.preventDefault(); },
    onDrop: (e: React.DragEvent) => {
      e.preventDefault();
      const id = e.dataTransfer.getData("text/plain");
      if (id) onDropPlayer(id);
    },
  } : {};

  if (!data || (!data.starters.length && !data.bench.length)) {
    return (
      <section {...drop} {...{ [LANDING]: "" }}
               className="relative rounded-lg border border-dashed border-line bg-panel">
        <Landed on={!!landing} />
        <header className="border-b border-line px-3 py-2">
          <span className="eyebrow">Your team</span>
        </header>
        <p className="px-3 py-6 text-center text-xs text-muted">
          Nothing drafted yet.{onDropPlayer && " Drag a name from the board here."}
        </p>
      </section>
    );
  }

  // grade carries mixed shapes (numbers plus an `unfilled` map), so it is
  // read through a narrow view rather than cast wholesale.
  const g = data.grade as {
    score?: number; season_floor?: number; season_ceiling?: number;
    unfilled?: Record<string, number>;
  };

  const Row = ({ p, slot }: { p: RosterView["starters"][0]; slot?: string }) => (
    <li className="group flex items-center gap-2 border-b border-line/50 px-3 py-1.5 last:border-0">
      <span className="num w-11 shrink-0 text-[10px] uppercase tracking-wider text-muted">
        {slot ?? "BE"}
      </span>
      <span className="flex-1 truncate text-xs">{p.player_name}</span>
      <span className="w-7 text-right text-[10px] font-bold"
            style={{ color: POS_HUE[p.position] ?? "#8CA096" }}>
        {p.position}
      </span>
      <span className={cn("num w-10 shrink-0 text-right text-[10.5px]",
        p.vor >= 0 ? "text-turf/80" : "text-alarm/80")}>
        {p.vor > 0 ? "+" : ""}{Math.round(p.vor)}
      </span>
      <button
        onClick={() => onRemove(p.player_id, p.player_name)}
        disabled={busy}
        title={`Remove ${p.player_name} — puts him back on the board`}
        className="ml-1 rounded px-1 text-[13px] leading-none text-muted opacity-0 transition-all hover:text-alarm focus:opacity-100 group-hover:opacity-100 disabled:opacity-30"
      >
        ×
      </button>
    </li>
  );

  return (
    <section {...drop} {...{ [LANDING]: "" }}
             className="relative rounded-lg border border-line bg-panel">
      <Landed on={!!landing} />
      <header className="flex items-center gap-2 border-b border-line px-3 py-2">
        <span className="eyebrow">Your team</span>
        {g.score != null && (
          <Term k="grade">
            <span className={cn("num text-[11px]",
              g.score >= 100 ? "text-turf" : "text-muted")}>
              grade {g.score}
            </span>
          </Term>
        )}
        {g.season_floor != null && (
          <Term k="range">
            <span className="num ml-auto text-[10.5px] text-muted">
              {Math.round(g.season_floor)}–{Math.round(g.season_ceiling ?? 0)}
            </span>
          </Term>
        )}
      </header>

      <ul>
        {data.starters.map((p) => (
          <Row key={p.player_id} p={p} slot={p.slot} />
        ))}
      </ul>

      {data.bench.length > 0 && (
        <>
          <div className="border-y border-line bg-raised/40 px-3 py-1">
            <span className="eyebrow">Bench</span>
          </div>
          <ul>
            {data.bench.map((p) => <Row key={p.player_id} p={p} />)}
          </ul>
        </>
      )}

      {g.unfilled && Object.keys(g.unfilled).length > 0 && (
        <p className="border-t border-line px-3 py-1.5 text-[10.5px] text-muted">
          still need{" "}
          {Object.entries(g.unfilled)
            .map(([k, v]) => (v > 1 ? `${v}× ${k}` : k))
            .join(", ")}
        </p>
      )}
    </section>
  );
}
