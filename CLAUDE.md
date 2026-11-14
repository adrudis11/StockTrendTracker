# StockTrendTracker: notes for Claude sessions

Pipeline that reads political and geopolitical news, turns it into structured events,
and ranks US and European stocks likely to outperform over the next 20 trading days,
keeping an honest track record. Since 2026-11-11 (the owner's choice on 2026-11-10) the
picks are made once a week, on Saturdays, from the whole week's news; weekdays only
collect data, for free. Until 2026-11-10 picks were daily, for 5 and 20 days. Full plan and progress log: `docs/PLAN.md`.
Current state, open items and the owner's preferences: `docs/HANDOFF.md` (read it
first in a new session).

## Ground rules

- Free data sources only; don't add paid data APIs. The one paid service is the
  Claude API for the unattended runs, the owner's choice on 2026-10-30 (the cloud
  session credits end on 2026-11-05): `stt analyze` and `stt review`, settings in
  `config/analysis.yaml`, the key in the `ANTHROPIC_API_KEY_STOCKTRACKER` repository
  secret (the runs pass it on as `ANTHROPIC_API_KEY`), every call's cost in
  `data/usage/api.jsonl`. Don't add other API calls without asking, and
  never ask for the key in chat. The API runs only in the Saturday run; the owner
  wants weekdays free (anything added to them must not call it). In a Claude session,
  the session's Claude can still do the analysis by hand (`prompts/weekly_analysis.md`).
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
  record; `stt record` only appends and a re-run never replaces a week's picks.
- Don't re-run `stt score` for a day or week that is already recorded: the ledger keeps
  the first version, so the picks file and the ledger would disagree.

## Scheduled runs

- **GitHub Actions**, in `.github/workflows/`, scheduled from 2026-11-04 (`START` in
  each workflow; the repository variable `STT_RUNNER` overrides it: `github` on, `off`
  paused):
  - `daily.yml`, started only by cron-job.org (no GitHub schedules since 2026-11-14,
    the owner's choice): `stt collect` (prices, news
    since the last digest, House and SEC filings, scoring of matured picks, the day's
    digest, dashboard data), commit `data/` as github-actions[bot], publish the
    dashboard to GitHub Pages (`stt site`). **No Claude API and no picks**: since
    2026-11-11 picks are made once a week on Saturdays (the owner's choice on
    2026-11-10; see `docs/PLAN.md`). The owner wants it done before 07:30 Paris time
    (2026-11-11), so an outside cron service (cron-job.org, the owner's account and
    token, README "On time every morning") starts it at 06:00 Paris every day with
    `trigger: cron`. A second run that day only refreshes. On Saturdays and Sundays (UTC) its `weekly` job starts
    `weekly.yml` instead of collecting (`gh workflow run` with the workflow's token,
    `actions: write`), so the same cron call makes the week's picks on Saturday
    morning; Sunday's does nothing once the week is recorded. Run by hand with "test"
    ticked to try it without committing.
  - `weekly.yml`, started on Saturday at 06:00 Paris by `daily.yml` (above), Sunday as
    a fallback; no schedules of its own: `stt
    weekly prepare` (the data, plus the week's digest joined from the weekday digests by
    `digest.merge_week`), `stt review` (writes `data/reviews/`), `stt analyze`, `stt
    weekly finish` (10 buys and 5 avoids at 20 days, ledger, report, dashboard), commit
    and publish. The week's date is that Saturday, even on the Sunday fallback; a
    recorded week prints NOTHING TO DO. Unattended the review only *proposes* edits to
    `config/exposures.yaml` and the analysis prompt; apply them in a session if they
    hold up. "test" ticked does the week up to today into downloadable files (about
    $1.50 of API) and records nothing.
- **Claude Code cloud sessions** ran the pipeline until 2026-11-03 (runner session
  `session_013RsqR6xHbq73HAZ5KzSrjh`; scheduled tasks `trig_016rhLYvBBRMi2Z8uNFtrene`
  daily and `trig_017pWbZ6vhDLnh1ro7bUqwv8` weekly, both paused on 2026-11-04 after
  the first GitHub run was checked). `prompts/weekly_run.md` and
  `prompts/weekly_review.md` work for doing a week or a review by hand in a session.
  If two runs meet on one week, the second does nothing: `weekly prepare` stops when
  the week is recorded.
- Dashboards: the static one on GitHub Pages (`stt site`: `dashboard/index.html` in
  `dashboard/standalone/shell.html` with `dash.js` standing in for the dashboard
  runtime) and the claude.ai Dashboard artifact (`config/dashboard.yaml`), which only a
  Claude session can refresh.

## Pipeline and commands (`uv run stt ...`)

| Step | Command | Output |
|---|---|---|
| Weekday run (no API) | `stt collect` | ingest, trackers, evaluate, digest, watch, dashboard |
| Watch on open picks | part of `collect` and `weekly finish` | `data/watch/<date>.json` (overwritten the same day) |
| Ingest prices and news | `stt ingest` | `cache/prices/daily.parquet`, `cache/raw/<date>/news.jsonl` |
| Rank news into stories | `stt digest` | `data/digests/<date>.json`, `cache/raw/<date>/digest.md` |
| Saturday, step 1 | `stt weekly prepare` | the data, feedback note, the week's digest in `data/digests/<saturday>.json` |
| Review the week (Claude) | `stt review` (API), or `prompts/weekly_review.md` | `data/reviews/<date>.md` |
| Analyse (Claude) | follow `prompts/weekly_analysis.md`, or `stt analyze` (API) | `data/events/<date>.json` |
| Check the analysis | `stt validate --day <date>` | exit 1 on errors |
| Rank picks | `stt score` | `data/picks/<date>.json` |
| Log picks + baselines | `stt record` | `data/ledger/picks.jsonl` (append-only) |
| Score matured picks and analysis calls | `stt evaluate` | `data/ledger/outcomes.jsonl`, `data/ledger/impact_outcomes.jsonl` |
| Feedback note for the analysis | `stt feedback` | `cache/raw/<date>/feedback.md` |
| Write the report | `stt report` | `data/reports/<date>.md` (includes track record) |
| Saturday, step 2 | `stt weekly finish` | validate, score, record, scoreboard, report, dashboard |

`scoring.model.HORIZONS` is `(20,)`: one-month picks and baselines only. Events files
from `events.validate.WEEKLY_FROM` (2026-11-11) may only have 20-day impacts; older
ones keep their 5-day impacts, which still spill into the 20-day signal until they
fade. The verdict (100 matured picks) and the calibration count one-month picks only;
the 1-week picks logged until 2026-11-10 stay in the track record as history.

The weekday extras are free (no API; the owner wants weekdays to stay so): the watch
(`tracking/watch.py`) re-scores the open one-month picks' stocks with the events already
logged (`scoring.run.score_day(..., only=...)`) and compares the non-news part of the
score with the pick's: stronger, holding, weaker, reversed, or spent; plus flags for
earnings before the exit, insider or House trades filed since the pick, and stories
naming the company. The dashboard shows a Today card (what changed, data health), a
Case column in Open picks, News this week (`digest.week_so_far`), prediction-market
moves and "new" marks on filings. The weekly review reads the latest watch. None of it
changes a pick or the ledger.

Trackers: `stt trackers update` (new House STOCK Act filings and SEC Form 4 insider
trades → `data/trackers/`, run by `collect` and `weekly prepare`; `stt trackers insiders` for the SEC
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
