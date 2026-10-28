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
- Don't read X/Twitter through mirrors (Nitter etc.) or other copies of walled
  content; X needs its paid API. Don't accept a site's terms on the user's behalf
  (e.g. the Senate disclosure search) without their explicit consent.
- `data/trackers/` is append-only like the ledger: new filings are added, old lines
  are never rewritten.
- **Never edit or delete lines in `data/ledger/`.** The ledger is the honest track
  record; `stt record` only appends and a re-run never replaces a day's picks.
- Don't re-run `stt score` for a day that is already recorded: the ledger keeps the
  first version, so the picks file and the ledger would disagree.

## Scheduled runs

- Weekdays before the European open: `prompts/daily_run.md` (`stt daily prepare`,
  Claude's analysis, `stt daily finish`, dashboard refresh, commit `data/`).
- Saturdays: `prompts/weekly_review.md` (score the week, write `data/reviews/`, small
  edits to `config/exposures.yaml` and the analysis prompt).
- Both run in the runner session `session_013RsqR6xHbq73HAZ5KzSrjh` (it has the repo
  attached; fresh sessions spawned by scheduled tasks don't). Scheduled tasks:
  `trig_016rhLYvBBRMi2Z8uNFtrene` (daily), `trig_017pWbZ6vhDLnh1ro7bUqwv8` (weekly).
- Dashboard: `config/dashboard.yaml` (a claude.ai Dashboard artifact; its page source
  is kept in `dashboard/index.html`).

## Pipeline and commands (`uv run stt ...`)

| Step | Command | Output |
|---|---|---|
| Ingest prices and news | `stt ingest` | `cache/prices/daily.parquet`, `cache/raw/<date>/news.jsonl` |
| Rank news into stories | `stt digest` | `data/digests/<date>.json`, `cache/raw/<date>/digest.md` |
| Analyse (Claude) | follow `prompts/daily_analysis.md` | `data/events/<date>.json` |
| Check the analysis | `stt validate --day <date>` | exit 1 on errors |
| Rank picks | `stt score` | `data/picks/<date>.json` |
| Log picks + baselines | `stt record` | `data/ledger/picks.jsonl` (append-only) |
| Score matured picks | `stt evaluate` | `data/ledger/outcomes.jsonl` |
| Write the report | `stt report` | `data/reports/<date>.md` (includes track record) |

Trackers: `stt trackers update` (new House STOCK Act filings → `data/trackers/`, run
by `daily prepare`), `stt trackers show` (strongest congressional buying/selling).
Polymarket odds are fetched by `stt ingest` and shown in the digest. Trump tracker: the
analysis logs his stock calls in `trump_calls` (events file); they become a ★ remark on
picks, the `trump` ledger strategy and a report/dashboard section (`trackers/trump.py`).
They carry no weight in the score. Senate trades are blocked by the site's bot
protection (Akamai); don't try to get around it.

Occasional: `stt backtest` (5-year momentum baseline and theme priors, monthly),
`stt scoreboard`.

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
