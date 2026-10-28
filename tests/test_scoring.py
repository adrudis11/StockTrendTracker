import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from stocktrend.events.schema import EventsFile
from stocktrend.features import regime, technical
from stocktrend.report.render import render
from stocktrend.scoring import model
from stocktrend.universe.exposures import Exposures

AS_OF = date(2026, 10, 28)

EXPOSURES = Exposures.model_validate(
    {
        "themes": {
            "oil": {
                "name": "Oil shock",
                "description": "Oil up",
                "members": {"XOM": 1, "CVX": 0.5, "DAL": -1, "XLE": 1},
            }
        }
    }
)

META = pd.DataFrame(
    [
        ["XOM", "Exxon", "US", "Energy", "stock"],
        ["CVX", "Chevron", "US", "Energy", "stock"],
        ["DAL", "Delta", "US", "Industrials", "stock"],
        ["EOG", "EOG", "US", "Energy", "stock"],
        ["XLE", "Energy SPDR", "US", "Energy", "etf"],
        ["BP.L", "BP", "Europe", "Energy", "stock"],
    ],
    columns=["ticker", "name", "region", "sector", "kind"],
).set_index("ticker")
SECTOR_ETFS = {("US", "Energy"): "XLE"}


def events(day: str, impacts: list[dict[str, object]], novelty: float = 1.0) -> EventsFile:
    return EventsFile.model_validate(
        {
            "date": day,
            "market_view": "",
            "events": [
                {
                    "id": f"{day}-01",
                    "title": "Refinery attack",
                    "category": "supply_disruption",
                    "regions": ["SA"],
                    "summary": "x",
                    "certainty": "confirmed",
                    "novelty": novelty,
                    "severity": 0.5,
                    "duration": "weeks",
                    "sources": ["S001"],
                    "impacts": impacts,
                }
            ],
        }
    )


def impact(target: str, direction: int = 1, horizon: int = 5) -> dict[str, object]:
    return {
        "target": target,
        "direction": direction,
        "magnitude": 1.0,
        "confidence": 1.0,
        "horizon_days": horizon,
        "rationale": "test rationale here",
    }


def test_decay_and_novelty() -> None:
    assert model.decay(0, 5) == 1.0
    assert model.decay(5, 5) == 0.0
    assert model.decay(10, 20) == 0.5
    assert model.novelty_factor(1.0) == 1.0
    assert model.novelty_factor(0.0) == pytest.approx(0.4)


def test_expand_applies_member_signs_and_horizon_spill() -> None:
    ef = events("2026-10-28", [impact("theme:oil")])
    c = model.expand([ef], AS_OF, EXPOSURES, META, SECTOR_ETFS)
    assert c[("XOM", 5)][0].strength == pytest.approx(1.0)
    assert c[("CVX", 5)][0].strength == pytest.approx(0.5)
    assert c[("DAL", 5)][0].strength == pytest.approx(-1.0)
    assert c[("XOM", 20)][0].strength == pytest.approx(0.3)  # 5-day call spills to 20d
    assert ("EOG", 5) not in c  # not a theme member


def test_expand_easing_flips_signs_and_old_events_fade() -> None:
    eased = events("2026-10-28", [impact("theme:oil", direction=-1)])
    c = model.expand([eased], AS_OF, EXPOSURES, META, SECTOR_ETFS)
    assert c[("DAL", 5)][0].strength > 0
    old = events("2026-10-21", [impact("theme:oil")])  # 5 trading days ago
    assert model.expand([old], AS_OF, EXPOSURES, META, SECTOR_ETFS) == {}


def test_expand_sector_targets_etf_and_stocks() -> None:
    ef = events("2026-10-28", [impact("sector:Energy@US", horizon=20)])
    c = model.expand([ef], AS_OF, EXPOSURES, META, SECTOR_ETFS)
    assert c[("XLE", 20)][0].strength == pytest.approx(1.0)
    assert c[("EOG", 20)][0].strength == pytest.approx(model.SECTOR_STOCK_WEIGHT)
    assert ("BP.L", 20) not in c  # other region


def _features(tickers: list[str], **overrides: dict[str, float]) -> pd.DataFrame:
    base = {
        "last": 100.0, "last_date": pd.Timestamp("2026-10-27"), "r1d": 0.0, "r5d": 0.0,
        "r20d": 0.0, "r60d": 0.0, "vol_daily": 0.02, "vol_annual": 0.3, "excess_5d": 0.0,
        "excess_20d": 0.0, "sector_excess_20d": 0.0, "volume_ratio": 1.0,
        "dist_52w_high": -0.1, "adv_usd": 1e9, "liquid": True,
    }  # fmt: skip
    rows = []
    for t in tickers:
        row = dict(base, ticker=t)
        row.update(overrides.get(t, {}))
        rows.append(row)
    return pd.DataFrame(rows).set_index("ticker")


