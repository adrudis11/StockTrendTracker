from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import pytest

from stocktrend.backtest import momentum, priors
from stocktrend.scoring import model
from stocktrend.scoring.model import Contribution, Driver, Pick, PicksFile
from stocktrend.tracking import ledger, scoreboard
from stocktrend.tracking.evaluate import (
    PriceBook,
    entry_session,
    evaluate,
    evaluate_entry,
    exit_session,
    read_outcomes,
)

LOGGED = datetime(2026, 10, 28, 6, 30, tzinfo=UTC)  # before both opens


Side = Literal["long", "avoid"]


def _pick(ticker: str, side: Side = "long", horizon: int = 5, region: str = "US") -> Pick:
    return Pick(
        ticker=ticker, name=ticker, kind="stock", region=region, sector="Energy",
        horizon=horizon, side=side, rank=1, score=0.3, conviction=0.3, prob=0.62, geo=0.3,
        prior=0.0, trend=0.0, priced_in=0.0, risk=1.0, risks=[],
        drivers=[Driver(event_id="e", title="t", target="theme:x", via="x", strength=0.2,
                        rationale="r")],
        primary_theme="x", last_close=100.0, last_date="2026-10-27", r5d=0.0, r20d=0.0,
        excess_5d=0.0, excess_20d=0.0, vol_annual=0.3, next_earnings=None,
    )  # fmt: skip


def _picks_file(picks: list[Pick]) -> PicksFile:
    return PicksFile(
        date="2026-10-28", model_version="v1", weights={}, risk_multipliers={},
        regime={}, events_used=[], picks=picks,
    )  # fmt: skip


# --- entry and exit timing --------------------------------------------------


def test_entry_is_first_open_after_logging() -> None:
    assert entry_session("XNYS", LOGGED) == pd.Timestamp("2026-10-28")
    after_eu_open = datetime(2026, 10, 28, 9, 38, tzinfo=UTC)
    assert entry_session("XETR", after_eu_open) == pd.Timestamp("2026-10-29")
    assert entry_session("XNYS", after_eu_open) == pd.Timestamp("2026-10-28")  # opens 13:30
    saturday = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    assert entry_session("XNYS", saturday) == pd.Timestamp("2026-11-02")


def test_exit_counts_entry_as_session_one() -> None:
    # Wed 28 Oct + 5 sessions -> Wed, Thu, Fri, Mon, Tue 3 Nov
    assert exit_session("XNYS", pd.Timestamp("2026-10-28"), 5) == pd.Timestamp("2026-11-03")


# --- ledger -------------------------------------------------------------------


def _features(tickers: list[str]) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    return pd.DataFrame(
        {"excess_5d": rng.normal(0, 0.03, len(tickers)),
         "excess_20d": rng.normal(0, 0.06, len(tickers)), "liquid": True},
        index=pd.Index(tickers, name="ticker"),
    )  # fmt: skip


