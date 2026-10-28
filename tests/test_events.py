import json
from datetime import UTC, datetime, timedelta

import pandas as pd

from stocktrend.events import digest as dg
from stocktrend.events.validate import check, known_tickers
from stocktrend.ingest.models import NewsItem
from stocktrend.universe.build import apply_overrides
from stocktrend.universe.exposures import load_exposures

NOW = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)


def news(title: str, publisher: str, feed: str = "google_news:energy:US", **kw: object) -> NewsItem:
    return NewsItem.make(
        url=f"https://example.com/{publisher}/{abs(hash(title))}",
        source=kw.pop("source", "rss"),  # type: ignore[arg-type]
        feed=feed,
        title=title,
        published_at=NOW - timedelta(hours=2),
        publisher=publisher,
        topics=["energy"],
        **kw,  # type: ignore[arg-type]
    )


# --- digest -----------------------------------------------------------------


def test_cluster_groups_same_story_and_keeps_official_apart() -> None:
    items = [
        news("Oil prices rise after attack on Saudi refinery", "Reuters"),
        news("Oil prices rise as attack on Saudi refinery reignites supply fears", "CNBC"),
        news("Oil rises after Saudi refinery attack", "Yahoo"),
        news("Apple unveils new iPhone colours", "Verge"),
        news("Adjusting Imports of Oil Refinery Equipment", "Federal Register",
             feed="federal_register:presidential", source="federal_register",
             extra={"official": True}),
    ]  # fmt: skip
    clusters = dg.cluster(items)
    sizes = sorted(len(c.items) for c in clusters)
    assert sizes == [1, 1, 3]
    big = max(clusters, key=lambda c: len(c.items))
    assert big.publishers == {"Reuters", "CNBC", "Yahoo"}


def test_score_rewards_coverage_and_officials() -> None:
    wide = dg._Cluster([news("x one two three", "A")], set(), {"A", "B", "C", "D"})
    narrow = dg._Cluster([news("x one two three", "A")], set(), {"A"})
    official = dg._Cluster(
        [news("Order", "FR", feed="federal_register:presidential", extra={"official": True})],
        set(),
        {"FR"},
    )
    assert dg.score(wide, NOW) > dg.score(narrow, NOW)
    assert dg.score(official, NOW) > dg.score(narrow, NOW)


def _story(i: int, topic: str, score: float, official: bool = False) -> dg.Story:
    return dg.Story(
        id="", title=f"story {i}", publishers=["p"], n_publishers=1, topics=[topic],
        official=official, first_seen=NOW, score=score, urls=[f"https://x/{i}"],
    )  # fmt: skip


def test_select_keeps_topic_floor_and_numbers_stories() -> None:
    stories = [_story(i, "energy", 10 - i * 0.01) for i in range(30)]
    stories += [_story(100 + i, "food_agri", 1.0) for i in range(10)]
    stories += [_story(200, "-", 0.5, official=True)]
    chosen = dg.select(stories, max_stories=20)
    assert len(chosen) == 20
    assert sum("food_agri" in s.topics for s in chosen) >= dg.PER_TOPIC_MIN
    assert any(s.official for s in chosen)
    assert [s.id for s in chosen[:3]] == ["S001", "S002", "S003"]


def test_ceremonial_filter() -> None:
    assert dg._CEREMONIAL.search("National Seafood Month, 2026")
    assert dg._CEREMONIAL.search("Veterans Day, 2026")
    assert not dg._CEREMONIAL.search("Adjusting Imports of Wind Turbines")


# --- validation -------------------------------------------------------------

DAY = "2026-10-28"


def _events(**overrides: object) -> str:
    impact = {
        "target": "theme:oil_supply_shock",
        "direction": 1,
        "magnitude": 0.4,
        "confidence": 0.5,
        "horizon_days": 5,
        "rationale": "Refinery attack -> supply -> producers up",
    }
    event = {
        "id": f"{DAY}-01",
        "title": "Refinery attack",
        "category": "supply_disruption",
        "regions": ["SA"],
        "summary": "Attack.",
        "certainty": "confirmed",
        "novelty": 0.8,
        "severity": 0.6,
        "duration": "weeks",
        "sources": ["S001"],
        "impacts": [impact],
    }
    event.update(overrides)
    return json.dumps({"date": DAY, "market_view": "Oil up.", "events": [event]})


def _check(raw: str) -> list[str]:
    return check(raw, DAY, {"S001", "S002"}, {"XOM", "AIR.PA"}, {"oil_supply_shock"}).errors


def test_valid_events_pass() -> None:
    assert _check(_events()) == []


def test_validation_catches_bad_references() -> None:
    bad_impacts = [
        {"target": "ticker:FAKE", "direction": 1, "magnitude": 0.3, "confidence": 0.5,
         "horizon_days": 20, "rationale": "made-up ticker here"},
        {"target": "theme:nonexistent", "direction": -1, "magnitude": 0.3, "confidence": 0.5,
         "horizon_days": 20, "rationale": "made-up theme here"},
        {"target": "sector:Energy@Asia", "direction": 1, "magnitude": 0.3, "confidence": 0.5,
         "horizon_days": 5, "rationale": "region not covered"},
    ]  # fmt: skip
    errors = _check(_events(sources=["S001", "S999"], impacts=bad_impacts))
    assert any("S999" in e for e in errors)
    assert any("unknown ticker" in e for e in errors)
    assert any("unknown theme" in e for e in errors)
    assert any("sector:<GICS sector>" in e for e in errors)


def test_validation_catches_schema_errors() -> None:
    assert any("confidence" in e for e in _check(_events(impacts=[{
        "target": "theme:oil_supply_shock", "direction": 1, "magnitude": 0.4,
        "confidence": 1.5, "horizon_days": 5, "rationale": "too confident by far"}])))  # fmt: skip
    assert any("horizon_days" in e for e in _check(_events(impacts=[{
        "target": "theme:oil_supply_shock", "direction": 1, "magnitude": 0.4,
        "confidence": 0.5, "horizon_days": 10, "rationale": "unsupported horizon"}])))  # fmt: skip
    assert _check("{not json")


def test_committed_events_files_are_valid() -> None:
    from stocktrend.config import DATA_DIR
    from stocktrend.events.validate import validate

    for path in sorted((DATA_DIR / "events").glob("*.json")):
        v = validate(path.stem)
        assert v.ok, (path.name, v.errors)


# --- reference data ---------------------------------------------------------


def test_exposure_members_are_known_tickers() -> None:
    known = known_tickers()
    for tid, theme in load_exposures().themes.items():
        unknown = [t for t in theme.members if t not in known]
        assert not unknown, (tid, unknown)
        assert theme.driver is None or theme.driver in known, tid


def test_apply_overrides() -> None:
    u = pd.DataFrame(
        [
            ["SHEL.L", "Shell", "Energy", "FTSE100"],
            ["SHELL.AS", "Shell", "Energy", "AEX"],
            ["IBE.MC", "Iberdrola", "Energy", "IBEX35"],
        ],
        columns=["ticker", "name", "sector", "indices"],
    )
    for col in ("region", "country", "exchange", "currency", "industry", "sector_raw", "cik"):
        u[col] = ""
    out = apply_overrides(
        u, {"duplicate_listings": {"SHELL.AS": "SHEL.L"}, "sector": {"IBE.MC": "Utilities"}}
    ).set_index("ticker")
    assert list(out.index) == ["SHEL.L", "IBE.MC"]
    assert out.at["SHEL.L", "indices"] == "FTSE100|AEX"
    assert out.at["IBE.MC", "sector"] == "Utilities"
