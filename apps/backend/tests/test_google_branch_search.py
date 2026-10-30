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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query_address", "candidate_address", "source_point", "google_point", "available"),
    [
        (
            "2 Chome-6-2 Kyomachi, Kokurakita Ward, Kitakyushu, Fukuoka 802-0002, Japan",
            "Japan, 〒802-0002 Fukuoka, Kitakyushu, Kokurakita Ward, Kyomachi, 2 Chome−6−2",
            (33.8857211, 130.8804594),
            (33.8858092, 130.8804664),
            True,
        ),
        (
            "2316 Chino Roces Avenue Extension, Makati, 1231 Metro Manila, Philippines",
            "2316, The Alley at Karrivin, Karrivin Plaza, 1231 Chino Roces Ave Ext, Makati City, Metro Manila",
            (14.5312541, 121.0226562),
            (14.5310898, 121.0227339),
            True,
        ),
        (
            "2316 Chino Roces Avenue Extension, Makati, 1231 Metro Manila, Philippines",
            "2317, The Alley at Karrivin, Karrivin Plaza, 1231 Chino Roces Ave Ext, Makati City, Metro Manila",
            (14.5312541, 121.0226562),
            (14.5310898, 121.0227339),
            False,
        ),
        (
            "2 Chome-6-2 Kyomachi, Kokurakita Ward, Kitakyushu, Fukuoka 802-0002, Japan",
            "Japan, 〒802-0002 Fukuoka, Kitakyushu, Kokurakita Ward, Kyomachi, 6 Chome−2−2",
            (33.8857211, 130.8804594),
            (33.8858092, 130.8804664),
            False,
        ),
        (
            "2316 Chino Roces Avenue Extension, Makati, 1231 Metro Manila, Philippines",
            "2316, The Alley at Karrivin, Karrivin Plaza, 1231 Chino Roces Ave Ext, Makati City, Metro Manila",
            (14.5312541, 121.0226562),
            (14.532, 121.0227339),
            False,
        ),
    ],
)
async def test_live_shaped_address_and_coordinates_require_corroboration(
    query_address, candidate_address, source_point, google_point, available
):
    row = record(address=candidate_address)
    row[9] = [None, None, *google_point]
    row[7] = ["/url?q=https%3A%2F%2Ftoyoeatery.com%2F&sa=U"]
    target = place().model_copy(
        update={
            "location": place().location.model_copy(
                update={
                    "formatted_address": query_address,
                    "coordinates": Coordinates(
                        latitude=source_point[0], longitude=source_point[1]
                    ),
                }
            )
        }
    )
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(
            200,
            content=search_body([row])
            if request.url.path == "/search"
            else review_body(),
        )

    provider, _ = client(handler)
    result = await provider.collect(
        target,
        limits=ReviewLimits(max_pages=1, max_reviews=1),
        source_website="https://toyoeatery.com/",
    )
    assert (result.availability == "available") is available
    assert len(paths) == (2 if available else 1)
    if available:
        assert result.sample.branch.evidence == (
            "exact_name",
            "address_and_coordinates",
            "exact_website",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "candidate_address",
    [
        "2316 Wrong Street, 1231 Chino Roces Ave Ext, Makati City, Metro Manila",
        "2316 Chino Roces Ave Ext, Unit 1231, Makati, Metro Manila",
        "1231, The Alley at Karrivin, Karrivin Plaza, 2316 Chino Roces Ave Ext, Makati City, Metro Manila",
    ],
)
async def test_conflicting_address_roles_never_fetch_reviews(candidate_address):
    row = record(address=candidate_address)
    row[9] = [None, None, 14.5310898, 121.0227339]
    row[7] = ["/url?q=https%3A%2F%2Ftoyoeatery.com%2F"]
    target = place().model_copy(
        update={
            "location": place().location.model_copy(
                update={
                    "formatted_address": "2316 Chino Roces Avenue Extension, Makati, 1231 Metro Manila, Philippines",
                    "coordinates": Coordinates(
                        latitude=14.5312541, longitude=121.0226562
                    ),
                }
            )
        }
    )
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, content=search_body([row]))

    provider, _ = client(handler)
    result = await provider.collect(target, source_website="https://toyoeatery.com/")
    assert result.availability == "no_match"
    assert paths == ["/search"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "candidate_address",
    [
        "Japan, 〒0002-802 Fukuoka, Kitakyushu, Kokurakita Ward, Kyomachi, 2 Chome−6−2",
        "Japan, 〒802-0002 Tokyo, Shinjuku Ward, Fake, Kyomachi, 2 Chome−6−2",
        "Japan, 〒802-0002 Fukuoka, Kitakyushu, Kokuraminami Ward, Kyomachi, 2 Chome−6−2",
    ],
)
async def test_conflicting_japanese_postcode_or_locality_never_fetches(
    candidate_address,
):
    row = record(address=candidate_address)
    row[9] = [None, None, 33.8858092, 130.8804664]
    target = place().model_copy(
        update={
            "location": place().location.model_copy(
                update={
                    "formatted_address": "2 Chome-6-2 Kyomachi, Kokurakita Ward, Kitakyushu, Fukuoka 802-0002, Japan",
                    "coordinates": Coordinates(
                        latitude=33.8857211, longitude=130.8804594
                    ),
                }
            )
        }
    )
    provider, calls = client(
        lambda request: httpx.Response(200, content=search_body([row]))
    )
    result = await provider.collect(target)
    assert result.availability == "no_match"
    assert len(calls.receipts) == 1


@pytest.mark.asyncio
async def test_source_website_required_and_same_name_matches_remain_ambiguous():
    address = "2316, The Alley at Karrivin, Karrivin Plaza, 1231 Chino Roces Ave Ext, Makati City, Metro Manila"
    row = record(address=address)
    row[9] = [None, None, 14.5310898, 121.0227339]
    row[7] = ["/url?q=https%3A%2F%2Ftoyoeatery.com%2F"]
    target = place().model_copy(
        update={
            "location": place().location.model_copy(
                update={
                    "formatted_address": "2316 Chino Roces Avenue Extension, Makati, 1231 Metro Manila, Philippines",
                    "coordinates": Coordinates(
                        latitude=14.5312541, longitude=121.0226562
                    ),
                }
            )
        }
    )
    provider, calls = client(
        lambda request: httpx.Response(200, content=search_body([row]))
    )
    result = await provider.collect(target)
    assert result.availability == "no_match"
    assert len(calls.receipts) == 1
    sibling = record(address=address, feature="0x3:0x4")
    sibling[9], sibling[7] = row[9], row[7]
    provider, calls = client(
        lambda request: httpx.Response(200, content=search_body([row, sibling]))
    )
    result = await provider.collect(target, source_website="https://toyoeatery.com/")
    assert result.availability == "no_match"
    assert result.reason == "ambiguous"
    assert len(calls.receipts) == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/url?q=https%3A%2F%2Ftoyoeatery.com%2F&sa=U", "https://toyoeatery.com/"),
        (
            "/url?q=https%3A%2F%2Ftoyoeatery.com%2F&q=https%3A%2F%2Fevil.invalid%2F",
            None,
        ),
        ("https://evil.invalid/url?q=https%3A%2F%2Ftoyoeatery.com%2F", None),
        ("/url?q=https%3A%2F%2Ftoyoeatery.com%40evil.invalid%2F", None),
        ("https://toyoeatery.com:444/", None),
    ],
)
def test_google_website_redirect_is_safe(value, expected):
    from app.providers.google_branch_search import _candidate_website

    assert _candidate_website(value) == expected
