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
  my_upcoming?: { overall: number; round: number }[];
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
  recent?: { overall: number; name: string; slot: number;
             round: number; mine: boolean }[];
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
  /** The average season rather than the middle one. They disagree exactly
   *  where a tail moved, which is the whole of the rookie penalty. */
  delta_mean?: number;
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
  /** Who the men you are receiving share a job with, and whether the price
   *  already knew. Facts, not an adjustment. */
  roles?: { player_id: string; player_name: string; position: string;
            team: string; depth_rank: number; expected_rank: number | null;
            ahead: string[]; behind: string[]; discount: number;
            injury_status?: string | null }[];
  /** The lineup card before and after — one cascade, not four separate gains. */
  moves?: { slot: string; position: string | null;
            out: string | null; out_id: string | null; out_points: number;
            in: string | null; in_id: string | null; in_points: number;
            waiver?: boolean; delta: number }[];
  /** What the same swap does to every other roster in the league. */
  general?: { median: number; low: number; high: number; teams: number;
              by_team?: Record<string, number> };
  /** Good trade, or good trade for YOU — the sentence for the gap. */
  fit?: string;
  /** Slots this trade leaves you filling off waivers, and by whom. */
  streamed?: { slot: string; player_id: string; player_name: string;
               position: string; points: number }[];
  note: string;
}

/** A version of the deal on the table with the numbers evened out. */
export interface Balanced extends TradeOffer {
  adds: { you_get: TradePlayer[]; you_give: TradePlayer[] };
  gap: number;
  /** Both sides land within noise of each other. False = closest possible. */
  even: boolean;
  why: string;
}

export interface TradeOffer {
  team_id: number; team_name: string;
  give: TradePlayer[]; get: TradePlayer[];
  our_gain: number; their_gain: number;
  /** What it does to THEIR starting lineup — the half of acceptance that
   *  understands a fourth receiver is worth less to a team starting three. */
  their_lineup?: number;
  win_probability: number; naive_delta: number; note: string;
}

/** A player named in the league read, with what he would add to a lineup. */
export interface Piece {
  player_id: string; player_name: string; position: string;
  projected_points: number; starting?: boolean;
  adds: number; starts_for_you?: boolean; over_replacement?: number;
}

/** A roster read on its own: strong where, thin where, who is spare. */
export interface Read {
  team_id: number | null; team_name: string; rank?: number; starters: number;
  strength: { position: string; edge: number; percentile: number;
              per_starter?: number; league_median: number }[];
  needs: string[];
  surplus: Piece[];
}

/** An opponent, which is a read plus what could cross the table. */
export interface TeamRead extends Read {
  team_id: number;
  get_from_them: Piece[];
  they_want_from_you: Piece[];
  fit: number;
  note: string;
}

/** What you told it was wrong with the last offer. */
export interface Ask {
  stance?: string; per_team?: number; top?: number; team_id?: number | null;
  keep?: string[]; want?: string[]; must_get?: string[];
  fewer?: boolean; richer?: boolean; harder?: boolean;
  seen?: string[]; note?: string;
}

export interface Scan {
  offers: TradeOffer[];
  keys: string[];
  me: Read;
  teams: TeamRead[];
  understood: string[];
  through_week: number;
  note: string;
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
  /** Starting slots with nobody in them, in lineup order. */
  gaps?: { slot: string; position: string }[];
  byes: { week: number; count: number; players: string[]; points: number }[];
  draft: { picks: { overall: number; player_id: string; player_name: string;
                    position: string | null;
                    adp: number; edge: number; verdict: string }[];
           total_edge: number; steals: number; reaches: number };
  pinned?: Record<string, string>;
  complete?: boolean;
  league?: { slot: number; mine: boolean; starters: number;
             floor: number | null; ceiling: number | null;
             players: number; rank: number;
             lineup?: { player_id: string; player_name: string;
                        position: string; projected_points?: number }[];
             shape?: { position: string; points: number; league: number;
                       edge: number }[] }[];
  improve?: { position: string; edge: number; percentile: number;
              replaces: number;
              available: { player_id: string; player_name: string;
                           position: string; ecr?: number | null;
                           projected_points?: number | null;
                           vor?: number | null }[] }[];
  team_names?: Record<string, string>;
  sleepers?: { player_id: string; player_name: string; position: string;
               ecr?: number | null; vor?: number | null;
               market_value?: number | null; market_edge: number;
               season_p20?: number | null; season_p80?: number | null }[];
  strengths: string[];
  weaknesses: string[];
}

