from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stocktrend.backtest import trackers
from stocktrend.events.schema import TrumpCall
from stocktrend.ingest import polymarket
from stocktrend.trackers import congress, trump
from stocktrend.tracking import ledger

PTR_TEXT = """ID Owner Asset Transaction Type Date Notification Date Amount Cap. Gains > $200?
Home Depot, Inc. (HD) [ST] P 09/17/2026 10/05/2026 $1,001 - $15,000
F      S     : New
D          : Automatic reinvestment of dividends earned.
SP NVIDIA Corporation - Common
Stock (NVDA) [OP] P 09/14/2026 09/14/2026 $1,000,001 - $5,000,000
F      S     : New
D          : Purchased 50 call options.
JT Apple Inc. (AAPL) [ST] S (partial) 09/02/2026 09/03/2026 Over $50,000,000
Bitcoin (BTC) [CT] P 09/18/2026 09/18/2026 $50,001 -
$100,000
"""


def test_parse_ptr_text() -> None:
    trades = {t["ticker"]: t for t in congress.parse_ptr_text(PTR_TEXT)}
    assert set(trades) == {"HD", "NVDA", "AAPL"}  # crypto is not a stock
    assert trades["HD"]["reinvestment"] and trades["HD"]["owner"] is None
    nv = trades["NVDA"]
    assert nv["owner"] == "SP" and nv["asset_type"] == "OP" and nv["type"] == "purchase"
    assert (nv["amount_low"], nv["amount_high"]) == (1_000_001, 5_000_000)
    assert nv["asset"].startswith("NVIDIA Corporation")
    ap = trades["AAPL"]
    assert ap["type"] == "partial_sale" and ap["amount_high"] is None and ap["owner"] == "JT"


def test_amounts_and_weights() -> None:
    assert congress.amount_range("$15,001 - $50,000") == (15_001, 50_000)
    assert congress.amount_range("Over $50,000,000") == (50_000_000, None)
    buy = {"type": "purchase", "amount_low": 1_000_001, "amount_high": 5_000_000}
    sell = {**buy, "type": "sale"}
    assert congress.trade_weight(buy) == pytest.approx(np.log10(3_000_000.5 / 1000))
    assert congress.trade_weight(sell) == pytest.approx(-0.5 * congress.trade_weight(buy))


def _trades(rows: list[dict[str, object]]) -> pd.DataFrame:
    base = {"chamber": "house", "state_district": "CA11", "doc_id": "1", "owner": None,
            "asset": "x", "asset_type": "ST", "amount_low": 15_001, "amount_high": 50_000,
            "reinvestment": False}  # fmt: skip
    return pd.DataFrame([{**base, **r} for r in rows])


def test_signal_uses_only_public_recent_trades() -> None:
    d = date(2026, 10, 28)
    df = _trades([
        {"member": "A", "ticker": "NVDA", "type": "purchase", "filed": d - timedelta(days=3), "tx_date": d - timedelta(days=20)},
        {"member": "B", "ticker": "NVDA", "type": "purchase", "filed": d - timedelta(days=5), "tx_date": d - timedelta(days=30)},
        {"member": "C", "ticker": "XOM", "type": "sale", "filed": d - timedelta(days=2), "tx_date": d - timedelta(days=10)},
        {"member": "D", "ticker": "AAPL", "type": "purchase", "filed": d, "tx_date": d},  # not public yet
        {"member": "E", "ticker": "MSFT", "type": "purchase", "filed": d - timedelta(days=2), "tx_date": d - timedelta(days=200)},  # stale
        {"member": "F", "ticker": "PG", "type": "purchase", "filed": d - timedelta(days=2), "tx_date": d - timedelta(days=5), "reinvestment": True},
        {"member": "G", "ticker": "BRK.B", "type": "purchase", "filed": d - timedelta(days=2), "tx_date": d - timedelta(days=5)},
    ])  # fmt: skip
    sig = congress.signal(df, d)
    assert sig.index[0] == "NVDA"
    rows = sig.to_dict("index")
    assert rows["NVDA"]["buyers"] == 2 and rows["XOM"]["score"] < 0
    assert not {"AAPL", "MSFT", "PG"} & set(sig.index)
    assert "BRK-B" in sig.index  # mapped to Yahoo format
    assert set(congress.signal(df, d, known={"NVDA"}).index) == {"NVDA"}


