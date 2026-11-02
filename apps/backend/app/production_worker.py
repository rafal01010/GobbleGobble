from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from openai import AsyncOpenAI

from app.agents.owner import CandidateAdmission, prepare_owner
from app.agents.owner_execution import OwnerOutcome, execute_owner
from app.agents.research import prepare_review_tool
from app.agents.research_assessment import promote_assessed_reviews
from app.agents.research_budget import ResearchBudget
from app.core.calls import AttemptCalls, Bounds, Completed
from app.core.settings import DatabaseSettings, Settings
from app.core.source_policy import Source
from app.db.attempt_calls import AttemptCallLedger
from app.db.checkpoints import commit_checkpoint
from app.db.completion import complete_without_result
from app.db.leases import LeaseClaim, claim_run, renew_lease
from app.db.migrations import require_current_schema
from app.db.ownership import OwnedSearch
from app.db.research_snapshots import (
    SnapshotCandidate,
    append_candidate_for_lease,
    store_snapshot,
)
from app.db.result_versions import EphemeralResult, publish_ephemeral_result
from app.db.session import session, transaction
from app.ephemeral_publications import PublicationBrokerError, exchange
from app.providers.geoapify import GeoapifyClient, MealPlaceLead
from app.providers.google_branch_search import GoogleBranchSearch
from app.providers.google_reviews import (
    BranchQuery,
    GoogleReviewClient,
    NoConfidentMatch,
    ReviewPreflightError,
    match_branch,
)
from app.publication_validation import ValidatedPublication, validate_publication
from app.publication_view import PublicResultOverlay, project_publication
from app.research_tools import ResearchTools
from app.review_evidence import append_review_evidence
from app.review_research import CandidateReview, collect_candidate_reviews
from app.schemas.evidence import EvidenceCollection, ReviewFinding
from app.schemas.lifecycle import InsufficientCoverageOutcome, TechnicalFailureOutcome
from app.schemas.recommendations import ResearchSnapshot
from app.schemas.search import (
    Coordinates,
    Place,
    ProviderReference,
    ResolvedLocation,
    SearchRequest,
)

_logger = logging.getLogger(__name__)

MAX_ATTEMPT_SECONDS = 180.0


@dataclass(frozen=True)
class ProductionResult:
    claim: LeaseClaim
    owner: OwnerOutcome
    publication: ValidatedPublication | None = None
    expires_at: datetime | None = None
    approved_findings: frozenset[ReviewFinding] = frozenset()


