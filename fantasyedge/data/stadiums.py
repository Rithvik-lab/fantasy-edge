"""Stadium coordinates, roof type, and timezone.

Needed for two feature families nflverse doesn't provide:

  weather  — Open-Meteo is queried by lat/long + kickoff time. Free, no key.
  travel   — distance and timezone delta between the two teams' home venues.
             A west-coast team playing a 1pm ET road game kicks off at 10am
             body clock, which is a real and measurable effect.

Roof matters more than most weather variables: it decides whether weather is
a factor at all. `pbp` carries a per-game roof field; this table is the
schedule-time prior, available before a season is played.
"""

from __future__ import annotations

import polars as pl

# team: (lat, lon, roof, tz, altitude_ft)
# roof: "dome" (always closed) | "retractable" | "outdoor"
# Team keys are nflverse abbreviations.
STADIUMS: dict[str, tuple[float, float, str, str, int]] = {
    "ARI": (33.5277, -112.2626, "retractable", "America/Phoenix", 1070),
    "ATL": (33.7554, -84.4008, "retractable", "America/New_York", 1050),
    "BAL": (39.2780, -76.6227, "outdoor", "America/New_York", 33),
    "BUF": (42.7738, -78.7870, "outdoor", "America/New_York", 600),
    "CAR": (35.2258, -80.8528, "outdoor", "America/New_York", 751),
    "CHI": (41.8623, -87.6167, "outdoor", "America/Chicago", 597),
    "CIN": (39.0955, -84.5161, "outdoor", "America/New_York", 490),
    "CLE": (41.5061, -81.6995, "outdoor", "America/New_York", 587),
    "DAL": (32.7473, -97.0945, "retractable", "America/Chicago", 600),
    "DEN": (39.7439, -105.0201, "outdoor", "America/Denver", 5280),
    "DET": (42.3400, -83.0456, "dome", "America/New_York", 600),
    "GB": (44.5013, -88.0622, "outdoor", "America/Chicago", 640),
    "HOU": (29.6847, -95.4107, "retractable", "America/Chicago", 50),
    "IND": (39.7601, -86.1639, "retractable", "America/New_York", 715),
    "JAX": (30.3239, -81.6373, "outdoor", "America/New_York", 16),
    "KC": (39.0489, -94.4839, "outdoor", "America/Chicago", 750),
    "LA": (33.9535, -118.3392, "dome", "America/Los_Angeles", 100),
    "LAC": (33.9535, -118.3392, "dome", "America/Los_Angeles", 100),
    "LV": (36.0909, -115.1833, "dome", "America/Los_Angeles", 2030),
    "MIA": (25.9580, -80.2389, "outdoor", "America/New_York", 8),
    "MIN": (44.9738, -93.2578, "dome", "America/Chicago", 830),
    "NE": (42.0909, -71.2643, "outdoor", "America/New_York", 285),
    "NO": (29.9511, -90.0812, "dome", "America/Chicago", 3),
    "NYG": (40.8135, -74.0745, "outdoor", "America/New_York", 7),
    "NYJ": (40.8135, -74.0745, "outdoor", "America/New_York", 7),
    "PHI": (39.9008, -75.1675, "outdoor", "America/New_York", 39),
    "PIT": (40.4468, -80.0158, "outdoor", "America/New_York", 730),
    "SEA": (47.5952, -122.3316, "outdoor", "America/Los_Angeles", 20),
    "SF": (37.4033, -121.9694, "outdoor", "America/Los_Angeles", 26),
    "TB": (27.9759, -82.5033, "outdoor", "America/New_York", 26),
    "TEN": (36.1665, -86.7713, "outdoor", "America/Chicago", 400),
    "WAS": (38.9077, -76.8645, "outdoor", "America/New_York", 200),
}

# Legacy / alternate abbreviations that appear in older nflverse seasons.
ALIASES = {
    "OAK": "LV", "SD": "LAC", "STL": "LA", "LAR": "LA", "WSH": "WAS", "JAC": "JAX",
}

OUTDOOR_COLD_TEAMS = ("BUF", "CHI", "CLE", "DEN", "GB", "KC", "NE", "NYG", "NYJ", "PIT", "WAS")


def normalize(team: str) -> str:
    return ALIASES.get(team, team)


def frame() -> pl.DataFrame:
    """Stadium table as a polars DataFrame, ready to join on home team."""
    return pl.DataFrame(
        [
            {
                "team": team,
                "lat": lat,
                "lon": lon,
                "roof": roof,
                "tz": tz,
                "altitude_ft": alt,
                "is_indoor": roof == "dome",
                "is_cold_weather": team in OUTDOOR_COLD_TEAMS,
            }
            for team, (lat, lon, roof, tz, alt) in STADIUMS.items()
        ]
    )


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance — the travel-burden feature."""
    from math import asin, cos, radians, sin, sqrt

    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * asin(sqrt(a))


def travel_miles(away_team: str, home_team: str) -> float | None:
    """Miles the away team travelled. None if either team is unknown."""
    a, h = normalize(away_team), normalize(home_team)
    if a not in STADIUMS or h not in STADIUMS:
        return None
    return haversine_miles(STADIUMS[a][0], STADIUMS[a][1], STADIUMS[h][0], STADIUMS[h][1])
