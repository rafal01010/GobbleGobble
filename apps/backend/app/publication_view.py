from __future__ import annotations

import json
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.core.source_policy import Source
from app.evidence_projection import Citation, project_evidence
from app.publication_validation import CheckedRecommendation, ValidatedPublication
from app.schemas.evidence import EvidenceCollection, ReviewFinding


class _PublicModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class PublicSource(_PublicModel):
    publisher: str
    url: str
    authors: tuple[str, ...]


class PublicFinding(_PublicModel):
    statement: str
    qualification: str
    polarity: str | None
    topic: str | None = None
    wording: str | None = None
    basis: str | None = None
    sources: tuple[PublicSource, ...]


class PublicReviewSample(_PublicModel):
    assessed_count: int
    sample_size: int
    reported_total: int | None
    partial: bool
    qualification: str


class PublicPlace(_PublicModel):
    name: str
    explanation: str
    sources: tuple[PublicSource, ...]
    findings: tuple[PublicFinding, ...] = ()
    review_samples: tuple[PublicReviewSample, ...] = ()
    gaps: tuple[str, ...] = ()


class PublicRecommendations(_PublicModel):
    primaries: tuple[PublicPlace, ...]
    runners_up: tuple[PublicPlace, ...]
    travel_unconfirmed: tuple[PublicPlace, ...]


class PublicResultOverlay(_PublicModel):
    publication: PublicRecommendations
    expires_at: AwareDatetime


def _source(citation: Citation) -> PublicSource:
    return PublicSource(
        publisher=citation.publisher,
        url=citation.url,
        authors=(citation.author,) if citation.author else (),
    )


def project_publication(
    publication: ValidatedPublication,
    *,
    source_classes: dict[str, Source],
    now: datetime,
    approved_findings: frozenset[ReviewFinding],
) -> PublicRecommendations:
    """Create a temporary view from explicitly approved review findings."""
    collection = publication.unverified_draft
    version = collection.versions[0]
    snapshot = next(
        item
        for item in collection.snapshots
        if item.snapshot_id == version.research_snapshot_id
    )
    places = {place.place_id: place for place in snapshot.researched_candidates}
    data = snapshot.evidence.model_dump(mode="json")
    approved = {finding.model_dump_json() for finding in approved_findings}
    for assessment in data["review_assessments"]:
        assessment["findings"] = [
            finding
            for finding in assessment["findings"]
            if ReviewFinding.model_validate_json(json.dumps(finding)).model_dump_json()
            in approved
        ]
        fallback = assessment["fallback"]
        if fallback is not None:
            fallback["findings"] = [
                finding
                for finding in fallback["findings"]
                if ReviewFinding.model_validate_json(
                    json.dumps(finding)
                ).model_dump_json()
                in approved
            ]
    checked_evidence = EvidenceCollection.model_validate_json(json.dumps(data))

    def project(item: CheckedRecommendation) -> PublicPlace:
        place = places[item.place_id]
        if not place.name:
            raise ValueError("selected place has no name")
        evidence = project_evidence(
            checked_evidence, item.place_id, source_classes=source_classes, now=now
        )
        findings = tuple(
            PublicFinding(
                statement=finding.statement,
                qualification=finding.qualification,
                polarity=finding.polarity,
                topic=finding.topic,
                wording=finding.wording,
                basis=finding.basis,
                sources=tuple(_source(citation) for citation in finding.citations),
            )
            for finding in evidence.reviews
        )
        sources = tuple(
            dict.fromkeys(source for item in findings for source in item.sources)
        )
        return PublicPlace(
            name=place.name,
            explanation=(
                "Selected reviews may help you decide."
                if findings
                else "Details about this place are limited."
            ),
            sources=sources,
            findings=findings,
            review_samples=tuple(
                PublicReviewSample(
                    assessed_count=sample.assessed_count,
                    sample_size=sample.sample_size,
                    reported_total=sample.reported_total,
                    partial=sample.partial,
                    qualification=sample.qualification,
                )
                for sample in evidence.review_samples
            ),
            gaps=evidence.gaps,
        )

    return PublicRecommendations(
        primaries=tuple(map(project, publication.qualified_primaries)),
        runners_up=tuple(map(project, publication.qualified_runners)),
        travel_unconfirmed=tuple(
            map(
                project,
                (*publication.unconfirmed_primaries, *publication.unconfirmed_runners),
            )
        ),
    )
