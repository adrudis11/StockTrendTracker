"""The weekday watch on open picks: how each case has moved since the pick, for free.

Every weekday `stt collect` re-scores the day with the events already logged (no new
analysis, no API) and compares each open one-month pick with how it was scored when it
was made:

* **case**: the non-news part of the score now against then: price trend,
  already-priced-in, risks such as earnings, the tracker factors. ``stronger``,
  ``holding`` or ``weaker``; ``reversed`` when the news now points the other way, and
  ``spent`` when no event behind it is still live (normal at the end of the month).
  The news part itself fades over the month by design, so it isn't compared.
* **flags**: earnings before the pick ends, insider or House trades in the stock filed
  since the pick, and stories since the last Saturday that name the company.

It changes no pick and nothing in the ledger. Each day's watch is saved to
``data/watch/<date>.json`` (overwritten by a second run that day), so the dashboard
can show what changed and the weekly review how the cases moved.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from stocktrend.config import DATA_DIR
from stocktrend.scoring.model import HORIZONS, Pick

WATCH_DIR = DATA_DIR / "watch"
STRONGER, WEAKER = 1.15, 0.85  # the non-news factors now / then
CASES = ("stronger", "holding", "weaker", "reversed", "spent")

# Words that don't identify a company on their own in a headline.
_SUFFIXES = {
    "inc", "corp", "corporation", "co", "company", "companies", "plc", "sa", "se", "ag",
    "nv", "asa", "ab", "oyj", "spa", "group", "holdings", "holding", "ltd", "limited", "the",
    "class", "a", "b", "c", "&", "and", "of", "de", "la",
}  # fmt: skip
_COMMON = {
    "general", "american", "united", "first", "national", "international", "global",
    "bank", "energy", "royal", "new", "southern", "western", "eastern", "northern",
    "public", "service", "services", "industries", "financial", "capital", "health",
    "air", "british", "deutsche", "banco", "credit", "power", "home", "union",
}  # fmt: skip


def _rest(p: Pick) -> float:
    """The non-news part of a pick's score: conviction over the news signal."""
    return p.conviction / abs(p.geo) if p.geo else 0.0


def case(then: Pick, now: Pick | None) -> tuple[str, float | None]:
    """The case for a pick now against when it was made, and the ratio it rests on."""
    if now is None:
        return "spent", None
    if now.side != then.side:
        return "reversed", None
    if not _rest(then):
        return "holding", None
    ratio = round(_rest(now) / _rest(then), 3)
    if ratio >= STRONGER:
        return "stronger", ratio
    if ratio <= WEAKER:
        return "weaker", ratio
    return "holding", ratio


def short_name(name: str) -> str:
    """What headlines call a company: "Exxon Mobil Corporation" -> "Exxon", "General
    Dynamics Corp" -> "General Dynamics", "Airbus SE" -> "Airbus"."""
    words = [w for w in re.split(r"[\s,.()]+", name) if len(w) > 1 and w.lower() not in _SUFFIXES]
    if not words:
        return name
    first = words[0]
    if (len(first) >= 4 and first.lower() not in _COMMON) or (first.isupper() and len(words) == 1):
        return first
    return " ".join(words[:2])


def mentions(title: str, short: str) -> bool:
    """Whether a headline names the company. Short all-caps names (BP, IAG) must match
    in capitals, so "bp" in a word or URL doesn't count."""
    if not short:
        return False
    flags = 0 if short.isupper() and len(short) <= 4 else re.IGNORECASE
    return re.search(rf"(?<![\w-]){re.escape(short)}(?![\w-])", title, flags) is not None


def filings_since(
    ticker: str, since: date, insiders: pd.DataFrame, house: pd.DataFrame
) -> list[str]:
    """Insider (SEC Form 4) and House trades in the stock filed after ``since``."""
    notes = []
    if not insiders.empty:
        ins = insiders[(insiders["ticker"] == ticker) & (insiders["filed"] > since)]
        for code, verb in (("P", "bought"), ("S", "sold")):
            n = ins.loc[ins["code"] == code, "owner"].nunique()
            if n:
                notes.append(f"{n} insider{'s' if n > 1 else ''} {verb}")
    if not house.empty:
        h = house[(house["ticker"] == ticker) & (house["filed"] > since)]
        for types, verb in ((("purchase",), "bought"), (("sale", "partial_sale"), "sold")):
            n = h.loc[h["type"].isin(types), "member"].nunique()
            if n:
                notes.append(f"{n} House member{'s' if n > 1 else ''} {verb}")
    return notes


