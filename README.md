# StockTrendTracker

Daily analysis of political and geopolitical news to find liquid US and European stocks and
ETFs likely to outperform over the following days to weeks. It explains the reasoning behind
every pick and keeps an honest, automatically scored track record.

**Status:** Phase 2 (event analysis) done. See [docs/PLAN.md](docs/PLAN.md) for the plan,
phases and progress log.

> Research and decision-support tool for personal use. Not investment advice; it never
> places trades.

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
uv run stt themes              # geopolitical themes and their main winners/losers
uv run stt universe find airbus
```

The analysis step between `digest` and `validate` is done by Claude in a cloud session,
following [prompts/daily_analysis.md](prompts/daily_analysis.md).

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
