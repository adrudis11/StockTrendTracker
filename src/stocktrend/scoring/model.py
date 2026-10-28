"""Scoring model v1: from Claude's events to ranked picks.

For each candidate and horizon (5 or 20 trading days):

    geo        = tanh(1.5 * sum of event contributions)       signed, in [-1, 1]
    conviction = |geo| * (1 + 0.4 trend) * (1 + 0.4 prior) * (1 - 0.5 priced_in) * risk
    score      = sign(geo) * conviction

The geopolitical signal is the base and everything else scales it, so price
momentum alone can never create a pick.

* An event contribution is direction x member weight x magnitude x confidence x
  novelty factor x age decay. Themes expand to their members with signed weights;
  ``sector:`` targets hit the region's sector ETF fully and its stocks at 0.3.
* ``trend`` rewards price action that agrees with the thesis (up to 1.5 sigma);
  ``priced_in`` penalises moves beyond 2 sigma that already happened.
* ``prior`` is the historical reaction of the theme's members after big moves in the
  theme's driver (``stt backtest``). It applies only while that driver is spiking
  now, shrunk by its t-statistic; 0 otherwise.
* ``risk`` is a multiplier below 1 for earnings inside the horizon, unknown
  earnings dates and very high volatility. Illiquid tickers are excluded.

Probabilities are an uncalibrated mapping of conviction until enough picks have
matured to fit them (Phase 6). The report says so.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel

from stocktrend.events.schema import EventsFile
from stocktrend.features.technical import move_z
from stocktrend.trackers.insiders import note as insider_note
from stocktrend.universe.exposures import Exposures

MODEL_VERSION = "v1"
HORIZONS = (5, 20)
WEIGHTS = {"trend": 0.4, "prior": 0.4, "priced_in": 0.5}
# Risk multipliers. Results dominate a stock's next week, so earnings inside a
# 5-day horizon cut conviction hardest.
RISK = {
    "earnings_5d": 0.4,
    "earnings_20d": 0.7,
    "earnings_unknown": 0.95,
    "high_vol": 0.8,
    "risk_off_vol": 0.9,
}
GEO_SCALE = 1.5
MIN_GEO = 0.10  # weaker signals are noise
SECTOR_STOCK_WEIGHT = 0.3
LOOKBACK_DAYS = 30  # calendar days of events files considered
# A 20-day thesis also says something about the next 5 days, and vice versa.
CROSS_HORIZON = {(20, 5): 0.5, (5, 20): 0.3}
MAX_PICKS, MAX_AVOID = 10, 5
MAX_PER_THEME, MAX_ETFS = 3, 3


class Driver(BaseModel):
    event_id: str
    title: str
    target: str
    via: str  # theme id, "direct" or "sector"
    strength: float
    rationale: str


class Pick(BaseModel):
    ticker: str
    name: str
    kind: Literal["stock", "etf"]
    region: str
    sector: str
    horizon: int
    side: Literal["long", "avoid"]
    rank: int
    score: float
    conviction: float
    prob: float
    geo: float
    prior: float
    trend: float
    priced_in: float
    risk: float
    risks: list[str]
    drivers: list[Driver]
    primary_theme: str | None
    last_close: float
    last_date: str
    r5d: float | None
    r20d: float | None
    excess_5d: float | None
    excess_20d: float | None
    vol_annual: float | None
    next_earnings: str | None
    tracker: float = 0.0  # congressional buying (+) or selling (-) in [-1, 1]
    tracker_note: str | None = None
    insider: float = 0.0  # insider buying (SEC Form 4) in [0, 1]
    insider_note: str | None = None
    trump_note: str | None = None  # what Trump said about it lately (no weight in the score)


class PicksFile(BaseModel):
    date: str
    model_version: str
    weights: dict[str, float]
    risk_multipliers: dict[str, float]
    regime: dict[str, Any]
    active_priors: dict[str, dict[int, float]] = {}  # themes whose driver is spiking now
    tracker_weight: float = 0.0  # how much congressional trades scale conviction
    insider_weight: float = 0.0  # how much insider buying scales conviction
    events_used: list[str]
    picks: list[Pick]

    def select(self, horizon: int, side: str) -> list[Pick]:
        return [p for p in self.picks if p.horizon == horizon and p.side == side]


@dataclass
class Contribution:
    event_id: str
    title: str
    target: str
    via: str
    strength: float
    impact_horizon: int
    rationale: str
    direction: int = 1  # of the impact: +1 theme intensifies, -1 eases


def trading_days_between(start: date, end: date) -> int:
    return int(np.busday_count(start, end))


def decay(age_td: int, horizon: int) -> float:
    """Linear fade over the impact's own horizon: a 5-day call is spent after 5 days."""
    return max(0.0, 1.0 - age_td / horizon)


