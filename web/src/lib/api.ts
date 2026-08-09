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
  planning?: boolean;
  live_overall?: number;
  my_picks?: { overall: number; round: number }[];
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
      player_id: string; player_name: string; vor: number; ecr: number | null;
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
    ecr: number | null; draft_rank: number | null; vor: number | null;
    rookie: boolean; drafted: boolean; headshot: string | null;
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

export interface PlayerProfile {
  player_id: string; player_name: string; position: string;
  headshot: string | null; rookie: boolean; drafted: boolean;
  adp: number | null; draft_rank: number | null; pos_rank: number | null;
  vor: number | null; projected_points: number | null;
  floor: number | null; median: number | null; ceiling: number | null;
  expected_games: number | null;
  td_share: number | null; rec_share: number | null;
  survives: number | null; picks_until_next: number | null;
  platform: string;
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


export interface TradePlayer {
  player_id: string; player_name: string; position: string; headshot?: string | null;
  projected_points?: number | null; vor?: number | null;
  season_p20?: number | null; season_p50?: number | null; season_p80?: number | null;
  expected_games?: number | null;
}

export interface TradeVerdict {
  delta_median: number;
  delta_floor: number;
  delta_ceiling: number;
  win_probability: number;
  before: { floor: number; median: number; ceiling: number; mean: number };
  after: { floor: number; median: number; ceiling: number; mean: number };
  give: TradePlayer[];
  get: TradePlayer[];
  dropped: TradePlayer[];
  naive_value_delta: number;
  opportunity_gap: number;
  roster_priced: boolean;
  pct_change?: number;
  per_week?: number;
  call?: "win" | "fair" | "loss";
  summary?: string;
  pros?: { stat: string | null; text: string }[];
  cons?: { stat: string | null; text: string }[];
  overlap?: {
    overlap: number; lo: number; hi: number;
    grid?: number[]; cdf_before?: number[]; cdf_after?: number[];
  };
  roster_before: number;
  roster_after: number;
  note: string;
}

export interface TradeOffer {
  team_id: number; team_name: string;
  give: TradePlayer[]; get: TradePlayer[];
  our_gain: number; their_gain: number;
  win_probability: number; naive_delta: number; note: string;
}

export interface SeasonStatus {
  synced: boolean; season?: number; week?: number | null;
  age_hours?: number; stale?: boolean;
  have?: string[]; not_published?: string[];
  has_results?: boolean; kickoff?: string; note?: string;
}

export interface LeagueRoster {
  team_id: number; name: string; mine: boolean;
  players: (TradePlayer & { lineup_slot: string | null; starting: boolean;
                            injury_status: string | null })[];
}


export interface TeamReport {
  empty: boolean;
  note?: string;
  grade: { score?: number; season_floor?: number; season_ceiling?: number;
           par?: number; unfilled?: Record<string, number> };
  starters: { player_id: string; player_name: string; position: string;
              slot?: string; projected_points?: number; vor?: number;
              headshot?: string | null; starting: boolean }[];
  bench: TeamReport["starters"];
  strength: { position: string; have: number; need: number; points: number;
              per_starter?: number; league_median: number; percentile: number;
              edge: number }[];
  byes: { week: number; count: number; players: string[]; points: number }[];
  draft: { picks: { overall: number; player_name: string; position: string | null;
                    adp: number; edge: number; verdict: string }[];
           total_edge: number; steals: number; reaches: number };
  strengths: string[];
  weaknesses: string[];
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    // A 404 on a route this build knows about means the engine is running
    // OLDER CODE than the interface. It is not a missing page and saying "not
    // found" sends you looking in the wrong place entirely -- a uvicorn here
    // sat up for four days answering 404 to every endpoint added in that time.
    if (res.status === 404) {
      throw new Error(
        "The engine is running older code than this page — restart it " +
        "(ctrl-c, then ./run.sh) and try again."
      );
    }
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
  suggestions: (n = 3, exclude: string[] = [], at?: number | null) =>
    req<Shortlist>(`/suggestions?n=${n}&exclude=${exclude.join(",")}`
      + (at ? `&at=${at}` : "")),
  pick: (body: { name?: string; player_id?: string; mine?: boolean }) =>
    req<Status>("/pick", { method: "POST", body: JSON.stringify(body) }),
  undo: () => req<Status>("/undo", { method: "POST" }),
  setSlot: (slot: number) => req<Status>(`/slot?slot=${slot}`, { method: "POST" }),
  teams: () => req<{ teams: TeamRow[] }>("/teams"),
  analytics: () => req<Analytics>("/analytics"),
  adp: (limit = 80) => req<AdpLadder>(`/adp?limit=${limit}`),
  player: (id: string) => req<PlayerProfile>(`/player/${id}`),
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
  tradeEvaluate: (give: string[], get: string[], roster: string[] = []) =>
    req<TradeVerdict>("/trade/evaluate", {
      method: "POST", body: JSON.stringify({ give, get, roster }) }),
  tradeSuggest: (perTeam = 1, top = 8, stance = "fair") =>
    req<{ offers: TradeOffer[]; through_week: number; note: string }>(
      `/trade/suggest?per_team=${perTeam}&top=${top}&stance=${stance}`),
  tradeCounter: (give: string[], get: string[], team_id: number, stance = "fair") =>
    req<{ offers: TradeOffer[] }>("/trade/counter", {
      method: "POST", body: JSON.stringify({ give, get, team_id, stance }) }),
  rosters: () => req<{ teams: LeagueRoster[]; as_of: number }>("/rosters"),
  season: () => req<SeasonStatus>("/season"),
  teamReport: () => req<TeamReport>("/team/report"),
  board: (pos?: string) => req<{ players: Suggestion[] }>(`/board${pos ? `?pos=${pos}` : ""}`),
};
