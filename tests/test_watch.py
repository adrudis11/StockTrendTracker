"""The free weekday extras: the watch on open picks, the week's news, odds moves."""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

import pandas as pd
import pytest

from stocktrend.events import digest as dg
from stocktrend.scoring.model import Driver, Pick
from stocktrend.tracking import watch


def _pick(
    side: Literal["long", "avoid"] = "long", conviction: float = 0.4, geo: float = 0.5
) -> Pick:
    return Pick(
        ticker="XOM", name="Exxon Mobil Corporation", kind="stock", region="US",
        sector="Energy", horizon=20, side=side, rank=1, score=conviction, conviction=conviction,
        prob=0.6, geo=geo, prior=0.0, trend=0.0, priced_in=0.0, risk=1.0, risks=[],
        drivers=[Driver(event_id="e", title="t", target="theme:x", via="x", strength=0.2,
                        rationale="r")],
        primary_theme="x", last_close=100.0, last_date="2026-11-13", r5d=0.0, r20d=0.0,
        excess_5d=0.0, excess_20d=0.0, vol_annual=0.3, next_earnings=None,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (None, "spent"),  # no event behind it is still live
        (_pick("avoid", 0.3, -0.4), "reversed"),
        # The news fades by design (geo 0.5 -> 0.25); only the rest of the score counts.
        (_pick(conviction=0.2, geo=0.25), "holding"),
        (_pick(conviction=0.3, geo=0.25), "stronger"),  # e.g. the trend turned its way
        (_pick(conviction=0.15, geo=0.25), "weaker"),  # e.g. earnings now inside the month
    ],
)
def test_case_compares_the_non_news_part(now: Pick | None, expected: str) -> None:
    assert watch.case(_pick(), now)[0] == expected


@pytest.mark.parametrize(
    ("name", "short"),
    [
        ("Exxon Mobil Corporation", "Exxon"),
        ("General Dynamics Corporation", "General Dynamics"),
        ("Airbus SE", "Airbus"),
        ("BP p.l.c.", "BP"),
        ("The Boeing Company", "Boeing"),
        ("UBS Group AG", "UBS"),
    ],
)
def test_short_name(name: str, short: str) -> None:
    assert watch.short_name(name) == short


def test_mentions() -> None:
    assert watch.mentions("BP cuts output after outage", "BP")
    assert not watch.mentions("Bpost rallies", "BP") and not watch.mentions("bp outage", "BP")
    assert watch.mentions("exxon raises its dividend", "Exxon")
    assert not watch.mentions("Exxonsomething", "Exxon")


def test_filings_since_the_pick() -> None:
    d = date.fromisoformat
    insiders = pd.DataFrame({
        "ticker": ["XOM", "XOM", "XOM", "CVX"], "owner": ["A", "B", "C", "D"],
        "code": ["P", "P", "S", "P"], "filed": [d("2026-11-17"), d("2026-11-18"),
                                                d("2026-11-10"), d("2026-11-18")],
    })  # fmt: skip
    house = pd.DataFrame({
        "ticker": ["XOM"], "member": ["M"], "type": ["partial_sale"], "filed": [d("2026-11-19")],
    })  # fmt: skip
    notes = watch.filings_since("XOM", d("2026-11-14"), insiders, house)
    assert notes == ["2 insiders bought", "1 House member sold"]  # the 11-10 sale was before


def test_changes_report_moved_cases_and_new_flags_once() -> None:
    row = {"pick_id": "p1", "ticker": "XOM", "side": "long", "date": "2026-11-14",
           "case": "holding", "flags": ["named in 1 story this week"]}  # fmt: skip
    later = dict(row, case="weaker",
                 flags=["named in 3 stories this week", "earnings on 2026-11-20, before it ends"])  # fmt: skip
    got = [c["text"] for c in watch.changes([later], [row])]
    assert got == [
        "XOM (buy, picked 2026-11-14): case holding → weaker",
        "XOM (buy, picked 2026-11-14): earnings on 2026-11-20, before it ends",
    ]  # more stories isn't a new flag
    assert watch.changes([row], [row]) == []


def test_week_so_far_joins_the_weekday_digests(tmp_path: Path) -> None:
    def save(day: str, title: str) -> None:
        story = dg.Story(
            id="S001", title=title, publishers=["p"], n_publishers=1, topics=["energy"],
            official=False, first_seen=datetime.fromisoformat(f"{day}T08:00:00+00:00"),
            score=3.0, urls=["https://x"],
        )  # fmt: skip
        d = dg.Digest(date=day, generated_at=datetime.fromisoformat(f"{day}T05:00:00+00:00"),
                      window_start=day, n_items=1, n_stories_total=1, stories=[story])  # fmt: skip
        (tmp_path / f"{day}.json").write_text(d.model_dump_json(), "utf-8")

    save("2026-11-14", "Last week's news")  # Saturday's week digest: not this week's
    save("2026-11-16", "Iran seizes two oil tankers in the Strait of Hormuz")
    save("2026-11-17", "Iran seizes oil tankers in the Strait of Hormuz again")
    now = datetime(2026, 11, 17, 9, tzinfo=UTC)
    week = dg.week_so_far("2026-11-17", now, tmp_path)
    assert week is not None and [s.days for s in week.stories] == [["2026-11-16", "2026-11-17"]]
    assert dg.week_so_far("2026-11-14", now, tmp_path) is not None  # Saturday: its own digest
    assert dg.week_so_far("2026-11-21", now, tmp_path / "none") is None
