"""Command-line entry point: ``stt``."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Annotated

import typer

from stocktrend import markets
from stocktrend.config import UNIVERSE_CSV
from stocktrend.http import session

app = typer.Typer(help="StockTrendTracker: geopolitical signals for US and European stocks.")
universe_app = typer.Typer(help="The list of stocks we cover.")
app.add_typer(universe_app, name="universe")
daily_app = typer.Typer(help="The scheduled daily run, in two halves around Claude's analysis.")
app.add_typer(daily_app, name="daily")
trackers_app = typer.Typer(help="Disclosed trades of US House members and company insiders.")
app.add_typer(trackers_app, name="trackers")


@app.callback()
def main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # yfinance logs every rate-limited cookie fetch; it recovers on its own.
    logging.getLogger("yfinance").setLevel(logging.ERROR)


@universe_app.command("build")
def universe_build() -> None:
    """Rebuild data/universe/stocks.csv from index member lists on Wikipedia."""
    from stocktrend.universe.build import build

    u = build(session())
    typer.echo(f"Wrote {len(u)} stocks to {UNIVERSE_CSV}")
    _print_universe(u)


@universe_app.command("show")
def universe_show() -> None:
    """Summarise the current universe."""
    from stocktrend.universe.build import load

    _print_universe(load())


def _print_universe(u: object) -> None:
    import pandas as pd

    assert isinstance(u, pd.DataFrame)
    typer.echo("\nBy country:")
    typer.echo(u.groupby(["region", "country"]).size().to_string())
    typer.echo("\nBy sector:")
    typer.echo(u["sector"].value_counts().to_string())


@universe_app.command("find")
def universe_find(query: Annotated[list[str], typer.Argument(help="Names or tickers")]) -> None:
    """Look up tickers by company name or ticker (stocks and ETFs/instruments)."""
    from stocktrend.universe.build import load
    from stocktrend.universe.instruments import load_instruments

    u = load()
    instruments = load_instruments().instruments
    for q in query:
        ql = q.lower()
        hits = u[
            u["name"].str.lower().str.contains(ql, regex=False) | (u["ticker"].str.lower() == ql)
        ]
        for r in hits.itertuples():
            typer.echo(f"{r.ticker:<10} {r.name} ({r.country}, {r.sector}; {r.industry})")
        for i in instruments:
            if ql in i.name.lower() or ql == i.ticker.lower():
                typer.echo(f"{i.ticker:<10} {i.name} ({i.kind}, {i.region})")
        if hits.empty and not any(
            ql in i.name.lower() or ql == i.ticker.lower() for i in instruments
        ):
            typer.echo(f"{q}: not covered")


def _day(day: str | None) -> str:
    return date.fromisoformat(day).isoformat() if day else datetime.now(UTC).date().isoformat()


DayOption = Annotated[str | None, typer.Option("--day", help="YYYY-MM-DD; default today (UTC)")]


@app.command()
def themes() -> None:
    """List the geopolitical themes events can target, with their main members."""
    from stocktrend.universe.exposures import load_exposures

    for tid, th in load_exposures().themes.items():
        top = sorted(th.members.items(), key=lambda kv: -abs(kv[1]))
        up = [t for t, w in top if w > 0][:6]
        down = [t for t, w in top if w < 0][:4]
        typer.echo(f"theme:{tid} - {th.name}: {th.description}")
        typer.echo(f"    up: {', '.join(up) or '-'}   down: {', '.join(down) or '-'}")


@app.command()
def digest(
    day: DayOption = None,
    show: Annotated[bool, typer.Option("--print", help="Print the Markdown digest")] = False,
) -> None:
    """Group and rank the ingested news into data/digests/<day>.json (+ Markdown in cache/)."""
    import json

    from stocktrend.config import raw_dir
    from stocktrend.events import digest as dg
    from stocktrend.features.returns import snapshot
    from stocktrend.ingest import prices
    from stocktrend.universe.build import load
    from stocktrend.universe.instruments import load_instruments

    d = _day(day)
    cfg = load_instruments()
    market = snapshot(
        prices.load(), {i.ticker: i.name for i in cfg.instruments}, load(), cfg.benchmarks
    )
    odds_path = raw_dir(d) / "polymarket.json"
    if odds_path.exists():
        market["prediction_markets"] = json.loads(odds_path.read_text(encoding="utf-8"))
    result = dg.build(d, datetime.now(UTC), market)
    path = dg.save(result)
    md = dg.to_markdown(result)
    md_path = raw_dir(d) / "digest.md"
    md_path.write_text(md, encoding="utf-8")
    if show:
        typer.echo(md)
    typer.echo(
        f"{len(result.stories)} stories (of {result.n_stories_total}, from {result.n_items} items)"
        f" -> {path}\nReadable version: {md_path}"
    )


@app.command()
def score(day: DayOption = None) -> None:
    """Rank picks from the last 30 days of events into data/picks/<day>.json."""
    from stocktrend.scoring.run import picks_path, run

    d = _day(day)
    result = run(d)
    for h in (5, 20):
        longs = result.select(h, "long")
        avoids = result.select(h, "avoid")
        typer.echo(
            f"{h}d long:  " + (", ".join(f"{p.ticker} {p.conviction:.2f}" for p in longs) or "-")
        )
        typer.echo(
            f"{h}d avoid: " + (", ".join(f"{p.ticker} {p.conviction:.2f}" for p in avoids) or "-")
        )
    typer.echo(f"Regime {result.regime.get('label')} -> {picks_path(d)}")


@app.command()
def record(day: DayOption = None) -> None:
    """Append the day's picks and baselines to the ledger (never overwrites)."""
    from stocktrend.events.schema import EventsFile
    from stocktrend.events.validate import events_path
    from stocktrend.scoring.run import context, insider_signal, tracker_signal
    from stocktrend.scoring.run import load as load_picks
    from stocktrend.tracking.ledger import LEDGER
    from stocktrend.tracking.ledger import record as record_picks

    d = _day(day)
    ctx = context()
    ev_path = events_path(d)
    calls = (
        EventsFile.model_validate_json(ev_path.read_text(encoding="utf-8")).trump_calls
        if ev_path.exists()
        else []
    )
    n = record_picks(
        load_picks(d), ctx.features, ctx.meta, tracker=tracker_signal(d, ctx.meta),
        trump_calls=calls, insiders=insider_signal(d, ctx.meta),
    )  # fmt: skip
    typer.echo(f"Logged {n} new ledger entries for {d} -> {LEDGER}")


