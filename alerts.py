"""
alerts.py  --  flag meaningful line moves, write an email body
==============================================================
Reads the NDJSON store and emits alerts for:

  1. LINE CROSS      the number moved on a total or run line (8 -> 8.5),
                     not just the juice around it
  2. PRICE MOVE      a moneyline moved >= CENT_THRESHOLD cents
  3. WATCHLIST       any move on a game you flagged

Why not alert on every change: on a normal slate BetOnline shades juice a cent
or two dozens of times per game. Tonight's ATL@MIL moneyline moved five times
in three hours, all of it noise. Alerting on everything means ~200 mails a day
and you stop reading them.

STATE: data/alert_state.json holds, per side, the last timestamp examined AND
the anchor price it was last compared against. The anchor matters: comparing
each record only to the one before it misses gradual drift. A moneyline going
-115 -> -109 -> +100 is 6c then 9c, neither of which trips a 10c rule, but it
is 15c and a favourite flip end to end. Alerts measure from the anchor and
reset it whenever one fires.

On the FIRST run it seeds silently -- otherwise you'd get one mail containing
every move ever recorded.

WATCHLIST: data/watchlist.json, optional
    {"teams": ["MIL", "NYY"], "events": []}

  python alerts.py                 # writes data/alert_body.txt if anything fired
  python alerts.py --dry-run       # print, change nothing
"""

import argparse
import glob
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DATA_DIR = os.environ.get("ODDS_DATA_DIR", "data")
SPORT    = os.environ.get("ODDS_SPORT", "mlb")
LOCAL_TZ = ZoneInfo(os.environ.get("ODDS_TZ", "America/Chicago"))

CENT_THRESHOLD = 10        # moneyline cents that count as a real move
LOOKBACK_DAYS  = 2         # day files to scan
MAX_ALERTS     = 40        # cap one email; beyond this something is wrong

POINT_MARKETS = {"totals", "spreads",
                 "totals_1st_5_innings", "spreads_1st_5_innings",
                 "team_totals"}
MONEY_MARKETS = {"h2h", "h2h_1st_5_innings"}

LABEL = {
    "h2h": "ML", "spreads": "RL", "totals": "Total",
    "h2h_1st_5_innings": "F5 ML", "spreads_1st_5_innings": "F5 RL",
    "totals_1st_5_innings": "F5 Tot", "team_totals": "TT",
}

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
    "Oakland Athletics": "ATH", "St Louis Cardinals": "STL",
}


def parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def local(s):
    return parse_iso(s).astimezone(LOCAL_TZ).strftime("%I:%M %p").lstrip("0")


def cents(odds):
    """
    American odds on a continuous scale so distances are comparable.

    +100 and -100 are the same price (even money), so the raw integers jump 200
    across a boundary that is actually zero wide. Mapping both to 0 makes
    -115 -> +100 read as 15 cents rather than 215.
    """
    if odds is None:
        return None
    return odds - 100 if odds >= 100 else odds + 100


def fmt_price(p):
    return "" if p is None else (f"+{p}" if p > 0 else str(p))


SPREAD_MARKETS = {"spreads", "spreads_1st_5_innings"}


def fmt_point(v, market=None):
    """Run lines carry an explicit + on the underdog so the column aligns."""
    if v is None:
        return ""
    txt = str(int(v)) if float(v).is_integer() else f"{v:g}"
    if market in SPREAD_MARKETS and v > 0:
        txt = "+" + txt
    return txt


def fmt(rec, market=None):
    mkt = market or rec.get("market")
    return f"{fmt_point(rec['point'], mkt)} {fmt_price(rec['price'])}".strip()


def label_for(rec):
    if rec["market"] == "team_totals":
        return f"{TEAM_CODE.get(rec['desc'], rec['desc'])} {rec['outcome']}"
    if rec["outcome"] in ("Over", "Under"):
        return rec["outcome"]
    return TEAM_CODE.get(rec["outcome"], rec["outcome"])


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def load_records():
    today = datetime.now(timezone.utc).date()
    days = [(today - timedelta(days=k)).isoformat()
            for k in range(LOOKBACK_DAYS, -2, -1)]
    out = []
    for d in days:
        p = os.path.join(DATA_DIR, SPORT, "odds", f"{d}.ndjson")
        try:
            with open(p, encoding="utf-8") as f:
                out += [json.loads(l) for l in f if l.strip()]
        except FileNotFoundError:
            continue
    return out


