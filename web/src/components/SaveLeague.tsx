import { useState } from "react";
import { api, type Status } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Name this draft so it survives the tab closing.
 *
 * Once named it autosaves on every pick, so there is nothing else to press.
 * Unsaved drafts stay in memory only — which is fine for a two-minute mock
 * and useless for the real thing, so this nags gently until you name it.
 */
export function SaveLeague({ status, onSaved }: {
  status: Status;
  onSaved: (s: Status) => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(status.saved_name || status.league_name || "");
  const [busy, setBusy] = useState(false);

  const saved = !!status.league_id;

  async function save() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      onSaved(await api.saveLeague(name.trim()));
      setOpen(false);
    } finally { setBusy(false); }
  }

  if (saved && !open) {
    return (
      <button
        onClick={() => setOpen(true)}
        title="Autosaving. Click to rename."
        className="flex items-center gap-1.5 text-[11px] text-muted transition-colors hover:text-chalk"
      >
        <span className="h-1.5 w-1.5 rounded-full bg-turf/70" />
        {status.saved_name}
      </button>
    );
  }

  if (!open) {
    return (
      <Button size="sm" variant="outline" className="h-7 px-2 text-[11px]"
              onClick={() => setOpen(true)}>
        Save this draft
      </Button>
    );
  }

  return (
    <div className={cn("flex items-center gap-1.5")}>
      <input
        autoFocus
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") save();
          if (e.key === "Escape") setOpen(false);
        }}
        placeholder="Name this league"
        className="h-7 w-40 rounded border border-line bg-ink px-2 text-[11px] placeholder:text-muted/60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-turf/50"
      />
      <Button size="sm" className="h-7 px-2 text-[11px]" onClick={save}
              disabled={busy || !name.trim()}>
        Save
      </Button>
      <button onClick={() => setOpen(false)}
              className="px-1 text-[13px] leading-none text-muted hover:text-chalk">×</button>
    </div>
  );
}
