"""RSS feeds: Google News topic searches plus fixed news and official feeds."""

from __future__ import annotations

import calendar
import math
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import feedparser
import requests

from stocktrend.http import DEFAULT_TIMEOUT
from stocktrend.ingest.models import NewsItem, SourceResult, clean_text

GOOGLE_NEWS_URL = "https://news.google.com/rss/search?"


def google_news_url(query: str, edition: dict[str, str], lookback_hours: int) -> str:
    days = max(1, math.ceil(lookback_hours / 24))
    params = {
        "q": f"{query} when:{days}d",
        "hl": edition["hl"],
        "gl": edition["gl"],
        "ceid": edition["ceid"],
    }
    return GOOGLE_NEWS_URL + urlencode(params)


def _published(entry: Any) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)


def parse_feed(
    content: bytes,
    *,
    feed_id: str,
    start: datetime,
    end: datetime,
    topics: list[str] | None = None,
    official: bool = False,
) -> Iterator[NewsItem]:
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"unparseable feed: {parsed.get('bozo_exception')}")
    for entry in parsed.entries:
        published = _published(entry)
        link = entry.get("link")
        title = clean_text(entry.get("title"), limit=300)
        if not (published and link and title) or published > end:
            continue
        # Official feeds often stamp releases at midnight, so match them by day.
        if (published.date() < start.date()) if official else (published < start):
            continue
        publisher = None
        source = entry.get("source")
        if source and source.get("title"):
            # Google News titles end in " - Publisher"; keep the headline only.
            publisher = source["title"]
            title = title.removesuffix(f" - {publisher}")
        summary = clean_text(entry.get("summary"))
        if summary and (summary.startswith(title) or (publisher and summary == publisher)):
            summary = None  # Google News summaries just repeat the headline
        yield NewsItem.make(
            url=link,
            source="rss",
            feed=feed_id,
            title=title,
            published_at=published,
            summary=summary,
            publisher=publisher,
            language="en",
            topics=list(topics or []),
            extra={"official": True} if official else {},
        )


def fetch(
    http: requests.Session, cfg: dict[str, Any], start: datetime, end: datetime
) -> tuple[list[NewsItem], list[SourceResult]]:
    lookback_hours = math.ceil((end - start).total_seconds() / 3600)
    jobs: list[tuple[str, str, list[str], bool]] = []
    for feed in cfg["feeds"]:
        jobs.append((feed["id"], feed["url"], [], feed.get("kind") == "official"))
    for topic, query in cfg["topics"].items():
        for edition in cfg["google_news"]["editions"]:
            url = google_news_url(query, edition, lookback_hours)
            jobs.append((f"google_news:{topic}:{edition['id']}", url, [topic], False))

    items: list[NewsItem] = []
    results: list[SourceResult] = []
    for feed_id, url, topics, official in jobs:
        result = SourceResult(feed_id)
        try:
            r = http.get(url, timeout=DEFAULT_TIMEOUT)
            r.raise_for_status()
            new = list(
                parse_feed(
                    r.content,
                    feed_id=feed_id,
                    start=start,
                    end=end,
                    topics=topics,
                    official=official,
                )
            )
            result.items = len(new)
            items.extend(new)
        except Exception as e:  # one broken feed must not stop the others
            result.error = f"{type(e).__name__}: {e}"[:200]
        results.append(result)
        if feed_id.startswith("google_news"):
            time.sleep(1)
    return items, results
