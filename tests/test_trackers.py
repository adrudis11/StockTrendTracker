from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from stocktrend.backtest import trackers
from stocktrend.ingest import polymarket
from stocktrend.trackers import congress

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
