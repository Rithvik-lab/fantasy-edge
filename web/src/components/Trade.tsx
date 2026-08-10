import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  api, type Ask, type LeagueRoster, type Piece, type Scan, type TeamRead,
  type TradeOffer, type TradePlayer, type TradeVerdict as Verdict,
} from "@/lib/api";
import { TradeVerdict } from "@/components/TradeVerdict";
import { AddByName, StancePicker, type Stance } from "@/components/TradeDeck";
import { ManualRoster, RosterPanel, TradePile } from "@/components/TradeBoard";
import { TradeScan, TeamSummary } from "@/components/TradeScan";
import { Simulating } from "@/components/Simulating";
import { useFlight } from "@/components/Flight";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Trade mode.
 *
 * Laid out the way the question is shaped: my team on the far left, theirs on
 * the far right, and what is crossing the table in the middle where both can
 * be read at once. Names move by drag or by click, from either roster.
 *
 * AUTOMATIC is the synced path — real rosters, so the app can scan eleven
 * opponents and put the offers worth sending at the top. MANUAL is for a
 * league we cannot read; you name both sides. Both get drag and typeahead.
 *
 * ANALYSE IS EXPLICIT. An earlier version repriced on every change, which
 * sounds responsive and is not: a four-player deal is assembled over several
 * seconds, so it spent simulations on half-built trades nobody meant to ask
 * about, and the answer flickered while you were still deciding.
 */

