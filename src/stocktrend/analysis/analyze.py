"""`stt analyze`: the morning analysis through the Claude API, outside a Claude session.

The model gets what a session reads: ``prompts/daily_analysis.md``, the themes, the
covered tickers, the feedback note, the last three events files and today's digest,
and replies with the events file as JSON (structured output, so it always parses).
Python then runs the same checks as ``stt validate``; any errors go back to the model
in the same conversation to fix, up to ``retries`` times, before the run fails.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stocktrend.analysis import api
from stocktrend.config import DATA_DIR, ROOT, raw_dir
from stocktrend.events.digest import load as load_digest
from stocktrend.events.schema import EventsFile
from stocktrend.events.validate import Validation, check, events_path, known_tickers
from stocktrend.scoring.run import build_meta
from stocktrend.tracking import feedback, impacts
from stocktrend.universe.build import load as load_universe
from stocktrend.universe.exposures import load_exposures, themes_text
from stocktrend.universe.instruments import load_instruments

PROMPT = ROOT / "prompts" / "daily_analysis.md"
RECENT_FILES = 3

API_NOTE = """## This run

You are running through the API, without tools or a shell. Everything the
instructions above tell you to run or read is included: the themes (`stt themes`) and
the covered tickers (`stt universe find`) below; the feedback note, the last three
events files and today's digest in the user message. Reply with the events file only,
as JSON matching the schema. Python checks it as `stt validate` does and sends back
any errors for you to fix."""

# JSON-schema keywords structured outputs don't accept; pydantic re-checks them after.
UNSUPPORTED = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
               "minLength", "maxLength", "minItems", "maxItems", "pattern", "uniqueItems",
               "default", "title"}  # fmt: skip


class AnalysisFailed(RuntimeError):
    """The model's events file still failed validation after the allowed retries."""


def schema() -> dict[str, Any]:
    """The events file's JSON schema in the form structured outputs accept."""

    def clean(node: Any) -> Any:
        if isinstance(node, list):
            return [clean(v) for v in node]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k in ("properties", "$defs"):  # names (an event has a "title"), not keywords
                out[k] = {name: clean(sub) for name, sub in v.items()}
            elif k not in UNSUPPORTED:
                out[k] = clean(v)
        if out.get("type") == "object" or "properties" in out:
            out["additionalProperties"] = False
        return out

    result: dict[str, Any] = clean(EventsFile.model_json_schema())
    return result


def tickers_text() -> str:
    meta = build_meta(load_universe(), load_instruments())
    return "\n".join(
        f"{t} | {r['name']} | {r['region']} | {r['sector']}"
        for t, r in meta.sort_index().iterrows()
    )


def system_prompt() -> str:
    return "\n\n".join([
        PROMPT.read_text(encoding="utf-8").strip(),
        API_NOTE,
        "## Themes\n\n" + themes_text(load_exposures()),
        "## Covered tickers (ticker | name | region | sector)\n\n" + tickers_text(),
    ])  # fmt: skip


def user_message(day: str, events_dir: Path = DATA_DIR / "events") -> str:
    digest = raw_dir(day) / "digest.md"
    if not digest.exists():
        raise FileNotFoundError(f"{digest} is missing: run `stt daily prepare` (or `stt digest`)")
    note = raw_dir(day) / "feedback.md"
    feedback_md = (
        note.read_text(encoding="utf-8")
        if note.exists()
        else feedback.to_markdown([o for o in impacts.read() if o.exit_date < day], day)
    )
    recent = [p for p in sorted(events_dir.glob("*.json")) if p.stem < day][-RECENT_FILES:]
    parts = [
        f"Today is {day}. Write data/events/{day}.json.",
        "# Feedback note\n\n" + feedback_md,
        "# The last events files\n\n" + "\n\n".join(
            f"## data/events/{p.name}\n\n{p.read_text(encoding='utf-8')}" for p in recent
        ),
        "# Today's digest\n\n" + digest.read_text(encoding="utf-8"),
    ]  # fmt: skip
    return "\n\n".join(parts)


def references(day: str) -> tuple[set[str], set[str], set[str]]:
    """The story ids, tickers and themes an events file may name."""
    stories = {s.id for s in load_digest(day).stories}
    return stories, known_tickers(), set(load_exposures().themes)


def run(
    day: str,
    client: Any = None,
    out: Path | None = None,
    usage_path: Path = api.USAGE_PATH,
) -> Validation:
    """Write the day's events file (to ``out`` if given). Raises if it can't validate."""
    cfg = api.settings()
    client = client or api.client()
    stories, tickers, themes = references(day)
    system = system_prompt()
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message(day)}]
    for attempt in range(cfg.retries + 1):
        message = api.ask(client, cfg, system, messages, "analysis", day, schema(), usage_path)
        raw = api.text_of(message)
        v = check(raw, day, stories, tickers, themes)
        if v.ok:
            path = out or events_path(day)
            path.parent.mkdir(parents=True, exist_ok=True)
            pretty = json.dumps(json.loads(raw), indent=2, ensure_ascii=False)
            path.write_text(pretty + "\n", encoding="utf-8")
            return v
        if attempt < cfg.retries:
            errors = "\n".join(f"- {e}" for e in v.errors)
            messages += [
                {"role": "assistant", "content": message.content},  # unchanged, thinking too
                {"role": "user", "content": "`stt validate` found these problems:\n"
                 f"{errors}\n\nFix them and reply with the whole corrected file."},
            ]  # fmt: skip
    raise AnalysisFailed("; ".join(v.errors))
