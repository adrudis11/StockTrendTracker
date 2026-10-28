"""Polymarket: crowd-priced odds for political, geopolitical and economic events.

Prediction markets are usually well calibrated, so they help the analyst judge how
likely an event is and how much is priced in; a big one-day move in the odds is
news in itself. Read-only public API, no account needed.
"""

from __future__ import annotations

import json
from typing import Any

import requests

from stocktrend.http import DEFAULT_TIMEOUT
from stocktrend.ingest.models import SourceResult

API_URL = "https://gamma-api.polymarket.com/events"
EVENT_URL = "https://polymarket.com/event/{slug}"


def _floats(value: Any) -> list[float]:
    """Polymarket sends lists like outcomePrices as JSON-encoded strings."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    try:
        return [float(v) for v in value or []]
    except (TypeError, ValueError):
        return []


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return [str(v) for v in value or []]


def open_markets(event: dict[str, Any], per_event: int) -> list[dict[str, Any]]:
    """The event's most-traded markets that are still undecided (2-98%)."""
    rows = []
    for m in event.get("markets") or []:
        if m.get("closed") or not m.get("active", True):
            continue
        prices = _floats(m.get("outcomePrices"))
        outcomes = _strings(m.get("outcomes"))
        if not prices:
            continue
        p = prices[0]
        if not 0.02 < p < 0.98:
            continue
        rows.append(
            {
                "question": m.get("question") or event.get("title"),
                "outcome": outcomes[0] if outcomes else "Yes",
                "probability": round(p, 3),
                "change_1d": m.get("oneDayPriceChange"),
                "volume_24h": round(float(m.get("volume24hr") or 0)),
                "end_date": (m.get("endDate") or "")[:10] or None,
            }
        )
    rows.sort(key=lambda r: -r["volume_24h"])
    return rows[:per_event]


def fetch(http: requests.Session, cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], SourceResult]:
    result = SourceResult("polymarket")
    events: dict[str, dict[str, Any]] = {}
    try:
        for tag in cfg["tags"]:
            r = http.get(
                API_URL,
                params={
                    "tag_slug": tag,
                    "active": "true",
                    "closed": "false",
                    "order": "volume24hr",
                    "ascending": "false",
                    "limit": str(cfg.get("per_tag", 20)),
                },
                timeout=DEFAULT_TIMEOUT,
            )
            r.raise_for_status()
            for item in r.json():
                events.setdefault(str(item.get("id")), {**item, "tag": tag})
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"[:200]
        return [], result

    ranked = sorted(events.values(), key=lambda ev: -float(ev.get("volume24hr") or 0))
    rows = []
    for ev in ranked:
        markets = open_markets(ev, cfg.get("markets_per_event", 3))
        if not markets:
            continue
        rows.append(
            {
                "event": ev.get("title"),
                "tag": ev["tag"],
                "url": EVENT_URL.format(slug=ev.get("slug", "")),
                "volume_24h": round(float(ev.get("volume24hr") or 0)),
                "markets": markets,
            }
        )
        if len(rows) >= cfg.get("max_events", 25):
            break
    result.items = len(rows)
    return rows, result
