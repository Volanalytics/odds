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


# Display name for the card header: city, plus the club name only where a city
# fields two teams. "Chicago Cubs" and "Miami", not "CHC" and "MIA".
MLB_SHORT = {
    "Arizona Diamondbacks": "Arizona", "Atlanta Braves": "Atlanta",
    "Baltimore Orioles": "Baltimore", "Boston Red Sox": "Boston",
    "Chicago Cubs": "Chicago Cubs", "Chicago White Sox": "Chicago White Sox",
    "Cincinnati Reds": "Cincinnati", "Cleveland Guardians": "Cleveland",
    "Colorado Rockies": "Colorado", "Detroit Tigers": "Detroit",
    "Houston Astros": "Houston", "Kansas City Royals": "Kansas City",
    "Los Angeles Angels": "LA Angels", "Los Angeles Dodgers": "LA Dodgers",
    "Miami Marlins": "Miami", "Milwaukee Brewers": "Milwaukee",
    "Minnesota Twins": "Minnesota", "New York Mets": "NY Mets",
    "New York Yankees": "NY Yankees", "Athletics": "Athletics",
    "Philadelphia Phillies": "Philadelphia", "Pittsburgh Pirates": "Pittsburgh",
    "San Diego Padres": "San Diego", "San Francisco Giants": "San Francisco",
    "Seattle Mariners": "Seattle", "St. Louis Cardinals": "St. Louis",
    "Tampa Bay Rays": "Tampa Bay", "Texas Rangers": "Texas",
    "Toronto Blue Jays": "Toronto", "Washington Nationals": "Washington",
    "Oakland Athletics": "Athletics", "Las Vegas Athletics": "Athletics",
}


# NHL codes: the league's own three-letter abbreviations (LAK, NJD, SJS, TBL),
# which is what NHL.com and most hockey datasets key on. Hard map, same
# reasoning as MLB -- 32 clubs is small enough to be exact.
NHL_TEAMS = {
    "Anaheim Ducks": "ANA", "Boston Bruins": "BOS",
    "Buffalo Sabres": "BUF", "Calgary Flames": "CGY",
    "Carolina Hurricanes": "CAR", "Chicago Blackhawks": "CHI",
    "Colorado Avalanche": "COL", "Columbus Blue Jackets": "CBJ",
    "Dallas Stars": "DAL", "Detroit Red Wings": "DET",
    "Edmonton Oilers": "EDM", "Florida Panthers": "FLA",
    "Los Angeles Kings": "LAK", "Minnesota Wild": "MIN",
    "Montreal Canadiens": "MTL", "Nashville Predators": "NSH",
    "New Jersey Devils": "NJD", "New York Islanders": "NYI",
    "New York Rangers": "NYR", "Ottawa Senators": "OTT",
    "Philadelphia Flyers": "PHI", "Pittsburgh Penguins": "PIT",
    "San Jose Sharks": "SJS", "Seattle Kraken": "SEA",
    "St Louis Blues": "STL", "Tampa Bay Lightning": "TBL",
    "Toronto Maple Leafs": "TOR", "Utah Mammoth": "UTA",
    "Vancouver Canucks": "VAN", "Vegas Golden Knights": "VGK",
    "Washington Capitals": "WSH", "Winnipeg Jets": "WPG",
    # tolerated drift: accents, the period in St. Louis, Utah's first name
    "Montr\u00e9al Canadiens": "MTL", "St. Louis Blues": "STL",
    "Utah Hockey Club": "UTA", "Utah HC": "UTA",
}
assert len(set(NHL_TEAMS.values())) == 32, "NHL map must cover exactly 32 clubs"

