"""Schema of ``data/events/<date>.json``: Claude's structured reading of the news.

Each event says what happened, how new and certain it is, and which targets
(themes, tickers, sectors) it should move, in which direction, over which horizon.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Category = Literal[
    "sanctions",
    "trade",
    "conflict",
    "defense",
    "energy",
    "technology",
    "election",
    "politics",
    "regulation",
    "central_bank",
    "supply_disruption",
    "industrial_policy",
    "diplomacy",
    "other",
]
Certainty = Literal["confirmed", "reported", "rumor", "scheduled"]
Duration = Literal["days", "weeks", "months", "years"]
Horizon = Literal[5, 20]
REGIONS = {"US", "Europe"}

TARGET_RE = re.compile(r"^(theme|ticker|sector):(.+)$")


class Impact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(
        description='"theme:<id>", "ticker:<TICKER>" or "sector:<GICS sector>@<US|Europe>"'
    )
    direction: Literal[1, -1] = Field(
        description="For a theme: +1 = the theme intensifies, -1 = it eases. "
        "For a ticker or sector: +1 = should outperform, -1 = underperform."
    )
    magnitude: float = Field(ge=0, le=1, description="0.2 small, 0.5 notable, 0.8 large")
    confidence: float = Field(ge=0, le=1, description="Chance the direction is right")
    horizon_days: Horizon
    rationale: str = Field(
        min_length=10, max_length=400, description="event -> mechanism -> target"
    )

    @field_validator("target")
    @classmethod
    def _target_format(cls, v: str) -> str:
        if not TARGET_RE.match(v):
            raise ValueError('target must look like "theme:x", "ticker:X" or "sector:S@US"')
        return v

    @property
    def kind(self) -> str:
        return self.target.split(":", 1)[0]

    @property
    def ref(self) -> str:
        return self.target.split(":", 1)[1]


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="<date>-NN, e.g. 2026-10-28-03")
    title: str = Field(max_length=160)
    category: Category
    regions: list[str] = Field(min_length=1, description="Countries or areas involved")
    summary: str = Field(max_length=600)
    certainty: Certainty
    novelty: float = Field(ge=0, le=1, description="1 = brand new, 0.5 = new development")
    severity: float = Field(ge=0, le=1)
    duration: Duration
    sources: list[str] = Field(min_length=1, description="Digest story ids, e.g. S004")
    impacts: list[Impact] = Field(min_length=1, max_length=8)


class EventsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    market_view: str = Field(max_length=1000)
    events: list[Event] = Field(max_length=20)
