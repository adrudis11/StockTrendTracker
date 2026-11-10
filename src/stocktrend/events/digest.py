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
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from stocktrend.config import DATA_DIR, raw_dir
from stocktrend.ingest.models import NewsItem

MAX_STORIES = 150
WEEK_STORIES = 250  # the week's digest for the Saturday analysis (since 2026-11-14)
DAYS_BONUS = 1.0  # a week's story gains this per doubling of the days it stayed in the news
PER_TOPIC_MIN = 6  # every topic gets at least its top stories in
# Trump's statements get a bigger floor and their own section (the Trump tracker).
TRUMP_TOPICS = ("trump_posts", "trump_says", "trump_stocks")
TRUMP_MIN = 15
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


DIGESTS_DIR = DATA_DIR / "digests"


def digest_path(day: str) -> Path:
    return DIGESTS_DIR / f"{day}.json"


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
    days: list[str] | None = None  # in a week's digest: the days it was in the news


class Digest(BaseModel):
    date: str
    generated_at: datetime
    window_start: str
    n_items: int
    n_stories_total: int
    stories: list[Story]
    market: dict[str, Any] = {}
    days: list[str] | None = None  # a week's digest: the daily digests it joins


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
        floor = TRUMP_MIN if topic in TRUMP_TOPICS else PER_TOPIC_MIN
        for s in [s for s in ranked if topic in s.topics and not s.official][:floor]:
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


def merge_week(
    day: str,
    fresh: Digest | None,
    now: datetime,
    market: dict[str, Any] | None = None,
    digests_dir: Path | None = None,
) -> Digest:
    """The week's digest for the Saturday analysis: the daily digests of the 6 days
    before ``day`` plus ``fresh`` (the news since the last of them), with stories that
    ran on several days joined into one.

    A joined story keeps its strongest day's headline and score, plus a bonus for
    staying in the news, and lists the days it ran. Official notices join only when
    the title is the same.
    """
    folder = digests_dir or DIGESTS_DIR
    start = (date.fromisoformat(day) - timedelta(days=7)).isoformat()
    dailies = [
        Digest.model_validate_json(p.read_text(encoding="utf-8"))
        for p in sorted(folder.glob("*.json"))
        if start < p.stem < day
    ]
    if fresh is not None:
        dailies.append(fresh)
    if not dailies:
        raise FileNotFoundError(f"no daily digests for the week to {day}")
    pairs = [(d.date, s) for d in dailies for s in d.stories]
    pairs.sort(key=lambda ds: ds[1].score, reverse=True)  # the strongest leads its group
    stories = [_joined(g) for g in _join(pairs)]
    return Digest(
        date=day,
        generated_at=now,
        window_start=min(d.window_start for d in dailies),
        n_items=sum(d.n_items for d in dailies),
        n_stories_total=len(stories),
        stories=select(stories, WEEK_STORIES),
        market=market if market is not None else dailies[-1].market,
        days=[d.date for d in dailies],
    )


def _join(pairs: list[tuple[str, Story]]) -> list[list[tuple[str, Story]]]:
    """Group the days' stories by headline, as `cluster` groups items."""
    groups: list[list[tuple[str, Story]]] = []
    lead_tokens: list[set[str]] = []
    index: dict[str, set[int]] = defaultdict(set)
    official: dict[str, int] = {}
    for day, s in pairs:
        toks = tokens(s.title)
        best, best_sim = None, 0.0
        if s.official:
            best = official.get(s.title)
            best_sim = 1.0 if best is not None else 0.0
        elif len(toks) >= 3:
            for gi in set().union(*(index[t] for t in toks)):
                if groups[gi][0][1].official:
                    continue
                shared = len(toks & lead_tokens[gi])
                sim = shared / len(toks | lead_tokens[gi])
                if shared >= 3 and sim > best_sim:
                    best, best_sim = gi, sim
        if best is not None and best_sim >= SIMILARITY:
            groups[best].append((day, s))
            for t in toks:
                index[t].add(best)
            continue
        groups.append([(day, s)])
        lead_tokens.append(toks)
        for t in toks:
            index[t].add(len(groups) - 1)
        if s.official:
            official[s.title] = len(groups) - 1
    return groups


def _joined(group: list[tuple[str, Story]]) -> Story:
    lead = group[0][1]
    stories = [s for _, s in group]
    days = sorted({d for d, _ in group})
    publishers = sorted({p for s in stories for p in s.publishers})
    return Story(
        id="",
        title=lead.title,
        publishers=publishers[:6],
        n_publishers=max(len(publishers), *(s.n_publishers for s in stories)),
        topics=list(dict.fromkeys(t for s in stories for t in s.topics)),
        official=lead.official,
        first_seen=min(s.first_seen for s in stories),
        score=round(lead.score + DAYS_BONUS * math.log2(len(days)), 2),
        summary=next((s.summary for s in stories if s.summary), None),
        urls=list(dict.fromkeys(u for s in stories for u in s.urls))[:3],
        gdelt=next((s.gdelt for s in stories if s.gdelt), None),
        days=days,
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
    title = f"# News digest, the week to {d.date}" if d.days else f"# News digest {d.date}"
    joined = f", joined from the digests of {', '.join(d.days)}" if d.days else ""
    lines = [
        title,
        "",
        f"{len(d.stories)} of {d.n_stories_total} stories ({d.n_items} items) since "
        f"{d.window_start[:16]} UTC{joined}. Cite stories by id.",
        "",
    ]
    if d.market:
        lines += [market_markdown(d.market), ""]
    trump = [s for s in d.stories if any(t in TRUMP_TOPICS for t in s.topics)]
    if trump:
        lines += [
            "## Trump statements",
            "",
            "Coverage of Trump's posts, interviews and remarks. Log any stock, company or "
            "market call in `trump_calls`.",
            "",
        ]
        lines += [_story_line(s) for s in trump] + [""]
    head = "id | first seen, days in the news | pubs | topics" if d.days else "id | pubs | topics"
    lines += ["## Stories", "", f"{head} | headline — summary", ""]
    lines += [_story_line(s) for s in d.stories if s not in trump]
    return "\n".join(lines) + "\n"


def _story_line(s: Story) -> str:
    tag = "OFFICIAL " if s.official else ""
    pubs = f"{s.n_publishers}: {', '.join(s.publishers[:3])}"
    gd = ""
    if s.gdelt:
        actors = "/".join(s.gdelt.get("actor_countries", []))
        gd = f" [GDELT {s.gdelt.get('event_type')} {actors}]"
    summary = f" — {s.summary[:220]}" if s.summary else ""
    topics = ",".join(s.topics) or "-"
    when = ""
    if s.days:  # a week's digest: when it broke and how long it stayed in the news
        seen = f"{s.first_seen:%a} {s.first_seen.day} {s.first_seen:%b}"
        when = f"{seen}, {len(s.days)} day{'s' if len(s.days) > 1 else ''} | "
    return f"{s.id} | {when}{pubs} | {topics} | {tag}{s.title}{gd}{summary}"


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
