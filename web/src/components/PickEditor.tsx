import { useMemo, useState } from "react";
import { api, type Status } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Which overall picks are yours — after trades.
 *
 * The snake formula stops describing your picks the moment you trade one.
 * Send round five away and the wait from round four doubles, and that gap is
 * the input every survival probability is built on. So it is stated, not
 * inferred: tap the picks you hold.
 */
export function PickEditor({
  status, onChange, onClose,
}: {
  status: Status;
  onChange: (s: Status) => void;
  onClose: () => void;
}) {
  const teams = status.n_teams ?? 12;
  const rounds = status.n_rounds ?? 16;
  const [sel, setSel] = useState<Set<number>>(new Set(status.my_picks ?? []));
  const [busy, setBusy] = useState(false);

  const made = status.picks_made ?? 0;
  const grid = useMemo(
    () =>
      Array.from({ length: rounds }, (_, r) =>
        Array.from({ length: teams }, (_, c) => r * teams + c + 1)
      ),
    [rounds, teams]
  );

  function toggle(n: number) {
    const next = new Set(sel);
    next.has(n) ? next.delete(n) : next.add(n);
    setSel(next);
  }

  async function save() {
    setBusy(true);
    try {
      onChange(await api.setOwnedPicks([...sel].sort((a, b) => a - b)));
      onClose();
    } finally { setBusy(false); }
  }

  async function reset() {
    setBusy(true);
    try {
      const s = await api.resetOwnedPicks();
      setSel(new Set(s.my_picks ?? []));
      onChange(s);
    } finally { setBusy(false); }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-ink/80 backdrop-blur-sm" onClick={onClose} />
      <div className="relative flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-line bg-panel">
        <header className="border-b border-line px-4 py-3">
          <h2 className="font-semibold">Your picks</h2>
          <p className="mt-0.5 text-[11.5px] leading-snug text-muted">
            Tap every pick you hold. Traded one away? Turn it off. Got one back?
            Turn it on. This sets how long you wait between turns, which is what
            every "will he last?" number is built on.
          </p>
        </header>

        <div className="flex-1 overflow-auto p-4">
          <div className="mb-2 flex gap-3 text-[10px] text-muted">
            <span className="flex items-center gap-1">
              <span className="h-2.5 w-2.5 rounded-sm bg-turf" /> yours
            </span>
            <span className="flex items-center gap-1">
              <span className="h-2.5 w-2.5 rounded-sm border border-line bg-raised" /> not yours
            </span>
            <span className="flex items-center gap-1">
              <span className="h-2.5 w-2.5 rounded-sm bg-line" /> already gone
            </span>
          </div>

          <div className="space-y-1">
            {grid.map((row, i) => (
              <div key={i} className="flex items-center gap-1">
                <span className="num w-7 shrink-0 text-[10px] text-muted">R{i + 1}</span>
                {row.map((n) => {
                  const mine = sel.has(n);
                  const gone = n <= made;
                  return (
                    <button
                      key={n}
                      onClick={() => toggle(n)}
                      title={`Overall pick ${n}`}
                      className={cn(
                        "num h-7 flex-1 rounded-sm text-[10px] transition-colors",
                        mine
                          ? "bg-turf font-semibold text-ink"
                          : "border border-line bg-raised text-muted hover:border-turf/40",
                        gone && !mine && "bg-line/60 text-muted/40"
                      )}
                    >
                      {n}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
        </div>

        <footer className="flex items-center gap-2 border-t border-line px-4 py-3">
          <span className="num text-[11px] text-muted">
            {sel.size} picks selected
          </span>
          <Button size="sm" variant="ghost" className="ml-auto" onClick={reset} disabled={busy}>
            Reset to snake
          </Button>
          <Button size="sm" variant="outline" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button size="sm" onClick={save} disabled={busy || sel.size === 0}>
            Save picks
          </Button>
        </footer>
      </div>
    </div>
  );
}
