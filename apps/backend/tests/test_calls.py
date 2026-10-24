import asyncio
import json
from decimal import Decimal

import pytest
from pydantic import SecretStr, ValidationError

from app.core.calls import (
    AttemptCalls,
    Bounds,
    CallSpec,
    Completed,
    Cost,
    Parsed,
    Receipt,
    Stopped,
    Usage,
)
from app.core.settings import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings.model_construct(
        mode="test",
        location_provider="geoapify",
        discovery_provider="geoapify",
        route_provider="geoapify",
        web_provider="openai",
        owner_model="gpt-4o",
        research_model="gpt-4o-mini",
        geographic_timeout_seconds=1.0,
        model_timeout_seconds=1.0,
    )


def geographic(**changes: object) -> CallSpec:
    return CallSpec.model_validate(
        {"capability": "location", "use": "api_diagnostic", "input_sources": ()}
        | changes
    )


def research(**changes: object) -> CallSpec:
    return CallSpec.model_validate(
        {
            "capability": "web_research",
            "use": "model_inference",
            "input_sources": (),
            "model_role": "research",
        }
        | changes
    )


@pytest.mark.asyncio
async def test_success_reports_observed_metadata_without_payload(
    settings: Settings,
) -> None:
    attempt = AttemptCalls(settings, Bounds(max_requests=1, total_seconds=2.0))
    payload = "private payload, source text, location, and URL"

    async def invoke(seconds: float) -> Parsed[str]:
        assert 0 < seconds <= 1.0
        return Parsed(
            payload, Usage(input_tokens=3, output_tokens=5), Cost(usd=Decimal("0.02"))
        )

    result = await attempt.run(research(), invoke)
    assert isinstance(result, Completed)
    assert result.value == payload
    assert result.receipt.outcome == "success"
    assert result.receipt.requested_model == "gpt-4o-mini"
    assert result.receipt.model_identity == "named"
    offset = result.receipt.started_at.utcoffset()
    assert offset is not None and offset.total_seconds() == 0
    assert result.receipt.safe_record()["usage"] == {
        "input_tokens": 3,
        "output_tokens": 5,
        "requests": None,
    }
    assert result.receipt.safe_record()["cost"] == {"usd": "0.02"}
    assert payload not in repr(result)
    assert payload not in repr(Parsed(payload))
    assert payload not in json.dumps(result.receipt.safe_record())
    assert attempt.receipts == (result.receipt,)


@pytest.mark.asyncio
async def test_last_slot_is_atomic_and_rejections_do_not_grow_history(
    settings: Settings,
) -> None:
    attempt = AttemptCalls(settings, Bounds(max_requests=1, total_seconds=2.0))
    entered, release = asyncio.Event(), asyncio.Event()
    invocations = 0

    async def invoke(seconds: float) -> Parsed[int]:
        nonlocal invocations
        invocations += 1
        entered.set()
        await release.wait()
        return Parsed(7)

    first = asyncio.create_task(attempt.run(geographic(), invoke))
    await entered.wait()
    rejected = await asyncio.gather(
        *(attempt.run(geographic(), invoke) for _ in range(20))
    )
    assert all(isinstance(item, Stopped) for item in rejected)
    assert {item.receipt.outcome for item in rejected} == {"request_limit"}
    assert all(not item.receipt.admitted for item in rejected)
    assert attempt.receipts == ()
    release.set()
    completed = await first
    assert isinstance(completed, Completed)
    assert invocations == 1
    assert len(attempt.receipts) == 1
    assert completed.receipt.usage is None
    assert completed.receipt.cost is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source", ["ordinary_google", "grounding_lite", "unknown_publisher"]
)
async def test_mixed_model_inputs_are_rejected_before_invocation(
    settings: Settings, source: str
) -> None:
    attempt = AttemptCalls(settings, Bounds(max_requests=1, total_seconds=2.0))
    invoked = False

    async def invoke(seconds: float) -> Parsed[int]:
        nonlocal invoked
        invoked = True
        return Parsed(1)

    result = await attempt.run(
        research(input_sources=("geoapify_geography", source)), invoke
    )
    assert isinstance(result, Stopped)
    assert result.receipt.outcome == "source_blocked"
    assert not invoked
    assert attempt.receipts == ()
    allowed = await attempt.run(research(input_sources=("geoapify_geography",)), invoke)
    assert isinstance(allowed, Completed)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google_grounding_lite", "google_geocoding"])
