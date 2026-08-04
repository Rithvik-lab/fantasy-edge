import { useCallback, useEffect, useRef, useState } from "react";
import {
  api, type Analytics, type Shortlist as ShortlistData, type Status, type TeamRow,
} from "@/lib/api";
import { Setup } from "@/components/Setup";
import { Shortlist } from "@/components/Shortlist";
import { Ticker, Teams } from "@/components/Ticker";
import { Charts } from "@/components/Charts";
import { GlossaryDrawer } from "@/components/Glossary";
import { PickEditor } from "@/components/PickEditor";
import { PickInput } from "@/components/PickInput";
import { Phase, SyncDot } from "@/components/Phase";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const SYNC_MS = 2500;
type Tab = "room" | "data";

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [list, setList] = useState<ShortlistData | null>(null);
  const [teams, setTeams] = useState<TeamRow[]>([]);
  const [stats, setStats] = useState<Analytics | null>(null);
  const [tab, setTab] = useState<Tab>("room");
  const [editPicks, setEditPicks] = useState(false);
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
    try { setTeams((await api.teams()).teams); } catch { /* optional */ }
    try { setStats(await api.analytics()); } catch { /* optional */ }
  }, [loadList]);

  useEffect(() => {
    api.status()
      .then((s) => { if (s.configured) refreshAll(s); })
      .catch(() => undefined);
  }, [refreshAll]);

  /* -- the poll --------------------------------------------------------- */
  /* Picks land on their own, and the moment the count changes the shortlist
     is recomputed against the new board — so a name that just went is never
     still sitting on screen. */
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
          try { setStats(await api.analytics()); } catch { /* optional */ }
        }
      } catch { /* transient; next tick retries */ }
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
      <header className="border-b border-line bg-panel/80 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5">
          <span className="font-bold tracking-tight">FantasyEdge</span>
          <span className="truncate text-xs text-muted">
            {status.league_name || status.describe}
          </span>

          <div className="ml-auto flex items-center gap-3">
            <GlossaryDrawer />
            <button
              onClick={() => setEditPicks(true)}
              className="rounded border border-line px-2 py-0.5 text-[11px] text-muted transition-colors hover:border-turf/40 hover:text-chalk"
              title="Set which picks are yours, after trades"
            >
              {status.picks_traded ? "picks: traded" : `slot ${status.my_slot}`}
            </button>
            <span className="num text-xs text-muted">
              R{status.round} · #{status.overall}
            </span>
            <span
              className={cn(
                "rounded px-2 py-0.5 text-[11px] font-semibold transition-colors",
                myTurn ? "bg-turf text-ink" : "bg-raised text-muted"
              )}
            >
              {myTurn ? "ON THE CLOCK"
                : status.picks_until_next != null
                  ? `next in ${status.picks_until_next}` : "waiting"}
            </span>
            <SyncDot status={status} />
            <Button size="sm" variant="ghost" className="h-7 px-2 text-[11px]"
                    onClick={() => setStatus({ configured: false })}>
              Change league
            </Button>
          </div>
        </div>
      </header>

      <Phase status={status} />

      {err && (
        <div className="flex items-center gap-3 border-b border-alarm/30 bg-alarm/10 px-4 py-1.5 text-xs text-alarm">
          {err}
          <Button size="sm" variant="ghost" className="ml-auto h-6"
                  onClick={() => setErr(null)}>Dismiss</Button>
        </div>
      )}

      {(status.warnings ?? []).map((w) => (
        <div key={w} className="border-b border-clock/30 bg-clock/10 px-4 py-2 text-xs text-clock">
          <div className="mx-auto flex max-w-[1400px] items-center gap-3">
            <span className="flex-1">{w}</span>
            {!status.slot_confirmed && (
              <Button size="sm" variant="outline" className="h-7"
                      onClick={() => setEditPicks(true)}>
                Set my picks
              </Button>
            )}
          </div>
        </div>
      ))}

      <main className="mx-auto w-full max-w-[1400px] flex-1 overflow-y-auto px-4 py-4">
        <div className="mb-3 flex rounded-md border border-line p-0.5">
          {(["room", "data"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={cn(
                "flex-1 rounded px-3 py-1.5 text-xs capitalize transition-colors",
                tab === t ? "bg-raised font-medium text-chalk" : "text-muted hover:text-chalk"
              )}
            >
              {t === "room" ? "The room" : "The numbers"}
            </button>
          ))}
        </div>

        <div key={tab} className="tick-in space-y-4">
          {tab === "room" ? (
            <>
              <div className="grid gap-4 lg:grid-cols-2">
                <Ticker status={status} onUndo={undo} busy={busy} />
                <Teams teams={teams} mySlot={status.my_slot ?? 1} />
              </div>
              {!status.espn_connected && (
                <PickInput status={status} onPicked={refreshAll}
                           busy={busy} setBusy={setBusy} />
              )}
            </>
          ) : (
            <Charts data={stats} />
          )}
        </div>
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

      {editPicks && (
        <PickEditor
          status={status}
          onChange={refreshAll}
          onClose={() => setEditPicks(false)}
        />
      )}
    </div>
  );
}
