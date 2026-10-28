"""`stt evaluate`: score ledger picks whose horizon has ended.

* Entry: the open of the first session on the instrument's exchange that starts
  after the pick was logged (no hindsight).
* Exit: the close of the ``horizon``-th session, counting the entry session as 1.
* Benchmark: S&P 500 (SPY) for US listings, STOXX Europe 600 (EXSA.DE, EUR) for
  European ones, over the same dates. European stocks quoted in GBP or CHF are
  converted to EUR first so currency moves don't count as skill.
* A pick is "correct" if a long beat the benchmark, or an avoid lagged it.

Results go to ``data/ledger/outcomes.jsonl``, one line per pick, written once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from stocktrend.config import DATA_DIR
from stocktrend.markets import calendar, exchange_for
from stocktrend.tracking.ledger import LedgerEntry, read

OUTCOMES = DATA_DIR / "ledger" / "outcomes.jsonl"
FX_PAIRS = {"GBp": "EURGBP=X", "GBP": "EURGBP=X", "CHF": "EURCHF=X"}  # quote per EUR


class Outcome(BaseModel):
    pick_id: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    ret: float  # in the benchmark's currency
    bench: str
    bench_ret: float
    excess: float  # from the pick's point of view: positive = the call was right
    correct: bool
    sector_etf: str | None = None
    sector_ret: float | None = None
    sector_excess: float | None = None
    evaluated_at: datetime


def read_outcomes(path: Path = OUTCOMES) -> list[Outcome]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [Outcome.model_validate_json(line) for line in f if line.strip()]


def entry_session(mic: str, logged_at: datetime) -> pd.Timestamp:
    """First session whose open is at or after ``logged_at``."""
    cal = calendar(mic)
    start = pd.Timestamp(logged_at.date())
    for s in cal.sessions_in_range(start, start + pd.Timedelta(days=14)):
        if cal.session_open(s) >= pd.Timestamp(logged_at).tz_convert("UTC"):
            return pd.Timestamp(s)
    raise ValueError(f"no {mic} session within 2 weeks of {logged_at}")


def exit_session(mic: str, entry: pd.Timestamp, horizon: int) -> pd.Timestamp:
    return pd.Timestamp(calendar(mic).session_offset(entry, horizon - 1))


class PriceBook:
    """Fast lookups of open/close bars by ticker and date."""

    def __init__(self, prices: pd.DataFrame) -> None:
        self.by_ticker = {
            t: g.set_index("date").sort_index() for t, g in prices.groupby("ticker", sort=False)
        }

    def window_return(self, ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> tuple[
        float, float, pd.Timestamp, pd.Timestamp
    ] | None:  # fmt: skip
        """Adjusted open-at-start to close-at-end; uses the nearest bars inside the window."""
        g = self.by_ticker.get(ticker)
        if g is None:
            return None
        inside = g.loc[(g.index >= start) & (g.index <= end)]
        if inside.empty or inside.index[-1] < end - pd.Timedelta(days=4):
            return None
        first, last = inside.iloc[0], inside.iloc[-1]
        adj = first["adj_close"] / first["close"] if first["close"] else 1.0
        entry = float(first["open"]) * float(adj)
        exit_ = float(last["adj_close"])
        if not entry or pd.isna(entry) or pd.isna(exit_):
            return None
        return entry, exit_, pd.Timestamp(inside.index[0]), pd.Timestamp(inside.index[-1])

    def close_on_or_before(self, ticker: str, day: pd.Timestamp) -> float | None:
        g = self.by_ticker.get(ticker)
        if g is None:
            return None
        before = g.loc[g.index <= day]
        return float(before["close"].iloc[-1]) if not before.empty else None

    def last_date(self, ticker: str) -> pd.Timestamp | None:
        g = self.by_ticker.get(ticker)
        return pd.Timestamp(g.index[-1]) if g is not None and len(g) else None


@dataclass
class _Window:
    entry_px: float  # adjusted open of the entry session
    exit_px: float  # adjusted close of the last session in the window
    d0: pd.Timestamp
    d1: pd.Timestamp
    ret: float  # in the benchmark's currency
    bench: str
    bench_ret: float
    excess: float  # from the pick's point of view: positive = the call is right


def _window(
    e: LedgerEntry, book: PriceBook, benchmarks: dict[str, str], start: pd.Timestamp,
    end: pd.Timestamp,
) -> _Window | None:  # fmt: skip
    """The pick's return and excess over the benchmark from ``start``'s open to ``end``."""
    window = book.window_return(e.ticker, start, end)
    bench = benchmarks[e.region]
    bench_window = book.window_return(bench, start, end)
    if window is None or bench_window is None:
        return None
    entry_px, exit_px, d0, d1 = window
    ret = exit_px / entry_px - 1
    pair = FX_PAIRS.get(exchange_for(e.ticker).currency) if e.region == "Europe" else None
    if pair:  # e.g. GBP per EUR: a weaker pound shrinks the EUR return
        fx0, fx1 = book.close_on_or_before(pair, d0), book.close_on_or_before(pair, d1)
        if fx0 and fx1:
            ret = (1 + ret) * fx0 / fx1 - 1
    bench_ret = bench_window[1] / bench_window[0] - 1
    sign = 1 if e.side == "long" else -1
    return _Window(entry_px, exit_px, d0, d1, ret, bench, bench_ret, sign * (ret - bench_ret))


def evaluate_entry(
    e: LedgerEntry,
    book: PriceBook,
    benchmarks: dict[str, str],
    sector_etfs: dict[tuple[str, str], str],
    now: datetime,
) -> Outcome | None:
    """Score one pick, or None if its horizon hasn't ended (or data is missing)."""
    mic = exchange_for(e.ticker).mic
    start = entry_session(mic, e.logged_at)
    end = exit_session(mic, start, e.horizon)
    last = book.last_date(e.ticker)
    if last is None or last < end:
        return None
    w = _window(e, book, benchmarks, start, end)
    if w is None:
        return None
    sign = 1 if e.side == "long" else -1
    sector_etf = sector_etfs.get((e.region, e.sector)) if e.kind == "stock" else None
    sector_ret = sector_excess = None
    if sector_etf:
        sw = book.window_return(sector_etf, start, end)
        if sw is not None:
            sector_ret = sw[1] / sw[0] - 1
            sector_excess = sign * (w.ret - sector_ret)
    return Outcome(
        pick_id=e.pick_id,
        entry_date=w.d0.date().isoformat(),
        exit_date=w.d1.date().isoformat(),
        entry_price=round(w.entry_px, 6),
        exit_price=round(w.exit_px, 6),
        ret=round(w.ret, 6),
        bench=w.bench,
        bench_ret=round(w.bench_ret, 6),
        excess=round(w.excess, 6),
        correct=w.excess > 0,
        sector_etf=sector_etf,
        sector_ret=None if sector_ret is None else round(sector_ret, 6),
        sector_excess=None if sector_excess is None else round(sector_excess, 6),
        evaluated_at=now,
    )


