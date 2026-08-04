import { useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input, Field } from "@/components/ui/input";
import { cn } from "@/lib/utils";

const RISK = ["safe", "combined", "aggressive"] as const;
type Risk = (typeof RISK)[number];
type Mode = null | "espn" | "manual";

function Choice<T extends string>({ value, options, onChange }: {
  value: T; options: readonly T[]; onChange: (v: T) => void;
}) {
  return (
    <div className="flex rounded-md border border-line p-0.5">
      {options.map((o) => (
        <button
          key={o} type="button" onClick={() => onChange(o)}
          className={cn(
            "flex-1 rounded px-2 py-1 text-xs capitalize transition-all duration-200",
            value === o ? "bg-turf text-ink font-semibold" : "text-muted hover:text-chalk"
          )}
        >
          {o}
        </button>
      ))}
    </div>
  );
}

/** The opening screen. One decision, made to look like one decision. */
function Chooser({ onPick }: { onPick: (m: Mode) => void }) {
  return (
    <div className="tick-in space-y-3">
      <button
        onClick={() => onPick("espn")}
        className="group w-full rounded-lg border border-line bg-panel p-4 text-left transition-all duration-200 hover:border-turf/50 hover:bg-raised"
      >
        <div className="flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-turf opacity-60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-turf" />
          </span>
          <span className="font-semibold">Connect my ESPN league</span>
          <span className="ml-auto text-muted transition-transform duration-200 group-hover:translate-x-0.5">›</span>
        </div>
        <p className="mt-1.5 text-[12px] leading-relaxed text-muted">
          Picks arrive on their own while you draft. Reads your league's real
          team count, lineup and scoring, so nothing is assumed.
        </p>
      </button>

      <button
        onClick={() => onPick("manual")}
        className="group w-full rounded-lg border border-line bg-panel p-4 text-left transition-all duration-200 hover:border-line/80 hover:bg-raised"
      >
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-muted" />
          <span className="font-semibold">Set it up myself</span>
          <span className="ml-auto text-muted transition-transform duration-200 group-hover:translate-x-0.5">›</span>
        </div>
        <p className="mt-1.5 text-[12px] leading-relaxed text-muted">
          Mock drafts, Yahoo, Sleeper, or a room with no API. You type the
          picks; everything else works the same.
        </p>
      </button>
    </div>
  );
}

