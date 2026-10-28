"""Open-market stock purchases and sales by company insiders (SEC Form 4).

Officers, directors and 10% owners of US-listed companies must report trades in
their company's shares within two business days, on Form 4. Studies of insider
trading (e.g. Lakonishok & Lee 2001; Cohen, Malloy & Pomorski 2012) find that
open-market purchases carry information, more so when several insiders buy at once,
while sales mostly don't: insiders sell for taxes, diversification or on pre-set
10b5-1 plans. So the score counts purchases only; sales are kept for context.

Both sources are SEC EDGAR, free, with a contact email in the User-Agent
(``config.sec_user_agent``):

* history: the quarterly "Insider Transactions Data Sets" (every Form 3/4/5 as tables);
* recent days: the daily form index, then each S&P 500 company's Form 4 filing.

Only US companies in our universe (the S&P 500) and only open-market trades (codes P
and S) are kept, one row per filing and code, in ``data/trackers/insider_trades.jsonl``
(append-only). ``insider_state.json`` records which quarters and days have been read.
"""

from __future__ import annotations

import io
import json
import logging
import math
import re
import threading
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

from stocktrend.config import CACHE_DIR, DATA_DIR
from stocktrend.markets import to_yahoo

log = logging.getLogger(__name__)

DATASET_URLS = tuple(  # newer quarters moved to the first path
    f"https://www.sec.gov/files/{folder}/data/insider-transactions-data-sets/{{q}}_form345.zip"
    for folder in ("datastandardsinnovation", "structureddata")
)
DAILY_INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/form.{ymd}.idx"
ARCHIVES_URL = "https://www.sec.gov/Archives/"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
TRADES_PATH = DATA_DIR / "trackers" / "insider_trades.jsonl"
STATE_PATH = DATA_DIR / "trackers" / "insider_state.json"
TICKERS_CACHE = CACHE_DIR / "sec" / "company_tickers.json"
FIRST_QUARTER = "2021q4"  # backfill start: matches the 5 years of prices in `stt backtest`
REQUESTS_PER_SECOND = 8  # the SEC allows 10
FORMS = ("4", "4/A")

Code = Literal["P", "S"]


class Trade(BaseModel):
    accession: str
    filed: date  # the day the filing became public
    ticker: str
    owner: str  # the first reporting owner, "+N" when the filing names several
    role: str  # e.g. "Director", "President & CEO", "10% owner"
    code: Code  # P open-market purchase, S open-market sale
    tx_date: date  # the latest trade date in the filing
    shares: float
    value: float  # shares x price, US dollars
    plan: bool  # the filer ticked "made under a 10b5-1 trading plan"


# --- mapping companies to tickers --------------------------------------------------


def ticker_map(http: requests.Session, universe: Iterable[str]) -> dict[int, str]:
    """SEC company id (CIK) -> our ticker, for the given US tickers.

    For dual share classes (GOOGL/GOOG) the SEC lists the main class first; it wins.
    """
    fresh = TICKERS_CACHE.exists() and time.time() - TICKERS_CACHE.stat().st_mtime < 7 * 86400
    if not fresh:
        r = http.get(TICKERS_URL, timeout=60)
        r.raise_for_status()
        TICKERS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TICKERS_CACHE.write_bytes(r.content)
    wanted = set(universe)
    out: dict[int, str] = {}
    for row in json.loads(TICKERS_CACHE.read_text(encoding="utf-8")).values():
        ticker = to_yahoo(str(row["ticker"]))
        if ticker in wanted:
            out.setdefault(int(row["cik_str"]), ticker)
    return out


# --- parsing ---------------------------------------------------------------------


def _role(director: bool, officer: bool, ten_pct: bool, title: str) -> str:
    parts = []
    if officer:
        parts.append(title.strip() or "Officer")
    if director:
        parts.append("Director")
    if ten_pct:
        parts.append("10% owner")
    return ", ".join(parts)[:60] or "Other"


def _flag(text: str | None) -> bool:
    return (text or "").strip().lower() in ("1", "true")


