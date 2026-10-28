"""`stt dashboard`: the JSON datasets behind the claude.ai dashboard.

Each run writes one file per dataset to ``cache/dashboard/``; the daily session
uploads them to the dashboard (see ``prompts/daily_run.md``). Percentages are
stored as percent numbers (11.0 = 11%) so the page shows exactly what the data
holds. The dashboard can't load a file with no rows, so the summary row counts every
other dataset and the page only reads one that has rows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stocktrend.config import CACHE_DIR
from stocktrend.events.schema import EventsFile
from stocktrend.events.validate import events_path
from stocktrend.markets import exchange_for
from stocktrend.scoring.run import load as load_picks
from stocktrend.tracking import scoreboard
from stocktrend.tracking.evaluate import first_matures, read_outcomes
from stocktrend.tracking.ledger import read as read_ledger

OUT_DIR = CACHE_DIR / "dashboard"
DATASETS = (
    "summary", "picks", "open_picks", "results", "events", "scoreboard", "congress", "insiders",
    "trump",
)  # fmt: skip
RESULT_ROWS = 60
CONGRESS_ROWS = INSIDER_ROWS = 15
TRUMP_DAYS, TRUMP_ROWS = 90, 25
STANCE_NAMES = {"buy": "Buy", "positive": "Talked up", "negative": "Attacked", "sell": "Sell"}
STRATEGY_NAMES = {
    "geo": "Geopolitical",
    "momentum": "Momentum",
    "random": "Random",
    "congress": "Congress",
    "insiders": "Insiders",
    "trump": "Trump",
}
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
    trump = trump_rows(day)
    scored = [t for t in trump if t["came_true"]]
    congress = congress_rows(day)
    insider = insider_rows(day)
    open_picks = open_pick_rows(day)
    results = result_rows(day)

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
        "n_picks": len(picks.picks),
        "n_open": len(open_picks),
        "n_results": len(results),
        "open_on_track": sum(r["on_track"] == "Yes" for r in open_picks),
        "open_entered": sum(bool(r["on_track"]) for r in open_picks),
        "n_congress": len(congress),
        "n_insiders": len(insider),
        "prices_as_of": max(
            (r["price_date"] for r in congress + insider + trump if r["price_date"]),
            default=None,
        ),
        "matured": sb["matured"],
        "open": sb["open"],
        "first_results": first.isoformat() if first else None,
        "model": picks.model_version,
        "tracker_weight": round(100 * picks.tracker_weight),
        "insider_weight": round(100 * picks.insider_weight),
        "trump_calls": len(trump),
        "trump_scored": len(scored),
        "trump_true": sum(t["came_true"] == "Yes" for t in scored),
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
            "last_close": round(p.last_close, 2),
            "currency": exchange_for(p.ticker).currency,
            "driver": p.drivers[0].title if p.drivers else "",
            "theme": _theme(p.primary_theme),
            "risks": "; ".join(
                p.risks + [n for n in (p.tracker_note, p.insider_note, p.trump_note) if n]
            ),
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
            "strategy": STRATEGY_NAMES.get(row["strategy"], row["strategy"]),
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
        "open_picks": open_picks,
        "results": results,
        "events": event_rows,
        "scoreboard": board_rows,
        "congress": congress,
        "insiders": insider,
        "trump": trump,
    }


def open_pick_rows(day: str) -> list[dict[str, Any]]:
    """Picks from earlier days still inside their horizon, at the latest close.

    ``entry`` is the open the pick is scored from (the first session after it was made),
    ``now`` the latest close and ``since`` the move between them, unadjusted and in the
    listing's currency. ``vs_benchmark`` is the excess return as the track record will
    score it (positive = the call is working, for avoids too); ``on_track`` says so.
    """
    from stocktrend.config import PRICES_PARQUET
    from stocktrend.ingest import prices as price_store
    from stocktrend.scoring.run import build_meta
    from stocktrend.tracking.evaluate import PriceBook, mark
    from stocktrend.universe.build import load as load_universe
    from stocktrend.universe.instruments import load_instruments

    done = {o.pick_id for o in read_outcomes()}
    entries = [
        e for e in read_ledger() if e.strategy == "geo" and e.date < day and e.pick_id not in done
    ]
    if not entries or not PRICES_PARQUET.exists():
        return []
    cfg = load_instruments()
    names = build_meta(load_universe(), cfg)["name"].to_dict()
    book = PriceBook(price_store.load())
    rows = []
    # Newest picks first; within a day 1-week before 1-month, longs before avoids, by rank.
    entries.sort(key=lambda e: (e.horizon, e.side != "long", e.rank))
    entries.sort(key=lambda e: e.date, reverse=True)
    for e in entries:
        m = mark(e, book, cfg.benchmarks)
        entry, now = m.entry_open, m.last_close
        rows.append(
            {
                "key": e.pick_id,
                "date": e.date,
                "horizon": e.horizon,
                "side": e.side,
                "rank": e.rank,
                "ticker": e.ticker,
                "name": str(names.get(e.ticker, "")),
                "currency": exchange_for(e.ticker).currency,
                "entry": round(entry, 2) if entry else None,
                "entry_date": m.entry_date.isoformat(),
                "now": round(now, 2) if now else None,
                "since": _pct(now / entry - 1) if entry and now else None,
                "vs_benchmark": _pct(m.excess),
                "on_track": "" if m.excess is None else ("Yes" if m.excess > 0 else "No"),
                "ends": m.exit_date.isoformat(),
                "price_date": m.last_date.isoformat() if m.last_date else None,
            }
        )
    return rows


def result_rows(day: str, limit: int = RESULT_ROWS) -> list[dict[str, Any]]:
    """The latest matured picks, as the track record scored them, newest exit first.

    ``entry`` and ``exit`` are the open and close the score used, ``result`` the move
    between them, in the listing's currency and unadjusted when the price cache has
    those days (else the scored, dividend-adjusted prices). ``vs_benchmark`` and
    ``correct`` are the scored excess return and verdict. ``now`` is the latest close
    and ``since`` the move from the entry, to show what happened after.
    """
    import pandas as pd

    from stocktrend.config import PRICES_PARQUET
    from stocktrend.ingest import prices as price_store
    from stocktrend.scoring.run import build_meta
    from stocktrend.tracking.evaluate import PriceBook
    from stocktrend.universe.build import load as load_universe
    from stocktrend.universe.instruments import load_instruments

    entries = {e.pick_id: e for e in read_ledger() if e.strategy == "geo" and e.date < day}
    scored = [o for o in read_outcomes() if o.pick_id in entries and o.exit_date < day]
    if not scored:
        return []
    names = build_meta(load_universe(), load_instruments())["name"].to_dict()
    no_prices = pd.DataFrame(columns=price_store.PRICE_COLUMNS)
    book = PriceBook(price_store.load() if PRICES_PARQUET.exists() else no_prices)
    scored.sort(key=lambda o: (o.exit_date, o.pick_id), reverse=True)
    return [_result_row(entries[o.pick_id], o, book, names) for o in scored[:limit]]


def _result_row(e: Any, o: Any, book: Any, names: dict[Any, Any]) -> dict[str, Any]:
    import pandas as pd

    bars = book.by_ticker.get(e.ticker)

    def raw(day: str, col: str, fallback: float) -> float:
        t = pd.Timestamp(day)
        if bars is not None and t in bars.index and pd.notna(bars.at[t, col]):
            return float(bars.at[t, col])
        return fallback

    entry, exit_ = raw(o.entry_date, "open", o.entry_price), raw(o.exit_date, "close", o.exit_price)
    last = book.last_date(e.ticker)
    now = book.close_on_or_before(e.ticker, last) if last is not None else None
    return {
        "key": e.pick_id,
        "date": e.date,
        "horizon": e.horizon,
        "side": e.side,
        "ticker": e.ticker,
        "name": str(names.get(e.ticker, "")),
        "currency": exchange_for(e.ticker).currency,
        "entry": round(entry, 2),
        "exit": round(exit_, 2),
        "exit_date": o.exit_date,
        "result": _pct(exit_ / entry - 1),
        "vs_benchmark": _pct(o.excess),
        "correct": "Yes" if o.correct else "No",
        "now": None if now is None else round(now, 2),
        "since": None if now is None else _pct(now / entry - 1),
        "price_date": None if last is None else last.date().isoformat(),
    }


def congress_rows(day: str) -> list[dict[str, Any]]:
    """The strongest net buying and selling by House members, disclosed in 30 days."""
    from datetime import date

    from stocktrend.trackers import congress, insiders
    from stocktrend.universe.build import load as load_universe

    names = dict(zip(*(load_universe()[c] for c in ("ticker", "name")), strict=True))
    trades, as_of = congress.load_trades(), date.fromisoformat(day)
    sig = congress.signal(trades, as_of)
    if sig.empty:
        return []
    sig = sig.reindex(sig["score"].abs().sort_values(ascending=False).index).head(CONGRESS_ROWS)
    window = congress.recent(trades, as_of)
    closes = _closes(list(sig.index), day)
    sec_names = insiders.company_names()  # for stocks outside our universe
    rows = []
    for t, r in sig.iterrows():
        # House filings give amount ranges, not prices: estimate the price as the close on
        # each trade date, weighted by the range's midpoint. Buys for net buying, else sales.
        sides = ("purchase",) if r["score"] > 0 else ("sale", "partial_sale")
        g = window[(window["ticker"] == t) & window["type"].isin(sides)]
        series = closes.get(str(t))
        sizes = [congress.amount_mid(lo, hi) for lo, hi in zip(g["amount_low"], g["amount_high"],
                                                               strict=True)]  # fmt: skip
        then = _weighted_close(series, list(g["tx_date"]), sizes)
        now, price_date = _last(series)
        name = (
            names.get(t)
            or sec_names.get(str(t))
            or (congress.asset_name(str(g["asset"].iloc[0])) if len(g) else "")
        )
        rows.append(
            {
                "ticker": t,
                "name": name,
                "score": round(float(r["score"]), 2),
                "buyers": int(r["buyers"]),
                "sellers": int(r["sellers"]),
                "price_then": then,
                "now": now,
                "since": _pct(now / then - 1) if now and then else None,
                "price_date": price_date,
                "members": r["members"],
                "last_filed": str(r["last_filed"]),
            }
        )
    return rows


def insider_rows(day: str) -> list[dict[str, Any]]:
    """The strongest open-market buying by company insiders, Form 4s of the last 30 days,
    with what they paid and the latest close (``since``: the move from their price)."""
    from datetime import date

    from stocktrend.trackers import insiders
    from stocktrend.universe.build import load as load_universe

    names = dict(zip(*(load_universe()[c] for c in ("ticker", "name")), strict=True))
    sig = insiders.signal(insiders.load_trades(), date.fromisoformat(day))
    sig = sig[sig["score"] > 0].head(INSIDER_ROWS)
    closes = _closes(list(sig.index), day)
    rows = []
    for t, r in sig.iterrows():
        paid = _float(r["avg_price"])
        now, as_of = _last(closes.get(str(t)))
        rows.append(
            {
                "ticker": t,
                "name": names.get(t, ""),
                "score": round(float(r["score"]), 2),
                "buyers": int(r["buyers"]),
                "bought": round(float(r["bought"])),
                "paid": paid,
                "last_buy": str(r["last_buy"]),
                "now": now,
                "since": _pct(now / paid - 1) if now and paid else None,
                "price_date": as_of,
                "sellers": int(r["sellers"]),
                "insiders": _first(str(r["insiders"]).split("; "), 3),
                "last_filed": str(r["last_filed"]),
            }
        )
    return rows


def _closes(tickers: list[str], day: str) -> dict[str, Any]:
    """ticker -> its daily closes (a Series by date) before ``day``, from the price cache
    and the tracker-stock cache; empty without them. Raw closes, comparable with the
    prices people traded at."""
    import pandas as pd

    from stocktrend.config import PRICES_PARQUET
    from stocktrend.ingest import prices as price_store

    paths = [p for p in (price_store.TRACKER_PRICES, PRICES_PARQUET) if p.exists()]
    if not paths:
        return {}
    px = pd.concat([price_store.load(p) for p in paths]).drop_duplicates(["date", "ticker"])
    px = px[px["ticker"].isin(tickers) & (px["date"] < pd.Timestamp(day))]
    return {
        str(t): g.set_index("date")["close"].dropna().sort_index() for t, g in px.groupby("ticker")
    }


def _last(series: Any) -> tuple[float | None, str | None]:
    """The latest close and its date."""
    if series is None or series.empty:
        return None, None
    return round(float(series.iloc[-1]), 2), series.index[-1].date().isoformat()


def _weighted_close(series: Any, days: list[Any], weights: list[float]) -> float | None:
    """Average close on ``days`` (the last close on or before each), weighted."""
    import pandas as pd

    if series is None or series.empty or not days:
        return None
    px = [series.asof(pd.Timestamp(d)) for d in days]
    pairs = [(p, w) for p, w in zip(px, weights, strict=True) if pd.notna(p)]
    total = sum(w for _, w in pairs)
    return round(sum(float(p) * w for p, w in pairs) / total, 2) if total else None


def _first(names: list[str], n: int) -> str:
    return "; ".join(names[:n]) + (f" and {len(names) - n} more" if len(names) > n else "")


def trump_rows(day: str) -> list[dict[str, Any]]:
    """Trump's stock and market calls of the last 90 days, newest first, and how they did.

    ``price_then`` is the close before he said it, ``now`` the latest close and ``since``
    the move between them (local currency). ``since_logged`` and ``excess`` run from the
    first open after we logged the call. ``came_true`` is "Yes", "No" or "" (not traded
    since it was logged).
    """
    from datetime import date, timedelta

    import pandas as pd

    from stocktrend.ingest import prices as price_store
    from stocktrend.markets import exchange_for
    from stocktrend.trackers import trump
    from stocktrend.universe.instruments import load_instruments

    since = (date.fromisoformat(day) - timedelta(days=TRUMP_DAYS)).isoformat()
    calls = trump.load_calls()
    calls = calls[(calls["logged"] <= day) & (calls["said_on"] >= since)]
    if calls.empty:
        return []
    try:
        prices = price_store.load()
    except FileNotFoundError:  # cache not downloaded in this session: show the calls alone
        prices = pd.DataFrame(columns=["date", "ticker", "open", "close", "adj_close"])
    perf = trump.performance(calls, prices, load_instruments().benchmarks)
    perf = perf.sort_values(["said_on", "logged"], ascending=False).head(TRUMP_ROWS)
    last_day = prices.groupby("ticker")["date"].max().to_dict() if len(prices) else {}
    rows = []
    for c in perf.to_dict("records"):
        true = c["came_true"]
        then, now = _float(c["price_then"]), _float(c["now"])
        when = last_day.get(c["ticker"])
        rows.append(
            {
                "key": f"{c['logged']}:{c['ticker']}",
                "said_on": str(c["said_on"]),
                "logged": str(c["logged"]),
                "ticker": str(c["ticker"]),
                "company": str(c["company"]),
                "stance": STANCE_NAMES[str(c["stance"])],
                "explicit": bool(c["explicit"]),
                "quote": str(c["quote"]),
                "price_then": None if then is None else round(then, 2),
                "now": None if now is None else round(now, 2),
                "since": _pct(now / then - 1) if now and then else None,
                "currency": exchange_for(str(c["ticker"])).currency,
                "price_date": None if when is None else pd.Timestamp(when).date().isoformat(),
                "reaction": _pct(_float(c["reaction"])),
                "since_logged": _pct(_float(c["since"])),
                "excess": _pct(_float(c["excess"])),
                "came_true": "" if true is None or pd.isna(true) else ("Yes" if true else "No"),
            }
        )
    return rows


def _float(x: Any) -> float | None:
    return None if x is None or x != x else float(x)


def write(day: str, out_dir: Path = OUT_DIR) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, rows in build(day).items():
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        paths[name] = path
    return paths