@dataclass
class Mark:
    """An open pick at the latest close, by the same rules as a matured one."""

    entry_date: date
    exit_date: date  # the last session of its horizon
    entry_open: float | None  # unadjusted, None until the entry session has traded
    last_close: float | None  # unadjusted
    last_date: date | None
    excess: float | None  # from the pick's point of view, as in ``Outcome``


def mark(e: LedgerEntry, book: PriceBook, benchmarks: dict[str, str]) -> Mark:
    mic = exchange_for(e.ticker).mic
    start = entry_session(mic, e.logged_at)
    end = exit_session(mic, start, e.horizon)
    last = book.last_date(e.ticker)
    m = Mark(start.date(), end.date(), None, None, None, None)
    if last is None:
        return m
    m.last_close = book.close_on_or_before(e.ticker, last)
    m.last_date = last.date()
    if last < start:
        return m  # not entered yet
    bars = book.by_ticker[e.ticker]
    m.entry_open = float(bars.loc[bars.index >= start, "open"].iloc[0])
    w = _window(e, book, benchmarks, start, min(last, end))
    m.excess = None if w is None else w.excess
    return m


def evaluate(
    prices: pd.DataFrame,
    benchmarks: dict[str, str],
    sector_etfs: dict[tuple[str, str], str],
    now: datetime | None = None,
    ledger_path: Path | None = None,
    outcomes_path: Path = OUTCOMES,
) -> tuple[int, int]:
    """Append outcomes for newly matured picks. Returns (newly scored, still open)."""
    now = now or datetime.now(UTC)
    entries = read(ledger_path) if ledger_path else read()
    done = {o.pick_id for o in read_outcomes(outcomes_path)}
    book = PriceBook(prices)
    new: list[Outcome] = []
    still_open = 0
    for e in entries:
        if e.pick_id in done:
            continue
        outcome = evaluate_entry(e, book, benchmarks, sector_etfs, now)
        if outcome is None:
            still_open += 1
        else:
            new.append(outcome)
    if new:
        outcomes_path.parent.mkdir(parents=True, exist_ok=True)
        with outcomes_path.open("a", encoding="utf-8") as f:
            for o in new:
                f.write(o.model_dump_json() + "\n")
    return len(new), still_open


def first_matures(entries: list[LedgerEntry]) -> date | None:
    """Earliest exit date among open picks: when the scoreboard gets its first data."""
    dates = []
    for e in entries:
        mic = exchange_for(e.ticker).mic
        dates.append(exit_session(mic, entry_session(mic, e.logged_at), e.horizon).date())
    return min(dates) if dates else None