def novelty_factor(novelty: float) -> float:
    return 0.4 + 0.6 * novelty


def expand(
    events: list[EventsFile],
    as_of: date,
    exposures: Exposures,
    meta: pd.DataFrame,
    sector_etfs: dict[tuple[str, str], str],
) -> dict[tuple[str, int], list[Contribution]]:
    """(ticker, horizon) -> contributions from all live impacts."""
    out: dict[tuple[str, int], list[Contribution]] = defaultdict(list)
    known = set(meta.index)
    for ef in events:
        age = trading_days_between(date.fromisoformat(ef.date), as_of)
        for ev in ef.events:
            nov = novelty_factor(ev.novelty)
            for imp in ev.impacts:
                d = decay(age, imp.horizon_days)
                if d <= 0:
                    continue
                base = imp.direction * imp.magnitude * imp.confidence * nov * d
                members: list[tuple[str, float, str]]
                if imp.kind == "theme":
                    theme = exposures.themes[imp.ref]
                    members = [(t, w, imp.ref) for t, w in theme.members.items()]
                elif imp.kind == "ticker":
                    members = [(imp.ref, 1.0, "direct")]
                else:
                    sector, _, region = imp.ref.partition("@")
                    members = [
                        (t, SECTOR_STOCK_WEIGHT, "sector")
                        for t in meta.index[(meta["sector"] == sector) & (meta["region"] == region)]
                        if meta.at[t, "kind"] == "stock"
                    ]
                    etf = sector_etfs.get((region, sector))
                    if etf:
                        members.append((etf, 1.0, "sector"))
                for ticker, weight, via in members:
                    if ticker not in known:
                        continue
                    for h in HORIZONS:
                        spill = (
                            1.0 if h == imp.horizon_days else CROSS_HORIZON[(imp.horizon_days, h)]
                        )
                        out[(ticker, h)].append(
                            Contribution(
                                ev.id, ev.title, imp.target, via, base * weight * spill,
                                imp.horizon_days, imp.rationale, imp.direction,
                            )
                        )  # fmt: skip
    return out


def geo_signal(contribs: list[Contribution]) -> float:
    return math.tanh(GEO_SCALE * sum(c.strength for c in contribs))


def candidates(
    contributions: dict[tuple[str, int], list[Contribution]], min_geo: float = MIN_GEO
) -> set[str]:
    return {t for (t, _), cs in contributions.items() if abs(geo_signal(cs)) >= min_geo}


def _primary_theme(contribs: list[Contribution]) -> str | None:
    by_via: dict[str, float] = defaultdict(float)
    for c in contribs:
        by_via[c.via] += abs(c.strength)
    return max(by_via, key=lambda k: by_via[k]) if by_via else None


def _prior(
    contribs: list[Contribution], priors: dict[str, dict[int, float]], h: int, side: int
) -> float:
    """Historical reaction of the themes behind this signal, scaled to [-1, 1].

    A theme prior is the members' sign-weighted excess return after the driver
    spiked in the theme's direction, so it is already "in the thesis direction"
    for winners and losers alike: positive = the move tended to continue. It only
    applies to contributions from an intensifying theme (direction +1) that push
    this ticker the same way as its overall signal. ``priors`` should hold only
    themes whose driver is spiking right now (see ``scoring.run.active_priors``).
    """
    total = sum(abs(c.strength) for c in contribs)
    if not total or not priors:
        return 0.0
    value = sum(
        abs(c.strength) / total * priors[c.via].get(h, 0.0) / 0.02
        for c in contribs
        if c.via in priors and c.direction == 1 and c.strength * side > 0
    )
    return max(-1.0, min(1.0, value))


