import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from stocktrend.analysis import analyze, api, review
from stocktrend.report import site
from stocktrend.report.dashboard import DATASETS

DAY = "2026-10-28"


@dataclass
class _Block:
    text: str
    type: str = "text"


@dataclass
class _Usage:
    input_tokens: int = 1000
    output_tokens: int = 500
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class _Message:
    content: list[_Block]
    stop_reason: str = "end_turn"
    model: str = "test-model"
    usage: _Usage = field(default_factory=_Usage)


class _Stream:
    def __init__(self, message: _Message) -> None:
        self.message = message

    def __enter__(self) -> "_Stream":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get_final_message(self) -> _Message:
        return self.message


class FakeClient:
    """Answers each streamed request with the next reply, and keeps the requests."""

    def __init__(self, replies: list[str], stop_reason: str = "end_turn") -> None:
        self.replies = list(replies)
        self.stop_reason = stop_reason
        self.requests: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **request: Any) -> _Stream:
        self.requests.append(copy.deepcopy(request))
        return _Stream(_Message([_Block(self.replies.pop(0))], self.stop_reason))


def _events(ticker: str = "XOM") -> str:
    impact = {
        "target": f"ticker:{ticker}",
        "direction": 1,
        "magnitude": 0.4,
        "confidence": 0.5,
        "horizon_days": 5,
        "rationale": "Refinery attack -> supply -> producers up",
    }
    event = {
        "id": f"{DAY}-01",
        "title": "Refinery attack",
        "category": "supply_disruption",
        "regions": ["SA"],
        "summary": "Attack.",
        "certainty": "confirmed",
        "novelty": 0.8,
        "severity": 0.6,
        "duration": "weeks",
        "sources": ["S001"],
        "impacts": [impact],
    }
    return json.dumps({"date": DAY, "market_view": "Oil up.", "events": [event]})


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep analyze.run off the digest, universe and prompt files."""
    monkeypatch.setattr(
        analyze, "references", lambda day: ({"S001"}, {"XOM"}, {"oil_supply_shock"})
    )
    monkeypatch.setattr(analyze, "system_prompt", lambda: "rules")
    monkeypatch.setattr(analyze, "user_message", lambda day: "today's digest")


def _keywords(node: Any, out: set[str]) -> set[str]:
    if isinstance(node, list):
        for v in node:
            _keywords(v, out)
    elif isinstance(node, dict):
        for k, v in node.items():
            if k in ("properties", "$defs"):
                for sub in v.values():
                    _keywords(sub, out)
            else:
                out.add(k)
                _keywords(v, out)
    return out


def test_schema_keeps_every_field_and_only_supported_keywords() -> None:
    from stocktrend.events.schema import EventsFile

    s, full = analyze.schema(), EventsFile.model_json_schema()
    assert not _keywords(s, set()) & analyze.UNSUPPORTED
    assert s["properties"].keys() == full["properties"].keys()
    for name, d in full["$defs"].items():
        assert s["$defs"][name]["properties"].keys() == d["properties"].keys()
        assert s["$defs"][name]["additionalProperties"] is False
    assert "title" in s["$defs"]["Event"]["properties"]


def test_analysis_sends_errors_back_then_writes_the_file(offline: None, tmp_path: Path) -> None:
    client = FakeClient([_events("NOPE"), _events()])
    out, usage = tmp_path / "events.json", tmp_path / "usage.jsonl"
    said: list[str] = []
    v = analyze.run(DAY, client, out=out, usage_path=usage, say=said.append)

    assert v.ok and json.loads(out.read_text()) == json.loads(_events())
    first, second = client.requests
    assert first["output_config"]["format"]["type"] == "json_schema"
    assert first["system"][0] == {
        "type": "text", "text": "rules", "cache_control": {"type": "ephemeral"}
    }  # fmt: skip
    user, assistant, fix = second["messages"]
    assert user == {"role": "user", "content": "today's digest"}
    assert assistant["role"] == "assistant" and assistant["content"][0].text == _events("NOPE")
    assert "ticker:NOPE: unknown ticker" in fix["content"]
    assert len(said) == 1 and said[0].startswith("Reply 1 failed") and "NOPE" in said[0]
    assert [json.loads(line)["purpose"] for line in usage.read_text().splitlines()] == [
        "analysis", "analysis"
    ]  # fmt: skip


def test_analysis_gives_up_after_the_retries(offline: None, tmp_path: Path) -> None:
    retries = api.settings().retries
    client = FakeClient(["{}"] * (retries + 1))
    out = tmp_path / "events.json"
    with pytest.raises(analyze.AnalysisFailed):
        analyze.run(DAY, client, out=out, usage_path=tmp_path / "usage.jsonl")
    assert len(client.requests) == retries + 1 and not out.exists()


@pytest.mark.parametrize(
    ("stop", "error"), [("refusal", api.Refused), ("max_tokens", RuntimeError)]
)
def test_refusals_and_cut_off_replies_fail(
    tmp_path: Path, stop: str, error: type[Exception]
) -> None:
    usage = tmp_path / "usage.jsonl"
    with pytest.raises(error):
        api.ask(FakeClient(["x"], stop), api.settings(), "s", [], "analysis", DAY, None, usage)
    assert len(usage.read_text().splitlines()) == 1  # paid for, so logged


def test_cost_and_spent(tmp_path: Path) -> None:
    price = {"input": 4.0, "output": 20.0, "cache_write": 5.0, "cache_read": 0.2}
    usage = _Usage(1_000_000, 100_000, 200_000, 500_000)
    assert api.cost(usage, price) == pytest.approx(4 + 2 + 1 + 0.1)

    path = tmp_path / "usage.jsonl"
    cfg = api.Settings("test-model", "high", 1000, 0, price)
    for day, purpose in ((DAY, "analysis"), (DAY, "analysis"), (DAY, "review"), ("x", "analysis")):
        api.log_usage(_Message([], usage=usage), cfg, purpose, day, path)
    assert api.spent(DAY, "analysis", path) == pytest.approx(2 * 7.1)
    assert api.spent(DAY, "review", tmp_path / "none.jsonl") == 0


def test_user_message_has_feedback_last_three_files_and_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(analyze, "raw_dir", lambda day: tmp_path)
    (tmp_path / "digest.md").write_text("DIGEST")
    (tmp_path / "feedback.md").write_text("FEEDBACK")
    events = tmp_path / "events"
    events.mkdir()
    for day in ("2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27", DAY):
        (events / f"{day}.json").write_text(f'{{"date": "{day}"}}')

    msg = analyze.user_message(DAY, events)
    assert msg.startswith(f"Today is {DAY}.") and "FEEDBACK" in msg and msg.endswith("DIGEST")
    assert "## data/events/2026-10-22.json" not in msg and f"## data/events/{DAY}" not in msg
    assert msg.index("2026-10-23") < msg.index("2026-10-26") < msg.index("2026-10-27")

    (tmp_path / "digest.md").unlink()
    with pytest.raises(FileNotFoundError):
        analyze.user_message(DAY, events)


def test_review_writes_markdown_without_a_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(review, "user_message", lambda day: "the week")
    client = FakeClient(["# Weekly review\n\nNothing solid yet.\n\n"])
    out = review.run("2026-10-31", client, out=tmp_path / "r.md", usage_path=tmp_path / "u.jsonl")
    assert out.read_text() == "# Weekly review\n\nNothing solid yet.\n"
    assert "format" not in client.requests[0]["output_config"]
    assert "can't edit files" in client.requests[0]["system"][0]["text"]


def test_review_message_covers_the_week() -> None:
    msg = review.user_message("2026-10-31")
    assert msg.startswith("Today is 2026-10-31. Write data/reviews/2026-10-31.md")
    for heading in ("matured this week", "# This week's daily reports"):
        assert heading in msg


def test_site_has_the_page_the_runtime_and_the_data(tmp_path: Path) -> None:
    data = tmp_path / "dashboard"
    data.mkdir()
    for d in DATASETS:
        (data / f"{d}.json").write_text("[]")
    out = site.build(tmp_path / "site", data)

    page = (out / "index.html").read_text()
    assert "<!-- PAGE -->" not in page and "dash-one-writer" not in page
    assert 'src="dash.js"' in page and 'data-draw="' in page
    assert json.loads((out / "data" / "index.json").read_text()) == list(DATASETS)
    assert (out / "dash.js").exists() and (out / ".nojekyll").exists()

    (data / "picks.json").unlink()
    with pytest.raises(FileNotFoundError, match="picks"):
        site.build(tmp_path / "site", data)
