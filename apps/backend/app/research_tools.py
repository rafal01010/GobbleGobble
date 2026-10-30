from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.core.source_policy import Source, SourceUseDenied, require_source_use
from app.db.ownership import OwnedSearch
from app.schemas.evidence import (
    AvailableReviews,
    EvidenceCollection,
    GoogleOrigin,
    GoogleReviewLocator,
    ZeroReviews,
)

type Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]


class _Input(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", revalidate_instances="always"
    )


class PageInput(_Input):
    place_id: Identifier
    after: Identifier | None = None
    limit: Annotated[int, Field(strict=True, ge=1, le=20)] = 10


class LookupInput(_Input):
    place_id: Identifier
    item_id: Identifier


class TextInput(LookupInput):
    offset: Annotated[int, Field(strict=True, ge=0)] = 0


class SamplePageInput(PageInput):
    assessment_id: Identifier


class ResearchTools:
    """Attempt-scoped access to retained handles and ephemeral review evidence."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        search: OwnedSearch,
        snapshot_id: str,
        *,
        place_ids: tuple[str, ...],
        max_content_bytes: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        ephemeral: EvidenceCollection | None = None,
        source_classes: dict[str, Source] | None = None,
    ) -> None:
        if type(max_content_bytes) is not int or not 512 <= max_content_bytes <= 65536:
            raise ValueError("invalid content limit")
        if len(place_ids) > 100 or len(set(place_ids)) != len(place_ids):
            raise ValueError("invalid place scope")
        for place_id in place_ids:
            LookupInput(place_id=place_id, item_id="scope")
        if (
            connection.execute(
                "SELECT 1 FROM research_snapshots WHERE search_id=? AND snapshot_id=?",
                (search.search_id, snapshot_id),
            ).fetchone()
            is None
        ):
            raise ValueError("unknown snapshot")
        for place_id in place_ids:
            if (
                connection.execute(
                    "SELECT 1 FROM research_candidates WHERE search_id=? AND snapshot_id=? AND place_id=?",
                    (search.search_id, snapshot_id, place_id),
                ).fetchone()
                is None
            ):
                raise ValueError("unknown scope")
        self._connection = connection
        self._search = search
        self._snapshot_id = snapshot_id
        self._place_ids = frozenset(place_ids)
        self._clock = clock
        self._handles: dict[str, tuple[str, str]] = {}
        self._handle_by_item: dict[tuple[str, str], str] = {}
        self._max_content_bytes = max_content_bytes
        self._ephemeral = (
            EvidenceCollection.model_validate_json(ephemeral.model_dump_json())
            if ephemeral is not None
            else None
        )
        self._source_classes = dict(source_classes or {})
        if ephemeral is not None:
            if len(ephemeral.evidence) > 200:
                raise ValueError("too much ephemeral evidence")
            if any(len(item.evidence_id) > 128 for item in ephemeral.evidence):
                raise ValueError("ephemeral evidence ID exceeds limit")
            if not set(ephemeral.place_ids) <= self._place_ids:
                raise ValueError("ephemeral evidence exceeds scope")

    @property
    def snapshot_id(self) -> str:
        return self._snapshot_id

    @property
    def search_id(self) -> str:
        return self._search.search_id

    def matches_ephemeral(self, collection: EvidenceCollection) -> bool:
        return self._ephemeral is not None and self._ephemeral == collection

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return now

    def _handle(self, place_id: str, item_id: str) -> str:
        key = (place_id, item_id)
        if key not in self._handle_by_item:
            handle = secrets.token_urlsafe(24)
            self._handles[handle] = key
            self._handle_by_item[key] = handle
        return self._handle_by_item[key]

    def list_findings(self, request: PageInput) -> dict[str, object]:
        request = PageInput.model_validate(request)
        if request.place_id not in self._place_ids:
            return {"status": "unavailable"}
        after = ""
        if request.after is not None:
            saved = self._handles.get(request.after)
            if saved is None or saved[0] != request.place_id:
                return {"status": "unavailable"}
            after = saved[1]
        args = (self._search.search_id, self._snapshot_id, request.place_id)
        rows = self._connection.execute(
            "SELECT finding_id FROM research_findings WHERE search_id=? AND snapshot_id=? AND place_id=? AND finding_id>? ORDER BY finding_id LIMIT ?",
            (*args, after, request.limit),
        ).fetchall()
        selected = [row[0] for row in rows]
        while selected:
            count = self._connection.execute(
                "SELECT COUNT(*) FROM research_findings WHERE search_id=? AND snapshot_id=? AND place_id=? AND finding_id>?",
                (*args, selected[-1]),
            ).fetchone()[0]
            handles = [self._handle(request.place_id, item) for item in selected]
            payload = {
                "status": "ok",
                "finding_handles": handles,
                "deferred_count": count,
                "next_after": handles[-1] if count else None,
            }
            if self._size(payload) <= self._max_content_bytes:
                return payload
            selected.pop()
        return (
            {
                "status": "ok",
                "finding_handles": [],
                "deferred_count": 0,
                "next_after": None,
            }
            if not rows
            else {"status": "oversized"}
        )

    def read_finding(self, request: LookupInput) -> dict[str, object]:
        request = LookupInput.model_validate(request)
        item = self._handles.get(request.item_id)
        if (
            request.place_id not in self._place_ids
            or item is None
            or item[0] != request.place_id
        ):
            return {"status": "unavailable"}
        exists = self._connection.execute(
            "SELECT 1 FROM research_findings WHERE search_id=? AND snapshot_id=? AND place_id=? AND finding_id=?",
            (self._search.search_id, self._snapshot_id, *item),
        ).fetchone()
        if exists is None:
            return {"status": "unavailable"}
        rows = self._connection.execute(
            "SELECT e.content_json FROM evidence_finding_support f JOIN evidence_items e ON e.search_id=f.search_id AND e.evidence_id=f.evidence_id WHERE f.search_id=? AND f.finding_id=?",
            (self._search.search_id, item[1]),
        ).fetchall()
        now = self._now()
        if not rows:
            return {"status": "unavailable"}
        states = []
        for row in rows:
            try:
                content = json.loads(row[0])
                retrieved = datetime.fromisoformat(content["retrieved_at"])
                expires = datetime.fromisoformat(content["fresh_until"])
                if (
                    retrieved.tzinfo is None
                    or expires.tzinfo is None
                    or retrieved > now
                ):
                    states.append("freshness_unknown")
                elif expires <= now:
                    states.append("stale")
                else:
                    states.append("fresh")
            except ValueError, TypeError, KeyError:
                states.append("freshness_unknown")
        if "stale" in states:
            return {"status": "stale"}
        if "freshness_unknown" in states:
            return {"status": "freshness_unknown"}
        return {"status": "restricted"}

    def list_ephemeral_evidence(self, request: PageInput) -> dict[str, object]:
        request = PageInput.model_validate(request)
        if request.place_id not in self._place_ids or self._ephemeral is None:
            return {"status": "unavailable"}
        restricted_count = sum(
            1
            for item in self._ephemeral.evidence
            if request.place_id in item.subject_place_ids
            and not self._approved(item.source_id, item.locator.kind)
        )
        ids = sorted(
            item.evidence_id
            for item in self._ephemeral.evidence
            if request.place_id in item.subject_place_ids
            and item.evidence_id > (request.after or "")
            and len(item.evidence_id) <= 128
            and self._approved(item.source_id, item.locator.kind)
        )
        selected = ids[: request.limit]
        while selected:
            deferred = len(ids) - len(selected)
            payload = {
                "status": "ok",
                "place_id": request.place_id,
                "evidence_ids": selected,
                "deferred_count": deferred,
                "restricted_count": restricted_count,
                "next_after": selected[-1] if deferred else None,
            }
            if self._size(payload) <= self._max_content_bytes:
                return payload
            selected.pop()
        if ids:
            return {"status": "oversized"}
        return {
            "status": "ok",
            "place_id": request.place_id,
            "evidence_ids": [],
            "deferred_count": 0,
            "restricted_count": restricted_count,
            "next_after": None,
        }

    def _size(self, value: dict[str, object]) -> int:
        return len(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )

    def _approved(self, source_id: str, locator_kind: str) -> bool:
        assert self._ephemeral is not None
        source = next(s for s in self._ephemeral.sources if s.source_id == source_id)
        source_class = self._source_classes.get(source_id)
        if (
            source_class != "public_google_reviews"
            or not isinstance(source.origin, GoogleOrigin)
            or locator_kind != "google_review"
            or source.policy.access.decision != "allowed"
            or source.policy.model_input.decision != "allowed"
        ):
            return False
        try:
            require_source_use((source_class,), "model", material="raw")
        except SourceUseDenied:
            return False
        return True

    def read_ephemeral_evidence(self, request: TextInput) -> dict[str, object]:
        request = TextInput.model_validate(request)
        if request.place_id not in self._place_ids or self._ephemeral is None:
            return {"status": "unavailable"}
        item = next(
            (
                item
                for item in self._ephemeral.evidence
                if item.evidence_id == request.item_id
                and request.place_id in item.subject_place_ids
            ),
            None,
        )
        if item is None:
            return {"status": "unavailable"}
        source = next(
            s for s in self._ephemeral.sources if s.source_id == item.source_id
        )
        if not self._approved(source.source_id, item.locator.kind):
            return {"status": "restricted"}
        now = self._now()
        if item.freshness.retrieved_at > now or item.freshness.fresh_until is None:
            return {"status": "freshness_unknown"}
        if item.freshness.fresh_until <= now:
            return {"status": "stale"}
        attribution_ids = set(source.attribution_ids) | set(item.attribution_ids)
        payload: dict[str, object] = {
            "status": "available",
            "place_id": request.place_id,
            "evidence_id": item.evidence_id,
            "source_id": source.source_id,
            "publisher": source.publisher,
            "source_url": source.source_url,
            "statement": "",
            "locator": item.locator.model_dump(exclude={"exact_quote"}),
            "attributions": [
                a.model_dump()
                for a in self._ephemeral.attributions
                if a.attribution_id in attribution_ids
            ],
        }
        if request.offset > len(item.statement):
            return {"status": "unavailable"}
        payload.update(
            {"offset": request.offset, "next_offset": None, "omitted_characters": 0}
        )
        if self._size(payload) > self._max_content_bytes:
            return {"status": "oversized", "evidence_id": item.evidence_id}
        low, high = request.offset, len(item.statement)
        while low < high:
            mid = (low + high + 1) // 2
            payload["statement"] = item.statement[request.offset : mid]
            payload["next_offset"] = mid if mid < len(item.statement) else None
            payload["omitted_characters"] = len(item.statement) - mid
            if self._size(payload) <= self._max_content_bytes:
                low = mid
            else:
                high = mid - 1
        if low == request.offset and low < len(item.statement):
            return {"status": "oversized", "evidence_id": item.evidence_id}
        payload["statement"] = item.statement[request.offset : low]
        payload["next_offset"] = low if low < len(item.statement) else None
        payload["omitted_characters"] = len(item.statement) - low
        return payload

    def review_freshness(self, place_id: str, review_id: str) -> str:
        """Check the bound review evidence without exposing its content."""
        if place_id not in self._place_ids or self._ephemeral is None:
            return "freshness_unknown"
        matches = [
            item
            for item in self._ephemeral.evidence
            if place_id in item.subject_place_ids
            and isinstance(item.locator, GoogleReviewLocator)
            and item.locator.review_id == review_id
        ]
        if len(matches) != 1:
            return "freshness_unknown"
        result = self.read_ephemeral_evidence(
            TextInput(place_id=place_id, item_id=matches[0].evidence_id, offset=0)
        )
        return str(result.get("status", "unavailable"))

    def review_binding(
        self,
        place_id: str,
        review_id: str,
        *,
        feature_id: str,
        source_url: str,
        link_basis: str,
        text: str | None,
        author_name: str | None = None,
        author_url: str | None = None,
    ) -> str:
        """Check a supplied sample against scoped source identity and evidence."""
        if place_id not in self._place_ids or self._ephemeral is None or not text:
            return "freshness_unknown"
        matches = [
            item
            for item in self._ephemeral.evidence
            if place_id in item.subject_place_ids
            and isinstance(item.locator, GoogleReviewLocator)
            and item.locator.review_id == review_id
        ]
        if len(matches) != 1:
            return "freshness_unknown"
        item = matches[0]
        locator = item.locator
        if not isinstance(locator, GoogleReviewLocator):
            return "freshness_unknown"
        source = next(
            s for s in self._ephemeral.sources if s.source_id == item.source_id
        )
        if (
            not isinstance(source.origin, GoogleOrigin)
            or locator.branch.feature_id != feature_id
            or source.origin.branch.feature_id != feature_id
            or locator.source_url != source_url
            or locator.link_basis != link_basis
            or item.statement != text
        ):
            return "source_mismatch"
        item_authors = [
            attribution
            for attribution in self._ephemeral.attributions
            if attribution.kind == "author"
            and attribution.attribution_id in item.attribution_ids
        ]
        authors = item_authors or [
            attribution
            for attribution in self._ephemeral.attributions
            if attribution.kind == "author"
            and attribution.attribution_id in source.attribution_ids
        ]
        if authors and all(
            (
                author.name is not None
                and author_name is not None
                and author.name != author_name
            )
            or (
                author.url is not None
                and author_url is not None
                and author.url != author_url
            )
            for author in authors
        ):
            return "source_mismatch"
        if source.policy.derived_output.decision != "allowed":
            return "restricted"
        try:
            require_source_use(("public_google_reviews",), "model", material="derived")
        except SourceUseDenied:
            return "restricted"
        result = self.read_ephemeral_evidence(
            TextInput(place_id=place_id, item_id=item.evidence_id, offset=0)
        )
        return str(result.get("status", "unavailable"))

    def read_review_context(self, request: LookupInput) -> dict[str, object]:
        request = LookupInput.model_validate(request)
        if request.place_id not in self._place_ids or self._ephemeral is None:
            return {"status": "unavailable"}
        assessment = next(
            (
                a
                for a in self._ephemeral.review_assessments
                if a.place_id == request.place_id and a.assessment_id == request.item_id
            ),
            None,
        )
        if assessment is None:
            return {"status": "unavailable"}
        outcome = assessment.google
        if isinstance(outcome, (AvailableReviews, ZeroReviews)):
            if not self._approved(outcome.source_id, "google_review"):
                return {"status": "restricted"}
            payload: dict[str, object] = {
                "status": outcome.status,
                "source_id": outcome.source_id,
            }
            if isinstance(outcome, AvailableReviews):
                sample = outcome.sample
                now = self._now()
                sample_evidence = [
                    e
                    for e in self._ephemeral.evidence
                    if e.evidence_id in sample.evidence_ids
                ]
                if any(
                    e.freshness.fresh_until is not None
                    and e.freshness.fresh_until <= now
                    for e in sample_evidence
                ):
                    return {"status": "stale"}
                if any(
                    e.freshness.fresh_until is None or e.freshness.retrieved_at > now
                    for e in sample_evidence
                ):
                    return {"status": "freshness_unknown"}
                payload.update(
                    {
                        "sample_state": sample.state,
                        "sample_size": sample.sample_size,
                        "assessed_count": sample.assessed_count,
                        "reported_total": sample.reported_total,
                        "reported_total_basis": sample.reported_total_basis,
                        "max_reviews": sample.bounds.max_reviews,
                        "max_pages": sample.bounds.max_pages,
                        "selection_method": sample.selection_method,
                        "requested_order": sample.requested_order,
                        "observed_order": sample.observed_order,
                        "partial": sample.partial,
                        "qualification": sample.qualification,
                        "omissions": sample.omissions,
                        "complete_history": False,
                    }
                )
            if self._size(payload) > self._max_content_bytes:
                return {"status": "oversized"}
            return payload
        return {"status": outcome.status}

    def list_sample_evidence(self, request: SamplePageInput) -> dict[str, object]:
        request = SamplePageInput.model_validate(request)
        if request.place_id not in self._place_ids or self._ephemeral is None:
            return {"status": "unavailable"}
        assessment = next(
            (
                a
                for a in self._ephemeral.review_assessments
                if a.place_id == request.place_id
                and a.assessment_id == request.assessment_id
            ),
            None,
        )
        if assessment is None:
            return {"status": "unavailable"}
        outcome = assessment.google
        if not isinstance(outcome, AvailableReviews):
            return {"status": outcome.status}
        if not self._approved(outcome.source_id, "google_review"):
            return {"status": "restricted"}
        ids = sorted(
            x for x in outcome.sample.evidence_ids if x > (request.after or "")
        )
        selected = ids[: request.limit]
        while selected:
            deferred = len(ids) - len(selected)
            payload: dict[str, object] = {
                "status": "ok",
                "source_id": outcome.source_id,
                "evidence": [
                    {
                        "evidence_id": item,
                        "assessed": item in outcome.sample.assessed_evidence_ids,
                    }
                    for item in selected
                ],
                "deferred_count": deferred,
                "next_after": selected[-1] if deferred else None,
            }
            if self._size(payload) <= self._max_content_bytes:
                return payload
            selected.pop()
        return (
            {
                "status": "ok",
                "source_id": outcome.source_id,
                "evidence": [],
                "deferred_count": 0,
                "next_after": None,
            }
            if not ids
            else {"status": "oversized"}
        )
