from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stocktrend.events.digest import Digest, Story
from stocktrend.tracking import feedback, impacts
from stocktrend.tracking.ledger import LedgerEntry
from stocktrend.universe.exposures import Exposures, Theme

DAY = "2026-10-28"  # a Wednesday; 5 sessions from its open end on 3 November
LOGGED = datetime(2026, 10, 28, 6, 30, tzinfo=UTC)
DATES = pd.bdate_range("2026-10-19", "2026-11-04")  # entry is index 7, the 5-day exit 11


def _bars(ticker: str, closes: Sequence[float]) -> pd.DataFrame:
    return pd.DataFrame({"date": DATES, "ticker": ticker, "open": closes, "high": closes,
                         "low": closes, "close": closes, "adj_close": closes, "volume": 1e6})  # fmt: skip


def _events(tmp: Path) -> Path:
    impact = {"magnitude": 0.5, "confidence": 0.6, "rationale": "a causal chain here"}
    event = {
        "id": f"{DAY}-01", "title": "Refinery attack", "category": "energy", "regions": ["SA"],
        "summary": "Attack.", "certainty": "confirmed", "novelty": 0.8, "severity": 0.6,
        "duration": "weeks", "sources": ["S001", "S002"],
        "impacts": [
            {**impact, "target": "theme:oil", "direction": 1, "horizon_days": 5},
            {**impact, "target": "ticker:XOM", "direction": -1, "horizon_days": 5,
             "against_coverage": True},
            {**impact, "target": "sector:Energy@US", "direction": 1, "horizon_days": 20},
        ],
    }  # fmt: skip
    events = tmp / "events"
    events.mkdir()
    events.joinpath(f"{DAY}.json").write_text(
        pd.Series({"date": DAY, "market_view": "x", "events": [event]}).to_json(), "utf-8"
    )
    return events


def _digest(tmp: Path) -> Path:
    now = datetime(2026, 10, 28, 5, tzinfo=UTC)
    story = Story(id="S001", title="t", publishers=["a"], n_publishers=1, topics=["energy"],
                  official=False, first_seen=now, score=1.0, urls=["https://x"])  # fmt: skip
    stories = [story, story.model_copy(update={"id": "S002", "n_publishers": 6})]
    d = Digest(date=DAY, generated_at=now, window_start=DAY, n_items=2, n_stories_total=2,
               stories=stories)  # fmt: skip
    digests = tmp / "digests"
    digests.mkdir()
    digests.joinpath(f"{DAY}.json").write_text(d.model_dump_json(), "utf-8")
    return digests


def test_every_analysis_call_is_scored_once_its_horizon_ends(tmp_path: Path) -> None:
    prices = pd.concat([
        # XOM ran up into the call (+11% over 5 sessions), then +10% from the entry.
        _bars("XOM", [90, 90, 92, 94, 96, 98, 100, 100, 102, 104, 106, 110, 111]),
        _bars("DAL", [100] * 8 + [99, 98, 97, 95, 94]),  # -5%
        _bars("SPY", [100] * 8 + [100.5, 101, 101.5, 102, 102]),  # +2%
        _bars("XLE", [100] * 13),
    ])  # fmt: skip
    exposures = Exposures(themes={"oil": Theme(name="Oil", description="d",
                                               members={"XOM": 1.0, "DAL": -0.5})})  # fmt: skip
    meta = pd.DataFrame({"sector": ["Energy"], "region": ["US"], "kind": ["etf"]},
                        index=pd.Index(["XLE"]))  # fmt: skip
    entry = LedgerEntry(pick_id="p", date=DAY, logged_at=LOGGED, strategy="geo", horizon=5,
                        side="long", ticker="XOM", region="US", sector="Energy", kind="stock",
                        rank=1)  # fmt: skip
    path = tmp_path / "impact_outcomes.jsonl"
    events, digests = _events(tmp_path), _digest(tmp_path)

    def run() -> tuple[int, int]:
        return impacts.evaluate(prices, {"US": "SPY"}, exposures, meta,
                                {("US", "Energy"): "XLE"}, [entry], events_dir=events,
                                digests_dir=digests, path=path)  # fmt: skip

    assert run() == (2, 1)  # the 20-day sector call is still open
    theme, ticker = impacts.read(path)
    # Theme: XOM (weight 1) beat SPY by 8 points, DAL (weight -0.5) lagged it by 7.
    assert theme.excess == pytest.approx((0.08 + 0.5 * 0.07) / 1.5, abs=1e-6)
    assert theme.correct and theme.members == 2 and theme.exit_date == "2026-11-03"
    assert theme.prior == pytest.approx((100 / 90 - 1) / 1.5, abs=1e-6)  # already moving
    assert (theme.official, theme.outlets, theme.against_coverage) == (False, 6, False)
    # Betting XOM would lag was wrong, and it bet against the coverage.
    assert ticker.excess == pytest.approx(-0.08) and not ticker.correct
    assert ticker.against_coverage and ticker.prior == pytest.approx(-(100 / 90 - 1), abs=1e-6)
    assert run() == (0, 1)  # each call is written once


def _outcome(day: date, n: int, excess: float, **kw: object) -> impacts.ImpactOutcome:
    base = {
        "impact_id": f"{day}:{n}", "date": day.isoformat(), "event_id": "e", "category": "trade",
        "certainty": "reported", "novelty": 0.5, "target": "theme:x", "kind": "theme",
        "direction": 1, "horizon": 5, "magnitude": 0.5, "confidence": 0.6, "official": False,
        "outlets": 3, "against_coverage": False, "prior": 0.01, "members": 3,
        "entry_date": day.isoformat(), "exit_date": day.isoformat(), "excess": excess,
        "correct": excess > 0, "evaluated_at": LOGGED,
    }  # fmt: skip
    return impacts.ImpactOutcome.model_validate({**base, **kw})


def test_feedback_note_gives_advice_only_on_solid_evidence() -> None:
    rng = np.random.default_rng(0)
    start = date(2026, 1, 5)
    out = []
    for d in range(30):
        day = start + timedelta(days=d)
        # Energy calls work; trade calls are noise; confident calls are overconfident.
        out.append(_outcome(day, 0, 0.02 + rng.normal(0, 0.01), category="energy"))
        out.append(_outcome(day, 1, rng.normal(0, 0.02)))
        for k in range(2):
            out.append(_outcome(day, 2 + k, rng.normal(0, 0.02), confidence=0.85))
    md = feedback.to_markdown(out, "2026-02-10")
    assert "120 calls scored over 30 days" in md
    assert "Category = energy: better than your other calls" in md
    assert "Category = trade: worse than your other calls" in md
    # Kinds every call shares (all 5-day themes here) carry no advice of their own.
    assert "Horizon = 5 days" not in md and "| Horizon | 5 days | 120 | 30 |" in md
    assert "Calls you rated 0.7 and above came true" in md and "overconfident" in md
    assert "No calls have been scored yet" in feedback.to_markdown([], "2026-02-10")


def test_feedback_note_with_too_little_evidence() -> None:
    out = [_outcome(date(2026, 1, 5) + timedelta(days=d), 0, 0.01) for d in range(12)]
    md = feedback.to_markdown(out, "2026-01-20")
    assert "Nothing is solid yet" in md
    assert "| Against the coverage's framing | no | 12 |" in md
