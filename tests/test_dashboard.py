from pathlib import Path

import pytest

from stocktrend.config import DATA_DIR
from stocktrend.report import dashboard
from stocktrend.report.dashboard import DATASETS, build, how_scored, reasons
from stocktrend.scoring.run import load as load_picks


def test_dashboard_datasets_from_committed_day() -> None:
    day = sorted(p.stem for p in (DATA_DIR / "picks").glob("*.json"))[0]
    data = build(day)
    assert set(data) == set(DATASETS)
    (summary,) = data["summary"]
    assert summary["date"] == day and summary["regime"]
    keys = [p["key"] for p in data["picks"]]
    assert len(keys) == len(set(keys))  # the page's tables key rows by this
    assert all(p["side"] in ("long", "avoid") for p in data["picks"])
    assert len({e["id"] for e in data["events"]}) == len(data["events"])
    # The page reads a dataset only when the summary counts rows in it: an empty file
    # can't load on the dashboard.
    counts = {"picks": "n_picks", "events": "events", "congress": "n_congress", "insiders": "n_insiders", "open_picks": "n_open", "results": "n_results", "call_kinds": "n_call_kinds",
              "calibration": "n_calibration", "adjustments": "n_adjustments",
              "trump": "trump_calls", "scoreboard": "matured", "proposals": "n_proposals"}  # fmt: skip
    for name, key in counts.items():
        assert (summary[key] > 0) == bool(data[name]), name
    for r in data["insiders"]:  # what insiders paid and where the stock is now
        if r["paid"] and r["now"]:
            assert r["since"] == round(100 * (r["now"] / r["paid"] - 1), 1)
    for r in data["congress"]:  # estimated price on the trade dates, and where it is now
        if r["price_then"] and r["now"]:
            assert r["since"] == round(100 * (r["now"] / r["price_then"] - 1), 1)
    assert all(p["last_close"] > 0 for p in data["picks"])  # the price each pick was made at


def test_dashboard_shows_the_latest_picks_on_days_without_any() -> None:
    days = sorted(p.stem for p in (DATA_DIR / "picks").glob("*.json"))
    assert dashboard.latest_picks_day(days[0]) == days[0]
    assert dashboard.latest_picks_day("2099-01-01") == days[-1]
    with pytest.raises(FileNotFoundError):
        dashboard.latest_picks_day("2000-01-01")


def test_dashboard_counts_one_month_picks_for_the_verdict() -> None:
    day = sorted(p.stem for p in (DATA_DIR / "picks").glob("*.json"))[-1]
    (s,) = build(day)["summary"]
    assert s["held_from"] and s["held_to"] and s["held_from"] < s["held_to"]
    assert s["record_h"] in (5, 20) and s["record_span"] in ("week", "month")
    assert s["record_old"] == int(s["record_h"] != 20)
    assert s["geo_matured"] >= 0 and s["verdict_left"] == max(0, 100 - s["geo_matured"])


def test_holding_is_twenty_sessions_from_the_next_open() -> None:
    from datetime import UTC, datetime

    # Made on Saturday 14 Nov: held from Monday 16 Nov, 20 NYSE sessions (Thanksgiving
    # on 26 Nov is closed), to Monday 14 Dec.
    assert dashboard.holding(datetime(2026, 11, 14, 7, tzinfo=UTC)) == ("2026-11-16", "2026-12-14")


def test_pick_reasons_back_the_pick_first() -> None:
    day = sorted(p.stem for p in (DATA_DIR / "picks").glob("*.json"))[-1]
    f = load_picks(day)
    for p in f.picks:
        rs = reasons(p)
        assert len(rs) == len(p.drivers)
        supports = [r["supports"] for r in rs]
        assert supports == sorted(supports, reverse=True)  # backers first, then the rest
        assert how_scored(p, f).endswith(f"= conviction {p.conviction:.2f}")


NEW_REVIEW = """# Weekly review

## 4. Changes

**Changes made:** None: this run can't edit files.

**Changes proposed:**
1. **`config/exposures.yaml`, `shipping_disruption`: lower BOAT's weight by 0.3.**
   - Evidence: BOAT fell 10.2%.
2. **`prompts/daily_analysis.md`, add:** "A leader's remark is not policy."

**Not proposed (for later):**
- Possible stale European prices.
"""

OLD_REVIEW = """## 4. Changes

**Made:** none.

**Proposed, not made (revisit when results arrive):**

- A US LNG export theme (US LNG approvals, Qatar's purchase).
  These went to `us_fossil_energy_policy`.
- Watch airline avoids.
"""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            NEW_REVIEW,
            [
                "config/exposures.yaml, shipping_disruption: lower BOAT's weight by 0.3.",
                'prompts/daily_analysis.md, add: "A leader\'s remark is not policy."',
            ],
        ),
        (
            OLD_REVIEW,
            [
                "A US LNG export theme (US LNG approvals, Qatar's purchase). "
                "These went to us_fossil_energy_policy.",
                "Watch airline avoids.",
            ],
        ),
        ("# Weekly review\n\nNothing to propose.\n", []),
    ],
)
def test_review_proposals(
    text: str, expected: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "2026-11-07.md").write_text(text, encoding="utf-8")
    (tmp_path / "2026-11-14.md").write_text("later", encoding="utf-8")  # after the day
    monkeypatch.setattr(dashboard, "REVIEWS_DIR", tmp_path)
    day, rows = dashboard.review_proposals("2026-11-10")
    assert day == "2026-11-07"
    assert [r["text"] for r in rows] == expected
