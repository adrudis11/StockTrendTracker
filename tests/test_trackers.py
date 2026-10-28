from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stocktrend.backtest import trackers
from stocktrend.events.schema import TrumpCall
from stocktrend.ingest import polymarket
from stocktrend.trackers import congress, insiders, trump
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
    # "Over $X" has no upper bound: None when parsed, NaN once loaded with pandas.
    assert congress.amount_mid(50_000_000, None) == congress.amount_mid(50_000_000, float("nan"))


def test_asset_name() -> None:
    cases = {
        "Main Street Trust > Benchmark Electronics, Inc. Common Stock":
            "Benchmark Electronics, Inc.",
        "Account Snowflake Inc. Common Stock": "Snowflake Inc.",
        "ImmunityBio, Inc. - Common Stock": "ImmunityBio, Inc.",
        "CACI International, Inc. Class A Common Stock": "CACI International, Inc.",
        "NOKIA A SHS": "NOKIA",
        "Transocean Ltd": "Transocean Ltd",
    }  # fmt: skip
    for asset, name in cases.items():
        assert congress.asset_name(asset) == name


def test_estimated_trade_price() -> None:
    from stocktrend.report.dashboard import _last, _weighted_close

    closes = pd.Series([10.0, 11.0, 12.0], index=pd.to_datetime(["2026-10-01", "2026-10-02",
                                                                  "2026-10-05"]))  # fmt: skip
    # Saturday 3 Oct uses Friday's close; weights are the amount ranges' midpoints.
    days = [date(2026, 10, 1), date(2026, 10, 3)]
    assert _weighted_close(closes, days, [1.0, 3.0]) == pytest.approx((10 + 3 * 11) / 4, abs=0.01)
    assert _weighted_close(closes, [date(2026, 9, 1)], [1.0]) is None  # before the data
    assert _weighted_close(None, days, [1.0, 1.0]) is None
    assert _last(closes) == (12.0, "2026-10-05") and _last(None) == (None, None)


def test_congress_report_section() -> None:
    from stocktrend.report.render import congress_markdown

    row = {"ticker": "NVDA", "name": "Nvidia", "score": 6.1, "buyers": 2, "sellers": 0,
           "price_then": 180.0, "now": 198.0, "since": 10.0, "price_date": "2026-10-27",
           "members": "A, B", "last_filed": "2026-10-20"}  # fmt: skip
    md = congress_markdown([row], 0.0)
    assert "| NVDA | Nvidia | +6.10 | 2 | 0 | $180.00 | $198.00 | +10.0% | A, B |" in md
    assert "Price then is an estimate" in md and "the close on 2026-10-27" in md


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
    assert (a["price_then"], a["now"]) == (50, 55)  # the close before he said it, the latest
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
           "explicit": True, "quote": "Buy Intel | now", "price_then": 40.0, "now": 42.0,
           "since": 5.0, "currency": "USD", "price_date": "2026-10-30", "reaction": 2.0,
           "since_logged": 3.3, "excess": 2.8, "came_true": "Yes"}  # fmt: skip
    azn = {**row, "ticker": "AZN.L", "currency": "GBp", "price_then": 12_602.0, "now": None,
           "since": None, "came_true": ""}  # fmt: skip
    md = trump_markdown([row, azn])
    assert ("| **INTC** | Intel | Buy (explicit) | Buy Intel / now | $40.00 | $42.00 | +5.0% | "
            "+2.0% | +2.8% | Yes |") in md  # fmt: skip
    assert "| 12,602p | \u2013 | \u2013 |" in md and "| open |" in md
    assert "So far 1 of 1 came true" in md and "the close on 2026-10-30" in md
    assert "No stock or market calls" in trump_markdown([])


# --- SEC insider tracker -----------------------------------------------------

FORM4 = """<SEC-DOCUMENT>
<XML>
<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <issuer><issuerCik>0000320193</issuerCik><issuerTradingSymbol>AAPL</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Doe Jane</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>true</isOfficer>
      <officerTitle>CEO</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Doe Trust</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isTenPercentOwner>1</isTenPercentOwner></reportingOwnerRelationship>
  </reportingOwner>
  <aff10b5One>0</aff10b5One>
  <nonDerivativeTable>
    {rows}
  </nonDerivativeTable>
</ownershipDocument>
</XML>
</SEC-DOCUMENT>"""


def _tx(code: str, day: str, shares: str, price: str) -> str:
    return (
        f"<nonDerivativeTransaction><transactionDate><value>{day}</value></transactionDate>"
        f"<transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>"
        f"<transactionAmounts><transactionShares><value>{shares}</value></transactionShares>"
        f"<transactionPricePerShare><value>{price}</value></transactionPricePerShare>"
        "</transactionAmounts></nonDerivativeTransaction>"
    )


