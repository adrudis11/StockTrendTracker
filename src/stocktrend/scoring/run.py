"""`stt score`: features + events + earnings -> ``data/picks/<date>.json``."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from stocktrend.config import DATA_DIR
from stocktrend.features import earnings, regime, technical
from stocktrend.ingest import prices as price_store
from stocktrend.markets import split_suffix
from stocktrend.scoring import model
from stocktrend.universe.build import load as load_universe
from stocktrend.universe.exposures import load_exposures
from stocktrend.universe.instruments import InstrumentsConfig, load_instruments

PICKABLE_ETF_KINDS = {"sector_etf", "theme_etf", "country_etf"}
PRIORS_PATH = DATA_DIR / "priors" / "theme_priors.json"


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
    if not PRIORS_PATH.exists():
        return {}
    import json

    raw = json.loads(PRIORS_PATH.read_text(encoding="utf-8"))
    return {
        theme: {int(h): float(v["mean_excess"]) for h, v in horizons.items() if v.get("usable")}
        for theme, horizons in raw.get("themes", {}).items()
    }


def run(day: str) -> model.PicksFile:
    as_of = date.fromisoformat(day)
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
    reg = regime.compute(prices)
    events = model.load_events(DATA_DIR / "events", as_of)
    exposures = load_exposures()
    contribs = model.expand(events, as_of, exposures, meta, sector_etfs)
    stock_candidates = sorted(
        t for t in model.candidates(contribs) if t in meta.index and meta.at[t, "kind"] == "stock"
    )
    earn = earnings.next_earnings(stock_candidates, as_of)
    result = model.score(
        as_of, events, exposures, meta, feats, sector_etfs, reg, earn, load_priors()
    )
    path = picks_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(indent=1), encoding="utf-8")
    return result


def load(day: str) -> model.PicksFile:
    return model.PicksFile.model_validate_json(picks_path(day).read_text(encoding="utf-8"))
