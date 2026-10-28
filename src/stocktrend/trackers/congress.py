"""Stock trades disclosed by members of the US House of Representatives.

Members must report trades over $1,000 within 45 days (STOCK Act) in Periodic
Transaction Reports (PTRs), published by the Clerk of the House:

* a yearly index, ``{year}FD.zip``, lists every filing; ``FilingType`` "P" is a PTR;
* each PTR is a PDF. Electronic filings have extractable text; paper filings are
  scans and are marked unreadable.

Parsed trades are kept in ``data/trackers/congress_trades.jsonl`` and every filing
we've looked at in ``data/trackers/house_filings.json``, so each run only downloads
new filings. This is official public data, the same source the "Pelosi tracker"
accounts use.

What counts as a signal: open-market purchases. Dividend reinvestments are dropped
(they're automatic), and sales count for less than purchases because people sell for
many reasons (taxes, diversification, spending).
"""

from __future__ import annotations

import io
import json
import logging
import math
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import requests
from pydantic import BaseModel
from pypdf import PdfReader

from stocktrend.config import DATA_DIR
from stocktrend.markets import to_yahoo

log = logging.getLogger(__name__)

INDEX_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
PTR_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"
TRADES_PATH = DATA_DIR / "trackers" / "congress_trades.jsonl"
FILINGS_PATH = DATA_DIR / "trackers" / "house_filings.json"

TradeType = Literal["purchase", "sale", "partial_sale", "exchange"]
TYPES: dict[str, TradeType] = {
    "P": "purchase",
    "S": "sale",
    "S (partial)": "partial_sale",
    "E": "exchange",
}
ASSET_TYPES = {"ST", "OP", "EF"}  # stocks, options on stocks, ETFs

_TRADE = re.compile(
    r"(?P<pre>.{0,240}?)"
    r"\((?P<ticker>[A-Z][A-Z0-9.\-]{0,9})\)\s*\[(?P<atype>[A-Z]{2})\]\s*"
    r"(?P<type>S \(partial\)|P|S|E)\s+"
    r"(?P<tx>\d{2}/\d{2}/\d{4})\s+(?P<notified>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<amount>Over \$[\d,]+|\$[\d,]+(?:\s*-\s*\$[\d,]+)?)"
)
_OWNER = re.compile(r"(?:^|\s)(SP|JT|DC)\s+(?=[A-Z0-9])")
_NOISE = re.compile(
    r".*\$200\?|F\s+S\s*:\s*(?:New|Amended)|S\s+O\s*:\s*[^:]*?(?=\s[A-Z][a-z]|\s*$)|D\s+:\s*[^.]*\.?"
)


class Trade(BaseModel):
    chamber: Literal["house"] = "house"
    member: str
    state_district: str
    doc_id: str
    filed: date  # when the report was filed: the day the trade became public
    owner: str | None  # SP spouse, JT joint, DC dependent child, None = the member
    asset: str
    ticker: str  # as written in the filing
    asset_type: str
    type: TradeType
    tx_date: date
    amount_low: int
    amount_high: int | None
    reinvestment: bool = False


def amount_range(text: str) -> tuple[int, int | None]:
    nums = [int(n.replace(",", "")) for n in re.findall(r"\$([\d,]+)", text)]
    if text.startswith("Over"):
        return nums[0], None
    return nums[0], nums[1] if len(nums) > 1 else None


def amount_mid(low: int, high: int | None) -> float:
    return (low + high) / 2 if high else low * 1.5


def _clean(text: str) -> str:
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    return re.sub(r"\s+", " ", text).strip()


def parse_ptr_text(text: str) -> list[dict[str, Any]]:
    """Transactions with a ticker in one PTR's extracted text."""
    text = _clean(text)
    matches = list(_TRADE.finditer(text))
    out = []
    for i, m in enumerate(matches):
        if m.group("atype") not in ASSET_TYPES:
            continue
        # The description ("D : Automatic reinvestment of dividends...") follows the
        # transaction, so it sits in the text before the next match's ticker.
        after = (
            matches[i + 1].group("pre") if i + 1 < len(matches) else text[m.end() : m.end() + 300]
        )
        pre = m.group("pre")
        owners = list(_OWNER.finditer(pre))
        owner = owners[-1].group(1) if owners and owners[-1].end() > len(pre) - 160 else None
        asset = _NOISE.sub(" ", pre[owners[-1].end() :] if owner and owners else pre)
        asset = re.sub(r"\s+", " ", asset).strip(" -,")[-120:]
        low, high = amount_range(m.group("amount"))
        out.append(
            {
                "owner": owner,
                "asset": asset,
                "ticker": m.group("ticker"),
                "asset_type": m.group("atype"),
                "type": TYPES[m.group("type")],
                "tx_date": datetime.strptime(m.group("tx"), "%m/%d/%Y").date(),
                "amount_low": low,
                "amount_high": high,
                "reinvestment": bool(re.search(r"reinvest", after, re.IGNORECASE)),
            }
        )
    return out


def parse_index(content: bytes, year: int) -> list[dict[str, str]]:
    """PTR filings listed in a yearly ``{year}FD.zip``."""
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        root = ET.fromstring(zf.read(f"{year}FD.xml").decode("utf-8-sig"))
    rows = []
    for m in root:
        if (m.findtext("FilingType") or "") != "P":
            continue
        first = (m.findtext("First") or "").strip()
        last = (m.findtext("Last") or "").strip()
        rows.append(
            {
                "doc_id": (m.findtext("DocID") or "").strip(),
                "member": f"{first} {last}".strip(),
                "state_district": (m.findtext("StateDst") or "").strip(),
                "filed": datetime.strptime(m.findtext("FilingDate") or "", "%m/%d/%Y")
                .date()
                .isoformat(),
                "year": str(year),
            }
        )
    return rows


