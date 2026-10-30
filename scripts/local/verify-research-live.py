from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agents.agent_output import AgentOutputSchema
from openai import AsyncOpenAI

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/backend"))

from app.agents.research import (
    collect_review_research,
    invoke_review_tool,
    prepare_review_tool,
)
from app.agents.research_assessment import SourceDecisions, assess_review_contribution
from app.core.calls import AttemptCalls, Bounds, Completed
from app.core.settings import ConfigurationError, load_settings
from app.discovery import MealDiscovery, MealDiscoveryQuery
from app.journey import JourneyEstimate, JourneyQualifier
from app.opening import assess_opening, parse_osm_opening_hours
from app.providers.geoapify import GeoapifyClient
from app.schemas.evidence import EvidenceCollection
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
    (
        "philippines",
        "Toyo Eatery, Makati, Philippines",
        "Toyo Eatery",
        "Toyo Eatery, 2316 Chino Roces Avenue Extension, Makati, 1231 Metro Manila, Philippines",
    ),
)


def select_target_location(leads, name, known_address):
    def normalized(value):
        return " ".join(value.casefold().split()) if value else ""

    postal = re.search(r"\b\d{3}-\d{4}\b", known_address or "")

    matches = [
        lead
        for lead in leads
        if (
            normalized(lead.name) == normalized(name)
            or (
                postal
                and len(normalized(lead.name)) >= 2
                and normalized(name).startswith(normalized(lead.name))
                and postal.group() in (lead.formatted_address or "")
            )
        )
        and lead.formatted_address
        and (
            not known_address
            or normalized(lead.formatted_address) == normalized(known_address)
            or (postal and postal.group() in lead.formatted_address)
        )
    ]
    return matches[0] if len(matches) == 1 else None


def discover_target(candidates, lead):
    matches = [
        item
        for item in candidates
        if item.name == lead.name
        and math.isclose(
            item.point.latitude, lead.point.latitude, rel_tol=0.0, abs_tol=1e-7
        )
        and math.isclose(
            item.point.longitude, lead.point.longitude, rel_tol=0.0, abs_tol=1e-7
        )
    ]
    return matches[0] if len(matches) == 1 else None


def source_website(target):
    if target is None or not target.provenance.datasource:
        return None
    if target.provenance.datasource.get("sourcename") != "openstreetmap":
        return None
    raw = target.provenance.datasource.get("raw")
    return (
        raw.get("website")
        if isinstance(raw, dict) and isinstance(raw.get("website"), str)
        else None
    )


def check_hours(target, request, journey, timezone):
    if target is None or not target.osm_opening_hours:
        return {"status": "source_unavailable"}
    datasource = target.provenance.datasource or {}
    raw = datasource.get("raw")
    if (
        not isinstance(raw, dict)
        or raw.get("osm_type") not in ("n", "w", "r")
        or type(raw.get("osm_id")) is not int
        or raw["osm_id"] <= 0
    ):
        return {"status": "source_link_unavailable"}
    osm_type = {"n": "node", "w": "way", "r": "relation"}[raw["osm_type"]]
    source_url = f"https://www.openstreetmap.org/{osm_type}/{raw['osm_id']}"
    local = request.eat_now_at.astimezone(ZoneInfo(timezone))
    schedule = parse_osm_opening_hours(
        target.osm_opening_hours, evidence_id="ev_hours", local_date=local.date()
    )
    if schedule is None:
        return {"status": "unsupported_schedule"}
    declaration = {
        "decision": "allowed",
        "basis": "Attributed Geoapify and OpenStreetMap geography",
    }
    policy = {
        key: declaration
        for key in ("access", "display", "model_input", "derived_output")
    } | {"tracing": {"decision": "prohibited", "basis": "Source policy"}}
    retention = {
        key: {"mode": "indefinite", "basis": "Geographic source policy"}
        for key in ("raw_content", "derived_content", "identifiers")
    }
    observed = datetime.now(UTC).isoformat()
    data = {
        "place_ids": ["target"],
        "sources": [
            {
                "source_id": "src_osm",
                "subject_place_ids": ["target"],
                "publisher": "OpenStreetMap contributors via Geoapify",
                "source_url": source_url,
                "origin": {"kind": "independent", "access_method": "Geoapify Places"},
                "policy": policy,
                "retention": retention,
            }
        ],
        "evidence": [
            {
                "evidence_id": "ev_hours",
                "source_id": "src_osm",
                "subject_place_ids": ["target"],
                "statement": target.osm_opening_hours,
                "locator": {"kind": "document"},
                "freshness": {
                    "retrieved_at": observed,
                    "basis": "Observed provider record; no asserted expiry",
                },
            }
        ],
    }
    evidence = EvidenceCollection.model_validate_json(json.dumps(data))
    assessment = assess_opening(
        request,
        place_id="target",
        destination_timezone=timezone,
        journey=journey.journey,
        evidence=evidence,
        schedules=(schedule,),
    )
    return {
        "status": "assessed",
        "source_link": source_url,
        "assessment_now": assessment.now is not None,
        "assessment_arrival": assessment.arrival is not None,
        "details": "omitted under source tracing policy",
    }


