import { useCallback, useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import { api, type Claim, type TeamReport, type Wire } from "@/lib/api";
import { POS_HUE } from "@/components/Charts";
import { PlayerHover } from "@/components/PlayerHover";
import { Term } from "@/components/Explain";
import { Floating, useHover } from "@/components/Floating";
import { Simulating } from "@/components/Simulating";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * The waiver wire, priced against your roster.
 *
 * THE SHAPE OF THIS SCREEN IS THE DRAFT BOARD'S, ON PURPOSE
 *
 * A waiver period is a draft with one round and no order you control. The man
 * you want can be claimed before your priority comes up, so a single
 * recommendation is useless the moment it is wrong — which is most weeks. Every
 * position therefore carries its next few, in the order you should want them,
 * each priced on its own. "He got claimed" is answered before it is asked.
 *
 * Your team is on the left because every number on this screen is about it. A
 * ranking of the wire that does not have your roster in it is the same list for
 * all twelve managers, which is the tell that it is about nobody's team.
 *
 * And it re-pulls. This is the one screen meant to be left open — an injury on
 * Thursday changes every answer on it, and a stale wire is worse than no wire
 * because it looks current.
 */

// His lineup order, which is not the engine's. Defence before kicker.
const DECK = ["QB", "RB", "WR", "TE", "DST", "K"] as const;

const QUEUE_LABEL = [
  "First choice",
  "If he is claimed",
  "If both are gone",
  "Fourth in line",
];

// Slow enough not to hammer ESPN, often enough that a Thursday injury lands
// before you have made a decision on Wednesday's list.
const POLL_MS = 90_000;

function hue(pos: string) {
  return POS_HUE[pos] ?? "#8CA096";
}

/** A player's face, or a coloured initial where there is no headshot. */
function Face({ c, size }: { c: Claim; size: number }) {
  return c.headshot ? (
    <img src={c.headshot} alt="" loading="lazy"
         style={{ height: size, width: size }}
         className="shrink-0 rounded-md bg-raised object-cover object-top ring-1 ring-line" />
  ) : (
    <span style={{ height: size, width: size, color: hue(c.position) }}
          className="flex shrink-0 items-center justify-center rounded-md bg-raised text-[11px] font-bold ring-1 ring-line">
      {c.position}
    </span>
  );
}

/**
 * One man in the queue.
 *
 * The rank label carries the whole idea: this is not "the best free agent" and
 * then three also-rans, it is what to do first and what to do when that fails.
 */
function WireCard({ c, i, active, onPick }: {
  c: Claim; i: number; active: boolean; onPick: () => void;
}) {
  const weekly = c.adds / 17;
  const worth = c.worth ?? c.adds;
  return (
    <motion.button
      layout
      onClick={onPick}
      className={cn(
        "flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors",
        active
          ? "border-turf/60 bg-turf/10"
          : "border-line bg-panel hover:border-turf/30 hover:bg-raised/50"
      )}
    >
      <span className="num w-6 shrink-0 text-center text-[15px] font-bold leading-none"
            style={{ color: i === 0 ? "var(--color-turf)" : undefined }}>
        {i + 1}
      </span>
      <PlayerHover playerId={c.player_id} className="shrink-0">
        <Face c={c} size={38} />
      </PlayerHover>
      <div className="min-w-0 flex-1">
        <PlayerHover playerId={c.player_id}>
          <span className="block cursor-help truncate text-[13px] font-medium leading-tight">
            {c.player_name}
          </span>
        </PlayerHover>
        <span className="block text-[10px] uppercase tracking-wider text-muted">
          {c.team ? <span className="num mr-1.5 text-chalk">{c.team}</span> : null}
          {QUEUE_LABEL[i] ?? `Number ${i + 1}`}
        </span>
      </div>
      <div className="shrink-0 text-right">
        <span className={cn("num block text-[14px] font-bold leading-none",
                            worth > 0 ? "text-turf" : "text-muted")}>
          {worth > 0 ? "+" : ""}{worth.toFixed(1)}
        </span>
        <span className="num text-[9.5px] text-muted">
          {c.upside ? `depth ${c.adds.toFixed(1)} · ceiling ${c.upside.toFixed(0)}`
                    : weekly >= 0.05 ? `+${weekly.toFixed(1)}/wk` : "no change"}
        </span>
      </div>
    </motion.button>
  );
}

/** Floor to ceiling as a bar, because a range asserted as a point is a lie. */
function Range({ c }: { c: Claim }) {
  if (c.floor == null || c.ceiling == null) return null;
  const lo = c.floor, hi = c.ceiling, mid = c.projected_points;
  const span = Math.max(hi - lo, 1);
  const at = Math.min(Math.max((mid - lo) / span, 0), 1) * 100;
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between">
        <span className="eyebrow">Season range</span>
        <span className="num text-[10px] text-muted">
          {Math.round(lo)} – {Math.round(hi)}
        </span>
      </div>
      <div className="relative h-2 rounded-full bg-raised">
        <div className="absolute inset-y-0 rounded-full opacity-45"
             style={{ left: 0, right: 0, background: hue(c.position) }} />
        {/* The projection sits inside the band rather than replacing it. */}
        <span className="absolute top-1/2 h-3.5 w-[3px] -translate-y-1/2 rounded-full bg-chalk"
              style={{ left: `calc(${at}% - 1.5px)` }} />
      </div>
      <div className="flex justify-between text-[9.5px] text-muted">
        <span>bad season</span>
        <span className="num text-chalk">{Math.round(mid)} projected</span>
        <span>good one</span>
      </div>
    </div>
  );
}