@app.command()
def evaluate() -> None:
    """Score every ledger pick whose horizon has ended (uses cached prices)."""
    from stocktrend.ingest import prices
    from stocktrend.scoring.run import sector_etf_map
    from stocktrend.tracking.evaluate import OUTCOMES
    from stocktrend.tracking.evaluate import evaluate as run_evaluation
    from stocktrend.universe.instruments import load_instruments

    cfg = load_instruments()
    new, still_open = run_evaluation(prices.load(), cfg.benchmarks, sector_etf_map(cfg))
    typer.echo(f"Scored {new} matured picks ({still_open} still open) -> {OUTCOMES}")


@app.command()
def scoreboard() -> None:
    """Track record of the geo picks against the momentum and random baselines."""
    import json

    from stocktrend.config import DATA_DIR
    from stocktrend.tracking import scoreboard as sb
    from stocktrend.tracking.evaluate import first_matures, read_outcomes
    from stocktrend.tracking.ledger import read

    entries = read()
    result = sb.compute(entries, read_outcomes())
    path = DATA_DIR / "ledger" / "scoreboard.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1), encoding="utf-8")
    first = first_matures(entries)
    typer.echo(sb.to_markdown(result, first.isoformat() if first else None))


@app.command()
def backtest(
    years: Annotated[int, typer.Option(help="Years of daily history")] = 5,
) -> None:
    """Backtest the momentum baseline and compute theme priors from price history."""
    import json

    from stocktrend.backtest import history, momentum, priors
    from stocktrend.config import DATA_DIR
    from stocktrend.scoring.run import PRIORS_PATH, listing_region
    from stocktrend.universe.build import load
    from stocktrend.universe.exposures import load_exposures
    from stocktrend.universe.instruments import load_instruments

    cfg = load_instruments()
    universe = load()
    tickers = list(universe["ticker"]) + [i.ticker for i in cfg.instruments]
    today = datetime.now(UTC).date()
    typer.echo(f"Loading {years}y of history for {len(tickers)} tickers (cached after first run)")
    closes = history.wide_closes(history.load_or_download(tickers, today, years))

    regions = dict(zip(universe["ticker"], universe["region"], strict=True))
    out = {str(h): momentum.run(closes, regions, cfg.benchmarks, h) for h in (5, 20)}
    mom_path = DATA_DIR / "backtests" / "momentum.json"
    mom_path.parent.mkdir(parents=True, exist_ok=True)
    mom_path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    for h, r in out.items():
        lg = r["long"]
        typer.echo(
            f"momentum {h}d: long mean excess {lg['mean_excess']}, hit {lg['hit_rate']},"
            f" t {lg['t_stat']} over {lg['periods']} periods"
        )

    all_regions = {**{i.ticker: listing_region(i.ticker) for i in cfg.instruments}, **regions}
    pri = priors.compute(closes, load_exposures(), all_regions, cfg.benchmarks)
    PRIORS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PRIORS_PATH.write_text(json.dumps(pri, indent=1), encoding="utf-8")
    for tid, t in pri["themes"].items():
        cells = []
        for h in ("5", "20"):
            r = t[h]
            mark = "" if r.get("usable") else " (not usable)"
            cells.append(f"{h}d n={r['n']} mean={r.get('mean_excess')} t={r.get('t_stat')}{mark}")
        typer.echo(f"prior {tid:<28} " + " | ".join(cells))
    typer.echo(f"-> {mom_path}, {PRIORS_PATH}")
    _backtest_trackers(closes, regions, cfg.benchmarks, set(universe["ticker"]))


