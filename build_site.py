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

import sports

DATA_DIR = os.environ.get("ODDS_DATA_DIR", "data")

# Set from sports.py in main(); nothing below hardcodes a league.
SPORT_ID = None
CFG      = None

# Display zone for game times and the build stamp. The Actions runner is UTC,
# so this must be set explicitly or every time on the board will be wrong.
LOCAL_TZ = ZoneInfo(os.environ.get("ODDS_TZ", "America/New_York"))




def code(name):
    """Short code where the sport has a map, full name where it doesn't."""
    teams = CFG.get("teams")
    return (teams.get(name, name) if teams else name)


def parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def stamp(iso):
    return parse_iso(iso).astimezone(LOCAL_TZ).strftime("%m/%d %I:%M:%S %p")


def fmt_price(p):
    return "" if p is None else (f"+{p}" if p > 0 else str(p))


def fmt_point(v, market=None):
    """
    Half lines print as 8.5, whole numbers as 8. Moneylines have no point.

    Run lines get an explicit + on the underdog so both rows carry a sign and
    the column aligns: +1.5 above -1.5 rather than 1.5 above -1.5. Totals are
    left unsigned -- "Over +8" would be nonsense.
    """
    if v is None:
        return ""
    txt = str(int(v)) if float(v).is_integer() else f"{v:g}"
    if market in CFG["spread_markets"] and v > 0:
        txt = "+" + txt
    return txt


def fmt_line(r):
    return f"{fmt_point(r['point'], r.get('market'))} {fmt_price(r['price'])}".strip()


def side_order(label, away, home):
    """
    Sort key putting sides in the same order as the Teams column: away above
    home, Over above Under. Sorting alphabetically inverts any matchup whose
    home team sorts first (NYY before TOR), which reads as flipped odds.
    """
    parts = label.split()
    if len(parts) == 2:                      # team_totals: "ATL Over"
        team, ou = parts
        return (0 if team == away else 1, 0 if ou == "Over" else 1)
    if label in ("Over", "Under"):
        return (0 if label == "Over" else 1, 0)
    return (0 if label == away else 1, 0)    # h2h / spreads: team code


def side_label(r, names=None):
    """
    h2h / spreads   -> team code (outcome holds the club name)
    totals          -> Over / Under
    team_totals*    -> team code + Over/Under (the TEAM lives in desc)

    `names` maps full club names to display codes for this game. College has no
    hard team map, so without it a side renders as "North Carolina Tar
    Heels +245" and overflows a phone card. startswith() catches the 1H variant
    as well as the full-game key.
    """
    names = names or {}
    if r["market"].startswith("team_totals"):
        team = names.get(r["desc"]) or code(r["desc"])
        return f"{team} {r['outcome']}"
    if r["outcome"] in ("Over", "Under"):
        return r["outcome"]
    return names.get(r["outcome"]) or code(r["outcome"])


def load_records(days):
    out = []
    for d in days:
        path = os.path.join(DATA_DIR, SPORT_ID, "odds", f"{d}.ndjson")
        try:
            with open(path, encoding="utf-8") as f:
                out += [json.loads(l) for l in f if l.strip()]
        except FileNotFoundError:
            continue
    return out