def _aggregate(
    accession: str,
    filed: date,
    ticker: str,
    owners: list[tuple[str, str]],
    plan: bool,
    rows: Iterable[tuple[str, date, float, float]],
) -> list[Trade]:
    """One Trade per code from a filing's (code, date, shares, price) lines."""
    by_code: dict[str, dict[str, Any]] = {}
    for code, tx_date, shares, price in rows:
        if code not in ("P", "S") or not shares or math.isnan(shares):
            continue
        agg = by_code.setdefault(code, {"shares": 0.0, "value": 0.0, "tx_date": tx_date})
        agg["shares"] += shares
        agg["value"] += shares * (price if price and not math.isnan(price) else 0.0)
        agg["tx_date"] = max(agg["tx_date"], tx_date)
    if not by_code or not owners:
        return []
    name, role = owners[0]
    owner = name + (f" +{len(owners) - 1}" if len(owners) > 1 else "")
    trades = []
    for code, agg in sorted(by_code.items()):
        trades.append(
            Trade(
                accession=accession, filed=filed, ticker=ticker, owner=owner, role=role,
                code="P" if code == "P" else "S", tx_date=agg["tx_date"],
                shares=round(agg["shares"], 2), value=round(agg["value"], 2), plan=plan,
            )
        )  # fmt: skip
    return trades


def parse_dataset(content: bytes, ciks: dict[int, str]) -> list[Trade]:
    """Open-market trades in one quarterly "Insider Transactions Data Set" zip."""
    z = zipfile.ZipFile(io.BytesIO(content))

    def read(name: str, cols: list[str]) -> pd.DataFrame:
        df = pd.read_csv(z.open(name), sep="\t", dtype=str, keep_default_na=False,
                         usecols=lambda c: c in cols)  # fmt: skip
        return df.reindex(columns=cols, fill_value="")  # AFF10B5ONE only exists since 2023

    sub = read("SUBMISSION.tsv", ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE",
                                  "ISSUERCIK", "AFF10B5ONE"])  # fmt: skip
    sub = sub[sub["DOCUMENT_TYPE"].isin(FORMS)]
    sub = sub.assign(cik=pd.to_numeric(sub["ISSUERCIK"], errors="coerce"))
    sub = sub[sub["cik"].isin(list(ciks))].set_index("ACCESSION_NUMBER")
    tr = read("NONDERIV_TRANS.tsv", ["ACCESSION_NUMBER", "TRANS_DATE", "TRANS_CODE",
                                     "TRANS_SHARES", "TRANS_PRICEPERSHARE"])  # fmt: skip
    tr = tr[tr["TRANS_CODE"].isin(("P", "S")) & tr["ACCESSION_NUMBER"].isin(sub.index)]
    own = read("REPORTINGOWNER.tsv", ["ACCESSION_NUMBER", "RPTOWNERNAME",
                                      "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"])  # fmt: skip
    own = own[own["ACCESSION_NUMBER"].isin(set(tr["ACCESSION_NUMBER"]))]
    owners: dict[str, list[tuple[str, str]]] = {}
    for o in own.itertuples(index=False):
        rel = str(o.RPTOWNER_RELATIONSHIP)
        role = _role("Director" in rel, "Officer" in rel, "TenPercentOwner" in rel,
                     str(o.RPTOWNER_TITLE))  # fmt: skip
        owners.setdefault(str(o.ACCESSION_NUMBER), []).append((str(o.RPTOWNERNAME), role))
    dates = pd.to_datetime(tr["TRANS_DATE"], format="%d-%b-%Y", errors="coerce")
    tr = tr.assign(
        day=dates.dt.date,
        shares=pd.to_numeric(tr["TRANS_SHARES"], errors="coerce"),
        price=pd.to_numeric(tr["TRANS_PRICEPERSHARE"], errors="coerce"),
    ).dropna(subset=["day"])
    meta: dict[Any, dict[Any, Any]] = sub[~sub.index.duplicated()].to_dict("index")
    trades: list[Trade] = []
    for acc, g in tr.groupby("ACCESSION_NUMBER", sort=False):
        s = meta[acc]
        filed = datetime.strptime(str(s["FILING_DATE"]), "%d-%b-%Y").date()
        rows = zip(g["TRANS_CODE"], g["day"], g["shares"], g["price"], strict=True)
        ticker, plan = ciks[int(s["cik"])], _flag(str(s["AFF10B5ONE"]))
        trades += _aggregate(str(acc), filed, ticker, owners.get(str(acc), []), plan, rows)
    return trades


