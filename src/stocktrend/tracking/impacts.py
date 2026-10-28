"""Score every call in the daily analysis, not just the picks.

Each impact in ``data/events/<date>.json`` is a prediction: "theme X intensifies, so
its winners beat the market and its losers lag over N days", "this stock
outperforms", "this sector underperforms". Once its horizon has passed it is scored
like a pick: entry at the first open after the analysis, exit at the close of the
horizon-th session, against SPY or the STOXX 600 (in euros).

* ``ticker:``: the stock's excess return.
* ``theme:``: the members' excess returns, each signed by its weight in the theme,
  averaged by weight. A theme removed from ``config/exposures.yaml`` can't be scored.
* ``sector:``: the region's sector ETF, or its stocks equally weighted without one.

The result is signed by the call's direction: positive = the call was right. Each
outcome also records what kind of call it was (category, certainty, confidence,
official sources or media only, how many outlets, whether it bet against the
coverage's framing, and whether the market was already moving the called way), so
``tracking.feedback`` can show which kinds of calls work.

Outcomes go to ``data/ledger/impact_outcomes.jsonl``, one line per impact, written once.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from stocktrend.config import DATA_DIR
from stocktrend.events.digest import Digest
from stocktrend.events.schema import Event, EventsFile, Impact
from stocktrend.markets import exchange_for, split_suffix
from stocktrend.tracking.evaluate import PriceBook, entry_session, exit_session, window
from stocktrend.tracking.ledger import LedgerEntry
from stocktrend.universe.exposures import Exposures

OUTCOMES = DATA_DIR / "ledger" / "impact_outcomes.jsonl"
EVENTS_DIR = DATA_DIR / "events"
DIGESTS_DIR = DATA_DIR / "digests"


class ImpactOutcome(BaseModel):
    impact_id: str  # <event id>:<impact index>
    date: str
    event_id: str
    category: str
    certainty: str
    novelty: float
    target: str
    kind: str
    direction: int
    horizon: int
    magnitude: float
    confidence: float
    official: bool  # at least one source is an official document
    outlets: int  # the most publishers covering one of its sources
    against_coverage: bool
    prior: float | None  # excess over the previous ``horizon`` sessions, in the call's direction
    members: int  # instruments scored
    entry_date: str
    exit_date: str
    excess: float  # in the call's direction: positive = the call was right
    correct: bool
    evaluated_at: datetime


def read(path: Path = OUTCOMES) -> list[ImpactOutcome]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [ImpactOutcome.model_validate_json(line) for line in f if line.strip()]


def region_of(ticker: str) -> str:
    return "Europe" if split_suffix(ticker)[1] else "US"


def members(
    imp: Impact,
    exposures: Exposures,
    meta: pd.DataFrame,
    sector_etfs: dict[tuple[str, str], str],
) -> dict[str, float] | None:
    """ticker -> signed weight of what the impact moves; None if it can't be scored."""
    if imp.kind == "ticker":
        return {imp.ref: 1.0}
    if imp.kind == "theme":
        theme = exposures.themes.get(imp.ref)
        return dict(theme.members) if theme else None
    sector, _, region = imp.ref.partition("@")
    etf = sector_etfs.get((region, sector))
    if etf:
        return {etf: 1.0}
    stocks = meta.index[(meta["sector"] == sector) & (meta["region"] == region)]
    return {str(t): 1.0 for t in stocks if meta.at[t, "kind"] == "stock"} or None


def _prior(book: PriceBook, ticker: str, bench: str, start: pd.Timestamp, n: int) -> float | None:
    """Excess return over the ``n`` sessions before ``start`` (close to close)."""
    moves = []
    for t in (ticker, bench):
        g = book.by_ticker.get(t)
        if g is None:
            return None
        closes = g.loc[g.index < start, "adj_close"].dropna()
        if len(closes) <= n:
            return None
        moves.append(float(closes.iloc[-1] / closes.iloc[-1 - n] - 1))
    return moves[0] - moves[1]


