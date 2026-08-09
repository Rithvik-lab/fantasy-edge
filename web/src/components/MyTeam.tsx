import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { api, type TeamReport } from "@/lib/api";
import { POS_HUE } from "@/components/Charts";
import { Term } from "@/components/Explain";
import { PlayerHover } from "@/components/PlayerHover";
import { cn } from "@/lib/utils";

/**
 * Your team, measured against the league it plays in.
 *
 * A roster grade is one number and one number cannot be acted on. "You are a
 * 112" says nothing about whether to stream a quarterback or chase a tight
 * end. What can be acted on is comparative and per position: against the other
 * eleven teams, where are you strong and where are you about to lose a week
 * you should have won.
 */

/**
 * THE CHART THAT ANSWERS THE QUESTION.
 *
 * Above or below the league's average starter, per position, on a shared
 * scale with a zero line down the middle. Deliberately diverging rather than
 * ranked bars: the question is POLARITY — am I ahead or behind — and a
 * diverging form answers it before you have read a single label.
 *
 * turf and alarm are the right colours here precisely because this is a
 * meaning question rather than an identity one. Everywhere else in the app
 * they are reserved from position hues for exactly this reason.
 */
function Edges({ rows }: { rows: TeamReport["strength"] }) {
  if (!rows.length) return null;
  const max = Math.max(...rows.map((r) => Math.abs(r.edge)), 40);

  return (
    <section className="rounded-lg border border-line bg-panel p-3">
      <header className="mb-3">
        <span className="eyebrow">Against the average team in this league</span>
        <p className="mt-0.5 text-[11px] leading-snug text-muted">
          Points your starters beat — or trail — a median starting lineup by, at
          each position. This is the number worth trading on.
        </p>
      </header>

      <div className="space-y-2.5">
        {rows.map((r, i) => {
          const w = (Math.abs(r.edge) / max) * 50;
          const good = r.edge >= 0;
          return (
            <div key={r.position} className="flex items-center gap-2">
              <span className="w-8 shrink-0 text-[11px] font-bold"
                    style={{ color: POS_HUE[r.position] ?? "#8CA096" }}>
                {r.position}
              </span>

              <div className="relative h-5 flex-1">
                {/* Zero line: the league's average starting lineup. */}
                <div className="absolute inset-y-0 left-1/2 w-px bg-line" />
                <motion.div
                  className={cn("absolute top-1/2 h-3.5 -translate-y-1/2 rounded-[2px]",
                    good ? "bg-turf" : "bg-alarm")}
                  style={good ? { left: "50%" } : { right: "50%" }}
                  initial={{ width: 0 }}
                  animate={{ width: `${w}%` }}
                  transition={{ type: "spring", stiffness: 180, damping: 26,
                                delay: i * 0.05 }}
                />
              </div>

              <span className={cn("num w-12 shrink-0 text-right text-[11.5px] font-semibold",
                good ? "text-turf" : "text-alarm")}>
                {good ? "+" : ""}{Math.round(r.edge)}
              </span>
              <span className="num w-10 shrink-0 text-right text-[10px] text-muted">
                {Math.round(r.percentile * 100)}%
              </span>
            </div>
          );
        })}
      </div>

      <p className="mt-2.5 border-t border-line pt-2 text-[10px] text-muted">
        Right of the line is ahead of the league. The percentage is where your
        average starter sits inside the pool of players who actually start at
        that position.
      </p>
    </section>
  );
}

