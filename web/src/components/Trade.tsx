import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  api, type LeagueRoster, type TradeOffer, type TradeVerdict as Verdict,
} from "@/lib/api";
import { TradeVerdict } from "@/components/TradeVerdict";
import { POS_HUE } from "@/components/Charts";
import { PlayerHover } from "@/components/PlayerHover";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Trade mode.
 *
 * The interaction is deliberately the same shape as the question: two columns,
 * yours and theirs, and you tap names into a deal. No form, no submit, no
 * modal — the verdict recomputes as you build, because a trade is something
 * you feel out rather than specify.
 */

/** Simulating two seasons takes a beat. Say something true while it does. */
const STEPS = [
  "reading both rosters",
  "filling your lineup, week by week",
  "playing the season 4,000 times",
  "pricing the roster spots",
];

function Thinking() {
  const [i, setI] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setI((n) => (n + 1) % STEPS.length), 900);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-line bg-panel py-10">
      {/* Seventeen weeks, filling. The wait is the season being simulated, so
          the spinner may as well be a season. */}
      <div className="flex gap-1">
        {Array.from({ length: 17 }).map((_, w) => (
          <motion.span
            key={w}
            className="h-6 w-1.5 rounded-full bg-turf/70"
            initial={{ scaleY: 0.25, opacity: 0.3 }}
            animate={{ scaleY: [0.25, 1, 0.25], opacity: [0.3, 1, 0.3] }}
            transition={{ duration: 1.4, repeat: Infinity, delay: w * 0.055,
                          ease: "easeInOut" }}
          />
        ))}
      </div>
      <AnimatePresence mode="wait">
        <motion.span
          key={i}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.2 }}
          className="text-[11.5px] text-muted"
        >
          {STEPS[i]}…
        </motion.span>
      </AnimatePresence>
    </div>
  );
}

function Pick({ p, on, onToggle }: {
  p: LeagueRoster["players"][0]; on: boolean; onToggle: () => void;
}) {
  return (
    <li>
      <button
        onClick={onToggle}
        className={cn(
          "flex w-full items-center gap-2 rounded border px-2 py-1.5 text-left transition-colors",
          on ? "border-turf/50 bg-turf/10" : "border-transparent hover:bg-raised/60"
        )}
      >
        <span className="w-7 shrink-0 text-[10px] font-bold"
              style={{ color: POS_HUE[p.position] ?? "#8CA096" }}>
          {p.position}
        </span>
        <PlayerHover playerId={p.player_id} className="min-w-0 flex-1">
          <span className="block cursor-help truncate text-xs">{p.player_name}</span>
        </PlayerHover>
        {p.injury_status && p.injury_status !== "ACTIVE" && (
          <span className="shrink-0 text-[9px] uppercase text-clock">
            {p.injury_status.slice(0, 3)}
          </span>
        )}
        <span className="num w-8 shrink-0 text-right text-[10.5px] text-muted">
          {Math.round(p.projected_points ?? 0)}
        </span>
      </button>
    </li>
  );
}

