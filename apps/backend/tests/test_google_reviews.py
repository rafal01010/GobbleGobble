import asyncio
import gzip
import json
import logging
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict, Unpack

import httpx
import pytest
from pydantic import JsonValue, ValidationError

from app.core.calls import AttemptCalls, Bounds
from app.core.settings import (
    ConfigurationError,
    ReviewSettings,
    load_review_settings,
    review_diagnostics,
)
from app.providers.google_reviews import (
    BranchCandidate,
    BranchQuery,
    GoogleReviewClient,
    MatchedBranch,
    NoConfidentMatch,
    ReviewLimits,
    ReviewPreflightError,
    match_branch,
    minimize_for_model,
    normalize_review_date,
    supports_food_phrase,
)

_FEATURE = "0x1:0x2"
_SOURCE = "https://www.google.com/maps/place/Example/data=!1s0x1:0x2!"
_NAME = "Synthetic meal place"
_ADDRESS = "1 Synthetic Street, Synthetic City"
_FOOD = "The food had balanced flavors and excellent texture."
_AUTHOR = "Synthetic Reviewer"
_AUTHOR_URL = "https://www.google.com/maps/contrib/123/reviews"


@pytest.fixture(autouse=True)
def isolate_review_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GG_REVIEW_PROVIDER", raising=False)
    monkeypatch.delenv("GG_REVIEW_TIMEOUT_SECONDS", raising=False)


class CandidateChanges(TypedDict, total=False):
    name: str
    google_maps_url: str | None
    feature_id: str | None
    full_address: str | None
    website: str | None
    rating: float | None
    reported_total: int | None


def candidate(**changes: Unpack[CandidateChanges]) -> BranchCandidate:
    return replace(BranchCandidate(_NAME, _SOURCE, _FEATURE, _ADDRESS), **changes)


def branch(**changes: Unpack[CandidateChanges]) -> MatchedBranch:
    result = match_branch(BranchQuery(_NAME, _ADDRESS), (candidate(**changes),))
    assert isinstance(result, MatchedBranch)
    return result


def row(
    identifier: str = "synthetic-review-1", text: str | None = _FOOD
) -> list[JsonValue]:
    values: list[JsonValue] = [None] * 30
    values[1] = 4
    values[2] = [None, None, "1700000000000000"]
    values[3] = [_AUTHOR, "https://lh3.googleusercontent.com/avatar", _AUTHOR_URL]
    values[5] = identifier
    values[27] = text
    values[29] = ["Google", "https://www.google.com/maps/reviews/?reviewid=synthetic"]
    return values


def body(rows: JsonValue, cursor: str | None = None) -> bytes:
    node: list[JsonValue] = [None, None, rows, None, None, None, cursor]
    payload: list[JsonValue] = [None] * 11
    payload[10] = node
    return b")]}'\n" + json.dumps([None, payload]).encode()


def calls(requests: int = 4, seconds: float = 3.0) -> AttemptCalls:
    settings = ReviewSettings(
        mode="test", review_provider="google_reviews", review_timeout_seconds=1.0
    )
    return AttemptCalls(settings, Bounds(max_requests=requests, total_seconds=seconds))


def test_exact_branch_identity_and_corroboration() -> None:
    matched = branch(rating=4.5, reported_total=120)
    assert matched.feature_id == _FEATURE
    assert matched.source_url == _SOURCE
    assert matched.name == _NAME
    assert matched.evidence == ("exact_name", "exact_full_address")
    assert matched.rating == 4.5 and matched.reported_total == 120
    assert (
        matched.metadata_basis == "supplied_google_candidate_not_refreshed_by_reviews"
    )
    assert _SOURCE not in repr(matched)
    assert _NAME not in repr(candidate())