/**
 * Why he is worth a claim.
 *
 * Every line carries the figure that produced it, computed by the engine and
 * passed through untouched. A waiver panel that says "great upside" is
 * describing its own enthusiasm.
 */
function TheCase({ c, full, onRecord, busy }: {
  c: Claim; full: boolean; onRecord: (c: Claim) => void; busy: boolean;
}) {
  // A CSS keyframe rather than motion's initial/animate, because this panel is
  // the whole point of the screen and CONTENT MUST NOT DEPEND ON AN ANIMATION
  // HAVING RUN. `initial={{opacity: 0}}` leaves the element at zero until a
  // frame fires; the resting state of `.tick-in` is visible, so the worst case
  // is an abrupt appearance rather than an empty box.
  return (
    <div key={c.player_id} className="tick-in space-y-3">
      <div className="flex items-center gap-3">
        <PlayerHover playerId={c.player_id} className="shrink-0">
          <Face c={c} size={46} />
        </PlayerHover>
        <div className="min-w-0 flex-1">
          <span className="block truncate text-[14px] font-semibold leading-tight">
            {c.player_name}
          </span>
          <span className="text-[10px] font-bold" style={{ color: hue(c.position) }}>
            {c.position}
            {c.team ? <span className="num ml-1.5 font-normal text-chalk">
              {c.team}
            </span> : null}
            {c.ecr ? <span className="ml-1.5 font-normal text-muted">
              drafted #{Math.round(c.ecr)}
            </span> : null}
          </span>
        </div>
      </div>

      <div className="rounded-md border border-line bg-raised/40 px-3 py-2">
        <Term k="wire_adds">
          <span className="eyebrow">Adds to your season</span>
        </Term>
        <div className="mt-0.5 flex items-baseline gap-2">
          <span className={cn("num text-2xl font-bold leading-none",
                              (c.worth ?? c.adds) > 0 ? "text-turf" : "text-muted")}>
            {(c.worth ?? c.adds) > 0 ? "+" : ""}{(c.worth ?? c.adds).toFixed(1)}
          </span>
          {/* The two halves, always visible. One total nobody can take apart
              is a number you either believe or do not. */}
          <span className="num text-[10.5px] text-muted">
            {c.upside
              ? `${c.adds.toFixed(1)} as depth + a ${c.upside.toFixed(0)}-point ceiling one year in five`
              : `${(c.adds / 17).toFixed(1)} a week`}
          </span>
        </div>
      </div>

      <Range c={c} />

      <ul className="space-y-2">
        {(c.why ?? []).map((w, i) => {
          // The glossary term is NAMED BY THE ENGINE, not guessed from the
          // text of the figure. Sniffing the string for "wire" or a leading
          // minus is the same class of mistake that had a negative gap
          // rendering as "+−33": a number is not a label.
          const chip = (
            <span className="num w-[3.9rem] shrink-0 whitespace-nowrap rounded bg-raised px-1 py-0.5 text-center text-[10px] font-semibold text-chalk">
              {w.stat}
            </span>
          );
          return (
            <li key={i} className="flex gap-2">
              {w.k ? <Term k={w.k}>{chip}</Term> : chip}
              <span className="text-[11px] leading-relaxed text-muted">{w.text}</span>
            </li>
          );
        })}
      </ul>

      {c.drop_name ? (
        <div className={cn(
          "rounded-md border px-3 py-2",
          c.drop_cost > 0.5 ? "border-clock/30 bg-clock/10"
                            : "border-turf/30 bg-turf/10")}>
          <Term k="wire_drop">
            <span className={cn("eyebrow",
                                c.drop_cost > 0.5 ? "text-clock" : "text-turf")}>
              {c.drop_cost > 0.5 ? "The price" : "The spot is free"}
            </span>
          </Term>
          <p className="mt-0.5 text-[11px] leading-relaxed text-chalk">
            Your roster is full. Claiming him drops{" "}
            <span className="font-semibold">{c.drop_name}</span>
            {c.drop_cost > 0.5 ? "." : ", who the wire already beats."}
          </p>
        </div>
      ) : full ? null : (
        <p className="text-[10.5px] leading-relaxed text-muted">
          You have a spare roster spot, so this claim costs nothing but the
          priority itself.
        </p>
      )}

      {/* THIS DOES NOT PLACE THE CLAIM. Nothing here can -- ESPN takes claims
          and this reads them. It records one that went through, so your
          lineup, the next wire and any trade you price afterwards are all
          about the roster you actually have. */}
      <Button size="sm" variant="outline" disabled={busy}
              className="w-full" onClick={() => onRecord(c)}>
        {busy ? "recording…"
          : c.drop_name ? `I claimed him — drop ${c.drop_name}`
          : "I claimed him"}
      </Button>
      <p className="text-[9.5px] leading-relaxed text-muted">
        Records the move here. Put the claim in on ESPN yourself.
      </p>
    </div>
  );
}

