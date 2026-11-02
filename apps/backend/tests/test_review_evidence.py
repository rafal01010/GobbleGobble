from dataclasses import replace
from datetime import UTC, datetime

from test_research_agent import setup_values

from app.review_evidence import append_review_evidence
from app.schemas.evidence import EvidenceCollection


def test_review_sample_keeps_branch_and_attribution():
    _, _, review = setup_values()
    sample = review.sample
    item = replace(sample.reviews[0], author_name="Reviewer", text="The soup was cold.")
    review = replace(review, sample=replace(sample, reviews=(item,)))
    result = append_review_evidence(
        EvidenceCollection(place_ids=(review.place_id,)),
        review,
        now=datetime(2026, 10, 31, tzinfo=UTC),
    )
    evidence = result.evidence[0]
    assert evidence.statement == "The soup was cold."
    assert evidence.locator.branch.feature_id == sample.branch.feature_id
    assert evidence.locator.review_id == item.review_id
    assert result.attributions[0].name == "Reviewer"
    assert result.sources[0].retention.raw_content.mode == "ephemeral"
    assert result.review_assessments[0].google.sample.assessed_count == 0


def test_unmatched_branch_is_an_explicit_gap():
    _, _, review = setup_values()
    review = replace(review, sample=None, availability="no_match", reason="ambiguous")
    result = append_review_evidence(
        EvidenceCollection(place_ids=(review.place_id,)),
        review,
        now=datetime(2026, 10, 31, tzinfo=UTC),
    )
    assert result.review_assessments[0].google.status == "no_match"
    assert result.review_assessments[0].google.reason == "ambiguous"
    assert result.sources == ()
