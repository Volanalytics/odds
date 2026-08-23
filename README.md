# BetOnline odds board

Static multi-sport odds board, updated by GitHub Actions, served from GitHub
Pages. No model output — prices only.

Sports and their markets are defined in `sports.py`; adding a league needs no
change anywhere else.

## Layout

    sports.py            market lists, columns, cadence per league
    odds_poller.py       --sport <id> --mode skeleton|main|deep
    build_site.py        -> site/data-<sport>.json + site/sports.json
    mlb_enrich.py        probable pitchers, live scores (free, MLB Stats API)
    espn_cfb_enrich.py   venue, NEUTRAL SITE flag, ranks (free, ESPN)
    alerts.py            ODDS_SPORT=<id>, emails on meaningful moves
    index.html           the board (tabs read sports.json)

    data/<sport>/odds/*.ndjson   append-only price changes
    data/<sport>/events.json     event index
    data/<sport>/poll_state.json cadence marks
    data/<sport>/enrich.json     free-API extras

## Migrating from the single-sport layout

    python migrate_to_sports.py --dry-run
    python migrate_to_sports.py

Moves data/odds, data/events.json and data/poll_state.json into data/mlb/.
Idempotent; nothing is deleted. Commit the result — the workflow runs against
the checked-out copy, so an unmigrated repo starts the history over.

## Setup

1. Push this repo, public.
2. Settings -> Secrets -> Actions -> new secret `ODDS_API_KEY`.
3. Settings -> Pages -> Source: **GitHub Actions**.
4. Actions tab -> poll-odds -> Run workflow, mode `skeleton`, to seed the index.
5. Run again with mode `main`.

Site lands at `https://<user>.github.io/<repo>/`.

## Reliable triggering

GitHub's scheduler has a 5-minute minimum and routinely runs 15-30 minutes
late. The cron in `poll.yml` is a fallback. For real timing, create a free
cron-job.org job hitting:

    POST https://api.github.com/repos/<user>/<repo>/actions/workflows/poll.yml/dispatches
    Authorization: Bearer <fine-grained PAT with Actions: write>
    Accept: application/vnd.github+json
    Body: {"ref":"main","inputs":{"mode":"main"}}

Every 5 minutes. The cadence tables in `odds_poller.py` decide which of those
invocations actually spend credits, so frequent triggering is cheap.

## Costs

    /odds             3 credits, whole slate  (h2h, spreads, totals)
    /events/{id}/odds ~4 credits per game     (F5 + team totals)
    /events           free

Roughly 350/day on the shipped cadence — comfortable inside 20k/month.
Deep sweeps run via workflow_dispatch with mode `deep`.

## Local use

    set ODDS_API_KEY=...
    python odds_poller.py --mode skeleton
    python odds_poller.py --mode main
    python build_site.py --outdir site
    cd site && python -m http.server 8080

## Migrating the existing SQLite store

    python migrate_from_sqlite.py --db C:\MLB\mlb.db

Writes data/events.json and data/odds/*.ndjson, collapsing consecutive
identical prices as it goes.