async def execute_production_run(
    database: DatabaseSettings,
    settings: Settings,
    run_id: str,
    *,
    worker_id: str,
    lease_seconds: int = 30,
) -> ProductionResult:
    """Run a leased research attempt through the owner, without publishing its draft."""
    with session(database) as connection:
        require_current_schema(connection)
        with transaction(connection):
            claim = claim_run(connection, run_id, worker_id, lease_seconds)
            row = connection.execute(
                "SELECT r.search_id,c.canonical_payload_json FROM runs r JOIN run_commands c ON c.run_id=r.run_id WHERE r.run_id=? AND c.command_kind='start'",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError("missing start command")
            search = OwnedSearch(row["search_id"])
            request = SearchRequest.model_validate_json(
                json.dumps(json.loads(row["canonical_payload_json"])["request"])
            )
            snapshot_id = f"snap_{uuid4().hex}"
            store_snapshot(connection, search, snapshot_id, candidates=(), findings=())
            commit_checkpoint(connection, claim, stage="discovering")
            used_requests = connection.execute(
                "SELECT count(*) FROM attempt_call_ledger WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            prior_model_calls = connection.execute(
                "SELECT count(*) FROM attempt_call_ledger WHERE run_id=? AND capability IN ('model','web_research')",
                (run_id,),
            ).fetchone()[0]
            elapsed_seconds = connection.execute(
                "SELECT max(0, (CAST(unixepoch('now','subsec') * 1000 AS INTEGER) - min(claimed_at_ms)) / 1000.0) FROM run_attempts WHERE run_id=?",
                (run_id,),
            ).fetchone()[0]
            if (
                used_requests >= 40
                or elapsed_seconds >= MAX_ATTEMPT_SECONDS
                or prior_model_calls * 131072 >= 1000000
            ):
                reason = (
                    "request_limit"
                    if used_requests >= 40
                    else "time_limit"
                    if elapsed_seconds >= MAX_ATTEMPT_SECONDS
                    else "usage_limit"
                )
                complete_without_result(
                    connection,
                    claim,
                    outcome=TechnicalFailureOutcome(
                        code="execution_limit",
                        message="The search reached its execution limit.",
                        retryable=False,
                    ),
                )
                return ProductionResult(
                    claim,
                    OwnerOutcome("technical_failure", "failed", reason=reason),
                )
            if settings.openai_api_key is None:
                complete_without_result(
                    connection,
                    claim,
                    outcome=TechnicalFailureOutcome(
                        code="model_unavailable",
                        message="The search is not configured.",
                        retryable=False,
                    ),
                )
                return ProductionResult(
                    claim,
                    OwnerOutcome("technical_failure", "failed", reason="configuration"),
                )
        ledger = AttemptCallLedger(database, claim)
        budget = ResearchBudget()
        budget.charged = prior_model_calls * budget.context_limit
        budget.turns = prior_model_calls
        if used_requests:
            budget.tools = budget.max_tools
        calls = AttemptCalls(
            settings,
            Bounds(
                max_requests=40 - used_requests,
                total_seconds=MAX_ATTEMPT_SECONDS - elapsed_seconds,
            ),
            research_budget=budget,
            on_admit=ledger.admit,
            on_receipt=ledger.finish,
        )
        geo = GeoapifyClient(calls)
        snapshot = ResearchSnapshot(
            snapshot_id=snapshot_id, evidence=EvidenceCollection(place_ids=())
        )
        lookup = ResearchTools(
            connection,
            search,
            snapshot_id,
            place_ids=(),
            max_content_bytes=12000,
            ephemeral=snapshot.evidence,
        )
        async with AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value()
            if settings.openai_api_key
            else "",
            max_retries=0,
        ) as client:

            async def admit(lead: MealPlaceLead) -> CandidateAdmission | None:
                nonlocal snapshot, lookup
                if not lead.name or not lead.formatted_address:
                    return None
                resolved = await geo.reverse_location(lead.point)
                if (
                    not isinstance(resolved, Completed)
                    or not resolved.value
                    or not resolved.value[0].timezone
                ):
                    return None
                place_id = f"place_{uuid4().hex}"
                place = Place(
                    place_id=place_id,
                    kind="branch",
                    name=lead.name,
                    location=ResolvedLocation(
                        coordinates=Coordinates(
                            latitude=lead.point.latitude, longitude=lead.point.longitude
                        ),
                        timezone=resolved.value[0].timezone,
                        name=lead.name,
                        formatted_address=lead.formatted_address,
                        provider_refs=(
                            ProviderReference(
                                provider="geoapify", provider_id=lead.place_id
                            ),
                        ),
                    ),
                )
                try:
                    lookup_result = await GoogleBranchSearch(calls).search(place)
                except ReviewPreflightError:
                    lookup_result = None
                candidates = (
                    lookup_result.value.candidates
                    if isinstance(lookup_result, Completed)
                    and lookup_result.value.status == "candidates"
                    else ()
                )
                match = match_branch(
                    BranchQuery(
                        name=lead.name,
                        full_address=lead.formatted_address,
                        coordinates=(lead.point.latitude, lead.point.longitude),
                    ),
                    candidates,
                )
                if isinstance(match, NoConfidentMatch):
                    if match.reason == "conflicting_identity":
                        return None
                    geocoded = await geo.resolve_location(
                        f"{lead.name} {lead.formatted_address}", limit=5
                    )
                    source = lead.provenance.datasource or {}
                    source_name = source.get("sourcename")

                    def normalized(value: str | None) -> str:
                        return " ".join(value.casefold().split()) if value else ""

                    corroborated = isinstance(geocoded, Completed) and any(
                        source_name
                        and (other.provenance.datasource or {}).get("sourcename")
                        and source_name
                        != (other.provenance.datasource or {}).get("sourcename")
                        and normalized(other.name) == normalized(lead.name)
                        and normalized(other.formatted_address)
                        == normalized(lead.formatted_address)
                        and abs(other.point.latitude - lead.point.latitude) < 0.0003
                        and abs(other.point.longitude - lead.point.longitude) < 0.0003
                        for other in geocoded.value
                    )
                    if not corroborated:
                        return None
                    reviews = (
                        CandidateReview(place_id, "no_match", match.reason)
                        if candidates
                        else CandidateReview(
                            place_id,
                            "missing_access"
                            if lookup_result is None
                            else "provider_failure",
                            "branch_search_unavailable",
                        )
                    )
                else:
                    reviews = await collect_candidate_reviews(
                        place, candidates, GoogleReviewClient(calls)
                    )
                raw = snapshot.evidence.model_dump(mode="json")
                raw["place_ids"].append(place_id)
                evidence = EvidenceCollection.model_validate_json(json.dumps(raw))
                evidence = append_review_evidence(
                    evidence, reviews, now=datetime.now(UTC)
                )
                updated = ResearchSnapshot(
                    snapshot_id=snapshot_id,
                    discovery_leads=snapshot.discovery_leads,
                    researched_candidates=(*snapshot.researched_candidates, place),
                    evidence=evidence,
                )
                with transaction(connection):
                    append_candidate_for_lease(
                        connection,
                        search,
                        snapshot_id,
                        SnapshotCandidate(
                            place_id,
                            "branch",
                            name=lead.name,
                            identity_source="geoapify_geography",
                            latitude=lead.point.latitude,
                            longitude=lead.point.longitude,
                            timezone=place.location.timezone,
                        ),
                        claim,
                    )
                source_classes: dict[str, Source] = {
                    source.source_id: "public_google_reviews"
                    for source in evidence.sources
                }
                next_lookup = ResearchTools(
                    connection,
                    search,
                    snapshot_id,
                    place_ids=evidence.place_ids,
                    max_content_bytes=12000,
                    ephemeral=evidence,
                    source_classes=source_classes,
                )
                specialist = (
                    prepare_review_tool(
                        calls, client, request, reviews, research_tools=next_lookup
                    )
                    if reviews.availability == "available"
                    else None
                )
                snapshot, lookup = updated, next_lookup
                return CandidateAdmission(
                    place_id,
                    updated,
                    next_lookup,
                    "geoapify_geography",
                    specialist,
                    None if specialist else reviews.availability,
                )

            owner = prepare_owner(
                calls,
                client,
                request,
                snapshot,
                lookup,
                geo,
                {},
                candidate_sources={},
                admit_candidate=admit,
                assess_review_findings=True,
            )
            work = asyncio.create_task(
                execute_owner(
                    owner,
                    calls,
                    search_id=search.search_id,
                    version_id=f"ver_{uuid4().hex}",
                )
            )

            async def heartbeat() -> None:
                nonlocal claim
                while True:
                    await asyncio.sleep(lease_seconds / 3)
                    with session(database) as other, transaction(other):
                        claim = renew_lease(other, claim, lease_seconds)
                        commit_checkpoint(
                            other, claim, stage="researching", receipts=calls.receipts
                        )

            pulse = asyncio.create_task(heartbeat())
            try:
                done, _ = await asyncio.wait(
                    (work, pulse), return_when=asyncio.FIRST_COMPLETED
                )
                if pulse in done:
                    await pulse
                try:
                    result = await work
                except Exception as error:
                    category = (
                        "timeout" if isinstance(error, TimeoutError) else "unexpected"
                    )
                    _logger.exception(
                        "owner_execution_failed: %s",
                        category,
                        exc_info=RuntimeError("owner_execution_failed"),
                    )
                    result = OwnerOutcome(
                        "technical_failure", "failed", reason="worker_failure"
                    )
            finally:
                if not work.done():
                    work.cancel()
                    with suppress(asyncio.CancelledError):
                        await work
                pulse.cancel()
                with suppress(asyncio.CancelledError):
                    await pulse
                with transaction(connection):
                    commit_checkpoint(
                        connection, claim, stage="composing", receipts=calls.receipts
                    )
            publication = None
            expires_at = None
            approved_findings: set[ReviewFinding] = set()
            if result.collection is not None:
                try:
                    collection = result.collection
                    evidence = owner.snapshot.evidence
                    for specialist, contribution, assessed in owner.scope.assessments:
                        sample = specialist.source.sample
                        if sample is None:
                            continue
                        if not all(
                            owner.research_tools.review_binding(
                                specialist.place_id,
                                proposal.review.review_id,
                                feature_id=sample.branch.feature_id,
                                source_url=proposal.review.source_url,
                                link_basis=proposal.review.link_basis,
                                text=proposal.review.text,
                                author_name=proposal.review.author_name,
                                author_url=proposal.review.author_url,
                            )
                            == "available"
                            for proposal in contribution.proposals[: assessed.selected]
                        ):
                            continue
                        before = {
                            finding
                            for assessment in evidence.review_assessments
                            for finding in assessment.findings
                        }
                        evidence = promote_assessed_reviews(
                            evidence, specialist.source, contribution, assessed
                        ).collection
                        approved_findings.update(
                            finding
                            for assessment in evidence.review_assessments
                            for finding in assessment.findings
                            if finding not in before
                        )
                    snapshots = tuple(
                        snapshot.model_copy(update={"evidence": evidence})
                        if snapshot.snapshot_id == owner.snapshot.snapshot_id
                        else snapshot
                        for snapshot in collection.snapshots
                    )
                    collection = type(collection).model_validate(
                        {**collection.model_dump(), "snapshots": snapshots}
                    )
                    version = collection.versions[0]
                    selected_ids = {
                        item.place_id
                        for item in (*version.primaries, *version.runners_up)
                    }
                    publication = validate_publication(
                        collection,
                        routes={
                            key: value
                            for key, value in owner.scope.routes.items()
                            if key in selected_ids
                        },
                        route_fresh_for=timedelta(seconds=MAX_ATTEMPT_SECONDS),
                        source_classes={
                            source.source_id: "public_google_reviews"
                            for source in owner.snapshot.evidence.sources
                            if source.origin.kind == "google"
                        },
                        now=datetime.now(UTC),
                    )
                    checked_at = datetime.now(UTC)
                    expiries = [checked_at + timedelta(seconds=120)]
                    expiries.extend(
                        route.observed_at + timedelta(seconds=MAX_ATTEMPT_SECONDS)
                        for key, route in owner.scope.routes.items()
                        if key in selected_ids
                    )
                    expiries.extend(
                        item.freshness.fresh_until
                        for item in evidence.evidence
                        if item.freshness.fresh_until is not None
                        and item.freshness.fresh_until > checked_at
                    )
                    expires_at = min(expiries)
                    result = replace(result, collection=None)
                except ValueError, TypeError:
                    result = OwnerOutcome(
                        "technical_failure", "failed", reason="invalid_publication"
                    )
            if result.status == "technical_failure":
                with transaction(connection):
                    complete_without_result(
                        connection,
                        claim,
                        outcome=TechnicalFailureOutcome(
                            code="worker_failure",
                            message="The search could not finish.",
                            retryable=True,
                        ),
                    )
            return ProductionResult(
                claim, result, publication, expires_at, frozenset(approved_findings)
            )