def _text(el: ET.Element | None, path: str) -> str:
    found = el.find(path) if el is not None else None
    return (found.text or "").strip() if found is not None and found.text else ""


def _num(text: str) -> float:
    try:
        return float(text)
    except ValueError:
        return math.nan


def parse_form4(raw: str, accession: str, filed: date, ciks: dict[int, str]) -> list[Trade]:
    """Open-market trades in one Form 4 (the full submission text, XML inside)."""
    m = re.search(r"<ownershipDocument>.*?</ownershipDocument>", raw, re.S)
    if not m:
        return []
    doc = ET.fromstring(m.group(0))
    if _text(doc, "documentType") not in FORMS:
        return []
    cik = _text(doc, "issuer/issuerCik")
    ticker = ciks.get(int(cik)) if cik.isdigit() else None
    if ticker is None:
        return []
    owners = []
    for o in doc.findall("reportingOwner"):
        rel = o.find("reportingOwnerRelationship")
        director, officer, ten_pct = (
            _flag(_text(rel, k)) for k in ("isDirector", "isOfficer", "isTenPercentOwner")
        )
        role = _role(director, officer, ten_pct, _text(rel, "officerTitle"))
        owners.append((_text(o, "reportingOwnerId/rptOwnerName"), role))
    rows = []
    for t in doc.findall("nonDerivativeTable/nonDerivativeTransaction"):
        try:
            day = date.fromisoformat(_text(t, "transactionDate/value")[:10])
        except ValueError:
            continue
        rows.append((
            _text(t, "transactionCoding/transactionCode"), day,
            _num(_text(t, "transactionAmounts/transactionShares/value")),
            _num(_text(t, "transactionAmounts/transactionPricePerShare/value")),
        ))  # fmt: skip
    return _aggregate(accession, filed, ticker, owners, _flag(_text(doc, "aff10b5One")), rows)


_INDEX_LINE = re.compile(r"^(4|4/A)\s+.+?\s+(\d+)\s+(\d{8})\s+(edgar/\S+)\s*$")


def parse_daily_index(text: str) -> list[tuple[int, str]]:
    """(filer CIK, path) of every Form 4 in a daily form index."""
    out = []
    for line in text.splitlines():
        m = _INDEX_LINE.match(line)
        if m:
            out.append((int(m.group(2)), m.group(4)))
    return out


# --- downloading -----------------------------------------------------------------


class _Pace:
    """Spaces requests across threads to stay under the SEC's rate limit."""

    def __init__(self, per_second: float) -> None:
        self.gap = 1.0 / per_second
        self.lock = threading.Lock()
        self.next = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next)
            self.next = at + self.gap
        time.sleep(max(0.0, at - now))


def quarters(first: str, last: str) -> list[str]:
    (y, q), (y1, q1) = (map(int, s.split("q")) for s in (first, last))
    out = []
    while (y, q) <= (y1, q1):
        out.append(f"{y}q{q}")
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def quarter_of(d: date) -> str:
    return f"{d.year}q{(d.month - 1) // 3 + 1}"


def quarter_end(q: str) -> date:
    y, n = map(int, q.split("q"))
    return (date(y + 1, 1, 1) if n == 4 else date(y, 3 * n + 1, 1)) - timedelta(days=1)


def _download_dataset(http: requests.Session, q: str) -> bytes | None:
    for url in DATASET_URLS:
        r = http.get(url.format(q=q), timeout=300)
        if r.status_code == 200 and r.content[:2] == b"PK":
            return r.content
    return None  # not published yet


def _load_state(path: Path) -> dict[str, list[str]]:
    if path.exists():
        state: dict[str, list[str]] = json.loads(path.read_text(encoding="utf-8"))
        return state
    return {"quarters": [], "days": []}


