import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { api, type Suggestion2 } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * One button that fixes the lineup, rather than a panel that proposes one.
 *
 * A LINEUP IS A WEEKLY QUESTION and the solver behind My Team answers a
 * seasonal one. Most weeks they agree; the weeks they do not are the weeks
 * people lose — a man on bye scores zero and a man listed out scores zero, and
 * a season projection cannot see either.
 *
 * The first version showed the changes and asked you to approve them, which is
 * a second decision about a question you had already answered by pressing the
 * button. Now it just does it: the roster rows are laid out with `layout`, so
 * the men move to their new slots rather than blinking into them, and what
 * changed is said once underneath and then gets out of the way.
 */
export function SuggestLineup({ onApplied }: { onApplied: () => void }) {
  const [week, setWeek] = useState(1);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<Suggestion2 | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // The receipt is not a state you have to leave. It says what moved and then
  // clears itself, because the answer is the roster above it.
  useEffect(() => {
    if (!said) return;
    const t = setTimeout(() => setSaid(null), 9000);
    return () => clearTimeout(t);
  }, [said]);

  async function fix(w: number) {
    setBusy(true);
    try {
      const s = await api.teamSuggest(w);
      if (!s.empty) {
        await api.rosterApply(s.pinned);
        onApplied();
        setSaid(s);
      }
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5">
        <Button size="sm" className="h-7 px-3 text-[11.5px]"
                onClick={() => fix(week)} disabled={busy}
                title="set the best lineup for this week, around byes and injuries">
          {busy ? "sorting…" : "Fix my lineup"}
        </Button>
        <select
          value={week}
          onChange={(e) => { const w = Number(e.target.value); setWeek(w); fix(w); }}
          disabled={busy}
          title="which week to solve for — byes are the reason this matters"
          className="h-7 rounded border border-line bg-ink px-1.5 text-[11px] text-muted focus:outline-none"
        >
          {Array.from({ length: 18 }, (_, i) => i + 1).map((w) => (
            <option key={w} value={w} className="bg-panel">week {w}</option>
          ))}
        </select>
      </div>

      <AnimatePresence>
        {said && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            className="text-[10.5px] leading-snug"
          >
            {said.changes.length === 0 ? (
              <span className="text-muted">
                Week {said.week} was already right — nobody on bye, nobody out.
              </span>
            ) : (
              <ul className="space-y-0.5">
                {said.changes.map((c, i) => (
                  <li key={i} className="text-muted">
                    <span className="text-turf">
                      {c.player_name ?? "nobody available"}
                    </span>
                    {" in for "}
                    <span className="text-chalk/70">{c.out_name}</span>
                    <span className={cn(" — ",
                      c.why.includes("bye") ? "text-clock"
                        : c.why.includes("listed") ? "text-alarm" : "")}>
                      {c.why}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {err && <p className="text-[10.5px] text-alarm">{err}</p>}
    </div>
  );
}