def _backtest_trackers(
    closes: object, regions: dict[str, str], benchmarks: dict[str, str], known: set[str]
) -> None:
    """Backtest each tracker on its stored trades; the weights go to data/priors/."""
    import json

    import pandas as pd

    from stocktrend.backtest import trackers
    from stocktrend.scoring.run import TRACKERS_PATH
    from stocktrend.trackers import congress, insiders

    assert isinstance(closes, pd.DataFrame)
    out: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "method": "every horizon sessions, the 10 stocks with the strongest buying disclosed "
        "in the 30 days before (filings public before that day, trades under 60 days old) "
        "vs SPY. Congress: House filings, no dividend reinvestments. Insiders: open-market "
        "purchases on Form 4. Current index members only.",
    }
    specs = (
        ("congress", "weight", congress.load_trades(), congress.signal),
        ("insiders", "insider_weight", insiders.load_trades(), insiders.signal),
    )
    for name, key, trades, signal in specs:
        if trades.empty:
            typer.echo(f"No {name} trades stored yet: run `stt trackers update` first.")
            continue
        start = min(trades["filed"]) + pd.Timedelta(days=60)
        results = {
            str(h): trackers.run(closes, trades, known, h, start, signal=signal) for h in (5, 20)
        }
        yardstick = {
            str(h): trackers.compare_momentum(closes, regions, benchmarks, h, start)["long"]
            for h in (5, 20)
        }
        out[key] = trackers.weight_from_evidence(results["20"])
        out[name] = results
        out["momentum_same_window" if name == "congress" else f"momentum_{name}_window"] = yardstick
        for h, r in results.items():
            b, m = r["buys"], yardstick[h]
            typer.echo(
                f"{name} {h}d: buys mean excess {b['mean_excess']} hit {b['hit_rate']} "
                f"t {b['t_stat']} ({b['periods']} periods, {b['picks']} picks) | "
                f"sells lag {r['sells']['mean_excess']} t {r['sells']['t_stat']} | "
                f"momentum same window {m['mean_excess']} t {m['t_stat']}"
            )
        typer.echo(f"{name} weight in the live score: {out[key]}")
    TRACKERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TRACKERS_PATH.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    typer.echo(f"-> {TRACKERS_PATH}")


