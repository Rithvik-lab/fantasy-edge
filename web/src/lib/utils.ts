import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * What he scores in a week he plays.
 *
 * The season total is the right number for the lineup solver -- it is what
 * gets added up -- and the wrong one to read beside a name, because it is two
 * facts multiplied together. 230 could be a very good player who misses four
 * games or a decent one who plays seventeen, and on a lineup card you are
 * comparing men for THIS Sunday, where those two are not the same at all.
 *
 * Availability is not lost by dividing it out: it is priced separately, in the
 * season band and in the simulation. This is the line his actual scoring gets
 * measured against, which is why the performance panel already reports him
 * this way.
 *
 * A missing `expected_games` falls back to a full season rather than to zero.
 * That flatters a man the projection expects to miss time -- but the number
 * only goes missing where the engine did not send one, and dividing by nothing
 * would put an infinity on the card.
 */
export function perGame(p: {
  projected_points?: number | null;
  expected_games?: number | null;
}): string {
  const total = p.projected_points ?? 0;
  if (!total) return "—";
  const games = p.expected_games && p.expected_games > 0 ? p.expected_games : 17;
  return (total / games).toFixed(1);
}