async def test_google_output_is_diagnostic_only(
    settings: Settings, provider: str
) -> None:
    configured = settings.model_copy(update={"location_provider": provider})
    attempt = AttemptCalls(configured, Bounds(max_requests=1, total_seconds=2.0))
    invoked = 0

    async def invoke(seconds: float) -> Parsed[int]:
        nonlocal invoked
        invoked += 1
        return Parsed(1)

    blocked = await attempt.run(geographic(use="model_inference"), invoke)
    assert blocked.receipt.outcome == "source_blocked"
    assert not blocked.receipt.admitted
    diagnostic = await attempt.run(geographic(), invoke)
    assert isinstance(diagnostic, Completed)
    assert invoked == 1
    assert diagnostic.receipt.provider == provider


@pytest.mark.asyncio
async def test_failure_is_safe_unknown_metering_and_never_refunded(
    settings: Settings,
) -> None:
    attempt = AttemptCalls(settings, Bounds(max_requests=1, total_seconds=2.0))
    unsafe = "https://private.invalid/?key=forbidden with publisher text"

    async def invoke(seconds: float) -> Parsed[int]:
        raise RuntimeError(unsafe)

    result = await attempt.run(geographic(), invoke)
    assert isinstance(result, Stopped)
    assert result.receipt.outcome == "failed"
    assert result.receipt.diagnostic == "unknown_failure"
    assert result.receipt.usage is None and result.receipt.cost is None
    assert unsafe not in repr(result)
    assert unsafe not in json.dumps(result.receipt.safe_record())
    assert (await attempt.run(geographic(), invoke)).receipt.outcome == "request_limit"
    assert len(attempt.receipts) == 1


@pytest.mark.asyncio
async def test_cancellation_records_admission_and_propagates(
    settings: Settings,
) -> None:
    attempt = AttemptCalls(settings, Bounds(max_requests=1, total_seconds=2.0))
    entered = asyncio.Event()

    async def invoke(seconds: float) -> Parsed[int]:
        entered.set()
        await asyncio.Future()
        return Parsed(1)

    task = asyncio.create_task(attempt.run(geographic(), invoke))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(attempt.receipts) == 1
    assert attempt.receipts[0].outcome == "cancelled"
    assert attempt.receipts[0].admitted
    assert (await attempt.run(geographic(), invoke)).receipt.outcome == "request_limit"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "total,per_call,expected",
    [
        (1.0, 0.01, "call_timeout"),
        (0.01, 1.0, "attempt_timeout"),
        (0.01, 0.01, "attempt_timeout"),
    ],
)
async def test_deadline_precedence_and_late_completion(
    settings: Settings, total: float, per_call: float, expected: str
) -> None:
    configured = settings.model_copy(update={"geographic_timeout_seconds": per_call})
    attempt = AttemptCalls(configured, Bounds(max_requests=1, total_seconds=total))

    async def invoke(seconds: float) -> Parsed[int]:
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            return Parsed(99)
        return Parsed(1)

    result = await attempt.run(geographic(), invoke)
    assert isinstance(result, Stopped)
    assert result.receipt.outcome == expected
    assert result.receipt.admitted
    assert len(attempt.receipts) == 1


@pytest.mark.asyncio
async def test_shared_deadline_starts_at_construction(settings: Settings) -> None:
    attempt = AttemptCalls(settings, Bounds(max_requests=1, total_seconds=0.001))
    await asyncio.sleep(0.01)

    async def invoke(seconds: float) -> Parsed[int]:
        pytest.fail("expired attempt invoked transport")

    result = await attempt.run(geographic(), invoke)
    assert result.receipt.outcome == "attempt_timeout"
    assert not result.receipt.admitted
    assert attempt.receipts == ()


@pytest.mark.asyncio
async def test_adapter_timeout_is_not_executor_timeout(settings: Settings) -> None:
    attempt = AttemptCalls(settings, Bounds(max_requests=1, total_seconds=2.0))

    async def invoke(seconds: float) -> Parsed[int]:
        raise TimeoutError("untrusted transport message")

    result = await attempt.run(geographic(), invoke)
    assert result.receipt.outcome == "failed"
    assert result.receipt.diagnostic == "unknown_failure"


