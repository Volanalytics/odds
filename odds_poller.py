"""
odds_poller.py  --  BetOnline MLB odds, append-only NDJSON store
================================================================
Storage is one JSON object per line in data/odds/YYYY-MM-DD.ndjson, one line per
GENUINE price change.

WHY NDJSON AND NOT SQLITE: this store lives in git so an ephemeral Actions
runner can pick up where the last run left off. Git stores a fresh compressed
copy of a binary blob on every commit and delta-compresses it poorly, so a
committed .db would add gigabytes of history per month. Text appends diff to
almost nothing -- a full season is a few MB. Same append-only philosophy, format
git is actually good at. Load it into SQLite any time:

    rows = [json.loads(l) for f in glob.glob("data/odds/*.ndjson")
                          for l in open(f)]

SCOPE
  9-inning:  h2h, spreads, totals          FEATURED   slate endpoint, 3 credits
  5-inning:  *_1st_5_innings               ADDITIONAL per-event
  team:      team_totals                   ADDITIONAL per-event

  There is no team_totals_1st_5_innings key -- period markets stop at
  halves/quarters/periods. F5 team totals are not fetchable.

COST
  /odds              = [markets SPECIFIED] x [regions]
  /events/{id}/odds  = [markets RETURNED]  x [regions]
  1-10 books = 1 region. Empty responses are free, so request generously
  per-event and stingily on the slate endpoint.

USAGE
  python odds_poller.py --mode skeleton                 free
  python odds_poller.py --mode main                     3 credits, whole slate
  python odds_poller.py --mode deep                     ~4 credits per game
  python odds_poller.py --mode deep --tomorrow --force
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

API_KEY  = os.environ.get("ODDS_API_KEY", "")
DATA_DIR = os.environ.get("ODDS_DATA_DIR", "data")
BOOK     = "betonlineag"
SPORT    = "baseball_mlb"
HOST     = "https://api.the-odds-api.com"

SLATE_MARKETS = ["h2h", "spreads", "totals"]
EVENT_MARKETS = [
    "h2h_1st_5_innings",
    "spreads_1st_5_innings",
    "totals_1st_5_innings",
    "team_totals",
]

# (hours_to_first_pitch_at_or_below, min_seconds_between_polls). None = skip.
# The workflow fires every 5 minutes; these tables decide which of those
# invocations actually spend credits.
SLATE_CADENCE = [(3, 300), (6, 900), (12, 1800), (999, 21600)]
EVENT_CADENCE = [(1, 1800), (3, 3600), (6, 7200), (999, None)]

BUDGET_FLOOR    = 300
CHUNK           = 10
REQUEST_SPACING = 0.35

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
}
assert len(set(TEAM_CODE.values())) == 30, "team map must cover exactly 30 clubs"

ALIASES = {
    "Oakland Athletics": "ATH", "Las Vegas Athletics": "ATH",
    "St Louis Cardinals": "STL", "Cleveland Indians": "CLE",
}


def to_code(name):
    code = TEAM_CODE.get(name) or ALIASES.get(name)
    if code is None:
        raise KeyError(f"unmapped team name from API: {name!r}")
    return code


# ═════════════════════════════════════════════════════════════════════════════
#  PATHS / TIME
# ═════════════════════════════════════════════════════════════════════════════

def now():
    return datetime.now(timezone.utc)


def now_iso():
    return now().strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def odds_path(day):
    return os.path.join(DATA_DIR, "odds", f"{day}.ndjson")


def events_path():
    return os.path.join(DATA_DIR, "events.json")


def state_path():
    return os.path.join(DATA_DIR, "poll_state.json")


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    os.replace(tmp, path)      # atomic: a killed run can't leave a truncated file


def append_ndjson(day, records):
    path = odds_path(day)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, separators=(",", ":"), sort_keys=True) + "\n")


def read_ndjson(day):
    try:
        with open(odds_path(day), encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]
    except FileNotFoundError:
        return []


def days_window():
    """Files consulted for dedupe: yesterday, today, tomorrow (UTC)."""
    d = now().date()
    return [(d + timedelta(days=k)).isoformat() for k in (-1, 0, 1)]


def load_last_prices(days):
    """Most recent (point, price) per side. Later files win."""
    last = {}
    for d in days:
        for r in read_ndjson(d):
            last[(r["event_id"], r["market"], r["outcome"], r["desc"])] = \
                (r["point"], r["price"])
    return last


def interval_for(hours_out, table):
    for bound, secs in table:
        if hours_out <= bound:
            return secs
    return None


def is_due(state, scope, tier, commence_time, table):
    hours_out = (parse_iso(commence_time) - now()).total_seconds() / 3600
    if hours_out < 0:
        return False
    interval = interval_for(hours_out, table)
    if interval is None:
        return False
    prev = state.get(f"{tier}:{scope}")
    if not prev:
        return True
    return (now() - parse_iso(prev)).total_seconds() >= interval


# ═════════════════════════════════════════════════════════════════════════════
#  HTTP
# ═════════════════════════════════════════════════════════════════════════════

class Budget:
    def __init__(self, floor):
        self.floor, self.remaining, self.spent = floor, None, 0

    def note(self, h):
        try:
            self.remaining = int(h.get("x-requests-remaining", -1))
        except (TypeError, ValueError):
            pass
        try:
            self.spent += int(h.get("x-requests-last", 0))
        except (TypeError, ValueError):
            pass

    def ok(self):
        return self.remaining is None or self.remaining > self.floor


BUDGET = Budget(BUDGET_FLOOR)


def api_get(path, **params):
    params.setdefault("apiKey", API_KEY)
    for attempt in range(4):
        resp = requests.get(f"{HOST}{path}", params=params, timeout=30)
        if resp.status_code == 429:
            wait = 2 ** attempt
            print(f"    429 rate limit; sleeping {wait}s")
            time.sleep(wait)
            continue
        BUDGET.note(resp.headers)
        if resp.status_code != 200:
            print(f"    {resp.status_code} on {path}: {resp.text[:160]}")
            return None
        time.sleep(REQUEST_SPACING)
        return resp.json()
    print(f"    gave up on {path} after repeated 429s")
    return None


# ═════════════════════════════════════════════════════════════════════════════
#  STORE
# ═════════════════════════════════════════════════════════════════════════════

def store_events(events, index):
    unmapped = []
    for ev in events:
        try:
            home, away = to_code(ev["home_team"]), to_code(ev["away_team"])
        except KeyError as e:
            unmapped.append(str(e))
            continue
        prev = index.get(ev["id"], {})
        index[ev["id"]] = {
            "commence_time": ev["commence_time"],
            "game_date":     ev["commence_time"][:10],
            "home_team":     ev["home_team"],
            "away_team":     ev["away_team"],
            "home":          home,
            "away":          away,
            # COALESCE by hand: a response that omits rotations must not wipe
            # numbers already captured.
            "home_rot":      ev.get("home_rotation") or prev.get("home_rot"),
            "away_rot":      ev.get("away_rotation") or prev.get("away_rot"),
            "first_seen":    prev.get("first_seen", now_iso()),
        }
    if unmapped:
        print("  UNMAPPED TEAMS -- fix TEAM_CODE before trusting this slate:")
        for u in unmapped:
            print(f"    {u}")
    return len(events) - len(unmapped)


def store_odds(payload, last, day):
    """
    Emit one record per side whose (point, price) differs from what we hold.

    Compare PRICES, never trust last_update for dedupe: BetOnline stamps one
    last_update across its whole bookmaker block and it ticks whenever anything
    refreshes, not when a given side moves. Keying on the timestamp appends a
    record per side per poll with identical prices.
    """
    event_id, polled, out = payload["id"], now_iso(), []

    for bm in payload.get("bookmakers", []):
        if bm["key"] != BOOK:
            continue
        for mkt in bm.get("markets", []):
            stamp = mkt.get("last_update") or bm.get("last_update") or polled
            for oc in mkt.get("outcomes", []):
                price = oc.get("price")
                if price is None:
                    continue
                outcome = oc.get("name") or ""
                desc    = oc.get("description") or ""   # TEAM on team_totals
                point   = oc.get("point")
                key     = (event_id, mkt["key"], outcome, desc)

                if last.get(key) == (point, int(price)):
                    continue
                last[key] = (point, int(price))
                out.append({
                    "event_id": event_id, "book": BOOK, "market": mkt["key"],
                    "outcome": outcome, "desc": desc, "point": point,
                    "price": int(price), "ts": stamp, "polled": polled,
                })

    if out:
        append_ndjson(day, out)
    return len(out)


# ═════════════════════════════════════════════════════════════════════════════
#  MODES
# ═════════════════════════════════════════════════════════════════════════════

def live_events(index, date=None):
    n = now_iso()
    items = [(eid, e) for eid, e in index.items()
             if e["commence_time"] > n and (date is None or e["game_date"] == date)]
    return sorted(items, key=lambda kv: kv[1]["commence_time"])


def mode_skeleton(index):
    data = api_get(f"/v4/sports/{SPORT}/events", includeRotationNumbers="true")
    if not data:
        print("  no events returned")
        return
    print(f"  {store_events(data, index)} events on the board (cost 0)")


def mode_main(index, state, force=False):
    """
    Featured markets, whole slate, 3 credits. Returns every game BetOnline has
    posted -- tomorrow's included once they open -- so future slates ride free.
    """
    events = live_events(index)
    if not events:
        print("  no upcoming events -- run --mode skeleton first")
        return
    soonest = events[0][1]["commence_time"]
    if not force and not is_due(state, "slate", "main", soonest, SLATE_CADENCE):
        print("  not due yet (slate cadence)")
        return

    data = api_get(f"/v4/sports/{SPORT}/odds", bookmakers=BOOK,
                   markets=",".join(SLATE_MARKETS), oddsFormat="american",
                   includeRotationNumbers="true")
    if not data:
        print("  no odds returned")
        return

    store_events(data, index)
    last = load_last_prices(days_window())
    day  = now().date().isoformat()
    n    = sum(store_odds(ev, last, day) for ev in data)
    state["main:slate"] = now_iso()
    print(f"  {len(data)} games, {n} price changes (3 credits)")


def mode_deep(index, state, dry_run=False, force=False, date=None):
    events = live_events(index, date)
    if not events:
        print("  no upcoming events -- run --mode skeleton first")
        return

    if dry_run:
        est = 0
        for eid, e in events:
            hrs = (parse_iso(e["commence_time"]) - now()).total_seconds() / 3600
            due = force or is_due(state, eid, "deep", e["commence_time"], EVENT_CADENCE)
            est += len(EVENT_MARKETS) if due else 0
            note = f"~{len(EVENT_MARKETS)} credits" if due else "not due"
            print(f"  {e['away']}@{e['home']}  T-{hrs:5.1f}h  {note}")
        print(f"\n  ESTIMATED SWEEP COST: ~{est} credits (upper bound)")
        return

    last  = load_last_prices(days_window())
    day   = now().date().isoformat()
    total = 0

    for eid, e in events:
        if not BUDGET.ok():
            print(f"  BUDGET FLOOR hit ({BUDGET.remaining} left) -- stopping")
            break
        if not force and not is_due(state, eid, "deep", e["commence_time"], EVENT_CADENCE):
            continue

        n = 0
        for i in range(0, len(EVENT_MARKETS), CHUNK):
            data = api_get(f"/v4/sports/{SPORT}/events/{eid}/odds",
                           bookmakers=BOOK,
                           markets=",".join(EVENT_MARKETS[i:i + CHUNK]),
                           oddsFormat="american")
            if data:
                n += store_odds(data, last, day)
        state[f"deep:{eid}"] = now_iso()
        total += n
        print(f"  {e['away']}@{e['home']}  {n} price changes")

    print(f"\n  {total} price changes total")


# ═════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["skeleton", "main", "deep"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--date", default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--tomorrow", action="store_true")
    args = ap.parse_args()

    if args.tomorrow:
        args.date = (now() + timedelta(days=1)).strftime("%Y-%m-%d")
    if not API_KEY and not args.dry_run:
        sys.exit("Set ODDS_API_KEY in the environment first.")

    index = read_json(events_path(), {})
    state = read_json(state_path(), {})

    scope = f"  slate={args.date}" if args.date else ""
    print("=" * 62)
    print(f"  BetOnline MLB poller  --  mode={args.mode}{scope}  {now_iso()}")
    print("=" * 62)

    if args.mode == "skeleton":
        mode_skeleton(index)
    elif args.mode == "main":
        mode_main(index, state, force=args.force)
    elif args.mode == "deep":
        mode_deep(index, state, dry_run=args.dry_run,
                  force=args.force, date=args.date)

    if not args.dry_run:
        # Drop long-finished events so the index stays small and its diffs
        # stay readable in the commit log.
        cut = (now() - timedelta(days=2)).strftime("%Y-%m-%d")
        for eid in [k for k, v in index.items() if v["game_date"] < cut]:
            del index[eid]
        write_json(events_path(), index)
        write_json(state_path(), state)

    print("-" * 62)
    print(f"  spent this run: {BUDGET.spent}   remaining: {BUDGET.remaining}")


if __name__ == "__main__":
    main()
