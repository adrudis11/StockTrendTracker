"""Build the stock universe from index constituent tables on Wikipedia.

The result is written to ``data/universe/stocks.csv`` and committed, so daily runs
use a reviewed, stable list instead of re-scraping. Rebuild it about once a month
(``stt universe build``) to pick up index changes.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests

from stocktrend.config import UNIVERSE_CSV
from stocktrend.http import DEFAULT_TIMEOUT
from stocktrend.markets import exchange_for, to_yahoo
from stocktrend.universe.sectors import normalize_label, to_gics

WIKI_URL = "https://en.wikipedia.org/wiki/{page}"

COLUMNS = [
    "ticker",
    "name",
    "region",
    "country",
    "exchange",
    "currency",
    "sector",
    "industry",
    "sector_raw",
    "indices",
    "cik",
]


@dataclass(frozen=True)
class IndexSpec:
    key: str
    page: str
    region: str  # "US" or "Europe"
    ticker_col: str
    name_col: str
    sector_col: str
    industry_col: str | None = None
    suffix: str | None = None  # appended when the table lists bare symbols
    min_rows: int = 15


# National indices come before the Euro STOXX 50 so their sector labels win when
# a company appears in both.
INDEX_SPECS: tuple[IndexSpec, ...] = (
    IndexSpec(
        "SP500",
        "List_of_S%26P_500_companies",
        "US",
        "Symbol",
        "Security",
        "GICS Sector",
        industry_col="GICS Sub-Industry",
        min_rows=450,
    ),
    IndexSpec("DAX", "DAX", "Europe", "Ticker", "Company", "Prime Standard Sector"),
    IndexSpec(
        "CAC40", "CAC_40", "Europe", "Ticker", "Company", "Sector", industry_col="GICS Sub-Industry"
    ),
    IndexSpec("IBEX35", "IBEX_35", "Europe", "Ticker", "Company", "Sector"),
    IndexSpec(
        "FTSE100",
        "FTSE_100_Index",
        "Europe",
        "Ticker",
        "Company",
        "FTSE industry classification benchmark sector",
        suffix=".L",
        min_rows=90,
    ),
    IndexSpec("FTSEMIB", "FTSE_MIB", "Europe", "Ticker", "Company", "ICB Sector"),
    IndexSpec("AEX", "AEX_index", "Europe", "Ticker", "Company", "ICB Sector"),
    IndexSpec("SMI", "Swiss_Market_Index", "Europe", "Ticker", "Name", "Sector", suffix=".SW"),
    IndexSpec("EUROSTOXX50", "EURO_STOXX_50", "Europe", "Ticker", "Name", "Sector", min_rows=45),
)


def find_table(html: str, spec: IndexSpec) -> pd.DataFrame:
    """Return the constituents table: the first one that has all the spec's columns."""
    wanted = [spec.ticker_col, spec.name_col, spec.sector_col]
    if spec.industry_col:
        wanted.append(spec.industry_col)
    for table in pd.read_html(io.StringIO(html), flavor="lxml"):
        table.columns = [normalize_label(str(c)) for c in table.columns]
        if len(table) >= spec.min_rows and all(normalize_label(w) in table.columns for w in wanted):
            return table
    raise ValueError(f"{spec.key}: no table with columns {wanted} and >= {spec.min_rows} rows")


def parse_constituents(html: str, spec: IndexSpec) -> pd.DataFrame:
    table = find_table(html, spec)
    col = {
        name: normalize_label(name) for name in (spec.ticker_col, spec.name_col, spec.sector_col)
    }
    rows = []
    for _, r in table.iterrows():
        symbol = r[col[spec.ticker_col]]
        if not isinstance(symbol, str) or not symbol.strip():
            continue
        ticker = to_yahoo(symbol, spec.suffix)
        exch = exchange_for(ticker)
        sector_raw = str(r[col[spec.sector_col]]).strip()
        industry = (
            str(r[normalize_label(spec.industry_col)]).strip() if spec.industry_col else sector_raw
        )
        cik = ""
        if "cik" in table.columns and pd.notna(r["cik"]):
            cik = str(int(r["cik"])).zfill(10)
        rows.append(
            {
                "ticker": ticker,
                "name": str(r[col[spec.name_col]]).strip(),
                "region": spec.region,
                "country": exch.country,
                "exchange": exch.mic,
                "currency": exch.currency,
                "sector": to_gics(sector_raw),
                "industry": industry,
                "sector_raw": sector_raw,
                "indices": spec.key,
                "cik": cik,
            }
        )
    return pd.DataFrame(rows, columns=COLUMNS)


def merge(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate index tables, keeping one row per ticker and all its index memberships."""
    df = pd.concat(frames, ignore_index=True)
    memberships = df.groupby("ticker", sort=False)["indices"].agg(
        lambda s: "|".join(dict.fromkeys(s))
    )
    first = df.drop_duplicates("ticker", keep="first").set_index("ticker")
    first["indices"] = memberships
    return first.reset_index()[COLUMNS].sort_values(["region", "ticker"], ignore_index=True)


def fetch_page(http: requests.Session, page: str) -> str:
    r = http.get(WIKI_URL.format(page=page), timeout=DEFAULT_TIMEOUT)
    r.raise_for_status()
    return r.text


def build(http: requests.Session, out: Path = UNIVERSE_CSV) -> pd.DataFrame:
    frames = [parse_constituents(fetch_page(http, spec.page), spec) for spec in INDEX_SPECS]
    universe = merge(frames)
    out.parent.mkdir(parents=True, exist_ok=True)
    universe.to_csv(out, index=False)
    return universe


def load(path: Path = UNIVERSE_CSV) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)