/** Pick-by-pick against ADP. Below the line is a name that fell to you. */
function DraftReport({ d }: { d: TeamReport["draft"] }) {
  if (!d?.picks?.length) return null;
  const max = Math.max(...d.picks.map((p) => Math.abs(p.edge)), 20);

  return (
    <section className="rounded-lg border border-line bg-panel p-3">
      <header className="mb-2 flex flex-wrap items-baseline gap-x-3">
        <span className="eyebrow">Draft report</span>
        <span className="num text-[11px] text-muted">
          {d.steals} steals &middot; {d.reaches} reaches &middot;{" "}
          <span className={d.total_edge >= 0 ? "text-turf" : "text-alarm"}>
            {d.total_edge > 0 ? "+" : ""}{Math.round(d.total_edge)} picks of value
          </span>
        </span>
      </header>
      <p className="mb-2.5 text-[11px] leading-snug text-muted">
        Where the room had each man against where you took him. It measures
        value only, not fit — a steal at a position you were already deep at is
        still a steal.
      </p>

      <ul className="space-y-1">
        {d.picks.map((p) => {
          const w = (Math.abs(p.edge) / max) * 46;
          const good = p.edge >= 0;
          return (
            <li key={p.overall} className="flex items-center gap-2">
              <span className="num w-8 shrink-0 text-[10px] text-muted">
                #{p.overall}
              </span>
              <span className="w-[104px] shrink-0 truncate text-[11px]">
                {p.player_name}
              </span>
              <span className="w-6 shrink-0 text-[9.5px] font-bold"
                    style={{ color: POS_HUE[p.position ?? ""] ?? "#8CA096" }}>
                {p.position}
              </span>
              <div className="relative h-3 flex-1">
                <div className="absolute inset-y-0 left-1/2 w-px bg-line" />
                <div
                  className={cn("absolute top-1/2 h-2 -translate-y-1/2 rounded-[2px]",
                    good ? "bg-turf/75" : "bg-alarm/75")}
                  style={good ? { left: "50%", width: `${w}%` }
                              : { right: "50%", width: `${w}%` }}
                />
              </div>
              <span className={cn("num w-9 shrink-0 text-right text-[10px]",
                good ? "text-turf/85" : "text-alarm/85")}>
                {good ? "+" : ""}{Math.round(p.edge)}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Lineup({ d }: { d: TeamReport }) {
  const Row = ({ p, slot }: { p: TeamReport["starters"][0]; slot?: string }) => (
    <li className="flex items-center gap-2 border-b border-line/40 px-2.5 py-1.5 last:border-0">
      <span className="num w-9 shrink-0 text-[9.5px] uppercase tracking-wider text-muted">
        {slot ?? "BE"}
      </span>
      {p.headshot ? (
        <PlayerHover playerId={p.player_id} className="shrink-0">
          <img src={p.headshot} alt="" loading="lazy"
               className="h-7 w-7 shrink-0 cursor-help rounded bg-raised object-cover object-top" />
        </PlayerHover>
      ) : <span className="h-7 w-7 shrink-0 rounded bg-raised" />}
      <PlayerHover playerId={p.player_id} className="min-w-0 flex-1">
        <span className="block cursor-help truncate text-[11.5px]">{p.player_name}</span>
      </PlayerHover>
      <span className="w-6 shrink-0 text-[10px] font-bold"
            style={{ color: POS_HUE[p.position] ?? "#8CA096" }}>
        {p.position}
      </span>
      <span className="num w-9 shrink-0 text-right text-[10.5px] text-muted">
        {Math.round(p.projected_points ?? 0)}
      </span>
    </li>
  );

  return (
    <section className="rounded-lg border border-line bg-panel">
      <header className="flex items-baseline gap-2 border-b border-line px-2.5 py-1.5">
        <span className="eyebrow">Your lineup</span>
        {d.grade?.score != null && (
          <Term k="grade">
            <span className={cn("num ml-auto text-[11px]",
              d.grade.score >= 100 ? "text-turf" : "text-muted")}>
              grade {d.grade.score}
            </span>
          </Term>
        )}
      </header>
      <ul>{d.starters.map((p) => <Row key={p.player_id} p={p} slot={p.slot} />)}</ul>
      {d.bench.length > 0 && (
        <>
          <div className="border-y border-line bg-raised/40 px-2.5 py-1">
            <span className="eyebrow">Bench</span>
          </div>
          <ul>{d.bench.map((p) => <Row key={p.player_id} p={p} />)}</ul>
        </>
      )}
    </section>
  );
}

export function MyTeam() {
  const [d, setD] = useState<TeamReport | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.teamReport().then(setD)
       .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);

  if (err) {
    return <p className="rounded-lg border border-line bg-panel p-6 text-center
                         text-[11.5px] text-muted">{err}</p>;
  }
  if (!d) {
    return <p className="rounded-lg border border-line bg-panel p-6 text-center
                         text-[11.5px] text-muted">reading your roster…</p>;
  }
  if (d.empty) {
    return (
      <div className="rounded-lg border border-dashed border-line p-10 text-center">
        <p className="text-sm text-muted">{d.note}</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <section className="rounded-md border border-turf/25 p-3">
          <div className="mb-2 flex items-baseline gap-2">
            <span className="h-px w-3 bg-turf" />
            <span className="eyebrow">what this team does well</span>
          </div>
          <ul className="space-y-1.5">
            {d.strengths.map((t, i) => (
              <li key={i} className="text-[11.5px] leading-snug text-chalk/80">{t}</li>
            ))}
          </ul>
        </section>
        <section className="rounded-md border border-alarm/25 p-3">
          <div className="mb-2 flex items-baseline gap-2">
            <span className="h-px w-3 bg-alarm" />
            <span className="eyebrow">where it will lose weeks</span>
          </div>
          <ul className="space-y-1.5">
            {d.weaknesses.map((t, i) => (
              <li key={i} className="text-[11.5px] leading-snug text-chalk/80">{t}</li>
            ))}
          </ul>
        </section>
      </div>

      <Edges rows={d.strength} />

      {d.byes.length > 0 && (
        <section className="rounded-lg border border-clock/25 bg-clock/[0.05] p-3">
          <span className="eyebrow text-clock/80">bye weeks that hurt</span>
          <ul className="mt-1.5 space-y-1">
            {d.byes.map((b) => (
              <li key={b.week} className="text-[11.5px] text-chalk/80">
                <span className="num text-clock">week {b.week}</span> — {b.count}{" "}
                starters out ({b.players.join(", ")}), about{" "}
                <span className="num">{Math.round(b.points / 17)}</span> points of
                your lineup.
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Lineup d={d} />
        <DraftReport d={d.draft} />
      </div>
    </div>
  );
}