/**
 * The position deck along the bottom — swipe it or click it.
 *
 * Dragging is the gesture he asked for and clicking is the one that works with
 * a mouse in a hurry, so both are here. The pill travels with a layoutId rather
 * than being redrawn, which is what makes the switch read as one movement.
 */
function Deck({ pos, setPos, wire }: {
  pos: string; setPos: (p: string) => void; wire: Wire | null;
}) {
  return (
    <motion.div
      drag="x"
      dragConstraints={{ left: 0, right: 0 }}
      dragElastic={0.1}
      onDragEnd={(_, info) => {
        const i = DECK.indexOf(pos as typeof DECK[number]);
        if (info.offset.x < -40) setPos(DECK[Math.min(i + 1, DECK.length - 1)]);
        if (info.offset.x > 40) setPos(DECK[Math.max(i - 1, 0)]);
      }}
      className="flex cursor-grab gap-0.5 rounded-md border border-line bg-ink/40 p-0.5 active:cursor-grabbing"
    >
      {DECK.map((p) => {
        const top = wire?.positions?.[p]?.[0];
        const on = p === pos;
        return (
          <button
            key={p}
            onClick={() => setPos(p)}
            title={top ? `${top.player_name}  ${top.adds > 0 ? "+" : ""}${top.adds.toFixed(1)}`
                       : `nobody unowned at ${p}`}
            className={cn(
              "relative select-none rounded px-2.5 py-1 text-[11px] font-bold transition-colors",
              on ? "text-ink" : "text-muted hover:text-chalk"
            )}
          >
            {on && (
              <motion.span
                layoutId="wire-pos"
                className="absolute inset-0 rounded bg-turf"
                transition={{ type: "spring", stiffness: 420, damping: 34 }}
              />
            )}
            <span className="relative z-10">{p}</span>
          </button>
        );
      })}
    </motion.div>
  );
}