export function Trade() {
  const [rosters, setRosters] = useState<LeagueRoster[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [them, setThem] = useState<number | null>(null);
  const [give, setGive] = useState<string[]>([]);
  const [get, setGet] = useState<string[]>([]);
  const [v, setV] = useState<Verdict | null>(null);
  const [busy, setBusy] = useState(false);
  const [offers, setOffers] = useState<TradeOffer[] | null>(null);
  const [scanning, setScanning] = useState(false);

  useEffect(() => {
    api.rosters()
      .then((r) => {
        setRosters(r.teams);
        const first = r.teams.find((t) => !t.mine);
        if (first) setThem(first.team_id);
      })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);

  const mine = rosters?.find((t) => t.mine) ?? null;
  const other = rosters?.find((t) => t.team_id === them) ?? null;

  const price = useCallback(async (g: string[], k: string[]) => {
    if (!g.length && !k.length) { setV(null); return; }
    setBusy(true);
    try {
      setV(await api.tradeEvaluate(g, k));
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  }, []);

  // Recompute as the deal is built. Debounced, because tapping four names in
  // a row should cost one simulation, not four.
  useEffect(() => {
    const t = setTimeout(() => price(give, get), 350);
    return () => clearTimeout(t);
  }, [give, get, price]);

  function toggle(list: string[], set: (v: string[]) => void, id: string) {
    set(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  }

  async function scan() {
    setScanning(true);
    try {
      setOffers((await api.tradeSuggest(1, 6)).offers);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setScanning(false); }
  }

  function load(o: TradeOffer) {
    setThem(o.team_id);
    setGive(o.give.map((p) => p.player_id));
    setGet(o.get.map((p) => p.player_id));
    setOffers(null);
  }

  if (err && !rosters) {
    return (
      <div className="rounded-lg border border-line bg-panel p-6 text-center">
        <p className="text-sm text-muted">{err}</p>
        <p className="mt-2 text-[11px] text-muted">
          Trade mode reads every team&rsquo;s roster from ESPN, so it needs a
          connected league.
        </p>
      </div>
    );
  }
  if (!rosters) return <Thinking />;

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_1.1fr]">
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <span className="eyebrow">Build a deal</span>
          <Button size="sm" variant="outline" className="ml-auto h-7 text-[11px]"
                  onClick={scan} disabled={scanning}>
            {scanning ? "scanning…" : "Find me trades"}
          </Button>
          {(give.length > 0 || get.length > 0) && (
            <Button size="sm" variant="ghost" className="h-7 text-[11px]"
                    onClick={() => { setGive([]); setGet([]); setV(null); }}>
              Clear
            </Button>
          )}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <section className="rounded-lg border border-line bg-panel">
            <header className="border-b border-line px-2.5 py-1.5">
              <span className="eyebrow">Your team</span>
            </header>
            <ul className="max-h-[42vh] space-y-0.5 overflow-y-auto p-1.5">
              {mine?.players.map((p) => (
                <Pick key={p.player_id} p={p} on={give.includes(p.player_id)}
                      onToggle={() => toggle(give, setGive, p.player_id)} />
              ))}
            </ul>
          </section>

          <section className="rounded-lg border border-line bg-panel">
            <header className="border-b border-line px-2.5 py-1.5">
              <select
                value={them ?? ""}
                onChange={(e) => { setThem(Number(e.target.value)); setGet([]); }}
                className="w-full bg-transparent text-[11px] font-semibold text-chalk focus:outline-none"
              >
                {rosters.filter((t) => !t.mine).map((t) => (
                  <option key={t.team_id} value={t.team_id} className="bg-panel">
                    {t.name}
                  </option>
                ))}
              </select>
            </header>
            <ul className="max-h-[42vh] space-y-0.5 overflow-y-auto p-1.5">
              {other?.players.map((p) => (
                <Pick key={p.player_id} p={p} on={get.includes(p.player_id)}
                      onToggle={() => toggle(get, setGet, p.player_id)} />
              ))}
            </ul>
          </section>
        </div>
      </div>

      <div className="space-y-3">
        {busy && !v && <Thinking />}
        {v && <TradeVerdict v={v} />}
        {!v && !busy && !offers && (
          <div className="rounded-lg border border-dashed border-line p-8 text-center">
            <p className="text-sm text-muted">Tap names on either side.</p>
            <p className="mt-1 text-[11px] text-muted">
              The verdict is what the deal does to the lineup you can field —
              not what the two piles add up to.
            </p>
          </div>
        )}

        <AnimatePresence>
          {offers && (
            <motion.section
              initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className="rounded-lg border border-line bg-panel"
            >
              <header className="flex items-center gap-2 border-b border-line px-3 py-2">
                <span className="eyebrow">Offers worth sending</span>
                <button onClick={() => setOffers(null)}
                        className="ml-auto text-[11px] text-muted hover:text-chalk">
                  close
                </button>
              </header>
              {offers.length === 0 ? (
                <p className="px-3 py-6 text-center text-[11.5px] text-muted">
                  Nothing worth offering — every roster is priced about right
                  against yours.
                </p>
              ) : (
                <ul className="divide-y divide-line/60">
                  {offers.map((o, i) => (
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
                            {o.give.map((p) => p.player_name).join(", ")}
                          </span>
                          {" → "}
                          <span className="text-turf/80">
                            {o.get.map((p) => p.player_name).join(", ")}
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

        {err && v && (
          <p className="rounded-md border border-alarm/30 bg-alarm/10 px-3 py-2 text-[11px] text-alarm">
            {err}
          </p>
        )}
      </div>
    </div>
  );
}
