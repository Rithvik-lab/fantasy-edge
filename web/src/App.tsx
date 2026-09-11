import { useCallback, useEffect, useRef, useState } from "react";
import {
  api, type AdpLadder, type RosterView, type TeamReport,
  type Shortlist as ShortlistData, type Status, type TeamRow,
} from "@/lib/api";
import { Home } from "@/components/Home";
import { Setup } from "@/components/Setup";
import { Roster } from "@/components/Roster";
import { SaveLeague } from "@/components/SaveLeague";
import { Shortlist, NAMES } from "@/components/Shortlist";
import { Ticker, Teams } from "@/components/Ticker";
import { Ladder } from "@/components/Ladder";
import { GlossaryDrawer } from "@/components/Glossary";
import { PickEditor } from "@/components/PickEditor";
import { PickInput } from "@/components/PickInput";
import { Phase, SyncDot } from "@/components/Phase";
import { useFlight } from "@/components/Flight";
import { Booting } from "@/components/Booting";
import { ModeSwitch, type Mode } from "@/components/Modes";
import { Trade } from "@/components/Trade";
import { Waivers } from "@/components/Waivers";
import { InjuryTag } from "@/components/InjuryTag";
import { MyTeam } from "@/components/MyTeam";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * How often to ask ESPN what has happened.
 *
 * This used to be a flat 2.5s for as long as the app was open, which over a
 * normal 90-second-per-pick draft is ~5,760 requests to an undocumented
 * endpoint, and it kept going after the draft finished — forever, at full
 * rate, for a board that could never change again.
 *
 * Nothing is gained by that. The only moment worth polling hard is when a pick
 * could land that changes your answer, so the rate follows the draft:
 */
const POLL = {
  near: 2000,      // your pick is close — every second counts here
  live: 4000,      // the draft is running but you are not up soon
  idle: 15000,     // connected before it starts; nothing can change yet
  paused: 30000,   // nothing has moved in a long while — someone is away
} as const;

/** No pick can land in a finished draft, so stop asking. */
function pollDelay(s: Status | null, quiet: number): number | null {
  if (!s?.espn_connected) return null;
  if (s.phase === "complete" || s.draft_complete) return null;
  if (s.phase !== "live") return POLL.idle;
  if (quiet > 20) return POLL.paused;
  const until = s.picks_until_next;
  return until != null && until <= 3 ? POLL.near : POLL.live;
}