def classify(anchor, cur, watched):
    """
    Reason string, or None. `anchor` is the last price we reported on this
    side -- not necessarily the previous record -- so slow drift accumulates
    instead of being reset by every intermediate tick.
    """
    market = cur["market"]

    if market in POINT_MARKETS and anchor["point"] != cur["point"]:
        return "LINE"

    if market in MONEY_MARKETS:
        a, b = cents(anchor["price"]), cents(cur["price"])
        if a is not None and b is not None and abs(b - a) >= CENT_THRESHOLD:
            return f"{abs(b - a):.0f}c"

    if watched and (anchor["point"] != cur["point"]
                    or anchor["price"] != cur["price"]):
        return "WATCH"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    index = read_json(os.path.join(DATA_DIR, SPORT, "events.json"), {})
    wl    = read_json(os.path.join(DATA_DIR, "watchlist.json"),
                      {"teams": [], "events": []})
    wl_teams  = set(wl.get("teams", []))
    wl_events = set(wl.get("events", []))

    state_path = os.path.join(DATA_DIR, SPORT, "alert_state.json")
    state      = read_json(state_path, None)
    seeding    = state is None          # first run: record, don't send
    state      = state or {}

    by_side = defaultdict(list)
    for r in load_records():
        by_side[(r["event_id"], r["market"], r["outcome"], r["desc"])].append(r)

    alerts, new_state = [], dict(state)

    for key, seq in by_side.items():
        seq.sort(key=lambda r: r["ts"])
        skey = "|".join(key)
        prior = state.get(skey) or {}
        # Tolerate the older state format, which stored a bare timestamp.
        if isinstance(prior, str):
            prior = {"ts": prior}
        last_seen = prior.get("ts")

        if seeding or len(seq) < 2:
            new_state[skey] = {"ts": seq[-1]["ts"],
                               "point": seq[-1]["point"],
                               "price": seq[-1]["price"]}
            continue

        ev = index.get(key[0])
        if not ev:
            continue
        watched = (key[0] in wl_events
                   or ev["away"] in wl_teams or ev["home"] in wl_teams)

        # Anchor: the last price reported, falling back to the oldest record
        # we hold when this side has never alerted.
        anchor = ({"point": prior["point"], "price": prior["price"]}
                  if "price" in prior else
                  {"point": seq[0]["point"], "price": seq[0]["price"]})

        for cur in seq[1:]:
            if last_seen and cur["ts"] <= last_seen:
                anchor = {"point": cur["point"], "price": cur["price"]}
                continue
            why = classify(anchor, cur, watched)
            if why:
                alerts.append({
                    "ts": cur["ts"], "why": why,
                    "game": f"{ev['away']}@{ev['home']}",
                    "start": ev["commence_time"],
                    "market": LABEL.get(cur["market"], cur["market"]),
                    "side": label_for(cur),
                    "from": fmt(anchor), "to": fmt(cur),
                })
                anchor = {"point": cur["point"], "price": cur["price"]}

        new_state[skey] = {"ts": seq[-1]["ts"],
                           "point": anchor["point"], "price": anchor["price"]}

    if not args.dry_run:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(new_state, f, indent=0, sort_keys=True)
        os.replace(tmp, state_path)

    if seeding:
        print(f"  seeded {len(new_state)} sides -- no mail on the first run")
        return

    alerts.sort(key=lambda a: a["ts"], reverse=True)
    truncated = len(alerts) > MAX_ALERTS
    shown = alerts[:MAX_ALERTS]

    print(f"  {len(alerts)} alert(s)")
    for a in shown:
        print(f"    [{a['why']:>5}] {a['game']:<9} {a['market']:<7} "
              f"{a['side']:<10} {a['from']} -> {a['to']}")

    body_path = os.path.join(DATA_DIR, "alert_body.txt")
    if os.path.exists(body_path) and not args.dry_run:
        os.remove(body_path)

    if not shown or args.dry_run:
        emit_count(0 if not shown else len(shown), "" if not shown else "dry")
        return

    lines = []
    for a in shown:
        lines.append(
            f"[{a['why']}] {a['game']}  {a['market']} {a['side']}\n"
            f"        {a['from']}  ->  {a['to']}    at {local(a['ts'])}"
            f"    (first pitch {local(a['start'])})\n")
    if truncated:
        lines.append(f"\n...and {len(alerts) - MAX_ALERTS} more, not shown.\n")

    with open(body_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    games = len({a["game"] for a in shown})
    emit_count(len(shown), f"{len(shown)} line moves, {games} game(s)")


def emit_count(n, subject):
    """Hand the workflow enough to decide whether to send."""
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"count={n}\n")
            f.write(f"subject={subject}\n")


if __name__ == "__main__":
    main()
