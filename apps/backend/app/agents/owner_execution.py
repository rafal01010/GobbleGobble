from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from agents import ModelSettings, RunConfig, Runner
from agents.agent_output import AgentOutputSchema, AgentOutputSchemaBase
from agents.exceptions import AgentsException
from agents.handoffs import Handoff
from agents.items import ModelResponse, TResponseInputItem, TResponseStreamEvent
from agents.models.interface import Model, ModelTracing
from agents.tool import FunctionTool, Tool
from agents.tool_context import ToolContext
from openai import APIError
from openai.types.responses.response_prompt_param import ResponsePromptParam
from pydantic import ValidationError

from app.agents.owner import PreparedOwner, eligible_claim_ids
from app.agents.owner_draft import OwnerDraft, validate_owner_draft
from app.agents.probe import _AdmissionStopped, _RecordedModel
from app.agents.research import guard_review_context
from app.core.calls import AttemptCalls, Receipt
from app.core.source_policy import SourceUseDenied, require_source_use
from app.research_tools import LookupInput, TextInput
from app.schemas.recommendations import RecommendationCollection


@dataclass(frozen=True)
class OwnerOutcome:
    status: Literal["completed", "partial", "technical_failure"]
    phase: Literal["finished", "failed"]
    collection: RecommendationCollection | None = field(default=None, repr=False)
    reason: str | None = None
    research_closed: bool = False
    recovery_attempted: bool = False
    retained: tuple[dict[str, object], ...] = field(default=(), repr=False)
    deferred_references: tuple[str, ...] = field(default=(), repr=False)


@dataclass
class _OwnerState:
    research_turns: int
    turns: int = 0
    closed: bool = False
    retained: list[dict[str, object]] = field(default_factory=list, repr=False)
    omissions: int = 0
    deferred_references: set[str] = field(default_factory=set, repr=False)
    force_compaction: bool = False
    last_composition_size: int = 0


def _compact(value: object, deferred: set[str], *, minimum_bytes: int = 1024) -> object:
    if isinstance(value, dict):
        result = {
            key: _compact(item, deferred, minimum_bytes=minimum_bytes)
            for key, item in value.items()
        }
        review = result.get("original_review")
        identifier = result.get("review_id")
        if (
            isinstance(review, str)
            and len(review.encode()) > minimum_bytes
            and isinstance(identifier, str)
            and identifier
        ):
            result["original_review"] = None
            result["review_context_omitted"] = True
            deferred.add(identifier)
        return result
    if isinstance(value, list):
        return [_compact(item, deferred, minimum_bytes=minimum_bytes) for item in value]
    return value


def _references(value: object) -> list[str]:
    found: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if (
                    key
                    in {
                        "place_id",
                        "item_id",
                        "source_id",
                        "evidence_id",
                        "review_id",
                        "claim_id",
                    }
                    and isinstance(child, str)
                    and 0 < len(child) <= 128
                ):
                    found.add(child)
                elif key in {
                    "place_ids",
                    "source_ids",
                    "evidence_ids",
                    "review_ids",
                    "claim_ids",
                    "food_claim_ids",
                } and isinstance(child, list):
                    found.update(
                        x for x in child if isinstance(x, str) and 0 < len(x) <= 128
                    )
                else:
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return sorted(found)


def _reference_fields(value: object) -> dict[str, object]:
    refs = _references(value)
    if not refs:
        return {}
    return {
        "references": refs[:100],
        "omitted_reference_count": max(0, len(refs) - 100),
    }