type Tab = "team" | "room";

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [list, setList] = useState<ShortlistData | null>(null);
  const [teams, setTeams] = useState<TeamRow[]>([]);
  const [ladder, setLadder] = useState<AdpLadder | null>(null);
  const [roster, setRoster] = useState<RosterView | null>(null);
  const [report, setReport] = useState<TeamReport | null>(null);
  const [screen, setScreen] = useState<"home" | "setup" | "draft">("home");
  // Held until we know WHICH app this is. setStatus lands on the first fetch
  // and unblocks the main layout, but the mode and tab are decided several
  // fetches later — so the live-draft interface flashed for three seconds in
  // front of people whose draft finished in August.
  const [booting, setBooting] = useState(true);
  const [lastSkip, setLastSkip] = useState<{ id: string; name: string } | null>(null);
  // Defaults to the room, because that is what a LIVE draft is for. It only
  // becomes My team once the draft is done -- set in open() from the phase we
  // already have, rather than as a blanket default that was wrong for anyone
  // mid-draft.
  const [tab, setTab] = useState<Tab>("room");
  const wasLive = useRef(false);
  const [mode, setMode] = useState<Mode>("draft");
  // A player handed over from trade mode for the waiver board to open on.
  // Cleared once it has been used, so coming back to waivers later does not
  // jump to a name you looked up on Tuesday.
  const [wireFocus, setWireFocus] = useState<string | null>(null);
  // WHICH news was dismissed, not WHETHER. A boolean here stays true for the
  // rest of the session, so the next man to get hurt would reprice the board
  // in silence -- the exact failure the banner exists to prevent. Kept client
  // side rather than cleared on the server, because the list is also the
  // record of why today's prices are what they are.
  const [seenNews, setSeenNews] = useState<string | null>(null);
  const [side, setSide] = useState<"feed" | "room">("feed");
  const [editPicks, setEditPicks] = useState(false);
  const [busy, setBusy] = useState(false);
  const [stale, setStale] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Which of your picks the shortlist is answering for. null = the live one,
  // and it snaps back to null whenever a pick lands so you are never quietly
  // planning round six while round three is on the clock.
  const [planAt, setPlanAt] = useState<number | null>(null);
  const planRef = useRef<number | null>(null);
  const { launch, layer, landingIn } = useFlight();
  const skipped = useRef<string[]>([]);
  const lastPickCount = useRef(-1);

  /* -- data ------------------------------------------------------------- */

  // The whole ranking comes down in one request and the shortlist pages
  // through it locally — an arrow press is a slide, not a round trip.
  const loadList = useCallback(async () => {
    try {
      setList(await api.suggestions(NAMES, skipped.current, planRef.current));
      setStale(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const open = useCallback(async (s: Status) => {
    // SETTLE THE PHASE BEFORE ANYTHING RENDERS.
    //
    // The status we boot with is the state we last SAVED. For a synced league
    // the truth is at ESPN, and the first poll -- a second or two after the
    // gate lifts -- is what discovers the draft actually finished. That is the
    // "went to draft mode, then randomly cut to My team" cut: the gate was
    // waiting on the wrong question and then the answer arrived in public.
    //
    // So an attached league gets synced here, inside the gate, and the phase
    // decision uses what comes back. A failed sync keeps the saved status,
    // which is the best answer available and never worse than not asking.
    if (s.espn_connected) {
      try { s = await api.sync(); } catch { /* keep the saved status */ }
    }
    setStatus(s);
    // Decide the mode and tab from the status we already have, BEFORE any of
    // this renders. Doing it in an effect afterwards is what made the app
    // correct itself in public.
    const done = s.phase === "complete";
    setTab(done ? "team" : "room");
    await loadList();
    try { setTeams((await api.teams()).teams); } catch { /* optional */ }
    try { setLadder(await api.adp()); } catch { /* optional */ }
    try { setRoster(await api.roster()); } catch { /* optional */ }
    // The team report is 1.4s cold and MyTeam fetched it on its own, so the
    // boot gate lifted onto a page of grey bars. Fetched HERE when it is the
    // screen you are about to land on, so the gate covers it and the first
    // thing you see is finished.
    if (done) {
      try { setReport(await api.teamReport()); } catch { /* optional */ }
    }
    setScreen("draft");
  }, [loadList]);

  /** After a pick, an undo, a removal. No gate — the page is already right,
   *  it just needs new numbers, and a loading screen between every pick would
   *  be far worse than the flash this file is trying to remove. */
  const refreshAll = open;

  /**
   * OPENING A LEAGUE. Different event, different treatment.
   *
   * This is the one route people actually use — click a league in My Leagues —
   * and it was the one route the loading screen never covered, because
   * `booting` was set false on first mount and never raised again. The gate
   * existed and simply was not up. That is the flash in the screenshot.
   */
  const openLeague = useCallback(async (s: Status) => {
    setBooting(true);
    // THE LEAGUE BEING OPENED, NOT THE ONE THAT WAS OPEN. `open` does not
    // reach its own `setStatus` until after it has synced ESPN, so for those
    // seconds `status` still held the PREVIOUS league -- and the loading
    // screen reads its name off state. Picking a second league from My
    // Leagues therefore spent the whole wait insisting you were opening the
    // first one.
    //
    // Setting it here also leaves the app honest if the open fails: the
    // engine has already switched leagues by the time we are called, so
    // keeping the old name on screen would have the interface and the server
    // disagreeing about which league is loaded.
    setStatus(s);
    // OPENING A LEAGUE LANDS ON MY TEAM, always. Mode is deliberately not
    // persisted, but it survived in memory across a league switch, so leaving
    // waivers open and picking a different league from My Leagues dropped you
    // into that league's wire — a screen about a roster you had not looked at
    // yet. Set here rather than in `open`, which is also the after-a-pick
    // refresh: that one must never move you.
    setMode("draft");
    // finally, always. A gate that can fail to open is worse than the flash it
    // exists to prevent -- a stuck loading screen has no way out but a reload.
    try {
      await open(s);
    } finally {
      setBooting(false);
    }
  }, [open]);


  useEffect(() => {
    api.status()
      .then((s) => {
        if (s.configured) openLeague(s);
        else setBooting(false);      // nothing to open; the home screen is right
      })
      .catch(() => setBooting(false));
  }, [openLeague]);

  /* -- the poll --------------------------------------------------------- */
  /* Picks land on their own, and the moment the count changes the shortlist
     is recomputed against the new board — so a name that just went is never
     still sitting on screen. */
  useEffect(() => {
    if (!status?.configured || !status.espn_connected) return;
    let alive = true;
    let timer: number | undefined;
    // Consecutive polls that changed nothing. Long runs mean the room is
    // away from their keyboards, not that the draft is moving.
    let quiet = 0;

    const tick = async () => {
      let next: Status | null = null;
      try {
        const s = await api.sync();
        if (!alive) return;
        next = s;
        setStatus(s);
        if (s.picks_made !== lastPickCount.current) {
          quiet = 0;
          if (lastPickCount.current >= 0) setStale(true);
          lastPickCount.current = s.picks_made ?? 0;
          skipped.current = [];
          await loadList();
          try { setTeams((await api.teams()).teams); } catch { /* optional */ }
                try { setLadder(await api.adp()); } catch { /* optional */ }
          try { setRoster(await api.roster()); } catch { /* optional */ }
        } else {
          quiet += 1;
        }
      } catch {
        // Transient. Count it as quiet so a flapping connection backs off
        // rather than retrying hard.
        quiet += 1;
      }
      if (!alive) return;
      const delay = pollDelay(next, quiet);
      if (delay != null) timer = window.setTimeout(tick, delay);
    };

    tick();
    return () => { alive = false; window.clearTimeout(timer); };
  }, [status?.configured, status?.espn_connected, loadList]);

  /* -- actions ---------------------------------------------------------- */

  /** Aim the shortlist at one of your picks. */
  async function planFor(overall: number | null) {
    planRef.current = overall;
    setPlanAt(overall);
    setBusy(true);
    await loadList();
    setBusy(false);
  }

  async function draft(
    playerId: string,
    from?: { el: Element | null; name: string; headshot: string | null },
  ) {
    // Fired before the request so the motion starts on the click rather than
    // on the round trip. It is purely visual; if the pick fails the ghost has
    // already faded and the roster simply never changed.
    if (from) launch(from.el, from.name, from.headshot, "mine");
    setBusy(true);
    try {
      skipped.current = [];
      // A pick landing ends the planning detour and moves you on.
      planRef.current = null;
      setPlanAt(null);
      await refreshAll(await api.pick({ player_id: playerId, mine: true }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  }

  async function skip(playerId: string, name: string) {
    skipped.current = [...skipped.current, playerId];
    setLastSkip({ id: playerId, name });
    setBusy(true);
    await loadList();
    setBusy(false);
  }

  /** Put back a name you skipped by accident. */
  async function unskip() {
    skipped.current = skipped.current.filter((id) => id !== lastSkip?.id);
    setLastSkip(null);
    setBusy(true);
    await loadList();
    setBusy(false);
  }

  /** Somebody else took him. Off the board, not onto your team — which in a
   *  manual draft is eleven picks out of every twelve. */
  async function markGone(
    playerId: string,
    from?: { el: Element | null; name: string; headshot: string | null },
  ) {
    if (from) launch(from.el, from.name, from.headshot, "gone");
    setBusy(true);
    try {
      skipped.current = [];
      await refreshAll(await api.pick({ player_id: playerId, mine: false }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  }

  /** Take one specific player back off the board, not just the last pick. */
  async function removePlayer(playerId: string) {
    setBusy(true);
    try {
      skipped.current = [];
      await refreshAll(await api.removePick(playerId));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  }

  async function undo() {
    setBusy(true);
    try {
      skipped.current = [];
      await refreshAll(await api.undo());
    } finally { setBusy(false); }
  }

  // A draft ending WHILE YOU WATCH. Opening an already-finished league is
  // handled in refreshAll before the first render, so this only fires for the
  // live transition -- and only once, never against a deliberate move back.
  useEffect(() => {
    if (booting) return;      // opening is handled in open(), before render
    if (status?.phase === "live") wasLive.current = true;
    if (status?.phase === "complete" && wasLive.current) {
      wasLive.current = false;
      setTab("team");
    }
  }, [status?.phase, booting]);

  if (booting) return <Booting name={status?.league_name || status?.saved_name} />;

  if (screen === "home" && !status?.configured) {
    return (
      <Home
        onOpen={(s) => openLeague(s)}
        onNew={() => setScreen("setup")}
      />
    );
  }
  if (!status?.configured || screen === "setup") {
    return (
      <Setup
        onBack={() => setScreen("home")}
        onReady={() => api.status().then(openLeague)}
      />
    );
  }

  const myTurn = !!status.on_the_clock;
  // Names the CONTENT of the news, so dismissing this batch does not dismiss
  // the next one. Includes the destination tag: the same man going
  // questionable and then out is two different pieces of news.
  const newsKey = (status.injury_news ?? [])
    .map((n) => `${n.player_id}:${n.to ?? ""}`).join("|");

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-line bg-panel/80 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5">
          <span className="font-bold tracking-tight">FantasyEdge</span>
          <span className="truncate text-xs text-muted">
            {status.league_name || status.describe}
          </span>

          <div className="ml-auto flex items-center gap-3">
            {/* NOTHING IS GATED HERE ANY MORE. Waivers was disabled until the
                draft finished, for a real reason -- while the draft runs,
                everyone unowned is a PICK and the board is the screen for that
                -- and the reason was invisible from the outside. A greyed
                button says only "no", so the mode opens and explains itself
                instead. */}
            <ModeSwitch mode={mode} onMode={setMode}
                        complete={status.phase === "complete"} />
            <GlossaryDrawer />
            {/* THE DRAFT CLOCK GOES AWAY WHEN THE DRAFT DOES. Round, overall
                pick, which picks are yours and "next in one" are all answers
                to a question nobody is asking in October, and leaving them up
                made a finished league look like it was still on the clock. */}
            {status.phase !== "complete" && (
              <>
                <button
                  onClick={() => setEditPicks(true)}
                  className="num rounded border border-line px-2 py-0.5 text-[11px] text-muted transition-colors hover:border-turf/40 hover:text-chalk"
                  title="Set which picks are yours, after trades"
                >
                  {status.picks_traded
                    ? "picks: custom"
                    : `picks ${(status.my_picks ?? []).slice(0, 3).join(" · ")}${
                        (status.my_picks?.length ?? 0) > 3 ? " …" : ""}`}
                </button>
                <span className="num text-xs text-muted">
                  R{status.round} · #{status.overall}
                </span>
              </>
            )}
            {status.platform && !status.espn_connected && (
              <span className="rounded border border-line px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-muted">
                {status.platform} adp
              </span>
            )}
            {status.phase !== "complete" && (
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
            )}
            <SyncDot status={status} />
            <SaveLeague status={status} onSaved={setStatus} />
            <Button size="sm" variant="ghost" className="h-7 px-2 text-[11px]"
                    onClick={() => { setStatus(null); setScreen("home"); }}>
              My leagues
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

      {/* THE NUMBERS MOVED, AND HERE IS WHY. A tag change drops the board and
          everything reprices on the next read, which without this reads as
          the app quietly disagreeing with what it said a minute ago. */}
      {!!(status.injury_news ?? []).length && newsKey !== seenNews && (
        <div className="border-b border-alarm/30 bg-alarm/[0.08] px-4 py-1.5 text-[11px]">
          <div className="mx-auto flex max-w-[1400px] items-center gap-2">
            <span className="shrink-0 font-semibold uppercase tracking-wider text-alarm">
              repriced
            </span>
            {/* THE SAME LETTERS AS THE ROSTER. This spelled the tags out --
                "questionable → out" -- which is a sentence where a badge
                does. One vocabulary for injuries across the whole app. */}
            {(status.injury_news ?? []).map((n, i) => (
              <span key={n.player_id + i} className="flex items-center gap-1">
                {i > 0 && <span className="text-muted/60">·</span>}
                <span className="truncate">{n.player_name}</span>
                {n.from
                  ? <InjuryTag tag={n.from} />
                  : <span className="text-[9px] text-muted">—</span>}
                <span className="text-muted/60">&rarr;</span>
                {n.to
                  ? <InjuryTag tag={n.to} />
                  : <span className="text-[9px] font-bold text-turf">OK</span>}
              </span>
            ))}
            <Button size="sm" variant="ghost" className="ml-auto h-6 shrink-0"
                    onClick={() => setSeenNews(newsKey)}>Dismiss</Button>
          </div>
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

      {/* min-h-0 lets this shrink instead of shoving the shortlist off the
          bottom; overflow-x-hidden because `overflow-y: auto` silently makes
          the other axis scrollable too. */}
      <main className="mx-auto w-full min-h-0 max-w-[1400px] flex-1 overflow-y-auto overflow-x-hidden px-4 py-4">
        {mode === "trade" ? (
          <div key="trade" className="tick-in">
            {/* Searching for a man who turns out to be unowned is not a failed
                trade search, it is a waiver question — so it changes mode
                rather than reporting nothing found. */}
            <Trade onFindOnWire={(id) => { setWireFocus(id); setMode("waiver"); }} />
          </div>
        ) : mode === "waiver" ? (
          <div key="waiver" className="tick-in">
            <Waivers drafting={status.phase !== "complete"}
                     focusId={wireFocus}
                     onFocused={() => setWireFocus(null)} />
          </div>
        ) : (
        <>
        {status.phase !== "complete" && (
        <div className="mb-3 flex rounded-md border border-line p-0.5">
          {(["team", "room"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={cn(
                "flex-1 rounded px-3 py-1.5 text-xs capitalize transition-colors",
                tab === t ? "bg-raised font-medium text-chalk" : "text-muted hover:text-chalk"
              )}
            >
              {t === "team" ? "My team" : "The room"}
            </button>
          ))}
        </div>
        )}

        <div key={tab} className="tick-in space-y-4">
          {tab === "team" ? (
            <MyTeam initial={report} />
          ) : (
            <div className="grid gap-4 lg:grid-cols-[1fr_1.15fr]">
              {/* Left: what you do. Right: the board itself, which is the
                  thing people actually stare at during a draft. */}
              <div className="flex flex-col gap-4">
                {!status.espn_connected && (
                  <PickInput status={status} onPicked={refreshAll}
                             busy={busy} setBusy={setBusy} />
                )}
                <div className="flex rounded-md border border-line p-0.5">
                  {(["feed", "room"] as const).map((v) => (
                    <button
                      key={v}
                      onClick={() => setSide(v)}
                      className={cn(
                        "flex-1 rounded px-3 py-1 text-[11px] transition-colors",
                        side === v ? "bg-raised font-medium text-chalk"
                                   : "text-muted hover:text-chalk"
                      )}
                    >
                      {v === "feed" ? "Off the board" : "The room"}
                    </button>
                  ))}
                </div>
                <div key={side} className="tick-in">
                  {side === "feed"
                    ? <Ticker status={status} onUndo={undo} busy={busy}
                                 landing={landingIn("gone")} />
                    : <Teams teams={teams} mySlot={status.my_slot ?? 1} />}
                </div>
                <Roster data={roster} onRemove={removePlayer} busy={busy}
                        onDropPlayer={draft} landing={landingIn("mine")} />
              </div>

              <div className="min-h-0">
                <Ladder data={ladder} myTurn={myTurn} onDraft={draft} onGone={markGone}
                        perPage={status.n_teams} />
              </div>
            </div>
          )}
        </div>
        </>
        )}
      </main>

      {lastSkip && (
        <div className="tick-in border-t border-line bg-raised px-4 py-1.5">
          <div className="mx-auto flex max-w-[1400px] items-center gap-3 text-[11px] text-muted">
            <span>Skipped {lastSkip.name}.</span>
            <button onClick={unskip}
                    className="font-medium text-turf underline-offset-2 hover:underline">
              Put him back
            </button>
            <button onClick={() => setLastSkip(null)} className="ml-auto hover:text-chalk">
              Dismiss
            </button>
          </div>
        </div>
      )}

      {mode === "draft" && status.phase !== "complete" && (
      <Shortlist
        data={list}
        busy={busy}
        myTurn={myTurn}
        stale={stale}
        onDraft={draft}
        onSkip={skip}
        planAt={planAt}
        onPlan={planFor}
      />
      )}

      {layer}

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
