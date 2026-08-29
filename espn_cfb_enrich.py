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

# groups=80 is FBS. Left off deliberately: BetOnline prices some FCS games and
# omitting the filter returns all divisions.
PARAMS = {"limit": 300}


def parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


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
    s = re.sub(r"\bst\.?\b", "saint", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def fetch_day(day):
    p = dict(PARAMS, dates=day)
    r = requests.get(API, params=p, timeout=30)
    r.raise_for_status()
    return r.json().get("events", [])


def summarize(ev):
    comp = (ev.get("competitions") or [{}])[0]
    venue = comp.get("venue") or {}
    addr = venue.get("address") or {}
    status = (ev.get("status") or {}).get("type") or {}

    teams = {}
    for c in comp.get("competitors", []):
        t = c.get("team") or {}
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

    return {
        "espn_id":  ev.get("id"),
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
        "away_rank": (teams.get("away") or {}).get("rank"),
        "home_rank": (teams.get("home") or {}).get("rank"),
        "away_r":   (teams.get("away") or {}).get("score"),
        "home_r":   (teams.get("home") or {}).get("score"),
        "_names":   {k: [v.get("name"), v.get("short")] for k, v in teams.items()},
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

    out, missed = {}, []
    for eid, ev in index.items():
        a, h = lookup.get(norm(ev["away_team"])), lookup.get(norm(ev["home_team"]))
        both = (a & h) if (a and h) else set()
        if not both:
            missed.append(f"{ev['away_team']} @ {ev['home_team']}")
            continue
        # A pairing can theoretically repeat (neutral-site doubleheaders are
        # not a thing, but rescheduled games are); take the closest kickoff.
        cands = [by_id[i] for i in both]
        best = cands[0]
        rec = {k: v for k, v in best.items() if not k.startswith("_")}
        out[eid] = rec

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
