from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from openai import Omit
from pydantic import BaseModel

if TYPE_CHECKING:
    from app.core.calls import Usage


_COMPLETION_DRAW: ContextVar[bool] = ContextVar(
    "research_completion_draw", default=False
)


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
        "specialist",
        "list_review_evidence",
        "read_review_evidence",
        "web_search",
        "list_findings",
        "list_food_claims",
        "read_finding",
        "list_evidence",
        "read_evidence",
        "search_nearby",
        "route_to_place",
        "research_place_reviews",
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
        self._completion_protected = False
        self._completion_usage = 0
        self._completion_turn = 0

    def protect_completion(self, usage: int) -> None:
        if self._completion_protected:
            raise ValueError("completion reserve already configured")
        if type(usage) is not int or usage <= 0:
            raise ValueError("completion reserve usage must be positive")
        if (
            usage > self.context_limit
            or self.charged + usage > self.usage_limit
            or self.turns >= self.max_turns
        ):
            raise ValueError("completion reserve exceeds remaining budget")
        self._completion_usage = usage
        self._completion_turn = 1
        self._completion_protected = True

    def grow_completion_reserve(self, usage: int) -> bool:
        if not self._completion_protected or self._completion_turn == 0:
            raise ValueError("completion reserve is not active")
        if type(usage) is not int or usage <= 0:
            raise ValueError("completion reserve usage must be positive")
        if usage <= self._completion_usage:
            return True
        if usage > self.context_limit or self.charged + usage > self.usage_limit:
            return False
        self._completion_usage = usage
        return True

    def can_research(self) -> bool:
        return (
            self.turns + self._completion_turn < self.max_turns
            and self.tools < self.max_tools
            and self.charged + self._completion_usage < self.usage_limit
        )

    def can_complete(self, estimated_usage: int = 0) -> bool:
        if type(estimated_usage) is not int or estimated_usage < 0:
            raise ValueError("estimated usage must be nonnegative")
        return (
            self.turns < self.max_turns
            and self.charged + estimated_usage <= self.usage_limit
            and estimated_usage <= self.context_limit
        )

    @contextmanager
    def completion_draw(self) -> Iterator[None]:
        token = _COMPLETION_DRAW.set(True)
        try:
            yield
        finally:
            _COMPLETION_DRAW.reset(token)

    @contextmanager
    def tool(
        self,
        name: Literal[
            "specialist",
            "list_review_evidence",
            "read_review_evidence",
            "web_search",
            "list_findings",
            "list_food_claims",
            "read_finding",
            "list_evidence",
            "read_evidence",
            "search_nearby",
            "route_to_place",
            "research_place_reviews",
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
        completion = _COMPLETION_DRAW.get()
        reserved_turn = 0 if completion else self._completion_turn
        reserved_usage = 0 if completion else self._completion_usage
        if self.turns + reserved_turn >= self.max_turns:
            raise ResearchLimit("turn_limit")
        reservation = estimate + output_tokens
        if self.charged + reservation + reserved_usage > self.usage_limit:
            raise ResearchLimit("usage_limit")
        if completion:
            self._completion_turn = 0
            self._completion_usage = 0
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