def _fit_tool_result(value: object) -> object:
    deferred: set[str] = set()
    value = _compact(value, deferred, minimum_bytes=0)
    if deferred:
        if isinstance(value, dict):
            value["deferred_review_context_ids"] = sorted(deferred)
        else:
            value = {
                "status": "partial",
                "items": value,
                "deferred_review_context_ids": sorted(deferred),
            }
    if len(_json(value).encode()) <= 24000:
        return value
    if isinstance(value, dict) and isinstance(value.get("proposals"), list):
        keep = {
            key: value[key]
            for key in (
                "status",
                "place_id",
                "source_url",
                "source_id",
                "evidence_id",
                "evidence_ids",
                "gaps",
                "omitted_reviews",
                "next_index",
                "reacquisition_review_ids",
                "deferred_review_context_ids",
            )
            if key in value
        }
        proposals = value["proposals"]
        selected: list[object] = []
        skipped: list[object] = []
        for item in proposals:
            trial = {**keep, "proposals": [*selected, item]}
            if len(_json(trial).encode()) <= 20000:
                selected.append(item)
            else:
                skipped.append(item)
        keep["proposals"] = selected
        keep["deferred_proposal_count"] = len(skipped)
        keep["deferred_proposal_refs"] = _reference_fields(skipped)
        keep["omitted_fields"] = sorted(set(value) - set(keep) - {"proposals"})
        if len(_json(keep).encode()) <= 24000:
            return keep
    result: dict[str, object] = {"status": "oversized", "reason": "tool_result_limit"}
    result.update(_reference_fields(value))
    return result


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _refresh_observations(prepared: PreparedOwner, state: _OwnerState) -> bool:
    changed = False
    first_invalidated: int | None = None
    tools = prepared.research_tools
    for index, observation in enumerate(state.retained):
        prior_omissions = state.omissions
        name = observation.get("tool")
        args = observation.get("arguments")
        old = observation.get("result")
        if not isinstance(args, dict) or not isinstance(old, dict):
            continue
        if old.get("status") in {
            "unavailable",
            "restricted",
            "stale",
            "freshness_unknown",
            "deferred",
            "oversized",
            "invalid_request",
        }:
            continue
        place = args.get("place_id")
        try:
            if name == "list_food_claims" and isinstance(place, str):
                eligible = eligible_claim_ids(prepared, place)
                claims = old.get("claims")
                if not isinstance(claims, list):
                    continue
                selected = [
                    item
                    for item in claims
                    if isinstance(item, dict)
                    and item.get("place_id") == place
                    and item.get("claim_id") in eligible
                ]
                if len(selected) != len(claims):
                    replacement = dict(old)
                    replacement["claims"] = selected
                    replacement["invalidated_claims"] = len(claims) - len(selected)
                    observation["result"] = replacement
                    state.omissions += len(claims) - len(selected)
                    changed = True
            elif name == "read_evidence":
                current = tools.read_ephemeral_evidence(
                    TextInput.model_validate(
                        {
                            "place_id": place,
                            "item_id": args.get("item_id"),
                            "offset": args.get("offset", 0),
                        }
                    )
                )
                if current.get("status") != "available" or current != old:
                    observation["result"] = current
                    state.omissions += 1
                    changed = True
            elif name == "read_finding":
                current = tools.read_finding(
                    LookupInput.model_validate(
                        {"place_id": place, "item_id": args.get("item_id")}
                    )
                )
                if current != old:
                    observation["result"] = current
                    state.omissions += 1
                    changed = True
            elif name == "list_evidence" and isinstance(place, str):
                ids = old.get("evidence_ids")
                if isinstance(ids, list):
                    available = [
                        item
                        for item in ids
                        if isinstance(item, str)
                        and tools.read_ephemeral_evidence(
                            TextInput(place_id=place, item_id=item, offset=0)
                        ).get("status")
                        == "available"
                    ]
                    if available != ids:
                        observation["result"] = dict(old) | {
                            "evidence_ids": available,
                            "invalidated_evidence": len(ids) - len(available),
                        }
                        state.omissions += len(ids) - len(available)
                        changed = True
            elif name in {"research_place", "research_place_reviews"}:
                require_source_use(
                    ("geoapify_geography", "public_google_reviews"), "model"
                )
                specialist = (
                    prepared.research.get(place) if isinstance(place, str) else None
                )
                sample = specialist.source.sample if specialist is not None else None
                lineage = observation.get("input_review_ids")
                proposals = old.get("proposals")
                source_statuses: dict[str, str] = {}
                trusted_reviews = {}
                if (
                    isinstance(place, str)
                    and sample is not None
                    and isinstance(lineage, list)
                    and lineage
                ):
                    for review_id in lineage:
                        matches = [
                            review
                            for review in sample.reviews
                            if review.review_id == review_id
                        ]
                        if not isinstance(review_id, str) or len(matches) != 1:
                            source_statuses[str(review_id)] = "freshness_unknown"
                            continue
                        review = matches[0]
                        trusted_reviews[review_id] = review
                        source_statuses[review_id] = tools.review_binding(
                            place,
                            review_id,
                            feature_id=sample.branch.feature_id,
                            source_url=review.source_url,
                            link_basis=review.link_basis,
                            text=review.text,
                            author_name=review.author_name,
                            author_url=review.author_url,
                        )
                else:
                    source_statuses["unknown"] = "freshness_unknown"
                bad_sources = {
                    key: value
                    for key, value in source_statuses.items()
                    if value != "available"
                }
                if bad_sources:
                    observation["result"] = {
                        "status": "unavailable",
                        "reason": "source_unconfirmed",
                        "source_statuses": bad_sources,
                        **_reference_fields([args, old]),
                    }
                    observation["arguments"] = {"place_id": place}
                    state.omissions += 1
                    changed = True
                elif isinstance(proposals, list):
                    accepted = []
                    rejected = 0
                    for proposal in proposals:
                        review_id = (
                            proposal.get("review_id")
                            if isinstance(proposal, dict)
                            else None
                        )
                        bound_review = (
                            trusted_reviews.get(review_id)
                            if isinstance(review_id, str)
                            else None
                        )
                        original = (
                            proposal.get("original_review")
                            if isinstance(proposal, dict)
                            else None
                        )
                        if (
                            bound_review is None
                            or proposal.get("source_url") != bound_review.source_url
                            or proposal.get("link_basis") != bound_review.link_basis
                            or proposal.get("author_name") != bound_review.author_name
                            or proposal.get("author_url") != bound_review.author_url
                            or (original is not None and original != bound_review.text)
                        ):
                            rejected += 1
                        else:
                            accepted.append(proposal)
                    allowed = {
                        "status",
                        "place_id",
                        "source_id",
                        "source_url",
                        "evidence_id",
                        "evidence_ids",
                        "proposals",
                        "gaps",
                        "omitted_reviews",
                        "next_index",
                        "reacquisition_review_ids",
                        "deferred_review_context_ids",
                        "deferred_proposal_count",
                        "deferred_proposal_refs",
                        "omitted_fields",
                        "omitted_derived_gaps",
                        "invalidated_proposals",
                    }
                    replacement = {
                        key: item for key, item in old.items() if key in allowed
                    }
                    replacement["proposals"] = accepted
                    extra = set(old) - allowed
                    if rejected or extra:
                        replacement["invalidated_proposals"] = rejected
                        replacement["omitted_fields"] = sorted(
                            set(replacement.get("omitted_fields", [])) | extra
                        )
                        observation["result"] = replacement
                        state.omissions += rejected + len(extra)
                        changed = True
            elif name in {"search_nearby", "route_to_place"}:
                require_source_use(("geoapify_geography",), "model")
        except SourceUseDenied, ValueError, TypeError:
            observation["result"] = {
                "status": "restricted",
                **_reference_fields([args, old]),
            }
            state.omissions += 1
            changed = True
        if state.omissions > prior_omissions and first_invalidated is None:
            first_invalidated = index
    if changed and first_invalidated is not None:
        for index, observation in enumerate(state.retained):
            if index <= first_invalidated:
                continue
            if observation.get("tool") not in {
                "research_place",
                "research_place_reviews",
            }:
                continue
            args = observation.get("arguments")
            old = observation.get("result")
            if not isinstance(args, dict) or not isinstance(old, dict):
                continue
            if old.get("status") not in {"restricted", "stale", "unavailable"}:
                observation["result"] = {
                    "status": "unavailable",
                    "reason": "source_changed",
                    **_reference_fields([args, old]),
                }
                state.omissions += 1
            observation["arguments"] = {"place_id": args.get("place_id")}
    return changed