def test_tracker_backtest_and_weight() -> None:
    n = 200
    idx = pd.bdate_range("2025-01-01", periods=n)
    closes = pd.DataFrame(
        {"SPY": np.linspace(100, 110, n), "WIN": 100 * np.exp(np.cumsum(np.full(n, 0.003))),
         "LOSE": 100 * np.exp(np.cumsum(np.full(n, -0.002)))}, index=idx,
    )  # fmt: skip
    rows = []
    for t in idx[::5]:  # members keep buying WIN and selling LOSE
        rows += [
            {"member": "A", "ticker": "WIN", "type": "purchase", "filed": t.date(), "tx_date": t.date()},
            {"member": "B", "ticker": "LOSE", "type": "sale", "filed": t.date(), "tx_date": t.date()},
        ]  # fmt: skip
    result = trackers.run(closes, _trades(rows), {"WIN", "LOSE"}, 20, date(2025, 2, 1))
    assert result["buys"]["mean_excess"] > 0 and result["buys"]["hit_rate"] == 1.0
    assert result["sells"]["mean_excess"] > 0  # sold stock lagged
    assert (
        trackers.weight_from_evidence({"buys": {"t_stat": 3.0, "mean_excess": 0.01, "periods": 20}})
        == trackers.MAX_WEIGHT
    )
    assert (
        trackers.weight_from_evidence({"buys": {"t_stat": 0.5, "mean_excess": 0.01, "periods": 20}})
        == 0.0
    )
    assert (
        trackers.weight_from_evidence(
            {"buys": {"t_stat": 2.5, "mean_excess": -0.01, "periods": 20}}
        )
        == 0.0
    )


def test_polymarket_open_markets() -> None:
    event = {"title": "Fed decision", "markets": [
        {"question": "Hold?", "outcomes": '["Yes", "No"]', "outcomePrices": '["0.95", "0.05"]',
         "volume24hr": 500, "oneDayPriceChange": 0.02, "active": True, "closed": False},
        {"question": "Cut?", "outcomes": '["Yes", "No"]', "outcomePrices": '["0.999", "0.001"]',
         "volume24hr": 900, "active": True, "closed": False},  # effectively decided
        {"question": "Old", "outcomePrices": '["0.5", "0.5"]', "closed": True},
    ]}  # fmt: skip
    (m,) = polymarket.open_markets(event, 3)
    assert m["question"] == "Hold?" and m["probability"] == 0.95 and m["outcome"] == "Yes"


# --- Trump tracker -----------------------------------------------------------


def _call(ticker: str, stance: str, said_on: str, logged: str = "2026-10-20") -> dict[str, object]:
    return {"logged": logged, "ticker": ticker, "company": ticker, "stance": stance,
            "explicit": stance in ("buy", "sell"), "said_on": said_on,
            "quote": f"{ticker} is something", "sources": ["S001"]}  # fmt: skip


def _price_path(ticker: str, closes: list[float], start: str = "2026-10-15") -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({"date": dates, "ticker": ticker, "open": closes, "close": closes,
                         "adj_close": closes})  # fmt: skip


