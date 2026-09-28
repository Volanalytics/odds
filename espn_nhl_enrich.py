"""
espn_nhl_enrich.py  --  live scores, period line and status for NHL
==================================================================
Free. No key, no quota, no effect on the odds-api budget.

Writes data/nhl/enrich.json keyed by odds-api event_id, in the same shape the
NCAAF enricher uses, so build_site.py and the page need no hockey-specific
reading code beyond period labels.

MATCHING: unlike college, NHL has a hard 32-club map in sports.py, so both
feeds are reduced to the same three-letter codes and matched exactly on
(away, home) plus a start time within a few hours. No fuzzy names. ESPN's own
abbreviations differ from the league's (LA / NJ / SJ / TB / UTAH), which is why
ESPN's display name is mapped through sports.NHL_TEAMS rather than trusting its
abbreviation.

  python espn_nhl_enrich.py
"""

import json
import os
import unicodedata
from datetime import datetime, timedelta, timezone

import requests

import sports

DATA_DIR = os.environ.get("ODDS_DATA_DIR", "data")
SPORT    = "nhl"
API      = ("https://site.api.espn.com/apis/site/v2/sports/"
            "hockey/nhl/scoreboard")

# ESPN abbreviation -> league code, used only if the display name misses.
ESPN_ABBR = {"LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL",
             "UTAH": "UTA", "UTA": "UTA", "MON": "MTL", "WAS": "WSH"}


def parse_iso(s):
    s = s.replace(".000Z", "Z")
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%MZ"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise ValueError(s)


def norm(s):
    """Lowercase, strip accents and periods: 'Montréal' / 'St. Louis' fold."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().replace(".", "").split())


NAME_TO_CODE = {norm(k): v for k, v in sports.NHL_TEAMS.items()}
VALID = set(sports.NHL_TEAMS.values())


def espn_code(team):
    """ESPN team dict -> league code, or None for a non-NHL opponent."""
    c = NAME_TO_CODE.get(norm(team.get("displayName")))
    if c:
        return c
    ab = (team.get("abbreviation") or "").upper()
    ab = ESPN_ABBR.get(ab, ab)
    return ab if ab in VALID else None


def summarize(ev):
    comp = (ev.get("competitions") or [{}])[0]
    status = (ev.get("status") or {}).get("type") or {}

    teams, ids, lines = {}, {}, {}
    for c in comp.get("competitors", []):
        t = c.get("team") or {}
        side = c.get("homeAway")
        if t.get("id"):
            ids[str(t["id"])] = side
        lines[side] = [ls.get("value") for ls in (c.get("linescores") or [])]
        teams[side] = {"code": espn_code(t), "score": c.get("score")}

    leaders = []
    for cat in (comp.get("leaders") or []):
        top = (cat.get("leaders") or [{}])[0]
        ath = top.get("athlete") or {}
        if ath.get("shortName") and top.get("displayValue"):
            leaders.append({
                "cat":  cat.get("shortDisplayName") or cat.get("name"),
                "who":  ath["shortName"],
                "team": ids.get(str((top.get("team") or {}).get("id"))),
                "val":  top["displayValue"],
            })

    state = status.get("state")          # pre | in | post
    return {
        "espn_id":   ev.get("id"),
        "line_away": lines.get("away") or [],
        "line_home": lines.get("home") or [],
        "leaders":   leaders[:4],
        "status":    status.get("description") or status.get("name"),
        # "12:34 - 2nd", "End of 2nd", "Final/OT", "Final/SO". The OT/SO
        # suffix matters on a hockey board: it decides regulation bets.
        "detail":    status.get("shortDetail"),
        "in_play":   state == "in",
        "final":     state == "post",
        "period":    (comp.get("status") or {}).get("period"),
        "clock":     (comp.get("status") or {}).get("displayClock"),
        "away_r":    (teams.get("away") or {}).get("score"),
        "home_r":    (teams.get("home") or {}).get("score"),
        "_away":     (teams.get("away") or {}).get("code"),
        "_home":     (teams.get("home") or {}).get("code"),
        "_start":    ev.get("date"),
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

    # game_date is the UTC date; a 10pm Central puck drop is tomorrow UTC.
    # Widen a day each side rather than miss those.
    dates = sorted({e["game_date"] for e in index.values()})
    lo = datetime.strptime(dates[0], "%Y-%m-%d") - timedelta(days=1)
    hi = datetime.strptime(dates[-1], "%Y-%m-%d") + timedelta(days=1)

    games, d = [], lo
    while d <= hi:
        try:
            r = requests.get(API, params={"dates": d.strftime("%Y%m%d"),
                                          "limit": 100}, timeout=30)
            r.raise_for_status()
            games += r.json().get("events", [])
        except requests.RequestException as e:
            print(f"  ESPN {d:%Y-%m-%d} failed ({e}); continuing")
        d += timedelta(days=1)
    print(f"  {len(games)} ESPN games {lo:%Y-%m-%d}..{hi:%Y-%m-%d} (cost 0)")

    by_pair = {}
    for s in map(summarize, games):
        if s["_away"] and s["_home"]:
            by_pair.setdefault((s["_away"], s["_home"]), []).append(s)

    out, missed = {}, []
    for eid, ev in index.items():
        target = parse_iso(ev["commence_time"])
        # A pair can meet twice in a window (home-and-home), so pick the
        # nearest start and require it to be within 6 hours.
        cands = []
        for s in by_pair.get((ev["away"], ev["home"]), []):
            try:
                gap = abs((parse_iso(s["_start"]) - target).total_seconds())
            except (ValueError, AttributeError):
                continue
            if gap < 6 * 3600:
                cands.append((gap, s))
        if not cands:
            missed.append(f"{ev['away']}@{ev['home']}")
            continue
        best = min(cands, key=lambda x: x[0])[1]
        out[eid] = {k: v for k, v in best.items() if not k.startswith("_")}

    os.makedirs(os.path.join(DATA_DIR, SPORT), exist_ok=True)
    dst = os.path.join(DATA_DIR, SPORT, "enrich.json")
    with open(dst + ".tmp", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    os.replace(dst + ".tmp", dst)

    live = sum(1 for v in out.values() if v["in_play"])
    print(f"  matched {len(out)}/{len(index)} events, {live} live")
    if missed:
        print(f"  unmatched ({len(missed)}): {', '.join(missed[:12])}")


if __name__ == "__main__":
    main()
