from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

import httpx

from app.core.calls import AttemptCalls, Receipt, Stopped
from app.core.source_policy import SourceUseDenied, require_source_use
from app.providers.google_branch_search import BranchSearchFailed, GoogleBranchSearch
from app.providers.google_reviews import (
    BranchCandidate,
    BranchQuery,
    GoogleReviewClient,
    NoConfidentMatch,
    ReviewLimits,
    ReviewPreflightError,
    ReviewSample,
    match_branch,
)
from app.schemas.search import Place

type ReviewAvailability = Literal[
    "available",
    "zero_reviews",
    "empty_sample",
    "omitted_sample",
    "no_match",
    "restricted",
    "missing_access",
    "timeout",
    "provider_failure",
    "budget_exhausted",
]


@dataclass(frozen=True)
class CandidateReview:
    place_id: str = field(repr=False)
    availability: ReviewAvailability
    reason: str
    sample: ReviewSample | None = field(default=None, repr=False)
    match: NoConfidentMatch | None = field(default=None, repr=False)
    lookup_receipt: Receipt | None = field(default=None, repr=False)


async def collect_candidate_reviews(
    place: Place,
    google_candidates: tuple[BranchCandidate, ...],
    client: GoogleReviewClient,
    *,
    limits: ReviewLimits | None = None,
) -> CandidateReview:
    """Collect a sample from supplied Google branch candidates, without discovery."""
    if not place.name:
        return CandidateReview(place.place_id, "no_match", "missing_place_name")
    matched = match_branch(
        BranchQuery(name=place.name, full_address=place.location.formatted_address),
        google_candidates,
    )
    if isinstance(matched, NoConfidentMatch):
        return CandidateReview(
            place.place_id, "no_match", matched.reason, match=matched
        )
    try:
        require_source_use(("public_google_reviews",), "model")
    except SourceUseDenied:
        return CandidateReview(place.place_id, "restricted", "source_policy")
    try:
        sample = await client.fetch(matched, limits=limits)
    except ReviewPreflightError as error:
        return CandidateReview(place.place_id, "missing_access", error.code)
    if sample.status in {"sample", "partial"} and any(r.text for r in sample.reviews):
        return CandidateReview(place.place_id, "available", sample.termination, sample)
    if sample.status in {"zero_reviews", "empty_sample", "omitted_sample"}:
        return CandidateReview(
            place.place_id, sample.status, sample.termination, sample
        )
    if sample.status in {"sample", "partial"}:
        status: ReviewAvailability = (
            "omitted_sample"
            if any(r.text_state == "omitted" for r in sample.reviews)
            else "empty_sample"
        )
        return CandidateReview(place.place_id, status, sample.termination, sample)
    status = (
        "restricted"
        if sample.termination == "source_blocked"
        else "timeout"
        if sample.termination in {"call_timeout", "attempt_timeout", "time_limit"}
        else "budget_exhausted"
        if sample.termination in {"request_limit", "page_limit", "byte_limit"}
        else "provider_failure"
    )
    return CandidateReview(place.place_id, status, sample.termination, sample)


class PrimaryReviewClient:
    def __init__(
        self, calls: AttemptCalls, *, test_transport: httpx.MockTransport | None = None
    ) -> None:
        self._lookup = GoogleBranchSearch(calls, test_transport=test_transport)
        self._reviews = GoogleReviewClient(calls, test_transport=test_transport)

    async def collect(
        self, place: Place, *, limits: ReviewLimits | None = None
    ) -> CandidateReview:
        if not place.name or not place.location.formatted_address:
            return CandidateReview(
                place.place_id, "no_match", "missing_name_or_address"
            )
        if len(place.name) + len(place.location.formatted_address) + 1 > 1000:
            return CandidateReview(place.place_id, "no_match", "query_too_long")
        try:
            require_source_use(("public_google_reviews",), "model")
        except SourceUseDenied:
            return CandidateReview(place.place_id, "restricted", "source_policy")
        try:
            lookup = await self._lookup.search(place)
        except ReviewPreflightError as error:
            return CandidateReview(place.place_id, "missing_access", error.code)
        if isinstance(lookup, BranchSearchFailed):
            return CandidateReview(
                place.place_id,
                "provider_failure",
                lookup.reason,
                lookup_receipt=lookup.receipt,
            )
        if isinstance(lookup, Stopped):
            outcome = lookup.receipt.outcome
            status: ReviewAvailability = (
                "timeout"
                if outcome in {"call_timeout", "attempt_timeout"}
                else "budget_exhausted"
                if outcome == "request_limit"
                else "restricted"
                if outcome == "source_blocked"
                else "provider_failure"
            )
            return CandidateReview(
                place.place_id, status, outcome, lookup_receipt=lookup.receipt
            )
        if lookup.value.status != "candidates":
            return CandidateReview(
                place.place_id,
                "provider_failure",
                lookup.value.status,
                lookup_receipt=lookup.receipt,
            )
        result = await collect_candidate_reviews(
            place, lookup.value.candidates, self._reviews, limits=limits
        )
        return replace(result, lookup_receipt=lookup.receipt)


@dataclass(frozen=True)
class ReviewBatch:
    reviews: tuple[dict[str, object], ...] = field(repr=False)
    omitted_reviews: int
    next_index: int | None
    source_class: Literal["public_google_reviews"] = "public_google_reviews"


def review_batch(
    result: CandidateReview, *, max_characters: int, start_index: int = 0
) -> ReviewBatch:
    """Return complete review bodies up to a caller's explicit character allowance."""
    if max_characters <= 0:
        raise ValueError("max_characters must be positive")
    if start_index < 0:
        raise ValueError("start_index must be nonnegative")
    require_source_use(("public_google_reviews",), "model")
    if result.availability != "available" or result.sample is None:
        return ReviewBatch((), 0, None)
    rows: list[dict[str, object]] = []
    omitted = 0
    remaining = max_characters
    for index, review in enumerate(result.sample.reviews[start_index:], start_index):
        if not review.text:
            continue
        if len(review.text) > remaining:
            if len(review.text) <= max_characters:
                return ReviewBatch(tuple(rows), omitted, index)
            omitted += 1
            continue
        remaining -= len(review.text)
        rows.append(
            {
                "text": review.text,
                "rating": review.rating,
                "author_name": review.author_name,
                "author_url": review.author_url,
                "source_url": review.source_url,
                "link_basis": review.link_basis,
                "published": review.published.raw if review.published else None,
            }
        )
    return ReviewBatch(tuple(rows), omitted, None)
