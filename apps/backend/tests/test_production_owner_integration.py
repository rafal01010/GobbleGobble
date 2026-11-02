import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx2
import pytest
from openai import AsyncOpenAI
from test_owner_execution import synthetic_response, tool_call
from test_research_agent import model_response, setup_values

from app.core.calls import Completed
from app.providers.geoapify import (
    LocationLead,
    MealPlaceLead,
    Point,
    Provenance,
    RouteAvailable,
)
from app.providers.google_reviews import BranchCandidate
from app.review_research import CandidateReview

pytest_plugins = ["test_foundation"]


@pytest.mark.asyncio
@pytest.mark.parametrize("google_available", [True, False])
async def test_admitted_run_discovers_and_composes_with_owned_candidate(
    database, admitted, monkeypatch, google_available, receipt
):
    from app import production_worker

    settings, _, _ = setup_values()
    lead = MealPlaceLead(
        "lead-a",
        Point(0, 0),
        "Cafe",
        "1 Main Street",
        (),
        Provenance('{"sourcename":"source-a"}'),
    )

    async def nearby(self, *args, **kwargs):
        return Completed((lead,), None)

    async def reverse(self, *args, **kwargs):
        return Completed((SimpleNamespace(timezone="Asia/Manila"),), None)

    async def branch_search(self, place):
        return Completed(
            SimpleNamespace(
                status="candidates" if google_available else "access_failure",
                candidates=(
                    BranchCandidate(
                        "Cafe", feature_id="0x1:0x2", full_address="1 Main Street"
                    ),
                )
                if google_available
                else (),
            ),
            None,
        )

    async def resolve(self, *args, **kwargs):
        return Completed(
            (
                LocationLead(
                    "lead-b",
                    Point(0, 0),
                    "Cafe",
                    "1 Main Street",
                    Provenance('{"sourcename":"source-b"}'),
                ),
            ),
            None,
        )

    async def reviews(place, candidates, client):
        if google_available:
            return replace(setup_values()[2], place_id=place.place_id)
        return CandidateReview(place.place_id, "no_match", "no_reviews")

    async def route(self, origin, destination, *, mode):
        return Completed(
            RouteAvailable(origin, destination, mode, 300.0, 250.0, Provenance("{}")),
            receipt.model_copy(
                update={"capability": "route", "started_at": datetime.now(UTC)}
            ),
        )

    monkeypatch.setattr(production_worker.GeoapifyClient, "nearby_places", nearby)
    monkeypatch.setattr(production_worker.GeoapifyClient, "reverse_location", reverse)
    monkeypatch.setattr(production_worker.GeoapifyClient, "resolve_location", resolve)
    monkeypatch.setattr(production_worker.GeoapifyClient, "route", route)
    monkeypatch.setattr(production_worker.GoogleBranchSearch, "search", branch_search)
    monkeypatch.setattr(production_worker, "collect_candidate_reviews", reviews)
    original_append = production_worker.append_review_evidence

    def append_with_claim(collection, review, *, now):
        result = original_append(collection, review, now=now)
        if review.availability != "available":
            return result
        raw = result.model_dump(mode="json")
        raw["food_claims"].append(
            {
                "claim_id": "test_food",
                "status": "sourced",
                "topic": "food",
                "statement": "The chicken was excellent.",
                "subjects": [
                    {
                        "place_id": review.place_id,
                        "evidence_ids": [raw["evidence"][0]["evidence_id"]],
                    }
                ],
            }
        )
        return type(result).model_validate_json(json.dumps(raw))

    monkeypatch.setattr(production_worker, "append_review_evidence", append_with_claim)
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx2.Response(
                200, json=tool_call("search_nearby", {"radius_m": 500, "limit": 1})
            )
        matches = re.findall(r"place_[0-9a-f]{32}", json.dumps(body))
        if google_available and len(requests) == 2:
            return httpx2.Response(
                200,
                json=tool_call(
                    "research_place",
                    {"place_id": matches[-1], "question": "What do diners say?"},
                    call_id="call_research",
                ),
            )
        if google_available and len(requests) == 3:
            return httpx2.Response(
                200,
                json=model_response(
                    {
                        "findings": [
                            {
                                "review_label": "r0",
                                "topic": "service",
                                "polarity": "negative",
                                "wording": "paraphrase",
                                "statement": "Service was slow.",
                                "basis": None,
                            }
                        ],
                        "gaps": [],
                    }
                ),
            )
        if google_available and len(requests) == 4:
            return httpx2.Response(
                200,
                json=model_response(
                    {
                        "decisions": [
                            {
                                "proposal_id": "p0",
                                "review_label": "r0",
                                "verdict": "supported",
                                "rationale": "The review says so.",
                            }
                        ]
                    }
                ),
            )
        if google_available and len(requests) == 5:
            return httpx2.Response(
                200,
                json=tool_call(
                    "list_food_claims",
                    {"place_id": matches[-1], "after": None, "limit": 10},
                    call_id="call_2",
                ),
            )
        if google_available and len(requests) == 6:
            return httpx2.Response(
                200,
                json=tool_call(
                    "route_to_place", {"place_id": matches[-1]}, call_id="call_3"
                ),
            )
        assert matches
        return httpx2.Response(
            200,
            json=synthetic_response(
                {
                    "primaries": [
                        {
                            "place_id": matches[-1],
                            "why_to_go": "A nearby cafe.",
                            "food_claim_ids": ["test_food"] if google_available else [],
                        }
                    ],
                    "runners_up": [],
                    "shortfall_reason": "One candidate.",
                }
            ),
        )

    def client_factory(**kwargs):
        return AsyncOpenAI(
            **kwargs,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        )

    monkeypatch.setattr(production_worker, "AsyncOpenAI", client_factory)
    result = await production_worker.execute_production_run(
        database, settings, admitted.run_id, worker_id="test"
    )
    assert result.owner.status == "partial", result.owner.reason
    assert result.owner.collection is None
    assert result.publication is not None
    checked = (
        result.publication.qualified_primaries[0]
        if google_available
        else result.publication.unconfirmed_primaries[0]
    )
    assert checked.place_id.startswith("place_")
    assert checked.rationale_status == "unverified"
    snapshot = result.publication.unverified_draft.snapshots[0]
    if google_available:
        assert snapshot.evidence.sources[0].subject_place_ids == (
            snapshot.researched_candidates[0].place_id,
        )
        assert (
            snapshot.evidence.evidence[0].source_id
            == snapshot.evidence.sources[0].source_id
        )
        assert (
            snapshot.evidence.evidence[0].subject_place_ids
            == snapshot.evidence.sources[0].subject_place_ids
        )
        claim = snapshot.evidence.food_claims[0]
        assert claim.subjects[0].evidence_ids == (
            snapshot.evidence.evidence[0].evidence_id,
        )
        assert checked.claims[0].reference_id == "test_food"
        assert (
            snapshot.evidence.review_assessments[0].findings[0].statement
            == "Service was slow."
        )
    assert len(requests) == (7 if google_available else 2)
    from app.db.session import session

    with session(database) as connection:
        records = connection.execute(
            "SELECT safe_record_json FROM attempt_call_ledger WHERE run_id=?",
            (admitted.run_id,),
        ).fetchall()
    assert len(records) == (7 if google_available else 2)
    assert all(row[0] is not None for row in records)