class _OwnerModel(Model):
    def __init__(
        self,
        delegate: _RecordedModel,
        calls: AttemptCalls,
        prepared: PreparedOwner,
        state: _OwnerState,
    ) -> None:
        self.delegate = delegate
        self.calls = calls
        self.prepared = prepared
        self.state = state

    def _request_size(
        self,
        instructions: str | None,
        payload: str,
        settings: ModelSettings,
        output_schema: AgentOutputSchemaBase | None,
    ) -> int:
        kwargs = self.delegate._build_response_create_kwargs(
            instructions, payload, settings, [], output_schema, []
        )
        return self.calls.research_budget.estimate(kwargs) + (settings.max_tokens or 0)

    def _context(
        self,
        instructions: str | None,
        settings: ModelSettings,
        output_schema: AgentOutputSchemaBase | None,
    ) -> str:
        if _refresh_observations(self.prepared, self.state):
            self.state.closed = True
        content: dict[str, object] = json.loads(self.prepared.input_json)
        content["observations"] = self.state.retained
        content["omitted_observations"] = self.state.omissions
        payload = _json(content)
        size = self._request_size(instructions, payload, settings, output_schema)
        if self.state.force_compaction or not self.calls.can_complete(size):
            deferred: set[str] = set()
            content["observations"] = _compact(
                self.state.retained,
                deferred,
                minimum_bytes=0 if self.state.force_compaction else 1024,
            )
            content["deferred_review_context_ids"] = sorted(deferred)
            payload = _json(content)
            size = self._request_size(instructions, payload, settings, output_schema)
            if self.state.force_compaction and (
                not deferred or size >= self.state.last_composition_size
            ):
                raise _OwnerStop("context_limit")
        if reason := self.calls.completion_unavailable_reason(size):
            raise _OwnerStop(reason)
        return payload

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        previous_response_id: str | None = None,
        conversation_id: str | None = None,
        prompt: ResponsePromptParam | None = None,
    ) -> ModelResponse:
        if _refresh_observations(self.prepared, self.state):
            self.state.closed = True
        if (
            self.state.closed
            or self.state.turns >= self.state.research_turns
            or not self.calls.can_research()
        ):
            self.state.closed = True
            settings = model_settings.resolve({"tool_choice": "none"})
            instructions = (
                (system_instructions or "")
                + " Research is closed. Compose from the retained observations and current request. State missing evidence as a gap."
            )
            payload = self._context(instructions, settings, output_schema)
            self.state.last_composition_size = self._request_size(
                instructions, payload, settings, output_schema
            )
            self.prepared.progress.failure = None
            with self.calls.completion():
                return await self.delegate.get_response(
                    instructions,
                    payload,
                    settings,
                    [],
                    output_schema,
                    [],
                    tracing,
                    previous_response_id,
                    conversation_id,
                    prompt,
                )
        self.state.turns += 1
        try:
            return await self.delegate.get_response(
                system_instructions,
                input,
                model_settings,
                tools,
                output_schema,
                handoffs,
                tracing,
                previous_response_id,
                conversation_id,
                prompt,
            )
        except _AdmissionStopped as error:
            receipt = error.receipt
            if (
                receipt.diagnostic not in {"context_limit", "usage_limit", "turn_limit"}
                and receipt.outcome != "completion_reserved"
            ):
                raise
            self.state.closed = True
            settings = model_settings.resolve({"tool_choice": "none"})
            instructions = (
                (system_instructions or "")
                + " Research is closed. Compose from the retained observations and current request. State missing evidence as a gap."
            )
            payload = self._context(instructions, settings, output_schema)
            self.state.last_composition_size = self._request_size(
                instructions, payload, settings, output_schema
            )
            self.prepared.progress.failure = None
            with self.calls.completion():
                return await self.delegate.get_response(
                    instructions,
                    payload,
                    settings,
                    [],
                    output_schema,
                    [],
                    tracing,
                    previous_response_id,
                    conversation_id,
                    prompt,
                )

    def stream_response(
        self, *args: Any, **kwargs: Any
    ) -> AsyncIterator[TResponseStreamEvent]:
        raise NotImplementedError("Owner execution is not streamed")


