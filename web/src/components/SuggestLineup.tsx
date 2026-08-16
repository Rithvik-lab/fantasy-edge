import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { api, type Suggestion2 } from "@/lib/api";
import { Term } from "@/components/Explain";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Set my lineup for a week.
 *
 * A LINEUP IS A WEEKLY QUESTION and the solver behind My Team answers a
 * seasonal one. Most weeks they agree; the weeks they do not are exactly the
 * weeks people lose — a man on bye scores zero and a man listed out scores
 * zero, and a season projection cannot see either. That is how a roster that
 * looked right in August starts a bye week in October.
 *
 * The week picker is the whole feature. Looking at week 13 in August is how
 * you find out four of your starters share a bye, which is a trade you make
 * now rather than a lineup you fix then.
 */
export function SuggestLineup({ onApplied }: { onApplied: () => void }) {
  const [week, setWeek] = useState(1);
  const [d, setD] = useState<Suggestion2 | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function look(w: number) {
    setWeek(w);
    setBusy(true);
    try {
      setD(await api.teamSuggest(w));
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  }

  async function apply() {
    if (!d) return;
    setBusy(true);
    try {
      await api.rosterApply(d.pinned);
      onApplied();
      setD(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" className="h-7 text-[11px]"
                onClick={() => look(week)} disabled={busy}
                title="solve this week's lineup around byes and injuries">
          {busy ? "solving…" : "Suggest lineup"}
        </Button>
        {d && (
          <div className="flex items-center gap-1">
            <span className="text-[10px] text-muted">week</span>
            <select
              value={week}
              onChange={(e) => look(Number(e.target.value))}
              className="h-6 rounded border border-line bg-ink px-1 text-[11px] text-chalk focus:outline-none"
            >
              {Array.from({ length: 18 }, (_, i) => i + 1).map((w) => (
                <option key={w} value={w} className="bg-panel">{w}</option>
              ))}
            </select>
          </div>
        )}
      </div>

      <AnimatePresence>
        {d && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden rounded-md border border-line bg-raised/40"
          >
            <div className="p-2.5">
              {d.changes.length === 0 ? (
                <p className="text-[11px] leading-snug text-muted">
                  Week {d.week} is already set the best way it can be — nobody
                  is on bye and nobody is listed out.
                </p>
              ) : (
                <>
                  <span className="eyebrow">week {d.week} — what changes</span>
                  <ul className="mt-1.5 space-y-1">
                    {d.changes.map((c, i) => (
                      <li key={i} className="flex items-center gap-2 text-[11px]">
                        <span className="num w-10 shrink-0 text-[9.5px] uppercase tracking-wider text-muted">
                          {c.slot ?? "—"}
                        </span>
                        <span className={cn("min-w-0 flex-1 truncate",
                          c.player_name ? "text-turf" : "text-alarm/80")}>
                          {c.player_name ?? "nobody available"}
                        </span>
                        <span className="shrink-0 text-muted">for</span>
                        <span className="min-w-0 flex-1 truncate text-muted line-through decoration-muted/50">
                          {c.out_name}
                        </span>
                        <span className={cn("shrink-0 text-[10px]",
                          c.why.includes("bye") ? "text-clock"
                            : c.why.includes("listed") ? "text-alarm" : "text-muted")}>
                          {c.why}
                        </span>
                      </li>
                    ))}
                  </ul>
                  <Button size="sm" className="mt-2 h-6 px-3 text-[11px]"
                          onClick={apply} disabled={busy}>
                    Set this lineup
                  </Button>
                </>
              )}

              {/* THE PART WORTH LOOKING AT IN AUGUST. A week with four of your
                  starters out is not a lineup problem, it is a trade you make
                  two months earlier. */}
              {Object.keys(d.byes ?? {}).length > 0 && (
                <div className="mt-2.5 border-t border-line pt-2">
                  <Term k="byes">
                    <span className="eyebrow">bye weeks ahead</span>
                  </Term>
                  <ul className="mt-1 space-y-0.5">
                    {Object.entries(d.byes)
                      .sort((a, b) => Number(a[0]) - Number(b[0]))
                      .map(([w, names]) => (
                        <li key={w}
                            className={cn("text-[10.5px]",
                              names.length >= 3 ? "text-clock" : "text-muted")}>
                          <button onClick={() => look(Number(w))}
                                  className="num mr-1.5 underline-offset-2 hover:underline">
                            week {w}
                          </button>
                          {names.join(", ")}
                          {names.length >= 3 && " — that is a hole, not a lineup"}
                        </li>
                      ))}
                  </ul>
                </div>
              )}

              {d.note && (
                <p className="mt-2 border-t border-line pt-2 text-[10px] leading-snug text-muted">
                  {d.note}
                </p>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {err && (
        <p className="text-[10.5px] text-alarm">{err}</p>
      )}
    </div>
  );
}
