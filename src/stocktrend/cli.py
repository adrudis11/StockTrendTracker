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
    from stocktrend.ingest import run as ingest_run

    skipped = frozenset(skip or [])
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
