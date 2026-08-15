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

function Ledger({ label, tone, items }: {
  label: string;
  tone: "turf" | "alarm";
  items: { stat: string | null; text: string }[];
}) {
  if (!items.length) return null;
  return (
    <section>
      <div className="mb-2 flex items-baseline gap-2">
        <span className={cn("h-px w-3", tone === "turf" ? "bg-turf" : "bg-alarm")} />
        <span className="eyebrow">{label}</span>
      </div>
      <ul className="space-y-2">
        {items.map((r, i) => (
          <li key={i} className="flex gap-2.5">
            <span className={cn(
              "num w-[62px] shrink-0 text-right text-[12px] font-semibold tabular-nums",
              tone === "turf" ? "text-turf" : "text-alarm")}>
              {r.stat ?? "—"}
            </span>
            <span className="flex-1 text-[11.5px] leading-snug text-chalk/75">
              {r.text}
            </span>
          </li>
        ))}
      </ul>
    </section>
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
          {/* The same move said three ways. A season total is hard to feel;
              a percentage and a weekly figure are not, and both are plain
              division rather than a score. */}
          <p className="num text-[11px] text-muted">
            {v.pct_change != null && (
              <>{v.pct_change > 0 ? "+" : ""}{v.pct_change.toFixed(1)}% of your season</>
            )}
            {v.per_week != null && (
              <> &middot; {v.per_week > 0 ? "+" : ""}{v.per_week.toFixed(1)} a week</>
            )}
            <> &middot; better in {Math.round(v.win_probability * 100)}% of seasons</>
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
        /* Numbers in their own column, in tabular figures, so the eye can run
           down them. Buried mid-sentence, six reasons read as six paragraphs
           and nobody reads the sixth. No tinted panels either — the sign is
           carried by one small mark and the figure, which is all it needs. */
        <div className="grid gap-x-6 gap-y-4 border-y border-line py-3 sm:grid-cols-2">
          <Ledger label="what you gain" tone="turf" items={v.pros ?? []} />
          <Ledger label="what it costs" tone="alarm" items={v.cons ?? []} />
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

      <Shape v={v} />
    </motion.section>
  );
}

/**
 * THE SHAPE OF THE MOVE, which one number cannot carry.
 *
 * Floor, middle and ceiling on a shared scale, so a deal that is level in the
 * middle and enormous in the tails looks like what it is at a glance. That
 * case is not exotic — it is every unproven player for a proven one, where the
 * measured difference is ENTIRELY in the left tail and a median-only verdict
 * reports a coin flip.
 *
 * Diverging, and turf/alarm mean better/worse here rather than identity: this
 * is a polarity question, which is the one place those colours are allowed to
 * carry meaning.
 */
function Shape({ v }: { v: Verdict }) {
  const rows = [
    { k: "floor_delta", label: "bad season", value: v.delta_floor },
    { k: "range", label: "middle", value: v.delta_median },
    { k: "ceiling_delta", label: "good season", value: v.delta_ceiling },
  ];
  const max = Math.max(...rows.map((r) => Math.abs(r.value)), 12);

  return (
    <div className="border-t border-line pt-2.5">
      <div className="mb-1.5 flex items-baseline gap-2">
        <span className="eyebrow">the shape of it</span>
        <span className="text-[10px] text-muted">
          how the whole season moves, not just the middle of it
        </span>
        <Term k="roster_spots" className="ml-auto">
          <span className="num text-[10px] text-muted">
            roster {v.roster_before} &rarr; {v.roster_after}
          </span>
        </Term>
      </div>

      <div className="space-y-1">
        {rows.map((r) => {
          const w = (Math.abs(r.value) / max) * 50;
          const good = r.value >= 0;
          return (
            <div key={r.label} className="flex items-center gap-2">
              <Term k={r.k} className="w-[74px] shrink-0">
                <span className="text-[10px] text-muted">{r.label}</span>
              </Term>
              <div className="relative h-3 flex-1">
                <div className="absolute inset-y-0 left-1/2 w-px bg-line" />
                <motion.div
                  className={cn("absolute top-1/2 h-2 -translate-y-1/2 rounded-[2px]",
                    good ? "bg-turf/80" : "bg-alarm/80")}
                  style={good ? { left: "50%" } : { right: "50%" }}
                  initial={{ width: 0 }}
                  animate={{ width: `${w}%` }}
                  transition={{ type: "spring", stiffness: 200, damping: 26 }}
                />
              </div>
              <span className={cn("num w-10 shrink-0 text-right text-[11px]",
                good ? "text-turf" : "text-alarm")}>
                {good ? "+" : ""}{Math.round(r.value)}
              </span>
            </div>
          );
        })}
      </div>

      <p className="mt-1.5 flex flex-wrap gap-x-3 text-[10px] text-muted">
        {v.delta_mean != null && (
          <Term k="mean_delta">
            <span className="num">
              average season {v.delta_mean > 0 ? "+" : ""}
              {Math.round(v.delta_mean)}
            </span>
          </Term>
        )}
        <Term k="win_prob">
          <span className="num">
            better in {Math.round(v.win_probability * 100)}% of seasons
          </span>
        </Term>
        {v.overlap?.overlap != null && (
          <Term k="overlap">
            <span className="num">
              {Math.round(v.overlap.overlap * 100)}% overlap
            </span>
          </Term>
        )}
      </p>
    </div>
  );
}
