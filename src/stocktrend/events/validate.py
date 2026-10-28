"""`stt validate`: check Claude's events file against the schema and our reference data.

Pydantic catches shape errors; this module checks meaning: every source must be a
story in that day's digest and every target must be a known theme, ticker or sector.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from pydantic import ValidationError

from stocktrend.config import DATA_DIR
from stocktrend.events.digest import load as load_digest
from stocktrend.events.schema import REGIONS, EventsFile
from stocktrend.universe.build import load as load_universe
from stocktrend.universe.exposures import load_exposures
from stocktrend.universe.instruments import load_instruments
from stocktrend.universe.sectors import GICS_SECTORS


def events_path(day: str) -> Path:
    return DATA_DIR / "events" / f"{day}.json"


@dataclass
class Validation:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    events: EventsFile | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


def known_tickers() -> set[str]:
    return set(load_universe()["ticker"]) | {i.ticker for i in load_instruments().instruments}


def check(
    raw: str, day: str, story_ids: set[str], tickers: set[str], themes: set[str]
) -> Validation:
    v = Validation()
    try:
        ef = EventsFile.model_validate(json.loads(raw))
    except json.JSONDecodeError as e:
        v.errors.append(f"not valid JSON: {e}")
        return v
    except ValidationError as e:
        for err in e.errors():
            loc = ".".join(str(p) for p in err["loc"])
            v.errors.append(f"{loc}: {err['msg']}")
        return v
    v.events = ef

    if ef.date != day:
        v.errors.append(f"date is {ef.date}, expected {day}")
    seen_ids: set[str] = set()
    for n, ev in enumerate(ef.events):
        where = f"events[{n}] ({ev.id})"
        if not ev.id.startswith(f"{day}-") or ev.id in seen_ids:
            v.errors.append(f"{where}: id must be unique and start with {day}-")
        seen_ids.add(ev.id)
        missing = [s for s in ev.sources if s not in story_ids]
        if missing:
            v.errors.append(f"{where}: sources not in the digest: {missing}")
        seen_targets: set[tuple[str, int]] = set()
        for m, imp in enumerate(ev.impacts):
            iw = f"{where}.impacts[{m}] {imp.target}"
            if (imp.target, imp.horizon_days) in seen_targets:
                v.errors.append(f"{iw}: duplicate target for the same horizon")
            seen_targets.add((imp.target, imp.horizon_days))
            if imp.kind == "theme" and imp.ref not in themes:
                v.errors.append(f"{iw}: unknown theme (see `stt themes`)")
            elif imp.kind == "ticker" and imp.ref not in tickers:
                v.errors.append(f"{iw}: unknown ticker (see `stt universe find`)")
            elif imp.kind == "sector":
                sector, _, region = imp.ref.partition("@")
                if sector not in GICS_SECTORS or region not in REGIONS:
                    v.errors.append(f"{iw}: use sector:<GICS sector>@<US|Europe>")
        if ev.novelty < 0.2:
            v.warnings.append(f"{where}: novelty {ev.novelty} - is this still news?")
        if all(imp.confidence >= 0.85 for imp in ev.impacts):
            v.warnings.append(f"{where}: every impact has confidence >= 0.85; be calibrated")
    if not ef.events:
        v.warnings.append("no events: a quiet day, or nothing market-relevant?")
    seen_calls: set[str] = set()
    for n, call in enumerate(ef.trump_calls):
        where = f"trump_calls[{n}] {call.ticker}"
        if call.ticker not in tickers:
            v.errors.append(f"{where}: unknown ticker (see `stt universe find`)")
        if call.ticker in seen_calls:
            v.errors.append(f"{where}: one entry per ticker per day")
        seen_calls.add(call.ticker)
        missing = [s for s in call.sources if s not in story_ids]
        if missing:
            v.errors.append(f"{where}: sources not in the digest: {missing}")
        try:
            said, today = date.fromisoformat(call.said_on), date.fromisoformat(day)
            if not today - timedelta(days=14) <= said <= today:
                v.errors.append(f"{where}: said_on must be within the 14 days up to {day}")
        except ValueError:
            v.errors.append(f"{where}: said_on must be YYYY-MM-DD")
        if call.explicit and call.stance in ("positive", "negative"):
            v.warnings.append(f"{where}: explicit calls are 'buy' or 'sell'")
    return v


def validate(day: str) -> Validation:
    path = events_path(day)
    if not path.exists():
        return Validation(errors=[f"{path} does not exist"])
    digest = load_digest(day)
    return check(
        path.read_text(encoding="utf-8"),
        day,
        {s.id for s in digest.stories},
        known_tickers(),
        set(load_exposures().themes),
    )
