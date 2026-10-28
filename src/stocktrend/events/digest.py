"""`stt digest`: boil a day's ~1,500 news items down to ~150 stories for Claude to read.

Items about the same story (similar headlines) are grouped, and stories are ranked
by how many independent publishers carry them, with a bonus for official sources.
The digest JSON is committed (``data/digests/<date>.json``) so every event Claude
writes can be traced back to its sources. A compact Markdown version is printed
or written to ``cache/`` for reading.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from stocktrend.config import DATA_DIR, raw_dir
from stocktrend.ingest.models import NewsItem

MAX_STORIES = 150
PER_TOPIC_MIN = 6  # every topic gets at least its top stories in
MAX_OFFICIAL = 25
SIMILARITY = 0.35  # Jaccard similarity of headline words to count as the same story
# Ceremonial proclamations ("National Seafood Month, 2026") carry no market news.
_CEREMONIAL = re.compile(r"\b(Month|Week|Day|Anniversary)(,| of)? .*\d{4}$|\bDay, \d{4}$")

# Bonus for publishers with strong original reporting. Coverage still matters more.
TIER1 = {
    "reuters",
    "bloomberg",
    "financial times",
    "the wall street journal",
    "wsj",
    "associated press",
    "ap news",
    "bbc",
    "the new york times",
    "the washington post",
    "politico",
    "politico europe",
    "nikkei asia",
    "the economist",
    "cnbc",
    "axios",
    "the guardian",
    "al jazeera",
    "defense news",
    "breaking defense",
    "s&p global",
    "argus media",
    "lloyd's list",
}

_STOPWORDS = set(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "from",
        "by",
        "with",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "after",
        "before",
        "over",
        "under",
        "into",
        "about",
        "amid",
        "against",
        "says",
        "say",
        "said",
        "new",
        "will",
        "would",
        "could",
        "may",
        "might",
        "can",
        "than",
        "then",
        "up",
        "down",
        "out",
        "off",
        "more",
        "most",
        "us",
        "uk",
        "eu",
        "his",
        "her",
        "their",
        "our",
        "your",
        "who",
        "what",
        "when",
        "where",
        "why",
        "how",
        "not",
        "no",
        "yes",
        "all",
        "any",
    ]
)


def digest_path(day: str) -> Path:
    return DATA_DIR / "digests" / f"{day}.json"


class Story(BaseModel):
    id: str
    title: str
    publishers: list[str]
    n_publishers: int
    topics: list[str]
    official: bool
    first_seen: datetime
    score: float
    summary: str | None = None
    urls: list[str]
    gdelt: dict[str, Any] | None = None


class Digest(BaseModel):
    date: str
    generated_at: datetime
    window_start: str
    n_items: int
    n_stories_total: int
    stories: list[Story]
    market: dict[str, Any] = {}


@dataclass
class _Cluster:
    items: list[NewsItem]
    tokens: set[str]
    publishers: set[str] = field(default_factory=set)


def tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", title.lower())
    return {w for w in words if len(w) >= 3 and w not in _STOPWORDS}


def _publisher(item: NewsItem) -> str:
    if item.publisher:
        return item.publisher.strip()
    return item.feed.split(":")[0].split("_")[0].upper()  # "bbc_world" -> "BBC"


def _is_official(item: NewsItem) -> bool:
    return bool(item.extra.get("official"))


def cluster(items: list[NewsItem]) -> list[_Cluster]:
    """Greedy single-pass clustering on headline words.

    Official items never merge: each executive order or ECB release stays its own story.
    """
    clusters: list[_Cluster] = []
    index: dict[str, set[int]] = defaultdict(set)  # word -> clusters containing it
    for item in items:
        toks = tokens(item.title)
        best, best_sim = None, 0.0
        if not _is_official(item) and len(toks) >= 3:
            candidates: set[int] = set().union(*(index[t] for t in toks)) if toks else set()
            for ci in candidates:
                c = clusters[ci]
                if _is_official(c.items[0]):
                    continue
                shared = len(toks & c.tokens)
                sim = shared / len(toks | c.tokens)
                if shared >= 3 and sim > best_sim:
                    best, best_sim = ci, sim
        if best is not None and best_sim >= SIMILARITY:
            c = clusters[best]
            c.items.append(item)
            c.publishers.add(_publisher(item))
            for t in toks:
                index[t].add(best)
            continue
        clusters.append(_Cluster([item], toks, {_publisher(item)}))
        for t in toks:
            index[t].add(len(clusters) - 1)
    return clusters


def score(c: _Cluster, now: datetime) -> float:
    lead = c.items[0]
    s = 2.0 * math.log2(1 + len(c.publishers))
    feeds = {i.feed.rsplit(":", 1)[0] for i in c.items}
    for i in c.items:
        feeds.update(f.rsplit(":", 1)[0] for f in i.extra.get("also_in", []))
    s += 0.3 * (len(feeds) - 1)  # picked up by several topic searches
    if _is_official(lead):
        presidential = lead.feed.endswith("presidential") or lead.feed.startswith(("ecb", "ec_"))
        s += 3.0 if presidential else 1.5
    if any(p.lower() in TIER1 for p in c.publishers):
        s += 1.0
    articles = max((int(i.extra.get("articles", 0)) for i in c.items), default=0)
    if articles:
        s += math.log10(1 + articles)
    newest = max(i.published_at for i in c.items)
    if (now - newest).total_seconds() < 12 * 3600:
        s += 0.5
    return round(s, 2)


def _best_item(c: _Cluster) -> NewsItem:
    """Headline from the most reputable publisher, else the earliest report."""
    tier1 = [i for i in c.items if _publisher(i).lower() in TIER1]
    return min(tier1 or c.items, key=lambda i: i.published_at)


def _to_story(c: _Cluster, s: float) -> Story:
    lead = _best_item(c)
    topics = list(dict.fromkeys(t for i in c.items for t in i.topics))
    urls = list(dict.fromkeys([lead.url] + [i.url for i in c.items]))[:3]
    summary = next((i.summary for i in [lead, *c.items] if i.summary), None)
    gdelt_keys: tuple[str, ...] = ("event_type", "actor_countries", "articles")
    gdelt = next(
        (
            {k: i.extra[k] for k in gdelt_keys if k in i.extra}
            for i in c.items
            if i.source == "gdelt_events"
        ),
        None,
    )
    return Story(
        id="",
        title=lead.title,
        publishers=sorted(c.publishers)[:6],
        n_publishers=len(c.publishers),
        topics=topics,
        official=_is_official(lead),
        first_seen=min(i.published_at for i in c.items),
        score=s,
        summary=summary,
        urls=urls,
        gdelt=gdelt,
    )


def select(stories: list[Story], max_stories: int = MAX_STORIES) -> list[Story]:
    """Top stories overall, but with a floor per topic and a cap on official notices."""
    ranked = sorted(stories, key=lambda s: s.score, reverse=True)
    chosen: dict[int, Story] = {}
    official = [s for s in ranked if s.official][:MAX_OFFICIAL]
    for s in official:
        chosen[id(s)] = s
    topics = {t for s in ranked for t in s.topics}
    for topic in sorted(topics):
        for s in [s for s in ranked if topic in s.topics and not s.official][:PER_TOPIC_MIN]:
            chosen[id(s)] = s
    for s in ranked:
        if len(chosen) >= max_stories:
            break
        if not s.official:
            chosen[id(s)] = s
    result = sorted(chosen.values(), key=lambda s: s.score, reverse=True)[:max_stories]
    for n, s in enumerate(result, 1):
        s.id = f"S{n:03d}"
    return result


def load_items(day: str) -> list[NewsItem]:
    path = raw_dir(day) / "news.jsonl"
    with path.open(encoding="utf-8") as f:
        return [NewsItem.model_validate_json(line) for line in f if line.strip()]


def build(day: str, now: datetime, market: dict[str, Any] | None = None) -> Digest:
    items = [
        i
        for i in load_items(day)
        if not (i.source == "federal_register" and _CEREMONIAL.search(i.title))
    ]
    report = json.loads((raw_dir(day) / "ingest_report.json").read_text(encoding="utf-8"))
    # Official and tier-1 items first, so they lead their clusters.
    items.sort(key=lambda i: (not _is_official(i), _publisher(i).lower() not in TIER1))
    clusters = cluster(items)
    stories = [_to_story(c, score(c, now)) for c in clusters]
    return Digest(
        date=day,
        generated_at=now,
        window_start=report["window_start"],
        n_items=len(items),
        n_stories_total=len(stories),
        stories=select(stories),
        market=market or {},
    )


def save(digest: Digest) -> Path:
    path = digest_path(digest.date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(digest.model_dump_json(exclude_none=True), encoding="utf-8")
    return path


def load(day: str) -> Digest:
    return Digest.model_validate_json(digest_path(day).read_text(encoding="utf-8"))


def to_markdown(d: Digest) -> str:
    """Compact rendering for Claude: one line per story, market snapshot first."""
    lines = [
        f"# News digest {d.date}",
        "",
        f"{len(d.stories)} of {d.n_stories_total} stories ({d.n_items} items) since "
        f"{d.window_start[:16]} UTC. Cite stories by id.",
        "",
    ]
    if d.market:
        lines += [market_markdown(d.market), ""]
    lines += ["## Stories", "", "id | pubs | topics | headline — summary", ""]
    for s in d.stories:
        tag = "OFFICIAL " if s.official else ""
        pubs = f"{s.n_publishers}: {', '.join(s.publishers[:3])}"
        gd = ""
        if s.gdelt:
            actors = "/".join(s.gdelt.get("actor_countries", []))
            gd = f" [GDELT {s.gdelt.get('event_type')} {actors}]"
        summary = f" — {s.summary[:220]}" if s.summary else ""
        topics = ",".join(s.topics) or "-"
        lines.append(f"{s.id} | {pubs} | {topics} | {tag}{s.title}{gd}{summary}")
    return "\n".join(lines) + "\n"


def _num(x: float) -> str:
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:.4g}"


def market_markdown(m: dict[str, Any]) -> str:
    out = [f"## Market snapshot (closes up to {m.get('last_session')})", ""]
    out.append("instrument | last | 1d % | 5d % | 20d %")
    for row in m.get("instruments", []):
        out.append(
            f"{row['ticker']} {row['name']} | {_num(row['last'])} | {row['r1d']:+.1f} | "
            f"{row['r5d']:+.1f} | {row['r20d']:+.1f}"
        )
    for key, label in (("leaders_5d", "Top 5-day movers vs benchmark"), ("laggards_5d", "Bottom")):
        if m.get(key):
            out += ["", f"{label}: " + ", ".join(f"{t} {r:+.1f}%" for t, r in m[key])]
    if m.get("prediction_markets"):
        out += ["", prediction_markdown(m["prediction_markets"])]
    return "\n".join(out)


def prediction_markdown(events: list[dict[str, Any]]) -> str:
    """Polymarket odds: probability of the listed outcome and its one-day change."""
    out = [
        "## Prediction markets (Polymarket, by 24h volume)",
        "",
        "question | odds | 1-day change | 24h volume",
    ]
    for e in events:
        for mk in e["markets"]:
            chg = mk.get("change_1d")
            chg_txt = f"{100 * chg:+.0f} pts" if isinstance(chg, int | float) else "-"
            out.append(
                f"{mk['question']} | {100 * mk['probability']:.0f}% {mk['outcome']} | "
                f"{chg_txt} | ${mk['volume_24h']:,}"
            )
    return "\n".join(out)
