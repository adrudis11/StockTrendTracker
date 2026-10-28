import pandas as pd

from stocktrend.config import UNIVERSE_CSV
from stocktrend.markets import exchange_for
from stocktrend.universe.build import IndexSpec, load, merge, parse_constituents
from stocktrend.universe.instruments import load_instruments
from stocktrend.universe.sectors import GICS_SECTORS, UNKNOWN, to_gics

FTSE_HTML = """
<table><tr><th>Year</th><th>Closing level</th></tr>
<tr><td>2024</td><td>8000</td></tr><tr><td>2025</td><td>9000</td></tr></table>
<table>
<tr><th>Company</th><th>Ticker</th><th>ISIN[34]</th>
    <th>FTSE industry classification benchmark sector[35]</th></tr>
<tr><td>BAE Systems</td><td>BA</td><td>GB0002634946</td><td>Aerospace and Defense</td></tr>
<tr><td>BT Group</td><td>BT.A</td><td>GB0030913577</td><td>Telecommunications Service Providers</td></tr>
<tr><td>Shell</td><td>SHEL</td><td>GB00BP6MXD84</td><td>Non-Renewable Energy</td></tr>
</table>
"""

FTSE_SPEC = IndexSpec(
    "FTSE100",
    "FTSE_100_Index",
    "Europe",
    "Ticker",
    "Company",
    "FTSE industry classification benchmark sector",
    suffix=".L",
    min_rows=3,
)


def test_parse_constituents_picks_the_right_table() -> None:
    df = parse_constituents(FTSE_HTML, FTSE_SPEC)
    assert list(df["ticker"]) == ["BA.L", "BT-A.L", "SHEL.L"]
    assert list(df["sector"]) == ["Industrials", "Communication Services", "Energy"]
    assert set(df["exchange"]) == {"XLON"}
    assert set(df["currency"]) == {"GBp"}


def test_merge_keeps_first_row_and_all_memberships() -> None:
    a = parse_constituents(FTSE_HTML, FTSE_SPEC)
    b = a.head(1).assign(indices="EUROSTOXX50", sector="Unknown")
    merged = merge([a, b])
    bae = merged.set_index("ticker").loc["BA.L"]
    assert bae["indices"] == "FTSE100|EUROSTOXX50"
    assert bae["sector"] == "Industrials"  # first source wins
    assert len(merged) == 3


def test_sector_mapping() -> None:
    assert to_gics("Aerospace & Defence") == "Industrials"
    assert to_gics("  Banks[1] ") == "Financials"
    assert to_gics("Health care") == "Health Care"
    assert to_gics("Something new") == UNKNOWN
    assert to_gics(None) == UNKNOWN


def test_committed_universe_is_consistent() -> None:
    u = load(UNIVERSE_CSV)
    assert u["ticker"].is_unique
    assert set(u["sector"]) <= set(GICS_SECTORS), "unmapped sector labels; extend sectors.py"
    assert set(u["region"]) == {"US", "Europe"}
    assert (u["region"] == "US").sum() >= 490
    assert (u["region"] == "Europe").sum() >= 280
    for row in u.itertuples():
        assert exchange_for(str(row.ticker)).mic == row.exchange


def test_instruments_config() -> None:
    cfg = load_instruments()
    tickers = {i.ticker for i in cfg.instruments}
    assert set(cfg.benchmarks) == {"US", "Europe"}
    # every GICS sector has a sector ETF in both regions
    for region in ("US", "Europe"):
        sectors = {
            i.sector for i in cfg.instruments if i.kind == "sector_etf" and i.region == region
        }
        assert sectors == set(GICS_SECTORS), region
    assert not tickers & set(pd.read_csv(UNIVERSE_CSV)["ticker"])
