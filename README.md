# BetOnline MLB odds board

Static odds board, updated by GitHub Actions, served from GitHub Pages.
No model output — prices only.

## Layout

    odds_poller.py     fetch + append price changes to data/odds/*.ndjson
    build_site.py      read the store -> site/data.json
    index.html         the board
    data/events.json   event index (ids, teams, rotations, start times)
    data/poll_state.json  last-polled marks, drives the cadence tables

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
