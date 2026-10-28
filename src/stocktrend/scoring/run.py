"""`stt score`: features + events + earnings -> ``data/picks/<date>.json``."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from stocktrend.backtest.priors import shock_z
from stocktrend.config import DATA_DIR
from stocktrend.features import earnings, regime, technical
from stocktrend.ingest import prices as price_store
from stocktrend.markets import split_suffix
from stocktrend.scoring import model
from stocktrend.universe.build import load as load_universe
from stocktrend.universe.exposures import Exposures, load_exposures
from stocktrend.universe.instruments import InstrumentsConfig, load_instruments

PICKABLE_ETF_KINDS = {"sector_etf", "theme_etf", "country_etf"}
PRIORS_PATH = DATA_DIR / "priors" / "theme_priors.json"
ACTIVE_Z = 1.5


def picks_path(day: str) -> Path:
    return DATA_DIR / "picks" / f"{day}.json"


def listing_region(ticker: str) -> str:
    """Where an instrument trades, which decides its benchmark (SPY or STOXX 600)."""
    return "Europe" if split_suffix(ticker)[1] else "US"


def build_meta(universe: pd.DataFrame, cfg: InstrumentsConfig) -> pd.DataFrame:
    """Everything that can be picked: stocks plus sector, theme and country ETFs."""
    stocks = universe[["ticker", "name", "region", "sector"]].assign(kind="stock")
    etfs = pd.DataFrame(
        [
            {
                "ticker": i.ticker,
                "name": i.name,
                "region": listing_region(i.ticker),
                "sector": i.sector or (f"ETF: {i.theme or i.country or i.kind}"),
                "kind": "etf",
            }
            for i in cfg.instruments
            if i.kind in PICKABLE_ETF_KINDS
        ]
    )
    return pd.concat([stocks, etfs], ignore_index=True).set_index("ticker")


def sector_etf_map(cfg: InstrumentsConfig) -> dict[tuple[str, str], str]:
    return {
        (i.region, i.sector): i.ticker
        for i in cfg.instruments
        if i.kind == "sector_etf" and i.sector
    }


def load_priors() -> dict[str, dict[int, float]]:
    """Usable theme priors, shrunk toward 0 when weak: mean x min(1, |t| / 2)."""
    if not PRIORS_PATH.exists():
        return {}
    raw = json.loads(PRIORS_PATH.read_text(encoding="utf-8"))
    out: dict[str, dict[int, float]] = {}
    for theme, entry in raw.get("themes", {}).items():
        for h, v in entry.items():
            if h.isdigit() and isinstance(v, dict) and v.get("usable"):
                shrink = min(1.0, abs(v.get("t_stat") or 0.0) / 2)
                out.setdefault(theme, {})[int(h)] = float(v["mean_excess"]) * shrink
    return out


def active_priors(
    priors: dict[str, dict[int, float]], exposures: Exposures, prices: pd.DataFrame
) -> dict[str, dict[int, float]]:
    """Keep the priors of themes whose driver is spiking now (5-day z >= ACTIVE_Z).

    The priors were measured after driver shocks, so they say nothing on calm days.
    """
    active = {}
    for theme, by_h in priors.items():
        th = exposures.themes.get(theme)
        if th is None or not th.driver:
            continue
        closes = prices.loc[prices["ticker"] == th.driver].sort_values("date")["close"]
        z = shock_z(closes.reset_index(drop=True))
        if z is not None and th.driver_sign * z >= ACTIVE_Z:
            active[theme] = by_h
    return active


@dataclass
class Context:
    cfg: InstrumentsConfig
    meta: pd.DataFrame
    sector_etfs: dict[tuple[str, str], str]
    prices: pd.DataFrame
    features: pd.DataFrame


def context() -> Context:
    """Reference data, cached prices and today's features, shared by score and record."""
    cfg = load_instruments()
    meta = build_meta(load_universe(), cfg)
    sector_etfs = sector_etf_map(cfg)
    prices = price_store.load()
    feats = technical.compute(
        prices,
        meta.reset_index().assign(is_etf=lambda d: d["kind"] == "etf"),
        cfg.benchmarks,
        sector_etfs,
    )
    return Context(cfg, meta, sector_etfs, prices, feats)


def run(day: str, ctx: Context | None = None) -> model.PicksFile:
    as_of = date.fromisoformat(day)
    ctx = ctx or context()
    meta, sector_etfs, feats = ctx.meta, ctx.sector_etfs, ctx.features
    reg = regime.compute(ctx.prices)
    events = model.load_events(DATA_DIR / "events", as_of)
    exposures = load_exposures()
    contribs = model.expand(events, as_of, exposures, meta, sector_etfs)
    stock_candidates = sorted(
        t for t in model.candidates(contribs) if t in meta.index and meta.at[t, "kind"] == "stock"
    )
    earn = earnings.next_earnings(stock_candidates, as_of)
    priors = active_priors(load_priors(), exposures, ctx.prices)
    result = model.score(as_of, events, exposures, meta, feats, sector_etfs, reg, earn, priors)
    path = picks_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(indent=1), encoding="utf-8")
    return result


def load(day: str) -> model.PicksFile:
    return model.PicksFile.model_validate_json(picks_path(day).read_text(encoding="utf-8"))
