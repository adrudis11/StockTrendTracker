# StockTrendTracker: notes for Claude sessions

Daily pipeline that reads political and geopolitical news, turns it into structured
events, and ranks US and European stocks likely to outperform over 5 and 20 trading
days, keeping an honest track record. Full plan and progress log: `docs/PLAN.md`.

## Ground rules

- Everything runs in Claude Code cloud sessions on free data sources. Don't add paid
  APIs or an Anthropic API key: the analysis step is done by the session's Claude.
- It is a research tool. Never add trading or order placement.
- Containers are wiped between sessions: anything worth keeping goes in `data/` and is
  committed. `cache/` is re-downloaded and gitignored.
- Don't put personal contact details in HTTP headers; use `config.USER_AGENT`.
- Use `requests`, not `httpx` (Wikipedia blocks httpx).

## Pipeline and commands (`uv run stt ...`)

| Step | Command | Output |
|---|---|---|
| Ingest prices and news | `stt ingest` | `cache/prices/daily.parquet`, `cache/raw/<date>/news.jsonl` |
| Rank news into stories | `stt digest` | `data/digests/<date>.json`, `cache/raw/<date>/digest.md` |
| Analyse (Claude) | follow `prompts/daily_analysis.md` | `data/events/<date>.json` |
| Check the analysis | `stt validate --day <date>` | exit 1 on errors |

Helpers: `stt themes` (targets for impacts), `stt universe find <name>` (tickers),
`stt calendar` (which exchanges trade today), `stt universe build` (monthly refresh).

## Reference data

- `data/universe/stocks.csv`: 798 stocks (S&P 500 + major European indices), built
  by `stt universe build` with fixes from `config/universe_overrides.yaml`.
- `config/instruments.yaml`: benchmarks (SPY, EXSA.DE), sector/theme/country ETFs, macro.
- `config/exposures.yaml`: geopolitical themes and the signed weights of their members.
- `config/news_sources.yaml`: topic queries and feeds.

## Before pushing

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src tests && uv run pytest
```

`uv run pytest -m live` runs smoke tests against the real sources.