export function Trade() {
  const [rosters, setRosters] = useState<LeagueRoster[] | null>(null);
  const [entry, setEntry] = useState<"auto" | "manual" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [them, setThem] = useState<number | null>(null);
  const [give, setGive] = useState<string[]>([]);
  const [get, setGet] = useState<string[]>([]);
  const [extra, setExtra] = useState<Map<string, TradePlayer>>(new Map());
  const [v, setV] = useState<Verdict | null>(null);
  const [busy, setBusy] = useState(false);
  const [stance, setStance] = useState<Stance>("fair");
  const [scan, setScan] = useState<Scan | null>(null);
  // Deals already shown. Re-roll means SHOW ME ANOTHER ONE, and the first
  // version handed back the same offer with the same numbers, which reads
  // exactly like a dead button.
  const [seen, setSeen] = useState<string[]>([]);
  const [counters, setCounters] = useState<TradeOffer[] | null>(null);
  const [scanning, setScanning] = useState(false);
  // Manual mode has nothing to read, so the rosters are typed. They are also
  // the hypothetical: any roster, real or not, can be priced against.
  const [myManual, setMyManual] = useState<string[]>([]);
  const [theirManual, setTheirManual] = useState<string[]>([]);
  const { launch, layer, landingIn } = useFlight();

  useEffect(() => {
    api.rosters()
      .then((r) => {
        setRosters(r.teams);
        setEntry("auto");
        const first = r.teams.find((t) => !t.mine);
        if (first) setThem(first.team_id);
      })
      .catch(() => { setRosters([]); setEntry("manual"); });
  }, []);

  const mine = rosters?.find((t) => t.mine) ?? null;
  const other = rosters?.find((t) => t.team_id === them) ?? null;

  /** Everyone we might need to draw, from rosters or typed in by hand. */
  const known = useMemo(() => {
    const m = new Map<string, TradePlayer>(extra);
    for (const t of rosters ?? []) for (const p of t.players) m.set(p.player_id, p);
    return m;
  }, [rosters, extra]);

  const price = useCallback(async (g: string[], k: string[],
                                  roster: string[] = []) => {
    if (!g.length && !k.length) { setV(null); setCounters(null); return; }
    setBusy(true);
    try {
      setV(await api.tradeEvaluate(g, k, roster));
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  }, []);

  const add = (side: "give" | "get", id: string, from?: Element | null) => {
    const set = side === "give" ? setGive : setGet;
    set((cur) => (cur.includes(id) ? cur : [...cur, id]));
    const p = known.get(id);
    if (from && p) launch(from, p.player_name, p.headshot ?? null, side);
  };
  const drop = (side: "give" | "get", id: string) => {
    const set = side === "give" ? setGive : setGet;
    set((cur) => cur.filter((x) => x !== id));
  };
  const toggle = (side: "give" | "get", id: string) => {
    const cur = side === "give" ? give : get;
    if (cur.includes(id)) drop(side, id); else add(side, id);
  };

  /**
   * Read the league and load the best deal it finds.
   *
   * The scan ends with the offer ON THE BOARD and priced, not sitting in a
   * list waiting to be clicked: the question was "find me a trade", and an
   * answer you still have to assemble by hand is half an answer.
   */
  async function runScan(a: Ask = {}) {
    setScanning(true);
    setCounters(null);
    try {
      const s = await api.tradeScan({ stance, ...a, seen: a.seen ?? seen });
      setScan(s);
      setSeen((prev) => [...new Set([...prev, ...s.keys])]);
      setErr(null);
      if (s.offers.length) load(s.offers[0], s);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setScanning(false); }
  }

  async function askCounter() {
    if (them == null) return;
    setScanning(true);
    try {
      const c = await api.tradeCounter(give, get, them, { stance, seen });
      setCounters(c.offers);
      setSeen((prev) => [...new Set([...prev, ...c.keys])]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setScanning(false); }
  }

  /** Put a deal on the board and price it in the same motion. */
  function load(o: TradeOffer, from?: Scan) {
    if (o.team_id > 0) setThem(o.team_id);
    setExtra((m) => {
      const n = new Map(m);
      for (const p of [...o.give, ...o.get]) n.set(p.player_id, p);
      return n;
    });
    const g = o.give.map((p) => p.player_id);
    const k = o.get.map((p) => p.player_id);
    setGive(g);
    setGet(k);
    setCounters(null);
    if (from) setScan(from);
    price(g, k, auto ? [] : myManual);
  }

  /** A name clicked in the league read goes straight into the right pile. */
  function fromRead(t: TeamRead, p: Piece, side: "give" | "get") {
    setThem(t.team_id);
    setExtra((m) => new Map(m).set(p.player_id, {
      player_id: p.player_id, player_name: p.player_name,
      position: p.position, headshot: null,
      projected_points: p.projected_points,
    }));
    add(side, p.player_id);
  }

  /** Keep a typed-in player around so we can draw him later. */
  function remember(h: { player_id: string; player_name: string;
                         position: string; headshot: string | null }) {
    setExtra((m) => new Map(m).set(h.player_id, {
      player_id: h.player_id, player_name: h.player_name,
      position: h.position, headshot: h.headshot,
    }));
  }

  function addTyped(side: "give" | "get",
                    h: { player_id: string; player_name: string;
                         position: string; headshot: string | null }) {
    setExtra((m) => new Map(m).set(h.player_id, {
      player_id: h.player_id, player_name: h.player_name,
      position: h.position, headshot: h.headshot,
    }));
    add(side, h.player_id);
  }

  if (!entry) return <Simulating label="reading your league" />;

  const auto = entry === "auto" && !!mine;
  const myIds = auto ? new Set(mine!.players.map((p) => p.player_id)) : null;
  const theirIds = auto && other ? new Set(other.players.map((p) => p.player_id)) : null;
  const empty = give.length === 0 && get.length === 0;
  const theirRead = scan?.teams.find((t) => t.team_id === them) ?? null;

  return (
    <div className="space-y-3">
      {layer}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-md border border-line p-0.5">
          {(["auto", "manual"] as const).map((e) => (
            <button
              key={e}
              onClick={() => setEntry(e)}
              disabled={e === "auto" && !mine}
              title={e === "auto"
                ? "your synced rosters, with offers found for you"
                : "name both sides yourself"}
              className={cn("rounded px-3 py-1 text-[11px] transition-colors",
                entry === e ? "bg-raised font-medium text-chalk"
                  : "text-muted hover:text-chalk disabled:opacity-40")}
            >
              {e === "auto" ? "Automatic" : "Manual"}
            </button>
          ))}
        </div>
        <StancePicker value={stance} onChange={setStance} />
        {auto && (
          <Button size="sm" variant="outline" className="h-7 text-[11px]"
                  onClick={() => runScan()} disabled={scanning}
                  title="read all twelve rosters and put the best deal on the board">
            {scanning ? "reading the league…"
                      : scan ? "Scan again" : "Scan the league"}
          </Button>
        )}
        {!empty && (
          <Button size="sm" variant="ghost" className="h-7 text-[11px]"
                  onClick={() => { setGive([]); setGet([]); setV(null); setCounters(null); }}>
            Clear
          </Button>
        )}
        {/* A disabled button with no reason is a dead end. The commonest
            confusion here is putting names on a ROSTER and expecting that to
            be the trade, so say the missing step rather than just greying
            out. */}
        <div className="ml-auto flex items-center gap-2">
          {empty && (
            <span className="text-[11px] text-clock">
              click a player on either roster to put him in the trade
            </span>
          )}
          <Button size="sm" className="h-8 px-6 text-[12px]"
                  onClick={() => price(give, get, auto ? [] : myManual)}
                  disabled={busy || empty}
                  title={empty
                    ? "Nothing is in the trade yet — click a name on a roster, or drag it into a pile"
                    : "Price this deal"}>
            {busy ? "Analysing…" : "Analyse"}
          </Button>
        </div>
      </div>

      {/* The league read sits ABOVE the board, because it is what you look at
          first: which manager, then which deal. It used to render inside the
          verdict block, so scanning before analysing anything computed eleven
          rosters and showed nothing at all. */}
      {scanning && !scan && (
        <Simulating label="reading every roster in the league" />
      )}

      <AnimatePresence>
        {scan && (
          <TradeScan
            scan={scan} busy={scanning} give={give} players={known}
            activeTeam={them}
            onOpenTeam={(t) => { setThem(t.team_id); setGet([]); setV(null); }}
            onLoadOffer={(o) => load(o)}
            onTake={(t, p) => fromRead(t, p, "get")}
            onSend={(t, p) => fromRead(t, p, "give")}
            onAgain={(a) => runScan(a)}
            onClose={() => setScan(null)}
          />
        )}
      </AnimatePresence>

      <div className="grid gap-3 lg:grid-cols-[1fr_0.8fr_0.8fr_1fr]">
        <div className="flex min-h-0 flex-col gap-2">
          {auto ? (
            <>
              <AddByName placeholder="add from my team…" restrictTo={myIds}
                         onAdd={(h) => addTyped("give", h)} />
              {scan && <TeamSummary read={scan.me} title="Your team" mine />}
              <RosterPanel title="My team" roster={mine} side="mine"
                           selected={give} onToggle={(id) => toggle("give", id)}
                           onAdd={(id, el) => add("give", id, el)} />
            </>
          ) : (
            <ManualRoster title="My team" ids={myManual} players={known}
                          selected={give}
                          onToggle={(id) => toggle("give", id)}
                          onAdd={(id, el) => add("give", id, el)}
                          onRemove={(id) => {
                            setMyManual((r) => r.filter((x) => x !== id));
                            drop("give", id);
                          }}>
              <AddByName placeholder="type a player on my team…" restrictTo={null}
                         onAdd={(h) => {
                           remember(h);
                           setMyManual((r) => r.includes(h.player_id) ? r : [...r, h.player_id]);
                           add("give", h.player_id);
                         }} />
            </ManualRoster>
          )}
        </div>

        <TradePile label="you give" tone="alarm" ids={give} players={known}
                   side="give" landing={landingIn("give")}
                   onDrop={(id) => add("give", id)}
                   onRemove={(id) => drop("give", id)} />
        <TradePile label="you get" tone="turf" ids={get} players={known}
                   side="get" landing={landingIn("get")}
                   onDrop={(id) => add("get", id)}
                   onRemove={(id) => drop("get", id)} />

        <div className="flex min-h-0 flex-col gap-2">
          {auto ? (
            <select
              value={them ?? ""}
              onChange={(e) => { setThem(Number(e.target.value)); setGet([]); setV(null); }}
              className="h-7 shrink-0 rounded border border-line bg-ink px-2 text-[11px] font-semibold text-chalk focus:outline-none"
            >
              {(rosters ?? []).filter((t) => !t.mine).map((t) => (
                <option key={t.team_id} value={t.team_id} className="bg-panel">
                  {t.name}
                </option>
              ))}
            </select>
          ) : null}
          {auto ? (
            <>
              <AddByName placeholder="search their roster…" restrictTo={theirIds}
                         onAdd={(h) => addTyped("get", h)} />
              {theirRead && (
                <TeamSummary read={theirRead} title="Their team" />
              )}
              <RosterPanel title={other?.name ?? "Their team"} roster={other}
                           side="theirs" selected={get}
                           onToggle={(id) => toggle("get", id)}
                           onAdd={(id, el) => add("get", id, el)} />
            </>
          ) : (
            <ManualRoster title="Their team" ids={theirManual} players={known}
                          selected={get}
                          onToggle={(id) => toggle("get", id)}
                          onAdd={(id, el) => add("get", id, el)}
                          onRemove={(id) => {
                            setTheirManual((r) => r.filter((x) => x !== id));
                            drop("get", id);
                          }}>
              <AddByName placeholder="type a player on their team…" restrictTo={null}
                         onAdd={(h) => {
                           remember(h);
                           setTheirManual((r) => r.includes(h.player_id) ? r : [...r, h.player_id]);
                           add("get", h.player_id);
                         }} />
            </ManualRoster>
          )}
        </div>
      </div>

      {busy && <Simulating />}

      {v && !busy && (
        <div className="grid gap-3 lg:grid-cols-[1.25fr_1fr]">
          <TradeVerdict v={v} />
          <div className="space-y-3">
            {auto && (
              <Button size="sm" variant="outline" className="w-full text-[11px]"
                      onClick={askCounter} disabled={scanning}>
                {scanning ? "looking for a better version…" : "Counter this trade"}
              </Button>
            )}
            <AnimatePresence>
              {counters && (
                <motion.section
                  initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  className="rounded-lg border border-line bg-panel"
                >
                  <header className="flex items-center gap-2 border-b border-line px-3 py-2">
                    <span className="eyebrow">Better versions of this deal</span>
                    <button onClick={() => setCounters(null)}
                            className="ml-auto text-[11px] text-muted hover:text-chalk">
                      close
                    </button>
                  </header>
                  {counters.length === 0 ? (
                    <p className="px-3 py-6 text-center text-[11.5px] leading-snug text-muted">
                      No nearby version does better while they would still
                      accept. Scan the league for a different partner, or say
                      what is wrong with this one.
                    </p>
                  ) : (
                    <ul className="divide-y divide-line/60">
                      {counters.map((o, i) => (
                        <li key={i}>
                          <button onClick={() => load(o)}
                                  className="w-full px-3 py-2 text-left transition-colors hover:bg-raised/60">
                            <div className="flex items-baseline gap-2">
                              <span className="text-xs font-medium">{o.team_name}</span>
                              <span className="num ml-auto text-xs font-semibold text-turf">
                                +{Math.round(o.our_gain)}
                              </span>
                            </div>
                            <p className="mt-0.5 text-[11px] leading-snug text-muted">
                              <span className="text-alarm/80">
                                {o.give.map((p) => p.player_name).join(", ") || "nothing"}
                              </span>
                              {" → "}
                              <span className="text-turf/80">
                                {o.get.map((p) => p.player_name).join(", ") || "nothing"}
                              </span>
                            </p>
                            <p className="mt-0.5 text-[10.5px] text-muted">
                              they read it as +{Math.round(o.their_gain)} in their favour
                            </p>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </motion.section>
              )}
            </AnimatePresence>
          </div>
        </div>
      )}

      {!v && !busy && (
        <div className="rounded-lg border border-dashed border-line p-6 text-center">
          <p className="text-sm text-muted">
            Click names into the two piles and hit Analyse
            {auto && !scan && (
              <>
                {" — or "}
                <button onClick={() => runScan()} disabled={scanning}
                        className="text-turf underline-offset-2 hover:underline">
                  scan the league
                </button>
                {" and let it find one"}
              </>
            )}.
          </p>
          <p className="mt-1 text-[11px] leading-snug text-muted">
            The ruling is what the deal does to the lineup you can field —
            not what the two piles add up to.
          </p>
        </div>
      )}

      {err && (
        <p className="rounded-md border border-alarm/30 bg-alarm/10 px-3 py-2 text-[11px] text-alarm">
          {err}
        </p>
      )}
    </div>
  );
}
