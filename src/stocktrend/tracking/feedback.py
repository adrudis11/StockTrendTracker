"""The feedback note the daily analysis reads: how past calls turned out, by kind.

Built from ``data/ledger/impact_outcomes.jsonl`` (``tracking.impacts``) by
``stt feedback``. Stock returns are noisy and calls made on the same day move
together, so every figure comes with its sample size and a t-statistic across days
(each day's calls are averaged first). Each kind of call is also compared with the
other calls of its section on the same days ("vs the rest"): that difference is
what tells the analysis to lean on a kind of call or to be warier of it. Only solid
findings, at least ``SOLID_DAYS`` days with |t| >= ``SOLID_T``, become advice.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pandas as pd

from stocktrend.tracking.impacts import ImpactOutcome

MIN_CALLS = 10  # smaller groups aren't shown
SOLID_DAYS, SOLID_T = 20, 2.0
CALIBRATION_MIN, CALIBRATION_GAP = 50, 0.10
CONFIDENCE_BINS = ((0.0, 0.5, "below 0.5"), (0.5, 0.6, "0.5 to 0.6"), (0.6, 0.7, "0.6 to 0.7"),
                   (0.7, 1.01, "0.7 and above"))  # fmt: skip


@dataclass
class Group:
    section: str
    label: str
    n: int
    days: int
    hit: float
    mean: float
    t: float | None
    vs_rest: float | None = None  # mean daily difference from the section's other calls
    vs_rest_t: float | None = None
    vs_rest_days: int = 0

    @property
    def solid(self) -> bool:
        return _solid(self.days, self.t)

    @property
    def solid_vs_rest(self) -> bool:
        return _solid(self.vs_rest_days, self.vs_rest_t)


def _solid(days: int, t: float | None) -> bool:
    return days >= SOLID_DAYS and t is not None and abs(t) >= SOLID_T


def _mean_t(per_day: pd.Series) -> tuple[float, float | None]:
    days = len(per_day)
    mean = float(per_day.mean())
    sd = float(per_day.std(ddof=1)) if days > 1 else math.nan
    return mean, (mean / (sd / math.sqrt(days)) if days > 1 and sd > 0 else None)


def _group(section: str, label: str, df: pd.DataFrame, rest: pd.DataFrame | None = None) -> Group:
    per_day = df.groupby("date")["excess"].mean()
    mean, t = _mean_t(per_day)
    g = Group(section, label, len(df), len(per_day), float(df["correct"].mean()), mean, t)
    if rest is not None and len(rest) >= MIN_CALLS:
        diff = (per_day - rest.groupby("date")["excess"].mean()).dropna()  # days with both
        if len(diff) > 1:
            g.vs_rest, g.vs_rest_t = _mean_t(diff)
            g.vs_rest_days = len(diff)
    return g


def _outlets(n: int) -> str:
    return "1 outlet" if n <= 1 else ("2 to 4 outlets" if n <= 4 else "5 or more outlets")


def _prior(x: float | None) -> str | None:
    if x is None or pd.isna(x):
        return None
    return "already moving the called way" if x > 0 else "moving the other way"


# section -> function giving each call's label in it (None = left out)
SECTIONS: dict[str, Callable[[dict[str, Any]], str | None]] = {
    "Horizon": lambda r: f"{r['horizon']} days",
    "Category": lambda r: str(r["category"]).replace("_", " "),
    "Target": lambda r: {"theme": "theme", "ticker": "single stock", "sector": "sector"}[
        str(r["kind"])
    ],
    "Certainty": lambda r: str(r["certainty"]),
    "Sources": lambda r: "an official source" if r["official"] else "media only",
    "Coverage": lambda r: _outlets(int(r["outlets"])),
    "Against the coverage's framing": lambda r: "yes" if r["against_coverage"] else "no",
    "Market before the call": lambda r: _prior(r["prior"]),
}


def frame(outcomes: list[ImpactOutcome]) -> pd.DataFrame:
    return pd.DataFrame([o.model_dump() for o in outcomes])


def groups(df: pd.DataFrame) -> list[Group]:
    out = []
    records = [{str(k): v for k, v in r.items()} for r in df.to_dict("records")]
    for section, label_of in SECTIONS.items():
        values: list[str | None] = [label_of(r) for r in records]
        labels = pd.Series(values, index=df.index, dtype=object)
        for label in sorted(labels.dropna().unique()):
            g = df[labels == label]
            if len(g) >= MIN_CALLS:
                rest = df[labels.notna() & (labels != label)]
                out.append(_group(section, str(label), g, rest))
    return out


def calibration(df: pd.DataFrame) -> list[tuple[str, int, float, float]]:
    """(confidence band, calls, average stated confidence, share right)."""
    rows = []
    for lo, hi, label in CONFIDENCE_BINS:
        g = df[(df["confidence"] >= lo) & (df["confidence"] < hi)]
        if len(g) >= MIN_CALLS:
            rows.append((label, len(g), float(g["confidence"].mean()), float(g["correct"].mean())))
    return rows


def advice(gs: list[Group], cal: list[tuple[str, int, float, float]]) -> list[str]:
    out = []
    for g in gs:
        what = f"{g.section} = {g.label}"
        stats = f"right {g.hit:.0%} of {g.n} calls, mean excess {100 * g.mean:+.2f}%"
        if g.solid and g.mean < 0:
            out.append(f"{what}: these calls have lost ({stats}, t {_t(g.t)}). Lower their "
                       "confidence, raise the bar for them, or leave them out.")  # fmt: skip
        elif g.solid_vs_rest and g.vs_rest is not None:
            better = g.vs_rest > 0
            gap = f"{100 * g.vs_rest:+.2f} points a day vs the rest, t {_t(g.vs_rest_t)}"
            out.append(
                f"{what}: {'better' if better else 'worse'} than your other calls ({stats}; "
                f"{gap}). " + ("Lean on them; their confidence can go up a little." if better
                               else "Be warier: lower their confidence or raise the bar.")
            )  # fmt: skip
    for label, n, stated, actual in cal:
        if n >= CALIBRATION_MIN and abs(actual - stated) >= CALIBRATION_GAP:
            way = "overconfident" if actual < stated else "underconfident"
            out.append(f"Calls you rated {label} came true {actual:.0%} of the time against "
                       f"{stated:.0%} stated ({n} calls): you are {way} there; adjust that "
                       "band.")  # fmt: skip
    return out


def _t(t: float | None) -> str:
    return "–" if t is None else f"{t:+.1f}"


@dataclass
class Results:
    overall: Group
    groups: list[Group]
    calibration: list[tuple[str, int, float, float]]
    advice: list[str]


def results(outcomes: list[ImpactOutcome]) -> Results | None:
    """Everything the note and the dashboard show, or None before any call is scored."""
    if not outcomes:
        return None
    df = frame(outcomes)
    gs, cal = groups(df), calibration(df)
    return Results(_group("All", "all calls", df), gs, cal, advice(gs, cal))


def to_markdown(outcomes: list[ImpactOutcome], as_of: str) -> str:
    lines = [f"# Your track record (as of {as_of})", ""]
    r = results(outcomes)
    if r is None:
        return "\n".join([*lines, "No calls have been scored yet. Make calls as the prompt "
                          "says; this note fills in as their horizons end.", ""])  # fmt: skip
    overall = r.overall
    lines += [
        f"{overall.n} calls scored over {overall.days} days: right {overall.hit:.0%}, mean "
        f"excess {100 * overall.mean:+.2f}% (t {_t(overall.t)}). A call is right when its "
        "target beat the benchmark in the direction called (a theme's losers lagging "
        "counts for the theme).",
        "",
        f"How to read this: t is computed across days, since calls made on the same day move "
        f"together. A finding is solid only with at least {SOLID_DAYS} days of calls and "
        f"|t| >= {SOLID_T:.0f}; anything else is noise until proven otherwise, so don't adjust "
        "on it.",
        "",
        "## What to adjust",
        "",
    ]
    gs, cal = r.groups, r.calibration
    lines += [f"- {t}" for t in r.advice] or [
        "- Nothing is solid yet. Keep calling as before; don't react to single wins or losses."
    ]
    lines += ["", "## By kind of call", "",
              "| Kind | Group | Calls | Days | Right | Mean excess | t | vs the rest (t) | "
              "Evidence |",
              "|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for g in gs:
        rest = "–" if g.vs_rest is None else f"{100 * g.vs_rest:+.2f} ({_t(g.vs_rest_t)})"
        evidence = "solid" if g.solid or g.solid_vs_rest else "weak"
        lines.append(
            f"| {g.section} | {g.label} | {g.n} | {g.days} | {g.hit:.0%} | "
            f"{100 * g.mean:+.2f}% | {_t(g.t)} | {rest} | {evidence} |"
        )
    if cal:
        lines += ["", "## Calibration", "",
                  "| Stated confidence | Calls | Average stated | Actually right |",
                  "|---|---|---|---|"]  # fmt: skip
        lines += [f"| {label} | {n} | {stated:.0%} | {actual:.0%} |" for label, n, stated, actual
                  in cal]  # fmt: skip
    return "\n".join([*lines, ""])
