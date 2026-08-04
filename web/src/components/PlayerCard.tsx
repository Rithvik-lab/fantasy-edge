import { useState } from "react";
import type { Suggestion } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Term } from "@/components/Explain";
import { POS_HUE } from "@/components/Charts";
import { PlayerHover } from "@/components/PlayerHover";
import { cn } from "@/lib/utils";

/** How likely he is to reach your next pick, said plainly. */
function survival(p: number) {
  if (p < 0.25) return { label: "Gone", tone: "alarm" as const };
  if (p < 0.55) return { label: "Coin flip", tone: "clock" as const };
  return { label: "Can wait", tone: "neutral" as const };
}

/**
 * The floor-to-ceiling band, drawn to scale against the whole shortlist.
 * A range is the honest shape of a season projection, and drawing it is the
 * only way to show that two players with the same median are different bets.
 */
function RangeBar({ floor, ceiling, max }: { floor: number; ceiling: number; max: number }) {
  const left = Math.max(0, (floor / max) * 100);
  const width = Math.max(2, ((ceiling - floor) / max) * 100);
  return (
    <div className="space-y-1">
      <div className="relative h-1.5 w-full rounded-full bg-raised">
        <div
          className="absolute h-1.5 rounded-full bg-gradient-to-r from-turf/35 to-turf"
          style={{ left: `${left}%`, width: `${width}%` }}
        />
      </div>
      <div className="num flex justify-between text-[10px] text-muted">
        <span>{Math.round(floor)}</span>
        <Term k="range"><span className="text-muted/70">season range</span></Term>
        <span>{Math.round(ceiling)}</span>
      </div>
    </div>
  );
}

export function PlayerCard({
  s, rank, max, onDraft, onSkip, busy, compact,
}: {
  s: Suggestion;
  rank: number;
  max: number;
  onDraft: () => void;
  onSkip: () => void;
  busy: boolean;
  /** Names added past the top three. They earn a row, not a card — vertical
   *  space at the bottom of the screen is the scarcest thing in this layout. */
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [imgBad, setImgBad] = useState(false);
  const surv = survival(s.p_survive);
  const initials = s.player_name.split(" ").map((w) => w[0]).slice(0, 2).join("");

  if (compact) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-line bg-panel px-2 py-1.5">
        <span className="num w-3 shrink-0 text-center text-[11px] font-bold text-muted">
          {rank}
        </span>
        <div className="h-7 w-7 shrink-0 overflow-hidden rounded bg-raised ring-1 ring-line">
          {s.headshot && !imgBad ? (
            <img src={s.headshot} alt="" loading="lazy" onError={() => setImgBad(true)}
                 className="h-full w-full object-cover object-top" />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-[9px] font-bold text-muted">
              {initials}
            </div>
          )}
        </div>
        <PlayerHover playerId={s.player_id} className="min-w-0 flex-1">
          <span className="block cursor-help truncate text-xs font-medium">
            {s.player_name}
          </span>
        </PlayerHover>
        <span className="shrink-0 text-[10px] font-bold"
              style={{ color: POS_HUE[s.position] ?? "#8CA096" }}>
          {s.position}
        </span>
        <Term k="vor">
          <span className={cn("num shrink-0 text-[10.5px]",
            s.vor >= 0 ? "text-turf" : "text-alarm")}>
            {s.vor > 0 ? "+" : ""}{Math.round(s.vor)}
          </span>
        </Term>
        <Term k="survive">
          <span className={cn("num shrink-0 text-[10.5px]",
            surv.tone === "alarm" ? "text-alarm"
              : surv.tone === "clock" ? "text-clock" : "text-muted")}>
            {Math.round(s.p_survive * 100)}%
          </span>
        </Term>
        <Button size="sm" variant="outline" className="h-6 shrink-0 px-2 text-[11px]"
                onClick={onDraft} disabled={busy}>
          Draft
        </Button>
        <Button size="sm" variant="ghost" className="h-6 shrink-0 px-1.5 text-[11px]"
                onClick={onSkip} disabled={busy} title="Show me someone else">
          Skip
        </Button>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "flex flex-col rounded-lg border bg-panel transition-colors",
        rank === 1 ? "border-turf/45" : "border-line hover:border-line/80"
      )}
    >
      <div className="flex gap-3 p-3">
        {/* Rank. Numbered because this genuinely is a ranked sequence. */}
        <div className="flex flex-col items-center pt-0.5">
          <span className={cn("num text-lg font-bold leading-none",
            rank === 1 ? "text-turf" : "text-muted")}>{rank}</span>
        </div>

        {/* Face. ESPN serves these by player id; fall back to initials. */}
        <div className="relative h-14 w-14 shrink-0 overflow-hidden rounded-md bg-raised ring-1 ring-line">
          {s.headshot && !imgBad ? (
            <img
              src={s.headshot}
              alt={s.player_name}
              loading="lazy"
              onError={() => setImgBad(true)}
              className="h-full w-full object-cover object-top"
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-sm font-bold text-muted">
              {initials}
            </div>
          )}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <PlayerHover playerId={s.player_id} className="min-w-0">
              <span className="block cursor-help truncate font-semibold leading-tight">
                {s.player_name}
              </span>
            </PlayerHover>
            {/* Position uses the validated categorical hue, never turf or
                clock — those mean "good value" and "urgent" everywhere else,
                and a colour cannot mean two things. */}
            <span className="text-xs font-bold"
                  style={{ color: POS_HUE[s.position] ?? "#8CA096" }}>
              {s.position}
            </span>
            {s.rookie && <Term k="rookie"><Badge tone="clock">R</Badge></Term>}
          </div>

          <div className="mt-0.5 flex items-center gap-3 text-[11px] text-muted num">
            <Term k="adp"><span>ADP {s.ecr ? Math.round(s.ecr) : "—"}</span></Term>
            <Term k="vor">
              <span className={s.vor >= 0 ? "text-turf" : "text-alarm"}>
                VOR {s.vor > 0 ? "+" : ""}{Math.round(s.vor)}
              </span>
            </Term>
            <Term k="survive">
              <Badge tone={surv.tone}>{surv.label} {Math.round(s.p_survive * 100)}%</Badge>
            </Term>
          </div>

          <p className="mt-1.5 text-[12px] leading-snug text-chalk/85">{s.headline}</p>
        </div>
      </div>

      {s.floor != null && s.ceiling != null && (
        <div className="px-3 pb-2">
          <RangeBar floor={s.floor} ceiling={s.ceiling} max={max} />
        </div>
      )}

      {open && (
        <ul className="space-y-1 border-t border-line px-3 py-2">
          {s.reasons.map((r, i) => (
            <li key={i} className="flex gap-2 text-[11.5px] leading-snug text-muted">
              <span className="text-turf/70">—</span>
              <span>{r}</span>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-auto flex items-center gap-2 border-t border-line p-2">
        <Button size="sm" className="flex-1" onClick={onDraft} disabled={busy}>
          Draft {s.player_name.split(" ").slice(-1)[0]}
        </Button>
        <Button size="sm" variant="ghost" onClick={onSkip} disabled={busy} title="Show me someone else">
          Skip
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setOpen((v) => !v)}>
          {open ? "Less" : "Why"}
        </Button>
      </div>
    </div>
  );
}
