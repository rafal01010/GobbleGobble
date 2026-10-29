from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.source_policy import require_source_use
from app.db.evidence import StoredFinding, lookup_fresh_evidence
from app.db.ownership import OwnedSearch

_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
type CandidateKind = Literal["branch", "venue", "stall"]
type GapReason = Literal[
    "restricted_source",
    "unavailable_source",
    "missing_menu",
    "missing_reviews",
    "partial_sample",
    "omitted_material",
    "candidate_reacquisition_required",
]
type FindingState = Literal["fresh", "stale", "freshness_unknown"]


@dataclass(frozen=True)
class SnapshotCandidate:
    place_id: str = field(repr=False)
    kind: CandidateKind
    name: str | None = field(default=None, repr=False)
    parent_place_id: str | None = field(default=None, repr=False)
    identity_source: Literal["geoapify_geography", "reacquisition_required"] = (
        "reacquisition_required"
    )
    latitude: float | None = field(default=None, repr=False)
    longitude: float | None = field(default=None, repr=False)
    timezone: str | None = field(default=None, repr=False)


@dataclass(frozen=True, order=True)
class FindingHandle:
    place_id: str = field(repr=False)
    finding_id: str = field(repr=False)


@dataclass(frozen=True)
class SnapshotGap:
    place_id: str = field(repr=False)
    reason: GapReason


@dataclass(frozen=True)
class SnapshotSample:
    place_id: str = field(repr=False)
    source_id: str = field(repr=False)
    sample_size: int
    assessed_count: int
    reported_total: int | None
    selection: Literal["source_selected", "unknown"]
    partial: bool


@dataclass(frozen=True)
class SnapshotView:
    candidates: tuple[SnapshotCandidate, ...] = field(repr=False)
    gaps: tuple[SnapshotGap, ...] = field(repr=False)
    samples: tuple[SnapshotSample, ...] = field(repr=False)
    selected: tuple[FindingHandle, ...] = field(repr=False)
    deferred_count: int
    next_after: FindingHandle | None = field(repr=False)
    deferred_candidates: int
    next_candidate_after: str | None = field(repr=False)


@dataclass(frozen=True)
class FindingLookup:
    state: FindingState
    finding: StoredFinding | None = field(repr=False)


