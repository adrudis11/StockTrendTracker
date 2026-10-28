"""The Trump tracker: stocks Donald Trump says or hints are a buy, or attacks.

Calls come from the daily analysis (``trump_calls`` in ``data/events/<date>.json``),
which reads news coverage of his posts, interviews and speeches. For each call we
measure two things:

* **reaction**: the move on the day he said it (benchmark-adjusted). Much of the
  effect often happens within minutes of a post, before anyone following along
  can act.
* **since logged**: from the first open after we logged the call to the latest
  close, against the stock's benchmark. This is what "did it come true?" means for
  someone acting on it. Calls on the whole market (SPY, QQQ) are measured in
  absolute terms, since a market call can't beat the market by definition.

Every stock call is also logged in the ledger as strategy ``trump`` (longs for buy
or positive calls, avoids for negative or sell), so it gets the same scoring as the
picks and baselines.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from stocktrend.config import DATA_DIR
from stocktrend.events.schema import BULLISH, MARKET_TICKERS, EventsFile
from stocktrend.markets import exchange_for, split_suffix
from stocktrend.tracking.evaluate import FX_PAIRS

STANCE_WORDS = {
    "buy": "said buy",
    "positive": "talked up",
    "negative": "attacked",
    "sell": "said sell",
}


def load_calls(events_dir: Path = DATA_DIR / "events") -> pd.DataFrame:
    """All calls from every events file, one row per (day logged, ticker)."""
    rows = []
    for path in sorted(events_dir.glob("*.json")):
        ef = EventsFile.model_validate_json(path.read_text(encoding="utf-8"))
        for c in ef.trump_calls:
            rows.append({"logged": ef.date, **c.model_dump()})
    cols = ["logged", "ticker", "company", "stance", "explicit", "said_on", "quote", "sources"]
    return pd.DataFrame(rows, columns=cols)


def _bars(prices: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {
        str(t): g.set_index("date").sort_index() for t, g in prices.groupby("ticker", sort=False)
    }


def _since(g: pd.DataFrame | None, start: pd.Timestamp) -> tuple[float, pd.Timestamp] | None:
    """From the open of the first bar on/after ``start`` to the latest close, and that bar."""
    if g is None:
        return None
    after = g.loc[g.index >= start]
    if after.empty:
        return None
    first = after.iloc[0]
    adj = first["adj_close"] / first["close"] if first["close"] else 1.0
    entry = float(first["open"]) * float(adj)
    last = float(g["adj_close"].iloc[-1])
    return (last / entry - 1, pd.Timestamp(after.index[0])) if entry else None


def _in_eur(ret: float, ticker: str, start: pd.Timestamp, bars: dict[str, pd.DataFrame]) -> float:
    """A UK or Swiss return in euros, like the ledger scores it against the STOXX 600."""
    fx = bars.get(FX_PAIRS.get(exchange_for(ticker).currency, ""))
    if fx is None:
        return ret
    before = fx.loc[fx.index <= start, "close"]
    if before.empty:
        return ret
    return (1 + ret) * float(before.iloc[-1]) / float(fx["close"].iloc[-1]) - 1


def _day_move(g: pd.DataFrame | None, day: pd.Timestamp) -> float | None:
    """Close-to-close move on ``day`` (or the next session if markets were shut)."""
    if g is None:
        return None
    after = g.loc[g.index >= day]
    before = g.loc[g.index < day]
    if after.empty or before.empty:
        return None
    return float(after["adj_close"].iloc[0] / before["adj_close"].iloc[-1] - 1)


def performance(
    calls: pd.DataFrame, prices: pd.DataFrame, benchmarks: dict[str, str]
) -> pd.DataFrame:
    """Calls with their reaction and their return since logged, vs the benchmark."""
    if calls.empty:
        empty = dict.fromkeys(("reaction", "since", "bench_since", "excess", "came_true"))
        return calls.assign(**empty)
    bars = _bars(prices)
    out = []
    for c in calls.to_dict("records"):
        t = str(c["ticker"])
        bench = benchmarks["Europe" if split_suffix(t)[1] else "US"]
        market = t in MARKET_TICKERS
        said, logged = pd.Timestamp(c["said_on"]), pd.Timestamp(c["logged"])
        move, bmove = _day_move(bars.get(t), said), _day_move(bars.get(bench), said)
        got, bgot = _since(bars.get(t), logged), _since(bars.get(bench), logged)
        since = bsince = excess = None
        if got is not None:
            since = _in_eur(got[0], t, got[1], bars) if split_suffix(t)[1] else got[0]
        if bgot is not None:
            bsince = bgot[0]
        if since is not None:
            excess = since if market else (since - bsince if bsince is not None else None)
        sign = 1 if c["stance"] in BULLISH else -1
        if move is not None and not market and bmove is not None:
            move -= bmove
        out.append(
            {
                **c,
                "reaction": move,
                "since": since,
                "bench_since": None if market else bsince,
                "excess": excess,
                # "came true" so far: a bullish call ahead of the benchmark, or a bearish one behind
                "came_true": None if excess is None else bool(sign * excess > 0),
            }
        )
    return pd.DataFrame(out)


def recent_notes(calls: pd.DataFrame, as_of: date, days: int = 30) -> dict[str, str]:
    """ticker -> remark for picks, e.g. 'Trump talked up on 2026-10-27: "great company"'.

    Only calls logged by ``as_of`` count, so re-scoring an old day can't see later ones.
    """
    if calls.empty:
        return {}
    said = pd.to_datetime(calls["said_on"]).dt.date
    recent = calls[(said >= as_of - timedelta(days=days)) & (calls["logged"] <= as_of.isoformat())]
    notes: dict[str, str] = {}
    for c in recent.sort_values("said_on").to_dict("records"):
        word = STANCE_WORDS[str(c["stance"])]
        quote = str(c["quote"])
        quote = quote if len(quote) <= 90 else quote[:87] + "..."
        notes[str(c["ticker"])] = f"Trump {word} on {c['said_on']}: “{quote}”"
    return notes


def summary(perf: pd.DataFrame) -> dict[str, Any]:
    scored = perf[perf["came_true"].notna()] if not perf.empty else perf
    return {
        "calls": len(perf),
        "scored": len(scored),
        "came_true": int(scored["came_true"].sum()) if len(scored) else 0,
        "explicit": int(perf["explicit"].sum()) if len(perf) else 0,
    }
