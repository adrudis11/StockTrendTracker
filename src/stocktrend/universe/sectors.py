"""Map the sector labels used by different index tables onto the 11 GICS sectors.

US members already come with GICS sectors. European index tables use GICS, ICB,
Deutsche Börse "Prime Standard" sectors or free text, so we map them explicitly.
"""

from __future__ import annotations

import re

GICS_SECTORS = (
    "Communication Services",
    "Consumer Discretionary",
    "Consumer Staples",
    "Energy",
    "Financials",
    "Health Care",
    "Industrials",
    "Information Technology",
    "Materials",
    "Real Estate",
    "Utilities",
)

UNKNOWN = "Unknown"

_C, _D, _S = "Communication Services", "Consumer Discretionary", "Consumer Staples"
_E, _F, _H = "Energy", "Financials", "Health Care"
_I, _T, _M = "Industrials", "Information Technology", "Materials"
_R, _U = "Real Estate", "Utilities"

_LABELS: dict[str, str] = {
    "aerospace": _I,
    "aerospace & defence": _I,
    "aerospace and defense": _I,
    "apparel": _D,
    "automobiles and parts": _D,
    "automotive": _D,
    "aviation": _I,
    "banks": _F,
    "basic materials": _M,
    "beverages": _S,
    "biotech": _H,
    "chemicals": _M,
    "closed end investments": _F,
    "clothes and cosmetics": _D,
    "communication": _C,
    "construction": _I,
    "construction materials": _M,
    "construction and materials": _M,
    "consumer cyclical": _D,
    "consumer defensive": _S,
    "consumer goods": _S,
    "consumer products and services": _D,
    "consumer services": _D,
    "distribution": _I,
    "e-commerce": _D,
    "electricity": _U,
    "electronic and electrical equipment": _I,
    "energy technology": _I,
    "finance and credit services": _F,
    "financial services": _F,
    "food producers": _S,
    "food, beverage and tobacco": _S,
    "gambling": _D,
    "gas, water and multi-utilities": _U,
    "general industrials": _I,
    "healthcare": _H,
    "household goods and home construction": _D,
    "industrial engineering": _I,
    "industrial goods and services": _I,
    "industrial metals and mining": _M,
    "industrial support services": _I,
    "infrastructure": _I,
    "insurance": _F,
    "investment banking and brokerage services": _F,
    "leisure goods": _D,
    "life insurance": _F,
    "logistics": _I,
    "manufacturing": _I,
    "mechanical engineering": _I,
    "media": _C,
    "medical equipment": _H,
    "medical equipment and services": _H,
    "non-life insurance": _F,
    "non-renewable energy": _E,
    "oil and gas": _E,
    "personal care, drug and grocery stores": _S,
    "personal goods": _D,
    "pharmaceuticals": _H,
    "pharmaceuticals, biotechnology and marijuana producers": _H,
    "precious metals and mining": _M,
    "real estate investment trusts": _R,
    "retail discretionary": _D,
    "retail staples": _S,
    "retailers": _D,
    "shipbuilding": _I,
    "software and computer services": _T,
    "solar energy": _I,
    "steel": _M,
    "technology": _T,
    "telecommunication": _C,
    "telecommunications": _C,
    "telecommunications service providers": _C,
    "textile": _D,
    "tobacco": _S,
    "tourism": _D,
    "travel and leisure": _D,
}
_LABELS.update({s.lower(): s for s in GICS_SECTORS})


def normalize_label(label: str) -> str:
    """Lowercase, drop citation marks like ``[35]`` and collapse whitespace."""
    label = re.sub(r"\[[^\]]*\]", "", label).replace("\xa0", " ")
    return re.sub(r"\s+", " ", label).strip().lower()


def to_gics(label: str | None) -> str:
    if not label:
        return UNKNOWN
    return _LABELS.get(normalize_label(label), UNKNOWN)