class _InvalidClaims(ValueError):
    pass


def _check_exposed_claims(
    prepared: PreparedOwner, state: _OwnerState, draft: OwnerDraft
) -> None:
    exposed: dict[str, set[str]] = {}
    for observation in state.retained:
        if observation.get("tool") != "list_food_claims":
            continue
        args = observation.get("arguments")
        result = observation.get("result")
        if (
            not isinstance(args, dict)
            or not isinstance(result, dict)
            or result.get("status") != "available"
        ):
            continue
        place = args.get("place_id")
        if not isinstance(place, str) or result.get("place_id") != place:
            continue
        claims = result.get("claims")
        if not isinstance(claims, list):
            continue
        for claim in claims:
            if (
                isinstance(claim, dict)
                and claim.get("place_id") == place
                and isinstance(claim.get("claim_id"), str)
            ):
                exposed.setdefault(place, set()).add(claim["claim_id"])
    checked: dict[str, set[str]] = {}
    for recommendation in (*draft.primaries, *draft.runners_up):
        if not recommendation.food_claim_ids:
            continue
        place = recommendation.place_id
        if place not in checked:
            checked[place] = eligible_claim_ids(prepared, place)
        if (
            not set(recommendation.food_claim_ids)
            <= exposed.get(place, set()) & checked[place]
        ):
            raise _InvalidClaims("unavailable_claim")


