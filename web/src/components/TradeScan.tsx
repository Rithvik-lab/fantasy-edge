import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import type {
  Ask, Piece, Read, Scan, TeamRead, TradeOffer, TradePlayer,
} from "@/lib/api";
import { POS_HUE } from "@/components/Charts";
import { PlayerHover } from "@/components/PlayerHover";
import { Term } from "@/components/Explain";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * The league, read — and the offers that fall out of it.
 *
 * The scan used to return a list of deals into a panel nested inside the
 * verdict, so pressing "find me trades" before analysing anything computed
 * eleven rosters and rendered NOTHING. That is the whole reason this is its
 * own component: the answer to a scan is not a footnote to a verdict, it is
 * the top of the page.
 *
 * And a list of offers alone answers the wrong question first. Before "what
 * should I send" comes "who should I be talking to", so every roster is here,
 * read the same way: strong where, thin where, who is spare, what they would
 * want back. Click a team and its best offer loads into the board.
 */

function Bars({ rows }: { rows: Read["strength"] }) {
  if (!rows.length) return null;
  const max = Math.max(...rows.map((r) => Math.abs(r.edge)), 40);
  return (
    <div className="space-y-1">
      {rows.map((r) => {
        const w = (Math.abs(r.edge) / max) * 50;
        const good = r.edge >= 0;
        return (
          <div key={r.position} className="flex items-center gap-1.5">
            <span className="w-6 shrink-0 text-[9.5px] font-bold"
                  style={{ color: POS_HUE[r.position] ?? "#8CA096" }}>
              {r.position}
            </span>
            <div className="relative h-2 flex-1">
              <div className="absolute inset-y-0 left-1/2 w-px bg-line" />
              <div className={cn("absolute top-1/2 h-1.5 -translate-y-1/2 rounded-[1px]",
                good ? "bg-turf/80" : "bg-alarm/80")}
                   style={good ? { left: "50%", width: `${w}%` }
                               : { right: "50%", width: `${w}%` }} />
            </div>
            <span className={cn("num w-8 shrink-0 text-right text-[9.5px]",
              good ? "text-turf/90" : "text-alarm/90")}>
              {good ? "+" : ""}{Math.round(r.edge)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/** One name with what he would move, either direction. */
function Name({ p, tone, onClick }: {
  p: Piece; tone: "turf" | "alarm"; onClick?: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={!onClick}
      title={onClick ? "put him in the trade" : undefined}
      className={cn(
        "flex w-full items-center gap-1.5 rounded px-1 py-0.5 text-left transition-colors",
        onClick && "hover:bg-raised")}
    >
      <span className="w-5 shrink-0 text-[9px] font-bold"
            style={{ color: POS_HUE[p.position] ?? "#8CA096" }}>
        {p.position}
      </span>
      <PlayerHover playerId={p.player_id} className="min-w-0 flex-1">
        <span className="block cursor-help truncate text-[11px]">
          {p.player_name}
        </span>
      </PlayerHover>
      {p.starting === false && (
        <Term k="spare">
          <span className="shrink-0 text-[8.5px] uppercase tracking-wider text-muted">
            spare
          </span>
        </Term>
      )}
      <Term k="adds">
        {/* Written out rather than composed: Tailwind reads class names at
            build time and never sees an interpolated one. */}
        <span className={cn("num shrink-0 text-[10px]",
          p.adds < 0 ? "text-muted"
            : tone === "turf" ? "text-turf" : "text-alarm")}>
          {p.adds >= 0 ? "+" : ""}{Math.round(p.adds)}
        </span>
      </Term>
    </button>
  );
}

/**
 * The read on ONE team, sat under its own roster.
 *
 * The grid above answers "who should I call". This answers "what am I looking
 * at" while you are building the deal, which is a different moment and wants
 * the analysis beside the names rather than three screens up.
 */
export function TeamSummary({ read, title, mine }: {
  read: Read | null;
  title: string;
  mine?: boolean;
}) {
  if (!read) return null;
  return (
    <section className={cn("rounded-lg border border-line bg-panel p-2.5",
      mine && "border-l-2 border-l-turf/50")}>
      <header className="flex items-baseline gap-2">
        <span className="eyebrow">{title}</span>
        {read.rank != null && (
          <span className="num text-[10px] text-muted">#{read.rank} in the league</span>
        )}
        <Term k="starters_total">
          <span className="num ml-auto text-[10.5px] text-muted">
            {Math.round(read.starters)}
          </span>
        </Term>
      </header>
      <div className="mt-1.5"><Bars rows={read.strength} /></div>
      <p className="mt-1.5 text-[10px] leading-snug text-muted">
        {read.needs.length ? (
          <>
            short at{" "}
            <Term k="team_needs">
              <span className="text-alarm/90">{read.needs.join(", ")}</span>
            </Term>
          </>
        ) : "no slot below the league average"}
        {read.surplus.length > 0 && (
          <>
            {" · "}
            <Term k="spare">
              <span>spare</span>
            </Term>
            {": "}
            {read.surplus.slice(0, 2).map((p) => p.player_name).join(", ")}
          </>
        )}
      </p>
    </section>
  );
}

function TeamCard({ t, active, onOpen, onTake, onSend }: {
  t: TeamRead;
  active: boolean;
  onOpen: () => void;
  onTake: (p: Piece) => void;
  onSend: (p: Piece) => void;
}) {
  return (
    <motion.div
      layout
      className={cn(
        "flex flex-col rounded-lg border bg-panel p-2.5 transition-colors",
        active ? "border-turf/50" : "border-line hover:border-line/80")}
    >
      <header className="flex items-baseline gap-1.5">
        <button onClick={onOpen}
                className="min-w-0 flex-1 truncate text-left text-[12px] font-medium hover:text-turf">
          {t.team_name}
        </button>
        {t.rank != null && (
          <span className="num shrink-0 text-[9.5px] text-muted">#{t.rank}</span>
        )}
        <Term k="fit">
          <span className="num shrink-0 text-[10.5px] font-semibold text-chalk">
            {Math.round(t.fit)}
          </span>
        </Term>
      </header>

      <p className="mt-1 text-[10.5px] leading-snug text-muted">{t.note}</p>

      <div className="mt-2"><Bars rows={t.strength} /></div>

      <div className="mt-2 grid gap-x-2 gap-y-0.5 border-t border-line/60 pt-1.5 sm:grid-cols-2">
        <div>
          <span className="eyebrow text-turf/70">you could get</span>
          {t.get_from_them.length === 0 ? (
            <p className="px-1 text-[10px] text-muted">nothing that starts for you</p>
          ) : t.get_from_them.map((p) => (
            <Name key={p.player_id} p={p} tone="turf" onClick={() => onTake(p)} />
          ))}
        </div>
        <div>
          <span className="eyebrow text-alarm/70">they would want</span>
          {t.they_want_from_you.length === 0 ? (
            <p className="px-1 text-[10px] text-muted">nothing of yours fits</p>
          ) : t.they_want_from_you.map((p) => (
            <Name key={p.player_id} p={p} tone="alarm" onClick={() => onSend(p)} />
          ))}
        </div>
      </div>
    </motion.div>
  );
}

/** The offer itself: two piles, two numbers, one button. */
function OfferRow({ o, onLoad }: { o: TradeOffer; onLoad: () => void }) {
  return (
    <li>
      <button onClick={onLoad}
              className="w-full px-3 py-2 text-left transition-colors hover:bg-raised/60">
        <div className="flex items-baseline gap-2">
          <span className="text-[11.5px] font-medium">{o.team_name}</span>
          <Term k="our_gain">
            <span className="num ml-auto text-[11.5px] font-semibold text-turf">
              +{Math.round(o.our_gain)}
            </span>
          </Term>
        </div>
        <p className="mt-0.5 text-[11px] leading-snug">
          <span className="text-alarm/80">
            {o.give.map((p) => p.player_name).join(", ") || "nothing"}
          </span>
          <span className="text-muted">{" → "}</span>
          <span className="text-turf/80">
            {o.get.map((p) => p.player_name).join(", ") || "nothing"}
          </span>
        </p>
        <p className="mt-0.5 flex flex-wrap gap-x-2 text-[10.5px] text-muted">
          <Term k="their_gain">
            <span>they read it as +{Math.round(o.their_gain)} their way</span>
          </Term>
          {o.their_lineup != null && (
            <Term k="their_lineup">
              <span>
                {o.their_lineup > 0
                  ? `fixes +${Math.round(o.their_lineup)} in their lineup`
                  : "fixes nothing for them"}
              </span>
            </Term>
          )}
          <span>{Math.round(o.win_probability * 100)}% of seasons</span>
        </p>
      </button>
    </li>
  );
}

/**
 * WHAT IS WRONG WITH THIS ONE.
 *
 * Re-roll on its own gives you another deal and no way to say why the last one
 * was no good. These chips are the four complaints that have an answer in the
 * search — hold a man, change the shape, raise the price, make it acceptable —
 * and each maps to a constraint rather than to a re-rank. The box underneath
 * is read by keyword against the two rosters, and whatever it understood comes
 * back on screen, because a text box that silently ignores you is worse than
 * no text box.
 */
function WhatsWrong({ give, players, onGo, busy, understood, team }: {
  give: string[];
  players: Map<string, TradePlayer>;
  onGo: (a: Ask) => void;
  busy: boolean;
  understood: string[];
  /** The manager this box searches. Null means the whole league. */
  team: TeamRead | null;
}) {
  const [keep, setKeep] = useState<string[]>([]);
  const [want, setWant] = useState<string[]>([]);
  const [flag, setFlag] = useState<Record<string, boolean>>({});
  const [note, setNote] = useState("");

  const toggle = (list: string[], set: (v: string[]) => void, v: string) =>
    set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  const Chip = ({ on, onClick, children }: {
    on: boolean; onClick: () => void; children: React.ReactNode;
  }) => (
    <button
      onClick={onClick}
      className={cn("rounded-full border px-2 py-0.5 text-[10.5px] transition-colors",
        on ? "border-turf/60 bg-turf/15 text-chalk"
           : "border-line text-muted hover:text-chalk")}
    >
      {children}
    </button>
  );

  return (
    <div className="space-y-2 border-t border-line px-3 py-2">
      <span className="eyebrow">
        {team ? `What's wrong with it?` : "Tell it what you are after"}
      </span>
      <p className="text-[10px] leading-snug text-muted">
        {team
          ? `Searches ${team.team_name} only — you have this manager open.`
          : "Searches every roster in the league."}
      </p>

      <div className="flex flex-wrap gap-1.5">
        {give.map((id) => {
          const p = players.get(id);
          return p ? (
            <Chip key={id} on={keep.includes(id)}
                  onClick={() => toggle(keep, setKeep, id)}>
              keep {p.player_name}
            </Chip>
          ) : null;
        })}
        {(["RB", "WR", "TE", "QB"] as const).map((pos) => (
          <Chip key={pos} on={want.includes(pos)}
                onClick={() => toggle(want, setWant, pos)}>
            I need a {pos}
          </Chip>
        ))}
        {([["fewer", "too many players"],
           ["richer", "not enough back"],
           ["harder", "they'd never accept"]] as const).map(([k, label]) => (
          <Chip key={k} on={!!flag[k]}
                onClick={() => setFlag((f) => ({ ...f, [k]: !f[k] }))}>
            {label}
          </Chip>
        ))}
      </div>

      <div className="flex gap-1.5">
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onGo({ keep, want, note, ...flag });
          }}
          placeholder={team
            ? `or say it — “keep Jeanty, I want a receiver”`
            : `or say it — “I'm set at tight end, I need a WR”`}
          className="h-7 flex-1 rounded border border-line bg-ink px-2 text-[11px] text-chalk placeholder:text-muted/70 focus:border-turf/50 focus:outline-none"
        />
        <Button size="sm" className="h-7 px-3 text-[11px]" disabled={busy}
                onClick={() => onGo({ keep, want, note, ...flag })}>
          {busy ? "looking…" : "Try again"}
        </Button>
      </div>

      {understood.length > 0 && (
        <p className="text-[10px] leading-snug text-muted">
          read as: {understood.join(" · ")}. Names and positions are matched
          against the two rosters — nothing else in the sentence is used.
        </p>
      )}
    </div>
  );
}

export function TradeScan({
  scan, busy, give, players, activeTeam, focus,
  onOpenTeam, onBack, onLoadOffer, onTake, onSend, onAgain, onClose,
}: {
  scan: Scan;
  busy: boolean;
  give: string[];
  players: Map<string, TradePlayer>;
  activeTeam: number | null;
  /** The manager currently open, or null for the grid of all of them. */
  focus: number | null;
  onOpenTeam: (t: TeamRead) => void;
  onBack: () => void;
  onLoadOffer: (o: TradeOffer) => void;
  onTake: (t: TeamRead, p: Piece) => void;
  onSend: (t: TeamRead, p: Piece) => void;
  onAgain: (a: Ask) => void;
  onClose: () => void;
}) {
  const [open, setOpen] = useState(true);
  const one = focus != null
    ? scan.teams.find((t) => t.team_id === focus) ?? null
    : null;

  // ONLY THE MANAGERS YOU CAN ACTUALLY DO BUSINESS WITH. A card for a team the
  // search found nothing with is an invitation to open an empty board, and
  // there is nothing behind it -- every combination against that roster either
  // loses for you or reads as a fleecing to them. They are counted at the
  // bottom rather than listed, because "no deal here" is worth knowing once
  // and not eleven times.
  const dealt = new Set(scan.offers.map((o) => o.team_id));
  const tradeable = scan.teams.filter((t) => dealt.has(t.team_id));
  const quiet = scan.teams.length - tradeable.length;

  // ONE MANAGER AT A TIME once you have picked one. Eleven cards is the right
  // answer to "who should I call" and the wrong one to "is this offer worth
  // sending" -- so the grid gets out of the way and leaves the door open.
  if (one) {
    return (
      <motion.section
        layout
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        className="rounded-lg border border-line bg-panel"
      >
        <header className="flex flex-wrap items-baseline gap-2 border-b border-line px-3 py-2">
          <button onClick={onBack}
                  className="text-[11px] text-muted transition-colors hover:text-chalk">
            &larr; all teams
          </button>
          <span className="text-[12.5px] font-medium">{one.team_name}</span>
          {one.rank != null && (
            <span className="num text-[10px] text-muted">#{one.rank}</span>
          )}
          <Term k="fit">
            <span className="num text-[10.5px] text-muted">fit {Math.round(one.fit)}</span>
          </Term>
          <button onClick={onClose}
                  className="ml-auto text-[11px] text-muted hover:text-chalk">
            close
          </button>
        </header>

        <div className="grid gap-3 p-3 lg:grid-cols-[1fr_1fr]">
          <div className="space-y-2">
            <p className="text-[11px] leading-snug text-muted">{one.note}</p>
            <Bars rows={one.strength} />
            <div className="grid gap-x-3 gap-y-0.5 border-t border-line/60 pt-1.5 sm:grid-cols-2">
              <div>
                <span className="eyebrow text-turf/70">ask them for</span>
                {one.get_from_them.map((p) => (
                  <Name key={p.player_id} p={p} tone="turf"
                        onClick={() => onTake(one, p)} />
                ))}
              </div>
              <div>
                <span className="eyebrow text-alarm/70">they would want</span>
                {one.they_want_from_you.map((p) => (
                  <Name key={p.player_id} p={p} tone="alarm"
                        onClick={() => onSend(one, p)} />
                ))}
              </div>
            </div>
          </div>

          <div className="rounded-lg border border-line">
            <header className="border-b border-line px-3 py-1.5">
              <span className="eyebrow">other offers to this team</span>
            </header>
            {scan.offers.filter((o) => o.team_id === one.team_id).length === 0 ? (
              <p className="px-3 py-4 text-center text-[11px] leading-snug text-muted">
                Nothing with this manager clears both tests at once — a deal
                has to win on your lineup AND read as a win on theirs, and no
                combination here does. The names on the left are what each side
                would want; put some in the piles and I will price whatever you
                build.
              </p>
            ) : (
              <ul className="divide-y divide-line/60">
                {scan.offers.filter((o) => o.team_id === one.team_id).map((o, i) => (
                  <OfferRow key={i} o={o} onLoad={() => onLoadOffer(o)} />
                ))}
              </ul>
            )}
            <WhatsWrong give={give} players={players} busy={busy}
                        team={one}
                        onGo={(a) => onAgain({ ...a, team_id: one.team_id })}
                        understood={scan.understood} />
          </div>
        </div>
      </motion.section>
    );
  }

  return (
    <motion.section
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="rounded-lg border border-line bg-panel"
    >
      <header className="flex flex-wrap items-baseline gap-2 border-b border-line px-3 py-2">
        <span className="eyebrow">The league, read</span>
        {scan.me && (
          <span className="text-[11px] text-muted">
            you are{" "}
            <span className="font-semibold text-chalk">#{scan.me.rank}</span>
            {" of "}{scan.teams.length + 1} on{" "}
            <Term k="starters_total">
              <span className="num">{Math.round(scan.me.starters)}</span>
            </Term>
            {scan.me.needs.length > 0 && (
              <>
                {" · short at "}
                <Term k="team_needs">
                  <span className="text-alarm/90">{scan.me.needs.join(", ")}</span>
                </Term>
              </>
            )}
          </span>
        )}
        <span className="text-[11px] text-muted">
          {tradeable.length} of {scan.teams.length} managers have a deal
        </span>
        <button onClick={() => setOpen((o) => !o)}
                className="ml-auto text-[11px] text-muted hover:text-chalk">
          {open ? "hide rosters" : "show rosters"}
        </button>
        <button onClick={onClose}
                className="text-[11px] text-muted hover:text-chalk">
          close
        </button>
      </header>

      <div className="grid gap-3 p-3 lg:grid-cols-[1.4fr_1fr]">
        <AnimatePresence initial={false}>
          {open && (
            <motion.div
              key="teams"
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              className="grid gap-2 sm:grid-cols-2"
            >
              {tradeable.map((t) => (
                <TeamCard key={t.team_id} t={t}
                          active={t.team_id === activeTeam}
                          onOpen={() => onOpenTeam(t)}
                          onTake={(p) => onTake(t, p)}
                          onSend={(p) => onSend(t, p)} />
              ))}
              {tradeable.length === 0 && (
                <p className="rounded-lg border border-dashed border-line p-4 text-center text-[11.5px] leading-snug text-muted sm:col-span-2">
                  No manager in the league has a deal that clears both tests
                  right now — it has to win on your lineup and read as a win on
                  theirs. Say what you are after below and it will look again.
                </p>
              )}
              {quiet > 0 && tradeable.length > 0 && (
                <p className="text-[10px] leading-snug text-muted sm:col-span-2">
                  {quiet} other {quiet === 1 ? "manager has" : "managers have"}{" "}
                  nothing that clears both tests today — every combination
                  against {quiet === 1 ? "that roster" : "those rosters"} either
                  loses for you or reads as a fleecing to them.
                </p>
              )}
            </motion.div>
          )}
        </AnimatePresence>

        <div className={cn("rounded-lg border border-line", !open && "lg:col-span-2")}>
          <header className="border-b border-line px-3 py-1.5">
            <span className="eyebrow">Offers worth sending</span>
            <p className="mt-0.5 text-[10px] leading-snug text-muted">
              Each one wins on your lineup AND reads as a win on theirs. Click
              to load it onto the board.
            </p>
          </header>
          {scan.offers.length === 0 ? (
            <p className="px-3 py-6 text-center text-[11px] leading-snug text-muted">
              {scan.note}
            </p>
          ) : (
            <ul className="divide-y divide-line/60">
              {scan.offers.map((o, i) => (
                <OfferRow key={i} o={o} onLoad={() => onLoadOffer(o)} />
              ))}
            </ul>
          )}
          <WhatsWrong give={give} players={players} onGo={onAgain} busy={busy}
                      team={null} understood={scan.understood} />
        </div>
      </div>
    </motion.section>
  );
}
