import { useEffect, useRef, useState } from "react";
import { api, type SearchHit, type Status } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { POS_HUE } from "@/components/Charts";
import { cn } from "@/lib/utils";

/**
 * Recording a pick, with the names offered rather than spelled.
 *
 * On the clock nobody should be typing "Smith-Njigba" correctly. Two
 * characters and it starts suggesting, arrows move, enter takes. Players
 * already drafted stay visible but greyed, because seeing that someone is
 * gone is itself the answer you were after.
 */
export function PickInput({ status, onPicked, busy, setBusy }: {
  status: Status;
  onPicked: (s: Status) => void;
  busy: boolean;
  setBusy: (b: boolean) => void;
}) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [active, setActive] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const myTurn = !!status.on_the_clock;

  useEffect(() => {
    if (q.trim().length < 2) { setHits([]); return; }
    let alive = true;
    const t = setTimeout(async () => {
      try {
        const r = await api.search(q.trim());
        if (alive) { setHits(r.players); setActive(0); }
      } catch { /* typing faster than the server; next keystroke retries */ }
    }, 120);
    return () => { alive = false; clearTimeout(t); };
  }, [q]);

  useEffect(() => {
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setHits([]);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, []);

  async function take(hit: SearchHit, mine: boolean) {
    if (hit.drafted) { setError(`${hit.player_name} is already off the board.`); return; }
    setBusy(true); setError(null);
    try {
      onPicked(await api.pick({ player_id: hit.player_id, mine }));
      setQ(""); setHits([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  }

  function onKey(e: React.KeyboardEvent) {
    if (!hits.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => (i + 1) % hits.length); }
    if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => (i - 1 + hits.length) % hits.length); }
    if (e.key === "Enter") { e.preventDefault(); take(hits[active], myTurn); }
    if (e.key === "Escape") setHits([]);
  }

  return (
    <section className="rounded-lg border border-line bg-panel p-3">
      <div className="flex items-center gap-2">
        <span className="eyebrow">Record a pick</span>
        {/* This is the answer to "what is that button in manual mode": at your
            own turn Enter records the pick as YOURS, otherwise as the room's. */}
        <span className={cn("text-[10.5px]", myTurn ? "text-turf" : "text-muted")}>
          {myTurn
            ? "your turn — Enter adds him to your roster"
            : "the room is picking — Enter marks him gone"}
        </span>
      </div>

      <div ref={box} className="relative mt-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKey}
          placeholder="Start typing a name…"
          className="h-9 w-full rounded-md border border-line bg-ink px-3 text-sm placeholder:text-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-turf/50"
        />

        {/* Downward. It used to open upward, and this box sits at the top of
            the column, so the list was clipped by the scroll container above
            it — you got a sliver of the first row and nothing else. */}
        {hits.length > 0 && (
          <ul className="absolute top-full z-30 mt-1 max-h-[280px] w-full overflow-y-auto overflow-x-hidden rounded-md border border-line bg-raised shadow-2xl">
            {hits.map((h, i) => (
              <li key={h.player_id}>
                <button
                  onMouseEnter={() => setActive(i)}
                  onClick={() => take(h, myTurn)}
                  disabled={h.drafted}
                  className={cn(
                    "flex w-full items-center gap-2 px-2 py-1.5 text-left transition-colors",
                    i === active && !h.drafted && "bg-turf/12",
                    h.drafted && "opacity-40"
                  )}
                >
                  {h.headshot ? (
                    <img src={h.headshot} alt="" loading="lazy"
                         className="h-7 w-7 shrink-0 rounded object-cover object-top" />
                  ) : (
                    <span className="h-7 w-7 shrink-0 rounded bg-line" />
                  )}
                  <span className="flex-1 truncate text-xs">{h.player_name}</span>
                  <span className="text-[10px] font-bold"
                        style={{ color: POS_HUE[h.position] ?? "#8CA096" }}>
                    {h.position}
                  </span>
                  <span className="num w-10 text-right text-[10px] text-muted">
                    {h.ecr ? `#${Math.round(h.ecr)}` : "—"}
                  </span>
                  {h.drafted && (
                    <span className="text-[9px] uppercase tracking-wider text-alarm">gone</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {hits.length > 0 && (
        <div className="mt-2 flex gap-2">
          <Button size="sm" variant="outline" className="flex-1"
                  onClick={() => take(hits[active], false)} disabled={busy}>
            Someone else took him
          </Button>
          <Button size="sm" className="flex-1"
                  onClick={() => take(hits[active], true)} disabled={busy}>
            That was my pick
          </Button>
        </div>
      )}

      {error && <p className="mt-1.5 text-xs text-alarm">{error}</p>}
    </section>
  );
}
