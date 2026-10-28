# StockTrendTracker — Project Plan

> Status: **Phase 5 (automation) set up** · Phase 6 (paper tracking) running · Last updated: 2026-10-28
>
> This is a research and decision-support tool for personal use. It is **not investment
> advice**, and it never places trades.

---

## 1. Goal

Each trading day, produce a short ranked list of liquid **US and European** stocks (and
sector/country ETFs) that the current **political and geopolitical situation** makes likely to
**outperform over the next days to weeks**. Every pick comes with:

- the event(s) driving it and the cause-and-effect chain (event → sector/company → why it should move),
- an estimated probability of beating the benchmark over **5 trading days (~1 week)** and
  **20 trading days (~1 month)**,
- the risks and what would invalidate the idea,
- links to the sources it was based on.

The system also **keeps score on itself**. Every pick is logged and checked automatically once
its horizon ends, so we learn from real results whether it has an edge, not from anecdotes.

### What "great chance to go up" means here

In a rising market almost every stock goes up, so "it went up" says little about whether the
pick was any good. We track two things:

1. **Absolute:** did the stock go up over the horizon?
2. **Relative (the main metric):** did it beat its own market's benchmark (S&P 500 for US
   stocks, STOXX Europe 600 for European stocks) and its sector? Each stock is compared in
   its own currency, so euro/dollar moves don't distort the result.

A pick only counts as good if it beats what you would have earned without the tool.

### Honest expectations

Markets react to headline news within minutes, so an edge, if there is one, is most likely to
come from:

- **Second-order effects:** sanctions on country A help competitors in country B; a conflict
  pushes up shipping rates, which helps tanker owners.
- **Slow-moving policy:** tariffs, defense budgets, subsidies and regulation that take effect
  over weeks or months, which markets often under-react to at first.
- **Breadth:** following more sources every day than a person can, and doing it consistently.

We will not trust the output until it has a track record (see Phase 6).

---

## 2. Constraint: built and run on cloud session credits only

The project is **built and run entirely in Claude Code cloud sessions** unless you decide
otherwise. In practice:

| Concern | How we handle it |
|---|---|
| Development | All code is written in cloud sessions and pushed to this GitHub repo. |
| Daily run | A **scheduled cloud session** runs the pipeline every trading day. No local machine or server. |
| AI reasoning | Claude reads the news **inside the scheduled session itself**. No separate Anthropic API key and no API bill. |
| Data | **Free sources only** (no paid market-data or news APIs). |
| State | Containers are wiped after each session, so everything we need to keep (pick ledger, daily events, reports) is **committed to git**. Raw price data is re-downloaded rather than stored. |
| Credit efficiency | Python does the heavy work (downloading, filtering, maths). Claude reads only a capped daily digest of candidate events, not thousands of raw articles. |
| CI | Tests run inside sessions. GitHub Actions is optional and needs your OK (it runs on GitHub minutes, not session credits). |

### ⚠️ Blocker: network access

This environment currently allows only package managers (PyPI, npm, …) and GitHub. On
2026-10-28 I tested these hosts and **all were blocked by the network policy (HTTP 403)**:
Yahoo Finance, Stooq, GDELT, SEC EDGAR, FRED, Google News RSS, Federal Register,
Congress.gov, USAspending, BBC RSS, ECB.

Before Phase 1 data work can start, these domains need to be added in the environment's
settings (cloud environment menu in the session title bar → Edit → Network access → Allowed
domains, with "Allow package managers" left ticked). Docs:
https://code.claude.com/docs/en/cloud-environments#network-access

**Minimum allowlist (Phases 1–3):**

```
query1.finance.yahoo.com
query2.finance.yahoo.com
fc.yahoo.com
finance.yahoo.com
stooq.com
api.gdeltproject.org
data.gdeltproject.org
www.federalregister.gov
www.sec.gov
data.sec.gov
fred.stlouisfed.org
api.stlouisfed.org
news.google.com
feeds.bbci.co.uk
en.wikipedia.org
ec.europa.eu
eur-lex.europa.eu
www.ecb.europa.eu
data-api.ecb.europa.eu
```

**Later (optional):** `gamma-api.polymarket.com` (prediction markets),
`api.congress.gov` (legislation), `api.usaspending.gov` (government contracts),
`www.federalreserve.gov`, plus any extra news RSS feeds we pick.

Scheduled sessions use the same environment, so they inherit this allowlist.

---

## 3. Architecture

