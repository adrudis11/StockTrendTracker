# StockTrendTracker: notes for Claude sessions

Daily pipeline that reads political and geopolitical news, turns it into structured
events, and ranks US and European stocks likely to outperform over 5 and 20 trading
days, keeping an honest track record. Full plan and progress log: `docs/PLAN.md`.

## Ground rules

- Free data sources only; don't add paid data APIs. The one paid service is the
  Claude API for the unattended runs, the owner's choice on 2026-10-30 (the cloud
  session credits end on 2026-11-05): `stt analyze` and `stt review`, settings in
  `config/analysis.yaml`, the key in the `ANTHROPIC_API_KEY` repository secret, every
  call's cost in `data/usage/api.jsonl`. Don't add other API calls without asking, and
  never ask for the key in chat. In a Claude session, the session's Claude can still
  do the analysis by hand (`prompts/daily_analysis.md`).
- It is a research tool. Never add trading or order placement.
- Containers are wiped between sessions: anything worth keeping goes in `data/` and is
  committed. `cache/` is re-downloaded and gitignored.
- Don't put personal contact details in HTTP headers; use `config.USER_AGENT`. One
  exception: SEC EDGAR requires a contact email, and the owner agreed to send theirs
  (2026-10-28). It comes from `STT_SEC_CONTACT` or the gitignored `.sec_contact`
  (`config.sec_user_agent`); never commit it or write it into the repo (it's public).
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

- **GitHub Actions** (from November 2026), in `.github/workflows/`, active once the
  repository variable `STT_RUNNER` is `github`:
  - `daily.yml`, weekdays 05:23 UTC: `stt daily prepare`, `stt analyze` (Claude API),
    `stt daily finish`, commit `data/` as github-actions[bot], publish the dashboard to
    GitHub Pages (`stt site`). Run by hand with "test" ticked to try it without
    recording anything.
  - `weekly.yml`, Saturdays 08:41 UTC: score the week, `stt review` writes
    `data/reviews/`. Unattended it only *proposes* edits to `config/exposures.yaml` and
    the analysis prompt; apply them in a session if they hold up.
- **Claude Code cloud sessions** (until the credits end on 2026-11-05):
  `prompts/daily_run.md` and `prompts/weekly_review.md`, in the runner session
  `session_013RsqR6xHbq73HAZ5KzSrjh` (it has the repo attached; fresh sessions spawned
  by scheduled tasks don't). Scheduled tasks: `trig_016rhLYvBBRMi2Z8uNFtrene` (daily),
  `trig_017pWbZ6vhDLnh1ro7bUqwv8` (weekly). Pause them once GitHub takes over. If both
  run, the second does nothing: `daily prepare` stops when the day is recorded.
- Dashboards: the static one on GitHub Pages (`stt site`: `dashboard/index.html` in
  `dashboard/standalone/shell.html` with `dash.js` standing in for the dashboard
  runtime) and the claude.ai Dashboard artifact (`config/dashboard.yaml`), which only a
  Claude session can refresh.

## Pipeline and commands (`uv run stt ...`)

| Step | Command | Output |
|---|---|---|
| Ingest prices and news | `stt ingest` | `cache/prices/daily.parquet`, `cache/raw/<date>/news.jsonl` |
| Rank news into stories | `stt digest` | `data/digests/<date>.json`, `cache/raw/<date>/digest.md` |
| Analyse (Claude) | follow `prompts/daily_analysis.md`, or `stt analyze` (API) | `data/events/<date>.json` |
| Check the analysis | `stt validate --day <date>` | exit 1 on errors |
| Rank picks | `stt score` | `data/picks/<date>.json` |
| Log picks + baselines | `stt record` | `data/ledger/picks.jsonl` (append-only) |
| Score matured picks and analysis calls | `stt evaluate` | `data/ledger/outcomes.jsonl`, `data/ledger/impact_outcomes.jsonl` |
| Feedback note for the analysis | `stt feedback` | `cache/raw/<date>/feedback.md` |
| Write the report | `stt report` | `data/reports/<date>.md` (includes track record) |

Trackers: `stt trackers update` (new House STOCK Act filings and SEC Form 4 insider
trades → `data/trackers/`, run by `daily prepare`; `stt trackers insiders` for the SEC
part alone, which skips with a warning when no contact email is set), `stt trackers
show` (strongest congressional and insider buying).
Polymarket odds are fetched by `stt ingest` and shown in the digest. Trump tracker: the
analysis logs his stock calls in `trump_calls` (events file); they become a ★ remark on
picks, the `trump` ledger strategy and a report/dashboard section (`trackers/trump.py`).
They carry no weight in the score. Senate trades are blocked by the site's bot
protection (Akamai); don't try to get around it.

Occasional: `stt backtest` (5-year momentum baseline and theme priors, monthly),
`stt scoreboard`. Unattended runs: `stt analyze` (the analysis through the API, with
`stt validate`'s checks and up to two fix rounds; `--out` for a test), `stt review`
(the weekly review, written only), `stt site` (the dashboard as a static site in
`site/`, from `cache/dashboard/`).

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
