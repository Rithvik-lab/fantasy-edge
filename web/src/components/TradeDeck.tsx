import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  api, type FoundPlayer, type LeagueRoster, type SearchHit, type TradePlayer,
} from "@/lib/api";
import { POS_HUE } from "@/components/Charts";
import { PlayerHover } from "@/components/PlayerHover";
import { cn } from "@/lib/utils";

/**
 * The two columns you build a deal in.
 *
 * Both ways of adding a name work everywhere, on purpose: drag when you can
 * see the player, type when you already know who you want. Neither is a mode
 * you have to be in — the fastest route depends on whether you are browsing
 * or aiming, and that changes several times inside one negotiation.
 *
 * Drag uses the platform's own drag events rather than a library. A roster row
 * is not a rich drag target; it is a name going into a pile.
 */
export type Stance = "conservative" | "fair" | "fleece";

export const STANCES: { id: Stance; label: string; hint: string }[] = [
  { id: "conservative", label: "Conservative",
    hint: "they gain a lot — high chance they accept, smaller edge for you" },
  { id: "fair", label: "Fair",
    hint: "both sides gain about the same" },
  { id: "fleece", label: "Fleece",
    hint: "they gain the least that still gets a yes — biggest edge, most rejections" },
];

export function StancePicker({ value, onChange }: {
  value: Stance; onChange: (s: Stance) => void;
}) {
  return (
    <div className="flex rounded-md border border-line p-0.5">
      {STANCES.map((s) => (
        <button
          key={s.id}
          onClick={() => onChange(s.id)}
          title={s.hint}
          className={cn(
            "relative rounded px-2.5 py-1 text-[10.5px] font-medium transition-colors",
            value === s.id ? "text-ink" : "text-muted hover:text-chalk"
          )}
        >
          {value === s.id && (
            <motion.span
              layoutId="stance-pill"
              className="absolute inset-0 rounded bg-turf"
              transition={{ type: "spring", stiffness: 400, damping: 32 }}
            />
          )}
          <span className="relative z-10">{s.label}</span>
        </button>
      ))}
    </div>
  );
}

