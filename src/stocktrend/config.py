"""Paths and shared settings."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(os.environ.get("STT_HOME", Path(__file__).resolve().parents[2]))
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"  # committed to git: anything we must keep
CACHE_DIR = ROOT / "cache"  # gitignored: re-downloaded every session

UNIVERSE_CSV = DATA_DIR / "universe" / "stocks.csv"
PRICES_PARQUET = CACHE_DIR / "prices" / "daily.parquet"

USER_AGENT = "StockTrendTracker/0.1 (https://github.com/adrudis11/StockTrendTracker)"


def raw_dir(day: str) -> Path:
    """Directory holding one run's raw ingested data, e.g. cache/raw/2026-10-28."""
    return CACHE_DIR / "raw" / day


def load_yaml(name: str) -> Any:
    with (CONFIG_DIR / name).open(encoding="utf-8") as f:
        return yaml.safe_load(f)
