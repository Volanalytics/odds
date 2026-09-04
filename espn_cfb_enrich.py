"""
espn_cfb_enrich.py  --  venue, neutral-site flag and scores for NCAAF
=====================================================================
Free. No key, no quota, no effect on the odds-api budget.

WHY THIS EXISTS: the odds feed gives home_team and away_team, but for a neutral
game that designation is arbitrary -- Week 0 in Dublin, conference title games,
and every bowl come through looking like ordinary home/away. ESPN's scoreboard
exposes competitions[0].neutralSite directly, along with the venue, so the board
can label them instead of you having to recognise them by matchup.

It also supplies canonical team abbreviations, which matters because there is no
hard team map for college football -- 130+ FBS schools plus whatever FCS games
BetOnline prices.

Writes data/ncaaf/espn.json keyed by odds-api event_id.

  python espn_cfb_enrich.py
"""

import json
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone

import requests

DATA_DIR = os.environ.get("ODDS_DATA_DIR", "data")
SPORT    = "ncaaf"
API      = ("https://site.api.espn.com/apis/site/v2/sports/"
            "football/college-football/scoreboard")

# ESPN's college football scoreboard defaults to FBS. Omitting the filter does
# NOT return everything -- FCS games simply never appear, so BetOnline's
# Arkansas Pine Bluff / Missouri type matchups could never match and fell back
# to full school names in the odds cells. 80 = FBS, 81 = FCS.
GROUPS = ["80", "81"]
PARAMS = {"limit": 300}


def parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


# Schools the two feeds spell differently enough that normalisation alone
# won't bridge them. Keys and values are both normalised forms.
ALIAS = {
    "umass": "massachusetts", "uconn": "connecticut",
    "ucf": "central florida", "usf": "south florida",
    "utsa": "texas san antonio", "utep": "texas el paso",
    "unlv": "nevada las vegas", "usc": "southern california",
    "ole miss": "mississippi", "app state": "appalachian state",
    "fiu": "florida international", "fau": "florida atlantic",
    "southern miss": "southern mississippi",
    "ul monroe": "louisiana monroe", "ull": "louisiana",
    "louisiana lafayette": "louisiana",
    "middle tennessee state": "middle tennessee",
    "nc state": "north carolina state",
    "pitt": "pittsburgh", "ul lafayette": "louisiana",
    # FCS schools BetOnline prices against FBS hosts, where the two feeds
    # disagree on the school name or ESPN uses the post-rename form.
    "houston baptist": "houston christian",
    "liu": "long island university",
    "albany": "ualbany",
    "citadel": "the citadel",
    "nicholls state": "nicholls",
    "sam houston state": "sam houston",
}


def canon(s):
    """
    Fold a normalised name onto one spelling both feeds agree on.

    Returns on the FIRST hit. The earlier version kept scanning after a
    substitution, so "sam houston state bearkats" matched the "sam houston"
    prefix and became "sam houston state state bearkats".
    """
    if s in ALIAS:
        return ALIAS[s]
    for k, v in ALIAS.items():
        if s.startswith(k + " "):
            return v + s[len(k):]
    return s


def norm(s):
    """
    Squash a school name to something comparable across two feeds.

    The odds API and ESPN disagree constantly on punctuation, ampersands and
    saint abbreviations -- "Texas A&M" / "Texas A&M Aggies", "St. John's" /
    "Saint Johns". Lowercase, strip accents and punctuation, collapse spaces.
    """
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ")
    # "St" is Saint at the start of a name (St. John's) and State anywhere
    # else (Youngstown St). Expanding it to "saint" everywhere is why the FCS
    # visitors failed to match.
    s = re.sub(r"^st\.?\b", "saint", s)
    s = re.sub(r"\bst\.?\b", "state", s)
    # Apostrophes are deleted so "John's" -> "johns" (matching ESPN's
    # "Johns"); every other separator becomes a space so "Arkansas-Pine Bluff"
    # keeps its word break instead of collapsing to "arkansaspine".
    s = s.replace("'", "").replace("\u2019", "")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return canon(re.sub(r"\s+", " ", s).strip())


def fetch_day(day):
    """All divisions for one date. Free, so the extra request costs nothing."""
    out, seen = [], set()
    for grp in GROUPS:
        p = dict(PARAMS, dates=day, groups=grp)
        r = requests.get(API, params=p, timeout=30)
        r.raise_for_status()
        for ev in r.json().get("events", []):
            if ev.get("id") not in seen:
                seen.add(ev.get("id"))
                out.append(ev)
    return out


