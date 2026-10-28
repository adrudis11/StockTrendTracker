"""`stt scoreboard`: how each strategy has done on matured picks.

For every strategy (geo, momentum, random), horizon and side: number of matured
picks, hit rate (share of correct calls), mean and median excess return, and a
t-statistic. For the geopolitical picks also the Brier score and a calibration
table: when we said 60%, were we right about 60% of the time?
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from stocktrend.tracking.evaluate import Outcome
from stocktrend.tracking.ledger import LedgerEntry, to_frame

CALIBRATION_BINS = [0.5, 0.55, 0.6, 0.65, 0.7, 0.75]
MIN_FOR_VERDICT = 100  # matured geo picks before we trust any conclusion


def frame(entries: list[LedgerEntry], outcomes: list[Outcome]) -> pd.DataFrame:
    if not entries or not outcomes:
        return pd.DataFrame()
    led = to_frame(entries)
    out = pd.DataFrame([o.model_dump() for o in outcomes])
    return led.merge(out, on="pick_id", how="inner")


def _stats(g: pd.DataFrame) -> dict[str, Any]:
    n = len(g)
    mean = float(g["excess"].mean())
    std = float(g["excess"].std(ddof=1)) if n > 1 else math.nan
    return {
        "n": n,
        "hit_rate": round(float(g["correct"].mean()), 3),
        "mean_excess": round(mean, 5),
        "median_excess": round(float(g["excess"].median()), 5),
        "t_stat": round(mean / (std / math.sqrt(n)), 2) if n > 1 and std > 0 else None,
    }


def compute(entries: list[LedgerEntry], outcomes: list[Outcome]) -> dict[str, Any]:
    df = frame(entries, outcomes)
    result: dict[str, Any] = {
        "matured": len(df),
        "open": len(entries) - len(df),
        "rows": [],
        "calibration": [],
        "brier": None,
        "enough_data": False,
    }
    if df.empty:
        return result
    for (strategy, h, side), g in df.groupby(["strategy", "horizon", "side"]):
        result["rows"].append(
            {"strategy": strategy, "horizon": int(str(h)), "side": side, **_stats(g)}
        )
    geo = df[(df["strategy"] == "geo") & df["prob"].notna()]
    if not geo.empty:
        result["brier"] = round(
            float(((geo["prob"] - geo["correct"].astype(float)) ** 2).mean()), 4
        )
        bins = pd.cut(geo["prob"], CALIBRATION_BINS, include_lowest=True)
        for interval, g in geo.groupby(bins, observed=True):
            result["calibration"].append(
                {
                    "bucket": str(interval),
                    "n": len(g),
                    "predicted": round(float(g["prob"].mean()), 3),
                    "observed": round(float(g["correct"].mean()), 3),
                }
            )
        result["enough_data"] = len(geo) >= MIN_FOR_VERDICT
    return result


def to_markdown(sb: dict[str, Any], first_matures: str | None = None) -> str:
    lines = ["## Track record", ""]
    if not sb["rows"]:
        when = f" The first 1-week results are due around {first_matures}." if first_matures else ""
        return "\n".join([*lines, f"_No picks have matured yet ({sb['open']} open).{when}_", ""])
    lines += [
        "| Strategy | Horizon | Side | Matured | Hit rate | Mean excess | Median | t |",
        "|---|---|---|---|---|---|---|---|",
    ]
    order = {"geo": 0, "momentum": 1, "random": 2}
    for r in sorted(sb["rows"], key=lambda r: (r["horizon"], r["side"], order[r["strategy"]])):
        t = "–" if r["t_stat"] is None else f"{r['t_stat']:+.1f}"
        lines.append(
            f"| {r['strategy']} | {r['horizon']}d | {r['side']} | {r['n']} | "
            f"{r['hit_rate']:.0%} | {100 * r['mean_excess']:+.2f}% | "
            f"{100 * r['median_excess']:+.2f}% | {t} |"
        )
    lines.append("")
    if sb["brier"] is not None:
        lines.append(
            f"Brier score of the geo picks' probabilities: {sb['brier']:.3f} "
            "(0.25 = no better than always saying 50%)."
        )
    if not sb["enough_data"]:
        lines.append(
            f"_Fewer than {MIN_FOR_VERDICT} matured geo picks: too early to draw conclusions._"
        )
    return "\n".join([*lines, ""])
