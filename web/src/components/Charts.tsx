import type { Analytics } from "@/lib/api";
import { cn } from "@/lib/utils";

/* ---------------------------------------------------------------------------
   Position colours are a VALIDATED categorical set, not picked by eye:
   worst all-pairs CVD ΔE 9.4, normal-vision 20.9, all inside the dark
   lightness band at ≥3:1 on this surface.

   There is deliberately no fourth hue. No four-colour set clears the
   colour-blind floors on this surface, so the charts FACET by position
   instead of stacking four series into one plot — which is also the clearer
   read, because the question is "where is the cliff at THIS position", not
   "compare all four at once".

   turf / clock / alarm stay reserved for meaning (value, urgency, gone) and
   are never reused as identity. A green bar always means good, never "RB".
--------------------------------------------------------------------------- */
export const POS_HUE: Record<string, string> = {
  RB: "#d95926",   // orange
  WR: "#3987e5",   // blue
  TE: "#199e70",   // aqua
  QB: "#8CA096",   // muted: no fourth hue clears the floors
  K: "#8CA096",
  DST: "#8CA096",
};

function Panel({ title, hint, children }: {
  title: string; hint?: string; children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-line bg-panel">
      <header className="border-b border-line px-3 py-2">
        <span className="eyebrow">{title}</span>
        {hint && <p className="mt-0.5 text-[11px] leading-snug text-muted">{hint}</p>}
      </header>
      <div className="p-3">{children}</div>
    </section>
  );
}

/**
 * THE CLIFF — one small panel per position, ranked by value over replacement.
 *
 * The decision this answers is not "who is best" but "how far does it fall if
 * I wait". A flat panel means the position keeps producing and you can come
 * back to it; a steep one means take yours now. Faceting is what makes those
 * two shapes comparable at a glance.
 */
function Cliff({ tiers }: { tiers: Analytics["tiers"] }) {
  const max = Math.max(1, ...tiers.flatMap((t) => t.players.map((p) => p.vor)));

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {tiers.map((t) => {
        const hue = POS_HUE[t.position] ?? "#8CA096";
        const top = t.players[0]?.vor ?? 0;
        const last = t.players[t.players.length - 1]?.vor ?? 0;
        const fall = Math.round(top - last);
        return (
          <figure key={t.position} className="space-y-1.5">
            <figcaption className="flex items-baseline gap-2">
              <span className="text-xs font-bold" style={{ color: hue }}>
                {t.position}
              </span>
              <span className="num text-[11px] text-muted">
                falls {fall} over the next {t.players.length}
              </span>
            </figcaption>

            <div className="space-y-[3px]">
              {t.players.map((p) => {
                const w = Math.max(1.5, (Math.max(p.vor, 0) / max) * 100);
                // Greyed when he will very likely still be there next turn:
                // that player is not really a decision right now.
                const waitable = p.survives != null && p.survives > 0.55;
                return (
                  <div key={p.player_name} className="group flex items-center gap-2"
                       title={`${p.player_name} — VOR ${p.vor}, ADP ${p.ecr ?? "—"}${
                         p.survives != null ? `, ${Math.round(p.survives * 100)}% to last` : ""}`}>
                    <span className="w-[92px] shrink-0 truncate text-[10.5px] text-muted">
                      {p.player_name}
                    </span>
                    <div className="relative h-2 flex-1">
                      <div
                        className={cn("h-2 rounded-r-[2px] transition-[width] duration-300",
                          waitable && "opacity-35")}
                        style={{ width: `${w}%`, background: hue }}
                      />
                    </div>
                    <span className="num w-8 shrink-0 text-right text-[10.5px] text-muted">
                      {Math.round(p.vor)}
                    </span>
                  </div>
                );
              })}
            </div>
          </figure>
        );
      })}
    </div>
  );
}

/**
 * SUPPLY AGAINST DEMAND — how many startable players are left at a position
 * versus how many starting slots the league still has to fill.
 *
 * When demand passes supply, that position is about to run. It is the same
 * information the dropoff number carries, in the form that shows you WHEN.
 */
function Scarcity({ rows }: { rows: Analytics["scarcity"] }) {
  const max = Math.max(1, ...rows.flatMap((r) => [r.startable_left, r.still_needed]));
  return (
    <div className="space-y-3">
      {rows.map((r) => {
        const hue = POS_HUE[r.position] ?? "#8CA096";
        const short = r.still_needed > r.startable_left;
        return (
          <div key={r.position} className="space-y-1">
            <div className="flex items-baseline gap-2 text-[11px]">
              <span className="w-7 font-bold" style={{ color: hue }}>{r.position}</span>
              <span className="num text-muted">
                {r.startable_left} startable left
              </span>
              <span className="text-muted/50">vs</span>
              <span className={cn("num", short ? "text-alarm" : "text-muted")}>
                {r.still_needed} slots to fill
              </span>
              {short && (
                <span className="ml-auto text-[10px] font-semibold uppercase tracking-wider text-alarm">
                  run coming
                </span>
              )}
            </div>
            {/* Two bars on one scale — supply above, demand below. */}
            <div className="space-y-[2px]">
              <div className="h-2 rounded-r-[2px]"
                   style={{ width: `${(r.startable_left / max) * 100}%`, background: hue }} />
              <div className="h-2 rounded-r-[2px] border border-dashed"
                   style={{ width: `${(r.still_needed / max) * 100}%`,
                            borderColor: hue, opacity: 0.55 }} />
            </div>
          </div>
        );
      })}
      <p className="pt-1 text-[10.5px] leading-snug text-muted">
        Solid bar is supply — players still on the board worth more than a
        free replacement. Dashed is demand — starting slots the league has
        not filled. Demand past supply is a run about to happen.
      </p>
    </div>
  );
}

export function Charts({ data }: { data: Analytics | null }) {
  if (!data) return null;
  return (
    <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
      <Panel
        title="Where each position falls off"
        hint="Value over replacement for the best left at each spot. Faded means he is likely to still be there at your next pick."
      >
        <Cliff tiers={data.tiers} />
      </Panel>
      <Panel title="Supply against demand">
        <Scarcity rows={data.scarcity} />
      </Panel>
    </div>
  );
}
