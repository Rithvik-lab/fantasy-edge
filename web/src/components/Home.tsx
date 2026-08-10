import { useCallback, useEffect, useState } from "react";
import { api, type SavedLeague, type Status } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

function when(ts: number | null) {
  if (!ts) return "";
  const mins = (Date.now() / 1000 - ts) / 60;
  if (mins < 1) return "just now";
  if (mins < 60) return `${Math.round(mins)}m ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h ago`;
  return `${Math.round(mins / 1440)}d ago`;
}

/**
 * The home screen: your leagues, and a way to start another.
 *
 * A draft is not one sitting — you mock during the week, draft for real on
 * Sunday, and run a friend's league in between. Each one keeps its own board,
 * roster and pick history, autosaved on every pick, so a closed tab or a
 * restarted laptop costs nothing.
 */
export function Home({ onOpen, onNew }: {
  onOpen: (s: Status) => void;
  onNew: () => void;
}) {
  const [leagues, setLeagues] = useState<SavedLeague[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  /**
   * A FAILED FETCH IS NOT AN EMPTY LIST.
   *
   * This used to `.catch(() => setLeagues([]))`, so any moment the engine was
   * not answering, the screen said you had no leagues and offered to set up
   * your first draft. Your saved leagues looked deleted. They were on disk the
   * whole time -- nothing had asked for them successfully.
   *
   * Losing work is the one thing this screen must never appear to have done,
   * so a failure now says so and offers to try again.
   */
  const load = useCallback(async () => {
    try {
      const r = await api.leagues();
      setLeagues(r.leagues);
      setFailed(false);
    } catch (e) {
      setFailed(true);
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);
  const [confirmDel, setConfirmDel] = useState<string | null>(null);

  useEffect(() => {
    load();
  }, [load]);

  async function open(id: string) {
    setBusy(id); setErr(null);
    try {
      onOpen(await api.loadLeague(id));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(null); }
  }

  async function remove(id: string) {
    setBusy(id);
    try {
      setLeagues((await api.deleteLeague(id)).leagues);
      setConfirmDel(null);
    } finally { setBusy(null); }
  }

  return (
    <div className="mx-auto flex min-h-full max-w-xl flex-col justify-center px-5 py-12">
      <div className="mb-1 flex items-baseline gap-2">
        <h1 className="text-3xl font-bold tracking-tight">FantasyEdge</h1>
        <span className="num text-[11px] text-turf">v1</span>
      </div>
      <p className="text-sm leading-relaxed text-muted">
        Three names, every pick — priced against what the board still owes you
        at your next turn.
      </p>

      <div className="my-6 h-px bg-gradient-to-r from-turf/40 via-line to-transparent" />

      {failed ? (
        <div className="rounded-lg border border-alarm/30 bg-alarm/10 p-4">
          <p className="text-sm text-alarm">Could not read your saved leagues.</p>
          <p className="mt-1 text-[11px] leading-snug text-muted">
            {err} Nothing has been lost — they are files on this machine, and
            this screen simply could not ask for them.
          </p>
          <Button size="sm" variant="outline" className="mt-2.5"
                  onClick={() => { setErr(null); load(); }}>
            Try again
          </Button>
        </div>
      ) : leagues === null ? (
        <p className="text-xs text-muted">Loading your leagues…</p>
      ) : (
        <div className="tick-in space-y-3">
          {leagues.length > 0 && (
            <>
              <span className="eyebrow">Your leagues</span>
              <ul className="space-y-2">
                {leagues.map((l) => {
                  const pct = l.total_picks
                    ? Math.min(100, (l.picks_made / l.total_picks) * 100) : 0;
                  return (
                    <li key={l.id}>
                      <div className="group relative overflow-hidden rounded-lg border border-line bg-panel transition-colors hover:border-turf/40">
                        {/* Progress reads as a fill behind the row. */}
                        <div className="absolute inset-y-0 left-0 bg-turf/8 transition-[width] duration-500"
                             style={{ width: `${pct}%` }} />
                        <button
                          onClick={() => open(l.id)}
                          disabled={!!busy}
                          className="relative flex w-full items-center gap-3 p-3 text-left"
                        >
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                              <span className="truncate font-semibold">{l.name}</span>
                              {l.complete && (
                                <span className="rounded bg-raised px-1.5 text-[9px] uppercase tracking-wider text-muted">
                                  done
                                </span>
                              )}
                            </div>
                            <div className="mt-0.5 flex items-center gap-2 text-[11px] text-muted">
                              <span className="uppercase tracking-wider">{l.platform}</span>
                              <span>·</span>
                              <span className="num">{l.n_teams} teams</span>
                              {l.my_slot != null && (
                                <>
                                  <span>·</span>
                                  <span className="num">slot {l.my_slot}</span>
                                </>
                              )}
                              <span>·</span>
                              <span className="num">
                                {l.picks_made}/{l.total_picks} picks
                              </span>
                            </div>
                          </div>
                          <span className="shrink-0 text-[10px] text-muted">
                            {when(l.saved_at)}
                          </span>
                          <span className="shrink-0 text-muted transition-transform duration-200 group-hover:translate-x-0.5">
                            ›
                          </span>
                        </button>

                        <button
                          onClick={(e) => { e.stopPropagation(); setConfirmDel(l.id); }}
                          className="absolute right-2 top-2 z-10 rounded px-1 text-[13px] leading-none text-muted opacity-0 transition-opacity hover:text-alarm group-hover:opacity-100"
                          title="Delete this league"
                        >
                          ×
                        </button>
                      </div>

                      {confirmDel === l.id && (
                        <div className="tick-in mt-1 flex items-center gap-2 rounded-md border border-alarm/30 bg-alarm/10 px-3 py-2 text-[11px] text-alarm">
                          <span className="flex-1">
                            Delete “{l.name}” and its {l.picks_made} picks?
                          </span>
                          <Button size="sm" variant="ghost" className="h-6"
                                  onClick={() => setConfirmDel(null)}>Keep</Button>
                          <Button size="sm" variant="danger" className="h-6"
                                  onClick={() => remove(l.id)} disabled={!!busy}>
                            Delete
                          </Button>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
              <div className="h-px bg-line" />
            </>
          )}

          {err && (
            <p className="rounded-md border border-alarm/30 bg-alarm/10 px-3 py-2 text-xs text-alarm">
              {err}
            </p>
          )}

          <Button
            size="lg"
            variant={leagues.length ? "outline" : "default"}
            className={cn("w-full", !leagues.length &&
              "shadow-[0_0_28px_-6px] shadow-turf/45")}
            onClick={onNew}
          >
            {leagues.length ? "Start another draft" : "Set up your first draft"}
          </Button>
        </div>
      )}
    </div>
  );
}