def _meta(tickers: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {"name": tickers, "region": "US", "sector": ["Energy", "Industrials"] * (len(tickers) // 2),
         "kind": "stock"},
        index=pd.Index(tickers, name="ticker"),
    )  # fmt: skip


def test_ledger_is_append_only_and_logs_baselines(tmp_path: Path) -> None:
    path = tmp_path / "picks.jsonl"
    tickers = [f"S{i}" for i in range(40)]
    pf = _picks_file([_pick("S1"), _pick("S2", side="avoid")])
    n = ledger.record(pf, _features(tickers), _meta(tickers), LOGGED, path)
    entries = ledger.read(path)
    assert n == len(entries)
    by = {(e.strategy, e.horizon, e.side) for e in entries}
    assert ("momentum", 20, "avoid") in by and ("random", 5, "long") in by
    momentum_longs = [e for e in entries if e.strategy == "momentum" and e.horizon == 5
                      and e.side == "long"]  # fmt: skip
    assert len(momentum_longs) == model.MAX_PICKS
    # random picks come from the picks' sectors (Energy) and are reproducible
    rand = [e for e in entries if e.strategy == "random" and e.horizon == 5 and e.side == "long"]
    assert {e.sector for e in rand} == {"Energy"}
    assert ledger.record(pf, _features(tickers), _meta(tickers), LOGGED, path) == 0
    # Tracker strategies log their strongest buys (score > 0) as longs.
    sig = pd.DataFrame({"score": [3.0, 0.0, 1.0]}, index=pd.Index(["S5", "S6", "NOPE"]))
    n = ledger.record(pf, _features(tickers), _meta(tickers), LOGGED, path, insiders=sig)
    added = [e for e in ledger.read(path) if e.strategy == "insiders"]
    assert n == len(added) == 2 and {e.ticker for e in added} == {"S5"}


# --- evaluation ---------------------------------------------------------------


def _bars(
    ticker: str, opens: list[float], closes: list[float], start: str = "2026-10-28"
) -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({"date": dates, "ticker": ticker, "open": opens, "high": closes,
                         "low": closes, "close": closes, "adj_close": closes, "volume": 1e6})  # fmt: skip


def _entry(ticker: str, side: Side = "long", region: str = "US") -> ledger.LedgerEntry:
    return ledger.LedgerEntry(
        pick_id=f"x:{ticker}:{side}", date="2026-10-28", logged_at=LOGGED, strategy="geo",
        horizon=5, side=side, ticker=ticker, region=region, sector="Energy", kind="stock", rank=1,
        prob=0.6,
    )  # fmt: skip


def test_mark_open_pick_at_the_latest_close() -> None:
    from stocktrend.tracking.evaluate import mark

    # Three sessions in: entry at the 28th's open (100), latest close 106; SPY +2%.
    prices = pd.concat([
        _bars("XOM", [100, 104, 105], [103, 105, 106]),
        _bars("SPY", [100, 100, 101], [100, 101, 102]),
    ])  # fmt: skip
    book = PriceBook(prices)
    m = mark(_entry("XOM"), book, {"US": "SPY"})
    assert (m.entry_open, m.last_close, str(m.last_date)) == (100, 106, "2026-10-30")
    assert str(m.entry_date) == "2026-10-28" and str(m.exit_date) == "2026-11-03"
    assert m.excess == pytest.approx(0.04)
    assert mark(_entry("XOM", "avoid"), book, {"US": "SPY"}).excess == pytest.approx(-0.04)
    # Before the entry session has traded: no entry or excess yet, the latest close only.
    early = PriceBook(_bars("XOM", [99], [99], start="2026-10-27"))
    m = mark(_entry("XOM"), early, {"US": "SPY"})
    assert (m.entry_open, m.last_close, m.excess) == (None, 99, None)


def test_evaluate_long_and_avoid_against_benchmark() -> None:
    prices = pd.concat([
        _bars("XOM", [100] * 6, [101, 102, 103, 104, 110, 111]),  # +10% open->close day 5
        _bars("SPY", [100] * 6, [100, 100, 101, 101, 102, 102]),  # +2%
        _bars("XLE", [100] * 6, [100, 101, 102, 103, 104, 105]),
    ])  # fmt: skip
    book = PriceBook(prices)
    now = datetime(2026, 11, 5, tzinfo=UTC)
    long = evaluate_entry(_entry("XOM"), book, {"US": "SPY"}, {("US", "Energy"): "XLE"}, now)
    assert long is not None
    assert long.exit_date == "2026-11-03"
    assert long.ret == pytest.approx(0.10) and long.bench_ret == pytest.approx(0.02)
    assert long.excess == pytest.approx(0.08) and long.correct
    assert long.sector_excess == pytest.approx(0.06)
    avoid = evaluate_entry(_entry("XOM", "avoid"), book, {"US": "SPY"}, {}, now)
    assert avoid is not None and avoid.excess == pytest.approx(-0.08) and not avoid.correct


def test_evaluate_converts_pence_to_eur_and_waits_for_maturity() -> None:
    prices = pd.concat([
        _bars("BP.L", [500] * 6, [500, 505, 510, 515, 550, 550]),  # +10% in GBp
        _bars("EXSA.DE", [60] * 6, [60] * 6),  # flat
        _bars("EURGBP=X", [0.85] * 6, [0.85, 0.85, 0.85, 0.85, 0.88, 0.88]),  # pound weakens
    ])  # fmt: skip
    book = PriceBook(prices)
    out = evaluate_entry(_entry("BP.L", region="Europe"), book, {"Europe": "EXSA.DE"}, {},
                         datetime(2026, 11, 5, tzinfo=UTC))  # fmt: skip
    assert out is not None
    assert out.ret == pytest.approx(1.10 * 0.85 / 0.88 - 1)
    early = PriceBook(prices[prices["date"] < "2026-11-02"])
    assert evaluate_entry(_entry("BP.L", region="Europe"), early, {"Europe": "EXSA.DE"}, {},
                          datetime(2026, 11, 1, tzinfo=UTC)) is None  # fmt: skip


def test_evaluate_writes_each_outcome_once(tmp_path: Path) -> None:
    led, out = tmp_path / "picks.jsonl", tmp_path / "outcomes.jsonl"
    ledger.append([_entry("XOM")], led)
    prices = pd.concat([_bars("XOM", [100] * 6, [101] * 6), _bars("SPY", [100] * 6, [100] * 6)])
    now = datetime(2026, 11, 5, tzinfo=UTC)
    assert evaluate(prices, {"US": "SPY"}, {}, now, led, out) == (1, 0)
    assert evaluate(prices, {"US": "SPY"}, {}, now, led, out) == (0, 0)
    assert len(read_outcomes(out)) == 1


# --- scoreboard ---------------------------------------------------------------


def test_scoreboard_stats_and_calibration(tmp_path: Path) -> None:
    led, out = tmp_path / "picks.jsonl", tmp_path / "outcomes.jsonl"
    entries = [_entry(t) for t in ("XOM", "CVX")]
    ledger.append(entries, led)
    prices = pd.concat([
        _bars("XOM", [100] * 6, [105] * 6), _bars("CVX", [100] * 6, [95] * 6),
        _bars("SPY", [100] * 6, [100] * 6),
    ])  # fmt: skip
    evaluate(prices, {"US": "SPY"}, {}, datetime(2026, 11, 5, tzinfo=UTC), led, out)
    sb = scoreboard.compute(ledger.read(led), read_outcomes(out))
    (row,) = sb["rows"]
    assert row["n"] == 2 and row["hit_rate"] == 0.5
    assert sb["brier"] == pytest.approx(((0.6 - 1) ** 2 + 0.6**2) / 2)
    assert sb["calibration"][0]["n"] == 2
    md = scoreboard.to_markdown(sb)
    assert "geo" in md and "too early" in md


def test_scoreboard_empty() -> None:
    sb = scoreboard.compute([], [])
    assert "No picks have matured" in scoreboard.to_markdown(sb, "2026-11-03")


# --- priors and momentum backtest ------------------------------------------------


def _closes(n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(1)
    idx = pd.bdate_range("2024-01-01", periods=n)
    driver = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    for i in range(150, n - 30, 40):  # inject spikes the members keep following
        driver[i - 5 : i + 1] *= np.linspace(1, 1.08, 6)
        driver[i + 1 :] *= 1.08
    spike = pd.Series(driver, index=idx).pct_change().fillna(0)
    follow = spike.shift(1).fillna(0) + rng.normal(0, 0.002, n)  # reacts a day later
    member = 50 * np.exp(np.cumsum(follow))
    bench = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    return pd.DataFrame({"DRV": driver, "MEM": member, "SPY": bench}, index=idx)


def test_shock_detection_and_prior_sign() -> None:
    closes = _closes()
    shocks = priors.shock_days(closes["DRV"], 1, 5)
    assert len(shocks) >= 4
    result = priors.theme_prior(closes, {"MEM": 1, "DRV": 1}, "DRV", 1, {}, {"US": "SPY"}, 5)
    assert result["n"] == len([s for s in shocks if s <= closes.index[-6]])
    assert result["mean_excess"] > 0  # members continued after shocks
    assert priors.shock_z(closes["DRV"]) is not None


def test_prior_only_boosts_contributions_in_the_thesis_direction() -> None:
    def contrib(strength: float, direction: int = 1) -> Contribution:
        return Contribution("e", "t", "theme:oil", "oil", strength, 5, "r", direction)

    p = {"oil": {5: -0.01}}  # history says: moves reverse
    assert model._prior([contrib(0.2)], p, 5, side=1) == pytest.approx(-0.5)
    assert model._prior([contrib(-0.2)], p, 5, side=-1) == pytest.approx(-0.5)  # losers too
    assert model._prior([contrib(-0.2, direction=-1)], p, 5, side=-1) == 0.0  # easing
    assert model._prior([contrib(0.2)], {}, 5, side=1) == 0.0


def test_momentum_backtest_detects_persistent_trend() -> None:
    n = 300
    idx = pd.bdate_range("2024-01-01", periods=n)
    data = {"SPY": np.linspace(100, 110, n)}
    for i in range(30):
        drift = 0.002 if i < 10 else -0.0005  # first ten keep outperforming
        data[f"S{i}"] = 100 * np.exp(np.cumsum(np.full(n, drift)))
    closes = pd.DataFrame(data, index=idx)
    regions = {f"S{i}": "US" for i in range(30)}
    # the backtest needs at least 50 stocks per date; pad with flat names
    for i in range(30, 60):
        closes[f"S{i}"] = 100.0
        regions[f"S{i}"] = "US"
    r = momentum.run(closes, regions, {"US": "SPY"}, 20)
    assert r["long"]["mean_excess"] > 0 and r["long"]["hit_rate"] > 0.9
