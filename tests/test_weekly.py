"""The weekly run since 2026-11-11: the week's date, the week's digest, one-month calls."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from stocktrend.cli import week_of
from stocktrend.events import digest as dg
from stocktrend.events.validate import WEEKLY_FROM, check

NOW = datetime(2026, 11, 14, 7, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("today", "week"),
    [
        ("2026-11-14", "2026-11-14"),  # Saturday: its own week
        ("2026-11-15", "2026-11-14"),  # the Sunday fallback still makes Saturday's picks
        ("2026-11-16", "2026-11-14"),  # a weekday: the latest Saturday
        ("2026-11-20", "2026-11-14"),
        ("2026-11-21", "2026-11-21"),
    ],
)
def test_week_of_is_the_latest_saturday(today: str, week: str) -> None:
    assert week_of(date.fromisoformat(today)).isoformat() == week


def _story(title: str, score: float, day: str, official: bool = False) -> dg.Story:
    seen = datetime.fromisoformat(f"{day}T08:00:00+00:00")
    return dg.Story(
        id="S000", title=title, publishers=[f"pub-{day}"], n_publishers=2, topics=["energy"],
        official=official, first_seen=seen, score=score, urls=[f"https://x/{day}/{score}"],
    )  # fmt: skip


def _digest(day: str, stories: list[dg.Story]) -> dg.Digest:
    return dg.Digest(
        date=day, generated_at=datetime.fromisoformat(f"{day}T05:30:00+00:00"),
        window_start=f"{day}T05:00:00+00:00", n_items=100, n_stories_total=len(stories),
        stories=stories,
    )  # fmt: skip


def _save(folder: Path, d: dg.Digest) -> None:
    (folder / f"{d.date}.json").write_text(d.model_dump_json(exclude_none=True), "utf-8")


def test_week_digest_joins_the_days_stories(tmp_path: Path) -> None:
    hormuz = "Iran seizes two oil tankers in the Strait of Hormuz"
    _save(tmp_path, _digest("2026-11-07", [_story("Last week's weekly digest", 9, "2026-11-07")]))
    _save(tmp_path, _digest("2026-11-09", [
        _story(hormuz, 5.0, "2026-11-09"),
        _story("Executive order on steel tariffs", 4.0, "2026-11-09", official=True),
    ]))  # fmt: skip
    _save(tmp_path, _digest("2026-11-11", [
        _story("Iran seizes oil tankers near the Strait of Hormuz again", 4.0, "2026-11-11"),
        _story("Executive order on steel tariffs", 3.0, "2026-11-11", official=True),
        _story("ECB holds rates", 4.5, "2026-11-11"),
    ]))  # fmt: skip
    fresh = _digest(
        "2026-11-14", [_story("Iran seizes two oil tankers in Hormuz Strait", 2, "2026-11-14")]
    )
    week = dg.merge_week("2026-11-14", fresh, NOW, digests_dir=tmp_path)

    assert week.date == "2026-11-14" and week.days == ["2026-11-09", "2026-11-11", "2026-11-14"]
    assert week.n_items == 300 and week.window_start == "2026-11-09T05:00:00+00:00"
    titles = [s.title for s in week.stories]
    assert "Last week's weekly digest" not in titles  # last Saturday's is not this week's
    assert [s.id for s in week.stories] == [f"S{n:03d}" for n in range(1, len(week.stories) + 1)]
    top = week.stories[0]  # three days in the news: the strongest day's score plus a bonus
    assert top.title == hormuz and top.days == ["2026-11-09", "2026-11-11", "2026-11-14"]
    assert top.score == pytest.approx(5.0 + dg.DAYS_BONUS * 1.585, abs=0.01)
    assert top.first_seen.date() == date(2026, 11, 9) and len(top.publishers) == 3
    order = titles.index("Executive order on steel tariffs")  # same official title: joined
    assert week.stories[order].days == ["2026-11-09", "2026-11-11"]
    assert titles.count("Executive order on steel tariffs") == 1
    md = dg.to_markdown(week)
    assert md.startswith("# News digest, the week to 2026-11-14")
    assert "Mon 9 Nov, 3 days" in md


def test_week_digest_needs_some_news(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        dg.merge_week("2026-11-14", None, NOW, digests_dir=tmp_path)


def test_new_events_files_take_one_month_calls_only() -> None:
    def errors(day: str, horizon: int) -> list[str]:
        impact = {"target": "theme:oil_supply_shock", "direction": 1, "magnitude": 0.4,
                  "confidence": 0.5, "horizon_days": horizon, "rationale": "supply -> up"}  # fmt: skip
        event = {"id": f"{day}-01", "title": "Refinery attack", "category": "supply_disruption",
                 "regions": ["SA"], "summary": "Attack.", "certainty": "confirmed",
                 "novelty": 0.8, "severity": 0.6, "duration": "weeks", "sources": ["S001"],
                 "impacts": [impact]}  # fmt: skip
        raw = json.dumps({"date": day, "market_view": "Oil up.", "events": [event]})
        return check(raw, day, {"S001"}, set(), {"oil_supply_shock"}).errors

    assert errors("2026-11-10", 5) == []  # the old daily files keep their 5-day calls
    assert errors(WEEKLY_FROM, 20) == []
    assert any("horizon_days must be 20" in e for e in errors(WEEKLY_FROM, 5))
