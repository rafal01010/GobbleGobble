from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Literal
from uuid import UUID, uuid4

from openai.types.shared import ChatModel
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from app.core.settings import ReviewSettings, Settings
from app.core.source_policy import SourceUseDenied, require_source_use

if TYPE_CHECKING:
    from app.agents.research_budget import ResearchBudget

Provider = Literal[
    "geoapify",
    "google_reviews",
    "openai",
    "google_grounding_lite",
    "google_geocoding",
    "google_places",
    "google_routes",
]
Capability = Literal[
    "location", "discovery", "route", "reviews", "web_research", "model"
]
Use = Literal["api_diagnostic", "model_inference"]
InputSource = Literal[
    "geoapify_geography",
    "public_google_reviews",
    "minimized_google_reviews",
    "ordinary_google",
    "grounding_lite",
    "unknown_publisher",
]
ModelRole = Literal["owner", "research"]
Outcome = Literal[
    "success",
    "failed",
    "call_timeout",
    "attempt_timeout",
    "cancelled",
    "request_limit",
    "source_blocked",
    "nested_call",
    "completion_reserved",
]
FailureDiagnostic = Literal[
    "invalid_response",
    "access_failure",
    "response_limit",
    "transport_failure",
    "operation_timeout",
    "context_limit",
    "usage_limit",
    "turn_limit",
    "tool_limit",
]
Diagnostic = Literal[
    "none",
    "unknown_failure",
    "invalid_response",
    "access_failure",
    "response_limit",
    "transport_failure",
    "operation_timeout",
    "context_limit",
    "usage_limit",
    "turn_limit",
    "tool_limit",
]


class CallFailure(Exception):
    """An adapter failure containing only a closed diagnostic code."""

    def __init__(self, diagnostic: FailureDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(diagnostic)


Seconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0)]
_MODELS = TypeAdapter(ChatModel)
_IN_TRANSPORT: ContextVar[bool] = ContextVar("in_call_transport", default=False)
_COMPLETION_SCOPE: ContextVar[tuple[object, asyncio.Task[object]] | None] = ContextVar(
    "completion_scope", default=None
)