def _risk(
    feat: dict[Any, Any], kind: str, next_earn: str | None, as_of: date, h: int, regime: str
) -> tuple[float, list[str]]:
    """Multiplier in (0, 1] and the reasons for it."""
    risk, notes = 1.0, []
    if kind == "stock":
        if next_earn is None:
            risk *= RISK["earnings_unknown"]
            notes.append("earnings date unknown")
        else:
            window_end = np.busday_offset(as_of, h, roll="forward").astype(date)
            if date.fromisoformat(next_earn) <= window_end:
                risk *= RISK["earnings_5d"] if h == 5 else RISK["earnings_20d"]
                notes.append(f"earnings {next_earn} inside the horizon")
    vol = feat.get("vol_annual")
    if vol is not None and math.isfinite(vol):
        if vol > 0.6:
            risk *= RISK["high_vol"]
            notes.append(f"high volatility ({vol:.0%} annualised)")
        if regime == "risk_off" and vol > 0.4:
            risk *= RISK["risk_off_vol"]
            notes.append("volatile stock in a risk-off market")
    return risk, notes


def _num(x: Any) -> float | None:
    return float(x) if x is not None and isinstance(x, int | float) and math.isfinite(x) else None


def score(
    as_of: date,
    events: list[EventsFile],
    exposures: Exposures,
    meta: pd.DataFrame,
    features: pd.DataFrame,
    sector_etfs: dict[tuple[str, str], str],
    regime: dict[str, Any],
    earnings: dict[str, str | None],
    priors: dict[str, dict[int, float]] | None = None,
    tracker: pd.DataFrame | None = None,
    tracker_weight: float = 0.0,
    trump_notes: dict[str, str] | None = None,
    insiders: pd.DataFrame | None = None,
    insider_weight: float = 0.0,
) -> PicksFile:
    """``meta``: index ticker, columns name, region, sector, kind ("stock" or "etf").

    ``tracker`` is the congressional-trades signal per ticker (``trackers.congress``).
    It can only scale an existing geopolitical signal, by up to ``tracker_weight``,
    which ``stt backtest`` sets from the tracker's historical record (0 = no edge).
    ``insiders`` (``trackers.insiders``) works the same way with ``insider_weight``.
    ``trump_notes`` (``trackers.trump.recent_notes``) are remarks only: a call that
    should move a stock reaches the score as an event impact in the analysis.
    """
    tracker_rows: dict[Any, dict[Any, Any]] = (
        tracker.to_dict("index") if tracker is not None else {}
    )
    insider_rows: dict[Any, dict[Any, Any]] = (
        insiders.to_dict("index") if insiders is not None else {}
    )
    contributions = expand(events, as_of, exposures, meta, sector_etfs)
    feature_rows: dict[Any, dict[Any, Any]] = features.to_dict("index")
    scored: list[Pick] = []
    for (ticker, h), contribs in contributions.items():
        geo = geo_signal(contribs)
        feat = feature_rows.get(ticker)
        if abs(geo) < MIN_GEO or feat is None or not bool(feat["liquid"]):
            continue
        side = 1 if geo > 0 else -1
        excess = feat["excess_5d"] if h == 5 else feat["excess_20d"]
        aligned = side * move_z(float(excess), float(feat["vol_daily"]), h)
        trend = max(-2.0, min(1.5, aligned)) / 1.5
        priced_in = min(1.0, max(0.0, aligned - 2.0) / 2.0)
        prior = _prior(contribs, priors or {}, h, side)
        kind = str(meta.at[ticker, "kind"])
        risk, risk_notes = _risk(
            feat, kind, earnings.get(ticker), as_of, h, regime.get("label", "")
        )
        if priced_in > 0:
            risk_notes.append(f"already moved {aligned:.1f} sigma in the thesis direction")
        congress = tracker_rows.get(ticker)
        tracker_score = math.tanh(float(congress["score"]) / 3) if congress else 0.0
        insider_row = insider_rows.get(ticker)
        insider_score = math.tanh(float(insider_row["score"]) / 3) if insider_row else 0.0
        conviction = (
            abs(geo)
            * (1 + WEIGHTS["trend"] * trend)
            * (1 + WEIGHTS["prior"] * prior)
            * (1 - WEIGHTS["priced_in"] * priced_in)
            * (1 + tracker_weight * side * tracker_score)
            * (1 + insider_weight * side * insider_score)
            * risk
        )
        if conviction < MIN_GEO / 2:
            continue
        top = sorted(contribs, key=lambda c: -abs(c.strength))[:3]
        scored.append(
            Pick(
                ticker=ticker,
                name=str(meta.at[ticker, "name"]),
                kind="etf" if kind == "etf" else "stock",
                region=str(meta.at[ticker, "region"]),
                sector=str(meta.at[ticker, "sector"]),
                horizon=h,
                side="long" if side > 0 else "avoid",
                rank=0,
                score=round(side * conviction, 4),
                conviction=round(conviction, 4),
                prob=round(0.5 + 0.25 * math.tanh(2 * conviction), 3),
                geo=round(geo, 4),
                prior=round(prior, 4),
                trend=round(trend, 4),
                priced_in=round(priced_in, 4),
                risk=round(risk, 4),
                risks=risk_notes,
                drivers=[
                    Driver(
                        event_id=c.event_id,
                        title=c.title,
                        target=c.target,
                        via=c.via,
                        strength=round(c.strength, 4),
                        rationale=c.rationale,
                    )
                    for c in top
                ],
                primary_theme=_primary_theme(contribs),
                last_close=float(feat["last"]),
                last_date=pd.Timestamp(feat["last_date"]).date().isoformat(),
                r5d=_num(feat["r5d"]),
                r20d=_num(feat["r20d"]),
                excess_5d=_num(feat["excess_5d"]),
                excess_20d=_num(feat["excess_20d"]),
                vol_annual=_num(feat["vol_annual"]),
                next_earnings=earnings.get(ticker),
                tracker=round(tracker_score, 3),
                tracker_note=_congress_note(congress),
                insider=round(insider_score, 3),
                insider_note=insider_note(insider_row),
                trump_note=(trump_notes or {}).get(ticker),
            )
        )
    picks = _select(scored)
    return PicksFile(
        date=as_of.isoformat(),
        model_version=MODEL_VERSION,
        weights=WEIGHTS,
        risk_multipliers=RISK,
        regime=regime,
        active_priors=priors or {},
        tracker_weight=tracker_weight,
        insider_weight=insider_weight,
        events_used=sorted({ev.id for ef in events for ev in ef.events}),
        picks=picks,
    )


