"""
migrate_from_sqlite.py  --  carry the local mlb.db store into NDJSON
====================================================================
One-off. Reads odds_snapshots + odds_events from the SQLite database built by
the earlier local version and writes data/events.json plus per-day NDJSON files,
preserving the history already captured.

Consecutive identical prices are collapsed on the way through, so this also
absorbs the dedupe_snapshots cleanup if it hasn't been run.

  python migrate_from_sqlite.py --db C:\\MLB\\mlb.db
"""

import argparse, json, os, sqlite3
from collections import defaultdict

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="mlb.db")
    ap.add_argument("--data", default="data")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row

    cols = {r[1] for r in con.execute("PRAGMA table_info(odds_events)")}
    rot  = "home_rotation" in cols

    index = {}
    for e in con.execute("SELECT * FROM odds_events"):
        index[e["event_id"]] = {
            "commence_time": e["commence_time"],
            "game_date":     e["game_date"],
            "home_team":     e["home_team"], "away_team": e["away_team"],
            "home":          e["home_code"], "away": e["away_code"],
            "home_rot":      e["home_rotation"] if rot else None,
            "away_rot":      e["away_rotation"] if rot else None,
            "first_seen":    e["first_seen"],
        }

    by_side = defaultdict(list)
    for r in con.execute(
        "SELECT * FROM odds_snapshots ORDER BY event_id, market, outcome,"
        "       description, last_update"):
        by_side[(r["event_id"], r["market"], r["outcome"], r["description"])].append(r)

    by_day, kept, dropped = defaultdict(list), 0, 0
    for (eid, market, outcome, desc), seq in by_side.items():
        last = None
        for r in seq:
            # -9999 was the SQLite sentinel for "no point" (moneylines); NDJSON
            # uses a real null, which is what fmt_point expects.
            point = None if r["point"] == -9999 else r["point"]
            cur   = (point, r["price"])
            if cur == last:
                dropped += 1
                continue
            last = cur
            kept += 1
            by_day[r["last_update"][:10]].append({
                "event_id": eid, "book": r["book"], "market": market,
                "outcome": outcome, "desc": desc, "point": point,
                "price": r["price"], "ts": r["last_update"],
                "polled": r["polled_at"],
            })
    con.close()

    os.makedirs(os.path.join(args.data, "odds"), exist_ok=True)
    with open(os.path.join(args.data, "events.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, indent=1, sort_keys=True)

    for day, recs in sorted(by_day.items()):
        recs.sort(key=lambda r: r["ts"])
        path = os.path.join(args.data, "odds", f"{day}.ndjson")
        with open(path, "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, separators=(",", ":"), sort_keys=True) + "\n")
        print(f"  {day}: {len(recs)} records")

    print(f"\n  {len(index)} events, {kept} price points kept, "
          f"{dropped} duplicates collapsed")

if __name__ == "__main__":
    main()
