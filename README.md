# StockTrendTracker

Daily analysis of political and geopolitical news to find liquid US and European stocks and
ETFs likely to outperform over the following days to weeks. It explains the reasoning behind
every pick and keeps an honest, automatically scored track record.

**Status:** Phase 5 (automation) set up; Phase 6 (paper-tracking period) running. See [docs/PLAN.md](docs/PLAN.md) for the plan,
phases and progress log.

> Research and decision-support tool for personal use. Not investment advice; it never
> places trades.

## How it runs

Every weekday at 05:23 UTC, before the European open, GitHub Actions runs
[.github/workflows/daily.yml](.github/workflows/daily.yml): `stt daily prepare` (prices,
news, trackers, scoring of matured picks, digest), `stt analyze` (the analysis, by
Claude through the API), `stt daily finish` (picks, ledger, report), a commit of
`data/`, and the dashboard on GitHub Pages. On Saturdays
[weekly.yml](.github/workflows/weekly.yml) scores the week and writes a review to
`data/reviews/`.

The GitHub runs start on 2026-11-04. Until 2026-11-03 the daily run happens in a
Claude Code cloud session ([prompts/daily_run.md](prompts/daily_run.md),
[prompts/weekly_review.md](prompts/weekly_review.md)), which also refreshes a private
claude.ai dashboard; its credits end on 2026-11-05. If both ever run on the same day,
the first records it and the second does nothing.

### Setting up the GitHub runs

1. **Claude API key.** At [console.anthropic.com](https://console.anthropic.com), add
   billing (pay as you go; a monthly spend limit is a good idea) and create an API
   key.
2. **Secrets.** In the repository, Settings → Secrets and variables → Actions → New
   repository secret: `ANTHROPIC_API_KEY_STOCKTRACKER` (the key) and `STT_SEC_CONTACT`
   (the contact email the SEC asks for; optional, the insider tracker is skipped
   without it). Secrets stay out of the public repository and the logs.
3. **Pages.** Settings → Pages → Build and deployment → Source: **GitHub Actions**.
   After the first real run the dashboard is at
   <https://adrudis11.github.io/StockTrendTracker/> (public, like the repository).
4. **Test.** Actions → Daily run → Run workflow, with "test" ticked. It runs the
   analysis into a downloadable `test-analysis` file and records nothing. Its log ends
   with the number of events and the API cost.
5. **Nothing to switch on.** The schedules start by themselves on 2026-11-04. The
   repository variable `STT_RUNNER` (Settings → Secrets and variables → Actions →
   Variables) overrides that: `github` starts them now, `off` pauses them.

Cost: the analysis is one request a day (about 35,000 tokens in and 10,000–30,000
out, including Claude's reasoning) plus a fix round now and then: roughly $0.40–0.80 a
day at the prices in [config/analysis.yaml](config/analysis.yaml), so about $10–15 a
month with the weekly review. Every call's actual cost is logged in
`data/usage/api.jsonl`. GitHub Actions and Pages are free for a public repository.

## Setup

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

## Usage

```bash
uv run stt ingest              # prices + last 24h of news (72h on Mondays) into cache/raw/<today>/
uv run stt ingest --skip prices --skip gdelt_doc
uv run stt calendar            # which exchanges trade today
uv run stt universe show       # stocks covered, by country and sector
uv run stt universe build      # refresh data/universe/stocks.csv from Wikipedia (monthly)
uv run stt digest --print      # group and rank today's news into ~150 stories
uv run stt validate            # check Claude's data/events/<today>.json
uv run stt score               # rank picks for 5 and 20 trading days -> data/picks/
uv run stt record              # append picks + baselines to the ledger (append-only)
uv run stt evaluate            # score picks, and every analysis call, whose horizon has ended
uv run stt feedback            # how past calls did, by kind: read by the daily analysis
uv run stt scoreboard          # track record vs momentum and random baselines
uv run stt report              # daily Markdown report -> data/reports/
uv run stt backtest            # 5-year momentum baseline, theme priors, tracker backtest
uv run stt trackers update     # new House trade filings (STOCK Act) and SEC Form 4s -> data/trackers/
uv run stt trackers insiders   # SEC Form 4 insider trades alone (needs a contact email, see below)
uv run stt trackers show       # strongest congressional and insider buying, last 30 days
uv run stt themes              # geopolitical themes and their main winners/losers
uv run stt universe find airbus
uv run stt analyze             # the analysis through the Claude API (needs ANTHROPIC_API_KEY)
uv run stt review              # the weekly review through the API (proposes edits, makes none)
uv run stt site                # the dashboard as a static site in site/ (after `stt dashboard`)
```

The analysis step between `digest` and `validate` follows
[prompts/daily_analysis.md](prompts/daily_analysis.md): either `stt analyze`, or Claude
by hand in a Claude Code session. It also logs any stock
Trump talks up or attacks (`trump_calls`): the **Trump tracker** marks those picks with
a ★, logs each call as its own strategy in the ledger, and shows his recent calls and
whether they came true in the report and on the dashboard.

The SEC asks automated clients for a contact email: set `STT_SEC_CONTACT` or put it in
`.sec_contact` (gitignored). Without one, the insider tracker is skipped.

`stt ingest` prints a per-source health table and exits with status 1 if the run isn't
usable (prices failed, or a whole news source failed). Add `--strict` to fail on any single
feed.

Output of one run, in `cache/` (not committed, re-downloaded each session):

| File | Contents |
|---|---|
| `cache/prices/daily.parquet` | ~13 months of daily OHLCV for every stock, ETF, index and macro series |
| `cache/raw/<date>/news.jsonl` | De-duplicated news items (title, link, time, publisher, topics) |
| `cache/raw/<date>/gdelt_events_agg.parquet` | GDELT event counts and tone per country and event type |
| `cache/raw/<date>/ingest_report.json` | What each source returned |

## Layout

```
config/            instruments.yaml, exposures.yaml (themes), news_sources.yaml, universe_overrides.yaml
data/universe/     stocks.csv: the 798 covered stocks (committed)
data/digests/      daily ranked stories (committed, so events can be traced to sources)
data/events/       Claude's daily structured analysis (committed)
data/picks/        ranked picks with every score component (committed)
data/reports/      daily Markdown reports (committed)
data/ledger/       every pick ever made and its outcome (append-only, committed)
data/backtests/    momentum baseline results; data/priors/ theme priors and tracker weight
data/trackers/     House members' and company insiders' disclosed trades (append-only, committed)
data/usage/        tokens and cost of every Claude API call
dashboard/         the dashboard page; standalone/ turns it into the GitHub Pages site
.github/workflows/ the scheduled daily run and weekly review
prompts/           instructions for the Claude steps
src/stocktrend/    universe/ (stock list, sectors), ingest/ (prices, RSS, Federal Register, GDELT)
tests/             offline unit tests; `uv run pytest -m live` hits the real sources
```

## Development

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
uv run mypy src tests
```