@pytest.mark.asyncio
async def test_nested_calls_and_child_tasks_are_not_admitted(
    settings: Settings,
) -> None:
    attempt = AttemptCalls(settings, Bounds(max_requests=3, total_seconds=2.0))

    async def leaf(seconds: float) -> Parsed[int]:
        pytest.fail("nested callback was invoked")

    async def outer(seconds: float) -> Parsed[int]:
        direct = await attempt.run(geographic(), leaf)
        child = await asyncio.create_task(attempt.run(geographic(), leaf))
        assert direct.receipt.outcome == "nested_call"
        assert child.receipt.outcome == "nested_call"
        return Parsed(42)

    result = await attempt.run(geographic(), outer)
    assert isinstance(result, Completed)
    assert result.value == 42
    assert len(attempt.receipts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "configured",
    ["arbitrary private setting", "https://private.invalid/model", "gpt-4o-mini"],
)
async def test_unknown_model_or_credential_match_is_omitted(
    settings: Settings, configured: str
) -> None:
    configured_settings = settings.model_copy(
        update={
            "research_model": configured,
            "openai_api_key": SecretStr("gpt-4o-mini"),
        }
    )
    attempt = AttemptCalls(
        configured_settings, Bounds(max_requests=1, total_seconds=2.0)
    )

    async def invoke(seconds: float) -> Parsed[int]:
        return Parsed(1)

    result = await attempt.run(research(), invoke)
    assert result.receipt.requested_model is None
    assert result.receipt.model_identity == "omitted"
    with pytest.raises(ValidationError):
        Receipt.model_validate(
            result.receipt.model_dump() | {"requested_model": "unreviewed model text"}
        )
    assert configured not in json.dumps(result.receipt.safe_record())
    assert configured not in repr(result)


@pytest.mark.asyncio
async def test_invalid_adapter_metadata_is_not_serialized(settings: Settings) -> None:
    attempt = AttemptCalls(settings, Bounds(max_requests=1, total_seconds=2.0))

    async def invoke(seconds: float) -> Parsed[int]:
        return Parsed(1, usage=Usage(input_tokens=-1))

    result = await attempt.run(geographic(), invoke)
    assert result.receipt.outcome == "failed"
    assert result.receipt.diagnostic == "invalid_response"
    assert result.receipt.usage is None


@pytest.mark.parametrize("invalid", [True, False, 0, -1, 1.5])
def test_request_bounds_are_positive_non_bool_integers(invalid: object) -> None:
    with pytest.raises(ValidationError):
        Bounds.model_validate({"max_requests": invalid, "total_seconds": 1.0})


@pytest.mark.parametrize("invalid", [True, 0.0, -1.0, float("inf"), float("nan")])
def test_deadline_bounds_are_positive_and_finite(invalid: object) -> None:
    with pytest.raises(ValidationError):
        Bounds.model_validate({"max_requests": 1, "total_seconds": invalid})


@pytest.mark.parametrize("invalid", [True, -1, 0.5, "123", float("nan")])
def test_usage_is_safe_numeric_metadata(invalid: object) -> None:
    with pytest.raises(ValidationError):
        Usage.model_validate({"input_tokens": invalid})


@pytest.mark.parametrize(
    "invalid", [Decimal(-1), Decimal("NaN"), Decimal("Infinity"), True, "0.1"]
)
def test_cost_is_nonnegative_finite_observed_decimal(invalid: object) -> None:
    with pytest.raises(ValidationError):
        Cost.model_validate({"usd": invalid})


def test_closed_metadata_rejects_raw_fields_and_arbitrary_model_names() -> None:
    with pytest.raises(ValidationError):
        Usage.model_validate({"url": "forbidden"})
    with pytest.raises(ValidationError):
        geographic(model_role="owner")
    with pytest.raises(ValidationError):
        research(model_role=None)