def load_mlb():
    """Optional: absent on a first run, so the board degrades to odds only."""
    try:
        with open(os.path.join(DATA_DIR, SPORT_ID, "enrich.json"), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def build(days_shown=2, history_days=4, keep_hours=12):
    with open(os.path.join(DATA_DIR, SPORT_ID, "events.json"), encoding="utf-8") as f:
        index = json.load(f)
    mlb = load_mlb()

    today   = datetime.now(LOCAL_TZ).date()
    horizon = today + timedelta(days=days_shown - 1)

    # Games drop off keep_hours after first pitch, not at a date boundary.
    #
    # The old filter compared events.json's game_date, which the poller stores
    # as the UTC date. A 10:11pm Central start is 03:11Z the NEXT day, so it
    # files under tomorrow and passed a ">= today" check forever. Late games
    # never aged out while early ones did -- inconsistent and confusing.
    # Elapsed time since start sidesteps the whole UTC/local mismatch.
    keep_from = datetime.now(timezone.utc) - timedelta(hours=keep_hours)

    # Read a few days back: a game tonight may have opened days ago, and the
    # opener column should reach that far.
    hist_days = [(today - timedelta(days=k)).isoformat()
                 for k in range(history_days, -2, -1)]

    by_event = defaultdict(list)
    for r in load_records(hist_days):
        by_event[r["event_id"]].append(r)

    now, games = datetime.now(timezone.utc), []

    for eid, ev in index.items():
        start_utc = parse_iso(ev["commence_time"])
        if start_utc < keep_from:                    # long finished
            continue
        if start_utc.astimezone(LOCAL_TZ).date() > horizon:   # too far out
            continue
        rows = by_event.get(eid, [])
        if not rows:
            continue

        m = mlb.get(eid, {})
        # College has no hard team map, so ESPN's abbreviation is the only way
        # to get "Georgia Bulldogs" down to something a column can hold.
        away_code = m.get("away_abbr") or ev["away"]
        home_code = m.get("home_abbr") or ev["home"]
        # Market outcomes carry the full club name. Without this map a college
        # side renders as "North Carolina Tar Heels+245", which overflows a
        # phone card and gets clipped.
        name_map = {ev["away_team"]: away_code, ev["home_team"]: home_code,
                    ev["away"]: away_code, ev["home"]: home_code}

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
                "label":     side_label(last, name_map),
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

        # Match the Teams column: away first, then home; Over before Under.
        for v in markets.values():
            v.sort(key=lambda s: side_order(s["label"], away_code, home_code))

        start = start_utc.astimezone(LOCAL_TZ)
        games.append({
            "game_pk":  m.get("game_pk"),
            "status":   m.get("status"),
            "live":     bool(m.get("in_play")),
            "warmup":   bool(m.get("warmup")),
            "final":    m.get("abstract") == "Final",
            "away_p":   m.get("away_p"), "away_ph": m.get("away_p_hand"),
            "home_p":   m.get("home_p"), "home_ph": m.get("home_p_hand"),
            # ESPN returns "0" for scheduled games, so gate on game state --
            # a green 0-0 on a game six days out reads as a live score.
            "away_r":   m.get("away_r") if (m.get("in_play") or m.get("final")) else None,
            "home_r":   m.get("home_r") if (m.get("in_play") or m.get("final")) else None,
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
            "away":      away_code, "home": home_code,
            "away_full": ev["away"], "home_full": ev["home"],
            # Football extras; absent for MLB and simply not rendered.
            "neutral":   bool(m.get("neutral")),
            "venue":     m.get("venue"),
            "city":      m.get("city"),
            "state":     m.get("state"),
            "away_rank": m.get("away_rank"),
            "home_rank": m.get("home_rank"),
            "period":    m.get("period"),
            "clock":     m.get("clock"),
            "away_rot":  ev.get("away_rot"), "home_rot": ev.get("home_rot"),
            "moves":     moves,
            "markets":   markets,
        })

    # Sort on the ISO UTC timestamp, never on the formatted display time:
    # "10:11 pm" sorts before "4:11 pm" as a string.
    games.sort(key=lambda g: g["start_iso"])
    return {
        "keep_h":    keep_hours,
        "generated": datetime.now(LOCAL_TZ).strftime("%a %b %d, %Y %I:%M:%S %p"),
        "tz":        str(LOCAL_TZ),
        "book":      "BetOnline.ag",
        "columns":   [{"key": k, "label": l} for k, l in CFG["columns"]],
        "games":     games,
    }


def build_one(sport, outdir, days, keep_hours):
    """days=None means use the sport's own window from sports.py."""
    """
    Emit data-<sport>.json and return a manifest entry for the tab bar.

    A sport with no data yet is not an error -- NCAAF has an empty index all
    summer. It still gets a tab, just an empty board.
    """
    global SPORT_ID, CFG
    SPORT_ID = sport
    CFG      = sports.cfg(sport)

    try:
        payload = build(days_shown=days or CFG.get("days_shown", 2),
                        keep_hours=keep_hours or CFG.get("keep_hours", 12))
    except FileNotFoundError:
        payload = {"generated": datetime.now(LOCAL_TZ).strftime("%a %b %d, %Y %I:%M:%S %p"),
                   "book": "BetOnline.ag", "games": [],
                   "columns": [{"key": k, "label": l} for k, l in CFG["columns"]]}

    payload["sport"] = sport
    payload["label"] = CFG["label"]

    with open(os.path.join(outdir, f"data-{sport}.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))

    games  = payload["games"]
    moves  = sum(g["moves"] for g in games)
    slates = sorted({g["slate"] for g in games})
    done   = sum(1 for g in games if g.get("final"))
    print(f"  {CFG['label']:<7} {len(games):>3} games, {moves:>3} sides moved, "
          f"{done} final  ({', '.join(slates) or 'no slates'})")

    return {"sport": sport, "label": CFG["label"], "games": len(games),
            "moves": moves, "file": f"data-{sport}.json"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="site")
    ap.add_argument("--sport", default=None, choices=list(sports.SPORTS),
                    help="build one sport; default builds all")
    ap.add_argument("--days", type=int, default=None,
                    help="days of slate to show (default: per sport)")
    ap.add_argument("--keep-hours", type=int, default=None,
                    help="hours after start to keep a game (default: per sport)")
    args = ap.parse_args()

    env_keep = os.environ.get("ODDS_KEEP_HOURS")
    keep = args.keep_hours if args.keep_hours is not None else (
        int(env_keep) if env_keep else None)

    os.makedirs(args.outdir, exist_ok=True)
    wanted = [args.sport] if args.sport else list(sports.DEFAULT_ORDER)

    manifest = [build_one(s, args.outdir, args.days, keep) for s in wanted]

    # The page reads this first to draw tabs, so it must list every sport even
    # when one is out of season and has nothing to show.
    with open(os.path.join(args.outdir, "sports.json"), "w", encoding="utf-8") as f:
        json.dump({"generated": datetime.now(LOCAL_TZ).strftime("%a %b %d, %Y %I:%M:%S %p"),
                   "sports": manifest}, f, separators=(",", ":"))

    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    dst = os.path.join(args.outdir, "index.html")
    if os.path.exists(src) and os.path.abspath(src) != os.path.abspath(dst):
        shutil.copy2(src, dst)

    print(f"  -> {args.outdir}/  ({len(manifest)} sport file(s) + sports.json)")


if __name__ == "__main__":
    main()