def _id(value: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError("invalid snapshot identifier")


def store_snapshot(
    connection: sqlite3.Connection,
    search: OwnedSearch,
    snapshot_id: str,
    *,
    candidates: tuple[SnapshotCandidate, ...],
    findings: tuple[FindingHandle, ...],
    gaps: tuple[SnapshotGap, ...] = (),
    samples: tuple[SnapshotSample, ...] = (),
) -> None:
    """Index retained evidence and explicitly classified candidate identity data.

    The caller establishes candidate provenance and branch associations. No review
    bodies, findings or free-form gap details are copied into snapshot tables.
    """
    if not connection.in_transaction:
        raise sqlite3.ProgrammingError("snapshot writes require a transaction")
    _id(snapshot_id)
    places = set()
    for candidate in candidates:
        _id(candidate.place_id)
        if candidate.place_id in places:
            raise ValueError("duplicate candidate")
        places.add(candidate.place_id)
        if candidate.kind not in ("branch", "venue", "stall"):
            raise ValueError("invalid candidate kind")
        if candidate.parent_place_id is not None:
            _id(candidate.parent_place_id)
        if candidate.identity_source == "geoapify_geography":
            require_source_use(("geoapify_geography",), "store", material="raw")
            lat, lon = candidate.latitude, candidate.longitude
            if (
                not isinstance(lat, (float, int))
                or isinstance(lat, bool)
                or not isinstance(lon, (float, int))
                or isinstance(lon, bool)
                or not math.isfinite(lat)
                or not math.isfinite(lon)
                or not -90 <= lat <= 90
                or not -180 <= lon <= 180
            ):
                raise ValueError("Geoapify candidate requires valid coordinates")
            try:
                ZoneInfo(candidate.timezone or "")
            except ZoneInfoNotFoundError, ValueError:
                raise ValueError("Geoapify candidate requires a timezone") from None
        elif candidate.identity_source == "reacquisition_required":
            if any(
                value is not None
                for value in (
                    candidate.name,
                    candidate.latitude,
                    candidate.longitude,
                    candidate.timezone,
                )
            ):
                raise ValueError("unavailable candidate cannot retain identity content")
        else:
            raise ValueError("invalid candidate identity source")
        if candidate.name is not None and (
            not isinstance(candidate.name, str)
            or not candidate.name.strip()
            or len(candidate.name) > 500
        ):
            raise ValueError("invalid candidate name")
    if any(
        c.parent_place_id is not None and c.parent_place_id not in places
        for c in candidates
    ):
        raise ValueError("candidate parent is not in snapshot")
    for item in findings:
        _id(item.place_id)
        _id(item.finding_id)
        if (
            item.place_id not in places
            or connection.execute(
                "SELECT 1 FROM evidence_finding_support WHERE search_id=? AND place_id=? AND finding_id=? LIMIT 1",
                (search.search_id, item.place_id, item.finding_id),
            ).fetchone()
            is None
        ):
            raise ValueError("finding is not retained for candidate")
    for gap in gaps:
        if gap.place_id not in places or gap.reason not in (
            "restricted_source",
            "unavailable_source",
            "missing_menu",
            "missing_reviews",
            "partial_sample",
            "omitted_material",
            "candidate_reacquisition_required",
        ):
            raise ValueError("invalid snapshot gap")
    if any(
        sum(1 for sample in samples if sample.place_id == place) > 8 for place in places
    ):
        raise ValueError("too many samples for candidate")
    for sample in samples:
        if (
            sample.place_id not in places
            or type(sample.sample_size) is not int
            or sample.sample_size < 0
            or type(sample.assessed_count) is not int
            or not 0 <= sample.assessed_count <= sample.sample_size
            or (
                sample.reported_total is not None
                and (
                    type(sample.reported_total) is not int
                    or sample.reported_total < sample.sample_size
                )
            )
            or sample.selection not in ("source_selected", "unknown")
            or type(sample.partial) is not bool
        ):
            raise ValueError("invalid sample metadata")
        retained = connection.execute(
            "SELECT s.kind FROM evidence_sources s JOIN evidence_source_subjects p ON p.search_id=s.search_id AND p.source_id=s.source_id WHERE s.search_id=? AND s.source_id=? AND p.place_id=?",
            (search.search_id, sample.source_id, sample.place_id),
        ).fetchone()
        if retained is None or retained[0] != "independent":
            raise ValueError("sample metadata is not retainable")
        require_source_use(("permitted_independent",), "store", material="derived")
    connection.execute(
        "INSERT INTO research_snapshots(search_id,snapshot_id) VALUES (?,?)",
        (search.search_id, snapshot_id),
    )
    connection.executemany(
        "INSERT INTO research_candidates(search_id,snapshot_id,place_id,kind,name,parent_place_id,identity_source,latitude,longitude,timezone) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            (
                search.search_id,
                snapshot_id,
                c.place_id,
                c.kind,
                c.name,
                c.parent_place_id,
                c.identity_source,
                c.latitude,
                c.longitude,
                c.timezone,
            )
            for c in candidates
        ),
    )
    connection.executemany(
        "INSERT INTO research_findings(search_id,snapshot_id,place_id,finding_id) VALUES (?,?,?,?)",
        ((search.search_id, snapshot_id, f.place_id, f.finding_id) for f in findings),
    )
    connection.executemany(
        "INSERT INTO research_samples(search_id,snapshot_id,place_id,source_id,sample_size,assessed_count,reported_total,selection,partial) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            (
                search.search_id,
                snapshot_id,
                x.place_id,
                x.source_id,
                x.sample_size,
                x.assessed_count,
                x.reported_total,
                x.selection,
                int(x.partial),
            )
            for x in samples
        ),
    )
    connection.executemany(
        "INSERT INTO research_gaps(search_id,snapshot_id,place_id,reason) VALUES (?,?,?,?)",
        (
            (search.search_id, snapshot_id, place_id, reason)
            for place_id, reason in sorted(
                {(g.place_id, g.reason) for g in gaps}
                | {
                    (c.place_id, "candidate_reacquisition_required")
                    for c in candidates
                    if c.identity_source == "reacquisition_required"
                }
            )
        ),
    )