/** Type a name, click it in. Works whether or not a roster is synced. */
export function AddByName({ onAdd, placeholder, restrictTo }: {
  onAdd: (p: SearchHit) => void;
  placeholder: string;
  /** When a roster is known, only offer names actually on it. */
  restrictTo?: Set<string> | null;
}) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (q.trim().length < 2) { setHits([]); return; }
    let alive = true;
    const t = setTimeout(async () => {
      try {
        const r = await api.search(q);
        if (!alive) return;
        const list = restrictTo
          ? r.players.filter((p) => restrictTo.has(p.player_id))
          : r.players;
        setHits(list.slice(0, 6));
      } catch { /* leave it */ }
    }, 160);
    return () => { alive = false; clearTimeout(t); };
  }, [q, restrictTo]);

  return (
    <div ref={box} className="relative">
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder={placeholder}
        className="h-7 w-full rounded border border-line bg-ink px-2 text-[11px] placeholder:text-muted/60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-turf/50"
      />
      {hits.length > 0 && (
        <ul className="absolute top-full z-30 mt-1 w-full overflow-hidden rounded-md border border-line bg-raised shadow-2xl">
          {hits.map((h) => (
            <li key={h.player_id}>
              <button
                onClick={() => { onAdd(h); setQ(""); setHits([]); }}
                className="flex w-full items-center gap-2 px-2 py-1.5 text-left transition-colors hover:bg-turf/12"
              >
                {h.headshot ? (
                  <img src={h.headshot} alt="" loading="lazy"
                       className="h-6 w-6 shrink-0 rounded object-cover object-top" />
                ) : <span className="h-6 w-6 shrink-0 rounded bg-line" />}
                <span className="flex-1 truncate text-[11px]">{h.player_name}</span>
                <span className="text-[10px] font-bold"
                      style={{ color: POS_HUE[h.position] ?? "#8CA096" }}>
                  {h.position}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Name a player, go to wherever he is.
 *
 * The two boxes above each search one roster, which cannot answer the question
 * you usually walk in with -- I want THIS man, who do I talk to. Picking the
 * manager is the thing the search was for, so this searches the whole league
 * and then does the routing itself: an opponent's player opens that manager
 * with him already in the get pile, one of yours goes into the give pile, and
 * a free agent is not a trade at all and belongs on the waiver board.
 */
export function FindPlayer({ onPick, busy }: {
  onPick: (p: FoundPlayer) => void;
  busy?: boolean;
}) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<FoundPlayer[]>([]);
  const [open, setOpen] = useState(false);
  // A SEARCH THAT FAILS MUST NOT LOOK LIKE A SEARCH THAT FOUND NOBODY. This
  // swallowed every error and left the list empty, so an engine running from
  // before the endpoint existed -- which is what a server nobody has restarted
  // is -- presented as a box you could type into that never did anything.
  // There was no way to tell that from "no such player" and no way at all to
  // tell it from a bug.
  const [state, setState] = useState<"idle" | "busy" | "ok" | "fail">("idle");
  const [why, setWhy] = useState<string | null>(null);

  useEffect(() => {
    if (q.trim().length < 2) { setHits([]); setState("idle"); return; }
    let alive = true;
    setState("busy");
    const t = setTimeout(async () => {
      try {
        const r = await api.whereis(q);
        if (!alive) return;
        setHits(r.players);
        setState("ok");
        setOpen(true);
      } catch (e) {
        if (!alive) return;
        setHits([]);
        setState("fail");
        // The api layer already writes these, and writes them well -- a 404
        // there says "the engine is running older code than this page",
        // which is the actual diagnosis. Repeating it here in different
        // words would only give the app two voices for one problem.
        setWhy(e instanceof Error ? e.message : String(e));
        setOpen(true);
      }
    }, 160);
    return () => { alive = false; clearTimeout(t); };
  }, [q]);

  const tag = (p: FoundPlayer) =>
    p.where === "mine" ? "yours"
      : p.where === "team" ? (p.owner ?? "owned")
        : p.where === "wire" ? "on the wire"
          : "owner unknown";
  const tone = (p: FoundPlayer) =>
    p.where === "mine" ? "text-alarm/80"
      : p.where === "wire" ? "text-clock" : "text-turf/80";

  return (
    <div className="relative min-w-[13rem] flex-1 sm:max-w-[20rem]">
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 140)}
        disabled={busy}
        placeholder="Search for player"
        className="h-7 w-full rounded border border-line bg-ink px-2 text-[11px] placeholder:text-muted/60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-turf/50 disabled:opacity-50"
      />
      {/* Every state that is not a list of names says which one it is. */}
      {open && state !== "ok" && q.trim().length >= 2 && (
        <div className={cn(
          "absolute top-full z-40 mt-1 w-full rounded-md border bg-raised px-2 py-1.5 text-[10.5px] leading-snug shadow-2xl",
          state === "fail" ? "border-alarm/40 text-alarm" : "border-line text-muted")}>
          {state === "busy" ? "looking…" : why}
        </div>
      )}
      {open && state === "ok" && hits.length === 0 && (
        <div className="absolute top-full z-40 mt-1 w-full rounded-md border border-line bg-raised px-2 py-1.5 text-[10.5px] text-muted shadow-2xl">
          Nobody by that name on the board.
        </div>
      )}
      {open && hits.length > 0 && (
        <ul className="absolute top-full z-40 mt-1 w-full overflow-hidden rounded-md border border-line bg-raised shadow-2xl">
          {hits.map((h) => (
            <li key={h.player_id}>
              <button
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  onPick(h);
                  setQ(""); setHits([]); setOpen(false); setState("idle");
                }}
                className="flex w-full items-center gap-2 px-2 py-1.5 text-left transition-colors hover:bg-turf/12"
              >
                {h.headshot ? (
                  <img src={h.headshot} alt="" loading="lazy"
                       className="h-6 w-6 shrink-0 rounded object-cover object-top" />
                ) : <span className="h-6 w-6 shrink-0 rounded bg-line" />}
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[11px]">{h.player_name}</span>
                  <span className={cn("block truncate text-[9.5px]", tone(h))}>
                    {tag(h)}
                  </span>
                </span>
                <span className="shrink-0 text-[10px] font-bold"
                      style={{ color: POS_HUE[h.position] ?? "#8CA096" }}>
                  {h.position}
                </span>
                {h.team && (
                  <span className="num w-7 shrink-0 text-right text-[9px] text-muted">
                    {h.team}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Row({ p, selected, onToggle, draggable }: {
  p: LeagueRoster["players"][0] | TradePlayer;
  selected: boolean;
  onToggle: () => void;
  draggable?: boolean;
}) {
  const inj = "injury_status" in p ? p.injury_status : null;
  return (
    <li
      draggable={draggable}
      onDragStart={(e) => {
        e.dataTransfer.setData("text/plain", p.player_id);
        e.dataTransfer.effectAllowed = "move";
      }}
      className={cn(
        "flex cursor-grab items-center gap-2 rounded border px-2 py-1.5 transition-colors active:cursor-grabbing",
        selected ? "border-turf/50 bg-turf/10" : "border-transparent hover:bg-raised/60"
      )}
      onClick={onToggle}
    >
      {"headshot" in p && p.headshot ? (
        <PlayerHover playerId={p.player_id} className="shrink-0">
          <img src={p.headshot} alt="" loading="lazy"
               className="h-7 w-7 shrink-0 cursor-help rounded bg-raised object-cover object-top" />
        </PlayerHover>
      ) : (
        <span className="h-7 w-7 shrink-0 rounded bg-raised" />
      )}
      <span className="w-6 shrink-0 text-[10px] font-bold"
            style={{ color: POS_HUE[p.position] ?? "#8CA096" }}>
        {p.position}
      </span>
      <PlayerHover playerId={p.player_id} className="min-w-0 flex-1">
        <span className="block cursor-help truncate text-xs">{p.player_name}</span>
      </PlayerHover>
      {inj && inj !== "ACTIVE" && (
        <span className="shrink-0 text-[9px] uppercase text-clock">{inj.slice(0, 3)}</span>
      )}
      <span className="num w-8 shrink-0 text-right text-[10.5px] text-muted">
        {Math.round(p.projected_points ?? 0)}
      </span>
    </li>
  );
}

/** Where dragged names land. */
export function DropZone({ label, tone, ids, players, onDrop, onRemove }: {
  label: string;
  tone: "alarm" | "turf";
  ids: string[];
  players: Map<string, TradePlayer>;
  onDrop: (id: string) => void;
  onRemove: (id: string) => void;
}) {
  const [over, setOver] = useState(false);
  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const id = e.dataTransfer.getData("text/plain");
        if (id) onDrop(id);
      }}
      className={cn(
        "min-h-[64px] rounded-lg border border-dashed p-2 transition-colors",
        over ? "border-turf bg-turf/10"
          : tone === "alarm" ? "border-alarm/25" : "border-turf/25"
      )}
    >
      <span className={cn("eyebrow", tone === "alarm" ? "text-alarm/70" : "text-turf/70")}>
        {label}
      </span>
      {ids.length === 0 ? (
        <p className="mt-1 text-[10.5px] text-muted">drag or type a name here</p>
      ) : (
        <AnimatePresence initial={false}>
          {ids.map((id) => {
            const p = players.get(id);
            if (!p) return null;
            return (
              <motion.div
                key={id}
                layout
                initial={{ opacity: 0, x: -6 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, scale: 0.9 }}
                className="mt-1 flex items-center gap-1.5"
              >
                {p.headshot && (
                  <img src={p.headshot} alt="" loading="lazy"
                       className="h-6 w-6 shrink-0 rounded object-cover object-top" />
                )}
                <span className="truncate text-[11px]">{p.player_name}</span>
                <button onClick={() => onRemove(id)}
                        className="ml-auto shrink-0 px-1 text-[13px] leading-none text-muted hover:text-alarm">
                  &times;
                </button>
              </motion.div>
            );
          })}
        </AnimatePresence>
      )}
    </div>
  );
}
