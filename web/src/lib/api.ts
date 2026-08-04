export interface Suggestion {
  player_id: string;
  player_name: string;
  position: string;
  ecr: number | null;
  vor: number;
  p_survive: number;
  score: number;
  now: number;
  next_pick: number;
  floor: number | null;
  ceiling: number | null;
  dropoff: number;
  vol_pct: number | null;
  rookie?: boolean;
  td_share?: number | null;
  rec_share?: number | null;
  headline: string;
  reasons: string[];
  headshot: string | null;
}

export interface Shortlist {
  round: number;
  pick: number;
  overall: number;
  picks_until_next: number | null;
  target_vol_pct: number;
  roster_risk: number | null;
  needs: Record<string, number>;
  compare: string;
  suggestions: Suggestion[];
  note?: string;
}

export interface SearchHit {
  player_id: string;
  player_name: string;
  position: string;
  ecr: number | null;
  vor: number | null;
  drafted: boolean;
  headshot: string | null;
}

export interface Analytics {
  picks_until_next: number | null;
  tiers: {
    position: string;
    players: {
      player_name: string; vor: number; ecr: number | null;
      floor: number | null; median: number | null; ceiling: number | null;
      survives: number | null;
    }[];
  }[];
  scarcity: {
    position: string; startable_left: number; league_slots: number;
    already_drafted: number; still_needed: number;
  }[];
}

export interface AdpLadder {
  platform: string;
  overall: number;
  my_next: number | null;
  players: {
    player_id: string; player_name: string; position: string;
    ecr: number | null; vor: number | null; rookie: boolean;
    drafted: boolean; headshot: string | null;
  }[];
}

export interface SavedLeague {
  id: string;
  name: string;
  platform: string;
  n_teams: number | null;
  roster_size: number | null;
  my_slot: number | null;
  espn_league_id: string | null;
  picks_made: number;
  total_picks: number;
  complete: boolean;
  saved_at: number | null;
}

export interface RosterView {
  grade: Record<string, unknown>;
  starters: { slot?: string; player_name: string; position: string;
              vor: number; projected_points: number; player_id: string;
              season_p20?: number; season_p80?: number }[];
  bench: RosterView["starters"];
}

export interface Status {
  configured: boolean;
  platform?: string;
  league_id?: string | null;
  saved_name?: string;
  phase?: "pre" | "live" | "complete";
  slot_confirmed?: boolean;
  draft_complete?: boolean;
  warnings?: string[];
  league_name?: string;
  describe?: string;
  n_teams?: number;
  n_rounds?: number;
  lineup?: Record<string, number>;
  my_slot?: number;
  my_picks?: number[];
  picks_traded?: boolean;
  draft_time?: number | null;
  seconds_to_draft?: number | null;
  round?: number;
  pick?: number;
  overall?: number;
  on_the_clock?: boolean;
  my_next_pick?: number | null;
  picks_until_next?: number | null;
  picks_made?: number;
  espn_connected?: boolean;
  last_sync?: number;
  sync_error?: string | null;
  recent?: { overall: number; name: string; slot: number; mine: boolean }[];
}

export interface TeamRow {
  slot: number;
  name: string;
  mine: boolean;
  counts: Record<string, number>;
  unfilled: Record<string, number>;
  players: { name: string; position: string | null; overall: number }[];
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* body was not json */
    }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  status: () => req<Status>("/status"),
  setLeague: (body: unknown) =>
    req<Status>("/league", { method: "POST", body: JSON.stringify(body) }),
  connectEspn: (body: unknown) =>
    req<Status>("/espn/connect", { method: "POST", body: JSON.stringify(body) }),
  sync: () => req<Status>("/espn/sync", { method: "POST" }),
  suggestions: (n = 3, exclude: string[] = []) =>
    req<Shortlist>(`/suggestions?n=${n}&exclude=${exclude.join(",")}`),
  pick: (body: { name?: string; player_id?: string; mine?: boolean }) =>
    req<Status>("/pick", { method: "POST", body: JSON.stringify(body) }),
  undo: () => req<Status>("/undo", { method: "POST" }),
  setSlot: (slot: number) => req<Status>(`/slot?slot=${slot}`, { method: "POST" }),
  teams: () => req<{ teams: TeamRow[] }>("/teams"),
  analytics: () => req<Analytics>("/analytics"),
  adp: (limit = 80) => req<AdpLadder>(`/adp?limit=${limit}`),
  search: (q: string) => req<{ players: SearchHit[] }>(`/search?q=${encodeURIComponent(q)}`),
  setOwnedPicks: (picks: number[]) =>
    req<Status>("/picks/mine", { method: "POST", body: JSON.stringify({ picks }) }),
  resetOwnedPicks: () => req<Status>("/picks/reset", { method: "POST" }),
  roster: () => req<RosterView>("/roster"),
  removePick: (player_id: string) =>
    req<Status>("/pick/remove", { method: "POST", body: JSON.stringify({ player_id }) }),
  leagues: () => req<{ leagues: SavedLeague[]; active: string | null }>("/leagues"),
  saveLeague: (name: string) =>
    req<Status>("/leagues/save", { method: "POST", body: JSON.stringify({ name }) }),
  loadLeague: (id: string) => req<Status>(`/leagues/${id}/load`, { method: "POST" }),
  deleteLeague: (id: string) =>
    req<{ leagues: SavedLeague[] }>(`/leagues/${id}`, { method: "DELETE" }),
  board: (pos?: string) => req<{ players: Suggestion[] }>(`/board${pos ? `?pos=${pos}` : ""}`),
};
