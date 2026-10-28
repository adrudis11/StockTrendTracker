"""`stt report`: the daily Markdown report in ``data/reports/<date>.md``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from stocktrend.config import DATA_DIR
from stocktrend.events.digest import Digest
from stocktrend.events.schema import EventsFile
from stocktrend.markets import exchange_for
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
        "| # | Ticker | Name | Last close | Conviction | p(beat)* | 5d / 20d move | Main driver "
        "| Risks |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for p in picks:
        mark = " ★" if p.trump_note else ""
        close = _price(p.last_close, exchange_for(p.ticker).currency)
        lines.append(
            f"| {p.rank} | **{p.ticker}**{mark} | {p.name} | {close} | {p.conviction:.2f} | "
            f"{p.prob:.0%} | {_pct(p.r5d)} / {_pct(p.r20d)} | {_why(p)} | "
            f"{'; '.join(p.risks) or '–'} |"
        )
    lines.append("")
    trump = [f"- ★ **{p.ticker}**: {p.trump_note}" for p in picks if p.trump_note]
    return [*lines, *trump, ""] if trump else lines


def _details(picks: list[Pick]) -> list[str]:
    lines = []
    for p in picks:
        lines.append(
            f"- **{p.ticker}**: geo {p.geo:+.2f}, trend {p.trend:+.2f}, "
            f"priced-in {p.priced_in:.2f}, risk ×{p.risk:.2f}"
        )
        if p.tracker_note:
            lines.append(f"  - Congress: {p.tracker_note} (tracker {p.tracker:+.2f})")
        if p.insider_note:
            lines.append(f"  - Insiders: {p.insider_note} (tracker {p.insider:+.2f})")
        if p.trump_note:
            lines.append(f"  - {p.trump_note} (a remark: no weight in the score)")
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
    congress_md: str | None = None,
    trump_md: str | None = None,
    insiders_md: str | None = None,
    open_md: str | None = None,
    results_md: str | None = None,
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

    if open_md:
        lines += [open_md]
    if trump_md:
        lines += [trump_md]
    if track_record:
        lines += [track_record]
    if results_md:
        lines += [results_md]
    if congress_md:
        lines += [congress_md]
    if insiders_md:
        lines += [insiders_md]
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


def congress_markdown(rows: list[dict[str, Any]], weight: float) -> str:
    lines = ["## Congress trades (disclosed in the last 30 days)", ""]
    if not rows:
        return "\n".join(
            [*lines, "_No stock trades disclosed by House members in the window._", ""]
        )
    lines += [
        "| Ticker | Name | Score | Buyers | Sellers | Price then (est.) | Now | Since | Members |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['ticker']} | {r['name']} | {r['score']:+.2f} | {r['buyers']} | "
            f"{r['sellers']} | {_price(r['price_then'], 'USD')} | {_price(r['now'], 'USD')} | "
            f"{_num(r['since'])} | {r['members']} |"
        )
    as_of = max((r["price_date"] for r in rows if r.get("price_date")), default=None)
    note = _weight_note(weight)
    return "\n".join(
        [
            *lines,
            "",
            f"Official House filings (STOCK Act; trades are reported up to 45 days late). "
            "They give amount ranges, not prices, so Price then is an estimate: the close on "
            "each trade date, weighted by the range's midpoint, over their purchases for net "
            f"buying and their sales for net selling. Now: the close on "
            f"{as_of or 'the last trading day'}; Since: the move from Price then. {note}",
            "",
        ]
    )


def _price(x: float | None, currency: str) -> str:
    """A price in its listing's currency: $12.50, €12.50, 1,250p, 12.50 CHF."""
    if not x:
        return "–"
    return {"USD": f"${x:,.2f}", "EUR": f"€{x:,.2f}", "GBp": f"{x:,.0f}p"}.get(
        currency, f"{x:,.2f} {currency}"
    )


def _weight_note(weight: float) -> str:
    if weight:
        return (
            f"Weight in the score: up to ±{100 * weight:.0f}% on stocks the day's events "
            "already point at."
        )
    return (
        "Weight in the score: 0 (its backtest hasn't shown an edge); logged as its own "
        "strategy so the track record can prove otherwise."
    )


