"""`stt report`: the daily Markdown report in ``data/reports/<date>.md``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from stocktrend.config import DATA_DIR
from stocktrend.events.digest import Digest
from stocktrend.events.schema import EventsFile
from stocktrend.scoring.model import Pick, PicksFile

DISCLAIMER = (
    "> Research tool for personal use, not investment advice. Probabilities are "
    "**uncalibrated** estimates until enough picks have matured to measure them."
)
REGIME_SERIES = (
    ("spy_r20d", "S&P 500"),
    ("stoxx600_r20d", "STOXX 600"),
    ("brent_r20d", "Brent"),
    ("ttf_r20d", "EU gas"),
    ("gold_r20d", "gold"),
)
HORIZON_LABEL = {5: "next ~1 week (5 trading days)", 20: "next ~1 month (20 trading days)"}


def report_path(day: str) -> Path:
    return DATA_DIR / "reports" / f"{day}.md"


def _pct(x: float | None, signed: bool = True) -> str:
    if x is None:
        return "–"
    return f"{100 * x:+.1f}%" if signed else f"{100 * x:.0f}%"


def _regime(r: dict[str, Any]) -> str:
    parts = [f"**Regime: {r.get('label', '?').replace('_', '-')}**"]
    if "vix" in r:
        parts.append(f"VIX {r['vix']:.1f} ({r.get('vix_percentile_1y', 0):.0%} of the past year)")
    for key, label in REGIME_SERIES:
        if r.get(key) is not None:
            parts.append(f"{label} {_pct(r[key])} (20d)")
    if "us10y" in r:
        parts.append(f"US 10y {r['us10y']:.2f}% ({r.get('us10y_change_20d_bp', 0):+.0f} bp, 20d)")
    return " · ".join(parts)


def _why(p: Pick) -> str:
    d = p.drivers[0]
    via = f"theme `{d.via}`" if d.via not in ("direct", "sector") else d.via
    return f"{d.title} → {via}"


def _pick_table(picks: list[Pick]) -> list[str]:
    if not picks:
        return ["_No picks cleared the bar for this horizon._", ""]
    lines = [
        "| # | Ticker | Name | Conviction | p(beat)* | 5d / 20d move | Main driver | Risks |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for p in picks:
        lines.append(
            f"| {p.rank} | **{p.ticker}** | {p.name} | {p.conviction:.2f} | {p.prob:.0%} | "
            f"{_pct(p.r5d)} / {_pct(p.r20d)} | {_why(p)} | {'; '.join(p.risks) or '–'} |"
        )
    return [*lines, ""]


def _details(picks: list[Pick]) -> list[str]:
    lines = []
    for p in picks:
        lines.append(
            f"- **{p.ticker}**: geo {p.geo:+.2f}, trend {p.trend:+.2f}, "
            f"priced-in {p.priced_in:.2f}, risk ×{p.risk:.2f}"
        )
        for d in p.drivers:
            via = f"theme {d.via}" if d.via not in ("direct", "sector") else d.via
            lines.append(f"  - `{d.event_id}` via {via} ({d.strength:+.3f}): {d.rationale}")
    return [*lines, ""] if lines else []


def _sources(stories: dict[str, Any], ids: list[str]) -> str:
    links = []
    for sid in ids[:2]:
        s = stories.get(sid)
        if s is not None:
            links.append(f"[{sid}]({s.urls[0]})")
    return ", ".join(links)


def render(
    picks: PicksFile,
    events: EventsFile | None,
    digest: Digest | None,
    track_record: str | None = None,
) -> str:
    lines = [f"# StockTrendTracker: {picks.date}", "", DISCLAIMER, ""]
    lines += ["## Backdrop", ""]
    if events and events.market_view:
        lines += [events.market_view, ""]
    lines += [_regime(picks.regime), ""]

    for h in (5, 20):
        longs, avoids = picks.select(h, "long"), picks.select(h, "avoid")
        lines += [f"## Picks: {HORIZON_LABEL[h]}", ""]
        lines += _pick_table(longs)
        if avoids:
            lines += [f"**Avoid / likely to lag ({h}d):** " + ", ".join(
                f"{p.ticker} ({p.conviction:.2f}: {p.drivers[0].title})" for p in avoids
            ), ""]  # fmt: skip
        details = _details(longs + avoids)
        if details:
            lines += ["<details><summary>How these scores were built</summary>", "", *details,
                      "</details>", ""]  # fmt: skip
    lines += [
        "\\* p(beat) = estimated chance of beating the benchmark (S&P 500 for US listings, "
        "STOXX Europe 600 for European ones) over the horizon. Uncalibrated.",
        "",
    ]

    if track_record:
        lines += [track_record]
    if events:
        stories = {s.id: s for s in digest.stories} if digest else {}
        lines += ["## Today's events", ""]
        lines += ["| Event | Category | New? | Certainty | Targets | Sources |",
                  "|---|---|---|---|---|---|"]  # fmt: skip
        for ev in events.events:
            targets = ", ".join(
                f"{i.target.split(':', 1)[1]} {'↑' if i.direction > 0 else '↓'}" for i in ev.impacts
            )
            lines.append(
                f"| {ev.title} | {ev.category} | {ev.novelty:.1f} | {ev.certainty} | {targets} | "
                f"{_sources(stories, ev.sources)} |"
            )
        lines.append("")
    lines += [
        "---",
        f"Model {picks.model_version} · events considered: {len(picks.events_used)} "
        f"(last 30 days, fading over each call's horizon) · "
        "[method](../../docs/PLAN.md#3-architecture)",
        "",
    ]
    return "\n".join(lines)


def write(day: str, text: str) -> Path:
    path = report_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
