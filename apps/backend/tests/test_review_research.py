import pytest

from app.providers.google_reviews import (
    BranchCandidate,
    GoogleReview,
    MatchedBranch,
    ReviewLimits,
    ReviewSample,
)
from app.review_research import CandidateReview, collect_candidate_reviews, review_batch
from app.schemas.search import Coordinates, Place, ResolvedLocation


def place():
    return Place(
        place_id="one",
        kind="branch",
        name="Example",
        location=ResolvedLocation(
            coordinates=Coordinates(latitude=1.0, longitude=2.0),
            timezone="UTC",
            formatted_address="1 Main St",
        ),
    )


@pytest.mark.asyncio
async def test_no_confident_match_never_calls_provider():
    class Unused:
        async def fetch(self, *args, **kwargs):
            raise AssertionError("must not fetch")

    candidate = BranchCandidate(
        "Example", feature_id="0x1:0x2", full_address="2 Main St"
    )
    result = await collect_candidate_reviews(place(), (candidate,), Unused())
    assert result.availability == "no_match"
    assert result.match.reason == "conflicting_identity"


def test_review_handoff_preserves_public_text_and_marks_omissions():
    branch = MatchedBranch(
        "0x1:0x2",
        "https://www.google.com/maps?cid=2",
        "Example",
        "1 Main St",
        None,
        4.0,
        2,
        ("exact_name", "exact_full_address"),
    )
    texts = ('I did not like "ramen".', "私は高いと思いました。")
    reviews = tuple(
        GoogleReview(
            str(i),
            2.0,
            text,
            "present",
            "Reviewer",
            None,
            "https://www.google.com/maps?cid=2",
            "branch",
            None,
        )
        for i, text in enumerate(texts)
    )
    sample = ReviewSample(
        branch,
        "sample",
        "sample_limit",
        "relevant",
        ReviewLimits(),
        reviews,
        (),
        1,
        10,
        0,
        0,
        2,
    )
    result = CandidateReview("one", "available", "sample_limit", sample)
    full = review_batch(result, max_characters=100)
    assert [r["text"] for r in full.reviews] == list(texts)
    assert full.reviews[0]["author_name"] == "Reviewer"
    assert full.omitted_reviews == 0
    small = review_batch(result, max_characters=len(texts[0]))
    assert [r["text"] for r in small.reviews] == [texts[0]]
    assert small.omitted_reviews == 0
    assert small.next_index == 1
    following = review_batch(
        result, max_characters=len(texts[0]), start_index=small.next_index
    )
    assert [r["text"] for r in following.reviews] == [texts[1]]
    assert following.next_index is None
    oversized = review_batch(result, max_characters=1)
    assert oversized.omitted_reviews == 2
    assert oversized.next_index is None