@pytest.mark.parametrize(
    "candidates,reason",
    [
        ((), "no_candidates"),
        ((BranchCandidate(_NAME, _SOURCE, _FEATURE),), "insufficient_corroboration"),
        ((candidate(feature_id="0x1:0x3"),), "conflicting_identity"),
        (
            (candidate(google_maps_url="https://www.google.com/maps?cid=3"),),
            "conflicting_identity",
        ),
        (
            (candidate(google_maps_url="https://google.com.attacker.invalid/maps"),),
            "conflicting_identity",
        ),
        ((candidate(full_address="Different branch"),), "conflicting_identity"),
        ((candidate(), candidate(reported_total=2)), "conflicting_identity"),
        (
            (
                candidate(),
                candidate(
                    feature_id="0x1:0x3",
                    google_maps_url="https://www.google.com/maps?cid=3",
                ),
            ),
            "ambiguous",
        ),
    ],
)
def test_unconfident_or_conflicting_candidates_are_explicit(
    candidates: tuple[BranchCandidate, ...], reason: str
) -> None:
    result = match_branch(BranchQuery(_NAME, _ADDRESS), candidates)
    assert isinstance(result, NoConfidentMatch)
    assert result.reason == reason


def test_website_alone_is_insufficient_for_chain_branch_matching() -> None:
    item = candidate(full_address=None, website="https://example.invalid/branch")
    result = match_branch(BranchQuery(_NAME, website=item.website), (item,))
    assert result == NoConfidentMatch("insufficient_corroboration")
    matched = match_branch(
        BranchQuery(_NAME, website=item.website, feature_id=_FEATURE), (item,)
    )
    assert isinstance(matched, MatchedBranch)
    assert matched.evidence == ("exact_name", "exact_website", "target_feature_id")


def test_cid_without_full_feature_identity_does_not_invent_one() -> None:
    result = match_branch(
        BranchQuery(_NAME, _ADDRESS),
        (
            candidate(
                feature_id=None, google_maps_url="https://www.google.com/maps?cid=2"
            ),
        ),
    )
    assert result == NoConfidentMatch("insufficient_corroboration")


def test_review_only_configuration_ignores_unrelated_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GG_REVIEW_PROVIDER", raising=False)
    monkeypatch.delenv("GG_REVIEW_TIMEOUT_SECONDS", raising=False)
    path = tmp_path / "reviews.env"
    path.write_text(
        "GG_REVIEW_PROVIDER=google_reviews\nGG_REVIEW_TIMEOUT_SECONDS=2\nGG_LOCATION_PROVIDER=unrelated-invalid\nOPENAI_API_KEY=unneeded-secret\n"
    )
    settings = load_review_settings(mode="test", env_file=path)
    assert settings.review_provider == "google_reviews"
    assert settings.review_timeout_seconds == 2.0
    assert asdict(review_diagnostics(settings)) == {
        "mode": "test",
        "review_provider": "google_reviews",
        "review_timeout_seconds": 2.0,
    }
    assert "unneeded-secret" not in repr(settings)
    path.write_text("GG_REVIEW_TIMEOUT_SECONDS=private-invalid-value\n")
    with pytest.raises(ConfigurationError) as caught:
        load_review_settings(mode="test", env_file=path)
    assert "private-invalid-value" not in str(caught.value)
    assert caught.value.issues[0].variable == "GG_REVIEW_TIMEOUT_SECONDS"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings,transport,code",
    [
        (ReviewSettings(mode="test"), True, "configure_GG_REVIEW_PROVIDER"),
        (
            ReviewSettings(mode="test", review_provider="google_reviews"),
            True,
            "configure_GG_REVIEW_TIMEOUT_SECONDS",
        ),
        (
            ReviewSettings(
                mode="test",
                review_provider="google_reviews",
                review_timeout_seconds=1.0,
            ),
            False,
            "test_transport_required",
        ),
        (
            ReviewSettings(
                mode="diagnostic",
                review_provider="google_reviews",
                review_timeout_seconds=1.0,
            ),
            True,
            "live_mode_required",
        ),
        (
            ReviewSettings(
                mode="live",
                review_provider="google_reviews",
                review_timeout_seconds=1.0,
            ),
            True,
            "test_transport_forbidden",
        ),
    ],
)
async def test_review_preflight_before_admission(
    settings: ReviewSettings, transport: bool, code: str
) -> None:
    attempt = AttemptCalls(settings, Bounds(max_requests=1, total_seconds=1.0))
    mock = httpx.MockTransport(lambda request: pytest.fail("preflight transmitted"))
    client = GoogleReviewClient(attempt, test_transport=mock if transport else None)
    with pytest.raises(ReviewPreflightError) as caught:
        await client.fetch(branch())
    assert caught.value.code == code
    assert attempt.receipts == ()


