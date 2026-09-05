"""
mlb_enrich.py  --  probable pitchers + live scores from the MLB Stats API
=========================================================================
Free. No key, no quota, no effect on the odds-api budget. One request covers
every game across the window, hydrated with probable pitchers and the linescore.

Writes data/mlb.json keyed by odds-api event_id:

    { "<event_id>": {
        "game_pk": 776543,               <- MLBAM id, joins to your Statcast DB
        "status": "In Progress",
        "away_p": "Chris Sale", "away_p_hand": "L",
        "home_p": "Jacob Misiorowski", "home_p_hand": "R",
        "away_r": 3, "home_r": 1,
        "inning": 6, "half": "Top", "outs": 1
    } }

MLB's terms allow individual, non-commercial, non-bulk use. One call per build
is well inside that.

  python mlb_enrich.py
"""

import json
import os
from datetime import datetime, timedelta, timezone

import requests

DATA_DIR = os.environ.get("ODDS_DATA_DIR", "data")
API      = "https://statsapi.mlb.com/api/v1/schedule"
CONTEXT  = "https://statsapi.mlb.com/api/v1/game/{pk}/contextMetrics"
PEOPLE   = "https://statsapi.mlb.com/api/v1/people"

TEAM_CODE = {
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
    "Oakland Athletics": "ATH", "Las Vegas Athletics": "ATH",
    "St Louis Cardinals": "STL", "Cleveland Indians": "CLE",
}


def parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def code(name):
    return TEAM_CODE.get(name, name)


def fetch_hands(ids):
    """
    Handedness in one batched call.

    The schedule endpoint's probablePitcher hydration returns id, fullName and
    link but NOT pitchHand, which is why the -L/-R suffix was always blank.
    /people accepts comma-separated personIds, so every starter on the slate
    costs a single extra request. Still free, still no key.
    """
    ids = [str(i) for i in ids if i]
    if not ids:
        return {}
    out = {}
    for i in range(0, len(ids), 60):          # keep the URL sane
        chunk = ids[i:i + 60]
        try:
            r = requests.get(PEOPLE, timeout=30,
                             params={"personIds": ",".join(chunk)})
            r.raise_for_status()
            for p in r.json().get("people", []):
                code_ = (p.get("pitchHand") or {}).get("code")
                if code_:
                    out[p["id"]] = code_
        except requests.RequestException as e:
            print(f"  handedness lookup failed ({e}); names will render bare")
    return out


def fetch_win_prob(pks):
    """
    Current win probability per team, one call per LIVE game.

    contextMetrics is the light endpoint -- the full winProbability endpoint
    returns every play of the game, which is a lot of payload for one number.
    Only live games are fetched: a scheduled game has no probability worth
    reading and a final one has collapsed to 0/100.
    """
    out = {}
    for pk in pks:
        try:
            r = requests.get(CONTEXT.format(pk=pk), timeout=20)
            r.raise_for_status()
            d = r.json() or {}
            home = d.get("homeWinProbability")
            away = d.get("awayWinProbability")
            if home is not None or away is not None:
                out[pk] = {"home": home, "away": away}
        except requests.RequestException:
            continue          # never fail the run over a nice-to-have
    return out


def fetch(start, end):
    r = requests.get(API, timeout=30, params={
        "sportId": 1, "startDate": start, "endDate": end,
        # linescore carries offense/defense, which is where the CURRENT
        # pitcher and batter live -- probablePitcher is only the starter.
        "hydrate": "probablePitcher,linescore,team",
    })
    r.raise_for_status()
    out = []
    for day in r.json().get("dates", []):
        out += day.get("games", [])
    return out


def fetch_hands(games):
    """
    probablePitcher hydration returns a stub (id, fullName, link) with no
    pitchHand, so handedness needs a second lookup. One batched /people call
    covers the whole slate -- still free, still one extra request.
    """
    ids = set()
    for g in games:
        for side in ("away", "home"):
            p = ((g.get("teams", {}).get(side) or {}).get("probablePitcher") or {})
            if p.get("id"):
                ids.add(p["id"])
    if not ids:
        return {}
    try:
        r = requests.get("https://statsapi.mlb.com/api/v1/people", timeout=30,
                         params={"personIds": ",".join(str(i) for i in sorted(ids))})
        r.raise_for_status()
        return {p["id"]: (p.get("pitchHand") or {}).get("code")
                for p in r.json().get("people", [])}
    except requests.RequestException as e:
        print(f"  handedness lookup failed ({e}); names only")
        return {}


