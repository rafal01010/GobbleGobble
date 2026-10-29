from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from openai import AsyncOpenAI

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/backend"))

from app.agents.research import invoke_review_tool, prepare_review_tool
from app.core.calls import AttemptCalls, Bounds, Completed
from app.core.settings import ConfigurationError, load_settings
from app.discovery import MealDiscovery, MealDiscoveryQuery
from app.journey import JourneyEstimate, JourneyQualifier
from app.providers.geoapify import GeoapifyClient
from app.review_research import PrimaryReviewClient
from app.schemas.search import (
    Coordinates,
    Place,
    ResolvedLocation,
    SearchOrigin,
    SearchRequest,
    WalkingConstraint,
)

CASES = (
    (
        "japan",
        "一平 本店, Kitakyushu, Japan",
        "一平 本店",
        "2 Chome-6-2 Kyomachi, Kokurakita Ward, Kitakyushu, Fukuoka 802-0002, Japan",
    ),
    ("philippines", "Toyo Eatery, Makati, Philippines", "Toyo Eatery", None),
)


def select_target_location(leads, name, known_address):
    def normalized(value):
        return " ".join(value.casefold().split()) if value else ""

    matches = [
        lead
        for lead in leads
        if normalized(lead.name) == normalized(name)
        and lead.formatted_address
        and (
            not known_address
            or normalized(lead.formatted_address) == normalized(known_address)
        )
    ]
    return matches[0] if len(matches) == 1 else None


async def run_case(settings, case):
    started = time.monotonic()
    label, query, name, known_address = case
    calls = AttemptCalls(settings, Bounds(max_requests=8, total_seconds=120.0))
    geo = GeoapifyClient(calls)
    result = {"case": label, "status": "incomplete"}
    location = await geo.resolve_location(query, limit=5)
    if not isinstance(location, Completed) or not location.value:
        result["location"] = "unavailable"
    elif (lead := select_target_location(location.value, name, known_address)) is None:
        result["location"] = "no_confident_target"
    else:
        result["location"] = "resolved" if lead.timezone else "timezone_unavailable"
        if lead.timezone:
            origin = SearchOrigin(
                kind="chosen",
                location=ResolvedLocation(
                    coordinates=Coordinates(
                        latitude=lead.point.latitude, longitude=lead.point.longitude
                    ),
                    timezone=lead.timezone,
                    name=lead.name,
                    formatted_address=lead.formatted_address,
                ),
            )
            request = SearchRequest(
                origin=origin,
                travel=WalkingConstraint(minutes=15),
                eat_now_at=datetime.now(UTC),
            )
            discovery = await MealDiscovery(geo).discover(
                MealDiscoveryQuery(center=lead, radius_m=500, page_size=10, max_pages=1)
            )
            result["discovery"] = {
                "count": len(discovery.candidates),
                "stop": discovery.stop_reason,
            }
            if discovery.candidates:
                journey = await JourneyQualifier(geo).assess(
                    request, discovery.candidates[0], identity_id="discovery_probe"
                )
                result["route"] = (
                    "estimated"
                    if isinstance(journey, JourneyEstimate)
                    else journey.reason
                )
            else:
                result["route"] = "no_destination"
            address = known_address or lead.formatted_address
            if address:
                place = Place(
                    place_id=f"gate_{label}",
                    kind="branch",
                    name=name,
                    location=ResolvedLocation(
                        coordinates=origin.location.coordinates,
                        timezone=lead.timezone,
                        name=name,
                        formatted_address=address,
                    ),
                )
                primary = await PrimaryReviewClient(calls).collect(place)
                result["google"] = primary.availability
                result["google_reason"] = primary.reason
                if primary.sample is not None:
                    result["google_feature_id"] = primary.sample.branch.feature_id
                    result["google_branch_source_url"] = (
                        primary.sample.branch.source_url
                    )
                    result["sample"] = {
                        "count": len(primary.sample.reviews),
                        "termination": primary.sample.termination,
                        "sort": primary.sample.sort,
                        "max_reviews": primary.sample.limits.max_reviews,
                        "max_pages": primary.sample.limits.max_pages,
                        "complete_history": False,
                        "attributed_count": sum(
                            bool(r.author_name or r.author_url)
                            for r in primary.sample.reviews
                        ),
                    }
                if primary.availability == "available":
                    async with AsyncOpenAI(
                        api_key=settings.openai_api_key.get_secret_value(),
                        max_retries=0,
                    ) as client:
                        prepared = prepare_review_tool(
                            calls, client, request, primary, max_output_tokens=1024
                        )
                        answer = await invoke_review_tool(
                            prepared,
                            "Describe useful food and service observations, including complaints, with their review references.",
                        )
                    result["specialist_input_builder_invocations"] = (
                        prepared.progress.invocations
                    )
                    result["specialist"] = answer.status
                    result["specialist_reason"] = answer.reason
                    result["proposals"] = sum(
                        len(c.proposals) for c in answer.contributions
                    )
                    result["unverified_proposals"] = result["proposals"]
                    result["matched_quotes"] = sum(
                        p.quote_matches_source
                        for c in answer.contributions
                        for p in c.proposals
                    )
                    result["source_support"] = "requires_source_review"
            else:
                result["google"] = "address_unavailable"
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    result["cost_usd"] = None
    result["limits"] = {
        "max_requests": 8,
        "total_seconds": 120,
        "model_output_tokens": 1024,
    }
    result["receipts"] = [receipt.safe_record() for receipt in calls.receipts]
    result["accounting"] = [
        {
            "estimated_input": r.estimated_input,
            "reserved_output": r.reserved_output,
            "actual_input": r.actual_input,
            "actual_output": r.actual_output,
            "outcome": r.outcome,
            "deferred_tool_results": r.deferred_tool_results,
        }
        for r in calls.research_budget.records
    ]
    return result


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    try:
        settings = load_settings(mode="live" if args.live else "diagnostic")
    except ConfigurationError:
        print(json.dumps({"status": "configuration_unavailable"}))
        return 2
    if (
        settings.research_model != "gpt-6-luna"
        or settings.review_provider != "google_reviews"
    ):
        print(json.dumps({"status": "configuration_mismatch"}))
        return 2
    if not args.live:
        print(json.dumps({"status": "diagnostic_ready", "case_count": len(CASES)}))
        return 0
    paths = (
        "scripts/local/verify-research-live.py",
        "apps/backend/app/agents/research.py",
        "apps/backend/app/agents/research_budget.py",
        "apps/backend/app/review_research.py",
        "apps/backend/app/providers/google_branch_search.py",
    )
    identities = {
        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths
    }
    print(
        json.dumps(
            {
                "status": "started",
                "research_model": settings.research_model,
                "revision_files": identities,
                "case_count": len(CASES),
            }
        ),
        flush=True,
    )
    for case in CASES:
        result = await run_case(settings, case)
        print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