/**
 * What to do, rather than what is available.
 *
 * Four names at six positions with a number beside each is still a screen you
 * have to think in front of, and the thinking is the same every week: is
 * anybody here worth less than what is free, and who replaces him. This is
 * that question answered — each move priced as the swap it actually is.
 */
function Plan({ plan, onOpen }: {
  plan: NonNullable<Wire["plan"]>; onOpen: (c: Claim) => void;
}) {
  const any = plan.moves.length > 0;
  // Open when there is something to do. A recommendation behind a click is a
  // recommendation you find after you have already made up your mind.
  const [open, setOpen] = useState(any);

  return (
    <section className={cn("rounded-lg border bg-panel",
                           any ? "border-turf/40" : "border-line")}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left"
      >
        <span className={cn("text-[12.5px] font-semibold",
                            any ? "text-turf" : "text-muted")}>
          {plan.headline}
        </span>
        {any && (
          <span className="num text-[11px] text-muted">
            worth +{plan.moves.reduce((s, m) => s + m.gain, 0).toFixed(1)} in total
          </span>
        )}
        <span className="ml-auto text-[10.5px] text-muted">
          {open ? "hide" : any ? "show me" : "why not"}
        </span>
      </button>

      {open && (
        <div className="tick-in space-y-2 border-t border-line p-2">
          {plan.moves.map((m, i) => (
            <button
              key={i}
              onClick={() => onOpen(m.add)}
              className="block w-full rounded-md border border-line bg-raised/40 p-2.5 text-left transition-colors hover:border-turf/40"
            >
              <div className="flex items-center gap-2">
                <span className="num shrink-0 rounded bg-turf/15 px-1.5 py-0.5 text-[10px] font-bold text-turf">
                  +{m.gain.toFixed(1)}
                </span>
                <span className="text-[12px] font-medium">
                  {m.kind === "swap" ? (
                    <>
                      Claim <span className="text-turf">{m.add.player_name}</span>
                      {", drop "}
                      <span className="text-alarm">{m.drop?.player_name}</span>
                    </>
                  ) : (
                    <>Claim <span className="text-turf">{m.add.player_name}</span></>
                  )}
                </span>
                <span className="ml-auto shrink-0 text-[9.5px] font-bold"
                      style={{ color: hue(m.add.position) }}>
                  {m.add.position}
                </span>
              </div>
              <p className="mt-1 text-[11px] leading-relaxed text-muted">{m.why}</p>
            </button>
          ))}
          <p className="px-1 text-[10px] leading-relaxed text-muted">{plan.note}</p>
        </div>
      )}
    </section>
  );
}

/**
 * The rest of the wire at this position, unpriced until you ask.
 *
 * The four above are the recommendation; this is the wire. Pricing a man is a
 * season simulation, so three hundred of them would cost four seconds to fill
 * a list nobody has read — one costs a tenth of a second, on click.
 */
