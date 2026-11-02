from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from app.core.source_policy import Source, require_source_use
from app.db.events import _append_event, _require_transaction, _validate_history
from app.db.evidence import _safe_url
from app.db.leases import LeaseClaim, _claim_digest, _owned_running, _write_snapshot
from app.db.ownership import OwnedSearch
from app.db.runs import read_snapshot
from app.evidence_validity import DerivedArtifact, assess_artifacts
from app.schemas.evidence import EvidenceCollection, EvidenceId, SourceId
from app.schemas.lifecycle import (
    CompletedState,
    PartialOutcome,
    ReadyOutcome,
    RunSnapshot,
)
from app.schemas.recommendations import SnapshotId, VersionId
from app.schemas.search import NonBlankText


class ResultItem(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    place_id: NonBlankText
    explanation: NonBlankText
    evidence_ids: Annotated[tuple[EvidenceId, ...], Field(min_length=1)]
    route: Literal["within_limit", "unconfirmed"]


class StorableResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    version_id: VersionId
    snapshot_id: SnapshotId
    primaries: Annotated[tuple[ResultItem, ...], Field(max_length=3)]
    runners_up: Annotated[tuple[ResultItem, ...], Field(max_length=4)]
    selection_evidence_ids: Annotated[tuple[EvidenceId, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_items(self) -> StorableResult:
        items = (*self.primaries, *self.runners_up)
        ids = tuple(item.place_id for item in items)
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("result requires distinct selected places")
        if len(self.selection_evidence_ids) != len(set(self.selection_evidence_ids)):
            raise ValueError("selection evidence must be unique")
        for item in items:
            if len(item.evidence_ids) != len(set(item.evidence_ids)):
                raise ValueError("item evidence must be unique")
        return self


class ResultAttribution(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    name: NonBlankText | None
    url: NonBlankText | None
    kind: Literal["publisher", "author"]


class ResultSourceReference(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    evidence_id: EvidenceId
    source_id: SourceId
    source_class: Source
    publisher: NonBlankText
    source_url: NonBlankText
    attributions: tuple[ResultAttribution, ...]
    retrieved_at: AwareDatetime
    fresh_until: AwareDatetime


class StoredResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    result: StorableResult
    sources: tuple[ResultSourceReference, ...]

    @model_validator(mode="after")
    def complete_sources(self) -> StoredResult:
        ids = tuple(source.evidence_id for source in self.sources)
        if len(ids) != len(set(ids)) or set(ids) != set(
            self.result.selection_evidence_ids
        ):
            raise ValueError("source references must cover selection evidence")
        return self


class EphemeralResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    kind: Literal["ephemeral"] = "ephemeral"
    version_id: VersionId
    snapshot_id: SnapshotId


class ResultUnavailable(Exception):
    pass


def read_result(
    connection: sqlite3.Connection,
    search: OwnedSearch,
    *,
    run_id: str,
    now: datetime,
) -> StoredResult | EphemeralResult | None:
    """Read a result for an already-authorized search in a consistent transaction."""
    _require_transaction(connection)
    if now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    snapshot = read_snapshot(connection, search_id=search.search_id, run_id=run_id)
    _validate_history(connection, snapshot)
    if not isinstance(snapshot.state, CompletedState) or not isinstance(
        snapshot.state.outcome, ReadyOutcome | PartialOutcome
    ):
        return None
    row = connection.execute(
        "SELECT version_id, snapshot_id, content_json FROM result_versions WHERE search_id=? AND run_id=?",
        (search.search_id, run_id),
    ).fetchone()
    if row is None:
        raise sqlite3.DatabaseError("missing_result_version")
    try:
        payload = json.loads(row["content_json"])
        stored = (
            EphemeralResult.model_validate_json(row["content_json"])
            if isinstance(payload, dict) and "kind" in payload
            else StoredResult.model_validate_json(row["content_json"])
        )
    except ValidationError, ValueError, TypeError:
        raise sqlite3.DatabaseError("invalid_result_version") from None
    if isinstance(stored, EphemeralResult):
        if (
            stored.version_id != row["version_id"]
            or stored.version_id != snapshot.state.outcome.version_id
            or stored.snapshot_id != row["snapshot_id"]
        ):
            raise sqlite3.DatabaseError("invalid_result_version")
        return stored
    if (
        stored.result.version_id != row["version_id"]
        or stored.result.version_id != snapshot.state.outcome.version_id
        or stored.result.snapshot_id != row["snapshot_id"]
    ):
        raise sqlite3.DatabaseError("invalid_result_version")
    for source in stored.sources:
        if now < source.retrieved_at or now >= source.fresh_until:
            raise ResultUnavailable("result_source_not_fresh")
        require_source_use(
            (source.source_class,), "display", material="derived", attributed=True
        )
    return stored


def publish_ephemeral_result(
    connection: sqlite3.Connection,
    claim: LeaseClaim,
    *,
    result: EphemeralResult,
) -> RunSnapshot:
    """Commit only an opaque result identity with its terminal event."""
    _require_transaction(connection)
    result = EphemeralResult.model_validate_json(result.model_dump_json())
    current = _owned_running(connection, claim)
    digest = _claim_digest(claim)
    _validate_history(connection, current.snapshot)
    search_id = current.snapshot.search_id
    if (
        connection.execute(
            "SELECT 1 FROM research_snapshots WHERE search_id=? AND snapshot_id=?",
            (search_id, result.snapshot_id),
        ).fetchone()
        is None
    ):
        raise ValueError("result snapshot is not owned by search")
    snapshot = RunSnapshot(
        search_id=search_id,
        run_id=claim.run_id,
        operation=current.snapshot.operation,
        attempt_id=claim.attempt_id,
        state=CompletedState(
            outcome=PartialOutcome(
                version_id=result.version_id,
                limitations=("Some information is available only temporarily.",),
            )
        ),
        last_event_sequence=current.snapshot.last_event_sequence + 1,
    )
    connection.execute(
        "INSERT INTO result_versions(version_id, search_id, run_id, snapshot_id, content_json) VALUES (?, ?, ?, ?, ?)",
        (
            result.version_id,
            search_id,
            claim.run_id,
            result.snapshot_id,
            result.model_dump_json(),
        ),
    ).close()
    _append_event(connection, snapshot)
    _write_snapshot(connection, current, claim, digest, snapshot)
    return snapshot


def publish_result(
    connection: sqlite3.Connection,
    claim: LeaseClaim,
    *,
    result: StorableResult,
    evidence: EvidenceCollection,
    source_classes: dict[str, Source],
    now: datetime,
    partial: bool,
) -> RunSnapshot:
    """Store a checked result and terminal event in the caller's transaction.

    The caller must supply every model input as evidence and check semantic support.
    This boundary validates retention and subject bindings, not entailment.
    """
    _require_transaction(connection)
    if type(partial) is not bool:
        raise ValueError("partial must be a boolean")
    result = StorableResult.model_validate(result)
    evidence = EvidenceCollection.model_validate(evidence)
    input_ids = {item.evidence_id for item in evidence.evidence}
    if set(result.selection_evidence_ids) != input_ids:
        raise ValueError("selection lineage must cover all supplied evidence")
    if set(source_classes) != {source.source_id for source in evidence.sources}:
        raise ValueError("every source requires a source class")
    artifacts = [DerivedArtifact("selection", "result", result.selection_evidence_ids)]
    for index, item in enumerate((*result.primaries, *result.runners_up)):
        artifacts.append(
            DerivedArtifact(
                f"item_{index}", "result", item.evidence_ids, place_id=item.place_id
            )
        )
    if not all(
        check.allowed
        for check in assess_artifacts(
            evidence,
            tuple(artifacts),
            source_classes=source_classes,
            now=now,
            action="store",
        )
    ):
        raise ValueError("result source storage is not permitted")
    if not all(
        check.allowed
        for check in assess_artifacts(
            evidence,
            tuple(artifacts),
            source_classes=source_classes,
            now=now,
            action="display",
            retained=True,
        )
    ):
        raise ValueError("result source display is not permitted or is not fresh")
    sources = {source.source_id: source for source in evidence.sources}
    attributions = {item.attribution_id: item for item in evidence.attributions}
    references = []
    for evidence_item in evidence.evidence:
        source = sources[evidence_item.source_id]
        source_class = source_classes[source.source_id]
        require_source_use((source_class,), "store", material="raw")
        if (
            source.retention.raw_content.mode != "indefinite"
            or source.retention.identifiers.mode != "indefinite"
            or not _safe_url(source.source_url)
            or evidence_item.freshness.fresh_until is None
        ):
            raise ValueError("source reference storage is not permitted")
        source_attributions = []
        for attribution_id in dict.fromkeys(
            (*source.attribution_ids, *evidence_item.attribution_ids)
        ):
            attribution = attributions[attribution_id]
            if attribution.url is not None and not _safe_url(attribution.url):
                raise ValueError("source attribution URL is not permitted")
            source_attributions.append(
                ResultAttribution(
                    name=attribution.name, url=attribution.url, kind=attribution.kind
                )
            )
        references.append(
            ResultSourceReference(
                evidence_id=evidence_item.evidence_id,
                source_id=source.source_id,
                source_class=source_class,
                publisher=source.publisher,
                source_url=source.source_url,
                attributions=tuple(source_attributions),
                retrieved_at=evidence_item.freshness.retrieved_at,
                fresh_until=evidence_item.freshness.fresh_until,
            )
        )
    stored = StoredResult(result=result, sources=tuple(references))
    current = _owned_running(connection, claim)
    digest = _claim_digest(claim)
    _validate_history(connection, current.snapshot)
    search_id = current.snapshot.search_id
    if (
        connection.execute(
            "SELECT 1 FROM research_snapshots WHERE search_id=? AND snapshot_id=?",
            (search_id, result.snapshot_id),
        ).fetchone()
        is None
    ):
        raise ValueError("result snapshot is not owned by search")
    for item in (*result.primaries, *result.runners_up):
        if (
            connection.execute(
                "SELECT 1 FROM research_candidates WHERE search_id=? AND snapshot_id=? AND place_id=?",
                (search_id, result.snapshot_id, item.place_id),
            ).fetchone()
            is None
        ):
            raise ValueError("result place is not in snapshot")
    outcome = (
        PartialOutcome(
            version_id=result.version_id,
            limitations=("Some information is unavailable.",),
        )
        if partial
        else ReadyOutcome(version_id=result.version_id)
    )
    snapshot = RunSnapshot(
        search_id=search_id,
        run_id=claim.run_id,
        operation=current.snapshot.operation,
        attempt_id=claim.attempt_id,
        state=CompletedState(outcome=outcome),
        last_event_sequence=current.snapshot.last_event_sequence + 1,
    )
    connection.execute(
        "INSERT INTO result_versions(version_id, search_id, run_id, snapshot_id, content_json) VALUES (?, ?, ?, ?, ?)",
        (
            result.version_id,
            search_id,
            claim.run_id,
            result.snapshot_id,
            stored.model_dump_json(),
        ),
    ).close()
    _append_event(connection, snapshot)
    _write_snapshot(connection, current, claim, digest, snapshot)
    return snapshot
