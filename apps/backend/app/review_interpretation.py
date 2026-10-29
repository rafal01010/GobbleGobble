from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.evidence import (
    AvailableReviews,
    EvidenceCollection,
    EvidenceId,
    ReviewFinding,
    SubjectSupport,
)
from app.schemas.search import NonBlankText

type Polarity = Literal["positive", "negative", "neutral", "mixed", "unclear"]
type ReviewTopic = Literal["complaint", "food", "price", "service", "reception"]


class InterpretedFinding(BaseModel):
    """A caller's assessment of one review, not an automated entailment check."""

    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", hide_input_in_errors=True
    )

    evidence_id: EvidenceId
    topic: ReviewTopic
    polarity: Polarity
    kind: Literal["paraphrase", "quotation"]
    statement: NonBlankText = Field(repr=False)


@dataclass(frozen=True)
class InterpretedReviews:
    collection: EvidenceCollection = field(repr=False)
    assessment_id: str
    findings: tuple[InterpretedFinding, ...] = field(repr=False)


def interpret_reviews(
    collection: EvidenceCollection,
    assessment_id: str,
    findings: tuple[InterpretedFinding, ...],
) -> InterpretedReviews:
    """Attach caller-reviewed findings to one assessed Google review sample.

    A paraphrase's meaning remains the caller's responsibility. Quotation checks
    are exact and attribution is required. No source-use permission is granted.
    """
    assessments = {item.assessment_id: item for item in collection.review_assessments}
    assessment = assessments.get(assessment_id)
    if assessment is None:
        raise ValueError("unknown review assessment")
    if assessment.findings:
        raise ValueError("assessment already contains findings")
    if findings and not isinstance(assessment.google, AvailableReviews):
        raise ValueError("findings require an available review sample")
    evidence = {item.evidence_id: item for item in collection.evidence}
    authors = {
        item.attribution_id
        for item in collection.attributions
        if item.kind == "author" and (item.name or item.url)
    }
    mapped: list[ReviewFinding] = []
    for finding in findings:
        item = evidence.get(finding.evidence_id)
        if item is None:
            raise ValueError("unknown review evidence")
        if finding.kind == "quotation":
            if finding.statement not in item.statement:
                raise ValueError("quotation does not match the review")
            if not authors.intersection(item.attribution_ids):
                raise ValueError("quotation requires review author attribution")
        mapped.append(
            ReviewFinding(
                topic=finding.topic,
                statement=finding.statement,
                polarity=finding.polarity,
                wording=finding.kind,
                subjects=(
                    SubjectSupport(
                        place_id=assessment.place_id,
                        evidence_ids=(finding.evidence_id,),
                    ),
                ),
            )
        )
    data = collection.model_dump()
    data["review_assessments"] = tuple(
        item.model_copy(update={"findings": tuple(mapped)})
        if item.assessment_id == assessment_id
        else item
        for item in collection.review_assessments
    )
    return InterpretedReviews(
        EvidenceCollection.model_validate(data), assessment_id, findings
    )
