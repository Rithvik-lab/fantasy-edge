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

  async function fix() {
    setBusy(true);
    try {
      const s = await api.teamSuggest();
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
                onClick={fix} disabled={busy}
                title="set the best lineup for the next week nobody has played">
          {busy ? "sorting…" : "Fix my lineup"}
        </Button>
        {said && (
          <span className="num text-[10px] text-muted">week {said.week}</span>
        )}
      </div>

      <AnimatePresence>
        {said && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            className="text-[10.5px] leading-snug"
          >
            {said.promoted.length > 0 && (
              <ul className="mb-0.5 space-y-0.5">
                {said.promoted.map((p) => (
                  <li key={p.player_id} className="text-clock">
                    {p.player_name} &times;{p.lift.toFixed(2)} — the man ahead
                    of him is listed out
                  </li>
                ))}
              </ul>
            )}
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

      {said?.note && (
        <p className="text-[9.5px] leading-snug text-muted/80">{said.note}</p>
      )}

      {err && <p className="text-[10.5px] text-alarm">{err}</p>}
    </div>
  );
}