@pytest.mark.asyncio
async def test_attributed_sample_and_fixed_endpoint_accounting() -> None:
    requested: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requested.append(request)
        assert (
            str(request.url.copy_with(query=None))
            == "https://www.google.com/httpservice/web/PrivateLocalSearchUiDataService/GetLocalBoqProxy"
        )
        payload = json.loads(request.url.params["reqpld"])
        assert payload[1][9][1] == 2
        assert payload[1][9][11] == [_FEATURE]
        assert payload[1][9][9] == 10
        assert request.headers.get("cookie") is None
        assert request.headers.get("accept-encoding") == "identity"
        return httpx.Response(200, content=body([row()]))

    attempt = calls()
    sample = await GoogleReviewClient(
        attempt, test_transport=httpx.MockTransport(handle)
    ).fetch(branch(rating=4.2, reported_total=100), sort="newest")
    assert sample.status == "sample" and sample.termination == "source_exhausted"
    assert sample.sort == "newest" and not sample.is_complete_history
    assert sample.received_row_count == 1 and sample.pages_received == 1
    review = sample.reviews[0]
    assert review.source_name == "Google Maps"
    assert review.text == _FOOD and review.rating == 4.0
    assert review.author_name == _AUTHOR and review.author_url == _AUTHOR_URL
    assert (
        review.source_url == "https://www.google.com/maps/reviews/?reviewid=synthetic"
    )
    assert review.link_basis == "review"
    assert review.published is not None
    assert review.published.normalized_utc == datetime(
        2023, 11, 14, 22, 13, 20, tzinfo=UTC
    )
    assert review.published.basis == "inferred_epoch_microseconds"
    assert len(requested) == len(attempt.receipts) == 1
    assert sample.receipts[0].provider == "google_reviews"
    assert sample.receipts[0].capability == "reviews"
    assert sample.receipts[0].input_sources == ("ordinary_google",)
    assert (
        sample.receipts[0].usage is not None and sample.receipts[0].usage.requests == 1
    )
    for text in (_FOOD, _AUTHOR, _AUTHOR_URL, _SOURCE, _NAME, "synthetic-review-1"):
        assert text not in repr(sample)
        assert text not in json.dumps(sample.receipts[0].safe_record())
    assert "avatar" not in repr(review)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows,total,status,termination",
    [
        ([], 0, "zero_reviews", "source_exhausted"),
        ([], None, "empty_sample", "empty_sample"),
        ([], 20, "empty_sample", "empty_sample"),
        (None, 0, "omitted_sample", "omitted_sample"),
    ],
)
async def test_zero_empty_omitted_samples_are_distinct(
    rows: JsonValue, total: int | None, status: str, termination: str
) -> None:
    sample = await GoogleReviewClient(
        calls(),
        test_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=body(rows))
        ),
    ).fetch(branch(reported_total=total))
    assert sample.status == status and sample.termination == termination
    assert sample.reviews == ()


