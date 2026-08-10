import { useEffect, useState } from "react";
import type { Status } from "@/lib/api";
import { cn } from "@/lib/utils";

function countdown(sec: number) {
  const s = Math.max(0, Math.floor(sec));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m ${String(ss).padStart(2, "0")}s`;
}

/**
 * You are early / you are late.
 *
 * Both are easy to hit and both silently produce nonsense: connecting early
 * looks identical to a draft where nobody has picked, and connecting after
 * looks identical to one that never started. Saying which it is costs a strip
 * of screen and removes the whole class of confusion.
 */
export function Phase({ status }: { status: Status }) {
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, []);

  // A FINISHED DRAFT GETS NO BANNER. It used to announce itself and then
  // apologise -- "nothing below is a live pick" -- while still showing a live
  // draft interface underneath. A completed draft is a state the app should
  // BE IN, not a caption over the wrong screen, so the layout changes instead
  // and the banner is gone.
  if (status.phase === "complete") return null;

  // Manual mode has no scheduled draft and nothing syncing, so "you are here
  // early" is meaningless there — it described a live connection that does not
  // exist. Only an attached ESPN league can be early.
  if (status.phase !== "pre" || !status.espn_connected) return null;

  const left = status.draft_time ? status.draft_time - now : null;
  const soon = left != null && left <= 0;

  return (
    <div className="border-b border-clock/25 bg-clock/8 px-4 py-2.5">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-3 gap-y-1">
        <span className="relative flex h-2 w-2 shrink-0">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-clock opacity-60" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-clock" />
        </span>
        <span className="text-sm font-medium text-clock">
          {soon ? "Draft is about to start" : "You are here early"}
        </span>
        {left != null && !soon ? (
          <span className="num text-sm text-clock/90">
            starts in {countdown(left)}
          </span>
        ) : (
          <span className="text-xs text-muted">
            No picks yet. Everything is connected and watching.
          </span>
        )}
        <span className="ml-auto text-[11px] text-muted">
          Board is built and syncing — leave this open.
        </span>
      </div>
    </div>
  );
}

/** Small live dot for the header. */
export function SyncDot({ status }: { status: Status }) {
  const tone = status.sync_error
    ? "alarm"
    : status.espn_connected
      ? "turf"
      : "muted";
  const label = status.sync_error
    ? "sync failed"
    : status.espn_connected
      ? "live"
      : "manual";
  return (
    <span
      className={cn("flex items-center gap-1.5 text-[11px]",
        tone === "alarm" ? "text-alarm" : tone === "turf" ? "text-turf" : "text-muted")}
      title={status.sync_error ?? (status.espn_connected
        ? "Picks arrive from ESPN automatically"
        : "You are entering picks by hand")}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full",
        tone === "alarm" ? "bg-alarm" : tone === "turf" ? "bg-turf animate-pulse" : "bg-muted")} />
      {label}
    </span>
  );
}
