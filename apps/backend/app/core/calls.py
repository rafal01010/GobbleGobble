from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal
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

from app.core.settings import Settings

Provider = Literal[
    "geoapify",
    "openai",
    "google_grounding_lite",
    "google_geocoding",
    "google_places",
    "google_routes",
]
Capability = Literal["location", "discovery", "route", "web_research", "model"]
Use = Literal["api_diagnostic", "model_inference"]
InputSource = Literal[
    "geoapify_geography",
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
]
Diagnostic = Literal["none", "unknown_failure", "invalid_response"]
Seconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0)]
_MODELS = TypeAdapter(ChatModel)
_IN_TRANSPORT: ContextVar[bool] = ContextVar("in_call_transport", default=False)


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

    Model input requires accurate lineage, including mixed origins. This gate
    grants no storage, tracing, attribution, or derived-output permission.
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

    def __init__(self, settings: Settings, bounds: Bounds) -> None:
        bounds = Bounds.model_validate(bounds)
        self._loop = asyncio.get_running_loop()
        self._deadline = self._loop.time() + bounds.total_seconds
        if not math.isfinite(self._deadline):
            raise ValueError("attempt deadline must be finite")
        self._settings = settings
        self._remaining = bounds.max_requests
        self._receipts: list[Receipt] = []

    @property
    def settings(self) -> Settings:
        """Return the immutable configuration used for this attempt's receipts."""
        return self._settings

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
        if spec.capability in {"model", "web_research"}:
            provider = self._settings.web_provider
            timeout = self._settings.model_timeout_seconds
        else:
            providers: dict[Capability, Provider] = {
                "location": self._settings.location_provider,
                "discovery": self._settings.discovery_provider,
                "route": self._settings.route_provider,
            }
            provider = providers[spec.capability]
            timeout = self._settings.geographic_timeout_seconds
        model: ChatModel | None = None
        identity: Literal["named", "omitted", "not_applicable"] = "not_applicable"
        if spec.model_role is not None:
            configured = (
                self._settings.owner_model
                if spec.model_role == "owner"
                else self._settings.research_model
            )
            secrets = (
                self._settings.openai_api_key,
                self._settings.google_maps_api_key,
                self._settings.geoapify_api_key,
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
        if (
            (provider == "openai" or spec.use == "model_inference")
            and any(source != "geoapify_geography" for source in spec.input_sources)
        ) or (provider.startswith("google_") and spec.use == "model_inference"):
            return Stopped(finish("source_blocked"))
        if started >= self._deadline:
            return Stopped(finish("attempt_timeout"))
        if self._remaining == 0:
            return Stopped(finish("request_limit"))
        self._remaining -= 1
        admitted = True
        call_deadline = min(started + timeout, self._deadline)
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
                    except ValidationError:
                        diagnostic = "invalid_response"
        except asyncio.CancelledError:
            finish("cancelled")
            raise
        except TimeoutError:
            pass
        finally:
            _IN_TRANSPORT.reset(token)
        now = self._loop.time()
        if now >= self._deadline:
            outcome, diagnostic = "attempt_timeout", "none"
        elif now >= call_deadline:
            outcome, diagnostic = "call_timeout", "none"
        receipt = finish(outcome, diagnostic, parsed)
        if outcome == "success" and parsed is not None:
            return Completed(parsed.value, receipt)
        return Stopped(receipt)