def _seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8") as f:
        return {json.loads(line)["accession"] for line in f if line.strip()}


def update(
    http: requests.Session,
    universe: Iterable[str],
    today: date | None = None,
    first_quarter: str = FIRST_QUARTER,
    workers: int = 4,
    trades_path: Path = TRADES_PATH,
    state_path: Path = STATE_PATH,
) -> dict[str, int]:
    """Append trades from filings we haven't read: quarterly sets, then daily indexes.

    The quarterly sets are read once, as a backfill; after that every business day up
    to yesterday is read from the daily index. A day whose index or filings fail is
    tried again next time (already-stored filings are skipped).
    """
    today = today or date.today()
    state = _load_state(state_path)
    ciks = ticker_map(http, universe)
    seen = _seen(trades_path)
    stats = {"quarters": 0, "days": 0, "filings": 0, "new_trades": 0, "errors": 0}
    new: list[Trade] = []

    def keep(trades: list[Trade]) -> None:
        for t in trades:
            if t.accession not in seen:
                new.append(t)
        seen.update(t.accession for t in trades)

    def save() -> None:
        """Write what's been read so far, so an interrupted backfill resumes."""
        trades_path.parent.mkdir(parents=True, exist_ok=True)
        with trades_path.open("a", encoding="utf-8") as out:
            for t in sorted(new, key=lambda t: (t.filed, t.accession, t.code)):
                out.write(t.model_dump_json() + "\n")
        stats["new_trades"] += len(new)
        new.clear()
        state_path.write_text(json.dumps(state, indent=0), encoding="utf-8")

    if not state["days"]:  # backfill from the quarterly sets
        for q in quarters(first_quarter, quarter_of(today)):
            if q in state["quarters"] or quarter_end(q) >= today:
                continue
            content = _download_dataset(http, q)
            if content is None:
                break
            keep(parse_dataset(content, ciks))
            state["quarters"].append(q)
            stats["quarters"] += 1
            save()
            log.info("insider data set %s: %d trades so far", q, stats["new_trades"])

    covered = [quarter_end(q) for q in state["quarters"]] + [
        date.fromisoformat(d) for d in state["days"]
    ]
    day = max(covered) + timedelta(days=1) if covered else today - timedelta(days=30)
    pace = _Pace(REQUESTS_PER_SECOND)
    while day < today:
        if day.weekday() < 5:
            ok = _read_day(http, day, today, ciks, seen, pace, workers, keep, stats)
            if not ok:
                break  # keep the days in order: retry from here next time
            state["days"].append(day.isoformat())
            stats["days"] += 1
            save()
        day += timedelta(days=1)
    save()
    return stats


def _read_day(
    http: requests.Session,
    day: date,
    today: date,
    ciks: dict[int, str],
    seen: set[str],
    pace: _Pace,
    workers: int,
    keep: Any,
    stats: dict[str, int],
) -> bool:
    """Read one day's Form 4s. False if the day can't be completed yet."""
    url = DAILY_INDEX_URL.format(y=day.year, q=(day.month - 1) // 3 + 1, ymd=day.strftime("%Y%m%d"))
    pace.wait()
    r = http.get(url, timeout=60)
    # No index (a holiday, or not published yet): EDGAR answers 404, or 403 with a storage
    # "AccessDenied" error. Its rate-limit block is a 403 web page instead.
    if r.status_code == 404 or (r.status_code == 403 and b"<Code>AccessDenied</Code>" in r.content):
        return day < today - timedelta(days=3)
    r.raise_for_status()
    paths: dict[str, str] = {}
    for cik, path in parse_daily_index(r.text):
        acc = path.rsplit("/", 1)[-1].removesuffix(".txt")
        if cik in ciks and acc not in seen:
            paths.setdefault(acc, path)  # a filing is listed under each company it names

    def fetch(item: tuple[str, str]) -> list[Trade] | None:
        acc, path = item
        for attempt in range(3):
            pace.wait()
            try:
                f = http.get(ARCHIVES_URL + path, timeout=60)
                if f.status_code == 200:
                    return parse_form4(f.text, acc, day, ciks)
                log.info("filing %s: HTTP %s", acc, f.status_code)
            except (requests.RequestException, ET.ParseError) as e:
                log.info("filing %s: %s", acc, e)
            time.sleep(2**attempt)
        return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(fetch, paths.items()))
    stats["filings"] += len(paths)
    failed = sum(r is None for r in results)
    stats["errors"] += failed
    for trades in results:
        if trades:
            keep(trades)
    return failed == 0