def view_snapshot(
    connection: sqlite3.Connection,
    search: OwnedSearch,
    snapshot_id: str,
    *,
    limit: int,
    after: FindingHandle | None = None,
    candidate_after: str | None = None,
    place_id: str | None = None,
) -> SnapshotView:
    """Return candidate identities and a bounded page of scoped finding handles."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if (
        connection.execute(
            "SELECT 1 FROM research_snapshots WHERE search_id=? AND snapshot_id=?",
            (search.search_id, snapshot_id),
        ).fetchone()
        is None
    ):
        raise ValueError("unknown snapshot")
    args: list[object] = [search.search_id, snapshot_id]
    clause = "search_id=? AND snapshot_id=?"
    if place_id is not None:
        clause += " AND place_id=?"
        args.append(place_id)
    candidate_clause = clause
    candidate_args = list(args)
    if candidate_after is not None:
        candidate_clause += " AND place_id>?"
        candidate_args.append(candidate_after)
    candidate_total = connection.execute(
        f"SELECT count(*) FROM research_candidates WHERE {candidate_clause}",
        candidate_args,
    ).fetchone()[0]
    candidates = tuple(
        SnapshotCandidate(*row)
        for row in connection.execute(
            f"SELECT place_id,kind,name,parent_place_id,identity_source,latitude,longitude,timezone FROM research_candidates WHERE {candidate_clause} ORDER BY place_id LIMIT ?",
            (*candidate_args, limit),
        )
    )
    gaps = tuple(
        SnapshotGap(row[0], row[1])
        for candidate in candidates
        for row in connection.execute(
            "SELECT place_id,reason FROM research_gaps WHERE search_id=? AND snapshot_id=? AND place_id=? ORDER BY reason",
            (search.search_id, snapshot_id, candidate.place_id),
        )
    )
    samples = tuple(
        SnapshotSample(row[0], row[1], row[2], row[3], row[4], row[5], bool(row[6]))
        for candidate in candidates
        for row in connection.execute(
            "SELECT place_id,source_id,sample_size,assessed_count,reported_total,selection,partial FROM research_samples WHERE search_id=? AND snapshot_id=? AND place_id=? ORDER BY source_id",
            (search.search_id, snapshot_id, candidate.place_id),
        )
    )
    deferred_candidates = candidate_total - len(candidates)
    if after is not None:
        clause += " AND (place_id,finding_id)>(?,?)"
        args.extend((after.place_id, after.finding_id))
    total = connection.execute(
        f"SELECT count(*) FROM research_findings WHERE {clause}", args
    ).fetchone()[0]
    rows = connection.execute(
        f"SELECT place_id,finding_id FROM research_findings WHERE {clause} ORDER BY place_id,finding_id LIMIT ?",
        (*args, limit),
    ).fetchall()
    selected = tuple(FindingHandle(*row) for row in rows)
    deferred = total - len(selected)
    return SnapshotView(
        candidates,
        gaps,
        samples,
        selected,
        deferred,
        selected[-1] if deferred else None,
        deferred_candidates,
        candidates[-1].place_id if deferred_candidates else None,
    )


def lookup_snapshot_finding(
    connection: sqlite3.Connection,
    search: OwnedSearch,
    snapshot_id: str,
    handle: FindingHandle,
    *,
    now: datetime,
) -> FindingLookup:
    """Resolve a snapshot handle without returning stale or unconfirmed content."""
    if (
        connection.execute(
            "SELECT 1 FROM research_findings WHERE search_id=? AND snapshot_id=? AND place_id=? AND finding_id=?",
            (search.search_id, snapshot_id, handle.place_id, handle.finding_id),
        ).fetchone()
        is None
    ):
        raise ValueError("unknown snapshot finding")
    lookup = lookup_fresh_evidence(connection, search, handle.place_id, now=now)
    for item in lookup.lookup.findings:
        if item.finding_id == handle.finding_id:
            return FindingLookup("fresh", item)
    if handle.finding_id in lookup.stale_findings:
        return FindingLookup("stale", None)
    if handle.finding_id in lookup.unknown_findings:
        return FindingLookup("freshness_unknown", None)
    raise ValueError("snapshot finding no longer available")
