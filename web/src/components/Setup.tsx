import { useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input, Field } from "@/components/ui/input";
import { cn } from "@/lib/utils";

const RISK = ["safe", "combined", "aggressive"] as const;

function Choice<T extends string>({ value, options, onChange }: {
  value: T; options: readonly T[]; onChange: (v: T) => void;
}) {
  return (
    <div className="flex rounded-md border border-line p-0.5">
      {options.map((o) => (
        <button
          key={o}
          type="button"
          onClick={() => onChange(o)}
          className={cn(
            "flex-1 rounded px-2 py-1 text-xs capitalize transition-colors",
            value === o ? "bg-turf text-ink font-semibold" : "text-muted hover:text-chalk"
          )}
        >
          {o}
        </button>
      ))}
    </div>
  );
}

/**
 * Two ways in, and the ESPN one is the point.
 *
 * Connecting a real league reads its own team count, lineup and PPR off
 * ESPN, so the tool fits whatever league you actually play in instead of
 * assuming a 12-team full-PPR one. Manual setup stays for mocks, Yahoo,
 * Sleeper, or anywhere the API cannot reach.
 */
export function Setup({ onReady }: { onReady: () => void }) {
  const [mode, setMode] = useState<"espn" | "manual">("espn");
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
  const [risk, setRisk] = useState<(typeof RISK)[number]>("combined");
  const [bench, setBench] = useState<(typeof RISK)[number]>("aggressive");

  async function go() {
    setBusy(true);
    setErr(null);
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
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex min-h-full max-w-xl flex-col justify-center px-5 py-12">
      <h1 className="text-2xl font-bold tracking-tight">FantasyEdge</h1>
      <p className="mt-1 text-sm text-muted">
        Three names, every pick, priced against what the board still owes you.
      </p>

      <div className="mt-6 flex rounded-md border border-line p-0.5">
        {(["espn", "manual"] as const).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={cn(
              "flex-1 rounded px-3 py-1.5 text-sm transition-colors",
              mode === m ? "bg-raised font-medium text-chalk" : "text-muted hover:text-chalk"
            )}
          >
            {m === "espn" ? "Connect ESPN league" : "Set up manually"}
          </button>
        ))}
      </div>

      <div className="mt-5 space-y-4">
        {mode === "espn" ? (
          <>
            <Field label="League ID"
                   hint="From your league URL: .../leagues/THIS_NUMBER">
              <Input value={leagueId} onChange={(e) => setLeagueId(e.target.value)}
                     placeholder="1234567" inputMode="numeric" />
            </Field>
            <Field label="Season">
              <Input type="number" value={season}
                     onChange={(e) => setSeason(+e.target.value)} />
            </Field>
            <details className="rounded-md border border-line px-3 py-2">
              <summary className="cursor-pointer text-xs text-muted">
                Private league? Add your cookies
              </summary>
              <div className="mt-3 space-y-3">
                <p className="text-[11px] leading-relaxed text-muted">
                  In a browser logged into ESPN, open DevTools → Application →
                  Cookies → espn.com, and copy <code className="text-chalk">espn_s2</code> and{" "}
                  <code className="text-chalk">SWID</code>. They stay on this machine and are
                  only sent to ESPN. You can also put them in <code className="text-chalk">.env</code>{" "}
                  as ESPN_S2 and SWID.
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
            <p className="text-[11px] text-muted">
              Team count, lineup and PPR are read from the league itself. Picks
              then sync on their own while you draft.
            </p>
          </>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Teams">
                <Input type="number" min={2} max={20} value={teams}
                       onChange={(e) => setTeams(+e.target.value)} />
              </Field>
              <Field label="Your draft slot">
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
                    <Input
                      type="number" min={0} max={4} value={lineup[pos]}
                      onChange={(e) =>
                        setLineup({ ...lineup, [pos]: +e.target.value })}
                    />
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
          <p className="rounded-md border border-alarm/30 bg-alarm/10 px-3 py-2 text-xs text-alarm">
            {err}
          </p>
        )}

        <Button size="lg" className="w-full" onClick={go}
                disabled={busy || (mode === "espn" && !leagueId.trim())}>
          {busy ? "Loading the board…" : "Start drafting"}
        </Button>
      </div>
    </div>
  );
}
