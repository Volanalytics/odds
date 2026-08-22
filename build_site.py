"""
build_site.py  --  emit site/data.json from the NDJSON store
============================================================
Reads data/events.json plus the day files in data/odds/ and derives, per side:

  open      earliest price recorded
  cur       latest
  moved_min minutes since the price last CHANGED (drives recency coloring)
  hist      every price the side has held, newest first

Openers only reach back to the first poll that saw the market. To capture
BetOnline's true opening number, be polling before they post.

  python build_site.py --outdir site
"""

import argparse
import json
import os
import shutil
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DATA_DIR = os.environ.get("ODDS_DATA_DIR", "data")

# Display zone for game times and the build stamp. The Actions runner is UTC,
# so this must be set explicitly or every time on the board will be wrong.
LOCAL_TZ = ZoneInfo(os.environ.get("ODDS_TZ", "America/New_York"))

MARKETS = [
    ("h2h",                   "ML"),
    ("spreads",               "RL"),
    ("totals",                "Total"),
    ("h2h_1st_5_innings",     "F5 ML"),
    ("spreads_1st_5_innings", "F5 RL"),
    ("totals_1st_5_innings",  "F5 Tot"),
    ("team_totals",           "TT"),
]

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


def stamp(iso):
    return parse_iso(iso).astimezone(LOCAL_TZ).strftime("%m/%d %I:%M:%S %p")


def fmt_price(p):
    return "" if p is None else (f"+{p}" if p > 0 else str(p))


def fmt_point(v):
    """Half lines print as 8.5, whole numbers as 8. Moneylines have no point."""
    if v is None:
        return ""
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def fmt_line(r):
    return f"{fmt_point(r['point'])} {fmt_price(r['price'])}".strip()


def side_label(r):
    """
    h2h / spreads -> team code (outcome holds the club name)
    totals        -> Over / Under
    team_totals   -> team code + Over/Under (the TEAM lives in desc)
    """
    if r["market"] == "team_totals":
        return f"{TEAM_CODE.get(r['desc'], r['desc'])} {r['outcome']}"
    if r["outcome"] in ("Over", "Under"):
        return r["outcome"]
    return TEAM_CODE.get(r["outcome"], r["outcome"])


def load_records(days):
    out = []
    for d in days:
        path = os.path.join(DATA_DIR, "odds", f"{d}.ndjson")
        try:
            with open(path, encoding="utf-8") as f:
                out += [json.loads(l) for l in f if l.strip()]
        except FileNotFoundError:
            continue
    return out


