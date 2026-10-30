import asyncio

import pytest
from test_calls import geographic

pytest_plugins = ["test_calls"]

from app.agents.research_budget import ResearchBudget, ResearchLimit
from app.core.calls import AttemptCalls, Bounds, CallSpec, Completed, Parsed, Stopped


def owner_spec():
    return CallSpec(
        capability="model", use="model_inference", input_sources=(), model_role="owner"
    )


async def value(seconds):
    return Parsed("done")


@pytest.mark.asyncio
async def test_last_request_is_reserved_for_owner_composition(settings):
    calls = AttemptCalls(settings, Bounds(max_requests=2, total_seconds=3.0))
    calls.reserve_completion(seconds=0.5, usage=3000)
    assert calls.can_research() and calls.can_complete(3000)
    first = await calls.run(geographic(), value)
    blocked = await calls.run(geographic(), value)
    assert isinstance(first, Completed) and first.value == "done"
    assert (
        isinstance(blocked, Stopped)
        and blocked.receipt.outcome == "completion_reserved"
    )
    assert not blocked.receipt.admitted and not calls.can_research()
    with calls.completion():
        final = await calls.run(owner_spec(), value)
    assert isinstance(final, Completed) and final.value == "done"
    assert len(calls.receipts) == 2
    assert not calls.can_complete()


@pytest.mark.asyncio
async def test_parallel_research_cannot_spend_reserved_last_slot(settings):
    calls = AttemptCalls(settings, Bounds(max_requests=3, total_seconds=3.0))
    calls.reserve_completion(seconds=0.5, usage=3000)
    entered = 0
    release = asyncio.Event()

    async def delayed(seconds):
        nonlocal entered
        entered += 1
        await release.wait()
        return Parsed("research")

    jobs = [asyncio.create_task(calls.run(geographic(), delayed)) for _ in range(5)]
    await asyncio.sleep(0)
    assert entered == 2
    release.set()
    results = await asyncio.gather(*jobs)
    assert sum(isinstance(r, Completed) for r in results) == 2
    assert all(
        r.receipt.outcome == "completion_reserved"
        for r in results
        if isinstance(r, Stopped)
    )
    with calls.completion():
        final = await calls.run(owner_spec(), value)
    assert isinstance(final, Completed)


@pytest.mark.asyncio
async def test_completion_scope_cannot_dispatch_tools_or_be_inherited(settings):
    calls = AttemptCalls(settings, Bounds(max_requests=4, total_seconds=3.0))
    calls.reserve_completion(seconds=0.5, usage=3000)
    with calls.completion():
        foreign = await asyncio.create_task(calls.run(owner_spec(), value))
        geographic_result = await calls.run(geographic(), value)
        research_result = await calls.run(
            CallSpec(
                capability="model",
                use="model_inference",
                input_sources=(),
                model_role="research",
            ),
            value,
        )
        final = await calls.run(owner_spec(), value)
    assert [
        x.receipt.outcome for x in (foreign, geographic_result, research_result)
    ] == ["completion_reserved"] * 3
    assert isinstance(final, Completed)
    assert len(calls.receipts) == 1


@pytest.mark.asyncio
async def test_shared_usage_and_turn_reserve_survive_research(settings):
    budget = ResearchBudget(
        context_limit=5000, usage_limit=1500, max_turns=2, headroom=10
    )
    calls = AttemptCalls(
        settings, Bounds(max_requests=4, total_seconds=3.0), research_budget=budget
    )
    calls.reserve_completion(seconds=0.5, usage=800)

    async def research(seconds):
        allocation = budget.reserve({"input": "small"}, 200)
        budget.finish(allocation, None, "success")
        return Parsed("research")

    assert isinstance(await calls.run(geographic(), research), Completed)
    assert not calls.can_research()
    with pytest.raises(ResearchLimit) as failure:
        budget.reserve({"input": "small"}, 200)
    assert failure.value.reason == "turn_limit"

    async def compose(seconds):
        allocation = budget.reserve({"input": "final"}, 700)
        budget.finish(allocation, None, "success")
        return Parsed("final")

    with calls.completion():
        final = await calls.run(owner_spec(), compose)
    assert isinstance(final, Completed) and final.value == "final"
    assert budget.turns == 2 and len(budget.records) == 2
    assert not calls.can_complete()


@pytest.mark.asyncio
async def test_research_timeout_preserves_completion_interval(settings):
    calls = AttemptCalls(settings, Bounds(max_requests=2, total_seconds=0.5))
    calls.reserve_completion(seconds=0.3, usage=3000)
    received = []

    async def slow(seconds):
        received.append(seconds)
        await asyncio.sleep(2)
        return Parsed("late")

    result = await calls.run(geographic(), slow)
    assert isinstance(result, Stopped)
    assert result.receipt.outcome == "completion_reserved"
    assert result.receipt.admitted and received[0] < 0.25
    assert not calls.can_research() and calls.can_complete(3000)
    with calls.completion():
        assert isinstance(await calls.run(owner_spec(), value), Completed)


@pytest.mark.asyncio
async def test_reserve_is_single_use_and_legacy_attempts_are_unchanged(settings):
    calls = AttemptCalls(settings, Bounds(max_requests=1, total_seconds=3.0))
    assert isinstance(await calls.run(geographic(), value), Completed)
    assert not calls.can_research()
    reserved = AttemptCalls(settings, Bounds(max_requests=2, total_seconds=3.0))
    reserved.reserve_completion(seconds=0.5, usage=3000)
    with pytest.raises(ValueError, match="already configured"):
        reserved.reserve_completion(seconds=0.5, usage=3000)


@pytest.mark.asyncio
async def test_usage_margin_cannot_be_spent_by_research(settings):
    budget = ResearchBudget(
        context_limit=5000, usage_limit=1200, max_turns=10, headroom=10
    )
    calls = AttemptCalls(
        settings, Bounds(max_requests=3, total_seconds=3.0), research_budget=budget
    )
    calls.reserve_completion(seconds=0.5, usage=800)
    with pytest.raises(ResearchLimit) as failure:
        budget.reserve({"request": "distinct research"}, 400)
    assert failure.value.reason == "usage_limit"
    assert budget.turns == 0 and budget.charged == 0

    async def compose(seconds):
        allocation = budget.reserve({"request": "final"}, 700)
        budget.finish(allocation, None, "success")
        return Parsed("final")

    with calls.completion():
        result = await calls.run(owner_spec(), compose)
    assert isinstance(result, Completed) and result.value == "final"
    assert budget.turns == 1 and budget.records[0].reserved_output == 700
