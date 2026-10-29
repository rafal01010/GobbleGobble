from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.evidence import (
    EvidenceCollection,
    EvidenceId,
    FoodTopic,
    InferredFoodClaim,
    SourcedFoodClaim,
    SubjectSupport,
    UnknownFoodClaim,
)
from app.schemas.search import NonBlankText


class _Proposal(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", hide_input_in_errors=True
    )


class SourcedFood(_Proposal):
    claim_id: NonBlankText
    place_id: NonBlankText
    evidence_id: EvidenceId
    topic: FoodTopic


class InferredFood(_Proposal):
    claim_id: NonBlankText
    place_id: NonBlankText
    evidence_id: EvidenceId


class FoodGap(_Proposal):
    claim_id: NonBlankText
    place_id: NonBlankText
    topic: FoodTopic
    gap: NonBlankText = Field(repr=False)


def interpret_food(
    collection: EvidenceCollection,
    *,
    sourced: tuple[SourcedFood, ...] = (),
    inferred: tuple[InferredFood, ...] = (),
    gaps: tuple[FoodGap, ...] = (),
) -> EvidenceCollection:
    """Add caller-classified food statements and qualified suggestions.

    The caller is responsible for the meaning and classification of evidence.
    This function checks subject associations, not truth or source permission.
    """
    evidence = {item.evidence_id: item for item in collection.evidence}
    claims = list(collection.food_claims)
    for proposal in sourced:
        item = evidence.get(proposal.evidence_id)
        if item is None:
            raise ValueError("unknown supporting evidence")
        claims.append(
            SourcedFoodClaim(
                claim_id=proposal.claim_id,
                topic=proposal.topic,
                statement=item.statement,
                subjects=(
                    SubjectSupport(
                        place_id=proposal.place_id,
                        evidence_ids=(proposal.evidence_id,),
                    ),
                ),
            )
        )
    for inference in inferred:
        item = evidence.get(inference.evidence_id)
        if item is None:
            raise ValueError("unknown supporting evidence")
        claims.append(
            InferredFoodClaim(
                claim_id=inference.claim_id,
                topic="food",
                statement=(
                    f"Inferred suggestion: Consider food described by this source: {item.statement} "
                    "Current availability and price are unconfirmed."
                ),
                basis="The suggestion is based on the cited source statement.",
                subjects=(
                    SubjectSupport(
                        place_id=inference.place_id,
                        evidence_ids=(inference.evidence_id,),
                    ),
                ),
            )
        )
    for gap in gaps:
        claims.append(
            UnknownFoodClaim(
                claim_id=gap.claim_id,
                topic=gap.topic,
                subject_place_ids=(gap.place_id,),
                gap=gap.gap,
            )
        )
    data = collection.model_dump()
    data["food_claims"] = tuple(claims)
    return EvidenceCollection.model_validate(data)
