import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.evidence_projection import project_evidence
from app.schemas.evidence import EvidenceCollection

EXAMPLES = Path(__file__).resolve().parents[3] / "docs/examples/evidence-contract"
NOW = datetime(2026, 10, 26, tzinfo=UTC)


def collection(name):
    data = json.loads((EXAMPLES / f"{name}.json").read_text())
    for source in data["sources"]:
        source["policy"]["display"]["decision"] = "allowed"
        source["policy"]["derived_output"]["decision"] = "allowed"
    for item in data["evidence"]:
        item["freshness"]["fresh_until"] = "2026-11-01T00:00:00Z"
    return data


def project(data, place="branch_a", source_class="permitted_independent"):
    source_classes = {s["source_id"]: source_class for s in data["sources"]}
    return project_evidence(
        EvidenceCollection.model_validate_json(json.dumps(data)),
        place,
        source_classes=source_classes,
        now=NOW,
    )


def test_source_scoped_food_inference_and_unknown_gap():
    data = collection("accepted_shared_source_scoped_menu_and_price")
    data["food_claims"].append(
        {
            "claim_id": "inference",
            "status": "inferred",
            "topic": "food",
            "statement": "Consider grilled food.",
            "basis": "The source describes grilling.",
            "subjects": [{"place_id": "branch_a", "evidence_ids": ["ev_menu_a"]}],
        }
    )
    data["food_claims"].append(
        {
            "claim_id": "gap",
            "status": "unknown",
            "topic": "price",
            "subject_place_ids": ["branch_a"],
            "gap": "private internal detail",
        }
    )
    result = project(data)
    assert [f.statement for f in result.food][-1] == "Consider grilled food."
    assert all("branch B" not in f.statement for f in result.food)
    assert result.food[-1].qualification == "Inferred suggestion."
    assert result.food[-1].citations[0].evidence_id == "ev_menu_a"
    assert result.food[-1].citations[0].url == "https://example.invalid/src_group_menu"
    assert result.gaps == ("Some food information is unavailable.",)
    assert "private internal detail" not in repr(result)


def test_stale_policy_and_unsafe_url_omit_derived_text():
    data = collection("accepted_shared_source_scoped_menu_and_price")
    data["evidence"][0]["freshness"]["fresh_until"] = "2026-10-26T00:00:00Z"
    data["sources"][0]["source_url"] = "https://example.invalid/menu?token=secret"
    result = project(data)
    assert result.food == ()
    assert "secret" not in repr(result)
    assert (
        "Some source information is unavailable or needs to be checked again."
        in result.gaps
    )
    data["evidence"][0]["freshness"]["fresh_until"] = "2026-11-01T00:00:00Z"
    assert "Some source links are unavailable." in project(data).gaps
    data["sources"][0]["policy"]["display"]["decision"] = "prohibited"
    assert project(data).food == ()


def test_review_citation_and_partial_sample():
    data = collection("accepted_partial_with_usable_complaint")
    result = project(data, source_class="public_google_reviews")
    assert len(result.reviews) == 1
    citation = result.reviews[0].citations[0]
    assert (
        citation.evidence_id,
        citation.source_id,
        citation.url,
        citation.author,
        citation.link_basis,
    ) == (
        "ev_review_a",
        "src_google_a",
        "https://example.invalid/review/ev_review_a",
        "Maria Example",
        "review",
    )
    assert result.review_samples[0].assessed_count == 1
    assert result.review_samples[0].partial is True
    assert (
        result.review_samples[0].qualification
        == "Selected reviews are not a complete review history."
    )
    data["evidence"][0]["attribution_ids"] = []
    assert project(data, source_class="public_google_reviews").reviews == ()


def test_unknown_place_rejected_and_wrong_branch_not_projected():
    data = collection("accepted_shared_source_scoped_menu_and_price")
    result = project(data, place="branch_b")
    assert all("branch A" not in f.statement for f in result.food)
    with pytest.raises(ValueError, match="unknown place"):
        project(data, place="elsewhere")


def test_independent_fallback_keeps_source_identity():
    data = collection("accepted_labeled_independent_review_fallback")
    data["review_assessments"][0]["fallback"]["findings"] = [
        {
            "topic": "complaint",
            "statement": "The reviewer found the soup cold.",
            "subjects": [
                {"place_id": "branch_a", "evidence_ids": ["ev_independent_a"]}
            ],
        }
    ]
    result = project_evidence(
        EvidenceCollection.model_validate_json(json.dumps(data)),
        "branch_a",
        source_classes={
            "src_google_a": "public_google_reviews",
            "src_independent_a": "permitted_independent",
        },
        now=NOW,
    )
    assert len(result.reviews) == 1
    assert result.reviews[0].qualification == "From selected independent reviews."
    citation = result.reviews[0].citations[0]
    assert (
        citation.source_id,
        citation.evidence_id,
        citation.publisher,
        citation.url,
    ) == (
        "src_independent_a",
        "ev_independent_a",
        "Example restaurant group",
        "https://example.invalid/src_independent_a",
    )
    assert result.review_samples[0].sample_size == 1


def test_review_wording_and_polarity_survive_projection():
    data = collection("accepted_partial_with_usable_complaint")
    finding = data["review_assessments"][0]["findings"][0]
    finding["polarity"] = "negative"
    finding["wording"] = "quotation"
    finding["statement"] = data["evidence"][0]["statement"]
    result = project(data, source_class="public_google_reviews")
    assert (result.reviews[0].polarity, result.reviews[0].wording) == (
        "negative",
        "quotation",
    )
    assert result.reviews[0].citations[0].author == "Maria Example"
