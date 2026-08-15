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

  const tone = verdict === "win" ? "turf" : verdict === "loss" ? "alarm" : "clock";

  return (
    <motion.section
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="overflow-hidden rounded-lg border border-line bg-panel"
    >
      {/* THE RULING, ACROSS THE TOP. This screen used to open with the same
          panels as the one you build a trade on, so arriving at the answer
          looked like not having moved. It opens on the two sides facing each
          other under a band in the colour of the call — a page you can tell
          apart from a glance at its top edge. */}
      <div className={cn("border-b px-4 py-3",
        tone === "turf" ? "border-turf/30 bg-turf/[0.07]"
          : tone === "alarm" ? "border-alarm/30 bg-alarm/[0.07]"
          : "border-clock/30 bg-clock/[0.06]")}>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span aria-live="polite"
                className={cn("select-none text-sm font-bold uppercase tracking-[0.14em]",
                  tone === "turf" ? "text-turf"
                    : tone === "alarm" ? "text-alarm" : "text-clock")}>
            {verdict === "win" ? "you win this"
              : verdict === "loss" ? "you lose this" : "about fair"}
          </span>
          <span className={cn("num text-3xl font-bold leading-none",
            good ? "text-turf" : "text-alarm")}>
            {good ? "+" : ""}{Math.round(v.delta_median)}
          </span>
          <Term k="range">
            <span className="text-[11px] text-chalk/80">
              {v.roster_priced ? "to your starting lineup"
                               : "comparing the two sides only"}
            </span>
          </Term>
          {/* The same move said three ways. A season total is hard to feel;
              a percentage and a weekly figure are not, and both are plain
              division rather than a score. */}
          <p className="num ml-auto text-[11px] text-muted">
            {v.pct_change != null && (
              <>{v.pct_change > 0 ? "+" : ""}{v.pct_change.toFixed(1)}% of your season</>
            )}
            {v.per_week != null && (
              <> &middot; {v.per_week > 0 ? "+" : ""}{v.per_week.toFixed(1)} a week</>
            )}
          </p>
        </div>

        <div className="mt-3 flex gap-4">
          <Side label="you give" players={v.give} tone="alarm" />
          <div className="self-center text-lg text-muted">&rarr;</div>
          <Side label="you get" players={v.get} tone="turf" />
        </div>
      </div>

      <div className="space-y-3 p-4">
      {v.summary && (
        <p className="text-[12px] leading-snug text-chalk/85">{v.summary}</p>
      )}

      <TradeChart v={v} />

      <Cascade moves={v.moves ?? []} />
      <Roles rows={v.roles ?? []} />

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
      </div>
    </motion.section>
  );
}

/**
 * WHO YOU ARE ACTUALLY BUYING.
 *
 * "He shares a backfield — is he still the right target?" is the question
 * behind every trade for a running back, and the answer is nearly always that
 * the market already knew. George Pickens is a WR2, everybody knows he is a
 * WR2, and it is exactly why he goes where he goes in drafts; his price has
 * carried the committee since July.
 *
 * So this reports the fact and says whether it is NEWS: the chart against what
 * the board already assumed. Matching ranks mean the committee is in the
 * price. A chart worse than the price is a demotion nobody has paid for yet,
 * and only that one is a reason to hesitate.
 */
function Roles({ rows }: { rows: NonNullable<Verdict["roles"]> }) {
  if (!rows.length) return null;
  return (
    <div className="rounded-md border border-line bg-raised/40 p-2.5">
      <div className="mb-1.5 flex items-baseline gap-2">
        <Term k="role">
          <span className="eyebrow">who you are buying</span>
        </Term>
        <span className="text-[10px] text-muted">the job, not the projection</span>
      </div>
      <ul className="space-y-1.5">
        {rows.map((r) => {
          const news = r.discount < 1;
          return (
            <li key={r.player_id} className="text-[11px] leading-snug">
              <span className="font-medium">{r.player_name}</span>
              <span className="text-muted">
                {" — "}listed {r.position}
                {r.depth_rank} on {r.team}
                {r.ahead.length > 0 && <> behind {r.ahead.join(" and ")}</>}
                {r.injury_status && r.injury_status !== "ACTIVE" && (
                  <span className="text-alarm"> · {r.injury_status.toLowerCase()}</span>
                )}
              </span>
              <div className={cn("mt-0.5", news ? "text-clock" : "text-muted")}>
                {news
                  ? `The board prices him as his team's ${r.position}${r.expected_rank ?? 1}, so this is a demotion it has not paid for — his projection is cut ${Math.round((1 - r.discount) * 100)}% here.`
                  : `That is exactly what his price already assumes, so the committee is not new information.`}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * YOUR LINEUP CARD, BEFORE AND AFTER.
 *
 * This replaced four separate reasons that each said "your WR2 slot gets
 * better". Every one was true and together they read like a bug — one receiver
 * arrived, so how did three slots improve? Because slots are ranks and not
 * people: the new man takes WR1, the old WR1 slides to WR2, that man slides to
 * the flex, and the back who left empties RB2. One cascade, shown as one.
 */
function Cascade({ moves }: { moves: NonNullable<Verdict["moves"]> }) {
  if (!moves.length) return null;
  return (
    <div className="rounded-md border border-line bg-raised/40 p-2.5">
      <div className="mb-1.5 flex items-baseline gap-2">
        <Term k="cascade">
          <span className="eyebrow">how your lineup rearranges</span>
        </Term>
        <span className="text-[10px] text-muted">
          {moves.length} slot{moves.length === 1 ? "" : "s"} move
        </span>
      </div>
      <ul className="space-y-0.5">
        {moves.map((m, i) => (
          <li key={i} className="flex items-center gap-2 text-[11px]">
            <span className="num w-10 shrink-0 text-[9.5px] uppercase tracking-wider text-muted">
              {m.slot}
            </span>
            <span className="min-w-0 flex-1 truncate text-muted line-through decoration-muted/50">
              {m.out ?? "—"}
            </span>
            <span className="shrink-0 text-muted">&rarr;</span>
            <span className={cn("min-w-0 flex-1 truncate",
              m.in ? "text-chalk" : "text-alarm/80")}>
              {m.in ?? "nobody"}
              {m.waiver && (
                <Term k="waiver">
                  <span className="ml-1 text-[9px] uppercase tracking-wider text-clock">
                    waivers
                  </span>
                </Term>
              )}
            </span>
            <span className={cn("num w-11 shrink-0 text-right text-[10.5px]",
              m.delta >= 0 ? "text-turf" : "text-alarm")}>
              {m.delta > 0 ? "+" : ""}{Math.round(m.delta)}
            </span>
          </li>
        ))}
      </ul>
    </div>
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
