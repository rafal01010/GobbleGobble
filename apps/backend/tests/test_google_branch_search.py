import json
from dataclasses import replace

import httpx
import pytest

from app.core import source_policy
from app.core.calls import AttemptCalls, Bounds
from app.core.settings import ReviewSettings
from app.providers.google_reviews import ReviewLimits
from app.review_research import PrimaryReviewClient
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


def record(name="Example", address="1 Main St", feature="0x1:0x2"):
    row = [None] * 51
    row[2] = [address]
    row[4] = [None] * 9
    row[4][7:9] = [4.5, 1]
    row[10], row[11] = feature, name
    return row


def search_body(rows):
    return b")]}'\n" + json.dumps([rows]).encode()


def review_body():
    row = [None] * 30
    row[1] = 2
    row[3] = ["Sam", None, None]
    row[5] = "review-one"
    row[27] = "I did not like it. 高い。"
    row[29] = ["Google", "https://www.google.com/maps/reviews/?reviewid=one"]
    rows = [None] * 11
    rows[10] = [None, None, [row], None, None, None, None]
    return b")]}'\n" + json.dumps([None, rows]).encode()


def client(handler, count=4):
    calls = AttemptCalls(
        ReviewSettings(
            mode="test", review_provider="google_reviews", review_timeout_seconds=1.0
        ),
        Bounds(max_requests=count, total_seconds=3.0),
    )
    return PrimaryReviewClient(
        calls, test_transport=httpx.MockTransport(handler)
    ), calls


@pytest.mark.asyncio
async def test_search_and_fetch_preserve_exact_branch_and_review():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/search":
            assert request.url.params["tbm"] == "map"
            assert request.url.params["q"] == "Example 1 Main St"
            return httpx.Response(200, content=search_body([record()]))
        return httpx.Response(200, content=review_body())

    provider, calls = client(handler)
    result = await provider.collect(
        place(), limits=ReviewLimits(max_pages=1, max_reviews=1)
    )
    assert result.availability == "available"
    assert result.sample.branch.feature_id == "0x1:0x2"
    assert result.sample.branch.reported_total == 1
    assert result.sample.reviews[0].text == "I did not like it. 高い。"
    assert result.sample.reviews[0].author_name == "Sam"
    assert paths == [
        "/search",
        "/httpservice/web/PrivateLocalSearchUiDataService/GetLocalBoqProxy",
    ]
    assert len(calls.receipts) == 2


@pytest.mark.asyncio
async def test_conflicting_branches_do_not_fetch_reviews():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(
            200, content=search_body([record(), record(feature="0x3:0x4")])
        )

    provider, calls = client(handler)
    result = await provider.collect(place())
    assert result.availability == "no_match"
    assert result.reason == "ambiguous"
    assert paths == ["/search"]
    assert len(calls.receipts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(429), "quota"),
        (httpx.Response(200, content=b"bad"), "malformed_response"),
        (
            httpx.Response(200, headers={"content-encoding": "gzip"}, content=b"x"),
            "unsupported_encoding",
        ),
    ],
)
async def test_lookup_errors_are_not_no_match(response, reason):
    provider, _ = client(lambda request: response)
    result = await provider.collect(place())
    assert result.availability == "provider_failure"
    assert result.reason == reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rows", "total", "expected"),
    [([], 0, "zero_reviews"), (None, 0, "omitted_sample"), ([], 1, "empty_sample")],
)
async def test_review_availability_preserves_empty_states(rows, total, expected):
    candidate = record()
    candidate[4][8] = total
    payload = [None] * 11
    payload[10] = [None, None, rows, None, None, None, None]

    def handler(request):
        return httpx.Response(
            200,
            content=search_body([candidate])
            if request.url.path == "/search"
            else b")]}'\n" + json.dumps([None, payload]).encode(),
        )

    provider, _ = client(handler)
    result = await provider.collect(place())
    assert result.availability == expected
    assert result.sample.branch.reported_total == total


@pytest.mark.asyncio
async def test_policy_restriction_stops_before_google_lookup(monkeypatch):
    monkeypatch.setattr(
        source_policy,
        "POLICIES",
        dict(source_policy.POLICIES)
        | {
            "public_google_reviews": replace(
                source_policy.POLICIES["public_google_reviews"], model=False
            )
        },
    )
    provider, calls = client(
        lambda request: pytest.fail("restricted content must not be retrieved")
    )
    result = await provider.collect(place())
    assert result.availability == "restricted"
    assert calls.receipts == ()
