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