def _congress_note(c: dict[Any, Any] | None) -> str | None:
    if not c:
        return None
    parts = []
    if c["buyers"]:
        parts.append(f"{c['buyers']} House member{'s' if c['buyers'] > 1 else ''} bought")
    if c["sellers"]:
        parts.append(f"{c['sellers']} sold")
    return " and ".join(parts) + " (disclosed in the last 30 days)" if parts else None


def _select(scored: list[Pick]) -> list[Pick]:
    """Top picks per horizon and side, spread across themes and companies."""
    chosen: list[Pick] = []
    for h in HORIZONS:
        for side, limit in (("long", MAX_PICKS), ("avoid", MAX_AVOID)):
            pool = sorted(
                (p for p in scored if p.horizon == h and p.side == side),
                key=lambda p: -p.conviction,
            )
            per_theme: dict[str | None, int] = defaultdict(int)
            names: set[str] = set()
            etfs = 0
            rank = 0
            for p in pool:
                company = p.name.split(" (")[0].lower()
                if company in names or per_theme[p.primary_theme] >= MAX_PER_THEME:
                    continue
                if p.kind == "etf" and etfs >= MAX_ETFS:
                    continue
                rank += 1
                p.rank = rank
                chosen.append(p)
                names.add(company)
                per_theme[p.primary_theme] += 1
                etfs += p.kind == "etf"
                if rank >= limit:
                    break
    return chosen


def load_events(
    events_dir: Any, as_of: date, lookback_days: int = LOOKBACK_DAYS
) -> list[EventsFile]:
    files = []
    for path in sorted(events_dir.glob("*.json")):
        d = date.fromisoformat(path.stem)
        if as_of - timedelta(days=lookback_days) <= d <= as_of:
            files.append(EventsFile.model_validate_json(path.read_text(encoding="utf-8")))
    return files
