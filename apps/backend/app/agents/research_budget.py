from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from openai import Omit
from pydantic import BaseModel

if TYPE_CHECKING:
    from app.core.calls import Usage


class ResearchLimit(Exception):
    def __init__(
        self,
        reason: Literal["context_limit", "usage_limit", "turn_limit", "tool_limit"],
    ) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class RequestAccounting:
    estimated_input: int
    reserved_output: int
    actual_input: int | None
    actual_output: int | None
    outcome: str
    deferred_tool_results: int


@dataclass(frozen=True)
class ToolAccounting:
    name: Literal[
        "specialist", "list_review_evidence", "read_review_evidence", "web_search"
    ]
    outcome: Literal["success", "failed"]


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {
            key: _json_value(item)
            for key, item in value.items()
            if not isinstance(item, Omit)
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value if not isinstance(item, Omit)]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("unsupported SDK request value")


class ResearchBudget:
    """Attempt-wide limits. Estimates count UTF-8 bytes, not provider tokens."""

    def __init__(
        self,
        *,
        context_limit: int = 131072,
        usage_limit: int = 1000000,
        max_turns: int = 32,
        max_tools: int = 64,
        headroom: int = 512,
    ) -> None:
        if any(
            type(v) is not int or v <= 0
            for v in (context_limit, usage_limit, max_turns, max_tools, headroom)
        ):
            raise ValueError("research limits must be positive integers")
        self.context_limit = context_limit
        self.usage_limit = usage_limit
        self.max_turns = max_turns
        self.max_tools = max_tools
        self.headroom = headroom
        self.turns = 0
        self.tools = 0
        self.charged = 0
        self.records: list[RequestAccounting] = []
        self.tool_records: list[ToolAccounting] = []
        self.unknown_hosted_reservations: list[int] = []

    @contextmanager
    def tool(
        self,
        name: Literal[
            "specialist", "list_review_evidence", "read_review_evidence", "web_search"
        ],
    ) -> Iterator[None]:
        if self.tools >= self.max_tools:
            raise ResearchLimit("tool_limit")
        self.tools += 1
        outcome: Literal["success", "failed"] = "failed"
        try:
            yield
            outcome = "success"
        finally:
            self.tool_records.append(ToolAccounting(name, outcome))

    def reserve_hosted_tools(self, count: int) -> None:
        if self.tools + count > self.max_tools:
            raise ResearchLimit("tool_limit")
        self.tools += count

    def finish_hosted_tools(
        self, reserved: int, outcomes: tuple[bool, ...] | None
    ) -> None:
        if outcomes is None:
            self.unknown_hosted_reservations.append(reserved)
            return
        self.tools += len(outcomes) - reserved
        self.tool_records.extend(
            ToolAccounting("web_search", "success" if outcome else "failed")
            for outcome in outcomes
        )

    def estimate(self, kwargs: dict[str, Any]) -> int:
        encoded = json.dumps(
            _json_value(kwargs),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return len(encoded) + self.headroom

    def reserve(
        self, kwargs: dict[str, Any], output_tokens: int, *, deferred: int = 0
    ) -> tuple[int, int, int]:
        if type(output_tokens) is not int or output_tokens <= 0:
            raise ValueError("an output token limit is required")
        estimate = self.estimate(kwargs)
        if estimate + output_tokens > self.context_limit:
            raise ResearchLimit("context_limit")
        if self.turns >= self.max_turns:
            raise ResearchLimit("turn_limit")
        reservation = estimate + output_tokens
        if self.charged + reservation > self.usage_limit:
            raise ResearchLimit("usage_limit")
        self.turns += 1
        self.charged += reservation
        return estimate, output_tokens, deferred

    def finish(
        self, reservation: tuple[int, int, int], usage: Usage | None, outcome: str
    ) -> None:
        if usage is not None:
            actual = (usage.input_tokens or 0) + (usage.output_tokens or 0)
            self.charged += max(0, actual - sum(reservation[:2]))
        self.records.append(
            RequestAccounting(
                reservation[0],
                reservation[1],
                None if usage is None else usage.input_tokens,
                None if usage is None else usage.output_tokens,
                outcome,
                reservation[2],
            )
        )
