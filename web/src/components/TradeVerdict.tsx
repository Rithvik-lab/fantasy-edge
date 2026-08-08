import { motion } from "motion/react";
import type { TradeVerdict as Verdict } from "@/lib/api";
import { POS_HUE } from "@/components/Charts";
import { Term } from "@/components/Explain";
import { PlayerHover } from "@/components/PlayerHover";
import { TradeChart } from "@/components/TradeChart";
import { cn } from "@/lib/utils";

/**
 * The answer, arranged so the argument is visible rather than asserted.
 *
 * The headline is what the deal does to the lineup you can actually field.
 * Directly under it sits what a value calculator would have told you, because
 * the DIFFERENCE between those two numbers is the whole point: it is the
 * roster spots, priced. "Three for one is not fair" stops being an opinion the
 * moment you can see 93 points sitting between the two scales.
 *
 * The bars are drawn to one shared scale so before and after are comparable by
 * eye. A range that shifts right is a better team; a range that also narrows is
 * a safer one, and those are different reasons to say yes.
 */
function Side({ label, players, tone }: {
  label: string; players: Verdict["give"]; tone: "alarm" | "turf";
}) {
  return (
    <div className="min-w-0 flex-1">
      <div className={cn("eyebrow mb-1.5",
        tone === "alarm" ? "text-alarm/80" : "text-turf/80")}>{label}</div>
      {players.length === 0 ? (
        <p className="text-[11px] text-muted">nobody</p>
      ) : (
        <ul className="space-y-1">
          {players.map((p) => (
            <li key={p.player_id} className="flex items-center gap-1.5">
              <PlayerHover playerId={p.player_id} className="shrink-0">
                <img
                  src={p.headshot ?? ""}
                  alt=""
                  loading="lazy"
                  onError={(e) => { e.currentTarget.style.visibility = "hidden"; }}
                  className="h-7 w-7 shrink-0 cursor-help rounded bg-raised object-cover object-top ring-1 ring-line"
                />
              </PlayerHover>
              <span className="w-6 shrink-0 text-[10px] font-bold"
                    style={{ color: POS_HUE[p.position] ?? "#8CA096" }}>
                {p.position}
              </span>
              <PlayerHover playerId={p.player_id} className="min-w-0 flex-1">
                <span className="block cursor-help truncate text-xs">{p.player_name}</span>
              </PlayerHover>
              <span className="num shrink-0 text-[10.5px] text-muted">
                {Math.round(p.projected_points ?? 0)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function TradeVerdict({ v }: { v: Verdict }) {
  const good = v.delta_median > 0;
  // Three calls, not a number. "Take it or not" was the ask, and a middling
  // deal is not a third verdict -- it is an instruction to counter.
  // Win / fair / loss, decided server-side so the words and the numbers can
  // never disagree. It is a STATEMENT of the result, deliberately not styled
  // as a control -- the old chip looked like a button, so it got clicked.
  const verdict = v.call ?? (
    v.delta_median >= 15 && v.win_probability >= 0.58 ? "win"
      : v.delta_median <= -15 || v.win_probability < 0.42 ? "loss" : "fair");

  // Where the two scales disagree. Positive means value totals flatter the
  // deal — the classic three-for-one that ignores your bench.
  const gap = v.opportunity_gap;

  return (
    <motion.section
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="space-y-3 rounded-lg border border-line bg-panel p-4"
    >
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className={cn("num text-3xl font-bold leading-none",
          good ? "text-turf" : "text-alarm")}>
          {good ? "+" : ""}{Math.round(v.delta_median)}
        </span>
        <div className="min-w-0">
          <Term k="range">
            <span className="text-xs text-chalk">
              {v.roster_priced ? "points to your starting lineup"
                               : "points, comparing the two sides only"}
            </span>
          </Term>
          <p className="text-[11px] text-muted">
            better in {Math.round(v.win_probability * 100)}% of simulated seasons
          </p>
        </div>
        <span
          aria-live="polite"
          className={cn(
            "ml-auto select-none border-l-4 py-0.5 pl-2.5 text-sm font-bold uppercase tracking-[0.14em]",
            verdict === "win" ? "border-turf text-turf"
              : verdict === "loss" ? "border-alarm text-alarm"
              : "border-clock text-clock")}
        >
          {verdict === "win" ? "you win" : verdict === "loss" ? "you lose" : "fair"}
        </span>
      </div>

      {v.summary && (
        <p className="text-[12px] leading-snug text-chalk/85">{v.summary}</p>
      )}

      <TradeChart v={v} />

      {(v.pros?.length || v.cons?.length) && (
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="rounded-md border border-turf/25 bg-turf/[0.06] p-2.5">
            <span className="eyebrow text-turf/80">what you gain</span>
            <ul className="mt-1.5 space-y-1.5">
              {(v.pros ?? []).map((t, i) => (
                <li key={i} className="flex gap-1.5 text-[11.5px] leading-snug text-chalk/85">
                  <span className="text-turf">+</span>{t}
                </li>
              ))}
            </ul>
          </div>
          <div className="rounded-md border border-alarm/25 bg-alarm/[0.06] p-2.5">
            <span className="eyebrow text-alarm/80">what it costs</span>
            <ul className="mt-1.5 space-y-1.5">
              {(v.cons ?? []).map((t, i) => (
                <li key={i} className="flex gap-1.5 text-[11.5px] leading-snug text-chalk/85">
                  <span className="text-alarm">&minus;</span>{t}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <div className="flex gap-4 border-y border-line py-3">
        <Side label="you give" players={v.give} tone="alarm" />
        <div className="self-center text-muted">&rarr;</div>
        <Side label="you get" players={v.get} tone="turf" />
      </div>

      {/* The argument. Two scales, and the space between them. */}
      <div className="rounded-md border border-line bg-raised/60 p-2.5">
        <div className="grid grid-cols-2 gap-3 text-[11px]">
          <div>
            <span className="block text-muted">On value totals</span>
            <span className="num text-sm text-muted">
              {v.naive_value_delta > 0 ? "+" : ""}{Math.round(v.naive_value_delta)}
            </span>
          </div>
          <div>
            <Term k="vor"><span className="block text-muted">On your lineup</span></Term>
            <span className={cn("num text-sm font-semibold",
              good ? "text-turf" : "text-alarm")}>
              {good ? "+" : ""}{Math.round(v.delta_median)}
            </span>
          </div>
        </div>
        {v.roster_priced && Math.abs(gap) >= 5 && (
          <p className="mt-2 border-t border-line pt-2 text-[11px] leading-snug text-muted">
            <span className="font-semibold text-chalk">
              {Math.abs(Math.round(gap))} points
            </span>{" "}
            {gap > 0
              ? "of that is roster spots. A value calculator counts everyone you receive at full price; your lineup only starts so many."
              : "in your favour that value totals miss — the players you give up were not starting for you anyway."}
          </p>
        )}
      </div>

      {v.note && (
        <p className="rounded-md border border-clock/30 bg-clock/10 px-2.5 py-2 text-[11px] leading-snug text-clock">
          {v.note}
        </p>
      )}

      <p className="text-[10.5px] text-muted">
        roster {v.roster_before} &rarr; {v.roster_after} &middot; floor{" "}
        {v.delta_floor > 0 ? "+" : ""}{Math.round(v.delta_floor)} &middot; ceiling{" "}
        {v.delta_ceiling > 0 ? "+" : ""}{Math.round(v.delta_ceiling)}
      </p>
    </motion.section>
  );
}
