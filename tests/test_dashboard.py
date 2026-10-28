from stocktrend.config import DATA_DIR
from stocktrend.report.dashboard import DATASETS, build


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
