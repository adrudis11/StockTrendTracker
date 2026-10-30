"""Geopolitical themes and the stocks they move (``config/exposures.yaml``)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from stocktrend.config import load_yaml


class Theme(BaseModel):
    name: str
    description: str
    driver: str | None = None
    driver_sign: Literal[1, -1] = 1
    members: dict[str, float] = Field(min_length=1)

    @field_validator("members")
    @classmethod
    def _weights_in_range(cls, members: dict[str, float]) -> dict[str, float]:
        bad = {t: w for t, w in members.items() if not -1 <= w <= 1 or w == 0}
        if bad:
            raise ValueError(f"weights must be in [-1, 1] and non-zero: {bad}")
        return members


class Exposures(BaseModel):
    themes: dict[str, Theme]


def load_exposures() -> Exposures:
    return Exposures.model_validate(load_yaml("exposures.yaml"))


def themes_text(exposures: Exposures) -> str:
    """The themes events can target, with their main winners and losers (`stt themes`)."""
    lines = []
    for tid, th in exposures.themes.items():
        top = sorted(th.members.items(), key=lambda kv: -abs(kv[1]))
        up = [t for t, w in top if w > 0][:6]
        down = [t for t, w in top if w < 0][:4]
        lines.append(f"theme:{tid} - {th.name}: {th.description}")
        lines.append(f"    up: {', '.join(up) or '-'}   down: {', '.join(down) or '-'}")
    return "\n".join(lines)