def watch_row(
    entry: Any,
    then: Pick,
    now: Pick | None,
    exit_date: date,
    day: str,
    insiders: pd.DataFrame,
    house: pd.DataFrame,
    titles: list[str],
    name: str,
) -> dict[str, Any]:
    label, ratio = case(then, now)
    flags = []
    earnings = (now.next_earnings if now else None) or then.next_earnings
    if earnings and day <= earnings <= exit_date.isoformat():
        flags.append(f"earnings on {earnings}, before it ends")
    flags += [f"since the pick: {n}" for n in filings_since(
        entry.ticker, date.fromisoformat(entry.date), insiders, house
    )]  # fmt: skip
    short = short_name(name)
    n = sum(mentions(t, short) for t in titles)
    if n:
        flags.append(f"named in {n} {'stories' if n > 1 else 'story'} this week")
    return {
        "pick_id": entry.pick_id,
        "date": entry.date,
        "ticker": entry.ticker,
        "horizon": entry.horizon,
        "side": entry.side,
        "ends": exit_date.isoformat(),
        "conviction_then": round(then.conviction, 3),
        "conviction_now": round(now.conviction, 3) if now and now.side == then.side else 0.0,
        "case": label,
        "ratio": ratio,
        "flags": flags,
    }


def week_titles(day: str, digests_dir: Path | None = None) -> list[str]:
    """Headlines of the digests since the last Saturday, up to ``day``."""
    from stocktrend.events.digest import DIGESTS_DIR, Digest, last_saturday

    folder = digests_dir or DIGESTS_DIR
    start = last_saturday(date.fromisoformat(day)).isoformat()
    titles = []
    for p in sorted(folder.glob("*.json")):
        if start < p.stem <= day:
            titles += [s.title for s in Digest.model_validate_json(p.read_text("utf-8")).stories]
    return titles


def build(day: str) -> list[dict[str, Any]]:
    """The watch for ``day``: one row per open one-month pick made before it."""
    from stocktrend.markets import exchange_for
    from stocktrend.scoring.run import context, score_day
    from stocktrend.scoring.run import load as load_picks
    from stocktrend.trackers import congress, insiders
    from stocktrend.tracking.evaluate import entry_session, exit_session, read_outcomes
    from stocktrend.tracking.ledger import read as read_ledger

    done = {o.pick_id for o in read_outcomes()}
    open_ = [
        e for e in read_ledger()
        if e.strategy == "geo" and e.horizon in HORIZONS and e.date < day and e.pick_id not in done
    ]  # fmt: skip
    if not open_:
        return []
    ctx = context()
    tickers = {e.ticker for e in open_}
    now = {(p.ticker, p.horizon): p for p in score_day(day, ctx, False, tickers).picks}
    made: dict[str, dict[tuple[int, str, str], Pick]] = {}
    for d in {e.date for e in open_}:
        try:
            made[d] = {(p.horizon, p.side, p.ticker): p for p in load_picks(d).picks}
        except FileNotFoundError:
            made[d] = {}
    ins, house = insiders.load_trades(), congress.load_trades()
    titles = week_titles(day)
    names = ctx.meta["name"].to_dict()
    rows = []
    for e in sorted(open_, key=lambda e: (e.date, e.side != "long", e.rank)):
        then = made[e.date].get((e.horizon, e.side, e.ticker))
        if then is None:
            continue
        mic = exchange_for(e.ticker).mic
        exit_date = exit_session(mic, entry_session(mic, e.logged_at), e.horizon).date()
        rows.append(watch_row(
            e, then, now.get((e.ticker, e.horizon)), exit_date, day, ins, house, titles,
            str(names.get(e.ticker, e.ticker)),
        ))  # fmt: skip
    return rows


def save(day: str, rows: list[dict[str, Any]], folder: Path | None = None) -> Path:
    path = (folder or WATCH_DIR) / f"{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return path


def load(day: str, folder: Path | None = None) -> tuple[str | None, list[dict[str, Any]]]:
    """The latest watch up to ``day`` (its date and rows), or (None, [])."""
    days = sorted(p.stem for p in (folder or WATCH_DIR).glob("*.json") if p.stem <= day)
    if not days:
        return None, []
    path = (folder or WATCH_DIR) / f"{days[-1]}.json"
    rows: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    return days[-1], rows


def changes(rows: list[dict[str, Any]], before: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What changed since the previous watch: cases that moved and new flags."""
    prev = {r["pick_id"]: r for r in before}
    out = []
    for r in rows:
        p = prev.get(r["pick_id"])
        what = f"{r['ticker']} ({'buy' if r['side'] == 'long' else 'avoid'}, picked {r['date']})"
        if p and p["case"] != r["case"]:
            out.append({"key": f"case:{r['pick_id']}", "kind": "case",
                        "text": f"{what}: case {p['case']} → {r['case']}"})  # fmt: skip
        had = {_kind(f) for f in p["flags"]} if p else set()
        new_flags = [f for f in r["flags"] if _kind(f) not in had]
        for f in new_flags:
            out.append({"key": f"flag:{r['pick_id']}:{f}", "kind": "flag", "text": f"{what}: {f}"})
    return out


def _kind(flag: str) -> str:
    """A flag without its counts, so "named in 3 stories" isn't new after "in 2"."""
    return re.sub(r"\d+ (\w+?)s? ", r"# \1 ", flag).replace("storie ", "story ")
