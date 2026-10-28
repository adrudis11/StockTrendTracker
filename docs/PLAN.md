# StockTrendTracker — Project Plan

> Status: **Phase 0 (planning)** · Last updated: 2026-10-28
>
> This is a research and decision-support tool for personal use. It is **not investment
> advice**, and it never places trades.

---

## 1. Goal

Each trading day, produce a short ranked list of liquid stocks (and sector/country ETFs) that
the current **political and geopolitical situation** makes likely to **outperform over the next
days to weeks**. Every pick comes with:

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
2. **Relative (the main metric):** did it beat the S&P 500 (SPY) and its own sector ETF?

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
```

**Later (optional):** `gamma-api.polymarket.com` (prediction markets),
`api.congress.gov` (legislation), `api.usaspending.gov` (government contracts),
`www.ecb.europa.eu`, `www.federalreserve.gov`, plus any extra news RSS feeds we pick.

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
| Headlines | RSS: Google News topic searches, BBC World, more to choose | Readable headlines and summaries for Claude. |
| US executive actions | **Federal Register API** | Executive orders, tariffs, sanctions rules, the primary source rather than coverage. |
| Company data | **SEC EDGAR** | Ticker ↔ company ↔ industry (SIC); 8-K filings later. |
| Prices | **yfinance** (Yahoo, unofficial), **Stooq** as fallback | Daily OHLCV for stocks, ETFs, VIX, oil, dollar. |
| Macro | **FRED** (free API key) | Yields, yield curve, dollar index, oil, for market-regime context. |
| Later | Polymarket / Kalshi, Congress.gov, USAspending, ECB/Fed releases, OFAC updates | Event probabilities, legislation, defense contracts, central banks, sanctions. |

### 3.2 Universe and exposure map

- **Universe v1:** S&P 500 members + ~30 sector, industry and country ETFs (XLE, ITA, SMH,
  EWZ, …). Liquidity filter: market cap > $2B and average daily traded value > $20M.
- **Exposure map** (`exposures.yaml`): a human-readable file mapping **geopolitical themes →
  likely winners and losers**, e.g. *Middle East escalation → oil producers, defense, tankers ↑;
  airlines, cruise lines ↓*. It starts with ~20 themes (defense spending, tariffs on China,
  chip export controls, energy supply shocks, rare earths, shipping disruption, agriculture,
  elections, sanctions, reshoring subsidies, …). Claude proposes changes and you can review
  them in git.

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
- **Market regime:** VIX, yield curve, dollar and oil trend. Risk-off regimes favour defensives.
- **Event-study prior:** historically, how did a sector perform in the N days after similar
  GDELT event spikes? This part **can be backtested** on 2015+ data.

### 3.5 Scoring (v1: transparent formula)

```
score = w1 · geo_signal          (direction × magnitude × confidence × novelty, from Claude)
      + w2 · event_study_prior    (historical sector reaction to similar events)
      + w3 · trend_confirmation   (price/volume agreeing with the thesis)
      − w4 · priced_in_penalty
      − w5 · risk_penalty         (earnings in window, volatility, liquidity)
```

The weights are set by hand at first. Once enough picks have matured, they are fitted
(logistic regression) and the scores are **calibrated**, so that "70%" really means about
7 out of 10.

Output: top **10 picks per horizon** (5d and 20d), plus a short "avoid" list for strongly
negative signals.

### 3.6 Tracking and evaluation

- **Append-only ledger** (`data/ledger/picks.jsonl`) committed to git. Git history makes it
  tamper-evident, so the record can't be quietly rewritten afterwards.
- Entry price = **next day's open** after the pick (no hindsight).
- After each horizon ends, picks are scored automatically against SPY and the sector ETF.
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
- `httpx`, `feedparser` (ingestion) · `yfinance` (prices) · `pandas` (data) · `pydantic`
  (schemas and validation) · `duckdb` (local queries) · `typer` (CLI) · `jinja2` (reports)
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

Runs on weekdays before the US market opens (time to be confirmed):

1. Install dependencies (`uv sync`).
2. `stt ingest`: news, Federal Register and prices for the last 24 hours.
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
| **0 · Planning** | This document, decisions on open questions, network allowlist. | You've answered §8 and the allowlist is in place. |
| **1 · Foundation** | Project skeleton, universe (S&P 500 + ETFs), price download, GDELT + RSS + Federal Register ingestion, storage layout, tests. | `stt ingest` gathers a full day of data reliably. |
| **2 · Event analysis** | Event schema, digest builder, `daily_analysis.md` prompt, exposure map v1 (~20 themes), validation. | One manual run turns a day's news into sensible, validated event → ticker mappings. |
| **3 · Quant + scoring** | Price features, regime, priced-in check, risk filters, scoring v1, report v1. | A daily report with top-10 picks per horizon and full rationale. |
| **4 · Tracking + backtest** | Ledger, automatic scoring, baselines, scoreboard; event-study backtest of the quant layer on GDELT history. | Scoreboard works; we know whether the quant layer on its own beats baselines historically. |
| **5 · Automation** | Scheduled daily session, dashboard, weekly review, credits-per-run measured. | Runs unattended for 2 weeks without manual fixes. |
| **6 · Paper-tracking period** | At least 8–12 weeks of live tracking, then calibrate weights; then consider expanding (EU stocks, prediction markets, alerts). | ≥100 matured picks with an honest verdict: does it beat SPY and the baselines? |

**Overall success criterion:** after Phase 6, the 20-day picks beat both SPY and the
sector-matched baselines on average excess return, with sensible calibration. If they don't,
we either iterate or keep it as a geopolitical briefing tool rather than a stock picker. That
is still useful, but we'd be clear about what it is.

---

## 8. Open questions (defaults in **bold**, used unless you say otherwise)

1. **Markets:** **US large caps + sector/country ETFs to start**, or include European stocks
   (Euro STOXX / IBEX) from day one?
2. **Network allowlist:** can you add the domains in §2? *This blocks Phase 1.*
3. **Schedule:** what time zone, and what time should the daily report be ready?
   **Default: before the US open.**
4. **Paid data:** **none**. All free sources.
5. **GitHub Actions for automated tests:** **not used** (tests run in sessions). OK to enable?
6. **Risk profile:** **liquid stocks and ETFs only; no penny stocks, options or leverage.**
7. **Where you read results:** **daily Markdown report in the repo + private dashboard page.**
   Do you want email or Telegram alerts later?

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| News is priced in within minutes | Focus on second-order and slow-moving policy effects; priced-in penalty; measure against baselines. |
| Look-ahead bias when evaluating the AI | Forward-only evaluation of the AI layer (§4). |
| AI invents tickers or sources | Universe check, source URLs must come from the ingested set, strict schema validation. |
| Free data sources break or rate-limit (yfinance is unofficial) | Stooq fallback, caching, health checks that fail the run loudly. |
| Overfitting to a small sample | No weight tuning before ~100 matured picks; baselines; out-of-sample checks. |
| Session credits per run grow over time | Capped digest size; measure credits per run in Phase 5; Python does the heavy lifting. |
| Ephemeral containers lose state | Everything important is committed to git at the end of each run. |
| Source terms of use | Personal, non-commercial use; respect rate limits; store headlines and links, not full articles. |
