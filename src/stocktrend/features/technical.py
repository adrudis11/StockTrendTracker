"""Per-ticker price features: momentum, volatility, relative strength, liquidity."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from stocktrend.markets import exchange_for

TRADING_DAYS = 252

# Minimum average daily traded value (20 sessions). Stocks: as in the plan.
# ETFs trade less on European exchanges but are diversified, so the bar is lower.
MIN_ADV = {"US": 20e6, "Europe": 10e6}  # USD for US, EUR for Europe
MIN_ADV_ETF = {"US": 5e6, "Europe": 1e6}


def fx_to_usd(prices: pd.DataFrame) -> dict[str, float]:
    """Latest conversion factors from a Yahoo quote currency to USD."""
    last = prices.sort_values("date").groupby("ticker")["close"].last()

    def rate(t: str, default: float) -> float:
        v = last.get(t)
        return float(v) if v is not None and v > 0 else default

    eurusd, gbpusd, usdchf = rate("EURUSD=X", 1.1), rate("GBPUSD=X", 1.3), rate("USDCHF=X", 0.9)
    return {
        "USD": 1.0,
        "EUR": eurusd,
        "GBp": gbpusd / 100,  # London quotes in pence
        "GBP": gbpusd,
        "CHF": 1 / usdchf,
        "DKK": eurusd / 7.46,  # DKK is pegged to EUR
        "SEK": eurusd / 11.5,  # rough; only affects the liquidity filter
        "NOK": eurusd / 11.7,
    }


def compute(
    prices: pd.DataFrame,
    tickers: pd.DataFrame,
    benchmarks: dict[str, str],
    sector_etfs: dict[tuple[str, str], str],
) -> pd.DataFrame:
    """Features for every ticker in ``tickers`` (columns: ticker, region, sector, is_etf).

    Excess returns are against the region's benchmark and the region's sector ETF,
    in each instrument's own currency (good enough for short-horizon features).
    """
    fx = fx_to_usd(prices)
    eurusd = fx["EUR"]
    by_ticker = {t: g for t, g in prices.sort_values("date").groupby("ticker", sort=False)}

    def closes(t: str) -> np.ndarray:
        g = by_ticker.get(t)
        if g is None:
            return np.array([])
        return np.asarray(g["adj_close"].fillna(g["close"]).to_numpy(dtype=float))

    def ret(c: np.ndarray, n: int) -> float:
        return float(c[-1] / c[-1 - n] - 1) if len(c) > n else math.nan

    bench_rets = {
        region: {n: ret(closes(t), n) for n in (5, 20)} for region, t in benchmarks.items()
    }
    sector_rets = {key: {n: ret(closes(t), n) for n in (5, 20)} for key, t in sector_etfs.items()}

    rows = []
    for r in tickers.itertuples(index=False):
        t, region = str(r.ticker), str(r.region)
        g = by_ticker.get(t)
        c = closes(t)
        if g is None or len(c) < 25:
            continue
        log_rets = np.diff(np.log(c[-61:]))
        vol_d = float(np.std(log_rets[-20:], ddof=1)) if len(log_rets) >= 20 else math.nan
        vol = g["volume"].to_numpy(dtype=float)
        turnover = (g["close"].to_numpy(dtype=float) * vol)[-20:]
        currency = exchange_for(t).currency
        adv_usd = float(np.nanmean(turnover)) * fx.get(currency, 1.0)
        adv_local = adv_usd if region == "US" else adv_usd / eurusd
        floor = (MIN_ADV_ETF if r.is_etf else MIN_ADV).get(region, MIN_ADV["US"])
        sector_key = (region, str(r.sector))
        row = {
            "ticker": t,
            "last_date": g["date"].iloc[-1],
            "last": float(c[-1]),
            "r1d": ret(c, 1),
            "r5d": ret(c, 5),
            "r20d": ret(c, 20),
            "r60d": ret(c, 60),
            "vol_daily": vol_d,
            "vol_annual": vol_d * math.sqrt(TRADING_DAYS),
            "excess_5d": ret(c, 5) - bench_rets.get(region, {}).get(5, math.nan),
            "excess_20d": ret(c, 20) - bench_rets.get(region, {}).get(20, math.nan),
            "sector_excess_20d": ret(c, 20) - sector_rets.get(sector_key, {}).get(20, math.nan),
            "volume_ratio": float(np.nanmean(vol[-5:]) / np.nanmean(vol[-60:]))
            if np.nanmean(vol[-60:]) > 0
            else math.nan,
            "dist_52w_high": float(c[-1] / np.max(c[-TRADING_DAYS:]) - 1),
            "adv_usd": adv_usd,
            # Index-level ETFs report no volume for some UCITS lines; treat as liquid.
            "liquid": bool(adv_local >= floor) or (bool(r.is_etf) and adv_usd == 0),
        }
        rows.append(row)
    return pd.DataFrame(rows).set_index("ticker")


def move_z(excess: float, vol_daily: float, horizon: int) -> float:
    """An excess move expressed in standard deviations for that horizon."""
    if not (math.isfinite(excess) and math.isfinite(vol_daily)) or vol_daily <= 0:
        return 0.0
    return excess / (vol_daily * math.sqrt(horizon))