def test_parse_form4_keeps_open_market_trades_per_filing() -> None:
    rows = (
        _tx("P", "2026-10-20", "100", "200")
        + _tx("P", "2026-10-21", "50", "210")
        + _tx(
            "F",
            "2026-10-21",
            "30",
            "210",  # tax withholding: not an open-market trade
        )
    )
    trades = insiders.parse_form4(
        FORM4.format(rows=rows), "acc-1", date(2026, 10, 22), {320193: "AAPL"}
    )
    (t,) = trades
    assert (t.ticker, t.code, t.shares, t.value) == ("AAPL", "P", 150.0, 30_500.0)
    assert (
        t.tx_date == date(2026, 10, 21) and t.owner == "Doe Jane +1" and t.role == "CEO, Director"
    )
    assert not t.plan
    assert insiders.parse_form4(FORM4.format(rows=rows), "acc-1", date(2026, 10, 22), {}) == []


def test_parse_daily_index() -> None:
    text = (
        "Form Type   Company Name   CIK   Date Filed  File Name\n" + "-" * 40 + "\n"
        "4                2752128 Ontario Ltd.                 2162362     20261027    edgar/data/2162362/0001493152-26-048754.txt   \n"
        "4/A              APPLE INC                            320193      20261027    edgar/data/320193/0000320193-26-000001.txt\n"
        "8-K              APPLE INC                            320193      20261027    edgar/data/320193/0000320193-26-000002.txt\n"
    )
    assert insiders.parse_daily_index(text) == [
        (2162362, "edgar/data/2162362/0001493152-26-048754.txt"),
        (320193, "edgar/data/320193/0000320193-26-000001.txt"),
    ]


