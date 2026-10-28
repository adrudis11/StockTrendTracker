"""GDELT 2.0: worldwide news events, updated every 15 minutes.

Two endpoints:

* **Event export files** (``data.gdeltproject.org``): one zipped TSV of
  machine-coded events every 15 minutes. Reliable and not rate-limited. We keep
  a per-country aggregate (for regime and spike features later) and turn the
  most-covered events into news items.
* **DOC article API** (``api.gdeltproject.org``): headline search. Allows one
  request per 5 seconds per IP and is often rate-limited from shared cloud IPs,
  so it is best effort: a 429 skips the remaining queries with a warning.
"""

from __future__ import annotations

import io
import math
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote, urlsplit

import pandas as pd
import requests

from stocktrend.ingest.models import NewsItem, SourceResult

DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
DOC_INTERVAL_S = 6  # GDELT asks for at most one request every 5 seconds
EXPORT_URL = "https://data.gdeltproject.org/gdeltv2/{stamp}.export.CSV.zip"
FILE_INTERVAL = timedelta(minutes=15)

# Positions in the 61-column GDELT 2.0 event export.
EVENT_COLUMNS = {
    0: "event_id",
    6: "actor1_name",
    7: "actor1_country",
    16: "actor2_name",
    17: "actor2_country",
    25: "is_root",
    26: "event_code",
    28: "root_code",
    29: "quad_class",
    30: "goldstein",
    31: "mentions",
    33: "articles",
    34: "tone",
    52: "location",
    53: "country",  # FIPS 10-4 code of where the action happened
    59: "date_added",
    60: "url",
}
NUMERIC = ["goldstein", "mentions", "articles", "tone"]

CAMEO_ROOTS = {
    "01": "Make public statement",
    "02": "Appeal",
    "03": "Express intent to cooperate",
    "04": "Consult",
    "05": "Diplomatic cooperation",
    "06": "Material cooperation",
    "07": "Provide aid",
    "08": "Yield",
    "09": "Investigate",
    "10": "Demand",
    "11": "Disapprove",
    "12": "Reject",
    "13": "Threaten",
    "14": "Protest",
    "15": "Exhibit force posture",
    "16": "Reduce relations",
    "17": "Coerce",
    "18": "Assault",
    "19": "Fight",
    "20": "Mass violence",
}
# Statements, appeals, consultations and investigations are mostly noise for markets.
NEWSWORTHY_ROOTS = set(CAMEO_ROOTS) - {"01", "02", "04", "09"}


# --- Event export files -----------------------------------------------------


def file_stamps(start: datetime, end: datetime) -> list[str]:
    """15-minute file timestamps covering ``[start, end]``."""
    t = start.replace(second=0, microsecond=0)
    t -= timedelta(minutes=t.minute % 15)
    if t < start:
        t += FILE_INTERVAL
    stamps = []
    while t <= end:
        stamps.append(t.strftime("%Y%m%d%H%M%S"))
        t += FILE_INTERVAL
    return stamps