def _score(features: pd.DataFrame, earnings: dict[str, str | None]) -> model.PicksFile:
    ef = events("2026-10-28", [impact("theme:oil")], novelty=0.5)
    return model.score(
        AS_OF, [ef], EXPOSURES, META, features, SECTOR_ETFS, {"label": "neutral"}, earnings
    )


def test_score_ranks_longs_and_avoids_with_risk_and_trend() -> None:
    feats = _features(
        ["XOM", "CVX", "DAL", "XLE"],
        CVX={"excess_5d": 0.04},  # +0.9 sigma move agreeing with the thesis
        XLE={"liquid": False},
    )
    pf = _score(feats, {"XOM": "2026-10-29", "CVX": "2026-12-01", "DAL": "2026-12-01"})
    longs = {p.ticker: p for p in pf.select(5, "long")}
    assert set(longs) == {"XOM", "CVX"}  # XLE is illiquid
    assert longs["XOM"].risk == pytest.approx(model.RISK["earnings_5d"])
    assert "inside the horizon" in longs["XOM"].risks[0]
    assert longs["CVX"].trend > 0
    assert longs["CVX"].rank == 1  # smaller signal, but no earnings and a confirming trend
    (dal,) = pf.select(5, "avoid")
    assert dal.ticker == "DAL" and dal.score < 0 and dal.side == "avoid"
    assert 0.5 < dal.prob < 0.75


def test_priced_in_penalty() -> None:
    calm = _score(_features(["XOM"]), {"XOM": "2026-12-01"}).select(5, "long")[0]
    moved = _score(
        _features(["XOM"], XOM={"excess_5d": 0.15}),  # +3.4 sigma already
        {"XOM": "2026-12-01"},
    ).select(5, "long")[0]
    assert moved.priced_in > 0 and moved.conviction < calm.conviction * 1.2
    assert any("already moved" in r for r in moved.risks)


def test_move_z() -> None:
    assert technical.move_z(0.02, 0.01, 4) == pytest.approx(1.0)
    assert technical.move_z(math.nan, 0.01, 5) == 0.0


def _prices(ticker: str, closes: list[float], volume: float = 1e6) -> pd.DataFrame:
    dates = pd.bdate_range(end="2026-10-27", periods=len(closes))
    return pd.DataFrame(
        {"date": dates, "ticker": ticker, "open": closes, "high": closes, "low": closes,
         "close": closes, "adj_close": closes, "volume": volume}
    )  # fmt: skip


def test_technical_features_and_liquidity() -> None:
    n = 80
    prices = pd.concat(
        [
            _prices("SPY", list(np.linspace(100, 110, n))),
            _prices("XOM", list(np.linspace(100, 130, n)), volume=1e6),
            _prices("TINY", list(np.linspace(10, 11, n)), volume=1000),
            _prices("EURUSD=X", [1.1] * n),
        ]
    )
    tickers = pd.DataFrame(
        [["XOM", "US", "Energy", False], ["TINY", "US", "Energy", False]],
        columns=["ticker", "region", "sector", "is_etf"],
    )
    f = technical.compute(prices, tickers, {"US": "SPY"}, {})
    xom = f.loc["XOM"].to_dict()
    assert xom["r20d"] > 0 and xom["excess_20d"] > 0
    assert bool(f.at["XOM", "liquid"]) and not bool(f.at["TINY", "liquid"])


def test_regime_labels() -> None:
    n = 260
    calm = pd.concat([_prices("^VIX", [14.0] * n), _prices("SPY", list(np.linspace(90, 100, n)))])
    assert regime.compute(calm)["label"] == "risk_on"
    scared = pd.concat([_prices("^VIX", [14.0] * (n - 1) + [32.0]), _prices("SPY", [100.0] * n)])
    assert regime.compute(scared)["label"] == "risk_off"


def test_report_renders() -> None:
    pf = _score(_features(["XOM", "CVX", "DAL"]), {})
    md = render(pf, events("2026-10-28", [impact("theme:oil")]), None)
    assert "Picks: next ~1 week" in md and "XOM" in md and "Avoid" in md
    assert "Uncalibrated" in md or "uncalibrated" in md