# --- the signal ------------------------------------------------------------------


def load_trades(path: Path = TRADES_PATH) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=list(Trade.model_fields))
    df = pd.read_json(path, lines=True, dtype={"ticker": str, "accession": str})
    for col in ("filed", "tx_date"):
        df[col] = pd.to_datetime(df[col]).dt.date
    # An amendment (4/A) repeats the original filing's trades.
    key = ["ticker", "owner", "code", "tx_date", "shares"]
    return df.drop_duplicates(key, keep="first").reset_index(drop=True)


def purchase_weight(value: float) -> float:
    """log10 of the dollars bought, in thousands: $10k counts 1, $1M counts 3."""
    return max(0.0, math.log10(value / 1_000)) if value > 0 else 0.0


def _money(x: float) -> str:
    return f"${x / 1e6:.1f}M" if x >= 1e6 else f"${x / 1e3:.0f}k"


def signal(
    trades: pd.DataFrame,
    as_of: date,
    known: set[str] | None = None,
    window_days: int = 30,
    max_trade_age_days: int = 60,
) -> pd.DataFrame:
    """Per-ticker insider buying from filings in the ``window_days`` before ``as_of``.

    ``score`` sums ``purchase_weight`` over open-market purchases, so several insiders
    or big purchases count more. Sales don't lower it (see the module docstring) but
    are counted. Only filings dated before ``as_of`` (public that morning) and trades
    from the last ``max_trade_age_days`` count. ``avg_price`` is what the buyers paid
    per share on average (dollar-weighted), ``last_buy`` their latest purchase date.
    """
    columns = ["ticker", "score", "buyers", "sellers", "bought", "sold", "avg_price",
               "last_buy", "insiders", "last_filed"]  # fmt: skip
    if trades.empty:
        return pd.DataFrame(columns=columns).set_index("ticker")
    recent = trades[
        (trades["filed"] < as_of)
        & (trades["filed"] >= as_of - timedelta(days=window_days))
        & (trades["tx_date"] >= as_of - timedelta(days=max_trade_age_days))
    ]
    if known is not None:
        recent = recent[recent["ticker"].isin(known)]
    rows = []
    for ticker, g in recent.groupby("ticker"):
        buys, sells = g[g["code"] == "P"], g[g["code"] == "S"]
        buyers = buys.groupby("owner")["role"].first()
        priced = buys[buys["value"] > 0]
        shares = float(priced["shares"].sum())
        rows.append(
            {
                "ticker": str(ticker),
                "score": round(sum(purchase_weight(v) for v in buys["value"]), 3),
                "buyers": len(buyers),
                "sellers": sells["owner"].nunique(),
                "bought": round(float(buys["value"].sum())),
                "sold": round(float(sells["value"].sum())),
                "avg_price": round(float(priced["value"].sum()) / shares, 2) if shares else None,
                "last_buy": max(buys["tx_date"]) if len(buys) else None,
                "insiders": "; ".join(f"{o} ({r})" for o, r in buyers.items()),
                "last_filed": max(g["filed"]),
            }
        )
    out = pd.DataFrame(rows, columns=columns).set_index("ticker")
    return out.sort_values(["score", "bought"], ascending=False)


def note(row: dict[Any, Any] | None) -> str | None:
    """Remark for a pick, e.g. "2 insiders bought $1.2M (filed in the last 30 days)"."""
    if not row or not row["buyers"]:
        return None
    n = int(row["buyers"])
    return (
        f"{n} insider{'s' if n > 1 else ''} bought {_money(float(row['bought']))} "
        "(Form 4s filed in the last 30 days)"
    )
