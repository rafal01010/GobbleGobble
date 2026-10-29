from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from app.core.source_policy import Source as SourceClass
from app.evidence_validity import DerivedArtifact, assess_artifacts
from app.schemas.evidence import (
    AvailableReviews,
    EvidenceCollection,
    GoogleReviewLocator,
    IndependentReviewFallback,
    InferredFoodClaim,
    ReviewFinding,
    ReviewSample,
    SourcedFoodClaim,
    SubjectSupport,
    UnknownFoodClaim,
)


@dataclass(frozen=True)
class Citation:
    evidence_id: str
    source_id: str
    publisher: str
    url: str
    author: str | None = None
    link_basis: Literal["document", "review", "branch"] = "document"


@dataclass(frozen=True)
class DisplayFinding:
    statement: str = field(repr=False)
    qualification: str
    citations: tuple[Citation, ...]
    polarity: str | None = None
    wording: str | None = None


@dataclass(frozen=True)
class ReviewSampleDisclosure:
    assessed_count: int
    sample_size: int
    reported_total: int | None
    partial: bool
    qualification: str = "Selected reviews are not a complete review history."


@dataclass(frozen=True)
class EvidenceProjection:
    food: tuple[DisplayFinding, ...]
    reviews: tuple[DisplayFinding, ...]
    review_samples: tuple[ReviewSampleDisclosure, ...]
    gaps: tuple[str, ...]


def _safe_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        return bool(
            url.scheme == "https"
            and url.hostname
            and url.username is None
            and url.password is None
            and url.port in (None, 443)
            and not url.query
            and not url.fragment
            and all(ord(c) >= 32 and ord(c) != 127 for c in value)
        )
    except ValueError:
        return False


def project_evidence(
    collection: EvidenceCollection,
    place_id: str,
    *,
    source_classes: dict[str, SourceClass],
    now: datetime,
) -> EvidenceProjection:
    """Project display-permitted, attributed findings for one place.

    Callers remain responsible for semantic support and for supplying only
    public-safe finding text and publisher/author labels.
    """
    collection = EvidenceCollection.model_validate(collection)
    if place_id not in collection.place_ids:
        raise ValueError("unknown place")
    evidence = {item.evidence_id: item for item in collection.evidence}
    sources = {item.source_id: item for item in collection.sources}
    attributions = {item.attribution_id: item for item in collection.attributions}
    gaps: set[str] = set()
    food: list[DisplayFinding] = []
    reviews: list[DisplayFinding] = []
    samples: list[ReviewSampleDisclosure] = []

    def render(
        statement: str,
        bindings: tuple[SubjectSupport, ...],
        qualification: str,
        polarity: str | None = None,
        wording: str | None = None,
    ) -> DisplayFinding | None:
        binding = next((item for item in bindings if item.place_id == place_id), None)
        if binding is None:
            return None
        result = assess_artifacts(
            collection,
            (
                DerivedArtifact(
                    "display", "claim", binding.evidence_ids, place_id=place_id
                ),
            ),
            source_classes=source_classes,
            now=now,
            action="display",
        )[0]
        if not result.allowed:
            gaps.add(
                "Some source information is unavailable or needs to be checked again."
            )
            return None
        citations = []
        for evidence_id in binding.evidence_ids:
            item = evidence[evidence_id]
            source = sources[item.source_id]
            locator = item.locator
            link_basis: Literal["document", "review", "branch"]
            author = next(
                (
                    attributions[key].name or attributions[key].url
                    for key in item.attribution_ids
                    if attributions[key].kind == "author"
                    and (attributions[key].name or attributions[key].url)
                ),
                None,
            )
            if isinstance(locator, GoogleReviewLocator):
                url = locator.source_url
                link_basis = locator.link_basis
            else:
                url = source.source_url
                link_basis = "document" if locator.kind == "document" else "branch"
            if not _safe_url(url):
                gaps.add("Some source links are unavailable.")
                return None
            citations.append(
                Citation(
                    item.evidence_id,
                    source.source_id,
                    source.publisher,
                    url,
                    author,
                    link_basis,
                )
            )
        return DisplayFinding(
            statement, qualification, tuple(citations), polarity, wording
        )

    for claim in collection.food_claims:
        if isinstance(claim, UnknownFoodClaim):
            if place_id in claim.subject_place_ids:
                gaps.add("Some food information is unavailable.")
            continue
        if isinstance(claim, SourcedFoodClaim | InferredFoodClaim):
            qualifier = (
                "Inferred suggestion."
                if isinstance(claim, InferredFoodClaim)
                else "Source-supported information."
            )
            display = render(claim.statement, claim.subjects, qualifier)
            if display is not None:
                food.append(display)

    def add_findings(
        findings: tuple[ReviewFinding, ...],
        *,
        review_sample: bool,
        independent: bool = False,
    ) -> bool:
        before = len(reviews)
        for finding in findings:
            if review_sample:
                qualification = (
                    (
                        "Inferred from selected independent reviews."
                        if independent
                        else "Inferred from selected reviews."
                    )
                    if finding.interpretation == "inferred"
                    else (
                        "From selected independent reviews."
                        if independent
                        else "From selected reviews."
                    )
                )
            else:
                qualification = (
                    "Inferred from independent sources."
                    if finding.interpretation == "inferred"
                    else "From independent sources."
                )
            display = render(
                finding.statement,
                finding.subjects,
                qualification,
                finding.polarity,
                finding.wording,
            )
            if display is not None:
                reviews.append(display)
        return len(reviews) > before

    def add_sample(sample: ReviewSample) -> None:
        samples.append(
            ReviewSampleDisclosure(
                sample.assessed_count,
                sample.sample_size,
                sample.reported_total,
                sample.partial,
            )
        )

    for assessment in collection.review_assessments:
        if assessment.place_id != place_id:
            continue
        if isinstance(assessment.google, AvailableReviews):
            if add_findings(assessment.findings, review_sample=True):
                add_sample(assessment.google.sample)
        else:
            gaps.add("Review information has not been confirmed.")
        fallback = assessment.fallback
        if fallback is not None:
            is_review_sample = isinstance(fallback, IndependentReviewFallback)
            if add_findings(
                fallback.findings, review_sample=is_review_sample, independent=True
            ) and isinstance(fallback, IndependentReviewFallback):
                add_sample(fallback.sample)
    return EvidenceProjection(
        tuple(food), tuple(reviews), tuple(samples), tuple(sorted(gaps))
    )
