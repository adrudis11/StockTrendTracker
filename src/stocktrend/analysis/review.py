"""`stt review`: the Saturday review through the Claude API, written only.

A review in a Claude session may also edit the theme map and the analysis prompt.
Run unattended, it only writes ``data/reviews/<date>.md`` and lists the changes it
would make, with their evidence, for the owner to apply (or a later session).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from stocktrend.analysis import api
from stocktrend.config import DATA_DIR, ROOT
from stocktrend.tracking import feedback, impacts
from stocktrend.tracking import scoreboard as sb
from stocktrend.tracking.evaluate import first_matures, read_outcomes
from stocktrend.tracking.ledger import read as read_ledger

PROMPT = ROOT / "prompts" / "weekly_review.md"
REVIEWS = DATA_DIR / "reviews"

REVIEW_NOTE = """## This run

You are running through the API, without tools or a shell, and can't edit files.
Everything steps 1 and 3 need is in the user message: the scoreboard, the feedback
note, this week's daily reports, and the picks and analysis calls that matured this
week. Skip steps 2 and 5. Under **Changes made** write "None: this run can't edit
files", and list every change you would make under **Changes proposed**: the file, the
exact edit, and the evidence from this week or the feedback note. Reply with the
review only, in Markdown."""


def _week(day: str) -> tuple[str, str]:
    end = date.fromisoformat(day)
    return (end - timedelta(days=7)).isoformat(), day


def user_message(day: str, reports_dir: Path = DATA_DIR / "reports") -> str:
    start, end = _week(day)
    entries, outcomes = read_ledger(), read_outcomes()
    first = first_matures([e for e in entries if e.strategy == "geo"])
    board = sb.to_markdown(sb.compute(entries, outcomes), first.isoformat() if first else None)
    calls = [o for o in impacts.read() if o.exit_date <= end]
    by_id = {e.pick_id: e for e in entries if e.strategy == "geo"}
    picks = [
        f"| {by_id[o.pick_id].date} | {by_id[o.pick_id].ticker} | {by_id[o.pick_id].side} | "
        f"{by_id[o.pick_id].horizon}d | {100 * o.excess:+.2f}% | {'yes' if o.correct else 'no'} |"
        for o in outcomes
        if o.pick_id in by_id and start < o.exit_date <= end
    ]
    week_calls = [
        f"| {o.date} | {o.target} | {o.direction:+d} | {o.category} | {o.confidence:.2f} | "
        f"{100 * o.excess:+.2f}% | {'yes' if o.correct else 'no'} |"
        for o in calls
        if start < o.exit_date <= end
    ]
    reports = [p for p in sorted(reports_dir.glob("*.md")) if start < p.stem <= end]
    parts = [
        f"Today is {day}. Write data/reviews/{day}.md for the week {start} to {end}.",
        board,
        feedback.to_markdown([o for o in calls if o.exit_date < day], day),
        "# Geopolitical picks that matured this week\n\n"
        + ("| Picked | Ticker | Side | Horizon | Excess | Right |\n|---|---|---|---|---|---|\n"
           + "\n".join(picks) if picks else "None."),
        "# Analysis calls that matured this week\n\n"
        + ("| Made | Target | Direction | Category | Confidence | Excess | Right |\n"
           "|---|---|---|---|---|---|---|\n" + "\n".join(week_calls) if week_calls else "None."),
        "# This week's daily reports\n\n"
        + "\n\n".join(f"## {p.name}\n\n{p.read_text(encoding='utf-8')}" for p in reports),
    ]  # fmt: skip
    return "\n\n".join(parts)


def run(
    day: str, client: Any = None, out: Path | None = None, usage_path: Path = api.USAGE_PATH
) -> Path:
    cfg = api.settings()
    client = client or api.client()
    system = PROMPT.read_text(encoding="utf-8").strip() + "\n\n" + REVIEW_NOTE
    messages = [{"role": "user", "content": user_message(day)}]
    message = api.ask(client, cfg, system, messages, "review", day, usage_path=usage_path)
    path = out or REVIEWS / f"{day}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(api.text_of(message).strip() + "\n", encoding="utf-8")
    return path