class _OwnerStop(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _tools(
    prepared: PreparedOwner,
    calls: AttemptCalls,
    state: _OwnerState,
    model: _OwnerModel,
    instructions: str,
    settings: ModelSettings,
    schema: AgentOutputSchemaBase,
) -> list[Tool]:
    result: list[Tool] = []

    def retain_gap(observation: dict[str, object]) -> bool:
        state.retained.append(observation)
        try:
            payload = model._context(instructions, settings, schema)
            fits = calls.grow_completion_reserve(
                model._request_size(instructions, payload, settings, schema)
            )
        except _OwnerStop:
            fits = False
        if not fits:
            state.retained.pop()
        return fits

    for original_tool in prepared.agent.tools:
        if not isinstance(original_tool, FunctionTool):
            result.append(original_tool)
            continue
        tool = copy.copy(original_tool)
        original = original_tool.on_invoke_tool
        name = original_tool.name

        async def invoke(
            ctx: ToolContext[None],
            args: str,
            *,
            original: Any = original,
            name: str = name,
        ) -> str:
            if _refresh_observations(prepared, state):
                state.closed = True
            if state.closed or not calls.can_research():
                state.closed = True
                return _json({"status": "unavailable", "reason": "research_closed"})
            try:
                arguments = json.loads(args)
                place = (
                    arguments.get("place_id") if isinstance(arguments, dict) else None
                )
                specialist = (
                    prepared.research.get(place)
                    if name == "research_place" and isinstance(place, str)
                    else None
                )
                before = len(specialist.contributions) if specialist is not None else 0
                try:
                    if specialist is None:
                        text = await original(ctx, args)
                    else:

                        def verify_context() -> str:
                            if _refresh_observations(prepared, state):
                                state.closed = True
                                return "stale"
                            return "available"

                        with guard_review_context(verify_context):
                            text = await original(ctx, args)
                except _AdmissionStopped as error:
                    reason = (
                        error.receipt.diagnostic
                        if error.receipt.diagnostic != "none"
                        else error.receipt.outcome
                    )
                    text = _json({"status": "unavailable", "reason": reason})
                parsed = json.loads(text)
                if isinstance(parsed, dict) and parsed.get("reason") in {
                    "tool_limit",
                    "context_limit",
                    "turn_limit",
                    "usage_limit",
                    "completion_reserved",
                    "request_limit",
                }:
                    state.closed = True
                if len(text.encode()) > 24000:
                    parsed = _fit_tool_result(parsed)
                    text = _json(parsed)
                if (
                    state.closed
                    and name == "research_place"
                    and isinstance(arguments, dict)
                ):
                    arguments = {"place_id": arguments.get("place_id")}
                observation: dict[str, object] = {
                    "tool": name,
                    "arguments": arguments,
                    "result": parsed,
                }
                if (
                    specialist is not None
                    and len(specialist.contributions) == before + 1
                ):
                    observation["input_review_ids"] = list(
                        specialist.contributions[-1].input_review_ids
                    )
                state.retained.append(observation)
                try:
                    payload = model._context(instructions, settings, schema)
                    requested = model._request_size(
                        instructions, payload, settings, schema
                    )
                    affordable = calls.grow_completion_reserve(requested)
                except _OwnerStop:
                    affordable = False
                if not affordable or len(_json(state.retained).encode()) > 100000:
                    state.retained.pop()
                    state.omissions += 1
                    state.closed = True
                    gap = {
                        "status": "deferred",
                        "reason": "completion_reserve",
                        **_reference_fields([arguments, parsed]),
                    }
                    state.deferred_references.update(_references([arguments, parsed]))
                    retain_gap({"tool": name, "arguments": arguments, "result": gap})
                    return _json(gap)
                return text
            except AgentsException, APIError, ValueError, TypeError, RuntimeError:
                state.closed = True
                observation = {
                    "tool": name,
                    "result": {"status": "unavailable", "reason": "tool_failed"},
                }
                if not retain_gap(observation):
                    state.omissions += 1
                return _json(observation["result"])

        tool.on_invoke_tool = invoke
        result.append(tool)
    return result


async def execute_owner(
    prepared: PreparedOwner,
    calls: AttemptCalls,
    *,
    search_id: str,
    version_id: str,
    completion_seconds: float = 5.0,
    completion_usage: int = 16000,
    research_turns: int = 6,
) -> OwnerOutcome:
    if prepared.calls is not calls or prepared.search_id != search_id:
        raise ValueError("owner context belongs to another attempt or search")
    if type(research_turns) is not int or research_turns < 0:
        raise ValueError("research turns must be nonnegative")
    delegate = prepared.agent.model
    if not isinstance(delegate, _RecordedModel):
        raise TypeError("owner must use the recorded SDK model")
    state = _OwnerState(research_turns)
    model = _OwnerModel(delegate, calls, prepared, state)
    settings = prepared.agent.model_settings
    schema = AgentOutputSchema(OwnerDraft)
    instructions = (
        str(prepared.agent.instructions or "")
        + " Research is closed. Compose from the retained observations and current request. State missing evidence as a gap."
    )
    try:
        closed_settings = settings.resolve({"tool_choice": "none"})
        initial_context = model._context(instructions, closed_settings, schema)
        size = model._request_size(
            instructions, initial_context, closed_settings, schema
        )
        if reason := calls.completion_unavailable_reason(size):
            return OwnerOutcome("technical_failure", "failed", reason=reason)
        if calls.remaining_seconds <= completion_seconds:
            return OwnerOutcome(
                "technical_failure", "failed", reason="completion_time_unavailable"
            )
        budget = calls.research_budget
        available = min(budget.context_limit, budget.usage_limit - budget.charged)
        calls.reserve_completion(
            seconds=completion_seconds,
            usage=max(size, min(completion_usage, available)),
        )
    except _OwnerStop as error:
        return OwnerOutcome("technical_failure", "failed", reason=error.reason)
    except ValueError, TypeError:
        return OwnerOutcome(
            "technical_failure", "failed", reason="completion_reserve_unavailable"
        )
    agent = prepared.agent.clone(
        model=model,
        tools=_tools(
            prepared,
            calls,
            state,
            model,
            instructions,
            settings.resolve({"tool_choice": "none"}),
            schema,
        ),
    )
    recovery = False
    last_reason = "invalid_output"
    for attempt in range(2):
        if attempt:
            try:
                model._context(
                    instructions, settings.resolve({"tool_choice": "none"}), schema
                )
            except _OwnerStop as error:
                last_reason = error.reason
                break
            recovery = True
            state.closed = True
            agent = agent.clone(tools=[])
        prepared.progress.failure = None
        try:
            run = await Runner.run(
                agent,
                prepared.input_json,
                max_turns=max(2, research_turns + 2),
                run_config=RunConfig(
                    tracing_disabled=True, trace_include_sensitive_data=False
                ),
            )
            draft = OwnerDraft.model_validate(run.final_output)
            if _refresh_observations(prepared, state):
                raise _InvalidClaims("source_unavailable")
            _check_exposed_claims(prepared, state, draft)
            collection = validate_owner_draft(
                draft,
                search_id=search_id,
                request=prepared.request,
                snapshot=prepared.snapshot,
                version_id=version_id,
            )
            version = collection.versions[0]
            if not version.primaries and not version.runners_up:
                raise ValueError("empty recommendation draft")
            status: Literal["completed", "partial"] = (
                "completed"
                if len(version.primaries) == 3 and len(version.runners_up) >= 2
                else "partial"
            )
            return OwnerOutcome(
                status,
                "finished",
                collection,
                research_closed=state.closed,
                recovery_attempted=recovery,
                retained=tuple(state.retained),
                deferred_references=tuple(sorted(state.deferred_references)),
            )
        except _OwnerStop as error:
            last_reason = error.reason
            break
        except _InvalidClaims:
            last_reason = "unavailable_claim"
            state.closed = True
            state.retained.append(
                {
                    "tool": "draft_validation",
                    "result": {
                        "status": "unavailable_claim",
                        "reason": "Claim was not exposed or is no longer available.",
                    },
                }
            )
            try:
                model._context(
                    instructions, settings.resolve({"tool_choice": "none"}), schema
                )
            except _OwnerStop:
                state.retained.pop()
                state.omissions += 1
        except (
            AgentsException,
            APIError,
            ValueError,
            TypeError,
            RuntimeError,
            _AdmissionStopped,
            ValidationError,
        ):
            failure = prepared.progress.failure
            if isinstance(failure, Receipt):
                last_reason = (
                    failure.diagnostic
                    if failure.diagnostic != "none"
                    else failure.outcome
                )
                if last_reason == "context_limit":
                    state.force_compaction = True
                elif last_reason not in {"response_limit", "invalid_response"}:
                    break
            else:
                last_reason = "invalid_output"
    return OwnerOutcome(
        "technical_failure",
        "failed",
        reason=last_reason,
        research_closed=state.closed,
        recovery_attempted=recovery,
        retained=tuple(state.retained),
        deferred_references=tuple(sorted(state.deferred_references)),
    )