async def execute_and_publish_production_run(
    database: DatabaseSettings, settings: Settings, run_id: str, *, worker_id: str
) -> ProductionResult:
    result = await execute_production_run(
        database, settings, run_id, worker_id=worker_id
    )
    publication = result.publication
    if publication is None:
        return result
    collection = publication.unverified_draft
    version = collection.versions[0]
    socket_path = os.environ.get("GG_PUBLICATION_SOCKET")
    try:
        if (
            socket_path is None
            or result.expires_at is None
            or result.expires_at <= datetime.now(UTC)
        ):
            raise PublicationBrokerError("broker_unavailable")
        snapshot = next(
            item
            for item in collection.snapshots
            if item.snapshot_id == version.research_snapshot_id
        )
        source_classes: dict[str, Source] = {
            source.source_id: "public_google_reviews"
            for source in snapshot.evidence.sources
            if source.origin.kind == "google"
        }
        view = project_publication(
            publication,
            source_classes=source_classes,
            now=datetime.now(UTC),
            approved_findings=result.approved_findings,
        )
        if not (view.primaries or view.runners_up or view.travel_unconfirmed):
            with session(database) as connection, transaction(connection):
                complete_without_result(
                    connection,
                    result.claim,
                    outcome=InsufficientCoverageOutcome(
                        gaps=("We could not check enough places.",)
                    ),
                )
            return result
        overlay = PublicResultOverlay(publication=view, expires_at=result.expires_at)
        reply = exchange(
            Path(socket_path),
            {
                "op": "put",
                "key": version.version_id,
                "payload": overlay.model_dump(mode="json"),
            },
        )
        if reply.get("status") != "ok":
            raise PublicationBrokerError("broker_unavailable")
        with session(database) as connection, transaction(connection):
            publish_ephemeral_result(
                connection,
                result.claim,
                result=EphemeralResult(
                    version_id=version.version_id,
                    snapshot_id=version.research_snapshot_id,
                ),
            )
    except PublicationBrokerError, ValueError, TypeError:
        with session(database) as connection, transaction(connection):
            complete_without_result(
                connection,
                result.claim,
                outcome=TechnicalFailureOutcome(
                    code="worker_failure",
                    message="The search could not finish.",
                    retryable=True,
                ),
            )
        return replace(
            result,
            owner=OwnerOutcome(
                "technical_failure", "failed", reason="publication_unavailable"
            ),
            publication=None,
        )
    return result