@app.command()
def dashboard(day: DayOption = None) -> None:
    """Write the dashboard's JSON datasets to cache/dashboard/ for upload."""
    from stocktrend.report.dashboard import write as write_dashboard

    for name, path in write_dashboard(_day(day)).items():
        typer.echo(f"{name:<11} {path}")


@app.command()
def report(day: DayOption = None) -> None:
    """Write the daily Markdown report to data/reports/<day>.md."""
    from stocktrend.events.digest import digest_path
    from stocktrend.events.digest import load as load_digest
    from stocktrend.events.schema import EventsFile
    from stocktrend.events.validate import events_path
    from stocktrend.report.render import render, write
    from stocktrend.scoring.run import load as load_picks

    d = _day(day)
    ev_path = events_path(d)
    events = (
        EventsFile.model_validate_json(ev_path.read_text(encoding="utf-8"))
        if ev_path.exists()
        else None
    )
    digest_obj = load_digest(d) if digest_path(d).exists() else None
    from stocktrend.tracking import scoreboard as sb
    from stocktrend.tracking.evaluate import first_matures, read_outcomes
    from stocktrend.tracking.ledger import read

    entries = read()
    first = first_matures([e for e in entries if e.strategy == "geo"])
    track = sb.to_markdown(
        sb.compute(entries, read_outcomes()), first.isoformat() if first else None
    )
    from stocktrend.report.dashboard import (
        congress_rows,
        insider_rows,
        open_pick_rows,
        result_rows,
        trump_rows,
    )
    from stocktrend.report.render import (
        congress_markdown,
        insiders_markdown,
        open_picks_markdown,
        results_markdown,
        trump_markdown,
    )

    picks_obj = load_picks(d)
    congress_md = congress_markdown(congress_rows(d)[:10], picks_obj.tracker_weight)
    insiders_md = insiders_markdown(insider_rows(d)[:10], picks_obj.insider_weight)
    open_md = open_picks_markdown(open_pick_rows(d))
    results_md = results_markdown(result_rows(d))
    trump_md = trump_markdown(trump_rows(d))
    text = render(
        picks_obj, events, digest_obj, track, congress_md, trump_md, insiders_md, open_md,
        results_md,
    )  # fmt: skip
    path = write(d, text)
    typer.echo(f"Report -> {path}")


@app.command()
def validate(day: DayOption = None) -> None:
    """Check data/events/<day>.json against the schema, digest and reference data."""
    from stocktrend.events.validate import validate as run_validation

    d = _day(day)
    v = run_validation(d)
    for w in v.warnings:
        typer.secho(f"warning: {w}", fg="yellow")
    for e in v.errors:
        typer.secho(f"error: {e}", fg="red", err=True)
    if not v.ok:
        raise typer.Exit(1)
    n = len(v.events.events) if v.events else 0
    typer.secho(f"OK: {n} events valid", fg="green")


@app.command()
def calendar(
    day: Annotated[str | None, typer.Option(help="YYYY-MM-DD; default today (UTC)")] = None,
) -> None:
    """Which of our exchanges trade on a given day."""
    d = date.fromisoformat(day) if day else datetime.now(UTC).date()
    exchanges = {markets.US_EXCHANGE, *markets.SUFFIX_EXCHANGES.values()}
    for ex in sorted(exchanges, key=lambda e: e.mic):
        status = "open" if markets.is_trading_day(ex.mic, d) else "closed"
        typer.echo(f"{ex.mic}  {ex.country:<15} {status}")