@pytest.mark.asyncio
async def test_missing_optional_fields_never_fabricate_values() -> None:
    raw = row(text=None)
    raw[1] = raw[2] = raw[3] = None
    raw[29] = ["Google", _SOURCE]
    sample = await GoogleReviewClient(
        calls(),
        test_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=body([raw]))
        ),
    ).fetch(branch())
    review = sample.reviews[0]
    assert review.text is None and review.text_state == "omitted"
    assert (
        review.rating is None
        and review.author_name is None
        and review.author_url is None
    )
    assert review.published is None
    assert review.source_url == _SOURCE and review.link_basis == "branch"


@pytest.mark.parametrize(
    "value,basis,epoch",
    [
        ("1700000000", "inferred_epoch_seconds", 1700000000),
        ("1700000000123", "inferred_epoch_milliseconds", 1700000000.123),
        ("1700000000000000", "inferred_epoch_microseconds", 1700000000),
        ("1700000000000000000", "inferred_epoch_nanoseconds", 1700000000),
        ("3 weeks ago", "uninterpreted", None),
        ("9999999999999999999", "uninterpreted", None),
    ],
)
def test_decimal_dates_preserve_inference_basis(
    value: str, basis: str, epoch: float | None
) -> None:
    result = normalize_review_date(value)
    assert result is not None and result.raw == value and result.basis == basis
    assert (
        result.normalized_utc.timestamp() if result.normalized_utc else None
    ) == epoch


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed",
    [
        b"private unparseable source body",
        b"{}",
        b"[null, []]",
        body("unexpected rows"),
        body([[None]]),
    ],
)
async def test_malformed_pages_cannot_be_empty_success(malformed: bytes) -> None:
    sample = await GoogleReviewClient(
        calls(),
        test_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=malformed)
        ),
    ).fetch(branch())
    assert sample.status == "unavailable"
    assert sample.termination in {"malformed_response", "invalid_rows"}
    if sample.termination == "malformed_response":
        assert sample.receipts[0].outcome == "failed"
        assert sample.receipts[0].diagnostic == "invalid_response"


@pytest.mark.asyncio
async def test_partial_failure_keeps_first_page_and_failed_receipt() -> None:
    count = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(
                200,
                content=body([row()], "next-private-cursor"),
                headers={"set-cookie": "private=blocked"},
            )
        assert request.headers.get("cookie") is None
        assert (
            json.loads(request.url.params["reqpld"])[1][9][19] == "next-private-cursor"
        )
        return httpx.Response(403, text="private error body")

    attempt = calls()
    sample = await GoogleReviewClient(
        attempt, test_transport=httpx.MockTransport(handle)
    ).fetch(branch())
    assert sample.status == "partial" and sample.termination == "access_failure"
    assert len(sample.reviews) == 1 and count == 2
    assert [item.outcome for item in attempt.receipts] == ["success", "failed"]
    assert attempt.receipts[1].diagnostic == "access_failure"
    assert "private" not in repr(sample)


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat_cursor", [False, True])
async def test_duplicate_and_repeated_cursor_failures_are_explicit(
    repeat_cursor: bool,
) -> None:
    count = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(
            200,
            content=body(
                [row()], "same-cursor" if count == 1 or repeat_cursor else "new-cursor"
            ),
        )

    sample = await GoogleReviewClient(
        calls(), test_transport=httpx.MockTransport(handle)
    ).fetch(branch())
    assert sample.status == "partial" and sample.duplicate_count == 1
    assert len(sample.reviews) == 1 and sample.received_row_count == 2
    assert sample.termination == (
        "repeated_cursor" if repeat_cursor else "duplicate_reviews"
    )
    assert count == 2


@pytest.mark.asyncio
async def test_page_and_sample_limits_disclose_overfetch() -> None:
    requested = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal requested
        requested += 1
        return httpx.Response(
            200, content=body([row(str(index)) for index in range(10)], "cursor")
        )

    client = GoogleReviewClient(calls(), test_transport=httpx.MockTransport(handle))
    sample = await client.fetch(
        branch(), limits=ReviewLimits(max_reviews=3, max_pages=1)
    )
    assert sample.status == "sample" and sample.termination == "sample_limit"
    assert (
        len(sample.reviews) == 3 and sample.received_row_count == 10 and requested == 1
    )
    limited = await client.fetch(
        branch(), limits=ReviewLimits(max_reviews=20, max_pages=1)
    )
    assert limited.status == "sample" and limited.termination == "page_limit"


