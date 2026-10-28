"""US Federal Register: presidential documents and market-moving agency actions."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, time
from typing import Any

import requests

from stocktrend.http import DEFAULT_TIMEOUT
from stocktrend.ingest.models import NewsItem, SourceResult, clean_text

API_URL = "https://www.federalregister.gov/api/v1/documents.json"
FIELDS = [
    "title",
    "abstract",
    "html_url",
    "publication_date",
    "type",
    "agencies",
    "document_number",
    "signing_date",
]
MAX_PAGES = 10


def parse_results(results: list[dict[str, Any]], feed_id: str) -> Iterator[NewsItem]:
    for doc in results:
        published = datetime.combine(
            datetime.fromisoformat(doc["publication_date"]).date(), time(0), tzinfo=UTC
        )
        agencies = [a.get("name") or a.get("raw_name") for a in doc.get("agencies") or []]
        yield NewsItem.make(
            url=doc["html_url"],
            source="federal_register",
            feed=feed_id,
            title=clean_text(doc["title"], limit=300) or doc["document_number"],
            published_at=published,
            # Presidential documents have no abstract; the signing date tells the
            # analyst how long markets have known (publication lags by days).
            summary=clean_text(doc.get("abstract"))
            or (f"Signed {doc['signing_date']}." if doc.get("signing_date") else None),
            publisher="Federal Register",
            language="en",
            country="US",
            extra={
                "official": True,
                "document_type": doc.get("type"),
                "document_number": doc.get("document_number"),
                "signing_date": doc.get("signing_date"),
                "agencies": [a for a in agencies if a],
            },
        )


def _query(http: requests.Session, params: list[tuple[str, str]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    url: str | None = API_URL
    for _ in range(MAX_PAGES):
        if url is None:
            break
        r = http.get(url, params=params if url == API_URL else None, timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("results") or [])
        url = data.get("next_page_url")
    return results


def fetch(
    http: requests.Session, cfg: dict[str, Any], start: datetime
) -> tuple[list[NewsItem], list[SourceResult]]:
    """Documents published on or after ``start``'s date (the API works in whole days)."""
    base = [
        ("conditions[publication_date][gte]", start.date().isoformat()),
        ("per_page", "100"),
        ("order", "newest"),
        *[("fields[]", f) for f in FIELDS],
    ]
    queries = {
        "federal_register:presidential": [*base, ("conditions[type][]", "PRESDOCU")],
        "federal_register:agencies": [
            *base,
            *[("conditions[type][]", t) for t in cfg["document_types"]],
            *[("conditions[agencies][]", a) for a in cfg["agencies"]],
        ],
    }
    items: list[NewsItem] = []
    results: list[SourceResult] = []
    for feed_id, params in queries.items():
        result = SourceResult(feed_id)
        try:
            new = list(parse_results(_query(http, params), feed_id))
            result.items = len(new)
            items.extend(new)
        except Exception as e:
            result.error = f"{type(e).__name__}: {e}"[:200]
        results.append(result)
    return items, results
