from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.schemas.search import NonBlankText

type SourceId = Annotated[str, StringConstraints(pattern=r"^src_[A-Za-z0-9_-]+$")]
type EvidenceId = Annotated[str, StringConstraints(pattern=r"^ev_[A-Za-z0-9_-]+$")]
type Count = Annotated[int, Field(strict=True, ge=0)]
type PlaceIds = Annotated[tuple[NonBlankText, ...], Field(min_length=1)]
type EvidenceIds = Annotated[tuple[EvidenceId, ...], Field(min_length=1)]


class _Model(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class UseDeclaration(_Model):
    decision: Literal["allowed", "prohibited", "unknown"]
    basis: NonBlankText = Field(repr=False)


class SourcePolicy(_Model):
    access: UseDeclaration
    display: UseDeclaration
    model_input: UseDeclaration
    derived_output: UseDeclaration
    tracing: UseDeclaration


class EphemeralRetention(_Model):
    mode: Literal["ephemeral"] = "ephemeral"
    basis: NonBlankText = Field(repr=False)


class UntilRetention(_Model):
    mode: Literal["until"] = "until"
    until: AwareDatetime = Field(repr=False)
    basis: NonBlankText = Field(repr=False)


class IndefiniteRetention(_Model):
    mode: Literal["indefinite"] = "indefinite"
    basis: NonBlankText = Field(repr=False)


class UnknownRetention(_Model):
    mode: Literal["unknown"] = "unknown"
    basis: NonBlankText = Field(repr=False)


type RetentionDecision = Annotated[
    EphemeralRetention | UntilRetention | IndefiniteRetention | UnknownRetention,
    Field(discriminator="mode"),
]


class Retention(_Model):
    raw_content: RetentionDecision
    derived_content: RetentionDecision
    identifiers: RetentionDecision


class Freshness(_Model):
    retrieved_at: AwareDatetime = Field(repr=False)
    observed_at: AwareDatetime | None = Field(default=None, repr=False)
    fresh_until: AwareDatetime | None = Field(default=None, repr=False)
    basis: NonBlankText = Field(repr=False)


class Attribution(_Model):
    attribution_id: NonBlankText = Field(repr=False)
    kind: Literal["publisher", "author"]
    name: NonBlankText | None = Field(default=None, repr=False)
    url: NonBlankText | None = Field(default=None, repr=False)
    avatar_url: NonBlankText | None = Field(default=None, repr=False)


class GoogleBranchIdentifiers(_Model):
    feature_id: NonBlankText | None = Field(default=None, repr=False)
    cid: NonBlankText | None = Field(default=None, repr=False)
    place_id: NonBlankText | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def identified(self) -> GoogleBranchIdentifiers:
        if not any((self.feature_id, self.cid, self.place_id)):
            raise ValueError("a Google branch identifier is required")
        return self


class GoogleOrigin(_Model):
    kind: Literal["google"] = "google"
    branch: GoogleBranchIdentifiers = Field(repr=False)
    access_method: NonBlankText = Field(repr=False)


class IndependentOrigin(_Model):
    kind: Literal["independent"] = "independent"
    access_method: NonBlankText = Field(repr=False)


type SourceOrigin = Annotated[
    GoogleOrigin | IndependentOrigin, Field(discriminator="kind")
]


class Source(_Model):
    source_id: SourceId = Field(repr=False)
    subject_place_ids: PlaceIds = Field(repr=False)
    publisher: NonBlankText = Field(repr=False)
    source_url: NonBlankText = Field(repr=False)
    attribution_ids: tuple[NonBlankText, ...] = Field(default=(), repr=False)
    origin: SourceOrigin = Field(repr=False)
    policy: SourcePolicy
    retention: Retention


class DocumentLocator(_Model):
    kind: Literal["document"] = "document"
    section: NonBlankText | None = Field(default=None, repr=False)
    exact_quote: NonBlankText | None = Field(default=None, repr=False)


class GoogleReviewLocator(_Model):
    kind: Literal["google_review"] = "google_review"
    branch: GoogleBranchIdentifiers = Field(repr=False)
    review_id: NonBlankText = Field(repr=False)
    source_url: NonBlankText = Field(repr=False)
    link_basis: Literal["review", "branch"]
    exact_quote: NonBlankText | None = Field(default=None, repr=False)


class GoogleBranchLocator(_Model):
    kind: Literal["google_branch"] = "google_branch"
    branch: GoogleBranchIdentifiers = Field(repr=False)


type EvidenceLocator = Annotated[
    DocumentLocator | GoogleReviewLocator | GoogleBranchLocator,
    Field(discriminator="kind"),
]


class Evidence(_Model):
    evidence_id: EvidenceId = Field(repr=False)
    source_id: SourceId = Field(repr=False)
    subject_place_ids: PlaceIds = Field(repr=False)
    statement: NonBlankText = Field(repr=False)
    locator: EvidenceLocator = Field(repr=False)
    attribution_ids: tuple[NonBlankText, ...] = Field(default=(), repr=False)
    freshness: Freshness = Field(repr=False)


class SubjectSupport(_Model):
    place_id: NonBlankText = Field(repr=False)
    evidence_ids: EvidenceIds = Field(repr=False)


type Supports = Annotated[tuple[SubjectSupport, ...], Field(min_length=1)]
type FoodTopic = Literal["food", "menu", "price"]


class SourcedFoodClaim(_Model):
    claim_id: NonBlankText = Field(repr=False)
    status: Literal["sourced"] = "sourced"
    topic: FoodTopic
    statement: NonBlankText = Field(repr=False)
    subjects: Supports = Field(repr=False)


class InferredFoodClaim(_Model):
    claim_id: NonBlankText = Field(repr=False)
    status: Literal["inferred"] = "inferred"
    topic: FoodTopic
    statement: NonBlankText = Field(repr=False)
    subjects: Supports = Field(repr=False)
    basis: NonBlankText = Field(repr=False)


class UnknownFoodClaim(_Model):
    claim_id: NonBlankText = Field(repr=False)
    status: Literal["unknown"] = "unknown"
    topic: FoodTopic
    subject_place_ids: PlaceIds = Field(repr=False)
    gap: NonBlankText = Field(repr=False)


type FoodClaim = Annotated[
    SourcedFoodClaim | InferredFoodClaim | UnknownFoodClaim,
    Field(discriminator="status"),
]


class ReviewFinding(_Model):
    topic: Literal["complaint", "food", "price", "service", "reception"]
    statement: NonBlankText = Field(repr=False)
    subjects: Supports = Field(repr=False)
    interpretation: Literal["sourced", "inferred"] = "sourced"
    basis: NonBlankText | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def inference_basis(self) -> ReviewFinding:
        if self.interpretation == "inferred" and self.basis is None:
            raise ValueError("inferred review finding requires a basis")
        return self


class SampleBounds(_Model):
    max_reviews: Annotated[int, Field(strict=True, gt=0)]
    max_pages: Annotated[int, Field(strict=True, gt=0)] | None = None


class ReviewSample(_Model):
    state: Literal["present", "empty", "omitted"]
    evidence_ids: tuple[EvidenceId, ...] = Field(repr=False)
    sample_size: Count
    assessed_evidence_ids: tuple[EvidenceId, ...] = Field(repr=False)
    assessed_count: Count
    reported_total: Count | None
    reported_total_basis: NonBlankText = Field(repr=False)
    selection_method: NonBlankText = Field(repr=False)
    requested_order: NonBlankText = Field(repr=False)
    observed_order: NonBlankText = Field(repr=False)
    bounds: SampleBounds
    partial: bool = False
    qualification: NonBlankText | None = Field(default=None, repr=False)
    omissions: tuple[NonBlankText, ...] = Field(default=(), repr=False)
    complete_history: Literal[False] = False

    @model_validator(mode="after")
    def sample_counts(self) -> ReviewSample:
        if self.sample_size != len(self.evidence_ids):
            raise ValueError("sample size must count declared review evidence")
        if self.assessed_count != len(self.assessed_evidence_ids):
            raise ValueError("assessed count must count assessed review evidence")
        if not set(self.assessed_evidence_ids) <= set(self.evidence_ids):
            raise ValueError("assessed evidence must belong to the sample")
        if (self.state == "present") != (self.sample_size > 0):
            raise ValueError("sample state does not match sample size")
        if self.sample_size > self.bounds.max_reviews:
            raise ValueError("sample exceeds declared retrieval bounds")
        if self.partial and self.qualification is None:
            raise ValueError("partial samples require a qualification")
        if self.reported_total == 0:
            raise ValueError("confirmed zero reviews uses the zero_review outcome")
        return self


class AvailableReviews(_Model):
    status: Literal["available"] = "available"
    source_id: SourceId = Field(repr=False)
    sample: ReviewSample = Field(repr=False)


class ZeroReviews(_Model):
    status: Literal["zero_review"] = "zero_review"
    source_id: SourceId = Field(repr=False)
    reported_total: Annotated[int, Field(strict=True, ge=0, le=0)] = 0
    confirmation_basis: NonBlankText = Field(repr=False)


class NoReviewMatch(_Model):
    status: Literal["no_match"] = "no_match"
    reason: NonBlankText = Field(repr=False)


class RestrictedReviews(_Model):
    status: Literal["restricted"] = "restricted"
    source_id: SourceId | None = Field(default=None, repr=False)
    requested_use: Literal[
        "access", "display", "model_input", "derived_output", "tracing"
    ]
    reason: NonBlankText = Field(repr=False)


class UnavailableReviews(_Model):
    status: Literal["provider_unavailable"] = "provider_unavailable"
    source_id: SourceId | None = Field(default=None, repr=False)
    reason: NonBlankText = Field(repr=False)


type GoogleReviewOutcome = Annotated[
    AvailableReviews
    | ZeroReviews
    | NoReviewMatch
    | RestrictedReviews
    | UnavailableReviews,
    Field(discriminator="status"),
]
type FallbackReason = Literal[
    "zero_review",
    "empty_sample",
    "omitted_sample",
    "no_match",
    "restricted",
    "provider_unavailable",
    "partial_sample",
]


class IndependentFoodFallback(_Model):
    kind: Literal["food_evidence"] = "food_evidence"
    source_id: SourceId = Field(repr=False)
    reason: FallbackReason
    evidence_ids: EvidenceIds = Field(repr=False)
    findings: tuple[ReviewFinding, ...] = Field(default=(), repr=False)


class IndependentReviewFallback(_Model):
    kind: Literal["review_sample"] = "review_sample"
    source_id: SourceId = Field(repr=False)
    reason: FallbackReason
    sample: ReviewSample = Field(repr=False)
    findings: tuple[ReviewFinding, ...] = Field(default=(), repr=False)


type IndependentFallback = Annotated[
    IndependentFoodFallback | IndependentReviewFallback, Field(discriminator="kind")
]


class ReviewAssessment(_Model):
    assessment_id: NonBlankText = Field(repr=False)
    place_id: NonBlankText = Field(repr=False)
    google: GoogleReviewOutcome = Field(repr=False)
    findings: tuple[ReviewFinding, ...] = Field(default=(), repr=False)
    fallback: IndependentFallback | None = Field(default=None, repr=False)


def _unique(values: tuple[str, ...], message: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(message)


class EvidenceCollection(_Model):
    """Validate declared evidence relationships, without granting source uses."""

    place_ids: tuple[NonBlankText, ...] = Field(repr=False)
    sources: tuple[Source, ...] = Field(default=(), repr=False)
    attributions: tuple[Attribution, ...] = Field(default=(), repr=False)
    evidence: tuple[Evidence, ...] = Field(default=(), repr=False)
    food_claims: tuple[FoodClaim, ...] = Field(default=(), repr=False)
    review_assessments: tuple[ReviewAssessment, ...] = Field(default=(), repr=False)

    @model_validator(mode="after")
    def relationships(self) -> EvidenceCollection:
        _unique(self.place_ids, "duplicate place IDs")
        for values, label in (
            (tuple(s.source_id for s in self.sources), "duplicate source IDs"),
            (
                tuple(a.attribution_id for a in self.attributions),
                "duplicate attribution IDs",
            ),
            (tuple(e.evidence_id for e in self.evidence), "duplicate evidence IDs"),
            (tuple(c.claim_id for c in self.food_claims), "duplicate claim IDs"),
            (
                tuple(a.assessment_id for a in self.review_assessments),
                "duplicate assessment IDs",
            ),
        ):
            _unique(values, label)
        places = set(self.place_ids)
        sources = {s.source_id: s for s in self.sources}
        evidence = {e.evidence_id: e for e in self.evidence}
        attribution_ids = {a.attribution_id for a in self.attributions}

        def subjects(values: tuple[str, ...]) -> None:
            _unique(values, "duplicate subject IDs")
            if not set(values) <= places:
                raise ValueError("unknown subject place")

        def attributions(values: tuple[str, ...]) -> None:
            _unique(values, "duplicate attribution references")
            if not set(values) <= attribution_ids:
                raise ValueError("unknown attribution reference")

        def supports(
            bindings: tuple[SubjectSupport, ...],
            expected_place: str | None = None,
            expected_source: str | None = None,
            allowed_evidence: tuple[str, ...] | None = None,
        ) -> None:
            subjects(tuple(binding.place_id for binding in bindings))
            for binding in bindings:
                if expected_place is not None and binding.place_id != expected_place:
                    raise ValueError("finding subject differs from assessed place")
                _unique(binding.evidence_ids, "duplicate supporting evidence")
                for evidence_id in binding.evidence_ids:
                    item = evidence.get(evidence_id)
                    if item is None:
                        raise ValueError("unknown supporting evidence")
                    if binding.place_id not in item.subject_place_ids:
                        raise ValueError("supporting evidence belongs to another place")
                    if (
                        expected_source is not None
                        and item.source_id != expected_source
                    ):
                        raise ValueError("finding evidence belongs to another source")
                    if (
                        allowed_evidence is not None
                        and evidence_id not in allowed_evidence
                    ):
                        raise ValueError(
                            "finding evidence is outside the assessed sample"
                        )

        def scoped_source(source_id: str, place_id: str, google: bool) -> Source:
            source = sources.get(source_id)
            if source is None:
                raise ValueError("unknown assessment source")
            if place_id not in source.subject_place_ids:
                raise ValueError("assessment source belongs to another place")
            if isinstance(source.origin, GoogleOrigin) != google:
                raise ValueError("assessment source origin does not match its role")
            return source

        def sample_scope(
            sample: ReviewSample, source_id: str, place_id: str, google: bool
        ) -> None:
            _unique(sample.evidence_ids, "duplicate sample evidence")
            _unique(sample.assessed_evidence_ids, "duplicate assessed evidence")
            review_ids: list[str] = []
            for evidence_id in sample.evidence_ids:
                item = evidence.get(evidence_id)
                if item is None:
                    raise ValueError("unknown sample evidence")
                if (
                    item.source_id != source_id
                    or place_id not in item.subject_place_ids
                ):
                    raise ValueError(
                        "sample evidence belongs to another source or place"
                    )
                if google:
                    if not isinstance(item.locator, GoogleReviewLocator):
                        raise ValueError("Google sample requires review evidence")
                    review_ids.append(item.locator.review_id)
            _unique(tuple(review_ids), "duplicate Google reviews in sample")

        for source in self.sources:
            subjects(source.subject_place_ids)
            attributions(source.attribution_ids)
            if (
                isinstance(source.origin, GoogleOrigin)
                and len(source.subject_place_ids) != 1
            ):
                raise ValueError("a Google branch source must identify one subject")
        for item in self.evidence:
            subjects(item.subject_place_ids)
            attributions(item.attribution_ids)
            evidence_source = sources.get(item.source_id)
            if evidence_source is None:
                raise ValueError("unknown evidence source")
            if not set(item.subject_place_ids) <= set(
                evidence_source.subject_place_ids
            ):
                raise ValueError("evidence subjects exceed source scope")
            if isinstance(evidence_source.origin, GoogleOrigin):
                if item.locator.kind == "document":
                    raise ValueError("Google evidence requires a Google locator")
                if item.locator.branch != evidence_source.origin.branch:
                    raise ValueError(
                        "Google evidence branch differs from source branch"
                    )
            elif item.locator.kind != "document":
                raise ValueError("independent evidence cannot use a Google locator")
        for claim in self.food_claims:
            if isinstance(claim, UnknownFoodClaim):
                subjects(claim.subject_place_ids)
            else:
                supports(claim.subjects)
        for assessment in self.review_assessments:
            subjects((assessment.place_id,))
            outcome = assessment.google
            if not isinstance(outcome, NoReviewMatch) and outcome.source_id is not None:
                scoped_source(outcome.source_id, assessment.place_id, True)
            if isinstance(outcome, AvailableReviews):
                sample_scope(
                    outcome.sample, outcome.source_id, assessment.place_id, True
                )
                for finding in assessment.findings:
                    supports(
                        finding.subjects,
                        assessment.place_id,
                        outcome.source_id,
                        outcome.sample.assessed_evidence_ids,
                    )
            elif assessment.findings:
                raise ValueError("Google findings require an available assessed sample")
            fallback = assessment.fallback
            if fallback is None:
                continue
            scoped_source(fallback.source_id, assessment.place_id, False)
            if isinstance(outcome, AvailableReviews):
                reason = {"empty": "empty_sample", "omitted": "omitted_sample"}.get(
                    outcome.sample.state
                )
                if reason is None and outcome.sample.partial:
                    reason = "partial_sample"
            else:
                reason = outcome.status
            if reason != fallback.reason:
                raise ValueError("fallback reason differs from Google primary outcome")
            if isinstance(fallback, IndependentReviewFallback):
                sample_scope(
                    fallback.sample, fallback.source_id, assessment.place_id, False
                )
                allowed = fallback.sample.assessed_evidence_ids
            else:
                supports(
                    (
                        SubjectSupport(
                            place_id=assessment.place_id,
                            evidence_ids=fallback.evidence_ids,
                        ),
                    ),
                    assessment.place_id,
                    fallback.source_id,
                )
                allowed = fallback.evidence_ids
            for finding in fallback.findings:
                supports(
                    finding.subjects, assessment.place_id, fallback.source_id, allowed
                )
        return self
