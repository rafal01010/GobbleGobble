from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

from app.core.source_policy import Source, require_source_use
from app.db.ownership import OwnedSearch
from app.schemas.evidence import (
    EvidenceCollection,
    GoogleOrigin,
    IndependentOrigin,
    InferredFoodClaim,
    ReviewFinding,
    SourcedFoodClaim,
)

_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_FEATURE = re.compile(r"0x[0-9a-fA-F]{1,16}:0x[0-9a-fA-F]{1,16}\Z")
_DECIMAL = re.compile(r"[0-9]{1,20}\Z")


@dataclass(frozen=True)
class StoredSource:
    source_id: str
    kind: Literal["google", "independent"]
    metadata: dict[str, object] = field(repr=False)


@dataclass(frozen=True)
class StoredEvidence:
    evidence_id: str
    source_id: str
    statement: str = field(repr=False)
    section: str | None = field(repr=False)
    exact_quote: str | None = field(repr=False)
    retrieved_at: str = field(repr=False)
    fresh_until: str | None = field(repr=False)
    attributions: list[dict[str, object]] = field(repr=False)


@dataclass(frozen=True)
class StoredFinding:
    finding_id: str
    topic: str
    statement: str = field(repr=False)
    interpretation: str
    basis: str | None = field(repr=False)
    evidence_ids: tuple[str, ...] = field(repr=False)
    supports: tuple[tuple[str, str], ...] = field(repr=False)


@dataclass(frozen=True)
class EvidenceLookup:
    sources: tuple[StoredSource, ...]
    evidence: tuple[StoredEvidence, ...]
    findings: tuple[StoredFinding, ...]


def _safe_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        host = (url.hostname or "").lower().rstrip(".")
        return (
            url.scheme == "https"
            and bool(host)
            and url.username is None
            and url.password is None
            and url.port in (None, 443)
            and not url.query
            and not url.fragment
            and all(ord(c) >= 32 and ord(c) != 127 for c in value)
            and not any(
                label
                in {
                    "google",
                    "googleusercontent",
                    "googleapis",
                    "gstatic",
                    "wikivoyage",
                }
                for label in host.split(".")
            )
            and host not in {"g.co", "goo.gl", "youtube.com", "youtu.be"}
            and not host.endswith((".g.co", ".goo.gl", ".youtube.com", ".youtu.be"))
        )
    except ValueError:
        return False


def _google_ids(origin: GoogleOrigin) -> dict[str, object]:
    branch = origin.branch
    result: dict[str, object] = {}
    for key, value, pattern in (
        ("feature_id", branch.feature_id, _FEATURE),
        ("cid", branch.cid, _DECIMAL),
    ):
        if value is not None:
            if pattern.fullmatch(value) is None:
                raise ValueError("invalid Google branch identifier")
            result[key] = value
    return result