@pytest.mark.asyncio
async def test_missing_model_key_completes_as_configuration_failure(database, admitted):
    from app.db.runs import read_snapshot
    from app.db.session import session
    from app.production_worker import execute_production_run

    settings, _, _ = setup_values()
    settings = settings.model_copy(update={"openai_api_key": None})
    result = await execute_production_run(
        database, settings, admitted.run_id, worker_id="test"
    )
    assert result.owner.status == "technical_failure"
    with session(database) as connection:
        run = read_snapshot(
            connection, search_id=admitted.search_id, run_id=admitted.run_id
        )
    assert run.state.status == "completed"
    assert run.state.outcome.code == "model_unavailable"


@pytest.mark.asyncio
async def test_expired_overall_deadline_does_not_restart(database, admitted):
    from app.db.leases import claim_run
    from app.db.session import session, transaction
    from app.production_worker import execute_production_run

    settings, _, _ = setup_values()
    with session(database) as connection, transaction(connection):
        first = claim_run(connection, admitted.run_id, "first", 30)
        connection.execute(
            "UPDATE run_attempts SET claimed_at_ms=1 WHERE attempt_id=?",
            (first.attempt_id,),
        )
        connection.execute(
            "UPDATE run_leases SET expires_at_ms=0 WHERE run_id=?", (admitted.run_id,)
        )
    result = await execute_production_run(
        database, settings, admitted.run_id, worker_id="second"
    )
    assert result.owner.status == "technical_failure"
    assert result.owner.reason == "time_limit"
