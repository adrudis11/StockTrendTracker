# Handoff: from the cloud sessions to local Claude Code (2026-11-10)

The project was built from 2026-10-28 to 2026-11-05 in Claude Code cloud sessions. The
owner now works on it in Claude Code on their own computer. Read this first, then
`CLAUDE.md` (rules and commands), `README.md` (how it runs, what to do when something
breaks) and the end of `docs/PLAN.md` (progress log).

## Where things stand

- **GitHub Actions runs everything** since 2026-11-04. No Claude session is needed
  for the daily or weekly runs.
  - The **daily run** (`daily.yml`) runs every weekday. It writes the picks, the ledger
    entries and the report, and refreshes the dashboard at
    <https://adrudis11.github.io/StockTrendTracker/>.
  - The **weekly review** (`weekly.yml`) runs on Saturdays and writes to `data/reviews/`.
- **Recorded days:** 10 so far (10-28 to 11-10). A Claude session analysed the days
  through 11-03; the Claude API has analysed them since 11-04 (`stt analyze`).
- **API cost:** $2.96 over 6 calls up to 11-10. That is about $0.45 per daily analysis
  and $0.65 per weekly review, so roughly $12 a month. Every call is logged in
  `data/usage/api.jsonl`.
- **Track record so far:** 57 matured geo picks, hit rate 25%, mean excess −2.89%.
  Most came from one theme (Hormuz) that reversed, and that is far too few to judge.
  Calibration and weight refits wait for 100 or more matured geo picks, expected late
  November to December. For comparison: momentum 75 matured, random 69, congress 50,
  insiders 50 (insiders lead).

## How it runs

| What | When | Notes |
|---|---|---|
| `daily.yml` | Weekdays, by an outside cron call at 06:00 Paris time | GitHub's own schedules (00:47, 02:47, 04:47 and 09:17 UTC) are a backup. A gate job makes an early one wait until 05:30 UTC. Only the `run` job holds the `pipeline` lock. |
| `weekly.yml` | Saturdays 08:41 UTC, with a Sunday fallback | The fallback skips if a review from the last 4 days exists. Unattended, it only proposes changes. |
| Manual runs | Actions → Run workflow | With "test" ticked, nothing is recorded. With "test" unticked, the day runs now, or on a recorded day the dashboard is refreshed. |

Settings that live on GitHub, not in the repo:
- **Secrets:** `ANTHROPIC_API_KEY_STOCKTRACKER` (the API key; the workflows pass it on as
  `ANTHROPIC_API_KEY`) and `STT_SEC_CONTACT` (the SEC contact email).
- **Variable:** `STT_RUNNER`, optional: `off` pauses the scheduled and outside-trigger
  runs, `github` forces them on.
- **Pages** source is set to GitHub Actions.

GitHub starts scheduled runs hours late or drops them. On the days so far, the backup
schedules started the runs between 07:12 and 07:30 UTC, and on 11-05 and 11-10 the day
had to be started by hand. The outside trigger fixes this, but see open item 3.

## Working locally

- **Pull first.** Run `git pull` before working: the bot commits every weekday morning
  and on Saturdays.
- **Push to `claude/beautiful-lovelace-8iabbf`.** It is the default branch, and the
  workflows run from it. `main` is only the stale base of PR #2.
- **Run the checks before pushing:**
  `uv run ruff check . && uv run ruff format --check . && uv run mypy src tests && uv run pytest`
- **Leave the daily and weekly runs to GitHub.** Don't run `stt daily prepare`,
  `stt daily finish`, `stt record` or `stt review` locally to produce real data: GitHub
  does that, and the commits would collide.
- **No secrets locally.** None are needed for development and tests. If the SEC part is
  ever needed locally, the contact goes in the gitignored `.sec_contact`, never in a
  committed file.
- **Test workflow changes on GitHub.** After pushing, use Actions → Run workflow with
  "test" ticked (daily and weekly both have it), then read the run's log.
- **Rules from `CLAUDE.md` that matter most:**
  - Never edit lines in `data/ledger/` or `data/trackers/`.
  - Don't re-score a recorded day.
  - Free data sources only; the Claude API is the one paid service.
  - Research tool: no trading.

## Open items, most useful first

1. **European prices may be stale.**
   - The 11-07 review saw European closes repeat across days (IAG.L 403–404p, TTE.PA
     €85.43, EZJ.L barely moving) and many European picks showing "–".
   - Every ingest also warns of about 280 stale stocks.
   - Check `src/stocktrend/ingest/prices.py` and its stale check, and how
     `report/dashboard.py` and `evaluate.py` read European prices, before trusting
     European results.
2. **Weekly review proposals from 11-07** (`data/reviews/2026-11-07.md`), not applied
   yet:
   - `config/exposures.yaml`, `shipping_disruption`: lower BOAT's weight by 0.3. It
     tracked oil sentiment, not tanker war risk.
   - `prompts/daily_analysis.md`, a no-stacking rule: no new call on a theme already
     called the same way in the last 3 sessions without a new physical or official
     fact.
   - `prompts/daily_analysis.md`: a leader's remark on trade is not policy. Keep the
     size small until official text confirms it.
   - The 11-05 test review also suggested: cap confidence when the main proxy has
     already moved 10% or more in 20 days, and anchor rates calls to market pricing.
3. **Outside trigger.** The owner sets this up on cron-job.org with a fine-grained token
   (README, "Exact timing"). As of 11-10, no run had come from it yet.
4. **Action versions.** `actions/checkout@v4`, `actions/upload-artifact@v4` and
   `astral-sh/setup-uv@v6` target Node 20. GitHub currently runs them on Node 24 with a
   warning. Bump them to newer major versions when convenient.
5. **Ideas offered, not built:**
   - An "Apply review" workflow: Claude applies the chosen proposals and opens a pull
     request.
   - `@claude` in GitHub issues through the Claude Code GitHub Action, restricted to the
     owner because the repo is public.
6. **Later work:**
   - Calibrate p(beat) and refit the score weights after 100 or more matured geo picks.
   - Rebuild the universe every few months (`uv run stt universe build`, then review
     `git diff data/universe/`).
7. **Tidy-ups:**
   - The claude.ai dashboard artifact (`config/dashboard.yaml`) is frozen at 11-03; the
     Pages dashboard replaced it.
   - PR #2 (`claude/beautiful-lovelace-8iabbf` → `main`) is open, and `main` is stale.
     Either merge it and make `main` the default branch (updating the branch name in
     both workflows' commit steps, the README and `CLAUDE.md`), or close it.

## The owner's preferences

- Writes short messages, often from a phone. For anything in GitHub or other settings
  pages, give step-by-step instructions with the exact names of the things to click.
- Wants to judge whether a signal is still worth acting on: "price then vs now"
  columns are on insiders, congress, Trump calls, picks and the track record.
- Wary of media spin. The analysis weighs official documents and hard data over
  coverage, and flags calls that go against the coverage (`against_coverage`).
- Wants it to run unattended. Say plainly when something failed or was skipped.
- Their email is used only as the SEC contact. Never commit it, and never ask for keys
  in chat: they add secrets in GitHub settings themselves.