def summarize(g, hands):
    ls  = g.get("linescore") or {}
    tms = g.get("teams", {})

    def pitcher(side):
        p = (tms.get(side) or {}).get("probablePitcher") or {}
        return p.get("fullName"), hands.get(p.get("id"))

    ap, ah = pitcher("away")
    hp, hh = pitcher("home")

    detailed = (g.get("status") or {}).get("detailedState")
    abstract = (g.get("status") or {}).get("abstractGameState")

    # Inning-by-inning plus R/H/E, for the box score panel.
    innings = [{"n": i.get("num"),
                "a": (i.get("away") or {}).get("runs"),
                "h": (i.get("home") or {}).get("runs")}
               for i in (ls.get("innings") or [])]
    lt = ls.get("teams") or {}

    # Who is actually on the mound right now, as opposed to who started.
    dfn = ls.get("defense") or {}
    off = ls.get("offense") or {}
    cur_p = (dfn.get("pitcher") or {}).get("fullName")
    cur_b = (off.get("batter") or {}).get("fullName")

    return {
        "game_pk":     g.get("gamePk"),
        "cur_pitcher": cur_p,
        "cur_batter":  cur_b,
        "innings":     innings,
        "rhe_away":    [(lt.get("away") or {}).get(k) for k in ("runs","hits","errors")],
        "rhe_home":    [(lt.get("home") or {}).get(k) for k in ("runs","hits","errors")],
        "status":      detailed,
        "abstract":    abstract,
        # MLB flips abstractGameState to Live during warmups, well before first
        # pitch. Split it out so the board can label warmup without painting
        # the row as an in-progress game.
        # MLB sets Pre-Game hours before first pitch, so labelling it "Warmup"
        # puts a warmup badge on a game three hours out. Only the real Warmup
        # state means players are on the field.
        "warmup":      detailed == "Warmup",
        # abstractGameState flips to Live during warmups too, well before the
        # first pitch, so a game in either pre-state must not read as in play.
        "in_play":     abstract == "Live" and detailed not in ("Warmup", "Pre-Game"),
        "away_p": ap, "away_p_hand": ah,
        "home_p": hp, "home_p_hand": hh,
        "away_r": (ls.get("teams", {}).get("away") or {}).get("runs"),
        "home_r": (ls.get("teams", {}).get("home") or {}).get("runs"),
        "inning": ls.get("currentInning"),
        "half":   ls.get("inningHalf"),
        "outs":   ls.get("outs"),
    }


def main():
    with open(os.path.join(DATA_DIR, "mlb", "events.json"), encoding="utf-8") as f:
        index = json.load(f)
    if not index:
        print("  events.json is empty -- run the poller first")
        return

    dates = sorted({e["game_date"] for e in index.values()})
    # game_date is the UTC date; a 10pm ET game files under tomorrow. Widen by a
    # day on each end so late games aren't missed.
    start = (datetime.strptime(dates[0], "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    end   = (datetime.strptime(dates[-1], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

    games = fetch(start, end)
    hands = fetch_hands(games)
    print(f"  {len(games)} MLB games {start}..{end}, "
          f"{len(hands)} pitcher hands (cost 0)")

    # Bucket by matchup; doubleheaders leave two entries under one key, so the
    # start time breaks the tie rather than whichever happened to come first.
    buckets = {}
    for g in games:
        t = g.get("teams", {})
        key = (code((t.get("away", {}).get("team") or {}).get("name", "")),
               code((t.get("home", {}).get("team") or {}).get("name", "")))
        buckets.setdefault(key, []).append(g)

    out, missed = {}, []
    for eid, ev in index.items():
        cands = buckets.get((ev["away"], ev["home"]))
        if not cands:
            missed.append(f"{ev['away']}@{ev['home']}")
            continue
        target = parse_iso(ev["commence_time"])
        best = min(cands, key=lambda g: abs(
            (parse_iso(g["gameDate"].replace(".000Z", "Z")) - target).total_seconds()))
        out[eid] = summarize(best, hands)

    os.makedirs(os.path.join(DATA_DIR, "mlb"), exist_ok=True)
    tmp = os.path.join(DATA_DIR, "mlb", "enrich.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    os.replace(tmp, os.path.join(DATA_DIR, "mlb", "enrich.json"))

    # Win probability for live games only, and a diff against the previous
    # poll so a pitching change is visible rather than inferred.
    live_pks = [v["game_pk"] for v in out.values() if v["in_play"] and v["game_pk"]]
    wp = fetch_win_prob(live_pks)

    prev_path = os.path.join(DATA_DIR, "mlb", "live_state.json")
    try:
        with open(prev_path, encoding="utf-8") as f:
            prev = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        prev = {}

    now_state = {}
    for eid, v in out.items():
        pk = str(v.get("game_pk"))
        if v.get("game_pk") in wp:
            v["wp_home"] = wp[v["game_pk"]]["home"]
            v["wp_away"] = wp[v["game_pk"]]["away"]
        if v.get("cur_pitcher"):
            now_state[pk] = v["cur_pitcher"]
            was = prev.get(pk)
            # A name change means the previous pitcher left. MLB does not say
            # why -- injury, matchup and pitch count look identical here.
            if was and was != v["cur_pitcher"]:
                v["pitcher_changed"] = True
                v["prev_pitcher"] = was

    tmp = prev_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(now_state, f, sort_keys=True)
    os.replace(tmp, prev_path)

    live = sum(1 for v in out.values() if v["in_play"])
    changed = sum(1 for v in out.values() if v.get("pitcher_changed"))
    pitch = sum(1 for v in out.values() if v["away_p"] and v["home_p"])
    print(f"  matched {len(out)}/{len(index)} events, {pitch} with both starters, "
          f"{live} live, {len(wp)} with win prob, {changed} pitching change(s)")
    if missed:
        print(f"  unmatched: {', '.join(missed)}")


if __name__ == "__main__":
    main()