NHL_SHORT = {name: city for name, city in [
    ("Anaheim Ducks", "Anaheim"), ("Boston Bruins", "Boston"),
    ("Buffalo Sabres", "Buffalo"), ("Calgary Flames", "Calgary"),
    ("Carolina Hurricanes", "Carolina"), ("Chicago Blackhawks", "Chicago"),
    ("Colorado Avalanche", "Colorado"), ("Columbus Blue Jackets", "Columbus"),
    ("Dallas Stars", "Dallas"), ("Detroit Red Wings", "Detroit"),
    ("Edmonton Oilers", "Edmonton"), ("Florida Panthers", "Florida"),
    ("Los Angeles Kings", "Los Angeles"), ("Minnesota Wild", "Minnesota"),
    ("Montreal Canadiens", "Montreal"), ("Montr\u00e9al Canadiens", "Montreal"),
    ("Nashville Predators", "Nashville"), ("New Jersey Devils", "New Jersey"),
    ("New York Islanders", "NY Islanders"), ("New York Rangers", "NY Rangers"),
    ("Ottawa Senators", "Ottawa"), ("Philadelphia Flyers", "Philadelphia"),
    ("Pittsburgh Penguins", "Pittsburgh"), ("San Jose Sharks", "San Jose"),
    ("Seattle Kraken", "Seattle"), ("St Louis Blues", "St. Louis"),
    ("St. Louis Blues", "St. Louis"), ("Tampa Bay Lightning", "Tampa Bay"),
    ("Toronto Maple Leafs", "Toronto"), ("Utah Mammoth", "Utah"),
    ("Utah Hockey Club", "Utah"), ("Utah HC", "Utah"),
    ("Vancouver Canucks", "Vancouver"), ("Vegas Golden Knights", "Vegas"),
    ("Washington Capitals", "Washington"), ("Winnipeg Jets", "Winnipeg"),
]}


# Books to request. 1-10 bookmakers count as ONE region, so a second book
# costs nothing extra -- the quota is [markets] x [regions], not per book.
# The first entry is primary and drives the board; the rest render alongside.
BOOKS = ["betonlineag", "kalshi"]


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
        "short":  MLB_SHORT,
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
    "nhl": {
        "key":    "icehockey_nhl",
        "label":  "NHL",
        "slate":  ["h2h", "spreads", "totals"],
        # Regulation 3-way (a tie after 60 is a Draw -- the market that
        # actually prices OT risk), 1st period, and team totals. As with NCAAF,
        # a key BetOnline doesn't post costs nothing, so listing it is safe.
        "event":  ["h2h_3_way", "h2h_p1", "spreads_p1", "totals_p1",
                   "team_totals"],
        "columns": [
            ("h2h",         "ML"),
            ("spreads",     "PL"),
            ("totals",      "Total"),
            ("h2h_3_way",   "Reg 3W"),
            ("h2h_p1",      "P1 ML"),
            ("spreads_p1",  "P1 PL"),
            ("totals_p1",   "P1 Tot"),
            ("team_totals", "TT"),
        ],
        # Flat for the same reason as MLB: a daily sport, so a proximity tier
        # keyed off the soonest game throttles the board overnight while the
        # next day's lines (and goalie news) move. 20 min rather than MLB's 15
        # -- hockey lines move far less between morning skate and puck drop.
        # ~216 credits/day.
        "slate_cadence": [(999, 1200)],
        # Per game. Goalie confirmations land in the last few hours, so that's
        # where the sweeps go. Roughly 3 sweeps x 5 keys x slate size;
        # ~120/day on an average 8-game night.
        "event_cadence": [(1, 3600), (6, 10800), (999, None)],
        "spread_markets": {"spreads", "spreads_p1"},
        "teams":  NHL_TEAMS,
        "short":  NHL_SHORT,
        # Puck line is almost always +/-1.5, so the point never crosses
        # anything, and the cents rule only watches money markets -- the ML
        # (and Reg 3W) carry the same information and do alert. Totals cluster
        # on 5.5-6.5; a move across 6 is the big one.
        "alerts": {
            "cents": 10,
            "keys":  {"totals": [5.5, 6, 6.5],
                      "totals_p1": [1.5]},
        },
        "days_shown": 2,
        "history_days": 4,
        "keep_hours": 12,
        "enrich": "espn_nhl",
    },
}

DEFAULT_ORDER = ["mlb", "nhl", "ncaaf"]


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
