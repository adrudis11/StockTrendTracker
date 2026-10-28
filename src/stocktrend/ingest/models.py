"""Common shape for every ingested news item, plus de-duplication."""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field

Source = Literal["rss", "federal_register", "gdelt_doc", "gdelt_events"]

SUMMARY_MAX_CHARS = 500
_TRACKING_PARAMS = re.compile(r"^(utm_|fbclid$|gclid$|ocid$|cmpid$|ref$|smid$)")


class NewsItem(BaseModel):
    id: str
    source: Source
    feed: str
    title: str
    url: str
    published_at: datetime  # always UTC
    summary: str | None = None
    publisher: str | None = None
    language: str | None = None
    country: str | None = None
    topics: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def make(cls, *, url: str, **fields: Any) -> NewsItem:
        url = canonical_url(url)
        return cls(id=hashlib.sha1(url.encode()).hexdigest()[:16], url=url, **fields)


@dataclass
class SourceResult:
    """Outcome of one source, for the ingest health report."""

    name: str
    items: int = 0
    error: str | None = None
    warning: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def canonical_url(url: str) -> str:
    """Drop fragments and tracking parameters so the same article dedupes."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if not _TRACKING_PARAMS.match(k)]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def clean_text(text: str | None, limit: int = SUMMARY_MAX_CHARS) -> str | None:
    """Strip HTML tags and entities, collapse whitespace, truncate."""
    if not text:
        return None
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text or None


def title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def dedupe(items: list[NewsItem]) -> list[NewsItem]:
    """Merge items with the same URL or the same title; the first one seen wins.

    Topics are unioned, and the feeds of merged duplicates are kept in
    ``extra["also_in"]``: an event reported by many feeds is a stronger signal.
    """
    by_key: dict[str, NewsItem] = {}
    kept: list[NewsItem] = []
    for item in items:
        keys = [item.id]
        tkey = title_key(item.title)
        if len(tkey) >= 25:  # short titles ("Live updates") collide by accident
            keys.append(f"t:{tkey}")
        existing = next((by_key[k] for k in keys if k in by_key), None)
        if existing is None:
            kept.append(item)
            for k in keys:
                by_key[k] = item
            continue
        existing.topics = list(dict.fromkeys(existing.topics + item.topics))
        also = existing.extra.setdefault("also_in", [])
        if item.feed != existing.feed and item.feed not in also:
            also.append(item.feed)
        for k in keys:
            by_key.setdefault(k, existing)
    return kept