def score(
    day: str,
    ev: Event,
    n: int,
    weights: dict[str, float],
    logged_at: datetime,
    book: PriceBook,
    benchmarks: dict[str, str],
    sources: dict[str, tuple[bool, int]],
    now: datetime,
) -> ImpactOutcome | None:
    """Score one impact, or None while its horizon hasn't ended for every member."""
    imp = ev.impacts[n]
    spans = {}
    for t in weights:
        mic = exchange_for(t).mic
        start = entry_session(mic, logged_at)
        spans[t] = (start, exit_session(mic, start, imp.horizon_days))
    last_end = max(end for _, end in spans.values())
    for bench in {benchmarks[region_of(t)] for t in weights}:
        last = book.last_date(bench)
        if last is None or last < last_end:
            return None  # not matured yet
    total = scored = 0.0
    prior_total = prior_weight = 0.0
    count = 0
    for t, w in weights.items():
        start, end = spans[t]
        region = region_of(t)
        win = window(t, region, 1, book, benchmarks, start, end)
        if win is None:
            continue  # no prices for this member (delisted, not downloaded)
        total += w * win.excess
        scored += abs(w)
        count += 1
        p = _prior(book, t, benchmarks[region], start, imp.horizon_days)
        if p is not None:
            prior_total += w * p
            prior_weight += abs(w)
    if not count:
        return None
    excess = imp.direction * total / scored
    cited = [sources[s] for s in ev.sources if s in sources]
    first = min(spans.values())
    return ImpactOutcome(
        impact_id=f"{ev.id}:{n}",
        date=day,
        event_id=ev.id,
        category=ev.category,
        certainty=ev.certainty,
        novelty=ev.novelty,
        target=imp.target,
        kind=imp.kind,
        direction=imp.direction,
        horizon=imp.horizon_days,
        magnitude=imp.magnitude,
        confidence=imp.confidence,
        official=any(off for off, _ in cited),
        outlets=max((k for _, k in cited), default=0),
        against_coverage=imp.against_coverage,
        prior=round(imp.direction * prior_total / prior_weight, 6) if prior_weight else None,
        members=count,
        entry_date=first[0].date().isoformat(),
        exit_date=last_end.date().isoformat(),
        excess=round(excess, 6),
        correct=excess > 0,
        evaluated_at=now,
    )


def stories(day: str, digests_dir: Path = DIGESTS_DIR) -> dict[str, tuple[bool, int]]:
    """Story id -> (official source?, number of publishers) from that day's digest."""
    path = digests_dir / f"{day}.json"
    if not path.exists():
        return {}
    digest = Digest.model_validate_json(path.read_text(encoding="utf-8"))
    return {s.id: (s.official, s.n_publishers) for s in digest.stories}


def logged_times(entries: list[LedgerEntry]) -> dict[str, datetime]:
    """When each day's analysis was acted on: the first ledger entry of that day."""
    times: dict[str, datetime] = {}
    for e in entries:
        if e.date not in times or e.logged_at < times[e.date]:
            times[e.date] = e.logged_at
    return times


def evaluate(
    prices: pd.DataFrame,
    benchmarks: dict[str, str],
    exposures: Exposures,
    meta: pd.DataFrame,
    sector_etfs: dict[tuple[str, str], str],
    entries: list[LedgerEntry],
    now: datetime | None = None,
    events_dir: Path = EVENTS_DIR,
    digests_dir: Path = DIGESTS_DIR,
    path: Path = OUTCOMES,
) -> tuple[int, int]:
    """Append outcomes for impacts whose horizon has ended. Returns (new, still open)."""
    now = now or datetime.now(UTC)
    done = {o.impact_id for o in read(path)}
    times = logged_times(entries)
    book = PriceBook(prices)
    new: list[ImpactOutcome] = []
    still_open = 0
    for file in sorted(events_dir.glob("*.json")):
        ef = EventsFile.model_validate_json(file.read_text(encoding="utf-8"))
        day = ef.date
        if day not in times:
            continue  # never acted on (no picks logged that day)
        day_stories = stories(day, digests_dir)
        for ev in ef.events:
            for n, imp in enumerate(ev.impacts):
                if f"{ev.id}:{n}" in done:
                    continue
                weights = members(imp, exposures, meta, sector_etfs)
                if not weights:
                    continue
                out = score(day, ev, n, weights, times[day], book, benchmarks, day_stories, now)
                if out is None:
                    still_open += 1
                else:
                    new.append(out)
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for o in new:
                f.write(o.model_dump_json() + "\n")
    return len(new), still_open