function Rest({ rows, onPrice, pending, chosen }: {
  rows: NonNullable<Wire["rest"]>[string];
  onPrice: (id: string) => void;
  pending: string | null;
  chosen?: string;
}) {
  if (!rows.length) return null;
  return (
    <div className="border-t border-line">
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="eyebrow">Everyone else unowned</span>
        <span className="ml-auto text-[10px] text-muted">click to price one</span>
      </div>
      <ul className="max-h-[280px] overflow-y-auto">
        {rows.map((r) => (
          <li key={r.player_id}>
            <button
              onClick={() => onPrice(r.player_id)}
              disabled={!!pending}
              className={cn(
                "flex w-full items-center gap-2.5 border-t border-line/40 px-3 py-1.5 text-left transition-colors hover:bg-raised/60 disabled:opacity-50",
                chosen === r.player_id && "bg-turf/10"
              )}
            >
              <PlayerHover playerId={r.player_id} className="shrink-0">
                {r.headshot ? (
                  <img src={r.headshot} alt="" loading="lazy"
                       className="h-6 w-6 shrink-0 cursor-help rounded bg-raised object-cover object-top ring-1 ring-line" />
                ) : (
                  <span className="h-6 w-6 shrink-0 rounded bg-raised" />
                )}
              </PlayerHover>
              {/* THE NAME, not only the face. Every other list in the app hangs
                  the profile off the name, and hanging it off a 24-pixel
                  thumbnail here meant three hundred kickers and defences had
                  no profile at all as far as anyone could tell. */}
              <PlayerHover playerId={r.player_id} className="min-w-0 flex-1">
                <span className="block cursor-help truncate text-[11.5px]">
                  {r.player_name}
                </span>
              </PlayerHover>
              {r.team && (
                <span className="num shrink-0 text-[9.5px] text-muted">{r.team}</span>
              )}
              {r.owned != null && r.owned >= 25 && (
                <Term k="wire_own">
                  <span className="num shrink-0 text-[9.5px] text-clock">
                    {Math.round(r.owned)}% rostered
                  </span>
                </Term>
              )}
              <span className="num shrink-0 text-[10.5px] text-muted">
                {pending === r.player_id ? "pricing…"
                  : Math.round(r.projected_points)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * An injury tag you can read into.
 *
 * The tooltip is built here rather than added to the glossary because the text
 * and the availability come from the engine — `depth.status_note` — so the
 * number in the sentence is the number the simulation uses for him, not a
 * second copy of it written into the interface.
 */
function Hurt({ h }: { h: NonNullable<Wire["hurt"]>[number] }) {
  const { ref, anchor, show, hide, keep } = useHover({ delay: 60, grace: 180 });
  return (
    <span ref={ref} tabIndex={0}
          onMouseEnter={show} onMouseLeave={hide}
          onFocus={show} onBlur={hide}
          className="inline-flex cursor-help">
      <span className="text-[9px] uppercase tracking-wider text-alarm underline decoration-dotted decoration-alarm/50 underline-offset-2">
        {(h.label ?? h.status).toLowerCase()}
      </span>
      {anchor && (
        <Floating anchor={anchor} width={264} z={80}
                  interactive onEnter={keep} onLeave={hide}>
          <div role="tooltip"
               className="rounded-md border border-line bg-raised p-2.5 shadow-2xl">
            <div className="flex items-baseline gap-2">
              <span className="text-[11px] font-semibold text-alarm">
                {h.label ?? h.status}
              </span>
              {h.plays != null && (
                <span className="num ml-auto text-[10px] text-muted">
                  plays {Math.round(h.plays * 100)}% of what is left
                </span>
              )}
            </div>
            <span className="mt-1 block text-[11px] leading-relaxed text-muted">
              {h.text || "ESPN has him flagged."}
            </span>
            <span className="mt-1.5 block text-[10px] leading-relaxed text-muted">
              Straight from ESPN's roster feed, which is the only live injury
              report that exists — nflverse publishes none until the season
              starts.
            </span>
          </div>
        </Floating>
      )}
    </span>
  );
}

/** Your roster, with the men a claim would touch marked. */
function Mine({ team, wire, dropId }: {
  team: TeamReport | null; wire: Wire | null; dropId?: string | null;
}) {
  const hurt = new Map((wire?.hurt ?? []).map((h) => [h.player_id, h]));
  const rows = team ? [...team.starters, ...team.bench] : [];

  return (
    <section className="rounded-lg border border-line bg-panel">
      <header className="flex items-center gap-2 border-b border-line px-3 py-2">
        <span className="eyebrow">Your team</span>
        <span className="num ml-auto text-[10.5px] text-muted">
          {wire?.roster ?? rows.length}/{wire?.limit ?? "—"}
        </span>
      </header>

      {wire?.hurt?.length ? (
        <div className="border-b border-alarm/25 bg-alarm/10 px-3 py-1.5">
          <span className="text-[10.5px] leading-snug text-alarm">
            {wire.hurt.map((h) => h.player_name).join(", ")}
            {wire.hurt.length > 1 ? " are " : " is "}
            flagged by ESPN. That is what the wire below is for.
          </span>
        </div>
      ) : null}

      <ul className="max-h-[520px] overflow-y-auto">
        {rows.map((p) => {
          const tag = hurt.get(p.player_id);
          const going = p.player_id === dropId;
          return (
            <li key={p.player_id}
                className={cn(
                  "flex items-center gap-2.5 border-b border-line/40 px-3 py-1.5 last:border-0",
                  going && "bg-alarm/10"
                )}>
              <span className="num w-8 shrink-0 text-[10px] uppercase tracking-wider text-muted">
                {p.slot ?? "BE"}
              </span>
              {p.headshot ? (
                <PlayerHover playerId={p.player_id} className="shrink-0">
                  <img src={p.headshot} alt="" loading="lazy"
                       className="h-7 w-7 shrink-0 cursor-help rounded bg-raised object-cover object-top ring-1 ring-line" />
                </PlayerHover>
              ) : <span className="h-7 w-7 shrink-0 rounded bg-raised" />}
              <div className="min-w-0 flex-1">
                <PlayerHover playerId={p.player_id}>
                  <span className={cn(
                    "block cursor-help truncate text-[11.5px] leading-tight",
                    going && "line-through decoration-alarm/70")}>
                    {p.player_name}
                  </span>
                </PlayerHover>
                {tag ? (
                  // The flag explains itself. "QUESTIONABLE" in red raises the
                  // question and answers none of it; the number the engine
                  // actually uses for that tag does.
                  <Hurt h={tag} />
                ) : (
                  <span className="text-[9px] font-bold"
                        style={{ color: hue(p.position) }}>{p.position}</span>
                )}
              </div>
              {going && (
                <span className="shrink-0 rounded bg-alarm/20 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-alarm">
                  drops
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function Waivers({ drafting }: { drafting?: boolean }) {
  const [wire, setWire] = useState<Wire | null>(null);
  const [team, setTeam] = useState<TeamReport | null>(null);
  const [pos, setPos] = useState<string>("RB");
  const [picked, setPicked] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // A man priced on demand, from below the recommendation. Kept beside the
  // wire rather than folded into it: he is what you asked about, not what was
  // suggested, and the queue should not silently reorder around a question.
  const [asked, setAsked] = useState<Claim | null>(null);
  const [pricing, setPricing] = useState<string | null>(null);
  const first = useRef(true);

  const pull = useCallback(async (force = false, sync = true) => {
    setBusy(true);
    try {
      // ESPN FIRST, THEN PRICE, on every pull but the first. The rosters are
      // where an injury tag and a rival manager's claim both live, so
      // re-pricing without re-reading them prices last night's league.
      //
      // The first pull skips it because a cold read of the wire is already
      // several seconds and putting ESPN in front of it means staring at a
      // loading panel for ten. Whatever the last sync left is a few minutes
      // old at worst, and the next tick fixes it.
      if (sync) await api.sync().catch(() => null);
      const [w, t] = await Promise.all([api.waivers(force), api.teamReport()]);
      setWire(w);
      setTeam(t);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void pull(false, false);
    const t = setInterval(() => void pull(), POLL_MS);
    return () => clearInterval(t);
  }, [pull]);

  // Open on the position where the best claim is, once, rather than on
  // whichever one happens to be first alphabetically.
  useEffect(() => {
    if (!wire || !first.current) return;
    first.current = false;
    const best = wire.claims?.[0];
    if (best) setPos(best.position);
  }, [wire]);

  const price = useCallback(async (id: string) => {
    setPricing(id);
    try {
      const { claim } = await api.waiverPrice(id);
      setAsked(claim);
      setPicked(claim.player_id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setPricing(null);
    }
  }, []);

  // Record a claim that actually went through, so every number downstream --
  // your lineup, the next wire, a trade you price on Thursday -- is about the
  // roster you now have. It does not place the claim; nothing here can. ESPN
  // takes claims and this reads them.
  const record = useCallback(async (c: Claim) => {
    setBusy(true);
    try {
      if (c.drop_id) await api.removePick(c.drop_id);
      await api.pick({ player_id: c.player_id, mine: true });
      setAsked(null);
      setPicked(null);
      await pull(true, false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [pull]);

  const men = wire?.positions?.[pos] ?? [];
  const chosen = (asked && asked.player_id === picked ? asked : null)
    ?? men.find((c) => c.player_id === picked) ?? men[0];

  if (err) {
    return (
      <div className="rounded-lg border border-alarm/30 bg-alarm/10 p-4">
        <p className="text-[12px] text-alarm">{err}</p>
        <Button size="sm" variant="outline" className="mt-2"
                onClick={() => void pull()}>Try again</Button>
      </div>
    );
  }

  if (!wire) return <Simulating label="Reading the wire" />;

  if (wire.empty) {
    return (
      <div className="rounded-lg border border-line bg-panel p-6 text-center">
        <p className="text-[12.5px] text-muted">{wire.note}</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* THE POSITION SWITCH LIVES UP HERE. It was a deck along the bottom,
          which put the control for the whole screen below the fold of the
          thing it controls — you scrolled down to change position and back up
          to read the result. */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[12px] text-chalk">
          {wire.pool} players nobody in this league owns
        </span>
        {wire.pulled && (
          <span className="num text-[10.5px] text-muted">· read at {wire.pulled}</span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <Button size="sm" variant="ghost" className="h-7 text-[11px]"
                  disabled={busy} onClick={() => void pull(true)}>
            {busy ? "pricing…" : "Re-price"}
          </Button>
          <Term k="wire_queue">
            <span className="eyebrow hidden sm:inline">position</span>
          </Term>
          <Deck pos={pos} setPos={(p) => { setAsked(null); setPos(p); setPicked(null); }}
                wire={wire} />
        </div>
      </div>

      {wire.plan && (
        <Plan plan={wire.plan}
              onOpen={(c) => { setAsked(c); setPos(c.position); setPicked(c.player_id); }} />
      )}

      {drafting && (
        <div className="rounded-md border border-clock/30 bg-clock/10 px-3 py-2">
          <p className="text-[11px] leading-relaxed text-clock">
            Your draft is not finished, so "unowned" here means UNDRAFTED — this
            is the draft board with a different question on top of it. The
            numbers are real, but until the draft ends the men below are picks
            rather than claims, and the board is the better screen for that.
          </p>
        </div>
      )}

      {/* The standing note is about the same roster the plan is about, so it
          only speaks when the plan has nothing to say. */}
      {!wire.plan?.moves.length && (
        <p className="text-[11px] leading-relaxed text-muted">{wire.note}</p>
      )}

      <div className="grid gap-3 lg:grid-cols-[minmax(0,270px)_minmax(0,1fr)_minmax(0,320px)]">
        <Mine team={team} wire={wire} dropId={chosen?.drop_id} />

        <section className="rounded-lg border border-line bg-panel">
          <header className="flex items-center gap-2 border-b border-line px-3 py-2">
            <span className="eyebrow">The wire</span>
            <span className="text-[11px] font-bold" style={{ color: hue(pos) }}>
              {pos}
            </span>
            <span className="ml-auto text-[10px] text-muted">
              in the order you should want them
            </span>
          </header>
          <div className="space-y-2 p-2">
            {men.length === 0 ? (
              <p className="px-1 py-6 text-center text-[11.5px] text-muted">
                Nobody at {pos} is unowned in this league.
              </p>
            ) : (
              men.map((c, i) => (
                <WireCard key={c.player_id} c={c} i={i}
                          active={chosen?.player_id === c.player_id}
                          onPick={() => { setAsked(null); setPicked(c.player_id); }} />
              ))
            )}
          </div>
          {pos === "K" || pos === "DST" ? (
            <p className="border-t border-line px-3 py-2 text-[10px] leading-relaxed text-muted">
              {wire.streaming}
            </p>
          ) : null}
          <Rest rows={wire.rest?.[pos] ?? []} onPrice={price}
                pending={pricing} chosen={chosen?.player_id} />
        </section>

        <section className="rounded-lg border border-line bg-panel p-3">
          {chosen
            ? <TheCase key={chosen.player_id} c={chosen} full={!!wire.full}
                       onRecord={record} busy={busy} />
            : <p className="text-[11.5px] text-muted">
                Pick a name to see the case for him.
              </p>}
        </section>
      </div>
    </div>
  );
}
