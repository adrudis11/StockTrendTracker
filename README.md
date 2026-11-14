# StockTrendTracker

Weekly analysis of political and geopolitical news to find liquid US and European stocks
and ETFs likely to outperform over the following month. It explains the reasoning behind
every pick and keeps an honest, automatically scored track record.

**Status:** Phase 5 (automation) set up; Phase 6 (paper-tracking period) running. Since
2026-11-11 the picks are made once a week, on Saturdays, and held for a month (20 trading
days); until 2026-11-10 they were made every weekday for 1 week and 1 month. See
[docs/PLAN.md](docs/PLAN.md) for the plan, phases and progress log.

> Research and decision-support tool for personal use. Not investment advice; it never
> places trades.

## How it runs

Two GitHub Actions workflows run everything:

- **Weekdays**, [.github/workflows/daily.yml](.github/workflows/daily.yml): `stt collect`
  gathers prices, the news since the last collection, prediction-market odds and new
  House and SEC insider filings, scores the picks whose month has ended, saves the day's
  news digest (`data/digests/`), checks every open pick's case (the watch,
  `data/watch/`), commits `data/` and refreshes the dashboard on GitHub Pages: what
  changed today, each pick's case, the week's news so far, prediction-market moves and
  new filings. It is free: no Claude API call and no new picks.