def parse_export(content: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        raw = zf.read(zf.namelist()[0])
    df = pd.read_csv(
        io.BytesIO(raw),
        sep="\t",
        header=None,
        dtype=str,
        quoting=3,  # csv.QUOTE_NONE: fields are tab-separated and never quoted
        usecols=list(EVENT_COLUMNS),
        keep_default_na=False,
        encoding_errors="replace",
    ).rename(columns=EVENT_COLUMNS)
    for col in NUMERIC:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _download_export(http: requests.Session, stamp: str) -> pd.DataFrame | None:
    r = http.get(EXPORT_URL.format(stamp=stamp), timeout=60)
    if r.status_code == 404:  # GDELT occasionally skips a slot
        return None
    r.raise_for_status()
    return parse_export(r.content)


def download_events(
    http: requests.Session, start: datetime, end: datetime
) -> tuple[pd.DataFrame, int]:
    """All events added in the window, and how many 15-minute files were missing."""
    stamps = file_stamps(start, end)
    with ThreadPoolExecutor(max_workers=6) as pool:
        frames = list(pool.map(lambda s: _download_export(http, s), stamps))
    found = [f for f in frames if f is not None]
    missing = len(frames) - len(found)
    if not found:
        return pd.DataFrame(columns=list(EVENT_COLUMNS.values())), missing
    return pd.concat(found, ignore_index=True), missing


def aggregate(events: pd.DataFrame) -> pd.DataFrame:
    """Daily activity per country and event type: the raw material for spike features."""
    return (
        events.groupby(["country", "root_code", "quad_class"], as_index=False)
        .agg(
            events=("event_id", "count"),
            mentions=("mentions", "sum"),
            articles=("articles", "sum"),
            avg_tone=("tone", "mean"),
            avg_goldstein=("goldstein", "mean"),
        )
        .sort_values("articles", ascending=False, ignore_index=True)
    )


def _is_id_token(word: str) -> bool:
    """Numbers, hashes and UUID fragments (``86b5``, ``a1b2c3d4``), not words like ``F35``."""
    if word.isdigit():
        return True
    return len(word) >= 4 and bool(re.fullmatch(r"(?=.*\d)[0-9a-f]+", word, re.IGNORECASE))


def title_from_url(url: str) -> str | None:
    """Event files carry no headline; most news URLs embed one in the slug."""
    segments = [unquote(s) for s in urlsplit(url).path.split("/") if s]
    for seg in reversed(segments):
        seg = re.sub(r"\.(s?html?|php|aspx?|cms|ece)$", "", seg, flags=re.IGNORECASE)
        seg = re.sub(r"^\d+\.", "", seg)  # "26675864.us-embassy-..." style ids
        words = [w for w in re.split(r"[-_+\s]+", seg) if w and not _is_id_token(w)]
        if len(words) >= 4:
            text = " ".join(words)
            return text[0].upper() + text[1:]
    return None


def top_events(events: pd.DataFrame, top_n: int) -> list[NewsItem]:
    """The most-covered newsworthy international events, one per source URL.

    "International" (actors from two different countries) is a cheap proxy for
    geopolitics; without it the list fills up with local crime stories.
    Domestic politics is covered by the RSS topic searches instead.
    """
    international = (
        (events["actor1_country"] != "")
        & (events["actor2_country"] != "")
        & (events["actor1_country"] != events["actor2_country"])
    )
    df = events[
        international & (events["is_root"] == "1") & events["root_code"].isin(NEWSWORTHY_ROOTS)
    ]
    df = df.sort_values(["articles", "mentions"], ascending=False).drop_duplicates("url")
    items: list[NewsItem] = []
    for row in df.to_dict("records"):
        if len(items) >= top_n:
            break
        title = title_from_url(row["url"])
        if not title:
            continue
        items.append(
            NewsItem.make(
                url=row["url"],
                source="gdelt_events",
                feed="gdelt_events",
                title=title,
                published_at=datetime.strptime(row["date_added"], "%Y%m%d%H%M%S").replace(
                    tzinfo=UTC
                ),
                publisher=urlsplit(row["url"]).netloc.removeprefix("www."),
                country=row["country"] or None,
                extra={
                    "event_code": row["event_code"],
                    "event_type": CAMEO_ROOTS.get(row["root_code"], row["root_code"]),
                    "quad_class": row["quad_class"],
                    "actors": [a for a in (row["actor1_name"], row["actor2_name"]) if a],
                    "actor_countries": [
                        c for c in (row["actor1_country"], row["actor2_country"]) if c
                    ],
                    "location": row["location"] or None,
                    "goldstein": row["goldstein"],
                    "tone": round(float(row["tone"]), 2),
                    "articles": int(row["articles"]),
                    "mentions": int(row["mentions"]),
                },
            )
        )
    return items


# --- DOC article API --------------------------------------------------------


def parse_doc(data: dict[str, Any], topic: str) -> list[NewsItem]:
    items = []
    for a in data.get("articles") or []:
        if not a.get("url") or not a.get("title"):
            continue
        items.append(
            NewsItem.make(
                url=a["url"],
                source="gdelt_doc",
                feed=f"gdelt_doc:{topic}",
                title=a["title"].strip(),
                published_at=datetime.strptime(a["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC),
                publisher=a.get("domain"),
                language=a.get("language"),
                country=a.get("sourcecountry"),
                topics=[topic],
            )
        )
    return items


def fetch_doc(
    http: requests.Session,
    topics: dict[str, str],
    max_records: int,
    start: datetime,
    end: datetime,
) -> tuple[list[NewsItem], list[SourceResult]]:
    hours = max(1, math.ceil((end - start).total_seconds() / 3600))
    items: list[NewsItem] = []
    results: list[SourceResult] = []
    rate_limited = False
    for i, (topic, query) in enumerate(topics.items()):
        result = SourceResult(f"gdelt_doc:{topic}")
        results.append(result)
        if rate_limited:
            result.warning = "skipped: API rate-limited"
            continue
        if i:
            time.sleep(DOC_INTERVAL_S)
        params = {
            "query": f"{query} sourcelang:english",
            "mode": "artlist",
            "maxrecords": str(max_records),
            "timespan": f"{hours}h",
            "format": "json",
            "sort": "hybridrel",
        }
        try:
            r = http.get(DOC_URL, params=params, timeout=60)
            if r.status_code == 429:
                time.sleep(DOC_INTERVAL_S * 2)
                r = http.get(DOC_URL, params=params, timeout=60)
            if r.status_code == 429:
                rate_limited = True
                result.warning = "rate-limited (HTTP 429)"
                continue
            r.raise_for_status()
            if not r.content.strip():  # no matches
                continue
            try:
                data = r.json()
            except ValueError:
                raise ValueError(r.text[:150]) from None  # GDELT reports errors as text
            new = [n for n in parse_doc(data, topic) if start <= n.published_at <= end]
            result.items = len(new)
            items.extend(new)
        except Exception as e:
            result.error = f"{type(e).__name__}: {e}"[:200]
    return items, results
