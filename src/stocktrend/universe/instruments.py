"""Benchmarks, ETFs and macro series listed in ``config/instruments.yaml``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from stocktrend.config import load_yaml

Kind = Literal["benchmark", "index", "sector_etf", "theme_etf", "country_etf", "macro"]


class Instrument(BaseModel):
    ticker: str
    kind: Kind
    region: str
    name: str
    sector: str | None = None
    theme: str | None = None
    country: str | None = None


class InstrumentsConfig(BaseModel):
    benchmarks: dict[str, str]
    instruments: list[Instrument]


def load_instruments() -> InstrumentsConfig:
    cfg = InstrumentsConfig.model_validate(load_yaml("instruments.yaml"))
    tickers = [i.ticker for i in cfg.instruments]
    dupes = {t for t in tickers if tickers.count(t) > 1}
    if dupes:
        raise ValueError(f"duplicate instruments: {sorted(dupes)}")
    missing = set(cfg.benchmarks.values()) - set(tickers)
    if missing:
        raise ValueError(f"benchmarks not listed as instruments: {sorted(missing)}")
    return cfg