def summarize(ev):
    comp = (ev.get("competitions") or [{}])[0]
    venue = comp.get("venue") or {}
    addr = venue.get("address") or {}
    status = (ev.get("status") or {}).get("type") or {}

    teams, ids = {}, {}
    for c in comp.get("competitors", []):
        t = c.get("team") or {}
        if t.get("id"):
            ids[str(t["id"])] = c.get("homeAway")
        teams[c.get("homeAway")] = {
            "abbr":  t.get("abbreviation"),
            "name":  t.get("displayName"),
            "short": t.get("shortDisplayName"),
            "rank":  c.get("curatedRank", {}).get("current"),
            "score": c.get("score"),
        }

    detailed = status.get("description") or status.get("name")
    state = status.get("state")          # pre | in | post
    # ESPN already formats the game clock the way a scoreboard reads it --
    # "14:15 - 2nd", "Halftime", "End of 3rd" -- including the cases a
    # period+clock pair can't express on its own.
    short = status.get("shortDetail")

    # Live drive state. Only present while a game is in progress; possession
    # is a team id, so it has to be resolved against the competitor list.
    sit = comp.get("situation") or {}
    poss = ids.get(str(sit.get("possession"))) if sit.get("possession") else None

    return {
        "espn_id":  ev.get("id"),
        "poss":     poss,                          # "away" | "home" | None
        "down":     sit.get("downDistanceText"),   # "2nd & 7"
        "spot":     sit.get("possessionText"),     # "RUTG 35"
        "redzone":  bool(sit.get("isRedZone")),
        "neutral":  bool(comp.get("neutralSite")),
        "conf_comp": bool(comp.get("conferenceCompetition")),
        "venue":    venue.get("fullName"),
        "city":     addr.get("city"),
        "state":    addr.get("state"),
        "status":   detailed,
        "detail":   short,
        "in_play":  state == "in",
        "final":    state == "post",
        "period":   comp.get("status", {}).get("period"),
        "clock":    comp.get("status", {}).get("displayClock"),
        "away_abbr": (teams.get("away") or {}).get("abbr"),
        "home_abbr": (teams.get("home") or {}).get("abbr"),
        # School without the mascot -- "UMass", "Wake Forest". Reads better on
        # a phone than a four-letter code, and far better than the full name.
        "away_short": (teams.get("away") or {}).get("short"),
        "home_short": (teams.get("home") or {}).get("short"),
        "away_rank": (teams.get("away") or {}).get("rank"),
        "home_rank": (teams.get("home") or {}).get("rank"),
        "away_r":   (teams.get("away") or {}).get("score"),
        "home_r":   (teams.get("home") or {}).get("score"),
        "_names":   {k: [v.get("name"), v.get("short")] for k, v in teams.items()},
        "_start":   ev.get("date"),
    }


def main():
    events_path = os.path.join(DATA_DIR, SPORT, "events.json")
    try:
        with open(events_path, encoding="utf-8") as f:
            index = json.load(f)
    except FileNotFoundError:
        print(f"  {events_path} not found -- run the poller first")
        return
    if not index:
        print("  no events")
        return

    dates = sorted({e["game_date"] for e in index.values()})
    # game_date is the UTC date; a 7pm Saturday kickoff on the west coast is
    # Sunday UTC. Widen a day each side rather than miss those.
    lo = (datetime.strptime(dates[0], "%Y-%m-%d") - timedelta(days=1))
    hi = (datetime.strptime(dates[-1], "%Y-%m-%d") + timedelta(days=1))

    games = []
    d = lo
    while d <= hi:
        try:
            games += fetch_day(d.strftime("%Y%m%d"))
        except requests.RequestException as e:
            print(f"  ESPN {d:%Y-%m-%d} failed ({e}); continuing")
        d += timedelta(days=1)

    print(f"  {len(games)} ESPN games {lo:%Y-%m-%d}..{hi:%Y-%m-%d} (cost 0)")

    summaries = [summarize(g) for g in games]

    # Index every name variant ESPN gives, so a match can hit on either the
    # full display name or the short one.
    lookup = {}
    for s in summaries:
        for side, names in s["_names"].items():
            for n in names:
                if n:
                    lookup.setdefault(norm(n), set()).add(id(s))
    by_id = {id(s): s for s in summaries}

    # ESPN kickoff times, for the single-team fallback below.
    when = {}
    for s_ in summaries:
        try:
            when[id(s_)] = parse_iso(s_["_start"].replace(".000Z", "Z"))
        except Exception:
            pass

    out, missed = {}, []
    for eid, ev in index.items():
        a = lookup.get(norm(ev["away_team"])) or set()
        h = lookup.get(norm(ev["home_team"])) or set()
        target = parse_iso(ev["commence_time"])

        cands = [by_id[i] for i in (a & h)]
        if not cands:
            # Fall back to ONE side matching. An FCS visitor at an FBS host is
            # the common case: the host always matches, the visitor often
            # doesn't, and a given team plays once a day -- so the host plus a
            # kickoff within a few hours identifies the game unambiguously.
            near = [by_id[i] for i in (a | h)
                    if id(by_id[i]) in when
                    and abs((when[id(by_id[i])] - target).total_seconds()) < 4 * 3600]
            cands = near

        if not cands:
            missed.append(f"{ev['away_team']} @ {ev['home_team']}")
            continue

        best = min(cands, key=lambda g: abs(
            (when.get(id(g), target) - target).total_seconds()))
        out[eid] = {k: v for k, v in best.items() if not k.startswith("_")}

    os.makedirs(os.path.join(DATA_DIR, SPORT), exist_ok=True)
    dst = os.path.join(DATA_DIR, SPORT, "enrich.json")
    tmp = dst + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    os.replace(tmp, dst)

    neutral = sum(1 for v in out.values() if v["neutral"])
    live    = sum(1 for v in out.values() if v["in_play"])
    print(f"  matched {len(out)}/{len(index)} events, "
          f"{neutral} neutral site, {live} live")
    if missed:
        print(f"  unmatched ({len(missed)}):")
        for m in missed[:12]:
            print(f"    {m}")


if __name__ == "__main__":
    main()
