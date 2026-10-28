"""`stt dashboard`: the JSON datasets behind the claude.ai dashboard.

Each run writes one file per dataset to ``cache/dashboard/``; the daily session
uploads them to the dashboard (see ``prompts/daily_run.md``). Percentages are
stored as percent numbers (11.0 = 11%) so the page shows exactly what the data
holds.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stocktrend.config import CACHE_DIR
from stocktrend.events.schema import EventsFile
from stocktrend.events.validate import events_path
from stocktrend.scoring.run import load as load_picks
from stocktrend.tracking import scoreboard
from stocktrend.tracking.evaluate import first_matures, read_outcomes
from stocktrend.tracking.ledger import read as read_ledger

OUT_DIR = CACHE_DIR / "dashboard"
DATASETS = ("summary", "picks", "events", "scoreboard")
REGIME_NAMES = {"risk_on": "Risk-on", "neutral": "Neutral", "risk_off": "Risk-off"}


def _pct(x: float | None, digits: int = 1) -> float | None:
    return None if x is None else round(100 * x, digits)


def _theme(name: str | None) -> str:
    if not name:
        return ""
    return {"direct": "Company-specific", "sector": "Sector-wide"}.get(
        name, name.replace("_", " ").capitalize()
    )


def build(day: str) -> dict[str, list[dict[str, Any]]]:
    picks = load_picks(day)
    path = events_path(day)
    events = (
        EventsFile.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None
    )
    entries = read_ledger()
    outcomes = read_outcomes()
    sb = scoreboard.compute(entries, outcomes)
    done = {o.pick_id for o in outcomes}
    first = first_matures([e for e in entries if e.strategy == "geo" and e.pick_id not in done])
    r = picks.regime

    summary = {
        "date": picks.date,
        "regime": REGIME_NAMES.get(r.get("label", ""), r.get("label", "")),
        "vix": r.get("vix"),
        "spy_r20d": _pct(r.get("spy_r20d")),
        "stoxx_r20d": _pct(r.get("stoxx600_r20d")),
        "brent_r20d": _pct(r.get("brent_r20d")),
        "ttf_r20d": _pct(r.get("ttf_r20d")),
        "us10y": r.get("us10y"),
        "market_view": events.market_view if events else "",
        "events": len(events.events) if events else 0,
        "picks_5d": len(picks.select(5, "long")),
        "picks_20d": len(picks.select(20, "long")),
        "matured": sb["matured"],
        "open": sb["open"],
        "first_results": first.isoformat() if first else None,
        "model": picks.model_version,
    }
    pick_rows = [
        {
            "key": f"{p.horizon}:{p.side}:{p.ticker}",
            "horizon": p.horizon,
            "side": p.side,
            "rank": p.rank,
            "ticker": p.ticker,
            "name": p.name,
            "region": p.region,
            "conviction": round(p.conviction, 2),
            "prob": _pct(p.prob, 0),
            "r5d": _pct(p.r5d),
            "r20d": _pct(p.r20d),
            "driver": p.drivers[0].title if p.drivers else "",
            "theme": _theme(p.primary_theme),
            "risks": "; ".join(p.risks),
        }
        for p in picks.picks
    ]
    event_rows = [
        {
            "id": ev.id,
            "title": ev.title,
            "category": ev.category.replace("_", " "),
            "novelty": ev.novelty,
            "certainty": ev.certainty,
            "targets": ", ".join(
                f"{i.target.split(':', 1)[1].replace('_', ' ')} {'↑' if i.direction > 0 else '↓'}"
                for i in ev.impacts
            ),
        }
        for ev in (events.events if events else [])
    ]
    board_rows = [
        {
            "key": f"{row['strategy']}:{row['horizon']}:{row['side']}",
            "strategy": {"geo": "Geopolitical", "momentum": "Momentum", "random": "Random"}[
                row["strategy"]
            ],
            "horizon": row["horizon"],
            "side": row["side"],
            "n": row["n"],
            "hit_rate": _pct(row["hit_rate"], 0),
            "mean_excess": _pct(row["mean_excess"], 2),
            "t_stat": row["t_stat"],
        }
        for row in sb["rows"]
    ]
    return {
        "summary": [summary],
        "picks": pick_rows,
        "events": event_rows,
        "scoreboard": board_rows,
    }


def write(day: str, out_dir: Path = OUT_DIR) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, rows in build(day).items():
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        paths[name] = path
    return paths
