from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.schemas.evidence import EvidenceCollection, FoodClaim, UnknownFoodClaim
from app.schemas.search import NonBlankText, Place, SearchRequest

type SnapshotId = Annotated[str, StringConstraints(pattern=r"^snap_[A-Za-z0-9_-]+$")]
type VersionId = Annotated[str, StringConstraints(pattern=r"^ver_[A-Za-z0-9_-]+$")]
type FoodClaimIds = Annotated[tuple[NonBlankText, ...], Field(min_length=1)]


class _Model(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


def _unique(values: tuple[str, ...], message: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(message)


class ResearchSnapshot(_Model):
    snapshot_id: SnapshotId = Field(repr=False)
    discovery_leads: tuple[Place, ...] = Field(default=(), repr=False)
    researched_candidates: tuple[Place, ...] = Field(default=(), repr=False)
    evidence: EvidenceCollection = Field(repr=False)

    @model_validator(mode="after")
    def place_catalog(self) -> ResearchSnapshot:
        place_ids = tuple(
            place.place_id
            for place in (*self.discovery_leads, *self.researched_candidates)
        )
        _unique(place_ids, "duplicate snapshot place IDs")
        if set(place_ids) != set(self.evidence.place_ids):
            raise ValueError("evidence place catalog differs from snapshot places")
        return self


class Recommendation(_Model):
    place_id: NonBlankText = Field(repr=False)
    why_to_go: NonBlankText = Field(repr=False)
    food_claim_ids: tuple[NonBlankText, ...] = Field(default=(), repr=False)


class FoodChoiceSupport(_Model):
    place_id: NonBlankText = Field(repr=False)
    food_claim_ids: FoodClaimIds = Field(repr=False)


class FoodChoice(_Model):
    choice_id: NonBlankText = Field(repr=False)
    version_id: VersionId = Field(repr=False)
    label: NonBlankText = Field(repr=False)
    supports: Annotated[tuple[FoodChoiceSupport, ...], Field(min_length=1)] = Field(
        repr=False
    )


class OfferedFoodChoiceRef(_Model):
    version_id: VersionId = Field(repr=False)
    choice_id: NonBlankText = Field(repr=False)


class InitialLineage(_Model):
    kind: Literal["initial"] = "initial"


class FoodRefinementLineage(_Model):
    kind: Literal["food_choice"] = "food_choice"
    parent_version_id: VersionId = Field(repr=False)
    choice: OfferedFoodChoiceRef = Field(repr=False)


class AllOptionsLineage(_Model):
    kind: Literal["all_options"] = "all_options"
    parent_version_id: VersionId = Field(repr=False)


type ResultLineage = Annotated[
    InitialLineage | FoodRefinementLineage | AllOptionsLineage,
    Field(discriminator="kind"),
]


class RecommendationVersion(_Model):
    version_id: VersionId = Field(repr=False)
    research_snapshot_id: SnapshotId = Field(repr=False)
    lineage: ResultLineage = Field(repr=False)
    primaries: Annotated[tuple[Recommendation, ...], Field(max_length=3)] = Field(
        repr=False
    )
    runners_up: Annotated[tuple[Recommendation, ...], Field(max_length=4)] = Field(
        repr=False
    )
    shortfall_reason: NonBlankText | None = Field(default=None, repr=False)
    food_choices: tuple[FoodChoice, ...] = Field(default=(), repr=False)

    @model_validator(mode="after")
    def explained_shortfall(self) -> RecommendationVersion:
        if (
            len(self.primaries) < 3 or len(self.runners_up) < 2
        ) and self.shortfall_reason is None:
            raise ValueError("smaller recommendation sets require a shortfall reason")
        return self


class RecommendationCollection(_Model):
    """Validate supplied search relationships, without admitting or publishing work."""

    search_id: NonBlankText = Field(repr=False)
    request: SearchRequest = Field(repr=False)
    snapshots: Annotated[tuple[ResearchSnapshot, ...], Field(min_length=1)] = Field(
        repr=False
    )
    versions: Annotated[tuple[RecommendationVersion, ...], Field(min_length=1)] = Field(
        repr=False
    )

    @model_validator(mode="after")
    def relationships(self) -> RecommendationCollection:
        _unique(
            tuple(snapshot.snapshot_id for snapshot in self.snapshots),
            "duplicate snapshot IDs",
        )
        _unique(
            tuple(version.version_id for version in self.versions),
            "duplicate version IDs",
        )
        snapshots = {snapshot.snapshot_id: snapshot for snapshot in self.snapshots}
        versions = {version.version_id: version for version in self.versions}
        parents: dict[str, str] = {}

        def claim_support(
            place_id: str,
            claim_ids: tuple[str, ...],
            candidates: set[str],
            claims: dict[str, FoodClaim],
            *,
            food_choice: bool,
        ) -> None:
            if place_id not in candidates:
                raise ValueError("place is not a researched candidate")
            _unique(claim_ids, "duplicate food claim references")
            for claim_id in claim_ids:
                claim = claims.get(claim_id)
                if claim is None:
                    raise ValueError("unknown food claim reference")
                if isinstance(claim, UnknownFoodClaim):
                    if food_choice:
                        raise ValueError("food choices require asserted food claims")
                    subjects = claim.subject_place_ids
                else:
                    subjects = tuple(subject.place_id for subject in claim.subjects)
                if place_id not in subjects:
                    raise ValueError("food claim belongs to another place")
                if food_choice and claim.topic not in ("food", "menu"):
                    raise ValueError("food choices require food or menu claims")

        for version in self.versions:
            snapshot = snapshots.get(version.research_snapshot_id)
            if snapshot is None:
                raise ValueError("unknown research snapshot")
            candidates = {place.place_id for place in snapshot.researched_candidates}
            claims = {claim.claim_id: claim for claim in snapshot.evidence.food_claims}
            selections = (*version.primaries, *version.runners_up)
            _unique(
                tuple(item.place_id for item in selections),
                "recommendation groups must have unique disjoint places",
            )

            for item in selections:
                claim_support(
                    item.place_id,
                    item.food_claim_ids,
                    candidates,
                    claims,
                    food_choice=False,
                )
            _unique(
                tuple(choice.choice_id for choice in version.food_choices),
                "duplicate food choice IDs within version",
            )
            for choice in version.food_choices:
                if choice.version_id != version.version_id:
                    raise ValueError("food choice differs from offering version")
                _unique(
                    tuple(support.place_id for support in choice.supports),
                    "duplicate food choice support places",
                )
                for support in choice.supports:
                    claim_support(
                        support.place_id,
                        support.food_claim_ids,
                        candidates,
                        claims,
                        food_choice=True,
                    )
            lineage = version.lineage
            if isinstance(lineage, InitialLineage):
                continue
            parent = versions.get(lineage.parent_version_id)
            if parent is None:
                raise ValueError("unknown parent version")
            if parent.version_id == version.version_id:
                raise ValueError("version cannot be its own parent")
            parents[version.version_id] = parent.version_id
            if isinstance(lineage, FoodRefinementLineage):
                if lineage.choice.version_id != parent.version_id:
                    raise ValueError("selected food choice differs from parent version")
                if lineage.choice.choice_id not in {
                    choice.choice_id for choice in parent.food_choices
                }:
                    raise ValueError("selected food choice was not offered by parent")

        complete: set[str] = set()
        for version_id in versions:
            path: set[str] = set()
            current = version_id
            while current not in complete:
                if current in path:
                    raise ValueError("version lineage contains a cycle")
                path.add(current)
                parent_id = parents.get(current)
                if parent_id is None:
                    break
                current = parent_id
            complete.update(path)
        return self