@app.command()
def ingest(
    lookback_hours: Annotated[
        int | None, typer.Option(help="News window; default 24h, 72h on Mondays")
    ] = None,
    skip: Annotated[
        list[str] | None,
        typer.Option(
            help="Source to skip (repeatable): prices, rss, federal_register, "
            "gdelt_events, gdelt_doc"
        ),
    ] = None,
    strict: Annotated[bool, typer.Option(help="Fail if any single feed fails")] = False,
) -> None:
    """Download prices and the latest news into cache/raw/<today>/."""
    _run_ingest(lookback_hours, frozenset(skip or []), strict)


def _run_ingest(lookback_hours: int | None, skipped: frozenset[str], strict: bool) -> None:
    from stocktrend.ingest import run as ingest_run

    unknown = skipped - set(ingest_run.SOURCES)
    if unknown:
        raise typer.BadParameter(f"unknown sources: {sorted(unknown)}")
    as_of = datetime.now(UTC)
    hours = lookback_hours or ingest_run.default_lookback_hours(as_of)
    report = ingest_run.run(as_of, hours, skipped)

    typer.echo(f"\nIngest {as_of:%Y-%m-%d %H:%M} UTC, news window {hours}h -> {report.out_dir}\n")
    for s in report.sources:
        mark = "FAIL" if s.error else ("warn" if s.warning else "ok")
        note = s.error or s.warning or ""
        typer.echo(f"  {mark:<4}  {s.name:<38} {s.items:>6}  {note}")
    if report.prices:
        p = report.prices
        typer.echo(
            f"\nPrices: {p['requested'] - len(p['missing'])}/{p['requested']} tickers, "
            f"last session {p['last_session']}"
        )
    typer.echo(f"News: {report.news_items} unique items {report.news_by_source}")

    failed_feeds = [s.name for s in report.sources if s.error]
    if not report.ok:
        typer.secho("\nRun FAILED: " + "; ".join(report.problems), fg="red", err=True)
        raise typer.Exit(1)
    if strict and failed_feeds:
        typer.secho(f"\nStrict mode: {len(failed_feeds)} feeds failed", fg="red", err=True)
        raise typer.Exit(1)
    typer.secho("\nRun OK", fg="green")


# --- trackers ------------------------------------------------------------------


@trackers_app.command("update")
def trackers_update(
    years: Annotated[
        list[int] | None,
        typer.Option("--year", help="Filing years (repeatable); default: this year"),
    ] = None,
    sec: Annotated[bool, typer.Option("--sec/--no-sec", help="Also read SEC Form 4s")] = True,
) -> None:
    """Download new House trade reports (and SEC insider filings) into data/trackers/."""
    from stocktrend.trackers import congress

    this_year = datetime.now(UTC).year
    chosen = years or ([this_year - 1, this_year] if datetime.now(UTC).month == 1 else [this_year])
    stats = congress.update(session(), chosen)
    typer.echo(f"House filings {chosen}: {stats}")
    _tracker_prices()
    if sec:
        trackers_insiders()


def _tracker_prices() -> None:
    """Prices for stocks members traded that we don't cover, for the Now and Since columns."""
    from stocktrend.config import PRICES_PARQUET
    from stocktrend.ingest import prices as price_store
    from stocktrend.trackers import congress

    today = datetime.now(UTC).date()
    have = set(price_store.load()["ticker"]) if PRICES_PARQUET.exists() else set()
    extra = sorted(set(congress.recent(congress.load_trades(), today)["ticker"]) - have)
    if not extra:
        return
    px = price_store.download(extra, today, history_days=120)
    price_store.save(px, price_store.TRACKER_PRICES)
    typer.echo(f"Prices for {px['ticker'].nunique()} of {len(extra)} traded stocks we don't cover")


