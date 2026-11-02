from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.review_research import CandidateReview
from app.schemas.evidence import EvidenceCollection


def append_review_evidence(
    collection: EvidenceCollection, review: CandidateReview, *, now: datetime
) -> EvidenceCollection:
    """Add an ephemeral, branch-scoped sample without persisting review content."""
    if review.place_id not in collection.place_ids or now.utcoffset() is None:
        raise ValueError("review does not belong to the snapshot")
    if any(item.place_id == review.place_id for item in collection.review_assessments):
        raise ValueError("place already has a review assessment")
    raw = collection.model_dump(mode="json")
    assessment_id = f"assessment_{uuid4().hex}"
    sample = review.sample
    outcome: dict[str, object]
    if sample is None:
        if review.availability == "restricted":
            outcome = {
                "status": "restricted",
                "requested_use": "model_input",
                "reason": review.reason,
            }
        elif review.availability == "no_match":
            outcome = {"status": "no_match", "reason": review.reason}
        else:
            outcome = {"status": "provider_unavailable", "reason": review.reason}
        raw["review_assessments"].append(
            {
                "assessment_id": assessment_id,
                "place_id": review.place_id,
                "google": outcome,
            }
        )
        return EvidenceCollection.model_validate_json(json.dumps(raw))
    branch = {"feature_id": sample.branch.feature_id}
    source_id = f"src_{uuid4().hex}"
    declaration = lambda decision: {
        "decision": decision,
        "basis": "Current public review source policy",
    }
    ephemeral = {
        "mode": "ephemeral",
        "basis": "Review content is reacquired when needed",
    }
    raw["sources"].append(
        {
            "source_id": source_id,
            "subject_place_ids": [review.place_id],
            "publisher": "Google Maps",
            "source_url": sample.branch.source_url,
            "origin": {
                "kind": "google",
                "branch": branch,
                "access_method": "anonymous Google review route",
            },
            "policy": {
                "access": declaration("allowed"),
                "display": declaration("allowed"),
                "model_input": declaration("allowed"),
                "derived_output": declaration("allowed"),
                "tracing": declaration("prohibited"),
            },
            "retention": {
                "raw_content": ephemeral,
                "derived_content": ephemeral,
                "identifiers": ephemeral,
            },
        }
    )
    evidence_ids = []
    for item in sample.reviews:
        if not item.text or not item.text.strip():
            continue
        evidence_id = f"ev_{uuid4().hex}"
        attribution_ids = []
        if item.author_name or item.author_url:
            attribution_id = f"author_{uuid4().hex}"
            raw["attributions"].append(
                {
                    "attribution_id": attribution_id,
                    "kind": "author",
                    "name": item.author_name,
                    "url": item.author_url,
                }
            )
            attribution_ids.append(attribution_id)
        raw["evidence"].append(
            {
                "evidence_id": evidence_id,
                "source_id": source_id,
                "subject_place_ids": [review.place_id],
                "statement": item.text,
                "locator": {
                    "kind": "google_review",
                    "branch": branch,
                    "review_id": item.review_id,
                    "source_url": item.source_url,
                    "link_basis": item.link_basis,
                },
                "attribution_ids": attribution_ids,
                "freshness": {
                    "retrieved_at": now.astimezone(UTC).isoformat(),
                    "fresh_until": (
                        now.astimezone(UTC) + timedelta(hours=1)
                    ).isoformat(),
                    "basis": "Bounded attempt sample",
                },
            }
        )
        evidence_ids.append(evidence_id)
    if review.availability == "zero_reviews":
        outcome = {
            "status": "zero_review",
            "source_id": source_id,
            "confirmation_basis": review.reason,
        }
    else:
        omitted = len(sample.reviews) - len(evidence_ids)
        state = (
            "present"
            if evidence_ids
            else "omitted"
            if review.availability == "omitted_sample"
            else "empty"
        )
        outcome = {
            "status": "available",
            "source_id": source_id,
            "sample": {
                "state": state,
                "evidence_ids": evidence_ids,
                "sample_size": len(evidence_ids),
                "assessed_evidence_ids": [],
                "assessed_count": 0,
                "reported_total": sample.branch.reported_total or None,
                "reported_total_basis": "Google branch search"
                if sample.branch.reported_total
                else "Not reported",
                "selection_method": "Provider-selected sample",
                "requested_order": sample.sort,
                "observed_order": "Provider order",
                "bounds": {
                    "max_reviews": sample.limits.max_reviews,
                    "max_pages": sample.limits.max_pages,
                },
                "partial": True,
                "qualification": "The sample is limited and has not been fully assessed.",
                "omissions": [f"{omitted} review bodies were empty or omitted."]
                if omitted
                else [],
            },
        }
    raw["review_assessments"].append(
        {"assessment_id": assessment_id, "place_id": review.place_id, "google": outcome}
    )
    return EvidenceCollection.model_validate_json(json.dumps(raw))