@pytest.mark.asyncio
async def test_shared_request_budget_counts_every_page_admission() -> None:
    sent = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        return httpx.Response(200, content=body([row()], "cursor"))

    attempt = calls(requests=1)
    sample = await GoogleReviewClient(
        attempt, test_transport=httpx.MockTransport(handle)
    ).fetch(branch())
    assert sent == 1 and len(attempt.receipts) == 1
    assert sample.status == "partial" and sample.termination == "request_limit"
    assert len(sample.receipts) == 2 and not sample.receipts[1].admitted


@pytest.mark.asyncio
@pytest.mark.parametrize("compressed", [False, True])
async def test_identity_body_limit_and_compressed_response_rejection(
    compressed: bool,
) -> None:
    raw = body([row(text="private review " * 3000)])
    content = gzip.compress(raw) if compressed else raw
    headers = {"content-encoding": "gzip"} if compressed else {}
    sample = await GoogleReviewClient(
        calls(),
        test_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=content, headers=headers)
        ),
    ).fetch(branch(), limits=ReviewLimits(max_response_bytes=1024))
    assert sample.status == "unavailable"
    assert sample.termination == (
        "unsupported_encoding" if compressed else "byte_limit"
    )
    assert sample.receipts[0].outcome == "failed"
    assert sample.receipts[0].diagnostic == (
        "invalid_response" if compressed else "response_limit"
    )
    assert sample.response_bytes == 0 if compressed else sample.response_bytes <= 1024
    assert "private review" not in repr(sample)


@pytest.mark.asyncio
async def test_total_byte_limit_is_shared_across_pages() -> None:
    raw = body([row()], "cursor")
    sample = await GoogleReviewClient(
        calls(),
        test_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=raw)
        ),
    ).fetch(branch(), limits=ReviewLimits(max_total_bytes=len(raw)))
    assert sample.status == "partial" and sample.termination == "byte_limit"
    assert len(sample.receipts) == 1 and sample.response_bytes == len(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 403, 429, 500])
async def test_access_failures_never_retry_or_follow_redirects(status: int) -> None:
    sent = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        return httpx.Response(
            status,
            headers={"location": "https://attacker.invalid/"},
            text="private error",
        )

    sample = await GoogleReviewClient(
        calls(), test_transport=httpx.MockTransport(handle)
    ).fetch(branch())
    assert sample.status == "unavailable" and sample.termination == "access_failure"
    assert sent == 1 and len(sample.receipts) == 1


