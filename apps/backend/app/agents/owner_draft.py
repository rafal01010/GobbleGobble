from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.schemas.recommendations import (
    InitialLineage,
    Recommendation,
    RecommendationCollection,
    RecommendationVersion,
    ResearchSnapshot,
)
from app.schemas.search import SearchRequest


class RecommendationDraft(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)

    place_id: str
    why_to_go: str
    food_claim_ids: list[str]


class OwnerDraft(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)

    primaries: list[RecommendationDraft]
    runners_up: list[RecommendationDraft]
    shortfall_reason: str | None


def validate_owner_draft(
    draft: OwnerDraft,
    *,
    search_id: str,
    request: SearchRequest,
    snapshot: ResearchSnapshot,
    version_id: str,
) -> RecommendationCollection:
    """Bind an owner draft to server-supplied search and evidence context."""
    draft = OwnerDraft.model_validate(draft)
    request = SearchRequest.model_validate(request)
    snapshot = ResearchSnapshot.model_validate(snapshot)

    def recommendations(items: list[RecommendationDraft]) -> tuple[Recommendation, ...]:
        return tuple(
            Recommendation(
                place_id=item.place_id,
                why_to_go=item.why_to_go,
                food_claim_ids=tuple(item.food_claim_ids),
            )
            for item in items
        )

    version = RecommendationVersion(
        version_id=version_id,
        research_snapshot_id=snapshot.snapshot_id,
        lineage=InitialLineage(),
        primaries=recommendations(draft.primaries),
        runners_up=recommendations(draft.runners_up),
        shortfall_reason=draft.shortfall_reason,
    )
    return RecommendationCollection(
        search_id=search_id,
        request=request,
        snapshots=(snapshot,),
        versions=(version,),
    )
