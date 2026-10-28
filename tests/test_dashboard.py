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
    # The page reads a dataset only when the summary counts rows in it: an empty file
    # can't load on the dashboard.
    counts = {"picks": "n_picks", "events": "events", "congress": "n_congress", "insiders": "n_insiders", "open_picks": "n_open",
              "trump": "trump_calls", "scoreboard": "matured"}  # fmt: skip
    for name, key in counts.items():
        assert (summary[key] > 0) == bool(data[name]), name
    for r in data["insiders"]:  # what insiders paid and where the stock is now
        if r["paid"] and r["now"]:
            assert r["since"] == round(100 * (r["now"] / r["paid"] - 1), 1)
    for r in data["congress"]:  # estimated price on the trade dates, and where it is now
        if r["price_then"] and r["now"]:
            assert r["since"] == round(100 * (r["now"] / r["price_then"] - 1), 1)
    assert all(p["last_close"] > 0 for p in data["picks"])  # the price each pick was made at