@pytest.mark.asyncio
async def test_operation_timeout_and_cancellation_are_accounted() -> None:
    entered = asyncio.Event()

    async def handle(request: httpx.Request) -> httpx.Response:
        entered.set()
        await asyncio.sleep(1)
        return httpx.Response(200, content=body([row()]))

    attempt = calls()
    client = GoogleReviewClient(attempt, test_transport=httpx.MockTransport(handle))
    sample = await client.fetch(branch(), limits=ReviewLimits(total_seconds=0.01))
    assert sample.status == "unavailable" and sample.termination == "time_limit"
    assert sample.receipts[0].admitted and sample.receipts[0].outcome == "failed"
    entered.clear()
    task = asyncio.create_task(client.fetch(branch()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert attempt.receipts[-1].outcome == "cancelled"


@pytest.mark.asyncio
async def test_content_logs_are_filtered(caplog: pytest.LogCaptureFixture) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        logging.getLogger("httpx").warning(_FOOD)
        return httpx.Response(200, content=body([row()]))

    with caplog.at_level(logging.DEBUG):
        sample = await GoogleReviewClient(
            calls(), test_transport=httpx.MockTransport(handle)
        ).fetch(branch())
    assert sample.status == "sample"
    assert _FOOD not in caplog.text and _SOURCE not in caplog.text


@pytest.mark.asyncio
async def test_model_minimization_has_explicit_lineage_limits_and_no_personal_fields() -> (
    None
):
    raw_rows: list[JsonValue] = [
        row(),
        row("personal", "My wife enjoyed the tasting menu."),
        row("sensitive", "The food triggered anaphylaxis and required treatment."),
        row("named", "The dish from Alice was delicious."),
        row("contact", "The food is good at food@example.invalid."),
        row("number", "The food costs 100 pesos."),
        row("second", "The bread had a crisp crust and soft center."),
    ]
    sample = await GoogleReviewClient(
        calls(),
        test_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=body(raw_rows))
        ),
    ).fetch(branch())
    minimal = minimize_for_model(sample)
    assert [item.text for item in minimal.passages] == [
        "The bread had a crisp crust and soft center.",
    ]
    payload = minimal.model_input()
    assert payload["source_class"] == "minimized_google_reviews"
    assert payload["requested_sort"] == "relevant"
    assert payload["sampled_reviews"] == 7 and payload["max_reviews"] == 20
    assert minimal.selected_words <= 60 and len(minimal.passages) <= 3
    assert len(minimal.limitations) == 4
    for unsafe in (
        _AUTHOR,
        _AUTHOR_URL,
        _SOURCE,
        _NAME,
        "synthetic-review-1",
        "wife",
        "anaphylaxis",
        "Alice",
        "@",
        "100",
    ):
        assert unsafe not in json.dumps(payload)
    assert _FOOD not in repr(minimal)
    duplicate = replace(sample, reviews=sample.reviews * 2)
    assert len(minimize_for_model(duplicate).passages) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source,expected,foods",
    [
        (
            "The staff were attentive, the chicken was fresh and delicious.",
            "the chicken was fresh and delicious.",
            ("chicken",),
        ),
        (
            "The staff were attentive; the bread had a crisp crust and soft center.",
            "the bread had a crisp crust and soft center.",
            ("bread",),
        ),
        (
            "Highlights include: the tasting menu was excellent and beautifully prepared.",
            "the tasting menu was excellent and beautifully prepared.",
            ("tasting menu",),
        ),
        (
            "The chicken was fresh and the rice was delicious.",
            "The chicken was fresh and the rice was delicious.",
            ("chicken", "rice"),
        ),
        (
            "The beautifully prepared fresh fish was excellent.",
            "The beautifully prepared fresh fish was excellent.",
            ("fish",),
        ),
        (
            "The chicken was delicious and rice was cooked.",
            "The chicken was delicious and rice was cooked.",
            ("chicken",),
        ),
    ],
)
async def test_minimized_passages_preserve_exact_food_spans_and_source_attribution(
    source: str, expected: str, foods: tuple[str, ...]
) -> None:
    sample = await GoogleReviewClient(
        calls(),
        test_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=body([row(text=source)]))
        ),
    ).fetch(branch())
    minimal = minimize_for_model(sample)
    assert [item.text for item in minimal.passages] == [expected]
    passage = minimal.passages[0]
    assert passage.foods == foods
    assert passage.source_review_index == 0
    assert source[passage.source_start : passage.source_end] == expected
    assert sample.reviews[passage.source_review_index].author_name == _AUTHOR
    assert minimal.model_input()["passages"] == [
        {"label": "p0", "text": expected, "foods": list(foods)}
    ]
    assert "source_start" not in json.dumps(minimal.model_input())
    assert "source_review_index" not in json.dumps(minimal.model_input())
    assert all(supports_food_phrase(passage, food) for food in foods)
    assert not supports_food_phrase(passage, "The chicken")
    assert not supports_food_phrase(passage, "food")
    assert expected not in repr(passage)
    assert repr(foods) not in repr(passage)