def insiders_markdown(rows: list[dict[str, Any]], weight: float) -> str:
    """Insider buying from SEC Form 4s (``report.dashboard.insider_rows``)."""
    lines = ["## Insider buying (SEC Form 4, last 30 days)", ""]
    if not rows:
        return "\n".join(
            [*lines, "_No open-market purchases by insiders filed in the window._", ""]
        )
    lines += [
        "| Ticker | Name | Score | Buyers | Bought | Paid (avg) | Now | Since | Sellers | "
        "Who bought |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        bought = r["bought"]
        money = f"${bought / 1e6:.1f}M" if bought >= 1e6 else f"${bought / 1e3:.0f}k"
        lines.append(
            f"| {r['ticker']} | {r['name']} | {r['score']:.1f} | {r['buyers']} | {money} | "
            f"{_price(r['paid'], 'USD')} | {_price(r['now'], 'USD')} | {_num(r['since'])} | "
            f"{r['sellers']} | {r['insiders']} |"
        )
    as_of = max((r["price_date"] for r in rows if r.get("price_date")), default=None)
    return "\n".join(
        [
            *lines,
            "",
            "Open-market purchases by officers, directors and 10% owners of S&P 500 companies, "
            "from their Form 4 filings (due within two business days). Paid: their average "
            f"price per share; Now: the close on {as_of or 'the last trading day'}; Since: the "
            "move from their price. Sales are counted but don't lower the score: insiders "
            f"sell for many reasons. {_weight_note(weight)}",
            "",
        ]
    )


def _num(x: float | None) -> str:
    return "–" if x is None else f"{x:+.1f}%"


def trump_markdown(rows: list[dict[str, Any]]) -> str:
    """The Trump tracker: his recent stock calls and whether they came true (``trump_rows``)."""
    lines = ["## Trump tracker (calls in the last 90 days)", ""]
    if not rows:
        return "\n".join([*lines, "_No stock or market calls by Trump logged in the window._", ""])
    lines += [
        "| Said | Ticker | Company | Call | Quote | Price then | Now | Since | Day move† | "
        "vs benchmark† | Came true? |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        call = r["stance"] + (" (explicit)" if r["explicit"] else "")
        quote = r["quote"].replace("|", "/")
        cur = r.get("currency", "USD")
        lines.append(
            f"| {r['said_on']} | **{r['ticker']}** | {r['company']} | {call} | {quote} | "
            f"{_price(r['price_then'], cur)} | {_price(r['now'], cur)} | {_num(r['since'])} | "
            f"{_num(r['reaction'])} | {_num(r['excess'])} | {r['came_true'] or 'open'} |"
        )
    as_of = max((r["price_date"] for r in rows if r.get("price_date")), default=None)
    scored = [r for r in rows if r["came_true"]]
    hits = sum(r["came_true"] == "Yes" for r in scored)
    record = (
        f"So far {hits} of {len(scored)} came true (a bullish call ahead of its benchmark, "
        "or a bearish one behind it)."
        if scored
        else "None has traded since it was logged yet."
    )
    return "\n".join(
        [
            *lines,
            "",
            f"{record} Price then: the close before he said it; Now: the close on "
            f"{as_of or 'the last trading day'}; Since: the move between them. † Day move: "
            "the stock on the day he said it, minus its benchmark. vs benchmark: from the "
            "first open after we logged the call to now, minus the benchmark (in euros for "
            'European listings); ahead of it (behind it for an attack) counts as "came '
            'true"; whole-market calls count in absolute terms. Calls carry no weight in '
            "the score; each is logged as strategy `trump` so the track record shows whether "
            "following them works.",
            "",
        ]
    )


def open_picks_markdown(rows: list[dict[str, Any]]) -> str:
    """Earlier picks still inside their horizon (``report.dashboard.open_pick_rows``)."""
    lines = ["## Open picks (earlier days, still inside their horizon)", ""]
    if not rows:
        return "\n".join([*lines, "_No picks from earlier days are open._", ""])
    entered = [r for r in rows if r["on_track"]]
    on_track = sum(r["on_track"] == "Yes" for r in entered)
    lines += [
        f"{len(rows)} open, {len(entered)} traded since their entry; {on_track} on track "
        "(ahead of the benchmark, or behind it for an avoid).",
        "",
        "<details><summary>Every open pick</summary>",
        "",
        "| Picked | Ticker | Name | Horizon | Side | Entry | Now | Since | vs benchmark | Ends |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        cur = r["currency"]
        lines.append(
            f"| {r['date']} | **{r['ticker']}** | {r['name']} | {r['horizon']}d | {r['side']} | "
            f"{_price(r['entry'], cur)} | {_price(r['now'], cur)} | {_num(r['since'])} | "
            f"{_num(r['vs_benchmark'])} | {r['ends']} |"
        )
    as_of = max((r["price_date"] for r in rows if r.get("price_date")), default=None)
    return "\n".join(
        [
            *lines,
            "",
            "</details>",
            "",
            "Entry: the open of the first session after the pick was made (what the track "
            f"record scores from); Now: the close on {as_of or 'the last trading day'}; Since: "
            "the move between them, in the listing's currency. vs benchmark: the excess return "
            "so far, as the track record will score it (positive = the call is working, for "
            "avoids too; in euros for European listings). Ends: the last session of its "
            "horizon.",
            "",
        ]
    )


def results_markdown(rows: list[dict[str, Any]]) -> str | None:
    """The latest matured picks under the track record (``report.dashboard.result_rows``)."""
    if not rows:
        return None
    lines = [
        "<details><summary>Latest matured picks</summary>",
        "",
        "| Picked | Ticker | Name | Horizon | Side | Entry | Exit | Result | vs benchmark | "
        "Correct? | Now | Since entry |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        cur = r["currency"]
        lines.append(
            f"| {r['date']} | **{r['ticker']}** | {r['name']} | {r['horizon']}d | {r['side']} | "
            f"{_price(r['entry'], cur)} | {_price(r['exit'], cur)} ({r['exit_date']}) | "
            f"{_num(r['result'])} | {_num(r['vs_benchmark'])} | {r['correct']} | "
            f"{_price(r['now'], cur)} | {_num(r['since'])} |"
        )
    as_of = max((r["price_date"] for r in rows if r.get("price_date")), default=None)
    return "\n".join(
        [
            *lines,
            "",
            "</details>",
            "",
            "Entry: the open after the pick was made; Exit: the close on the last day of its "
            "horizon; Result: the move between them, in the listing's currency. vs benchmark "
            "and Correct?: as scored (in euros for European listings; for avoids, positive "
            f"means it lagged as called). Now: the close on {as_of or 'the last trading day'}; "
            "Since entry: the move from the entry to now, to show what happened after.",
            "",
        ]
    )