async def run_case(settings, case):
    started = time.monotonic()
    label, query, name, known_address = case
    calls = AttemptCalls(settings, Bounds(max_requests=10, total_seconds=180.0))
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
            target = discover_target(discovery.candidates, lead)
            if discovery.candidates:
                journey = await JourneyQualifier(geo).assess(
                    request,
                    target or discovery.candidates[0],
                    identity_id="discovery_probe",
                )
                result["route"] = (
                    "estimated"
                    if isinstance(journey, JourneyEstimate)
                    else journey.reason
                )
            else:
                result["route"] = "no_destination"
            if target is not None and isinstance(journey, JourneyEstimate):
                result["hours"] = check_hours(target, request, journey, lead.timezone)
            else:
                result["hours"] = {"status": "unavailable"}
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
                primary = await collect_review_research(
                    calls, place, source_website=source_website(target)
                )
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
                            calls, client, request, primary, max_output_tokens=4096
                        )
                        answer = await invoke_review_tool(
                            prepared,
                            "Describe useful food and service observations, including complaints, with their review references.",
                        )
                        if answer.contributions:
                            assessed = await assess_review_contribution(
                                calls, client, primary, answer.contributions[0]
                            )
                            result["source_assessment"] = {
                                "status": assessed.status,
                                "reason": assessed.reason,
                                "selected": assessed.selected,
                                "deferred": assessed.deferred,
                                "supported": sum(
                                    d.verdict == "supported" for d in assessed.decisions
                                ),
                                "supported_food": sum(
                                    decision.verdict == "supported"
                                    and answer.contributions[0]
                                    .proposals[int(decision.proposal_id[1:])]
                                    .finding.topic
                                    == "food"
                                    for decision in assessed.decisions
                                ),
                                "unsupported": sum(
                                    d.verdict == "unsupported"
                                    for d in assessed.decisions
                                ),
                                "unclear": sum(
                                    d.verdict == "unclear" for d in assessed.decisions
                                ),
                                "basis": "fallible model assessment against original review",
                            }
                    result["specialist_input_builder_invocations"] = (
                        prepared.progress.invocations
                    )
                    result["model_input"] = {
                        "review_bodies": prepared.progress.input_reviews,
                        "bytes": prepared.progress.input_bytes,
                        "output_schema_sha256": prepared.progress.output_schema_hash,
                    }
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
    assessment = result.get("source_assessment", {})
    if (
        result.get("location") == "resolved"
        and result.get("route") == "estimated"
        and result.get("google") == "available"
        and result.get("specialist") == "available"
        and assessment.get("status") == "assessed"
        and assessment.get("supported_food", 0) > 0
    ):
        result["status"] = "observed"
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    result["cost_usd"] = None
    result["limits"] = {
        "max_requests": 10,
        "total_seconds": 180,
        "model_output_tokens": 4096,
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
        "apps/backend/app/agents/research_assessment.py",
        "apps/backend/app/opening.py",
        "apps/backend/app/providers/geoapify.py",
        "apps/backend/app/providers/google_reviews.py",
        "apps/backend/app/agents/probe.py",
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
                "assessment_schema_sha256": hashlib.sha256(
                    json.dumps(
                        AgentOutputSchema(SourceDecisions).json_schema(),
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
            }
        ),
        flush=True,
    )
    results = []
    for case in CASES:
        result = await run_case(settings, case)
        results.append(result)
        print(json.dumps(result), flush=True)
    return 0 if all(result["status"] == "observed" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
