import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  api, type LeagueRoster, type TradeOffer, type TradePlayer,
  type TradeVerdict as Verdict,
} from "@/lib/api";
import { TradeVerdict } from "@/components/TradeVerdict";
import {
  AddByName, DropZone, Row, StancePicker, type Stance,
} from "@/components/TradeDeck";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Trade mode.
 *
 * AUTOMATIC is the synced path: real rosters on both sides, so the app can
 * scan all eleven opponents and put the offers worth sending at the top.
 * MANUAL is for a league we cannot read — you name both sides yourself and it
 * prices them. Both get drag AND typeahead, because the fastest way to add a
 * name depends on whether you are browsing or aiming, and that changes several
 * times inside one negotiation.
 *
 * The verdict recomputes as you build. A trade is something you feel out.
 */

const STEPS = [
  "reading both rosters",
  "filling your lineup, week by week",
  "playing the season out, 4,000 times",
  "pricing what the roster spots cost",
];

function Thinking({ label }: { label?: string }) {
  const [i, setI] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setI((n) => (n + 1) % STEPS.length), 900);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-line bg-panel py-10">
      {/* Seventeen bars, filling. The wait IS a season being simulated, so the
          spinner may as well be one. */}
      <div className="flex gap-1">
        {Array.from({ length: 17 }).map((_, w) => (
          <motion.span
            key={w}
            className="h-7 w-1.5 rounded-full bg-turf/70"
            initial={{ scaleY: 0.2, opacity: 0.25 }}
            animate={{ scaleY: [0.2, 1, 0.2], opacity: [0.25, 1, 0.25] }}
            transition={{ duration: 1.4, repeat: Infinity, delay: w * 0.055,
                          ease: "easeInOut" }}
          />
        ))}
      </div>
      <AnimatePresence mode="wait">
        <motion.span key={i}
          initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.2 }}
          className="text-[11.5px] text-muted">
          {label ?? STEPS[i]}…
        </motion.span>
      </AnimatePresence>
    </div>
  );
}

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
  const [offers, setOffers] = useState<TradeOffer[] | null>(null);
  const [counters, setCounters] = useState<TradeOffer[] | null>(null);
  const [scanning, setScanning] = useState(false);

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

  /** Every player we might need to draw, from rosters or typed in by hand. */
  const known = useMemo(() => {
    const m = new Map<string, TradePlayer>(extra);
    for (const t of rosters ?? []) for (const p of t.players) m.set(p.player_id, p);
    return m;
  }, [rosters, extra]);

  const price = useCallback(async (g: string[], k: string[]) => {
    if (!g.length && !k.length) { setV(null); setCounters(null); return; }
    setBusy(true);
    try {
      setV(await api.tradeEvaluate(g, k));
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  }, []);

  // Debounced: tapping four names in a row should cost one simulation.
  useEffect(() => {
    const t = setTimeout(() => price(give, get), 350);
    return () => clearTimeout(t);
  }, [give, get, price]);

  const add = (side: "give" | "get", id: string) => {
    const set = side === "give" ? setGive : setGet;
    set((cur) => (cur.includes(id) ? cur : [...cur, id]));
  };
  const drop = (side: "give" | "get", id: string) => {
    const set = side === "give" ? setGive : setGet;
    set((cur) => cur.filter((x) => x !== id));
  };
  const toggle = (side: "give" | "get", id: string) => {
    const cur = side === "give" ? give : get;
    cur.includes(id) ? drop(side, id) : add(side, id);
  };

  async function scan() {
    setScanning(true);
    setCounters(null);
    try {
      setOffers((await api.tradeSuggest(1, 6, stance)).offers);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setScanning(false); }
  }

  async function askCounter() {
    if (them == null) return;
    setScanning(true);
    try {
      setCounters((await api.tradeCounter(give, get, them, stance)).offers);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setScanning(false); }
  }

  function load(o: TradeOffer) {
    if (o.team_id > 0) setThem(o.team_id);
    setExtra((m) => {
      const n = new Map(m);
      for (const p of [...o.give, ...o.get]) n.set(p.player_id, p);
      return n;
    });
    setGive(o.give.map((p) => p.player_id));
    setGet(o.get.map((p) => p.player_id));
    setOffers(null);
    setCounters(null);
  }

  function addTyped(side: "give" | "get", h: { player_id: string; player_name: string;
                                               position: string; headshot: string | null }) {
    setExtra((m) => new Map(m).set(h.player_id, {
      player_id: h.player_id, player_name: h.player_name,
      position: h.position, headshot: h.headshot,
    }));
    add(side, h.player_id);
  }

  if (!entry) return <Thinking label="reading your league" />;

  const auto = entry === "auto" && !!mine;
  const myIds = auto ? new Set(mine!.players.map((p) => p.player_id)) : null;
  const theirIds = auto && other ? new Set(other.players.map((p) => p.player_id)) : null;

  return (
    <div className="space-y-3">
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
                  onClick={scan} disabled={scanning}>
            {scanning ? "scanning…" : offers ? "Re-roll" : "Find me trades"}
          </Button>
        )}
        {(give.length > 0 || get.length > 0) && (
          <Button size="sm" variant="ghost" className="h-7 text-[11px]"
                  onClick={() => { setGive([]); setGet([]); setV(null); setCounters(null); }}>
            Clear
          </Button>
        )}
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_1.05fr]">
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <DropZone label="you give" tone="alarm" ids={give} players={known}
                      onDrop={(id) => add("give", id)}
                      onRemove={(id) => drop("give", id)} />
            <DropZone label="you get" tone="turf" ids={get} players={known}
                      onDrop={(id) => add("get", id)}
                      onRemove={(id) => drop("get", id)} />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <section className="rounded-lg border border-line bg-panel">
              <header className="space-y-1.5 border-b border-line p-2">
                <span className="eyebrow">Your team</span>
                <AddByName placeholder="type a name…" restrictTo={myIds}
                           onAdd={(h) => addTyped("give", h)} />
              </header>
              {auto && (
                <ul className="max-h-[34vh] space-y-0.5 overflow-y-auto p-1.5">
                  {mine!.players.map((p) => (
                    <Row key={p.player_id} p={p} draggable
                         selected={give.includes(p.player_id)}
                         onToggle={() => toggle("give", p.player_id)} />
                  ))}
                </ul>
              )}
            </section>

            <section className="rounded-lg border border-line bg-panel">
              <header className="space-y-1.5 border-b border-line p-2">
                {auto ? (
                  <select
                    value={them ?? ""}
                    onChange={(e) => { setThem(Number(e.target.value)); setGet([]); }}
                    className="w-full bg-transparent text-[11px] font-semibold text-chalk focus:outline-none"
                  >
                    {(rosters ?? []).filter((t) => !t.mine).map((t) => (
                      <option key={t.team_id} value={t.team_id} className="bg-panel">
                        {t.name}
                      </option>
                    ))}
                  </select>
                ) : <span className="eyebrow">Their team</span>}
                <AddByName placeholder="type a name…" restrictTo={theirIds}
                           onAdd={(h) => addTyped("get", h)} />
              </header>
              {auto && other && (
                <ul className="max-h-[34vh] space-y-0.5 overflow-y-auto p-1.5">
                  {other.players.map((p) => (
                    <Row key={p.player_id} p={p} draggable
                         selected={get.includes(p.player_id)}
                         onToggle={() => toggle("get", p.player_id)} />
                  ))}
                </ul>
              )}
            </section>
          </div>
        </div>

        <div className="space-y-3">
          {busy && !v && <Thinking />}
          {v && (
            <>
              <TradeVerdict v={v} />
              {auto && (
                <Button size="sm" variant="outline" className="w-full text-[11px]"
                        onClick={askCounter} disabled={scanning}>
                  {scanning ? "looking for a better version…" : "Find a better version of this"}
                </Button>
              )}
            </>
          )}
          {!v && !busy && !offers && (
            <div className="rounded-lg border border-dashed border-line p-8 text-center">
              <p className="text-sm text-muted">Drag or type names into the two piles.</p>
              <p className="mt-1 text-[11px] leading-snug text-muted">
                The verdict is what the deal does to the lineup you can field —
                not what the two piles add up to.
              </p>
            </div>
          )}

          <AnimatePresence>
            {(offers || counters) && (
              <motion.section
                initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                className="rounded-lg border border-line bg-panel"
              >
                <header className="flex items-center gap-2 border-b border-line px-3 py-2">
                  <span className="eyebrow">
                    {counters ? "Better versions of this deal" : "Offers worth sending"}
                  </span>
                  <button onClick={() => { setOffers(null); setCounters(null); }}
                          className="ml-auto text-[11px] text-muted hover:text-chalk">
                    close
                  </button>
                </header>
                {(counters ?? offers ?? []).length === 0 ? (
                  <p className="px-3 py-6 text-center text-[11.5px] text-muted">
                    {counters
                      ? "No nearby version does better while they would still accept."
                      : "Nothing worth offering — every roster is priced about right against yours."}
                  </p>
                ) : (
                  <ul className="divide-y divide-line/60">
                    {(counters ?? offers ?? []).map((o, i) => (
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

          {err && (
            <p className="rounded-md border border-alarm/30 bg-alarm/10 px-3 py-2 text-[11px] text-alarm">
              {err}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
