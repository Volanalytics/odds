"""
sports.py  --  per-sport configuration shared by poller and site builder
========================================================================
One place to add a league. Everything downstream reads this.

COST SHAPE, which drives the cadence choices below:
  slate  /odds              = [markets SPECIFIED] x 1 region  -> 3 credits for
                              the WHOLE slate, however many games
  event  /events/{id}/odds  = [markets RETURNED]  x 1 region  -> per game

So featured markets are effectively free to scale, and per-event markets are
not. A 60-game NCAAF Saturday at 6 event markets is ~360 credits a sweep, which
is why ncaaf sweeps are throttled far harder than MLB's.
"""

# MLB codes, verified against pitches.home_team in the projection database.
# Hard map, no fuzzy matching -- 30 clubs is small enough to be exact, and
# name matching is where this kind of pipeline rots.
MLB_TEAMS = {
    "Arizona Diamondbacks": "AZ", "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC", "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL", "Detroit Tigers": "DET",
    "Houston Astros": "HOU", "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA", "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN", "New York Mets": "NYM",
    "New York Yankees": "NYY", "Athletics": "ATH",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SD", "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB", "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR", "Washington Nationals": "WSH",
    # tolerated drift
    "Oakland Athletics": "ATH", "Las Vegas Athletics": "ATH",
    "St Louis Cardinals": "STL", "Cleveland Indians": "CLE",
}
assert len(set(MLB_TEAMS.values())) == 30, "MLB map must cover exactly 30 clubs"


SPORTS = {
    "mlb": {
        "key":    "baseball_mlb",
        "label":  "MLB",
        "slate":  ["h2h", "spreads", "totals"],
        "event":  ["h2h_1st_5_innings", "spreads_1st_5_innings",
                   "totals_1st_5_innings", "team_totals"],
        "columns": [
            ("h2h",                   "ML"),
            ("spreads",               "RL"),
            ("totals",                "Total"),
            ("h2h_1st_5_innings",     "F5 ML"),
            ("spreads_1st_5_innings", "F5 RL"),
            ("totals_1st_5_innings",  "F5 Tot"),
            ("team_totals",           "TT"),
        ],
        # (hours_to_start_at_or_below, min_seconds_between_polls). None = skip.
        "slate_cadence": [(3, 300), (6, 900), (12, 1800), (999, 21600)],
        "event_cadence": [(1, 1800), (3, 3600), (6, 7200), (999, None)],
        "spread_markets": {"spreads", "spreads_1st_5_innings"},
        "teams":  MLB_TEAMS,
        # Daily sport: today plus tomorrow is the whole picture.
        "days_shown": 2,
        # Hours after first pitch to keep a finished game on the board.
        "keep_hours": 12,
        "enrich": "mlb",
    },

    "ncaaf": {
        "key":    "americanfootball_ncaaf",
        "label":  "NCAAF",
        "slate":  ["h2h", "spreads", "totals"],
        # 1H and 1Q plus team totals. Each key here costs 1 credit per game per
        # sweep, but only if BetOnline actually returns it -- unposted markets
        # are free, so listing them speculatively is safe.
        "event":  ["spreads_h1", "totals_h1", "h2h_h1",
                   "spreads_q1", "totals_q1", "h2h_q1",
                   "team_totals", "team_totals_h1"],
        "columns": [
            ("h2h",            "ML"),
            ("spreads",        "Spread"),
            ("totals",         "Total"),
            ("h2h_h1",         "1H ML"),
            ("spreads_h1",     "1H Spr"),
            ("totals_h1",      "1H Tot"),
            ("h2h_q1",         "1Q ML"),
            ("spreads_q1",     "1Q Spr"),
            ("totals_q1",      "1Q Tot"),
            ("team_totals",    "TT"),
            ("team_totals_h1", "1H TT"),
        ],
        # Slates are huge and mostly Saturday, so sweep sparingly.
        "slate_cadence": [(3, 300), (6, 1800), (24, 7200), (999, 43200)],
        "event_cadence": [(2, 7200), (6, 21600), (999, None)],
        "spread_markets": {"spreads", "spreads_h1", "spreads_q1"},
        # No hard map: 130+ FBS schools plus whatever FCS games BetOnline
        # prices. Full names are shown until ESPN supplies abbreviations.
        "teams":  None,
        # Weekly sport. Lines post ~a week out and the slate is one or two
        # days; a 2-day window would hide everything for most of the week.
        "days_shown": 9,
        # Football runs long and late kickoffs finish after midnight; 12h would
        # clear a Saturday night game before Sunday morning.
        "keep_hours": 16,
        "enrich": "espn_cfb",
    },
}

DEFAULT_ORDER = ["mlb", "ncaaf"]


def cfg(sport):
    if sport not in SPORTS:
        raise KeyError(f"unknown sport {sport!r}; known: {', '.join(SPORTS)}")
    return SPORTS[sport]


def to_code(conf, name):
    """
    Display code for a team.

    Sports with a hard map raise on an unknown name -- a silent passthrough
    would put a full club name in a column sized for three characters and
    quietly break the away/home ordering. Sports without one (college) return
    the name unchanged; fuzzy-matching hundreds of schools is how a board
    starts showing the wrong team.
    """
    teams = conf.get("teams")
    if not teams:
        return name
    code = teams.get(name)
    if code is None:
        raise KeyError(f"unmapped team name from API: {name!r}")
    return code