def _dataset_zip() -> bytes:
    import io
    import zipfile

    def tsv(rows: list[list[str]]) -> str:
        return "\n".join("\t".join(r) for r in rows) + "\n"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:  # an older set: no AFF10B5ONE column
        z.writestr("SUBMISSION.tsv", tsv([
            ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK"],
            ["a1", "03-FEB-2022", "4", "0000320193"],
            ["a2", "03-FEB-2022", "4", "0000999999"],  # not in our universe
            ["a3", "04-FEB-2022", "3", "0000320193"],  # an initial holdings report
        ]))  # fmt: skip
        z.writestr("NONDERIV_TRANS.tsv", tsv([
            ["ACCESSION_NUMBER", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES", "TRANS_PRICEPERSHARE"],
            ["a1", "01-FEB-2022", "S", "10", "150"],
            ["a1", "02-FEB-2022", "S", "10", "160"],
            ["a1", "02-FEB-2022", "M", "10", "0"],
            ["a2", "01-FEB-2022", "P", "10", "5"],
        ]))  # fmt: skip
        z.writestr("REPORTINGOWNER.tsv", tsv([
            ["ACCESSION_NUMBER", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"],
            ["a1", "Roe Rick", "Officer", "CFO"],
        ]))  # fmt: skip
    return buf.getvalue()


def test_parse_dataset() -> None:
    (t,) = insiders.parse_dataset(_dataset_zip(), {320193: "AAPL"})
    assert (t.accession, t.code, t.shares, t.value, t.role) == ("a1", "S", 20.0, 3_100.0, "CFO")
    assert t.filed == date(2022, 2, 3) and t.tx_date == date(2022, 2, 2) and not t.plan


class _FakeSEC:
    """Answers the URLs ``insiders.update`` asks for, from a dict; 404 otherwise."""

    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages
        self.asked: list[str] = []

    def get(self, url: str, timeout: int = 0) -> object:
        import requests

        self.asked.append(url)
        r = requests.Response()
        r.status_code = 200 if url in self.pages else 404
        r._content = self.pages.get(url, b"")
        r.encoding = "utf-8"
        return r


def test_insider_update_backfills_then_reads_daily_filings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(insiders, "ticker_map", lambda http, universe: {320193: "AAPL"})
    monkeypatch.setattr(insiders._Pace, "wait", lambda self: None)
    # 1 October: the first day after the 2026q3 data set.
    index = (
        "4  APPLE INC  320193  20261001  edgar/data/320193/acc-2.txt\n"
        "4  DOE JANE   1234567 20261001  edgar/data/1234567/acc-2.txt\n"  # same filing, owner's id
    )
    pages = {
        insiders.DATASET_URLS[1].format(q="2026q3"): _dataset_zip(),
        insiders.DAILY_INDEX_URL.format(y=2026, q=4, ymd="20261001"): index.encode(),
        insiders.ARCHIVES_URL + "edgar/data/320193/acc-2.txt": FORM4.format(
            rows=_tx("P", "2026-09-30", "1000", "250")
        ).encode(),
    }
    http = _FakeSEC(pages)
    tp, sp = tmp_path / "t.jsonl", tmp_path / "s.json"
    stats = insiders.update(http, ["AAPL"], date(2026, 10, 2), "2026q3",  # type: ignore[arg-type]
                            trades_path=tp, state_path=sp)  # fmt: skip
    assert stats["quarters"] == 1 and stats["days"] == 1 and stats["new_trades"] == 2
    trades = insiders.load_trades(tp)
    assert list(trades["accession"]) == ["a1", "acc-2"]
    assert sum(u.endswith("acc-2.txt") for u in http.asked) == 1  # fetched once
    # The next run reads only the new day; the 2nd is a holiday index (404) and too recent
    # to skip, so it is retried later.
    again = insiders.update(http, ["AAPL"], date(2026, 10, 3), trades_path=tp, state_path=sp)  # type: ignore[arg-type]
    assert again["quarters"] == 0 and again["days"] == 0 and again["new_trades"] == 0


def test_insider_signal_counts_purchases_only() -> None:
    d = date(2026, 10, 28)
    rows = [
        ("AAPL", "A", "P", 2, 500_000.0), ("AAPL", "B", "P", 3, 20_000.0),
        ("AAPL", "C", "S", 3, 9_000_000.0), ("MSFT", "D", "S", 2, 1_000_000.0),
        ("NVDA", "E", "P", 0, 1_000_000.0),  # filed today: not public before the open
        ("XOM", "F", "P", 45, 1_000_000.0),  # filed too long ago
    ]  # fmt: skip
    trades = pd.DataFrame([
        {"accession": f"a{i}", "filed": d - timedelta(days=age), "ticker": t, "owner": o,
         "role": "Director", "code": c, "tx_date": d - timedelta(days=age + 2), "shares": 1.0,
         "value": v, "plan": False}
        for i, (t, o, c, age, v) in enumerate(rows)
    ])  # fmt: skip
    sig = insiders.signal(trades, d)
    assert list(sig.index) == ["AAPL", "MSFT"]
    a = sig.loc["AAPL"]
    assert a["score"] == pytest.approx(np.log10(500) + np.log10(20))
    assert (a["buyers"], a["sellers"], a["bought"]) == (2, 1, 520_000)
    assert a["avg_price"] == 260_000 and a["last_buy"] == d - timedelta(days=4)  # 2 shares
    assert pd.isna(sig.loc["MSFT", "avg_price"])
    assert sig.loc["MSFT", "score"] == 0
    assert (
        insiders.note(a.to_dict()) == "2 insiders bought $520k (Form 4s filed in the last 30 days)"
    )
    assert insiders.note(sig.loc["MSFT"].to_dict()) is None


def test_insiders_report_section() -> None:
    from stocktrend.report.render import insiders_markdown

    row = {"ticker": "AAPL", "name": "Apple", "score": 4.3, "buyers": 2, "bought": 520_000,
           "paid": 250.0, "now": 262.5, "since": 5.0, "price_date": "2026-10-27",
           "sellers": 1, "insiders": "A (Director); B (CEO)", "last_filed": "2026-10-26"}  # fmt: skip
    md = insiders_markdown([row, {**row, "ticker": "X", "now": None, "since": None}], 0.0)
    assert ("| AAPL | Apple | 4.3 | 2 | $520k | $250.00 | $262.50 | +5.0% | 1 | "
            "A (Director); B (CEO) |") in md  # fmt: skip
    assert "| $250.00 | \u2013 | \u2013 |" in md and "the close on 2026-10-27" in md
    assert "Weight in the score: 0" in md
    assert "No open-market purchases" in insiders_markdown([], 0.0)


def test_open_picks_report_section() -> None:
    from stocktrend.report.render import open_picks_markdown

    row = {"date": "2026-10-28", "ticker": "AIR.PA", "name": "Airbus", "horizon": 20,
           "side": "long", "currency": "EUR", "entry": 150.0, "now": 156.0, "since": 4.0,
           "vs_benchmark": 3.1, "on_track": "Yes", "ends": "2026-11-24",
           "price_date": "2026-10-30"}  # fmt: skip
    pending = {**row, "ticker": "XOM", "currency": "USD", "entry": None, "since": None,
               "vs_benchmark": None, "on_track": ""}  # fmt: skip
    md = open_picks_markdown([row, pending])
    assert "2 open, 1 traded since their entry; 1 on track" in md
    assert ("| 2026-10-28 | **AIR.PA** | Airbus | 20d | long | €150.00 | €156.00 | +4.0% | "
            "+3.1% | 2026-11-24 |") in md  # fmt: skip
    assert "the close on 2026-10-30" in md
    assert "No picks from earlier days are open" in open_picks_markdown([])
