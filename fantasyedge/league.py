"""League settings as a runtime value, not a global constant.

Every valuation depends on league rules. Replacement level — the thing that
makes a WR comparable to a TE — is a pure function of team count and starting
lineup, so nothing downstream may assume a particular league. Anyone passes
their own settings and gets valuations correct for *their* league.

    twelve = LeagueSettings()                      # the common default
    ten    = LeagueSettings(n_teams=10)
    sflex  = LeagueSettings(lineup={..., "SUPERFLEX": 1})
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_LINEUP: dict[str, int] = {
    "QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1,
}

# How flex slots historically break down by position in PPR. Flex is
# overwhelmingly RB/WR; TEs rarely win the slot outside elite tiers.
FLEX_ALLOCATION: dict[str, float] = {"RB": 0.45, "WR": 0.45, "TE": 0.10}

SUPERFLEX_ALLOCATION: dict[str, float] = {"QB": 0.85, "RB": 0.05, "WR": 0.10}


@dataclass(frozen=True)
class LeagueSettings:
    """Everything about a league that changes what a player is worth."""

    n_teams: int = 12
    lineup: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_LINEUP))
    points_per_reception: float = 1.0
    roster_size: int = 16
    flex_eligible: tuple[str, ...] = ("RB", "WR", "TE")

    def __post_init__(self) -> None:
        if self.n_teams < 2:
            raise ValueError("n_teams must be at least 2")
        if not self.lineup:
            raise ValueError("lineup cannot be empty")

    # -- derived ----------------------------------------------------------

    @property
    def n_rounds(self) -> int:
        return self.roster_size

    @property
    def total_picks(self) -> int:
        return self.n_teams * self.n_rounds

    @property
    def is_superflex(self) -> bool:
        return "SUPERFLEX" in self.lineup or self.lineup.get("QB", 1) > 1

    @property
    def scoring_name(self) -> str:
        ppr = self.points_per_reception
        return {1.0: "full_ppr", 0.5: "half_ppr", 0.0: "standard"}.get(
            ppr, f"{ppr}_ppr"
        )

    def replacement_ranks(self) -> dict[str, int]:
        """Positional rank of the *last startable* player at each position.

        This is what makes cross-position comparison possible. In a 12-team
        league starting 2 RB, the 24th RB is the worst you'd ever be forced
        to start — so RB25's points are nearly free, and a RB is only worth
        what he produces *above* that line.

        Flex slots are allocated across eligible positions by how often each
        actually wins the slot, rather than assigning them all to one.
        """
        ranks: dict[str, float] = {}

        for slot, count in self.lineup.items():
            if slot in ("FLEX", "SUPERFLEX", "K", "DST"):
                continue
            ranks[slot] = ranks.get(slot, 0) + count * self.n_teams

        flex_slots = self.lineup.get("FLEX", 0) * self.n_teams
        if flex_slots:
            for pos, share in FLEX_ALLOCATION.items():
                if pos in self.flex_eligible:
                    ranks[pos] = ranks.get(pos, 0) + flex_slots * share

        sflex_slots = self.lineup.get("SUPERFLEX", 0) * self.n_teams
        if sflex_slots:
            for pos, share in SUPERFLEX_ALLOCATION.items():
                ranks[pos] = ranks.get(pos, 0) + sflex_slots * share

        return {pos: max(1, int(round(n))) for pos, n in ranks.items()}

    def starters_at(self, position: str) -> int:
        """Guaranteed (non-flex) starting slots at a position, league-wide."""
        return self.lineup.get(position, 0) * self.n_teams

    def describe(self) -> str:
        slots = " ".join(
            f"{n}{s}" for s, n in self.lineup.items() if n
        )
        return (
            f"{self.n_teams}-team {self.scoring_name} | {slots} | "
            f"{self.roster_size}-man rosters"
        )


# ---------------------------------------------------------------------------
# Snake draft mechanics
# ---------------------------------------------------------------------------

def overall_pick(round_no: int, pick_no: int, n_teams: int) -> int:
    """Convert round.pick into an overall pick number (both 1-indexed)."""
    if round_no < 1 or pick_no < 1 or pick_no > n_teams:
        raise ValueError(f"invalid pick {round_no}.{pick_no} for {n_teams} teams")
    return (round_no - 1) * n_teams + pick_no


def slot_for_pick(round_no: int, pick_no: int, n_teams: int) -> int:
    """Which draft slot owns round_no.pick_no, accounting for the snake.

    Odd rounds run 1..N, even rounds run N..1. The person picking 3rd in
    round 2 is the slot that picked N-2 in round 1.
    """
    return pick_no if round_no % 2 == 1 else n_teams - pick_no + 1


def picks_for_slot(slot: int, n_teams: int, n_rounds: int) -> list[int]:
    """Every overall pick number belonging to a draft slot."""
    picks = []
    for rnd in range(1, n_rounds + 1):
        pos = slot if rnd % 2 == 1 else n_teams - slot + 1
        picks.append(overall_pick(rnd, pos, n_teams))
    return picks


def picks_until_next(slot: int, current_overall: int, n_teams: int,
                     n_rounds: int) -> int | None:
    """How many other picks happen before this slot picks again.

    This is the number that should drive draft strategy. At the turn it can
    be 22 in a 12-team league; mid-round it can be 12. Taking a player who
    would still be there next time is a wasted pick, and the size of the gap
    is what decides that.
    """
    return gap_until_next(picks_for_slot(slot, n_teams, n_rounds),
                          current_overall)


def gap_until_next(owned: list[int], current_overall: int) -> int | None:
    """Same question, for a set of picks that need not follow the snake.

    People trade picks. Once they do, "your picks" is just a set of overall
    numbers and the tidy snake formula stops describing it -- trade away
    round five and the wait from round four doubles, which changes every
    survival probability and therefore the whole shortlist.
    """
    later = sorted(p for p in owned if p > current_overall)
    if not later:
        return None
    return later[0] - current_overall - 1
