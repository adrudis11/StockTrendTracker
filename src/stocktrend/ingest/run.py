"""`stt ingest`: gather one run's prices and news, and report what worked."""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from stocktrend.config import load_yaml, raw_dir
from stocktrend.http import session
from stocktrend.ingest import federal_register, gdelt, prices, rss
from stocktrend.ingest.models import NewsItem, SourceResult, dedupe
from stocktrend.universe.build import load as load_universe
from stocktrend.universe.instruments import load_instruments

log = logging.getLogger(__name__)

SOURCES = ("prices", "rss", "federal_register", "gdelt_events", "gdelt_doc")


def default_lookback_hours(as_of: datetime) -> int:
    """24h, or 72h on Mondays so Friday evening and the weekend are covered."""
    return 72 if as_of.weekday() == 0 else 24


@dataclass
class IngestReport:
    as_of: str
    window_start: str
    out_dir: str
    sources: list[SourceResult] = field(default_factory=list)
    news_items: int = 0
    news_by_source: dict[str, int] = field(default_factory=dict)
    prices: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)  # anything that fails the run

    @property
    def ok(self) -> bool:
        return not self.problems

    def to_json(self) -> str:
        data = asdict(self)
        data["ok"] = self.ok
        return json.dumps(data, indent=2, default=str)


def _ingest_prices(as_of: datetime, report: IngestReport) -> None:
    universe = load_universe()
    cfg = load_instruments()
    tickers = list(universe["ticker"]) + [i.ticker for i in cfg.instruments]
    df = prices.download(tickers, as_of.date())
    check = prices.check(df, tickers, set(universe["ticker"]), as_of.date())
    prices.save(df)
    last_session = df["date"].max()
    report.prices = {
        "requested": check.requested,
        "rows": check.rows,
        "last_session": last_session.date().isoformat() if pd.notna(last_session) else None,
        "missing": check.missing,
        "stale": check.stale,
    }
    report.sources.append(
        SourceResult(
            "prices",
            items=check.requested - len(check.missing),
            warning=f"{len(check.stale)} stale stocks" if check.stale else None,
            error=None if check.ok else f"{len(check.missing)} tickers returned no data",
        )
    )


def run(as_of: datetime, lookback_hours: int, skip: frozenset[str] = frozenset()) -> IngestReport:
    start = as_of - timedelta(hours=lookback_hours)
    out = raw_dir(as_of.date().isoformat())
    out.mkdir(parents=True, exist_ok=True)
    report = IngestReport(as_of.isoformat(), start.isoformat(), str(out))
    http = session()
    news_cfg = load_yaml("news_sources.yaml")
    items: list[NewsItem] = []

    if "prices" not in skip:
        log.info("prices: downloading")
        try:
            _ingest_prices(as_of, report)
        except Exception as e:
            report.sources.append(SourceResult("prices", error=f"{type(e).__name__}: {e}"[:200]))

    # Official sources first: dedupe keeps the first copy of a story.
    if "federal_register" not in skip:
        log.info("federal_register: fetching")
        new, results = federal_register.fetch(http, news_cfg["federal_register"], start)
        items += new
        report.sources += results

    if "rss" not in skip:
        log.info("rss: fetching feeds and Google News topics")
        new, results = rss.fetch(http, news_cfg, start, as_of)
        items += new
        report.sources += results

    if "gdelt_events" not in skip and news_cfg["gdelt"]["events"]["enabled"]:
        log.info("gdelt_events: downloading 15-minute event files")
        result = SourceResult("gdelt_events")
        try:
            events, missing = gdelt.download_events(http, start, as_of)
            gdelt.aggregate(events).to_parquet(out / "gdelt_events_agg.parquet", index=False)
            new = gdelt.top_events(events, news_cfg["gdelt"]["events"]["top_n"])
            result.items = len(new)
            if missing:
                result.warning = f"{missing} 15-minute files missing"
            items += new
        except Exception as e:
            result.error = f"{type(e).__name__}: {e}"[:200]
        report.sources.append(result)

    if "gdelt_doc" not in skip and news_cfg["gdelt"]["doc_api"]["enabled"]:
        log.info("gdelt_doc: querying article API")
        new, results = gdelt.fetch_doc(
            http, news_cfg["topics"], news_cfg["gdelt"]["doc_api"]["max_records"], start, as_of
        )
        items += new
        report.sources += results

    news = dedupe(items)
    _write_news(news, out / "news.jsonl")
    report.news_items = len(news)
    report.news_by_source = dict(Counter(str(n.source) for n in news).most_common())
    _judge(report, skip)
    (out / "ingest_report.json").write_text(report.to_json(), encoding="utf-8")
    return report


def _write_news(news: list[NewsItem], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        for n in sorted(news, key=lambda n: n.published_at, reverse=True):
            f.write(n.model_dump_json() + "\n")


def _judge(report: IngestReport, skip: frozenset[str]) -> None:
    """Decide whether the run is usable. Single feeds may fail; whole sources may not."""
    by_group: dict[str, list[SourceResult]] = {}
    for s in report.sources:
        by_group.setdefault(s.name.split(":")[0].replace("google_news", "rss"), []).append(s)
    if "prices" not in skip and not all(s.ok for s in by_group.get("prices", [])):
        report.problems.append("prices failed")
    rss_feeds = [
        s for s in report.sources if s.name.startswith(("google_news", "bbc", "ec", "ecb"))
    ]
    if "rss" not in skip and rss_feeds and sum(not s.ok for s in rss_feeds) > len(rss_feeds) / 2:
        report.problems.append("more than half of the RSS feeds failed")
    for group in ("federal_register", "gdelt_events"):
        if group not in skip and group in by_group and not any(s.ok for s in by_group[group]):
            report.problems.append(f"{group} failed")
    news_sources = set(SOURCES) - {"prices"}
    if not news_sources <= skip and report.news_items == 0:
        report.problems.append("no news items at all")
