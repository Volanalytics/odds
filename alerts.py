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

WATCHLIST: data/watchlist.json, optional. Two modes:

    "add"  (default) -- global rules apply everywhere, PLUS any move at all on
                        a selected game. Use when you want broad coverage but
                        extra sensitivity on a few games.
    "only"           -- nothing alerts except selected games. Use when you only
                        care about a short list.

    {
      "mode": "only",
      "mlb":   {"teams": ["MIL"], "games": ["ATL@MIL"]},
      "ncaaf": {"teams": [], "games": ["UNC@TCU"]}
    }

  Teams use the codes shown on the board. Games use AWAY@HOME exactly as the
  board prints them, which is why they are matched case-insensitively.

  python alerts.py                 # writes data/alert_body.txt if anything fired
  python alerts.py --dry-run       # print, change nothing
"""

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import sports

DATA_DIR = os.environ.get("ODDS_DATA_DIR", "data")
SPORT    = os.environ.get("ODDS_SPORT", "mlb")
LOCAL_TZ = ZoneInfo(os.environ.get("ODDS_TZ", "America/Chicago"))

CENT_THRESHOLD = 10        # moneyline cents that count as a real move
LOOKBACK_DAYS  = 2         # day files to scan
MAX_ALERTS     = 40        # cap one email; beyond this something is wrong

def market_kind(market):
    """
    Point market or money market, derived from the key rather than a list.

    The old hardcoded sets named only baseball keys, so spreads_h1, totals_q1
    and team_totals_h1 matched nothing and football never alerted at all.
    """
    if market.startswith("h2h"):
        return "money"
    if market.startswith(("spreads", "totals", "team_totals")):
        return "point"
    return None

def labels_for(sport):
    return {k: v for k, v in sports.cfg(sport)["columns"]}


def crossed(keys, a, b):
    """Key numbers strictly between the two prices, i.e. actually crossed."""
    if a is None or b is None:
        return [k for k in keys if False]
    lo, hi = sorted((abs(a), abs(b)))
    return [k for k in keys if lo < k < hi or (lo == k != hi) or (hi == k != lo)]

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


def label_for(rec, names=None):
    """
    Side label, abbreviated where possible.

    `names` maps full club names to short codes for this game; without it a
    college side reads "North Carolina Tar Heels" in the alert body while the
    subject shows UNC, which looks like two different games.
    """
    names = names or {}
    if rec["market"].startswith("team_totals"):
        team = names.get(rec["desc"]) or TEAM_CODE.get(rec["desc"], rec["desc"])
        return f"{team} {rec['outcome']}"
    if rec["outcome"] in ("Over", "Under"):
        return rec["outcome"]
    return (names.get(rec["outcome"])
            or TEAM_CODE.get(rec["outcome"], rec["outcome"]))


def load_watchlist():
    """
    Per-sport selections, tolerating the flat legacy shape.

    Sport-scoped lists matter because codes collide: "MIL" is a baseball team
    and could match something unrelated in another league.
    """
    wl = read_json(os.path.join(DATA_DIR, "watchlist.json"), {}) or {}
    mode = (wl.get("mode") or "add").lower()
    if mode not in ("add", "only"):
        mode = "add"

    per = {}
    for sp in sports.SPORTS:
        node = wl.get(sp) or {}
        per[sp] = {
            "teams": {t.upper() for t in node.get("teams", [])},
            "games": {g.upper().replace(" ", "") for g in node.get("games", [])},
            "events": set(node.get("events", [])),
        }
        # Legacy flat lists applied to every sport.
        per[sp]["teams"] |= {t.upper() for t in wl.get("teams", [])}
        per[sp]["events"] |= set(wl.get("events", []))
    return mode, per


def short_codes(sport):
    """
    event_id -> (away, home) display codes.

    events.json holds the full club name for sports without a hard team map,
    so college alerts would otherwise read "North Carolina Tar Heels@TCU Horned
    Frogs". The ESPN abbreviations live in the enrichment file.
    """
    enrich = read_json(os.path.join(DATA_DIR, sport, "enrich.json"), {}) or {}
    out = {}
    for eid, m in enrich.items():
        if m.get("away_abbr") and m.get("home_abbr"):
            out[eid] = (m["away_abbr"], m["home_abbr"])
    return out


def is_watched(sel, event_id, ev):
    if event_id in sel["events"]:
        return True
    if sel["teams"] & {ev["away"].upper(), ev["home"].upper()}:
        return True
    return f"{ev['away']}@{ev['home']}".upper() in sel["games"]


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def load_records(sport):
    today = datetime.now(timezone.utc).date()
    days = [(today - timedelta(days=k)).isoformat()
            for k in range(LOOKBACK_DAYS, -2, -1)]
    out = []
    for d in days:
        p = os.path.join(DATA_DIR, sport, "odds", f"{d}.ndjson")
        try:
            with open(p, encoding="utf-8") as f:
                out += [json.loads(l) for l in f if l.strip()]
        except FileNotFoundError:
            continue
    return out


def classify(anchor, cur, watched, conf):
    """
    Reason string, or None. `anchor` is the last price we reported on this
    side -- not necessarily the previous record -- so slow drift accumulates
    instead of being reset by every intermediate tick.
    """
    market = cur["market"]
    kind = market_kind(market)

    if kind == "point" and anchor["point"] != cur["point"]:
        hits = crossed(conf["keys"].get(market, []),
                       anchor["point"], cur["point"])
        # Crossing 3 or 7 in football, or a whole run in baseball, is worth
        # separating from a line drifting half a point inside the same range.
        return f"KEY {hits[0]:g}" if hits else "LINE"

    if kind == "money":
        a, b = cents(anchor["price"]), cents(cur["price"])
        if a is not None and b is not None and abs(b - a) >= conf["cents"]:
            return f"{abs(b - a):.0f}c"

    if watched and (anchor["point"] != cur["point"]
                    or anchor["price"] != cur["price"]):
        return "WATCH"
    return None


def scan(sport, dry_run=False):
    """Alerts for one sport. Returns (list, seeding_flag)."""
    conf   = sports.cfg(sport)["alerts"]
    LABEL  = labels_for(sport)
    index = read_json(os.path.join(DATA_DIR, sport, "events.json"), {})
    mode, sel = load_watchlist()
    sel = sel[sport]
    codes = short_codes(sport)
    has_selection = bool(sel["teams"] or sel["games"] or sel["events"])

    state_path = os.path.join(DATA_DIR, sport, "alert_state.json")
    state      = read_json(state_path, None)
    seeding    = state is None          # first run: record, don't send
    state      = state or {}

    by_side = defaultdict(list)
    for r in load_records(sport):
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
        watched = is_watched(sel, key[0], ev)
        # "only" mode with an empty list would silence everything, which is
        # almost certainly a mistake rather than an instruction.
        if mode == "only" and has_selection and not watched:
            new_state[skey] = {"ts": seq[-1]["ts"],
                               "point": seq[-1]["point"],
                               "price": seq[-1]["price"]}
            continue

        # Anchor: the last price reported, falling back to the oldest record
        # we hold when this side has never alerted.
        anchor = ({"point": prior["point"], "price": prior["price"]}
                  if "price" in prior else
                  {"point": seq[0]["point"], "price": seq[0]["price"]})

        for cur in seq[1:]:
            if last_seen and cur["ts"] <= last_seen:
                anchor = {"point": cur["point"], "price": cur["price"]}
                continue
            why = classify(anchor, cur, watched, conf)
            if why:
                away, home = codes.get(key[0], (ev["away"], ev["home"]))
                nmap = {ev["away_team"]: away, ev["home_team"]: home,
                        ev["away"]: away, ev["home"]: home}
                alerts.append({
                    "ts": cur["ts"], "why": why, "sport": sports.cfg(sport)["label"],
                    "game": f"{away}@{home}",
                    "start": ev["commence_time"],
                    "market": LABEL.get(cur["market"], cur["market"]),
                    "side": label_for(cur, nmap),
                    # anchor carries no market, so pass it explicitly or a
                    # spread's "from" loses its sign: "2.5 -> +3.5".
                    "from": fmt(anchor, cur["market"]), "to": fmt(cur),
                })
                anchor = {"point": cur["point"], "price": cur["price"]}

        new_state[skey] = {"ts": seq[-1]["ts"],
                           "point": anchor["point"], "price": anchor["price"]}

    if not dry_run:
        os.makedirs(os.path.join(DATA_DIR, sport), exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(new_state, f, indent=0, sort_keys=True)
        os.replace(tmp, state_path)

    return alerts, seeding


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sport", default=None, choices=list(sports.SPORTS),
                    help="scan one sport; default scans all")
    args = ap.parse_args()

    wanted = [args.sport] if args.sport else list(sports.DEFAULT_ORDER)
    alerts, seeded = [], []

    for sp in wanted:
        try:
            found, seeding = scan(sp, dry_run=args.dry_run)
        except FileNotFoundError:
            print(f"  {sp}: no data yet")
            continue
        if seeding:
            seeded.append(sp)
            print(f"  {sp}: seeded -- no mail on the first run")
            continue
        print(f"  {sp}: {len(found)} alert(s)")
        alerts += found

    # One email covering every sport rather than one per league; a stream of
    # single-sport mails is how an alert channel gets muted.
    alerts.sort(key=lambda a: a["ts"], reverse=True)
    truncated = len(alerts) > MAX_ALERTS
    shown = alerts[:MAX_ALERTS]

    for a in shown:
        print(f"    [{a['why']:>6}] {a['sport']:<6} {a['game']:<12} "
              f"{a['market']:<7} {a['side']:<12} {a['from']} -> {a['to']}")

    body_path = os.path.join(DATA_DIR, "alert_body.txt")
    if os.path.exists(body_path) and not args.dry_run:
        os.remove(body_path)

    if not shown or args.dry_run:
        emit_count(0, "")
        return

    lines = []
    for a in shown:
        lines.append(
            f"[{a['why']}] {a['sport']}  {a['game']}  {a['market']} {a['side']}\n"
            f"        {a['from']}  ->  {a['to']}    at {local(a['ts'])}"
            f"    (start {local(a['start'])})\n")
    if truncated:
        lines.append(f"\n...and {len(alerts) - MAX_ALERTS} more, not shown.\n")

    with open(body_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # Subject is the games themselves: scanning "ATL@MIL, UNC@TCU" on a phone
    # lock screen tells you whether to open it; "7 moves, 4 games" does not.
    seen, names = set(), []
    for a in shown:                       # already newest-first
        if a["game"] not in seen:
            seen.add(a["game"])
            names.append(a["game"])

    subj, shown_names = "", []
    for n in names:
        trial = ", ".join(shown_names + [n])
        if len(trial) > 60:               # keep it readable in a mail list
            break
        shown_names.append(n)
        subj = trial
    extra = len(names) - len(shown_names)
    if extra:
        subj = f"{subj} +{extra} more" if subj else f"{extra} games"
    emit_count(len(shown), subj or "line moves")


def emit_count(n, subject):
    """Hand the workflow enough to decide whether to send."""
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"count={n}\n")
            f.write(f"subject={subject}\n")


if __name__ == "__main__":
    main()
