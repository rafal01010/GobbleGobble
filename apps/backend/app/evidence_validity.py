from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from app.core.source_policy import POLICIES, SourceUseDenied, require_source_use
from app.core.source_policy import Source as SourceClass
from app.schemas.evidence import EvidenceCollection, GoogleOrigin

type ArtifactKind = Literal["claim", "summary", "choice", "result", "event", "trace"]
type Action = Literal["display", "store", "trace"]
type FreshnessStatus = Literal["fresh", "stale", "unknown"]
type RetentionStatus = Literal["permitted", "expired", "not_permitted"]


@dataclass(frozen=True)
class DerivedArtifact:
    artifact_id: str
    kind: ArtifactKind
    evidence_ids: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()
    place_id: str | None = None


@dataclass(frozen=True)
class ArtifactValidity:
    artifact_id: str
    freshness: FreshnessStatus
    retention: RetentionStatus
    source_use_allowed: bool
    allowed: bool


def assess_artifacts(
    collection: EvidenceCollection,
    artifacts: tuple[DerivedArtifact, ...],
    *,
    source_classes: dict[str, SourceClass],
    now: datetime,
    action: Action,
    retained: bool = False,
) -> tuple[ArtifactValidity, ...]:
    """Assess explicitly declared dependencies, without inspecting artifact text.

    Callers must supply complete, truthful lineage and invoke this at each use.
    Time-limited content cannot be stored by the current persistence layer.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if action not in ("display", "store", "trace"):
        raise ValueError("unknown action")
    collection = EvidenceCollection.model_validate(collection)
    items = {item.evidence_id: item for item in collection.evidence}
    sources = {source.source_id: source for source in collection.sources}
    by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    if len(by_id) != len(artifacts) or any(not key.strip() for key in by_id):
        raise ValueError("artifact IDs must be nonblank and unique")
    cache: dict[str, frozenset[str]] = {}
    visiting: set[str] = set()

    def dependencies(artifact_id: str) -> frozenset[str]:
        if artifact_id in cache:
            return cache[artifact_id]
        artifact = by_id.get(artifact_id)
        if artifact is None:
            raise ValueError("unknown artifact dependency")
        if artifact_id in visiting:
            raise ValueError("cyclic artifact dependencies")
        if artifact.kind not in (
            "claim",
            "summary",
            "choice",
            "result",
            "event",
            "trace",
        ):
            raise ValueError("unknown artifact kind")
        if not artifact.evidence_ids and not artifact.artifact_ids:
            raise ValueError("artifact requires evidence lineage")
        visiting.add(artifact_id)
        ids = set(artifact.evidence_ids)
        for parent in artifact.artifact_ids:
            ids.update(dependencies(parent))
        visiting.remove(artifact_id)
        if not ids or not ids <= items.keys():
            raise ValueError("unknown evidence dependency")
        if artifact.place_id is not None and any(
            artifact.place_id not in items[key].subject_place_ids for key in ids
        ):
            raise ValueError("evidence dependency belongs to another place")
        cache[artifact_id] = frozenset(ids)
        return cache[artifact_id]

    result = []
    for artifact in artifacts:
        ids = dependencies(artifact.artifact_id)
        freshness: FreshnessStatus = "fresh"
        retention: RetentionStatus = "permitted"
        source_use_allowed = True
        for evidence_id in ids:
            item = items[evidence_id]
            source = sources[item.source_id]
            if item.freshness.fresh_until is None or now < item.freshness.retrieved_at:
                if freshness == "fresh":
                    freshness = "unknown"
            elif now >= item.freshness.fresh_until:
                freshness = "stale"
            rule = source.retention.derived_content
            if rule.mode == "until" and now >= rule.until:
                retention = "expired"
            elif retention != "expired" and (
                rule.mode == "unknown"
                or ((action == "store" or retained) and rule.mode != "indefinite")
            ):
                retention = "not_permitted"
            source_class = source_classes.get(item.source_id)
            google_class = source_class in {
                "public_google_reviews",
                "minimized_google_reviews",
                "ordinary_google",
            }
            if (
                source_class is None
                or source_class not in POLICIES
                or isinstance(source.origin, GoogleOrigin) != google_class
            ):
                source_use_allowed = False
                continue
            declaration = source.policy.derived_output.decision == "allowed"
            if action == "display":
                declaration = (
                    declaration and source.policy.display.decision == "allowed"
                )
            elif action == "trace":
                declaration = (
                    declaration and source.policy.tracing.decision == "allowed"
                )
            if not declaration:
                source_use_allowed = False
            try:
                url = urlsplit(source.source_url)
                attributed = bool(
                    source.publisher.strip()
                    and url.scheme == "https"
                    and url.hostname
                    and url.username is None
                    and url.password is None
                )
                if item.locator.kind == "google_review":
                    attributed = attributed and any(
                        attribution.attribution_id in item.attribution_ids
                        and attribution.kind == "author"
                        and attribution.name
                        for attribution in collection.attributions
                    )
                require_source_use(
                    (source_class,), action, material="derived", attributed=attributed
                )
            except SourceUseDenied, ValueError:
                source_use_allowed = False
        if artifact.kind == "trace" and action != "trace":
            source_use_allowed = False
        result.append(
            ArtifactValidity(
                artifact.artifact_id,
                freshness,
                retention,
                source_use_allowed,
                (action == "store" or freshness == "fresh")
                and retention == "permitted"
                and source_use_allowed,
            )
        )
    return tuple(result)
