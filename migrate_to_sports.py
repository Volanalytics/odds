"""
migrate_to_sports.py  --  move the flat MLB store into data/mlb/
================================================================
The single-sport layout put everything at the top of data/. Multi-sport needs
one folder per league, or NCAAF events would land in the same events.json as
MLB and the dedupe map would collide across sports.

  data/odds/*.ndjson   ->  data/mlb/odds/*.ndjson
  data/events.json     ->  data/mlb/events.json
  data/poll_state.json ->  data/mlb/poll_state.json
  data/mlb.json        ->  data/mlb/enrich.json
  data/alert_state.json, data/watchlist.json  ->  left where they are

Idempotent: running it twice is a no-op. Nothing is deleted -- files are moved,
so the price history you have already paid credits to collect comes along.

  python migrate_to_sports.py --dry-run
  python migrate_to_sports.py
"""

import argparse
import os
import shutil

DATA = os.environ.get("ODDS_DATA_DIR", "data")
SPORT = "mlb"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--data", default=DATA)
    args = ap.parse_args()

    root = args.data
    dest = os.path.join(root, SPORT)

    moves = []

    for name, newname in [("events.json", "events.json"),
                          ("poll_state.json", "poll_state.json"),
                          ("mlb.json", "enrich.json")]:
        src = os.path.join(root, name)
        if os.path.exists(src):
            moves.append((src, os.path.join(dest, newname)))

    odds_src = os.path.join(root, "odds")
    if os.path.isdir(odds_src):
        for f in sorted(os.listdir(odds_src)):
            if f.endswith(".ndjson"):
                moves.append((os.path.join(odds_src, f),
                              os.path.join(dest, "odds", f)))

    if not moves:
        print("  nothing to migrate (already done, or no data yet)")
        return

    lines = 0
    for src, dst in moves:
        if src.endswith(".ndjson"):
            with open(src, encoding="utf-8") as fh:
                lines += sum(1 for l in fh if l.strip())
        print(f"  {src}  ->  {dst}")

    print(f"\n  {len(moves)} file(s), {lines} price records")

    if args.dry_run:
        print("  dry run -- nothing moved")
        return

    for src, dst in moves:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            print(f"  SKIP (destination exists): {dst}")
            continue
        shutil.move(src, dst)

    # Clean up the now-empty odds folder, but never anything with files left.
    if os.path.isdir(odds_src) and not os.listdir(odds_src):
        os.rmdir(odds_src)

    print("  done")


if __name__ == "__main__":
    main()
