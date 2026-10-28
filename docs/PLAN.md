# StockTrendTracker — Project Plan

> Status: **Phase 4 (tracking and backtests) done** · next: Phase 5 (automation) · Last updated: 2026-10-28
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
| **5 · Automation** | Scheduled daily session, dashboard, weekly review, credits-per-run measured. | Runs unattended for 2 weeks without manual fixes. |
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

- **Ledger started 2026-10-28** with 77 entries: 14 geopolitical picks plus 63 baseline
  picks. Entry is the first open after logging (today's were logged at 09:38 UTC, after
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
