"""One streamed request to the Claude API, with refusal fallback and a cost log.

Settings (model, effort, prices) come from ``config/analysis.yaml`` and the key from
``ANTHROPIC_API_KEY``. A request the model declines for safety reasons is re-run
server-side on the model Anthropic recommends for that kind of refusal
(``fallbacks: "default"``); only a refusal by the whole chain fails the run.

Every call appends its token use and estimated cost to ``data/usage/api.jsonl``
(committed), so what the project costs is on record.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stocktrend.config import DATA_DIR, load_yaml

USAGE_PATH = DATA_DIR / "usage" / "api.jsonl"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class Refused(RuntimeError):
    """The model, and its fallback, declined the request."""


@dataclass
class Settings:
    model: str
    effort: str
    max_tokens: int
    retries: int
    price: dict[str, float]


def settings() -> Settings:
    raw = load_yaml("analysis.yaml")
    return Settings(raw["model"], raw["effort"], int(raw["max_tokens"]), int(raw["retries"]),
                    {k: float(v) for k, v in raw["price"].items()})  # fmt: skip


def client() -> Any:
    """An API client; the SDK retries overloaded and failed requests with backoff."""
    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set (GitHub runs: the ANTHROPIC_API_KEY_STOCKTRACKER secret)"
        )
    return anthropic.Anthropic(max_retries=4)


def ask(
    client: Any,
    cfg: Settings,
    system: str,
    messages: list[dict[str, Any]],
    purpose: str,
    day: str,
    schema: dict[str, Any] | None = None,
    usage_path: Path = USAGE_PATH,
) -> Any:
    """Send one request and return the final message. With ``schema``, the reply's text
    is JSON matching it. The system prompt is cached, so a retry within minutes
    re-reads it at a tenth of the price."""
    output_config: dict[str, Any] = {"effort": cfg.effort}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    with client.beta.messages.stream(
        model=cfg.model,
        max_tokens=cfg.max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
        output_config=output_config,
        betas=[FALLBACK_BETA],
        fallbacks="default",
    ) as stream:
        message = stream.get_final_message()
    log_usage(message, cfg, purpose, day, usage_path)
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        raise Refused(f"the model declined ({getattr(details, 'category', None)})")
    if message.stop_reason == "max_tokens":
        raise RuntimeError(f"the reply was cut off at max_tokens ({cfg.max_tokens})")
    return message


def text_of(message: Any) -> str:
    return "".join(b.text for b in message.content if b.type == "text")


def cost(usage: Any, price: dict[str, float]) -> float:
    """Estimated USD cost of one response at the configured prices."""
    tokens = {
        "input": usage.input_tokens or 0,
        "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "output": usage.output_tokens or 0,
    }
    return sum(n * price[k] for k, n in tokens.items()) / 1e6


def log_usage(message: Any, cfg: Settings, purpose: str, day: str, path: Path = USAGE_PATH) -> None:
    u = message.usage
    row = {
        "date": day,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "purpose": purpose,
        "model": message.model,  # the model that answered (a fallback may have)
        "input": u.input_tokens,
        "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
        "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
        "output": u.output_tokens,
        "cost_usd": round(cost(u, cfg.price), 4),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def spent(day: str, purpose: str, path: Path = USAGE_PATH) -> float:
    """What the day's calls for ``purpose`` cost so far, in USD (estimated)."""
    if not path.exists():
        return 0.0
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return sum(float(r["cost_usd"]) for r in rows if r["date"] == day and r["purpose"] == purpose)