```
┌─────────────────────────── Scheduled cloud session (each trading day) ───────────────────────────┐
│                                                                                                   │
│  1. INGEST (Python)          2. DIGEST (Python)        3. ANALYSE (Claude)       4. SCORE (Python) │
│  ─ News & events      ──►   dedupe, cluster,    ──►   classify events,   ──►   combine with       │
│  ─ Government sources       rank by volume &          map to themes &          price/quant        │
│  ─ Market prices            novelty, cap size         tickers, write           features, rank     │
│  ─ Macro indicators                                   validated JSON           picks              │
│                                                                                     │              │
│  6. PUBLISH  ◄──────────────────────────  5. TRACK & EVALUATE (Python) ◄────────────┘              │
│  report.md + dashboard,                   log picks, score matured picks,                         │
│  commit & push to git                     update scoreboard & calibration                         │
└───────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Ingestion (free sources)

| Type | Source | Why |
|---|---|---|
| Global news & events | **GDELT 2.0** (DOC API + 15-minute event/GKG files) | Free, worldwide, already tagged with themes, countries and tone; history back to 2015 for backtesting. |
| Headlines | RSS: Google News topic searches (US and European editions), BBC World, more to choose | Readable headlines and summaries for Claude. |
| US executive actions | **Federal Register API** | Executive orders, tariffs, sanctions rules, the primary source rather than coverage. |
| EU actions | **European Commission press corner**, **EUR-Lex** (Official Journal), **ECB press releases** | EU sanctions packages, trade measures, regulation, monetary policy. |
| Company data | **SEC EDGAR** (US); index constituent lists (Wikipedia) + Yahoo profiles for European sector and industry | Ticker ↔ company ↔ industry; 8-K filings later. |
| Prices | **yfinance** (Yahoo, unofficial); Stooq as fallback if it becomes reachable (see §10) | Daily OHLCV for US and European exchanges (`.DE`, `.PA`, `.MC`, `.L`, `.MI`, `.AS`, `.SW`), ETFs, VIX, oil, gas, EUR/USD. |
| Macro | **FRED** (free API key), **ECB Data Portal** | US and euro-area yields, curves, currencies, oil, for market-regime context. |
| Later | Polymarket / Kalshi, Congress.gov, USAspending, ECB/Fed releases, OFAC updates | Event probabilities, legislation, defense contracts, central banks, sanctions. |

### 3.2 Universe and exposure map

- **Universe v1:**
  - **US:** S&P 500 members.
  - **Europe:** members of the main indices, with duplicates removed (Euro STOXX 50, DAX 40,
    CAC 40, IBEX 35, FTSE 100, FTSE MIB, AEX, SMI), about 350 companies.
  - **ETFs:** ~40 US and European sector, industry and country ETFs (XLE, ITA, SMH, EWZ,
    STOXX Europe 600 sector ETFs, …).
  - **Liquidity filter:** market cap > 2B (USD/EUR equivalent) and average daily traded
    value > $20M (US) or > €10M (Europe, where volumes are lower).
- **Exposure map** (`exposures.yaml`): a human-readable file mapping **geopolitical themes →
  likely winners and losers on both sides of the Atlantic**, e.g. *Middle East escalation →
  oil producers, defense, tankers ↑; airlines, cruise lines ↓*. It starts with ~25 themes
  (US and European defense spending, US–China and US–EU tariffs, chip export controls,
  European gas supply, energy supply shocks, rare earths, shipping disruption, agriculture,
  elections, US and EU sanctions, reshoring subsidies, …). Claude proposes changes and you
  can review them in git.

### 3.3 Event analysis (Claude, inside the session)

Claude reads the daily digest (around 150 headlines/summaries at most, plus Federal Register
items) and follows `prompts/daily_analysis.md` to:

1. Group articles into distinct **events** and drop what is old news.
2. Classify each event: category (sanctions, tariff, conflict, election, regulation, subsidy,
   central bank, supply disruption), regions, severity, **novelty** (genuinely new or already
   known?) and expected duration.
3. Map each event to **themes and tickers** with direction, size, confidence, horizon and a
   one-line causal chain.
4. Write `data/events/YYYY-MM-DD.json`, which Python then **validates** against a strict schema.

Guardrails against made-up output:
- tickers must exist in the universe;
- every source URL must come from that day's ingested set;
- the run fails loudly on invalid JSON rather than continuing on bad data.

Example of one event (illustrative, not real):

```json
{
  "event_id": "2026-10-28-003",
  "title": "Hypothetical: new export controls on advanced chip equipment",
  "category": "regulation",
  "regions": ["US", "CN"],
  "novelty": 0.8,
  "severity": 0.6,
  "sources": ["https://www.federalregister.gov/d/..."],
  "impacts": [
    {
      "target": "theme:domestic_chip_equipment",
      "direction": -1,
      "magnitude": 0.4,
      "confidence": 0.6,
      "horizon_days": 20,
      "rationale": "Lost China sales for US equipment makers; partly priced in if leaked earlier"
    }
  ]
}
```

### 3.4 Quant layer (Python)

- **Price features:** 5/20/60-day momentum, strength relative to sector, volatility, unusual
  volume, distance from 52-week high.
- **"Already priced in" check:** if a stock already moved strongly on the news, its score drops.
- **Risk filters:** earnings date inside the horizon (a coin flip unrelated to geopolitics),
  very high volatility, low liquidity.
- **Market regime:** VIX and VSTOXX, US and euro-area yield curves, EUR/USD, oil and European
  gas trend. Risk-off regimes favour defensives.
- **Event-study prior:** historically, how did a sector perform in the N days after similar
  GDELT event spikes? This part **can be backtested** on 2015+ data.

### 3.5 Scoring (v1: transparent formula)

```
geo        = tanh(1.5 × Σ direction × member weight × magnitude × confidence × novelty × age decay)
conviction = |geo| × (1 + 0.4·trend) × (1 + 0.4·prior) × (1 − 0.5·priced_in) × risk
score      = sign(geo) × conviction
```

- `trend`: price move agreeing with the thesis, up to +1.5σ (negative if it disagrees).
- `prior`: historical reaction of the theme's members after big moves in its driver.
- `priced_in`: the part of a move beyond 2σ that already happened.
- `risk`: multiplier for earnings inside the horizon (×0.4 for 5 days, ×0.7 for 20),
  unknown earnings dates (×0.95) and very high volatility (×0.8). Illiquid → excluded.

The geopolitical signal is the base and the other terms scale it, so price momentum
alone can never create a pick (an additive first version let momentum dominate).
Events fade linearly over their own horizon. The weights are set by hand at first. Once enough picks have matured, they are fitted
(logistic regression) and the scores are **calibrated**, so that "70%" really means about
7 out of 10.

Output: top **10 picks per horizon** (5d and 20d), plus a short "avoid" list for strongly
negative signals.

### 3.6 Tracking and evaluation

- **Append-only ledger** (`data/ledger/picks.jsonl`) committed to git. Git history makes it
  tamper-evident, so the record can't be quietly rewritten afterwards.
- Entry price = **next open on the stock's own exchange** after the pick (no hindsight).
- After each horizon ends, picks are scored automatically against their market benchmark
  (S&P 500 or STOXX Europe 600) and sector, in local currency, counting horizons in that
  exchange's trading days (holidays differ between the US and Europe).
- **Metrics:** hit rate (absolute and vs benchmark), average excess return, Brier score and
  calibration, and comparison against **simple baselines**: momentum-only picks and random
  picks from the same sectors. If we can't beat these, the geopolitical layer isn't adding value.

### 3.7 Output

- **Daily report** `data/reports/YYYY-MM-DD.md`: market context, top events, picks with
  rationale, sources and risks, and the running scoreboard.
- **Dashboard:** a private claude.ai Artifact page updated by each run, showing the picks,
  open positions and track record.
- **Later (optional):** email or Telegram alerts.

---

## 4. Important caveat: why we can't simply backtest the AI part

Claude already knows, from its training, what happened after past news events. If we asked it
to "analyse" 2022 news, it would already know the outcome, so a backtest would look far better
than reality (**look-ahead bias**). Therefore:

- **The quant parts** (event-study priors, price features, baselines) are backtested on history.
- **The AI judgement part** is evaluated **only going forward**, through paper tracking from the
  first live day. That's why Phase 6 exists and can't be skipped.

---

## 5. Tech stack

- **Python 3.13**, managed with **uv**
- `requests`, `feedparser` (ingestion) · `yfinance` (prices) · `pandas` (data) · `pydantic`
  (schemas and validation) · `duckdb` (local queries) · `typer` (CLI) · `jinja2` (reports) ·
  `exchange_calendars` (trading days and holidays per exchange)
- `pytest`, `ruff`, `mypy` for quality
- Storage: JSON/JSONL/Markdown in git for anything we must keep; Parquet caches that are
  re-downloaded each session.

### Repository layout (target)

```
StockTrendTracker/
├── README.md
├── docs/PLAN.md                  ← this file
├── pyproject.toml
├── prompts/
│   ├── daily_analysis.md         instructions Claude follows in the daily run
│   └── weekly_review.md          instructions for the weekly self-review
├── src/stocktrend/
│   ├── cli.py                    `stt ingest | digest | validate | score | evaluate | report`
│   ├── ingest/                   gdelt.py, rss.py, federal_register.py, prices.py, macro.py
│   ├── universe/                 universe.py, exposures.yaml
│   ├── events/                   schema.py (pydantic), digest.py
│   ├── features/                 technical.py, regime.py, event_study.py
│   ├── scoring/                  model.py
│   ├── tracking/                 ledger.py, evaluate.py
│   └── report/                   render.py, templates/
├── data/
│   ├── events/YYYY-MM-DD.json
│   ├── ledger/picks.jsonl
│   └── reports/YYYY-MM-DD.md
└── tests/
```

---

## 6. Daily run (scheduled cloud session)

Runs on weekdays **before the European open (09:00 CET)**. The US closed the evening before
and opens hours later, so one run covers both markets:

1. Install dependencies (`uv sync`).
2. `stt ingest`: news, Federal Register, EU sources and prices for the last 24 hours.
3. `stt digest`: deduplicated, ranked, size-capped bundle of candidate events.
4. Claude reads the digest and writes `data/events/<date>.json`, then runs `stt validate`.
5. `stt score`, `stt evaluate` (score matured picks), `stt report`.
6. Commit and push, then update the dashboard.

**Weekly review session:** Claude compares last week's picks with what actually happened,
writes down what it got wrong and why, and proposes changes to `exposures.yaml` and the
prompt as a commit you can review. To avoid overfitting to a few weeks of data, the
**weights are not retuned weekly**.

---

## 7. Phases and milestones

| Phase | What | Done when |
|---|---|---|
| **0 · Planning** | This document, decisions (§8), GitHub access, network allowlist. | Decisions made ✅; GitHub access and the allowlist are in place. |
| **1 · Foundation** ✅ | Project skeleton, US + European universe and ETFs, exchange calendars, price download, GDELT + RSS + Federal Register + EU sources ingestion, storage layout, tests. | `stt ingest` gathers a full day of data for both markets reliably. |
| **2 · Event analysis** ✅ | Event schema, digest builder, `daily_analysis.md` prompt, exposure map v1 (~20 themes), validation. | One manual run turns a day's news into sensible, validated event → ticker mappings. |
| **3 · Quant + scoring** ✅ | Price features, regime, priced-in check, risk filters, scoring v1, report v1. | A daily report with top-10 picks per horizon and full rationale. |
| **4 · Tracking + backtest** ✅ | Ledger, automatic scoring, baselines, scoreboard; event-study backtest of the quant layer on GDELT history. | Scoreboard works; we know whether the quant layer on its own beats baselines historically. |
| **5 · Automation** ✅ | Scheduled daily session, dashboard, weekly review, credits-per-run measured. | Runs unattended for 2 weeks without manual fixes. |
| **6 · Paper-tracking period** | At least 8–12 weeks of live tracking, then calibrate weights; then consider expanding (EU stocks, prediction markets, alerts). | ≥100 matured picks with an honest verdict: does it beat SPY and the baselines? |

**Overall success criterion:** after Phase 6, the 20-day picks beat both their market
benchmark and the sector-matched baselines on average excess return, with sensible calibration. If they don't,
we either iterate or keep it as a geopolitical briefing tool rather than a stock picker. That
is still useful, but we'd be clear about what it is.

---

## 8. Decisions (2026-10-28)

1. **Markets:** US **and** Europe from the start (§3.2).
2. **Schedule:** weekdays, before the European open (09:00 CET).
3. **Paid data:** none. All free sources.
4. **GitHub Actions:** not used; tests run in sessions.
5. **Risk profile:** liquid stocks and ETFs only; no penny stocks, options or leverage.
6. **Where you read results:** daily Markdown report in the repo + private dashboard page.
   No email or Telegram alerts for now.

**Still needed from you:** the network allowlist in §2. *This blocks real data in Phase 1.*

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| News is priced in within minutes | Focus on second-order and slow-moving policy effects; priced-in penalty; measure against baselines. |
| Look-ahead bias when evaluating the AI | Forward-only evaluation of the AI layer (§4). |
| AI invents tickers or sources | Universe check, source URLs must come from the ingested set, strict schema validation. |
| Free data sources break or rate-limit (yfinance is unofficial) | Several independent news sources, retries, health checks that fail the run loudly (§10). |
| Overfitting to a small sample | No weight tuning before ~100 matured picks; baselines; out-of-sample checks. |
| Session credits per run grow over time | Capped digest size; measure credits per run in Phase 5; Python does the heavy lifting. |
| Ephemeral containers lose state | Everything important is committed to git at the end of each run. |
| Mixing markets distorts results (currencies, holidays, different trading hours) | Score each stock in local currency against its own market's benchmark; per-exchange calendars. |
| European data is thinner on free sources (earnings dates, sector tags) | Cross-check Yahoo with index lists; tag missing earnings dates as a risk instead of assuming none. |
| Source terms of use | Personal, non-commercial use; respect rate limits; store headlines and links, not full articles. |

---

## 10. Progress log

### Phase 1 · Foundation (done 2026-10-28)

Built: `stt universe build|show`, `stt calendar`, `stt ingest`. See the README for usage.

First live run (2026-10-28, 09:05 UTC, 24h window, ~2.5 minutes):

| Source | Result |
|---|---|
| Prices (Yahoo) | 914/914 tickers (805 stocks + 109 ETFs, indices, macro), ~13 months of daily bars |
| Google News (12 topics × US/UK editions) | ~1,400 unique items after de-duplication |
| BBC World/Business, ECB, European Commission | ok |
| Federal Register | 4 presidential documents + 14 agency rules/notices |
| GDELT event files | ~120k events; 300 most-covered international events kept as items, plus a per-country aggregate |
| GDELT article API | rate-limited (HTTP 429), skipped with a warning |

Findings and deviations from the plan:

- **GDELT article API is rate-limited from this cloud IP** (HTTP 429 even on the first
  request; the egress IP is shared). It is best effort; GDELT's raw 15-minute event files
  work and are the main GDELT source instead.
- **GDELT events are noisy.** Machine coding marks some local stories as international.
  Google News is the best headline source; Phase 2's digest should weight stories by how
  many independent feeds carry them (`extra.also_in`).
- **Stooq** accepts the connection and then drops it, so there is no price fallback yet.
- **EUR-Lex** answers with an anti-bot challenge. EU actions come from the Commission
  press corner and ECB feeds for now.
- **VSTOXX** isn't on Yahoo; VIX is the only volatility index.
- **Wikipedia blocks the `httpx` client**, so the project uses `requests`.
- The universe has 805 stocks (503 US, 302 European in 9 countries). Index membership
  stands in for the market-cap filter; the traded-value filter will be applied from price
  data in Phase 3.
- Prices only include completed sessions: when a market is open, Yahoo's partial bar for
  today is dropped.

### Phase 2 · Event analysis (done 2026-10-28)

Built: `stt digest`, `stt validate`, `stt themes`, `stt universe find`;
`config/exposures.yaml` (28 themes, 355 stock/ETF links), `prompts/daily_analysis.md`,
`CLAUDE.md` for future sessions.

- **Digest:** 1,736 items → 1,462 stories (headlines grouped by word overlap, Jaccard ≥
  0.35) → top 150 by publisher count, official-source bonus and a per-topic floor.
  The Markdown version is ~6,500 tokens, which keeps the daily analysis cheap. It opens
  with a market snapshot (key indices, oil, gas, FX, theme ETFs, biggest 5-day movers)
  so the analyst can judge what is already priced in.
- **First analysis (2026-10-28):** 13 validated events, e.g. the Houthi strike on Ras
  Tanura, mines near Hormuz, Qatari mediation, Qatar LNG force majeure, the Kherson truce
  and possible US sanctions relief for Russia, US-EU friction over an Airbus loan, Canada
  auto tariff threats, US wind-turbine tariffs, Belgian nuclear nationalisation, and the
  Israeli election deadlock.
- Universe corrections: 7 duplicate listings dropped (e.g. SHELL.AS kept as SHEL.L), 8
  Spanish/Italian utilities moved from Energy to Utilities. Universe is now 798 stocks.
- Federal Register presidential documents carry no abstract and their full text sits
  behind a CAPTCHA, so the digest now shows the **signing date**: the three documents
  published on 28 October were signed on 22 October, so markets already knew.
- One headline ("Supreme Court invalidates most of Trump's tariffs") appeared in only one
  item with no summary, so it was not treated as new. Single-source big claims need
  corroboration before they become events.

### Phase 3 · Quant + scoring (done 2026-10-28)

Built: `stt score` (→ `data/picks/<date>.json`) and `stt report` (→ `data/reports/<date>.md`);
modules `features/technical.py` (returns, volatility, excess vs benchmark and sector,
liquidity in USD/EUR), `features/regime.py`, `features/earnings.py`, `scoring/model.py`,
`report/render.py`.

- First run (2026-10-28, regime neutral, VIX 17.5): 1-week longs XLE, BOAT, EOG, DVN;
  avoid IAG, easyJet, JETS, EIS, Stellantis. 1-month longs XOP, EQT, EPOL; avoid
  Stellantis, Airbus, European autos, GM, LVMH.
- **Earnings season matters:** many names touched by today's events (Exxon, Shell,
  Airbus, Stellantis, GE Vernova) report within days, so they're down-weighted; results
  would swamp a geopolitical signal over a week.
- Lists are short by design: at most 3 picks per theme and 3 ETFs per list, and weak
  calls (geo < 0.10) don't qualify. More picks will qualify as aligned events build up
  over several days.

### Phase 4 · Tracking and backtests (done 2026-10-28)

Built: `stt record` (append-only ledger with momentum and random-sector baselines),
`stt evaluate` (scores matured picks), `stt scoreboard` (hit rate, excess return,
t-statistic, Brier score, calibration), `stt backtest` (5-year momentum baseline and
theme priors). The daily report now ends with the track record.

- **Ledger started 2026-10-28** with 77 entries: 17 geopolitical picks plus 60 baseline
  picks (20 `congress` entries were added later that day, when the tracker arrived). Entry is the first open after logging (today's were logged at 09:38 UTC, after
  Europe's open, so European picks enter on 29 October). First results: 3 November.
- **Momentum baseline, 5 years** (2021–2026, current index members, so flattered by
  survivorship bias): 5-day picks +0.14% excess per period, hit rate 48%, t = 0.7 (no
  edge); 20-day picks +2.0% per period, hit rate 50%, t = 1.9. The 20-day bar is
  demanding.
- **Theme priors** (members' excess return after the driver spikes > 2σ in the theme's
  direction): mostly noise (|t| < 2). Notable: after Brent spikes, Middle East
  escalation stocks *gave back* ~0.9% over 5 days (t = −1.9); cyber-security stocks kept
  rising after CIBR spikes (+6.9% over 20 days, t = 2.0).
- Because priors are measured *after* driver spikes, the model only applies a prior
  while that driver is spiking now (5-day z ≥ 1.5), and shrinks it by min(1, |t|/2).
  On 2026-10-28 only the cyber theme qualified, and no event targeted it, so the logged
  picks were unaffected.
- Writing the tests caught a sign error: priors had been flipped for "avoid" calls.
  Fixed before any prior was ever applied.

### Phase 5 · Automation (set up 2026-10-28)

Built: `stt daily prepare` / `stt daily finish`, `stt dashboard`, runbooks
`prompts/daily_run.md` and `prompts/weekly_review.md`, the claude.ai dashboard
(`config/dashboard.yaml`, page source in `dashboard/index.html`).

How the runs are wired:

- **Runner session** `session_013RsqR6xHbq73HAZ5KzSrjh` ("StockTrendTracker daily
  runner"), created with this repository and branch attached, auto permission mode,
  Opus 5.5. Two scheduled tasks wake it:
  - **Daily run**, weekdays 07:17 Paris time (`trig_016rhLYvBBRMi2Z8uNFtrene`).
    First run: 2026-10-29.
  - **Weekly review**, Saturdays 09:43 Paris time (`trig_017pWbZ6vhDLnh1ro7bUqwv8`).
    First run: 2026-10-31.
- Why a runner session: a test showed that a scheduled task created from inside a
  session starts a fresh session **without the repository** (empty working
  directory). A session created with the repository attached can clone and push, so
  the schedules send their prompts into that one session. Its conversation grows over
  time; auto-compaction keeps it bounded.
- Setup checks: the runner passed all five (repo and branch, `uv sync` and CLI,
  Yahoo prices, push dry-run, artifact tools) at about $0.26 of usage at list prices.
  The cost of a full daily run will be measured after the first runs.
- **Dashboard:** a private claude.ai Dashboard artifact with four datasets
  (summary, picks, events, scoreboard) uploaded as JSON by each daily run.

Still to watch in the first two weeks (the Phase 5 exit criterion):

- that every weekday run commits, and that the dashboard refresh works unattended;
- the cost per run;
- Yahoo rate limits: a plain `curl` to Yahoo from a fresh session got HTTP 429, while
  yfinance's own client worked.

### Phase 6 · Paper-tracking period (started 2026-10-28)

Runs automatically: the ledger grows every weekday, `stt evaluate` scores picks as
they mature (first 1-week results on 2026-11-03), and the weekly review writes
`data/reviews/`. A verdict needs at least 100 matured geopolitical picks, which at the
current pace (around 5–15 per day) means late November to December. Only then:
calibrate the probabilities and refit the weights.

### Trackers (added 2026-10-28)

Request: add "trackers" such as the Cramer, Pelosi and Trump accounts on X, if they get
more right than wrong.

What was added:

- **US House trades** (the data behind "Pelosi tracker" accounts): official STOCK Act
  Periodic Transaction Reports from the House Clerk, parsed from the filings' PDFs.
  Backfill 2024–2026: 1,403 filings (819 with stock trades, 408 without, 176 scanned
  paper forms that can't be read), 11,510 trades by 130 members. Dividend reinvestments
  are dropped. New filings are picked up by every daily run.
- **Polymarket odds** for the top geopolitics, politics and economy questions, shown in
  the digest (e.g. on 2026-10-28: US–Iran ceasefire holding through 31 October 94%,
  through 15 November 52%).
- **Trump's Truth Social posts**, through a Google News topic search: outlets report
  market-moving posts within minutes.

Did it get more right than wrong? Backtest (filings public before each day, current
S&P 500 members, vs SPY, 2024-02 to 2026-10):

| Strategy | 5 days | 20 days |
|---|---|---|
| Top 10 net House buys | +0.04%, hit 49%, t = 0.2 | +0.23%, hit 48%, t = 0.3 |
| Net House sells (as avoids) | lag +0.17%, t = 1.1 | −0.30%, t = −0.4 |
| Momentum, same window | +0.51%, t = 1.7 | +4.53%, t = 3.6 |

Single trades, from the first session after disclosure: Pelosi household purchases
(26) −1.65% at 5 days; +2.9% mean but −1.2% median at 20 days, hit 42%. Purchases of
$250k or more by any member (42): +2.9% mean and +2.7% median at 20 days, hit 55%,
t = 1.5. That's suggestive, not proven.

Price then vs now (added 2026-10-28): House filings give amount ranges, not prices, so
the report and dashboard estimate the price as the close on each trade date, weighted
by the range's midpoint (purchases for net buying, sales for net selling), next to the
latest close. `stt trackers update` downloads prices for traded stocks outside our
universe (`cache/prices/trackers.parquet`); names for those come from the SEC's list.

Conclusion: after disclosure (up to 45 days late), following Congress hasn't beaten
the market, so its weight in the score is **0** (`data/priors/trackers.json`; up to
±20% once its backtest shows t ≥ 2). It is logged every day as its own strategy
(`congress`) so the live record can change that.

Not added:

- **X/Twitter accounts**: reading them needs X's paid API; mirrors such as Nitter copy
  content X keeps behind its login, so they aren't used.
- **Cramer**: no clean data source, and the evidence is against it (Mad Money picks get
  a brief bump that reverses; the Inverse Cramer ETF shut down within about a year).
- **US Senate trades**: the owner accepted the Senate search site's terms on
  2026-10-28, but the site (efdsearch.senate.gov) answers every request from the cloud
  session with an Akamai "Access Denied" page. Getting past that would mean evading the
  site's bot protection, so it isn't done. The House filings cover most of the same
  ground (Pelosi is a House member).
- **Corporate insider buying (SEC Form 4)**: added 2026-10-28 once the owner agreed to
  send their email to the SEC (below).

### Trump tracker (added 2026-10-28)

Request: flag stocks Trump says or hints are a good buy ("a lot of those come true"),
with a section of its own.

- **Source**: news coverage of his posts, interviews and remarks. Three Google News
  queries (`trump_posts`, `trump_says`, `trump_stocks`) feed a "Trump statements"
  section at the top of the digest, with at least 15 stories kept a day.
- **Logging**: the daily analysis records each call in `trump_calls` in
  `data/events/<date>.json`: ticker, stance (`buy`, `positive`, `negative`, `sell`),
  whether it was explicit, the day he said it, a quote and the source stories.
  `stt validate` checks the ticker, the sources and the date (within 14 days).
- **Remark on picks**: a pick he talked up or attacked in the last 30 days carries a ★
  and the quote in the report, and a note in the dashboard. It has no weight in the
  score: if a statement should move a stock, the analysis adds a normal event for it.
- **Ledger**: every stock or ETF call is logged as strategy `trump` at both horizons
  (praise or "buy" as longs, attacks or "sell" as avoids), so the scoreboard measures
  whether following him beats the market. Whole-market calls (SPY, QQQ) aren't logged.
- **Report and dashboard section**: his calls of the last 90 days, the price when he
  said it (the close before) next to the latest close and the move since, the move on
  the day he said it (net of the benchmark), the return since the call was logged
  against the benchmark (in euros for European listings), and whether it came true.

There is no free, reliable history of his past calls to backtest, so the record starts
on 2026-10-29. The day-of move often happens within minutes of a post, before anyone
reading the news can act; the "since logged" column is the fair test.

### Insider tracker: SEC Form 4 (added 2026-10-28)

Request: use the owner's email for the SEC so corporate insider buying can be tracked.
The email is sent only to the SEC, in the User-Agent its fair-access policy asks for.
It is kept out of the repo, which is public: `STT_SEC_CONTACT` or the gitignored
`.sec_contact`, which the daily run writes from its scheduled-task prompt.

- **Data**: open-market purchases (code P) and sales (code S) by officers, directors
  and 10% owners of the 500 US stocks we cover, one row per filing and code
  (`data/trackers/insider_trades.jsonl`, append-only). The backfill used the SEC's
  quarterly "Insider Transactions Data Sets" from 2021 Q4 to 2026 Q3 (43,280 trades);
  later days come from the daily EDGAR index and each Form 4's XML (about 50–150
  filings a day, around 30 seconds at the SEC's rate limit).
- **Signal**: per stock, over Form 4s filed in the 30 days before the morning (trades
  under 60 days old), the sum of log10(dollars bought / 1,000) per purchase. Sales are
  counted but don't lower it: the research finds them uninformative (taxes,
  diversification, 10b5-1 plans).

Did it get more right than wrong? Backtest, top 10 stocks by the signal every horizon
sessions, current S&P 500 members, vs SPY, 2021-11 to 2026-10:

| Strategy | 5 days | 20 days |
|---|---|---|
| Top 10 insider buys | +0.09%, hit 48%, t = 0.7 | +0.33%, hit 49%, t = 0.7 |
| Momentum, same window | +0.13%, t = 0.7 | +2.66%, t = 2.5 |

An event study (each stock and filing day, from the first session after filing) gives
a skewed picture: after all 3,224 purchase events the 20-day mean is +0.74% but the
median +0.05%, hit 50%. Two or more insiders buying within 30 days: +1.60% mean,
+0.40% median (n = 1,484); but three or more: −0.18% (n = 504), and CEO/CFO purchases
−0.45%. Events bunch in sell-offs (2022, March 2023), so their t-statistics overstate.
The inconsistent subgroups look like noise, not an edge to tune for.

Conclusion: in large US caps, following insider purchases hasn't beaten the market
over the last five years (the research finds the effect mainly in small caps), so its
weight in the score is **0** (`insider_weight` in `data/priors/trackers.json`; up to
±20% once the monthly backtest shows t ≥ 2). It is shown in the report and on the
dashboard, noted on picks it touches, and logged as its own strategy (`insiders`,
started 2026-10-28) so the live record can change that.

**Paid vs now (added 2026-10-28).** The report and dashboard show what insiders paid
(dollar-weighted average), the latest close and the move since, so a reader can see
how far the price has run. Does that distance matter? Every 5 sessions from 2021-12
to 2026-09, each stock in the signal (6,185 stock-dates), bucketed by its move from
the insiders' price, then its excess return over SPY in the next 20 sessions:

| Move since insiders bought | n | Mean | Median | Hit | t (per date) |
|---|---|---|---|---|---|
| below −10% | 706 | +1.31% | +0.92% | 53% | 2.7 |
| −10% to −5% | 561 | +0.32% | −0.03% | 50% | 0.6 |
| −5% to 0 | 1,559 | −0.10% | −0.35% | 48% | 0.0 |
| 0 to +5% | 1,971 | −0.09% | −0.45% | 48% | −1.0 |
| +5% to +10% | 867 | −0.03% | −0.07% | 50% | −2.1 |
| above +10% | 521 | +1.56% | +1.00% | 56% | 1.8 |

No "too late to enter" rule: stocks already 10%+ above the insiders' price did no
worse. The two extremes did best, but these are 12 tests on overlapping 20-day windows
(which inflate t), so neither is solid. At 5 sessions every bucket is flat.

### Picks: price then vs now (added 2026-10-28)

Today's pick tables show each pick's last close (the price it was made at). A new "Open
picks" section in the report and on the dashboard lists every earlier pick still inside
its horizon: entry (the open of the first session after the pick, as the ledger scores
it), the latest close, the move since, and the excess return over the benchmark so far
(`tracking.evaluate.mark`, the same rules as a matured pick, including the euro
conversion). Today's picks join it from the next run, once their entry is known.
Under the track record, "Latest matured picks" lists the last 60 scored picks: entry
and exit prices, the result, vs benchmark and the verdict as scored, and the latest
close with the move since entry, to show what happened after the horizon.

### Learning loop (added 2026-10-28)

Request: improve accuracy over time by seeing what works and what doesn't; and keep in
mind that media coverage sometimes shows the opposite of what is really happening.

- **Every call is scored** (`tracking/impacts.py`), not just the picks: each impact in
  the events files (about 19 a day) once its horizon ends, like a pick (entry at the
  open after the analysis, against SPY or the STOXX 600 in euros). A theme scores as
  its members' excess returns signed by their weights. Each outcome records the kind
  of call: category, horizon, target type, certainty, confidence, official source or
  media only, number of outlets, whether it bet against the coverage's framing, and
  whether the market was already moving the called way. Written once to
  `data/ledger/impact_outcomes.jsonl`.
- **Feedback note** (`tracking/feedback.py`, `stt feedback`, run by `daily prepare`):
  hit rate and mean excess by kind of call, each kind against the rest of its section
  on the same days, and calibration by stated confidence. t-statistics are computed
  across days (calls on the same day move together). Only solid findings (20+ days,
  |t| >= 2) become advice; everything else is labelled weak. The daily analysis reads
  it first and acts only on its "What to adjust" list; the weekly review uses it as
  the basis for prompt and theme edits instead of a single week's anecdotes.
- **Coverage vs what is happening**: the analysis prompt now ranks evidence (official
  documents, hard data and money-backed signals such as prices and Polymarket odds
  above reporting and commentary), caps single-outlet or anonymous claims at
  `reported`/`rumor`, and flags calls that bet against the coverage's framing
  (`against_coverage`). It doesn't assume coverage is wrong by default: the feedback
  note measures whether contrarian calls, media-only calls and calls chasing a move
  do better or worse than the rest.

Expected pace: the first 5-day calls mature on 2026-11-03; with about 19 calls a day a
solid finding needs at least 20 days of calls, so the first advice can appear around
early December, and per-category findings take months.
