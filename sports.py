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
        # FLAT, deliberately. The slate endpoint returns every posted game for
        # 3 credits, so proximity tiers buy nothing here: with first pitches
        # every ~30 min there is always a game imminent, the near tier fires
        # all day, and spend runs ~470/day instead of ~280. Worse, the tier
        # keys off the SOONEST game -- once the last game starts, "soonest"
        # becomes tomorrow ~19h out and a far tier silently throttles the whole
        # board overnight while tomorrow's lines move. A single interval is
        # both cheaper and predictable. ~279 credits/day.
        "slate_cadence": [(999, 900)],
        # Per-game, so this is where MLB spend concentrates: 4 markets x 15
        # games. ~180 credits/day at these intervals.
        "event_cadence": [(1, 3600), (6, 10800), (999, None)],
        "spread_markets": {"spreads", "spreads_1st_5_innings"},
        "teams":  MLB_TEAMS,
        # Alerting. cents = moneyline move worth reporting; keys = numbers
        # whose crossing matters more than the distance moved.
        "alerts": {
            "cents": 10,
            "keys":  {"totals": [7, 7.5, 8, 8.5, 9],
                      "spreads": [1.5]},
        },
        # Daily sport: today plus tomorrow is the whole picture.
        "days_shown": 2,
        # Day files to read back. A side's record lives in the file for the day
        # its price last CHANGED, so this must reach back to when lines opened
        # or unmoved sides silently vanish from the board. Baseball posts a day
        # or two ahead, so 4 is ample.
        "history_days": 4,
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
        # Tiers DO earn their keep here: football games cluster on one day, so
        # "soonest game" is a real signal rather than always-imminent. Midweek
        # costs ~72/day, Saturday ~309/day.
        "slate_cadence": [(3, 600), (24, 1800), (999, 3600)],
        # 8 keys x 60 games is ~480 credits for ONE full pass, so period
        # markets only sweep inside 6h of kickoff and a Saturday clears in
        # waves. ~384 credits per Saturday.
        "event_cadence": [(2, 7200), (6, 21600), (999, None)],
        "spread_markets": {"spreads", "spreads_h1", "spreads_q1"},
        # No hard map: 130+ FBS schools plus whatever FCS games BetOnline
        # prices. Full names are shown until ESPN supplies abbreviations.
        "teams":  None,
        # Football prices run far longer than baseball's, so a 10-cent rule
        # would fire constantly on heavy favourites. The signal in football is
        # the spread crossing 3 or 7 -- the two most common margins of victory.
        "alerts": {
            "cents": 20,
            "keys":  {"spreads": [3, 7, 10, 14],
                      "spreads_h1": [3, 7],
                      "totals": [41, 44, 47, 51]},
        },
        # Weekly sport. Lines post ~a week out and the slate is one or two
        # days; a 2-day window would hide everything for most of the week.
        "days_shown": 9,
        # Football posts about a week out and a line can sit untouched for
        # days. With history_days=4 a Week 1 price written on the 22nd stopped
        # being read on the 26th and the game went blank. Must comfortably
        # exceed days_shown.
        "history_days": 14,
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