@pytest.mark.asyncio
async def test_minimization_scans_later_reviews_and_later_sentences_within_twenty() -> (
    None
):
    texts = [_FOOD] * 10 + [
        "The chicken was delicious and tender. The bread was fresh and crispy.",
        "The rice was delicious and perfectly cooked.",
    ]
    sample = await GoogleReviewClient(
        calls(),
        test_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=body(
                    [row(str(index), text) for index, text in enumerate(texts)]
                ),
            )
        ),
    ).fetch(branch())
    minimal = minimize_for_model(sample)
    assert [item.text for item in minimal.passages] == [
        "The chicken was delicious and tender.",
        "The bread was fresh and crispy.",
        "The rice was delicious and perfectly cooked.",
    ]
    assert [item.source_review_index for item in minimal.passages] == [10, 10, 11]
    assert minimal.selected_words == 19
    hidden = replace(sample.reviews[0], text="The coffee was delicious and rich.")
    beyond_bound = replace(sample, reviews=sample.reviews[:1] * 20 + (hidden,))
    assert minimize_for_model(beyond_bound).model_input()["passages"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        _FOOD,
        "The dish was delicious and beautifully prepared.",
        "The sauce was delicious and beautifully prepared.",
        "The meat was delicious and beautifully prepared.",
        "The menu was excellent and beautifully prepared.",
        "The chicken was bland and dry.",
        "The chicken was cooked and the presentation was excellent.",
        "If you like bland food, the chicken was fresh and delicious.",
        "The service was not good, the chicken was fresh and delicious.",
        "The service wasn't good, the chicken was fresh and delicious.",
        "I don't agree, the chicken was fresh and delicious.",
        "The chicken was fresh and delicious, but the rice was bland.",
        "Although the chicken was fresh and delicious, the kitchen was unsafe.",
        "The staff said the chicken was fresh and delicious.",
        "The staff said, the chicken was fresh and delicious.",
        'The staff said "the chicken was fresh and delicious."',
        "'The chicken was fresh and delicious.'",
        "Apparently, the chicken was fresh and delicious.",
        "Perhaps, the chicken was fresh and delicious.",
        "The chicken might be fresh and delicious.",
        "Unlike the fish, the chicken was fresh and delicious.",
        "Compared with rice, the chicken was fresh and delicious.",
        "Is the chicken fresh and delicious?",
        "No es bueno, the chicken was fresh and delicious.",
        "美味しくない, the chicken was fresh and delicious.",
        "My wife was recovering from surgery, the chicken was fresh and delicious.",
        "After chemotherapy, the chicken was fresh and delicious.",
        "Contact alice@example.invalid, the chicken was fresh and delicious.",
        "Call 123456789, the chicken was fresh and delicious.",
        "The chicken xyz was fresh and delicious.",
        "The chicken was fresh xyz and delicious.",
    ],
)
async def test_minimization_rejects_generic_negative_unsafe_or_unknown_context(
    source: str,
) -> None:
    sample = await GoogleReviewClient(
        calls(),
        test_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=body([row(text=source)]))
        ),
    ).fetch(branch())
    minimal = minimize_for_model(sample)
    assert minimal.model_input()["passages"] == []
    assert minimal.selected_words == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "opening,closing,separator",
    [
        ('"', '"', " "),
        ('"', '"', "\n"),
        ("\u201c", "\u201d", " "),
        ("\u201c", "\u201d", "\n"),
        ("'", "'", " "),
        ("'", "'", "\n"),
        ("\u2018", "\u2019", " "),
        ("\u2018", "\u2019", "\n"),
    ],
)
async def test_minimization_excludes_quoted_middle_sentences_and_keeps_unquoted_evidence(
    opening: str, closing: str, separator: str
) -> None:
    source = (
        f"The advertisement claimed {opening}The room was attractive.{separator}"
        f"The chicken was fresh and delicious.{separator}"
        f"The service was excellent.{closing}{separator}"
        "The bread was fresh and crispy."
    )
    sample = await GoogleReviewClient(
        calls(),
        test_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=body([row(text=source)]))
        ),
    ).fetch(branch())
    minimal = minimize_for_model(sample)
    assert [item.text for item in minimal.passages] == [
        "The bread was fresh and crispy."
    ]
    passage = minimal.passages[0]
    assert passage.foods == ("bread",)
    assert source[passage.source_start : passage.source_end] == passage.text