class _Closed(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class Bounds(_Closed):
    max_requests: Annotated[int, Field(gt=0)]
    total_seconds: Seconds


class CallSpec(_Closed):
    """Declare every imported input origin. An empty tuple means none.

    Model input requires accurate lineage, including mixed origins. Matched public
    reviews use public_google_reviews; other Google content uses ordinary_google.
    This gate grants no storage or tracing.
    """

    capability: Capability
    use: Use
    input_sources: tuple[InputSource, ...]
    model_role: ModelRole | None = None

    @model_validator(mode="after")
    def _role(self) -> CallSpec:
        if (self.capability in {"model", "web_research"}) != (
            self.model_role is not None
        ):
            raise ValueError("model role does not match capability")
        return self


class Usage(_Closed):
    input_tokens: Count | None = None
    output_tokens: Count | None = None
    requests: Count | None = None


class Cost(_Closed):
    """Observed provider USD amount. Missing cost must remain unknown."""

    usd: Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


@dataclass(frozen=True)
class Parsed[T]:
    value: T = field(repr=False)
    usage: Usage | None = None
    cost: Cost | None = None


class Receipt(_Closed):
    local_request_id: UUID
    provider: Provider
    capability: Capability
    use: Use
    input_sources: tuple[InputSource, ...]
    model_role: ModelRole | None
    requested_model: ChatModel | None
    model_identity: Literal["named", "omitted", "not_applicable"]
    started_at: datetime
    duration_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    admitted: bool
    outcome: Outcome
    diagnostic: Diagnostic
    usage: Usage | None
    cost: Cost | None

    def safe_record(self) -> dict[str, object]:
        """Project only the closed receipt schema for persistence or display."""
        return {
            "local_request_id": str(self.local_request_id),
            "provider": self.provider,
            "capability": self.capability,
            "use": self.use,
            "input_sources": list(self.input_sources),
            "model_role": self.model_role,
            "requested_model": self.requested_model,
            "model_identity": self.model_identity,
            "started_at": self.started_at.isoformat(),
            "duration_seconds": self.duration_seconds,
            "admitted": self.admitted,
            "outcome": self.outcome,
            "diagnostic": self.diagnostic,
            "usage": None
            if self.usage is None
            else {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
                "requests": self.usage.requests,
            },
            "cost": None if self.cost is None else {"usd": str(self.cost.usd)},
        }


@dataclass(frozen=True)
class Completed[T]:
    value: T = field(repr=False)
    receipt: Receipt


@dataclass(frozen=True)
class Stopped:
    receipt: Receipt


class AttemptCalls:
    """One attempt confined to its creating event loop.

    Admission consumes one request without suspension and never refunds it.
    History holds only admitted completion receipts, at most max_requests.
    Rejections return receipts without adding to history. Running calls appear
    only when they finish or are cancelled.
    """

    def __init__(
        self,
        settings: Settings | ReviewSettings,
        bounds: Bounds,
        *,
        research_budget: ResearchBudget | None = None,
    ) -> None:
        from app.agents.research_budget import ResearchBudget

        bounds = Bounds.model_validate(bounds)
        self._loop = asyncio.get_running_loop()
        self._deadline = self._loop.time() + bounds.total_seconds
        if not math.isfinite(self._deadline):
            raise ValueError("attempt deadline must be finite")
        self._settings = settings
        self._remaining = bounds.max_requests
        self._receipts: list[Receipt] = []
        self._research_budget = research_budget or ResearchBudget()
        self._completion_reserved = False
        self._completion_request = 0
        self._completion_seconds = 0.0
        self._completion_inflight = False

    def reserve_completion(self, *, seconds: float, usage: int) -> None:
        self._check_loop()
        if self._completion_reserved:
            raise ValueError("completion reserve already configured")
        if (
            type(seconds) not in (float, int)
            or not math.isfinite(seconds)
            or seconds <= 0
            or self._loop.time() + seconds >= self._deadline
            or self._remaining < 1
        ):
            raise ValueError("completion reserve exceeds remaining attempt")
        self._research_budget.protect_completion(usage)
        self._completion_reserved = True
        self._completion_request = 1
        self._completion_seconds = float(seconds)

    def grow_completion_reserve(self, usage: int) -> bool:
        self._check_loop()
        if not self._completion_reserved or not self._completion_request:
            raise ValueError("completion reserve is not active")
        return self._research_budget.grow_completion_reserve(usage)

    def can_research(self) -> bool:
        self._check_loop()
        return (
            self._remaining > self._completion_request
            and self._loop.time() < self._deadline - self._completion_seconds
            and self._research_budget.can_research()
        )

    def completion_unavailable_reason(self, estimated_usage: int = 0) -> str | None:
        self._check_loop()
        if type(estimated_usage) is not int or estimated_usage < 0:
            raise ValueError("estimated usage must be nonnegative")
        if self._loop.time() >= self._deadline:
            return "attempt_timeout"
        if self._remaining <= 0:
            return "request_limit"
        if self._completion_inflight:
            return "completion_inflight"
        if estimated_usage > self._research_budget.context_limit:
            return "context_limit"
        if self._research_budget.turns >= self._research_budget.max_turns:
            return "turn_limit"
        if (
            self._research_budget.charged + estimated_usage
            > self._research_budget.usage_limit
        ):
            return "usage_limit"
        return None

    def can_complete(self, estimated_usage: int = 0) -> bool:
        return self.completion_unavailable_reason(estimated_usage) is None

    @contextmanager
    def completion(self) -> Iterator[None]:
        self._check_loop()
        if not self._completion_reserved:
            raise ValueError("completion reserve is not configured")
        if _COMPLETION_SCOPE.get() is not None:
            raise ValueError("nested completion scope")
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("completion requires an active task")
        token = _COMPLETION_SCOPE.set((self, task))
        try:
            yield
        finally:
            _COMPLETION_SCOPE.reset(token)

    @property
    def remaining_requests(self) -> int:
        self._check_loop()
        return self._remaining

    @property
    def remaining_seconds(self) -> float:
        self._check_loop()
        return max(0.0, self._deadline - self._loop.time())

    @property
    def research_budget(self) -> ResearchBudget:
        return self._research_budget

    @property
    def settings(self) -> Settings:
        """Return the immutable configuration used for this attempt's receipts."""
        if not isinstance(self._settings, Settings):
            raise TypeError("review-only attempt has no general settings")
        return self._settings

    @property
    def review_settings(self) -> ReviewSettings:
        return ReviewSettings(
            mode=self._settings.mode,
            review_provider=self._settings.review_provider,
            review_timeout_seconds=self._settings.review_timeout_seconds,
        )

    @property
    def receipts(self) -> tuple[Receipt, ...]:
        self._check_loop()
        return tuple(self._receipts)

    def _check_loop(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("attempt belongs to another event loop")

    async def run[T](
        self,
        spec: CallSpec,
        invoke_once: Callable[[float], Awaitable[Parsed[T]]],
    ) -> Completed[T] | Stopped:
        """Invoke a trusted, cancellable callback for exactly one transmission.

        The callback disables retries, redirects, pagination, reconnect, polling,
        and background work, or admits each transmission separately as a sibling
        run. Nested run calls, including inherited child tasks, are rejected.
        Orchestration and hosted tool runs require their own accounting contract.
        The callback parses transport data into Parsed and receives effective
        seconds. This boundary cannot police it or stop remote dispatched work.
        Adapters must separately prove SDK retry and content-tracing controls.
        Results are ephemeral. Only Receipt.safe_record is a storage surface.
        """
        self._check_loop()
        spec = CallSpec.model_validate(spec)
        provider: Provider
        if spec.capability == "reviews":
            if (
                self._settings.review_provider != "google_reviews"
                or self._settings.review_timeout_seconds is None
            ):
                raise ValueError(
                    "configure GG_REVIEW_PROVIDER and GG_REVIEW_TIMEOUT_SECONDS"
                )
            provider = "google_reviews"
            timeout = self._settings.review_timeout_seconds
        elif spec.capability in {"model", "web_research"}:
            provider = self.settings.web_provider
            timeout = self.settings.model_timeout_seconds
        else:
            providers: dict[Capability, Provider] = {
                "location": self.settings.location_provider,
                "discovery": self.settings.discovery_provider,
                "route": self.settings.route_provider,
            }
            provider = providers[spec.capability]
            timeout = self.settings.geographic_timeout_seconds
        model: ChatModel | None = None
        identity: Literal["named", "omitted", "not_applicable"] = "not_applicable"
        if spec.model_role is not None:
            configured = (
                self.settings.owner_model
                if spec.model_role == "owner"
                else self.settings.research_model
            )
            secrets = (
                self.settings.openai_api_key,
                self.settings.google_maps_api_key,
                self.settings.geoapify_api_key,
            )
            identity = "omitted"
            if not any(
                key is not None and key.get_secret_value() in configured
                for key in secrets
            ):
                try:
                    model = _MODELS.validate_python(configured, strict=True)
                except ValidationError:
                    pass
                else:
                    identity = "named"
        request_id, started_at = uuid4(), datetime.now(UTC)
        started = self._loop.time()
        admitted = False

        def finish(
            outcome: Outcome,
            diagnostic: Diagnostic = "none",
            parsed: Parsed[T] | None = None,
        ) -> Receipt:
            receipt = Receipt(
                local_request_id=request_id,
                provider=provider,
                capability=spec.capability,
                use=spec.use,
                input_sources=spec.input_sources,
                model_role=spec.model_role,
                requested_model=model,
                model_identity=identity,
                started_at=started_at,
                duration_seconds=max(0.0, self._loop.time() - started),
                admitted=admitted,
                outcome=outcome,
                diagnostic=diagnostic,
                usage=None if parsed is None else parsed.usage,
                cost=None if parsed is None else parsed.cost,
            )
            if admitted:
                self._receipts.append(receipt)
            return receipt

        if _IN_TRANSPORT.get():
            return Stopped(finish("nested_call"))
        if provider == "openai" or spec.use == "model_inference":
            try:
                require_source_use(spec.input_sources, "model")
                if provider != "openai" and any(
                    source in {"public_google_reviews", "minimized_google_reviews"}
                    for source in spec.input_sources
                ):
                    raise SourceUseDenied("Google review model input requires OpenAI")
                if spec.use != "model_inference" and any(
                    source in {"public_google_reviews", "minimized_google_reviews"}
                    for source in spec.input_sources
                ):
                    raise SourceUseDenied("Google review input requires inference")
                if provider.startswith("google_") and spec.use == "model_inference":
                    raise SourceUseDenied("Google model inference is not enabled")
            except SourceUseDenied:
                return Stopped(finish("source_blocked"))
        if started >= self._deadline:
            return Stopped(finish("attempt_timeout"))
        scope = _COMPLETION_SCOPE.get()
        completion = bool(
            scope is not None
            and scope == (self, asyncio.current_task())
            and spec.capability == "model"
            and spec.model_role == "owner"
            and spec.use == "model_inference"
        )
        if scope is not None and (not completion or self._completion_inflight):
            return Stopped(finish("completion_reserved"))
        if self._remaining == 0:
            return Stopped(finish("request_limit"))
        research_deadline = self._deadline - self._completion_seconds
        if not completion and (
            self._remaining <= self._completion_request or started >= research_deadline
        ):
            return Stopped(finish("completion_reserved"))
        self._remaining -= 1
        admitted = True
        if completion:
            self._completion_request = 0
            self._completion_inflight = True
        call_deadline = min(
            started + timeout, self._deadline if completion else research_deadline
        )
        reserve_cutoff = not completion and research_deadline <= started + timeout
        token = _IN_TRANSPORT.set(True)
        parsed: Parsed[T] | None = None
        outcome: Outcome = "failed"
        diagnostic: Diagnostic = "unknown_failure"
        try:
            async with asyncio.timeout_at(call_deadline):
                effective_seconds = call_deadline - self._loop.time()
                if effective_seconds <= 0:
                    raise TimeoutError
                with suppress(Exception):
                    try:
                        if completion:
                            with self._research_budget.completion_draw():
                                result = await invoke_once(effective_seconds)
                        else:
                            result = await invoke_once(effective_seconds)
                        if (
                            type(result) is not Parsed
                            or (
                                result.usage is not None
                                and type(result.usage) is not Usage
                            )
                            or (
                                result.cost is not None
                                and type(result.cost) is not Cost
                            )
                        ):
                            diagnostic = "invalid_response"
                        else:
                            parsed = Parsed(
                                result.value,
                                None
                                if result.usage is None
                                else Usage.model_validate(result.usage),
                                None
                                if result.cost is None
                                else Cost.model_validate(result.cost),
                            )
                            outcome, diagnostic = "success", "none"
                    except CallFailure as error:
                        diagnostic = error.diagnostic
                    except ValidationError:
                        diagnostic = "invalid_response"
        except asyncio.CancelledError:
            finish("cancelled")
            raise
        except TimeoutError:
            pass
        finally:
            _IN_TRANSPORT.reset(token)
            if completion:
                self._completion_inflight = False
        now = self._loop.time()
        if now >= self._deadline:
            outcome, diagnostic = "attempt_timeout", "none"
        elif now >= call_deadline:
            outcome, diagnostic = (
                ("completion_reserved", "none")
                if reserve_cutoff and self._completion_reserved
                else ("call_timeout", "none")
            )
        receipt = finish(outcome, diagnostic, parsed)
        if outcome == "success" and parsed is not None:
            return Completed(parsed.value, receipt)
        return Stopped(receipt)