export function Setup({ onReady }: { onReady: () => void }) {
  const [mode, setMode] = useState<Mode>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const [leagueId, setLeagueId] = useState("");
  const [season, setSeason] = useState(2026);
  const [s2, setS2] = useState("");
  const [swid, setSwid] = useState("");

  const [teams, setTeams] = useState(12);
  const [slot, setSlot] = useState(1);
  const [ppr, setPpr] = useState(1);
  const [rounds, setRounds] = useState(16);
  const [lineup, setLineup] = useState<Record<string, number>>({
    QB: 1, RB: 2, WR: 2, TE: 1, FLEX: 1, K: 1, DST: 1,
  });
  const [risk, setRisk] = useState<Risk>("combined");
  const [bench, setBench] = useState<Risk>("aggressive");

  async function go() {
    setBusy(true); setErr(null);
    try {
      if (mode === "espn") {
        await api.connectEspn({
          league_id: leagueId.trim(), season,
          espn_s2: s2.trim() || null, swid: swid.trim() || null,
        });
      } else {
        await api.setLeague({
          n_teams: teams, my_slot: slot, points_per_reception: ppr,
          roster_size: rounds, lineup, risk_tolerance: risk,
          bench_tolerance: bench,
        });
      }
      onReady();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  }

  return (
    <div className="mx-auto flex min-h-full max-w-xl flex-col justify-center px-5 py-12">
      {/* Wordmark carries the thesis: players are assets with a price. */}
      <div className="mb-1 flex items-baseline gap-2">
        <h1 className="text-3xl font-bold tracking-tight">FantasyEdge</h1>
        <span className="num text-[11px] text-turf">v1</span>
      </div>
      <p className="text-sm leading-relaxed text-muted">
        Three names, every pick — priced against what the board still owes you
        at your next turn.
      </p>

      <div className="my-6 h-px bg-gradient-to-r from-turf/40 via-line to-transparent" />

      {mode === null ? (
        <Chooser onPick={setMode} />
      ) : (
        <div className="tick-in">
          <button
            onClick={() => { setMode(null); setErr(null); }}
            className="mb-4 flex items-center gap-1.5 text-xs text-muted transition-colors hover:text-chalk"
          >
            <span className="transition-transform duration-200">‹</span> Back
          </button>

          <h2 className="mb-4 font-semibold">
            {mode === "espn" ? "Connect your ESPN league" : "Set up your league"}
          </h2>

          <div className="space-y-4">
            {mode === "espn" ? (
              <>
                <Field label="League ID"
                       hint="The number in your league URL: .../leagues/1234567">
                  <Input value={leagueId} onChange={(e) => setLeagueId(e.target.value)}
                         placeholder="1234567" inputMode="numeric" autoFocus />
                </Field>
                <Field label="Season">
                  <Input type="number" value={season}
                         onChange={(e) => setSeason(+e.target.value)} />
                </Field>

                <details className="group rounded-md border border-line px-3 py-2">
                  <summary className="cursor-pointer list-none text-xs text-muted transition-colors hover:text-chalk">
                    <span className="inline-block transition-transform duration-200 group-open:rotate-90">›</span>{" "}
                    Private league? Add your cookies
                  </summary>
                  <div className="mt-3 space-y-3">
                    <p className="text-[11px] leading-relaxed text-muted">
                      In a browser logged into ESPN: DevTools → Application →
                      Cookies → espn.com. Copy <code className="text-chalk">espn_s2</code>{" "}
                      and <code className="text-chalk">SWID</code>. They stay on this
                      machine and are only sent to ESPN. You can also put them in{" "}
                      <code className="text-chalk">.env</code> as ESPN_S2 and SWID.
                    </p>
                    <Field label="espn_s2">
                      <Input value={s2} onChange={(e) => setS2(e.target.value)} />
                    </Field>
                    <Field label="SWID">
                      <Input value={swid} onChange={(e) => setSwid(e.target.value)}
                             placeholder="{XXXXXXXX-....}" />
                    </Field>
                  </div>
                </details>

                <p className="rounded-md border border-line bg-panel px-3 py-2 text-[11px] leading-relaxed text-muted">
                  Team count, lineup and PPR come from the league itself. Traded
                  picks and your draft seat can be adjusted once you are in.
                </p>
              </>
            ) : (
              <>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Teams">
                    <Input type="number" min={2} max={20} value={teams}
                           onChange={(e) => setTeams(+e.target.value)} autoFocus />
                  </Field>
                  <Field label="Your draft slot" hint="Trades are handled later">
                    <Input type="number" min={1} max={teams} value={slot}
                           onChange={(e) => setSlot(+e.target.value)} />
                  </Field>
                  <Field label="Points per reception">
                    <Input type="number" step={0.5} min={0} max={2} value={ppr}
                           onChange={(e) => setPpr(+e.target.value)} />
                  </Field>
                  <Field label="Roster size">
                    <Input type="number" min={1} max={30} value={rounds}
                           onChange={(e) => setRounds(+e.target.value)} />
                  </Field>
                </div>

                <div>
                  <span className="eyebrow">Starting lineup</span>
                  <div className="mt-1.5 grid grid-cols-4 gap-2">
                    {Object.keys(lineup).map((pos) => (
                      <label key={pos} className="space-y-1">
                        <span className="block text-[11px] text-muted">{pos}</span>
                        <Input type="number" min={0} max={4} value={lineup[pos]}
                               onChange={(e) =>
                                 setLineup({ ...lineup, [pos]: +e.target.value })} />
                      </label>
                    ))}
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <span className="eyebrow block">Starter risk</span>
                    <Choice value={risk} options={RISK} onChange={setRisk} />
                  </div>
                  <div className="space-y-1.5">
                    <span className="eyebrow block">Bench risk</span>
                    <Choice value={bench} options={RISK} onChange={setBench} />
                  </div>
                </div>
                <p className="text-[11px] leading-relaxed text-muted">
                  Bench risk runs separately because a bench bust costs nothing —
                  you never start him — while a bench hit becomes a starter. That
                  payoff is one-sided, so variance is worth more there.
                </p>
              </>
            )}

            {err && (
              <p className="tick-in rounded-md border border-alarm/30 bg-alarm/10 px-3 py-2 text-xs text-alarm">
                {err}
              </p>
            )}

            <Button
              size="lg"
              className="w-full shadow-[0_0_28px_-6px] shadow-turf/45 transition-shadow hover:shadow-[0_0_34px_-4px] hover:shadow-turf/60"
              onClick={go}
              disabled={busy || (mode === "espn" && !leagueId.trim())}
            >
              {busy ? "Building the board…" : "Start drafting"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