def test_trump_performance_against_the_benchmark() -> None:
    # Sessions 15, 16, 19, 20, 21, 22, 23 Oct: said on the 16th, logged on the 20th.
    prices = pd.concat([
        _price_path("SPY", [100, 100, 100, 100, 100, 101, 102]),
        _price_path("AAA", [50, 55, 55, 50, 50, 52, 55]),   # talked up: +10% since the 20th open
        _price_path("BBB", [20, 20, 20, 20, 20, 21, 22]),   # attacked, but up +10%
        _price_path("SAP.DE", [10, 10, 10, 10, 10, 10, 11]),
        _price_path("EXSA.DE", [5, 5, 5, 5, 5, 5, 5]),
    ])  # fmt: skip
    calls = pd.DataFrame([
        _call("AAA", "positive", "2026-10-16"),
        _call("BBB", "negative", "2026-10-16"),
        _call("SPY", "buy", "2026-10-16"),
        _call("SAP.DE", "buy", "2026-10-16"),
        _call("ZZZ", "buy", "2026-10-16"),  # no prices
    ])  # fmt: skip
    perf = trump.performance(calls, prices, {"US": "SPY", "Europe": "EXSA.DE"}).set_index("ticker")
    a = perf.loc["AAA"]
    assert a["reaction"] == pytest.approx(0.10)  # +10% on the day, SPY flat
    assert a["since"] == pytest.approx(0.10) and a["excess"] == pytest.approx(0.08)
    assert a["came_true"] is True
    assert perf.loc["BBB", "came_true"] is False  # attacked but beat SPY
    spy = perf.loc["SPY"]  # a market call counts in absolute terms
    assert spy["excess"] == pytest.approx(0.02) and pd.isna(spy["bench_since"])
    assert perf.loc["SAP.DE", "excess"] == pytest.approx(0.10)
    assert pd.isna(perf.loc["ZZZ", "came_true"])
    s = trump.summary(perf)
    assert (s["calls"], s["scored"], s["came_true"], s["explicit"]) == (5, 4, 3, 3)


def test_trump_notes_only_use_recent_logged_calls() -> None:
    calls = pd.DataFrame([
        _call("AAA", "negative", "2026-09-01", logged="2026-09-02"),  # too old
        _call("BBB", "buy", "2026-10-26", logged="2026-10-27"),
        _call("CCC", "positive", "2026-10-27", logged="2026-10-29"),  # logged after as_of
        _call("BBB", "positive", "2026-10-27", logged="2026-10-28"),  # newer call wins
    ])  # fmt: skip
    notes = trump.recent_notes(calls, date(2026, 10, 28))
    assert set(notes) == {"BBB"}
    assert notes["BBB"].startswith("Trump talked up on 2026-10-27: “BBB")
    assert trump.recent_notes(calls.iloc[:0], date(2026, 10, 28)) == {}


def test_trump_calls_are_logged_as_their_own_strategy(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from stocktrend.scoring.model import PicksFile

    meta = pd.DataFrame({"region": ["US", "US"], "sector": ["Energy", "Industrials"],
                         "kind": ["stock", "stock"]}, index=pd.Index(["XOM", "LMT"]))  # fmt: skip
    feats = pd.DataFrame({"liquid": [True, True], "excess_5d": [0.0, 0.0],
                          "excess_20d": [0.0, 0.0]}, index=meta.index)  # fmt: skip
    pf = PicksFile(date="2026-10-28", model_version="v1", weights={}, risk_multipliers={},
                   regime={}, events_used=[], picks=[])  # fmt: skip
    base = {"company": "x", "explicit": False, "said_on": "2026-10-27", "quote": "a quote",
            "sources": ["S001"]}  # fmt: skip
    calls = [TrumpCall.model_validate({"ticker": t, "stance": st, **base})
             for t, st in (("XOM", "positive"), ("LMT", "negative"), ("SPY", "buy"))]  # fmt: skip
    path = tmp_path / "picks.jsonl"
    logged = datetime(2026, 10, 28, 6, 30, tzinfo=UTC)
    ledger.record(pf, feats, meta, logged, path, trump_calls=calls)
    got = {(e.ticker, e.horizon, e.side) for e in ledger.read(path) if e.strategy == "trump"}
    assert got == {("XOM", 5, "long"), ("XOM", 20, "long"), ("LMT", 5, "avoid"),
                   ("LMT", 20, "avoid")}  # fmt: skip


def test_trump_report_section() -> None:
    from stocktrend.report.render import trump_markdown

    row = {"said_on": "2026-10-27", "ticker": "INTC", "company": "Intel", "stance": "Buy",
           "explicit": True, "quote": "Buy Intel | now", "reaction": 2.0, "since": 3.3,
           "excess": 2.8, "came_true": "Yes"}  # fmt: skip
    md = trump_markdown([row, {**row, "ticker": "F", "came_true": "", "since": None}])
    assert (
        "| **INTC** | Intel | Buy (explicit) | Buy Intel / now | +2.0% | +3.3% | +2.8% | Yes |"
        in md
    )
    assert "| \u2013 |" in md and "| open |" in md and "So far 1 of 1 came true" in md
    assert "No stock or market calls" in trump_markdown([])
