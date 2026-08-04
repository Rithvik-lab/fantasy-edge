import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Shortlist as ShortlistData, type Status, type TeamRow } from "@/lib/api";
import { Setup } from "@/components/Setup";
import { Shortlist } from "@/components/Shortlist";
import { Ticker, Teams } from "@/components/Ticker";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const SYNC_MS = 2500;

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [list, setList] = useState<ShortlistData | null>(null);
  const [teams, setTeams] = useState<TeamRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [stale, setStale] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const skipped = useRef<string[]>([]);
  const lastPickCount = useRef(-1);

  /* -- data ------------------------------------------------------------- */

  const loadList = useCallback(async () => {
    try {
      setList(await api.suggestions(3, skipped.current));
      setStale(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const refreshAll = useCallback(async (s: Status) => {
    setStatus(s);
    await loadList();
    try {
      setTeams((await api.teams()).teams);
    } catch { /* the room view is optional */ }
  }, [loadList]);

  useEffect(() => {
    api.status()
      .then((s) => { if (s.configured) refreshAll(s); })
      .catch(() => undefined);
  }, [refreshAll]);

  /* -- the poll --------------------------------------------------------- */
  /* The reason this app exists. Picks land on their own, and the moment the
     count changes the shortlist is recomputed against the new board, so a
     name that just went is never still sitting on screen. */
  useEffect(() => {
    if (!status?.configured || !status.espn_connected) return;
    let alive = true;
    const tick = async () => {
      try {
        const s = await api.sync();
        if (!alive) return;
        setStatus(s);
        if (s.picks_made !== lastPickCount.current) {
          if (lastPickCount.current >= 0) setStale(true);
          lastPickCount.current = s.picks_made ?? 0;
          skipped.current = [];
          await loadList();
          try { setTeams((await api.teams()).teams); } catch { /* optional */ }
        }
      } catch { /* transient network; the next tick retries */ }
    };
    tick();
    const id = setInterval(tick, SYNC_MS);
    return () => { alive = false; clearInterval(id); };
  }, [status?.configured, status?.espn_connected, loadList]);

  /* -- actions ---------------------------------------------------------- */

  async function draft(playerId: string) {
    setBusy(true);
    try {
      skipped.current = [];
      await refreshAll(await api.pick({ player_id: playerId, mine: true }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  }

  async function skip(playerId: string) {
    skipped.current = [...skipped.current, playerId];
    setBusy(true);
    await loadList();
    setBusy(false);
  }

  async function undo() {
    setBusy(true);
    try {
      skipped.current = [];
      await refreshAll(await api.undo());
    } finally { setBusy(false); }
  }

  if (!status?.configured) {
    return <Setup onReady={() => api.status().then(refreshAll)} />;
  }

  const myTurn = !!status.on_the_clock;

  return (
    <div className="flex h-full flex-col">
      {/* Where the draft is, and whether we can still see it. */}
      <header className="border-b border-line bg-panel/80 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-5 gap-y-1 px-4 py-2.5">
          <span className="font-bold tracking-tight">FantasyEdge</span>
          <span className="truncate text-xs text-muted">
            {status.league_name || status.describe}
          </span>

          <div className="ml-auto flex items-center gap-4">
            <span className="num text-xs text-muted">
              R{status.round} · P{status.pick} · #{status.overall}
            </span>
            {status.picks_until_next != null && !myTurn && (
              <span className="num text-xs text-muted">
                next in {status.picks_until_next}
              </span>
            )}
            <span
              className={cn(
                "rounded px-2 py-0.5 text-[11px] font-semibold",
                myTurn ? "bg-turf text-ink" : "bg-raised text-muted"
              )}
            >
              {myTurn ? "ON THE CLOCK" : `slot ${status.my_slot}`}
            </span>
            <span
              className={cn("flex items-center gap-1.5 text-[11px]",
                status.sync_error ? "text-alarm"
                  : status.espn_connected ? "text-turf" : "text-muted")}
              title={status.sync_error ?? undefined}
            >
              <span className={cn("h-1.5 w-1.5 rounded-full",
                status.sync_error ? "bg-alarm"
                  : status.espn_connected ? "bg-turf animate-pulse" : "bg-muted")} />
              {status.sync_error ? "sync failed"
                : status.espn_connected ? "live" : "manual"}
            </span>
          </div>
        </div>
      </header>

      {err && (
        <div className="flex items-center gap-3 border-b border-alarm/30 bg-alarm/10 px-4 py-1.5 text-xs text-alarm">
          {err}
          <Button size="sm" variant="ghost" className="ml-auto h-6"
                  onClick={() => setErr(null)}>
            Dismiss
          </Button>
        </div>
      )}

      {/* The room on the left, everyone's holes on the right. */}
      <main className="mx-auto w-full max-w-[1400px] flex-1 overflow-y-auto px-4 py-4">
        <div className="grid gap-4 lg:grid-cols-2">
          <Ticker status={status} onUndo={undo} busy={busy} />
          <Teams teams={teams} mySlot={status.my_slot ?? 1} />
        </div>

        {!status.espn_connected && (
          <ManualEntry onPicked={refreshAll} busy={busy} setBusy={setBusy} />
        )}
      </main>

      <Shortlist
        data={list}
        busy={busy}
        myTurn={myTurn}
        stale={stale}
        onDraft={draft}
        onSkip={skip}
        onRefresh={loadList}
      />
    </div>
  );
}

/** Typing picks in, for mocks and rooms the API cannot reach. */
function ManualEntry({ onPicked, busy, setBusy }: {
  onPicked: (s: Status) => void; busy: boolean; setBusy: (b: boolean) => void;
}) {
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(mine: boolean) {
    if (!name.trim()) return;
    setBusy(true); setError(null);
    try {
      onPicked(await api.pick({ name: name.trim(), mine }));
      setName("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  }

  return (
    <section className="mt-4 rounded-lg border border-line bg-panel p-3">
      <span className="eyebrow">Record a pick</span>
      <div className="mt-2 flex gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit(false)}
          placeholder="Player name — partial is fine"
          className="h-9 flex-1 rounded-md border border-line bg-ink px-3 text-sm placeholder:text-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-turf/50"
        />
        <Button size="sm" variant="outline" onClick={() => submit(false)} disabled={busy}>
          Someone else
        </Button>
        <Button size="sm" onClick={() => submit(true)} disabled={busy}>
          That was me
        </Button>
      </div>
      {error && <p className="mt-1.5 text-xs text-alarm">{error}</p>}
    </section>
  );
}