def test_attempt_cannot_cross_event_loops(settings: Settings) -> None:
    async def create() -> AttemptCalls:
        return AttemptCalls(settings, Bounds(max_requests=1, total_seconds=1.0))

    attempt = asyncio.run(create())

    async def inspect() -> None:
        with pytest.raises(RuntimeError, match="another event loop"):
            _ = attempt.receipts

    asyncio.run(inspect())


@pytest.mark.asyncio
async def test_minimized_google_review_lineage_is_narrowly_admitted(
    settings: Settings,
) -> None:
    attempt = AttemptCalls(settings, Bounds(max_requests=2, total_seconds=2.0))
    invoked = 0

    async def invoke(seconds: float) -> Parsed[int]:
        nonlocal invoked
        invoked += 1
        return Parsed(7)

    allowed = await attempt.run(
        research(input_sources=("geoapify_geography", "minimized_google_reviews")),
        invoke,
    )
    assert isinstance(allowed, Completed) and allowed.value == 7
    assert allowed.receipt.input_sources == (
        "geoapify_geography",
        "minimized_google_reviews",
    )
    for source in ("ordinary_google", "grounding_lite", "unknown_publisher"):
        blocked = await attempt.run(
            research(input_sources=("minimized_google_reviews", source)), invoke
        )
        assert (
            blocked.receipt.outcome == "source_blocked" and not blocked.receipt.admitted
        )
    diagnostic = await attempt.run(
        research(use="api_diagnostic", input_sources=("minimized_google_reviews",)),
        invoke,
    )
    assert diagnostic.receipt.outcome == "source_blocked"
    geographic_model = await attempt.run(
        geographic(use="model_inference", input_sources=("minimized_google_reviews",)),
        invoke,
    )
    assert geographic_model.receipt.outcome == "source_blocked"
    assert invoked == 1


@pytest.mark.asyncio
async def test_reviews_use_independent_configuration_and_shared_accounting() -> None:
    from app.core.settings import ReviewSettings

    configured = ReviewSettings(
        mode="test", review_provider="google_reviews", review_timeout_seconds=0.5
    )
    attempt = AttemptCalls(configured, Bounds(max_requests=1, total_seconds=2.0))

    async def invoke(seconds: float) -> Parsed[int]:
        assert 0 < seconds <= 0.5
        return Parsed(9, Usage(requests=1))

    review_spec = CallSpec(
        capability="reviews", use="api_diagnostic", input_sources=("ordinary_google",)
    )
    result = await attempt.run(review_spec, invoke)
    assert isinstance(result, Completed) and result.value == 9
    assert result.receipt.provider == "google_reviews"
    assert result.receipt.model_identity == "not_applicable"
    assert attempt.review_settings == configured
    assert len(attempt.receipts) == 1
    assert (await attempt.run(review_spec, invoke)).receipt.outcome == "request_limit"
    with pytest.raises(TypeError, match="review-only attempt"):
        _ = attempt.settings


@pytest.mark.asyncio
async def test_google_review_provider_cannot_perform_model_inference(
    settings: Settings,
) -> None:
    configured = settings.model_copy(
        update={"review_provider": "google_reviews", "review_timeout_seconds": 1.0}
    )
    attempt = AttemptCalls(configured, Bounds(max_requests=1, total_seconds=2.0))

    async def invoke(seconds: float) -> Parsed[int]:
        pytest.fail("Google provider performed model inference")

    blocked = await attempt.run(
        CallSpec(
            capability="reviews",
            use="model_inference",
            input_sources=("minimized_google_reviews",),
        ),
        invoke,
    )
    assert blocked.receipt.provider == "google_reviews"
    assert blocked.receipt.outcome == "source_blocked" and not blocked.receipt.admitted
    assert attempt.receipts == ()


@pytest.mark.asyncio
async def test_closed_adapter_failure_produces_failed_safe_receipt(
    settings: Settings,
) -> None:
    from app.core.calls import CallFailure

    attempt = AttemptCalls(settings, Bounds(max_requests=1, total_seconds=2.0))

    async def invoke(seconds: float) -> Parsed[int]:
        raise CallFailure("response_limit")

    result = await attempt.run(geographic(), invoke)
    assert result.receipt.outcome == "failed"
    assert result.receipt.diagnostic == "response_limit"
    assert result.receipt.admitted and result.receipt.usage is None