def _load_filings(path: Path = FILINGS_PATH) -> dict[str, dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _fetch_ptr(http: requests.Session, filing: dict[str, str]) -> tuple[str, list[dict[str, Any]]]:
    """(status, trades) for one filing: parsed, no_stock_trades, unreadable or error."""
    url = PTR_URL.format(year=filing["year"], doc_id=filing["doc_id"])
    try:
        r = http.get(url, timeout=60)
        if r.status_code == 404:
            return "missing", []
        r.raise_for_status()
        text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(r.content)).pages)
    except Exception as e:
        log.info("PTR %s failed: %s", filing["doc_id"], e)
        return "error", []
    finally:
        time.sleep(0.5)  # be polite to the Clerk's server
    if len(_clean(text)) < 200:
        return "unreadable", []  # a scanned paper filing
    trades = parse_ptr_text(text)
    return ("parsed" if trades else "no_stock_trades"), trades


def update(
    http: requests.Session,
    years: Iterable[int],
    workers: int = 3,
    trades_path: Path = TRADES_PATH,
    filings_path: Path = FILINGS_PATH,
) -> dict[str, int]:
    """Download filings we haven't seen (and retry earlier errors); append their trades."""
    seen = _load_filings(filings_path)
    todo: list[dict[str, str]] = []
    for year in years:
        r = http.get(INDEX_URL.format(year=year), timeout=60)
        r.raise_for_status()
        for f in parse_index(r.content, year):
            if seen.get(f["doc_id"], {}).get("status") not in (None, "error", "missing"):
                continue
            todo.append(f)
    stats = {"new_filings": len(todo), "new_trades": 0}
    if not todo:
        return stats
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda f: (f, *_fetch_ptr(http, f)), todo))
    trades_path.parent.mkdir(parents=True, exist_ok=True)
    with trades_path.open("a", encoding="utf-8") as out:
        for f, status, trades in results:
            seen[f["doc_id"]] = {k: f[k] for k in ("member", "filed", "year")} | {
                "status": status,
                "trades": len(trades),
            }
            stats[status] = stats.get(status, 0) + 1
            for t in trades:
                trade = Trade(
                    member=f["member"],
                    state_district=f["state_district"],
                    doc_id=f["doc_id"],
                    filed=date.fromisoformat(f["filed"]),
                    **t,
                )
                out.write(trade.model_dump_json() + "\n")
                stats["new_trades"] += 1
    filings_path.write_text(json.dumps(seen, indent=0, sort_keys=True), encoding="utf-8")
    return stats


def load_trades(path: Path = TRADES_PATH) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=list(Trade.model_fields))
    df = pd.read_json(path, lines=True, dtype={"ticker": str, "doc_id": str})
    for col in ("filed", "tx_date"):
        df[col] = pd.to_datetime(df[col]).dt.date
    # Amended filings repeat earlier trades.
    key = ["member", "ticker", "type", "tx_date", "amount_low", "owner"]
    return df.drop_duplicates(key, keep="first").reset_index(drop=True)


def trade_weight(t: dict[Any, Any]) -> float:
    """Signed weight: bigger trades count more (log of size); sales count half."""
    size = max(0.0, math.log10(amount_mid(t["amount_low"], t["amount_high"]) / 1_000))
    return {"purchase": 1.0, "sale": -0.5, "partial_sale": -0.25, "exchange": 0.0}[t["type"]] * size


def signal(
    trades: pd.DataFrame,
    as_of: date,
    known: set[str] | None = None,
    window_days: int = 30,
    max_trade_age_days: int = 60,
) -> pd.DataFrame:
    """Per-ticker score from trades disclosed in the last ``window_days`` before ``as_of``.

    Only filings dated before ``as_of`` count (what was public that morning), and only
    trades made in the last ``max_trade_age_days``: older news is stale by the time
    it's disclosed. Tickers are mapped to Yahoo format; with ``known``, others drop.
    """
    columns = ["ticker", "score", "buyers", "sellers", "trades", "members", "last_filed"]
    if trades.empty:
        return pd.DataFrame(columns=columns).set_index("ticker")
    recent = trades[
        (trades["filed"] < as_of)
        & (trades["filed"] >= as_of - timedelta(days=window_days))
        & (trades["tx_date"] >= as_of - timedelta(days=max_trade_age_days))
        & ~trades["reinvestment"].astype(bool)
    ]
    rows: dict[str, dict[str, Any]] = {}
    for t in recent.to_dict("records"):
        ticker = to_yahoo(str(t["ticker"]))
        if known is not None and ticker not in known:
            continue
        r = rows.setdefault(
            ticker,
            {
                "score": 0.0,
                "buyers": set(),
                "sellers": set(),
                "trades": 0,
                "last_filed": t["filed"],
            },
        )
        r["score"] += trade_weight(t)
        r["trades"] += 1
        r["last_filed"] = max(r["last_filed"], t["filed"])
        if t["type"] == "purchase":
            r["buyers"].add(t["member"])
        elif t["type"] in ("sale", "partial_sale"):
            r["sellers"].add(t["member"])
    out = pd.DataFrame(
        [
            {
                "ticker": k,
                "score": round(v["score"], 3),
                "buyers": len(v["buyers"]),
                "sellers": len(v["sellers"]),
                "trades": v["trades"],
                "members": ", ".join(sorted(v["buyers"] | v["sellers"])),
                "last_filed": v["last_filed"],
            }
            for k, v in rows.items()
        ],
        columns=columns,
    )
    return out.set_index("ticker").sort_values("score", ascending=False)