export interface Suggestion2 {
  empty?: boolean;
  note?: string;
  week: number;
  pinned: Record<string, string>;
  changes: { slot: string | null; player_id: string | null;
             player_name: string | null; points: number;
             out_id: string | null; out_name: string | null;
             out_points: number; why: string }[];
  unavailable: { player_id: string; player_name: string; reason: string }[];
  byes: Record<string, string[]>;
}

export interface Performance {
  ready: boolean;
  week: number;
  scope?: string;
  kickoff?: string;
  note: string;
  rows: { player_id: string; player_name: string; position: string;
          ecr?: number | null; projected_points?: number | null;
          expected_ppg?: number | null; ppg?: number | null;
          games?: number | null; delta: number; cv?: number | null;
          headshot?: string | null; mine?: boolean }[];
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    // fetch itself failing is the same story as a 502: nothing is listening.
    throw new Error(
      "The engine is not running. Start it with ./run.sh in the project " +
      "folder, then try again."
    );
  }
  if (!res.ok) {
    // A 404 on a route this build knows about means the engine is running
    // OLDER CODE than the interface. It is not a missing page and saying "not
    // found" sends you looking in the wrong place entirely -- a uvicorn here
    // sat up for four days answering 404 to every endpoint added in that time.
    // 502/503/504 through the dev proxy means the ENGINE IS NOT RUNNING. The
    // browser reports "Bad Gateway", which reads as a server bug and sends you
    // looking at the league id, the cookies, ESPN — anywhere except the
    // process that is simply not there.
    if (res.status === 502 || res.status === 503 || res.status === 504) {
      throw new Error(
        "The engine is not running. Start it with ./run.sh in the project " +
        "folder, then try again."
      );
    }
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
  adp: (limit?: number) =>
    req<AdpLadder>(`/adp?upcoming_only=true`
      + (limit ? `&limit=${limit}` : "")),
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
  tradeScan: (ask: Ask = {}) =>
    req<Scan>("/trade/scan", {
      method: "POST",
      body: JSON.stringify({ per_team: 1, top: 8, stance: "fair", ...ask }) }),
  tradeBalance: (give: string[], get: string[], team_id: number | null,
                 extra: { roster?: string[]; their_roster?: string[] } = {}) =>
    req<{ offers: Balanced[]; before: { our_gain: number }; note: string }>(
      "/trade/balance", {
        method: "POST",
        body: JSON.stringify({ give, get, team_id, ...extra }) }),
  tradeCounter: (give: string[], get: string[], team_id: number | null,
                 ask: Ask & { roster?: string[]; their_roster?: string[] } = {}) =>
    req<{ offers: TradeOffer[]; keys: string[]; understood: string[] }>(
      "/trade/counter", {
        method: "POST",
        body: JSON.stringify({ give, get, team_id, stance: "fair", ...ask }) }),
  rosters: () => req<{ teams: LeagueRoster[]; as_of: number }>("/rosters"),
  season: () => req<SeasonStatus>("/season"),
  teamReport: () => req<TeamReport>("/team/report"),
  performance: (scope: "mine" | "league" = "mine") =>
    req<Performance>(`/performance?scope=${scope}`),
  teamSuggest: (week = 1) =>
    req<Suggestion2>(`/team/suggest?week=${week}`),
  rosterApply: (pinned: Record<string, string>) =>
    req<{ pinned: Record<string, string> }>("/roster/apply", {
      method: "POST", body: JSON.stringify({ pinned }) }),
  rosterSwap: (a: string, b: string) =>
    req<{ pinned: Record<string, string> }>("/roster/swap", {
      method: "POST", body: JSON.stringify({ a, b }) }),
  rosterUnpin: () =>
    req<{ pinned: Record<string, string> }>("/roster/unpin", { method: "POST" }),
  board: (pos?: string) => req<{ players: Suggestion[] }>(`/board${pos ? `?pos=${pos}` : ""}`),
};