@pytest.mark.asyncio
@pytest.mark.parametrize("opening", ['"', "\u201c", "'", "\u2018"])
async def test_minimization_excludes_unclosed_quoted_speech_through_end_of_review(
    opening: str,
) -> None:
    source = (
        "The bread was fresh and crispy.\n"
        f"The advertisement claimed {opening}The room was attractive.\n"
        "The chicken was fresh and delicious.\n"
        "The rice was delicious and tender."
    )
    sample = await GoogleReviewClient(
        calls(),
        test_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=body([row(text=source)]))
        ),
    ).fetch(branch())
    minimal = minimize_for_model(sample)
    assert [item.text for item in minimal.passages] == [
        "The bread was fresh and crispy."
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("apostrophe", ["'", "\u2019"])
async def test_minimization_keeps_unquoted_food_after_a_separate_contraction(
    apostrophe: str,
) -> None:
    source = (
        f"The service wasn{apostrophe}t good.\nThe chicken was fresh and delicious."
    )
    sample = await GoogleReviewClient(
        calls(),
        test_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=body([row(text=source)]))
        ),
    ).fetch(branch())
    minimal = minimize_for_model(sample)
    assert [item.text for item in minimal.passages] == [
        "The chicken was fresh and delicious."
    ]


@pytest.mark.asyncio
async def test_minimization_keeps_passage_word_character_and_deduplication_bounds() -> (
    None
):
    safe = "The chicken was delicious and " + "very " * 14 + "tender."
    too_long = "The rice was delicious and " + "very " * 15 + "tender."
    repeated = ". ".join([safe.rstrip(".")] * 4) + "."
    sample = await GoogleReviewClient(
        calls(),
        test_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=body(
                    [
                        row("long", too_long),
                        row("repeated", repeated),
                        row("bread", "The bread was fresh and crispy."),
                        row("rice", "The rice was fresh and delicious."),
                        row("fish", "The fish was fresh and delicious."),
                    ]
                ),
            )
        ),
    ).fetch(branch())
    minimal = minimize_for_model(sample)
    assert [item.text for item in minimal.passages] == [
        safe,
        "The bread was fresh and crispy.",
        "The rice was fresh and delicious.",
    ]
    assert minimal.selected_words == 32
    assert all(5 <= len(item.text.split()) <= 20 for item in minimal.passages)
    assert all(len(item.text) <= 220 for item in minimal.passages)


@pytest.mark.parametrize(
    "change",
    [
        {"max_pages": True},
        {"max_pages": 6},
        {"max_reviews": 0},
        {"max_response_bytes": 1048577},
        {"max_total_bytes": 5242881},
        {"total_seconds": float("nan")},
        {"total_seconds": 61.0},
    ],
)
def test_review_bounds_are_closed_finite_and_small(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ReviewLimits.model_validate(change)


@pytest.mark.asyncio
async def test_socket_timeout_is_explicit_and_never_retried() -> None:
    sent = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        raise httpx.ReadTimeout("private socket error", request=request)

    sample = await GoogleReviewClient(
        calls(), test_transport=httpx.MockTransport(handle)
    ).fetch(branch())
    assert sample.status == "unavailable" and sample.termination == "call_timeout"
    assert sample.receipts[0].diagnostic == "operation_timeout"
    assert sample.receipts[0].outcome == "failed" and sent == 1
    assert "private socket error" not in repr(sample)