@trackers_app.command("insiders")
def trackers_insiders() -> None:
    """Read new SEC Form 4 filings: insider trades in S&P 500 stocks (first run: 5 years)."""
    from stocktrend.http import sec_session
    from stocktrend.trackers import insiders
    from stocktrend.universe.build import load as load_universe

    http = sec_session()
    if http is None:
        typer.secho(
            "SEC insider filings skipped: no contact email (set STT_SEC_CONTACT or write it "
            "to .sec_contact).",
            fg="yellow",
        )
        return
    u = load_universe()
    stats = insiders.update(http, u.loc[u["region"] == "US", "ticker"])
    typer.echo(f"SEC insider filings: {stats}")


@trackers_app.command("show")
def trackers_show(
    day: DayOption = None,
    top: Annotated[int, typer.Option(help="Rows to show")] = 15,
) -> None:
    """Strongest congressional and insider buying disclosed in the last 30 days."""
    from stocktrend.trackers import congress, insiders

    d = date.fromisoformat(_day(day))
    sig = congress.signal(congress.load_trades(), d)
    typer.echo("House members (net buying, then net selling):")
    if sig.empty:
        typer.echo("No disclosed trades in the window.")
    else:
        typer.echo(sig.head(top).to_string())
        typer.echo("...")
        typer.echo(sig.tail(5).to_string())
    ins = insiders.signal(insiders.load_trades(), d)
    ins = ins[ins["score"] > 0]
    typer.echo("\nCompany insiders (open-market purchases, SEC Form 4):")
    typer.echo(ins.head(top).to_string() if not ins.empty else "No purchases filed in the window.")


# --- the scheduled daily run ----------------------------------------------------

STALE_PRIORS_DAYS = 30


def _already_recorded(day: str) -> bool:
    from stocktrend.tracking.ledger import read

    return any(e.date == day and e.strategy == "geo" for e in read())


@daily_app.command("prepare")
def daily_prepare() -> None:
    """Step 1: check markets, ingest, score matured picks, build the digest.

    Prints NOTHING TO DO and exits 0 when no covered market trades today or the
    day is already recorded; the session should then stop.
    """
    from stocktrend.config import raw_dir

    d = _day(None)
    today = date.fromisoformat(d)
    if not (markets.is_trading_day("XNYS", today) or markets.is_trading_day("XETR", today)):
        typer.echo(f"NOTHING TO DO: neither the NYSE nor Xetra trades on {d}.")
        return
    if _already_recorded(d):
        typer.echo(f"NOTHING TO DO: picks for {d} are already in the ledger.")
        return
    _run_ingest(None, frozenset(), strict=False)
    for name, sec in (("House filings", False), ("SEC insider filings", True)):
        try:
            trackers_insiders() if sec else trackers_update(years=None, sec=False)
        except Exception as e:  # the trackers are optional; never block the run on them
            typer.secho(f"warning: {name} update failed: {e}", fg="yellow")
    evaluate()
    digest(day=d, show=False)
    typer.echo(
        f"\nNEXT: read {raw_dir(d) / 'digest.md'}, follow prompts/daily_analysis.md to write "
        f"data/events/{d}.json, then run `uv run stt daily finish`."
    )


@daily_app.command("finish")
def daily_finish() -> None:
    """Step 2: validate the analysis, score, log to the ledger, report, dashboard data."""
    from stocktrend.scoring.run import PRIORS_PATH

    d = _day(None)
    validate(day=d)
    if PRIORS_PATH.exists():
        age = datetime.now(UTC).timestamp() - PRIORS_PATH.stat().st_mtime
        stale = age > STALE_PRIORS_DAYS * 86400
    else:
        stale = True
    if stale:
        typer.echo("Theme priors are missing or over a month old: refreshing (about 2 minutes).")
        backtest(years=5)
    if _already_recorded(d):
        typer.echo(f"Picks for {d} are already in the ledger: not re-scoring.")
    else:
        score(day=d)
        record(day=d)
    scoreboard()
    report(day=d)
    dashboard(day=d)
    typer.echo(
        "\nNEXT: upload the nine files above to the dashboard and commit data/ "
        "(see prompts/daily_run.md)."
    )