- **Saturdays**, [.github/workflows/weekly.yml](.github/workflows/weekly.yml): `stt weekly
  prepare` (the same data, plus the week's digest joined from the weekday ones), `stt
  review` (Claude looks back at the week and proposes changes), `stt analyze` (Claude
  reads the week's news and writes the events), `stt weekly finish` (10 picks to buy and
  5 to avoid, the ledger, the report, the dashboard), a commit of `data/` and the
  dashboard. These are the only Claude API calls.

The weekday run should be done before 07:30 Paris time, so the dashboard is fresh in
the morning: an outside cron service starts it at 06:00 (below), because GitHub starts
its own schedules hours late; those stay as a backup. A late or dropped run still leaves
no gap in the news: each run reads the news since the last digest (24 to 96 hours). The Saturday picks are logged
before Monday's open and scored from it, so GitHub starting a schedule hours late
changes nothing; there are three Saturday tries and a Sunday fallback, and a week that
is already recorded is never redone. To run the week at once, start the workflow by
hand (Actions → Weekly run → Run workflow, "test" unticked).

GitHub has run everything since 2026-11-04. Until 2026-11-03 the runs happened in a
Claude Code cloud session, which also kept a private claude.ai dashboard (no longer
updated). [prompts/weekly_run.md](prompts/weekly_run.md) and
[prompts/weekly_review.md](prompts/weekly_review.md) still work for doing a week or a
review by hand in a Claude session; if two runs meet on one week, the first records it
and the second does nothing.

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
4. **Test.** Actions → Weekly run → Run workflow, with "test" ticked. It does the week
   up to today with the real review and analysis, keeps them as a downloadable
   `test-week` file and records nothing. Its log ends with the number of events and
   the API cost. Actions → Daily collection → Run workflow with "test" ticked tries the
   free weekday run.
5. **Nothing to switch on.** The schedules started by themselves on 2026-11-04. The
   repository variable `STT_RUNNER` (Settings → Secrets and variables → Actions →
   Variables) overrides that: `github` starts them now, `off` pauses them.

Cost: one review and one analysis a week. The analysis reads the week's digest (about
250 stories, roughly 1.6 times a single day's) plus the cached instructions and ticker
list; the review reads the scoreboard and the week's results. At the prices in
[config/analysis.yaml](config/analysis.yaml) that is about $1.50 a week, $6–8 a month
(the daily analysis cost about $0.45 a day). Every call's actual cost is logged in
`data/usage/api.jsonl`. GitHub Actions and Pages are free for a public repository.

### On time every morning: the outside trigger (cron-job.org)

GitHub starts its own schedules hours late (runs set for 00:47 UTC started at 07:12 to
07:44 UTC in November 2026) and sometimes drops them. So that the weekday dashboard is
ready before 07:30 Paris time, a free outside cron service starts the daily workflow at
06:00 Paris time every day; the weekday run takes about three minutes. On Saturdays and
Sundays the same call starts the weekly run instead (review, analysis, the week's picks;
about six minutes), so one job covers both; Sunday only acts if Saturday's week wasn't
recorded. GitHub's schedules stay as a backup.
Set up once, about 10 minutes. The token goes only into GitHub and cron-job.org.

**1. A GitHub token that can only start this repository's workflows**

1. On github.com, click your profile picture (top right) → **Settings** → at the
   bottom of the left sidebar **Developer settings** → **Personal access tokens** →
   **Fine-grained tokens** → **Generate new token**.
2. **Token name:** `StockTrendTracker cron`. **Expiration:** the longest offered
   (GitHub emails you before it expires; then generate a new one and paste it into
   the cron job).
3. **Repository access:** *Only select repositories* → `adrudis11/StockTrendTracker`.
4. **Permissions** → *Repository permissions* → **Actions** → *Read and write*. Leave
   the rest at *No access* (*Metadata: Read-only* is added automatically).
5. **Generate token** and copy it (it starts with `github_pat_` and is shown once).

**2. The job at cron-job.org**

1. Sign up at [cron-job.org](https://cron-job.org) (free) and confirm your email.
2. **Cronjobs** → **Create cronjob**.
3. **Title:** `StockTrendTracker daily`. **URL:**
   `https://api.github.com/repos/adrudis11/StockTrendTracker/actions/workflows/daily.yml/dispatches`
4. **Schedule:** every day at 06:00, time zone **Europe/Paris** (in crontab form
   `0 6 * * *`). Every day, not Monday to Friday: the Saturday call makes the week's
   picks. It follows summer and winter time by itself.
5. **Advanced:**
   - **Request method:** `POST`
   - **Headers** (add four):
     - `Authorization` = `Bearer ` followed by your token
     - `Accept` = `application/vnd.github+json`
     - `X-GitHub-Api-Version` = `2022-11-28`
     - `Content-Type` = `application/json`
   - **Request body:**
     `{"ref":"claude/beautiful-lovelace-8iabbf","inputs":{"test":"false","trigger":"cron"}}`
   - Turn on the email notification for failed executions.
6. **Save**, then press **Test run**. The response should be `204 No Content` and a
   new "Daily collection" run appears in the Actions tab within a minute. On a day that
   is already collected it only refreshes prices and the dashboard. On a Saturday or
   Sunday it starts the weekly run (a real one, about $1.50 of API, unless the week is
   already recorded), so press Test run on a weekday.

The token can only start, cancel and read Actions runs of this repository. The weekday
run makes no Claude API call; the Saturday run is the week's one review and analysis. `STT_RUNNER` = `off` also
pauses the outside trigger's runs; your own manual runs always go.

### If something goes wrong

Everything below is in the repository's **Actions** tab or **Settings**; no Claude
session is needed.

| What you see | What to do |
|---|---|
| An email from GitHub that "Weekly run" failed | Open the run, look at the red step. A data source down or an API error: Actions → Weekly run → Run workflow ("test" unticked) runs the week again. A week that is already recorded is left alone. |
| An email that "Daily collection" failed | Usually a data source down; the next run picks up the news since the last digest, so nothing is lost. Run it by hand ("test" unticked) to refresh the dashboard now. |
| No "Collect … (GitHub Actions)" commit by about 06:10 Paris time on a weekday | Check cron-job.org's execution history (a 401 means the token expired or was revoked: make a new one). Actions → Daily collection → Run workflow ("test" unticked) runs it now. |
| No "Weekly run … (GitHub Actions)" commit by Sunday | Run it by hand as above; Monday's open is what counts. |
| "ANTHROPIC_API_KEY is not set" or an authentication error | The `ANTHROPIC_API_KEY_STOCKTRACKER` secret is missing, wrong or the key was revoked: create a new key in the Anthropic Console and update the secret. |
| A billing or credit error from the API | Add credit or raise the spend limit in the Anthropic Console. |
| "model not found" or a deprecation error | Claude models are retired after some time: put a current model in `config/analysis.yaml` (`model:`). |
| Want to pause everything | Settings → Secrets and variables → Actions → Variables: `STT_RUNNER` = `off` (delete it to resume). |
| The weekly review proposes changes | It can't make them itself: apply them to `config/exposures.yaml` or `prompts/weekly_analysis.md`, or ask a Claude session to. |

Now and then (every few months) the stock universe should be rebuilt to follow index
changes: `uv run stt universe build`, then review `git diff data/universe/` (in a
Claude session or locally). The theme priors refresh themselves monthly.

## Setup

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

## Usage

```bash
uv run stt ingest              # prices + news since the last digest (24-96h) into cache/raw/<today>/
uv run stt ingest --skip prices --skip gdelt_doc
uv run stt calendar            # which exchanges trade today
uv run stt universe show       # stocks covered, by country and sector
uv run stt universe build      # refresh data/universe/stocks.csv from Wikipedia (monthly)
uv run stt digest --print      # group and rank today's news into ~150 stories
uv run stt validate            # check Claude's data/events/<today>.json
uv run stt score               # rank one-month picks (20 trading days) -> data/picks/
uv run stt record              # append picks + baselines to the ledger (append-only)
uv run stt evaluate            # score picks, and every analysis call, whose horizon has ended
uv run stt feedback            # how past calls did, by kind: read by the weekly analysis
uv run stt scoreboard          # track record vs momentum and random baselines
uv run stt report              # the week's Markdown report -> data/reports/
uv run stt backtest            # 5-year momentum baseline, theme priors, tracker backtest
uv run stt trackers update     # new House trade filings (STOCK Act) and SEC Form 4s -> data/trackers/
uv run stt trackers insiders   # SEC Form 4 insider trades alone (needs a contact email, see below)
uv run stt trackers show       # strongest congressional and insider buying, last 30 days
uv run stt themes              # geopolitical themes and their main winners/losers
uv run stt universe find airbus
uv run stt analyze             # the analysis through the Claude API (needs ANTHROPIC_API_KEY)
uv run stt review              # the weekly review through the API (proposes edits, makes none)
uv run stt site                # the dashboard as a static site in site/ (after `stt dashboard`)
uv run stt collect             # the weekday run: data, digest, scoring, dashboard (no API)
uv run stt weekly prepare      # the Saturday run, step 1: data and the week's digest
uv run stt weekly finish       # step 2, after the review and analysis: picks, ledger, report
```

The analysis step between `weekly prepare` and `weekly finish` follows
[prompts/weekly_analysis.md](prompts/weekly_analysis.md): either `stt analyze`, or Claude
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
data/digests/      ranked stories: daily, and the week's on Saturdays (committed, so events can be traced to sources)
data/events/       Claude's structured analysis, weekly since 2026-11-14 (committed)
data/picks/        ranked picks with every score component (committed)
data/reports/      Markdown reports, weekly since 2026-11-14 (committed)
data/ledger/       every pick ever made and its outcome (append-only, committed)
data/backtests/    momentum baseline results; data/priors/ theme priors and tracker weight
data/trackers/     House members' and company insiders' disclosed trades (append-only, committed)
data/usage/        tokens and cost of every Claude API call
data/watch/        the weekday watch on open picks: each one's case and flags, by day
dashboard/         the dashboard page; standalone/ turns it into the GitHub Pages site
.github/workflows/ the weekday collection and the Saturday run
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