def load_mlb():
    """Optional: absent on a first run, so the board degrades to odds only."""
    try:
        with open(os.path.join(DATA_DIR, "mlb.json"), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def build(days_shown=2, history_days=4):
    with open(os.path.join(DATA_DIR, "events.json"), encoding="utf-8") as f:
        index = json.load(f)
    mlb = load_mlb()

    today   = datetime.now(LOCAL_TZ).date()
    horizon = (today + timedelta(days=days_shown - 1)).isoformat()
    cutoff  = today.isoformat()

    # Read a few days back: a game tonight may have opened days ago, and the
    # opener column should reach that far.
    hist_days = [(today - timedelta(days=k)).isoformat()
                 for k in range(history_days, -2, -1)]

    by_event = defaultdict(list)
    for r in load_records(hist_days):
        by_event[r["event_id"]].append(r)

    now, games = datetime.now(timezone.utc), []

    for eid, ev in index.items():
        if not (cutoff <= ev["game_date"] <= horizon):
            continue
        rows = by_event.get(eid, [])
        if not rows:
            continue

        # A "side" is one bettable option tracked over time. point is
        # deliberately NOT in the key -- a total moving 8.5 -> 9 is the same
        # Over changing price, not a new market. Key on point and every move
        # looks like a fresh side with no history, defeating the board.
        by_side = defaultdict(list)
        for r in rows:
            by_side[(r["market"], r["outcome"], r["desc"])].append(r)

        markets, newest, moves = {}, None, 0

        for (market, _, _), seq in by_side.items():
            # ts is ISO-8601, so lexical sort is chronological. Never sort on a
            # formatted 12-hour stamp: "01:10 PM" sorts before "09:30 AM".
            seq.sort(key=lambda r: r["ts"])
            first, last = seq[0], seq[-1]
            changed = len(seq) > 1
            moved_at = parse_iso(last["ts"])

            if changed:
                moves += 1
                if newest is None or moved_at > newest:
                    newest = moved_at

            side = {
                "label":     side_label(last),
                "open":      fmt_line(first),
                "cur":       fmt_line(last),
                "changed":   changed,
                "moved_min": round((now - moved_at).total_seconds() / 60, 1),
                "opened_at": stamp(first["ts"]),
            }
            if changed:
                side["hist"] = [{"ts": stamp(r["ts"]), "line": fmt_line(r)}
                                for r in reversed(seq)]
            markets.setdefault(market, []).append(side)

        # Stable column order within each cell.
        for v in markets.values():
            v.sort(key=lambda s: s["label"])

        m = mlb.get(eid, {})
        start = parse_iso(ev["commence_time"]).astimezone(LOCAL_TZ)
        games.append({
            "game_pk":  m.get("game_pk"),
            "status":   m.get("status"),
            "live":     bool(m.get("in_play")),
            "warmup":   bool(m.get("warmup")),
            "final":    m.get("abstract") == "Final",
            "away_p":   m.get("away_p"), "away_ph": m.get("away_p_hand"),
            "home_p":   m.get("home_p"), "home_ph": m.get("home_p_hand"),
            "away_r":   m.get("away_r"), "home_r": m.get("home_r"),
            "inning":   m.get("inning"), "half": m.get("half"),
            "outs":     m.get("outs"),
            "event_id":  eid,
            "start_iso": ev["commence_time"],
            "slate":     start.strftime("%Y-%m-%d"),
            "slate_lbl": start.strftime("%A, %B %d").replace(" 0", " "),
            "date":      start.strftime("%m/%d"),
            "time":      start.strftime("%I:%M %p").lstrip("0").lower(),
            # Warmup is deliberately NOT "started": MLB flips abstractGameState
            # to Live during warmups, which would red-flag a game whose lines
            # are still very much open.
            "started":   ((m.get("in_play") or m.get("abstract") == "Final")
                          if m else start < datetime.now(LOCAL_TZ)),
            "away":      ev["away"], "home": ev["home"],
            "away_rot":  ev.get("away_rot"), "home_rot": ev.get("home_rot"),
            "moves":     moves,
            "markets":   markets,
        })

    # Sort on the ISO UTC timestamp, never on the formatted display time:
    # "10:11 pm" sorts before "4:11 pm" as a string.
    games.sort(key=lambda g: g["start_iso"])
    return {
        "generated": datetime.now(LOCAL_TZ).strftime("%a %b %d, %Y %I:%M:%S %p"),
        "tz":        str(LOCAL_TZ),
        "book":      "BetOnline.ag",
        "columns":   [{"key": k, "label": l} for k, l in MARKETS],
        "games":     games,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="site")
    ap.add_argument("--days", type=int, default=2,
                    help="slates to show: 1 = today, 2 = today + tomorrow")
    args = ap.parse_args()

    payload = build(days_shown=args.days)
    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, "data.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))

    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    dst = os.path.join(args.outdir, "index.html")
    if os.path.exists(src) and os.path.abspath(src) != os.path.abspath(dst):
        shutil.copy2(src, dst)

    moves  = sum(g["moves"] for g in payload["games"])
    slates = sorted({g["slate"] for g in payload["games"]})
    print(f"  {len(payload['games'])} games across {len(slates)} slate(s) "
          f"({', '.join(slates) or 'none'})")
    print(f"  {moves} sides with recorded movement -> {args.outdir}/data.json")


if __name__ == "__main__":
    main()
