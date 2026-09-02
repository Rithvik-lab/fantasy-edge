import { Floating, useHover } from "@/components/Floating";
import { cn } from "@/lib/utils";

/**
 * An injury flag the way a fantasy site writes one: a letter beside the name.
 *
 * The app used to spell it out — "questionable" in lowercase red under every
 * flagged player, and a paragraph above the roster explaining that three men
 * were flagged and that this was what the wire was for. Nobody reads a roster
 * that way. You read down a column of names looking for the letters, because
 * every fantasy site you have ever used puts a Q or an O there and your eye
 * already knows where to go.
 *
 * The explanation is not deleted, it is moved into the hover, which is the
 * right place for a sentence: it answers the question the letter raises,
 * rather than raising it in a bigger font.
 */

// Keyed on both vocabularies at once. The engine's canonical labels
// ("Questionable", "Injured Reserve") and ESPN's raw tags ("QUESTIONABLE",
// "INJURY_RESERVE") reach the interface from different endpoints, and
// normalising to one shape here beats making every caller remember which it
// happens to be holding.
const SHORT: Record<string, string> = {
  QUESTIONABLE: "Q",
  DAY_TO_DAY: "Q",
  DOUBTFUL: "D",
  OUT: "O",
  NOT_ACTIVE: "O",
  IR: "IR",
  INJURY_RESERVE: "IR",
  INJURED_RESERVE: "IR",
  PUP: "PUP",
  SUSPENSION: "SUS",
  SUSPENDED: "SUS",
};

/** Questionable is a maybe and everything else is a no. Two colours, not six. */
const AMBER = new Set(["Q", "D"]);

export function injuryCode(tag?: string | null): string | null {
  if (!tag) return null;
  const k = tag.trim().toUpperCase().replace(/[\s-]+/g, "_");
  // ACTIVE and NORMAL land here and return null, which is the point: a lineup
  // card covered in green ACTIVE badges is a lineup card nobody reads.
  return SHORT[k] ?? null;
}

export function InjuryTag({ tag, note, className }: {
  tag?: string | null;
  /** `depth.status_note` from the engine, when the caller has it. */
  note?: { label?: string; plays?: number; text?: string } | null;
  className?: string;
}) {
  const { ref, anchor, show, hide, keep } = useHover({ delay: 60, grace: 180 });
  const code = injuryCode(tag);
  if (!code) return null;

  const amber = AMBER.has(code);
  const label = note?.label ?? tag;
  return (
    <span
      ref={ref}
      tabIndex={0}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      title={note ? undefined : (label ?? undefined)}
      className={cn(
        "inline-flex cursor-help select-none items-center justify-center rounded-[3px] px-1 text-[9px] font-bold leading-[14px] tracking-tight",
        amber ? "bg-clock/20 text-clock" : "bg-alarm/20 text-alarm",
        className
      )}
    >
      {code}
      {note && anchor && (
        <Floating anchor={anchor} width={252} z={80}
                  interactive onEnter={keep} onLeave={hide}>
          <div role="tooltip"
               className="rounded-md border border-line bg-raised p-2.5 text-left shadow-2xl">
            <div className="flex items-baseline gap-2">
              <span className={cn("text-[11px] font-semibold",
                                  amber ? "text-clock" : "text-alarm")}>
                {label}
              </span>
              {note.plays != null && (
                <span className="num ml-auto text-[10px] text-muted">
                  plays {Math.round(note.plays * 100)}%
                </span>
              )}
            </div>
            {note.text && (
              <span className="mt-1 block text-[11px] font-normal leading-relaxed text-muted">
                {note.text}
              </span>
            )}
          </div>
        </Floating>
      )}
    </span>
  );
}