def store_evidence(
    connection: sqlite3.Connection,
    search: OwnedSearch,
    collection: EvidenceCollection,
    *,
    source_classes: dict[str, Source],
) -> None:
    """Store a validated evidence projection in the caller's write transaction.

    Source classes and subject associations are trusted upstream assertions.
    Unknown, restricted and time-limited content is not persisted.
    """
    if not connection.in_transaction:
        raise sqlite3.ProgrammingError("evidence writes require a transaction")
    collection = EvidenceCollection.model_validate(collection)
    if set(source_classes) != {source.source_id for source in collection.sources}:
        raise ValueError("each source requires exactly one source class")
    if any(_IDENTIFIER.fullmatch(place) is None for place in collection.place_ids):
        raise ValueError("invalid place identifier")

    retained: set[str] = set()
    attributions = {item.attribution_id: item for item in collection.attributions}
    for source in collection.sources:
        source_class = source_classes[source.source_id]
        if isinstance(source.origin, GoogleOrigin):
            if source_class not in {
                "public_google_reviews",
                "minimized_google_reviews",
                "ordinary_google",
            }:
                raise ValueError("Google origin has a mismatched source class")
            if source.retention.identifiers.mode != "indefinite":
                continue
            metadata: dict[str, object] = _google_ids(source.origin)
            if not metadata:
                continue
            kind = "google"
        elif isinstance(source.origin, IndependentOrigin):
            if source_class != "permitted_independent":
                continue
            if not _safe_url(source.source_url):
                raise ValueError("independent source URL is not permitted")
            if (
                any(
                    decision.mode != "indefinite"
                    for decision in (
                        source.retention.raw_content,
                        source.retention.derived_content,
                        source.retention.identifiers,
                    )
                )
                or source.policy.derived_output.decision != "allowed"
            ):
                continue
            require_source_use((source_class,), "store", material="raw")
            require_source_use((source_class,), "store", material="derived")
            metadata = {
                "publisher": source.publisher,
                "source_url": source.source_url,
                "attributions": [
                    attributions[key].model_dump(mode="json")
                    for key in source.attribution_ids
                ],
            }
            kind = "independent"
            retained.add(source.source_id)
        else:
            raise TypeError("unsupported source origin")
        connection.execute(
            "INSERT INTO evidence_sources(search_id, source_id, kind, metadata_json) VALUES (?, ?, ?, ?)",
            (
                search.search_id,
                source.source_id,
                kind,
                json.dumps(metadata, ensure_ascii=False),
            ),
        ).close()
        connection.executemany(
            "INSERT INTO evidence_source_subjects(search_id, source_id, place_id) VALUES (?, ?, ?)",
            (
                (search.search_id, source.source_id, place)
                for place in source.subject_place_ids
            ),
        ).close()
    stored_evidence: set[str] = set()
    for item in collection.evidence:
        if item.source_id not in retained:
            continue
        if item.locator.kind != "document":
            raise ValueError("independent evidence requires a document locator")
        content = {
            "statement": item.statement,
            "section": item.locator.section,
            "exact_quote": item.locator.exact_quote,
            "retrieved_at": item.freshness.retrieved_at.isoformat(),
            "fresh_until": item.freshness.fresh_until.isoformat()
            if item.freshness.fresh_until
            else None,
            "attributions": [
                attributions[key].model_dump(mode="json")
                for key in item.attribution_ids
            ],
        }
        connection.execute(
            "INSERT INTO evidence_items(search_id, evidence_id, source_id, content_json) VALUES (?, ?, ?, ?)",
            (
                search.search_id,
                item.evidence_id,
                item.source_id,
                json.dumps(content, ensure_ascii=False),
            ),
        ).close()
        connection.executemany(
            "INSERT INTO evidence_subjects(search_id, evidence_id, place_id) VALUES (?, ?, ?)",
            (
                (search.search_id, item.evidence_id, place)
                for place in item.subject_place_ids
            ),
        ).close()
        stored_evidence.add(item.evidence_id)

    def save_finding(
        finding_id: str, finding: SourcedFoodClaim | InferredFoodClaim | ReviewFinding
    ) -> None:
        if any(
            evidence_id not in stored_evidence
            for subject in finding.subjects
            for evidence_id in subject.evidence_ids
        ):
            return
        if isinstance(finding, ReviewFinding):
            interpretation = finding.interpretation
            basis = finding.basis
        else:
            interpretation = finding.status
            basis = finding.basis if isinstance(finding, InferredFoodClaim) else None
        content = {
            "topic": finding.topic,
            "statement": finding.statement,
            "interpretation": interpretation,
            "basis": basis,
        }
        connection.execute(
            "INSERT INTO evidence_findings(search_id, finding_id, content_json) VALUES (?, ?, ?)",
            (search.search_id, finding_id, json.dumps(content, ensure_ascii=False)),
        ).close()
        connection.executemany(
            "INSERT INTO evidence_finding_support(search_id, finding_id, place_id, evidence_id) VALUES (?, ?, ?, ?)",
            (
                (search.search_id, finding_id, subject.place_id, evidence_id)
                for subject in finding.subjects
                for evidence_id in subject.evidence_ids
            ),
        ).close()

    for claim in collection.food_claims:
        if isinstance(claim, (SourcedFoodClaim, InferredFoodClaim)):
            if _IDENTIFIER.fullmatch(claim.claim_id) is None:
                raise ValueError("invalid claim identifier")
            save_finding("food_" + claim.claim_id, claim)
    for assessment in collection.review_assessments:
        if _IDENTIFIER.fullmatch(assessment.assessment_id) is None:
            raise ValueError("invalid assessment identifier")
        for index, finding in enumerate(assessment.findings):
            save_finding(f"review_{assessment.assessment_id}_{index}", finding)
        if assessment.fallback is not None:
            for index, finding in enumerate(assessment.fallback.findings):
                save_finding(f"fallback_{assessment.assessment_id}_{index}", finding)


def lookup_evidence(
    connection: sqlite3.Connection, search: OwnedSearch, place_id: str
) -> EvidenceLookup:
    """Return only source context and evidence explicitly associated with a place."""
    sources = connection.execute(
        "SELECT s.source_id, s.kind, s.metadata_json FROM evidence_sources AS s JOIN evidence_source_subjects AS p ON p.search_id=s.search_id AND p.source_id=s.source_id WHERE s.search_id=? AND p.place_id=? ORDER BY s.source_id",
        (search.search_id, place_id),
    ).fetchall()
    items = connection.execute(
        "SELECT e.evidence_id, e.source_id, e.content_json FROM evidence_items AS e JOIN evidence_subjects AS p ON p.search_id=e.search_id AND p.evidence_id=e.evidence_id WHERE e.search_id=? AND p.place_id=? ORDER BY e.evidence_id",
        (search.search_id, place_id),
    ).fetchall()
    findings = connection.execute(
        "SELECT f.finding_id, f.content_json, p.place_id, p.evidence_id FROM evidence_findings AS f JOIN evidence_finding_support AS p ON p.search_id=f.search_id AND p.finding_id=f.finding_id WHERE f.search_id=? AND EXISTS (SELECT 1 FROM evidence_finding_support AS target WHERE target.search_id=f.search_id AND target.finding_id=f.finding_id AND target.place_id=?) ORDER BY f.finding_id, p.place_id, p.evidence_id",
        (search.search_id, place_id),
    ).fetchall()
    grouped: dict[str, tuple[dict[str, Any], list[tuple[str, str]]]] = {}
    for row in findings:
        finding_id = row["finding_id"]
        if finding_id not in grouped:
            grouped[finding_id] = (json.loads(row["content_json"]), [])
        grouped[finding_id][1].append((row["place_id"], row["evidence_id"]))
    return EvidenceLookup(
        tuple(
            StoredSource(
                row["source_id"], row["kind"], json.loads(row["metadata_json"])
            )
            for row in sources
        ),
        tuple(
            StoredEvidence(
                row["evidence_id"], row["source_id"], **json.loads(row["content_json"])
            )
            for row in items
        ),
        tuple(
            StoredFinding(
                finding_id,
                **content,
                evidence_ids=tuple(
                    evidence for subject, evidence in supports if subject == place_id
                ),
                supports=tuple(supports),
            )
            for finding_id, (content, supports) in grouped.items()
        ),
    )
