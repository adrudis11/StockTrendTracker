"""The append-only pick ledger: every pick ever made, plus same-day baselines.

``data/ledger/picks.jsonl`` holds one line per pick. Lines are never edited or
removed, and re-running a day never replaces what was logged first, so the track
record can't be quietly improved after the fact (git history shows any tampering).

Baselines logged alongside the geopolitical picks, same day and horizons:

* ``momentum``: the liquid stocks with the strongest excess return over the horizon
  (longs) and the weakest (avoids). Answers "is this just momentum?"
* ``random``: liquid stocks drawn at random from the same sectors as the day's
  picks. Answers "is this just sector luck?"
* ``congress``: the stocks with the strongest net buying by House members disclosed
  in the last 30 days. Answers "does following Congress work?"
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel

from stocktrend.config import DATA_DIR
from stocktrend.scoring.model import HORIZONS, MAX_AVOID, MAX_PICKS, PicksFile

LEDGER = DATA_DIR / "ledger" / "picks.jsonl"

Strategy = Literal["geo", "momentum", "random", "congress"]


class LedgerEntry(BaseModel):
    pick_id: str
    date: str
    logged_at: datetime  # entry is the first market open after this moment
    strategy: Strategy
    horizon: int
    side: Literal["long", "avoid"]
    ticker: str
    region: str
    sector: str
    kind: str
    rank: int
    conviction: float | None = None
    prob: float | None = None
    model_version: str | None = None


def pick_id(day: str, strategy: str, horizon: int, side: str, ticker: str) -> str:
    return f"{day}:{strategy}:{horizon}:{side}:{ticker}"


def read(path: Path = LEDGER) -> list[LedgerEntry]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [LedgerEntry.model_validate_json(line) for line in f if line.strip()]


def append(entries: list[LedgerEntry], path: Path = LEDGER) -> int:
    """Append entries whose id isn't logged yet. Returns how many were written."""
    seen = {e.pick_id for e in read(path)}
    new = [e for e in entries if e.pick_id not in seen]
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for e in new:
                f.write(e.model_dump_json() + "\n")
    return len(new)


def geo_entries(picks: PicksFile, logged_at: datetime) -> list[LedgerEntry]:
    return [
        LedgerEntry(
            pick_id=pick_id(picks.date, "geo", p.horizon, p.side, p.ticker),
            date=picks.date,
            logged_at=logged_at,
            strategy="geo",
            horizon=p.horizon,
            side=p.side,
            ticker=p.ticker,
            region=p.region,
            sector=p.sector,
            kind=p.kind,
            rank=p.rank,
            conviction=p.conviction,
            prob=p.prob,
            model_version=picks.model_version,
        )
        for p in picks.picks
    ]


def _seed(*parts: object) -> int:
    return int(hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()[:12], 16)


def baseline_entries(
    picks: PicksFile,
    features: pd.DataFrame,
    meta: pd.DataFrame,
    logged_at: datetime,
    tracker: pd.DataFrame | None = None,
) -> list[LedgerEntry]:
    """Momentum, random-sector and congress baselines over liquid stocks (no ETFs)."""
    stocks = meta[meta["kind"] == "stock"].index
    feats = features[features.index.isin(stocks) & features["liquid"].astype(bool)]
    entries: list[LedgerEntry] = []

    def entry(strategy: Strategy, h: int, side: str, ticker: str, rank: int) -> LedgerEntry:
        return LedgerEntry(
            pick_id=pick_id(picks.date, strategy, h, side, ticker),
            date=picks.date,
            logged_at=logged_at,
            strategy=strategy,
            horizon=h,
            side="long" if side == "long" else "avoid",
            ticker=ticker,
            region=str(meta.at[ticker, "region"]),
            sector=str(meta.at[ticker, "sector"]),
            kind="stock",
            rank=rank,
        )

    for h in HORIZONS:
        col = "excess_5d" if h == 5 else "excess_20d"
        ranked = feats[col].dropna().sort_values(ascending=False)
        for side, tickers in (
            ("long", list(ranked.index[:MAX_PICKS])),
            ("avoid", list(ranked.index[::-1][:MAX_AVOID])),
        ):
            entries += [entry("momentum", h, side, t, i + 1) for i, t in enumerate(tickers)]

        for side, n in (("long", MAX_PICKS), ("avoid", MAX_AVOID)):
            sectors = {p.sector for p in picks.select(h, side) if p.kind == "stock"}
            pool = sorted(t for t in feats.index if not sectors or meta.at[t, "sector"] in sectors)
            rng = random.Random(_seed(picks.date, h, side))
            chosen = rng.sample(pool, min(n, len(pool)))
            entries += [entry("random", h, side, t, i + 1) for i, t in enumerate(chosen)]

        if tracker is not None and not tracker.empty:
            buys = tracker[(tracker["score"] > 0) & tracker.index.isin(feats.index)]
            top = list(buys.sort_values("score", ascending=False).index[:MAX_PICKS])
            entries += [entry("congress", h, "long", t, i + 1) for i, t in enumerate(top)]
    return entries


def record(
    picks: PicksFile,
    features: pd.DataFrame,
    meta: pd.DataFrame,
    logged_at: datetime | None = None,
    path: Path = LEDGER,
    tracker: pd.DataFrame | None = None,
) -> int:
    logged_at = logged_at or datetime.now(UTC)
    entries = geo_entries(picks, logged_at) + baseline_entries(
        picks, features, meta, logged_at, tracker
    )
    return append(entries, path)


def to_frame(entries: list[LedgerEntry]) -> pd.DataFrame:
    return pd.DataFrame([json.loads(e.model_dump_json()) for e in entries])
