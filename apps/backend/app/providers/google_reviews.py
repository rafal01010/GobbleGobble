from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, ClassVar, Literal
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.core.calls import (
    AttemptCalls,
    CallFailure,
    CallSpec,
    Completed,
    FailureDiagnostic,
    Parsed,
    Receipt,
    Usage,
)

# The BOQ payload and positional review fields adapt YasogaN's
# google-maps-review-scraper 3.1.0. MIT notice in docs/THIRD_PARTY_NOTICES.md.
_ENDPOINT = (
    "https://www.google.com/httpservice/web/"
    "PrivateLocalSearchUiDataService/GetLocalBoqProxy"
)
_FEATURE = re.compile(r"0x[0-9a-fA-F]{1,16}:0x[0-9a-fA-F]{1,16}")
_TEXT_CANDIDATE_POSITIONS = (27, 28, 11)
_SORTS = {"relevant": 1, "newest": 2, "highest": 3, "lowest": 4}
type ReviewSort = Literal["relevant", "newest", "highest", "lowest"]
type SampleStatus = Literal[
    "sample", "partial", "zero_reviews", "empty_sample", "omitted_sample", "unavailable"
]
type Termination = Literal[
    "source_exhausted",
    "sample_limit",
    "page_limit",
    "time_limit",
    "byte_limit",
    "access_failure",
    "unsupported_encoding",
    "transport_failure",
    "malformed_response",
    "omitted_sample",
    "empty_sample",
    "duplicate_reviews",
    "repeated_cursor",
    "invalid_rows",
    "conflicting_total",
    "request_limit",
    "call_timeout",
    "attempt_timeout",
    "source_blocked",
    "nested_call",
    "cancelled",
    "failed",
]


class ReviewInputError(ValueError):
    def __init__(self) -> None:
        super().__init__("invalid Google review request")


type PreflightCode = Literal[
    "live_mode_required",
    "test_transport_required",
    "test_transport_forbidden",
    "configure_GG_REVIEW_PROVIDER",
    "configure_GG_REVIEW_TIMEOUT_SECONDS",
]


class ReviewPreflightError(Exception):
    def __init__(self, code: PreflightCode) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class BranchQuery:
    name: str = field(repr=False)
    full_address: str | None = field(default=None, repr=False)
    website: str | None = field(default=None, repr=False)
    feature_id: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class BranchCandidate:
    name: str = field(repr=False)
    google_maps_url: str | None = field(default=None, repr=False)
    feature_id: str | None = field(default=None, repr=False)
    full_address: str | None = field(default=None, repr=False)
    website: str | None = field(default=None, repr=False)
    rating: float | None = field(default=None, repr=False)
    reported_total: int | None = field(default=None, repr=False)


@dataclass(frozen=True)
class MatchedBranch:
    feature_id: str = field(repr=False)
    source_url: str = field(repr=False)
    name: str = field(repr=False)
    full_address: str | None = field(repr=False)
    website: str | None = field(repr=False)
    rating: float | None = field(repr=False)
    reported_total: int | None = field(repr=False)
    evidence: tuple[
        Literal[
            "exact_name", "exact_full_address", "exact_website", "target_feature_id"
        ],
        ...,
    ]
    metadata_basis: ClassVar[str] = "supplied_google_candidate_not_refreshed_by_reviews"


@dataclass(frozen=True)
class NoConfidentMatch:
    reason: Literal[
        "no_candidates",
        "insufficient_corroboration",
        "ambiguous",
        "conflicting_identity",
    ]


class ReviewLimits(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", hide_input_in_errors=True
    )
    max_pages: Annotated[int, Field(ge=1, le=5)] = 2
    max_reviews: Annotated[int, Field(ge=1, le=50)] = 20
    max_response_bytes: Annotated[int, Field(ge=1, le=1048576)] = 1048576
    max_total_bytes: Annotated[int, Field(ge=1, le=5242880)] = 2097152
    total_seconds: Annotated[float, Field(gt=0, le=60, allow_inf_nan=False)] = 30.0


@dataclass(frozen=True)
class ReviewDate:
    raw: str = field(repr=False)
    normalized_utc: datetime | None = field(repr=False)
    basis: Literal[
        "inferred_epoch_seconds",
        "inferred_epoch_milliseconds",
        "inferred_epoch_microseconds",
        "inferred_epoch_nanoseconds",
        "uninterpreted",
    ]


@dataclass(frozen=True)
class GoogleReview:
    review_id: str = field(repr=False)
    rating: float | None = field(repr=False)
    text: str | None = field(repr=False)
    text_state: Literal["present", "empty", "omitted"]
    author_name: str | None = field(repr=False)
    author_url: str | None = field(repr=False)
    source_url: str = field(repr=False)
    link_basis: Literal["review", "branch"]
    published: ReviewDate | None = field(repr=False)
    source_name: ClassVar[str] = "Google Maps"


@dataclass(frozen=True)
class ReviewSample:
    branch: MatchedBranch = field(repr=False)
    status: SampleStatus
    termination: Termination
    sort: ReviewSort
    limits: ReviewLimits
    reviews: tuple[GoogleReview, ...] = field(repr=False)
    receipts: tuple[Receipt, ...]
    pages_received: int
    response_bytes: int
    duplicate_count: int
    invalid_row_count: int
    received_row_count: int
    is_complete_history: ClassVar[Literal[False]] = False
    retention: ClassVar[str] = "ephemeral_reacquire_on_demand"


@dataclass(frozen=True)
class FoodPassage:
    label: str
    text: str = field(repr=False)


@dataclass(frozen=True)
class MinimizedGoogleReviewSample:
    passages: tuple[FoodPassage, ...] = field(repr=False)
    sample_status: SampleStatus
    sample_termination: Termination
    sort: ReviewSort
    max_reviews: int
    max_pages: int
    sampled_reviews: int
    selected_words: int
    source_class: ClassVar[Literal["minimized_google_reviews"]] = (
        "minimized_google_reviews"
    )
    limitations: ClassVar[tuple[str, ...]] = (
        "bounded selected sample, not representative or complete history",
        "ordering requested from source, not independently guaranteed",
        "food passages only, no claim about current menus or operating status",
        "vocabulary filtering minimizes personal data, not proof of anonymization",
    )

    def model_input(self) -> dict[str, JsonValue]:
        return {
            "source_class": self.source_class,
            "sample_status": self.sample_status,
            "sample_termination": self.sample_termination,
            "requested_sort": self.sort,
            "max_reviews": self.max_reviews,
            "max_pages": self.max_pages,
            "sampled_reviews": self.sampled_reviews,
            "selected_words": self.selected_words,
            "limitations": list(self.limitations),
            "passages": [
                {"label": item.label, "text": item.text} for item in self.passages
            ],
        }


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _normalized(value: str | None) -> str:
    return " ".join(value.casefold().split()) if value else ""


def _google_url(value: object, *, author: bool = False) -> str | None:
    text = _text(value)
    if text is None or len(text) > 4096:
        return None
    try:
        url = urlsplit(text)
        if (
            url.scheme != "https"
            or url.hostname not in {"www.google.com", "google.com", "maps.google.com"}
            or url.username is not None
            or url.password is not None
            or url.port not in {None, 443}
            or not url.path.startswith("/maps")
            or (author and "/contrib/" not in url.path)
        ):
            return None
    except ValueError:
        return None
    return text


def _website(value: str | None) -> str:
    if value is None:
        return ""
    try:
        url = urlsplit(value)
        if (
            url.scheme not in {"https", "http"}
            or not url.hostname
            or url.username
            or url.password
        ):
            return ""
        return url.hostname.casefold().removeprefix("www.") + url.path.rstrip("/")
    except ValueError:
        return ""


def _feature_id(value: str | None) -> str | None:
    if value is None or _FEATURE.fullmatch(value) is None:
        return None
    first, second = value.split(":")
    return f"0x{int(first, 16):x}:0x{int(second, 16):x}"


def _url_identity(value: str) -> tuple[str | None, int | None]:
    decoded = unquote(value)
    features = {_feature_id(match.group()) for match in _FEATURE.finditer(decoded)}
    if len(features) > 1:
        raise ReviewInputError()
    feature = next(iter(features), None)
    query = parse_qs(urlsplit(value).query)
    cid_values = query.get("cid", [])
    if len(cid_values) > 1:
        raise ReviewInputError()
    cid = None
    if cid_values:
        if (
            not cid_values[0].isascii()
            or not cid_values[0].isdigit()
            or len(cid_values[0]) > 20
        ):
            raise ReviewInputError()
        cid = int(cid_values[0])
        if not 0 < cid < 2**64:
            raise ReviewInputError()
    return feature, cid


def match_branch(
    query: BranchQuery, candidates: tuple[BranchCandidate, ...]
) -> MatchedBranch | NoConfidentMatch:
    """Corroborate supplied Google candidates. This performs no discovery.

    Exact names and full addresses are deliberately strict. Website-only chain
    matches require an explicitly targeted full feature ID as well. Supplied
    metadata is evidence from the caller, not newly fetched Google metadata.
    """
    if type(query) is not BranchQuery or not _text(query.name) or len(query.name) > 500:
        raise ReviewInputError()
    for value in (query.full_address, query.website, query.feature_id):
        if value is not None and (not isinstance(value, str) or len(value) > 4096):
            raise ReviewInputError()
    if type(candidates) is not tuple or len(candidates) > 20:
        raise ReviewInputError()
    target_id = _feature_id(query.feature_id)
    if query.feature_id is not None and target_id is None:
        raise ReviewInputError()
    matches: dict[str, MatchedBranch] = {}
    identities: dict[str, BranchCandidate] = {}
    if not candidates:
        return NoConfidentMatch("no_candidates")
    for candidate in candidates:
        if type(candidate) is not BranchCandidate or not _text(candidate.name):
            raise ReviewInputError()
        for value in (
            candidate.name,
            candidate.google_maps_url,
            candidate.feature_id,
            candidate.full_address,
            candidate.website,
        ):
            if value is not None and (not isinstance(value, str) or len(value) > 4096):
                raise ReviewInputError()
        if _normalized(candidate.name) != _normalized(query.name):
            continue
        supplied_id = _feature_id(candidate.feature_id)
        source_url = _google_url(candidate.google_maps_url)
        if candidate.google_maps_url is not None and source_url is None:
            return NoConfidentMatch("conflicting_identity")
        try:
            url_id, cid = _url_identity(source_url) if source_url else (None, None)
        except ReviewInputError:
            return NoConfidentMatch("conflicting_identity")
        feature = supplied_id or url_id
        if (
            (candidate.feature_id is not None and supplied_id is None)
            or (supplied_id and url_id and supplied_id != url_id)
            or (feature and cid is not None and int(feature.split(":")[1], 16) != cid)
            or (target_id and feature and target_id != feature)
        ):
            return NoConfidentMatch("conflicting_identity")
        if feature is None:
            continue
        if feature in identities and identities[feature] != candidate:
            return NoConfidentMatch("conflicting_identity")
        identities[feature] = candidate
        address_matches = bool(query.full_address) and _normalized(
            query.full_address
        ) == _normalized(candidate.full_address)
        site_matches = bool(_website(query.website)) and _website(
            query.website
        ) == _website(candidate.website)
        if (query.full_address and candidate.full_address and not address_matches) or (
            query.website and candidate.website and not site_matches
        ):
            return NoConfidentMatch("conflicting_identity")
        if not address_matches and not (site_matches and feature == target_id):
            continue
        if (
            candidate.rating is not None
            and (
                type(candidate.rating) not in (int, float)
                or not 1 <= candidate.rating <= 5
            )
        ) or (
            candidate.reported_total is not None
            and (
                type(candidate.reported_total) is not int
                or candidate.reported_total < 0
            )
        ):
            raise ReviewInputError()
        evidence: list[
            Literal[
                "exact_name", "exact_full_address", "exact_website", "target_feature_id"
            ]
        ] = ["exact_name"]
        if address_matches:
            evidence.append("exact_full_address")
        if site_matches:
            evidence.append("exact_website")
        if target_id == feature:
            evidence.append("target_feature_id")
        matches[feature] = MatchedBranch(
            feature,
            source_url
            or f"https://www.google.com/maps?cid={int(feature.split(':')[1], 16)}",
            candidate.name,
            candidate.full_address,
            candidate.website,
            candidate.rating,
            candidate.reported_total,
            tuple(evidence),
        )
    if len(matches) > 1:
        return NoConfidentMatch("ambiguous")
    return (
        next(iter(matches.values()))
        if matches
        else NoConfidentMatch("insufficient_corroboration")
    )


def normalize_review_date(value: JsonValue) -> ReviewDate | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    raw = str(value)
    scales: dict[
        int,
        tuple[
            int,
            Literal[
                "inferred_epoch_seconds",
                "inferred_epoch_milliseconds",
                "inferred_epoch_microseconds",
                "inferred_epoch_nanoseconds",
            ],
        ],
    ] = {
        10: (1, "inferred_epoch_seconds"),
        13: (1000, "inferred_epoch_milliseconds"),
        16: (1000000, "inferred_epoch_microseconds"),
        19: (1000000000, "inferred_epoch_nanoseconds"),
    }
    if raw.isascii() and raw.isdigit() and len(raw) in scales:
        scale, basis = scales[len(raw)]
        seconds, fraction = divmod(int(raw), scale)
        try:
            result = datetime.fromtimestamp(seconds, UTC) + timedelta(
                microseconds=fraction * 1000000 // scale
            )
        except ValueError, OverflowError, OSError:
            return ReviewDate(raw, None, "uninterpreted")
        if (
            datetime(2000, 1, 1, tzinfo=UTC)
            <= result
            <= datetime.now(UTC) + timedelta(days=2)
        ):
            return ReviewDate(raw, result, basis)
    return ReviewDate(raw, None, "uninterpreted")


def _at(value: JsonValue, *path: int) -> JsonValue:
    for index in path:
        if not isinstance(value, list) or index >= len(value):
            return None
        value = value[index]
    return value


@dataclass(frozen=True)
class _Page:
    rows: tuple[GoogleReview, ...] = field(repr=False)
    cursor: str = field(repr=False)
    sample_state: Literal["present", "empty", "omitted"]
    decoded_bytes: int
    invalid_rows: int


@dataclass(frozen=True)
class _PageFailure:
    reason: Termination
    decoded_bytes: int = 0


def _parse_review(row: JsonValue, branch: MatchedBranch) -> GoogleReview | None:
    if not isinstance(row, list) or len(row) < 6:
        return None
    review_id = _text(row[5])
    rating = row[1]
    if review_id is None or len(review_id) > 512:
        return None
    if rating is not None and (
        isinstance(rating, bool)
        or not isinstance(rating, (int, float))
        or not 1 <= rating <= 5
    ):
        return None
    google_indices = [
        index
        for index, item in enumerate(row)
        if isinstance(item, list) and item and item[0] == "Google"
    ]
    if not google_indices or google_indices[-1] < 6:
        return None
    google_index = google_indices[-1]
    qa_indices = [
        index
        for index in range(6, google_index)
        if isinstance(_at(row[index], 0, 0, 0), str)
    ]
    texts: list[str] = []
    if qa_indices:
        for index in (qa_indices[-1] - 3, qa_indices[-1] - 2):
            value = row[index] if index >= 6 else None
            if isinstance(value, str):
                texts.append(value)
    if not texts:
        for index in _TEXT_CANDIDATE_POSITIONS:
            value = row[index] if index < google_index else None
            if isinstance(value, str) and not value.startswith("http"):
                texts.append(value)
    text = next((item for item in texts if item.strip()), "" if texts else None)
    if text is not None and len(text) > 20000:
        return None
    author_name = _text(_at(row, 3, 0))
    author_url = _google_url(_at(row, 3, 2), author=True)
    review_url = _google_url(_at(row, google_index, 1))
    if review_url and not any(
        token in review_url for token in ("reviewid=", "!1sCh", "/reviews/")
    ):
        review_url = None
    return GoogleReview(
        review_id,
        float(rating) if isinstance(rating, (int, float)) else None,
        text,
        "omitted" if text is None else "present" if text else "empty",
        author_name,
        author_url,
        review_url or branch.source_url,
        "review" if review_url else "branch",
        normalize_review_date(_at(row, 2, 2)),
    )


def _reject_constant(_value: str) -> JsonValue:
    raise ValueError("invalid JSON constant")


def _parse_page(body: bytes, branch: MatchedBranch) -> _Page | _PageFailure:
    clean = body[4:].lstrip(b"\n") if body.startswith(b")]}'") else body
    try:
        data: JsonValue = json.loads(clean, parse_constant=_reject_constant)
    except ValueError, UnicodeError, RecursionError:
        return _PageFailure("malformed_response", len(body))
    node = _at(data, 1, 10)
    if not isinstance(node, list) or len(node) < 3:
        return _PageFailure("malformed_response", len(body))
    raw_rows = node[2]
    cursor = _at(node, 6)
    if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 8192):
        return _PageFailure("malformed_response", len(body))
    if raw_rows is None:
        return _Page((), cursor or "", "omitted", len(body), 0)
    if not isinstance(raw_rows, list) or len(raw_rows) > 100:
        return _PageFailure("malformed_response", len(body))
    rows = tuple(
        item for row in raw_rows if (item := _parse_review(row, branch)) is not None
    )
    if raw_rows and not rows:
        return _PageFailure("malformed_response", len(body))
    return _Page(
        rows,
        cursor or "",
        "present" if raw_rows else "empty",
        len(body),
        len(raw_rows) - len(rows),
    )


class _DenyContent(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return False


_DENY_CONTENT = _DenyContent()


def _silence_http_content() -> None:
    for name in ("httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2"):
        logger = logging.getLogger(name)
        if _DENY_CONTENT not in logger.filters:
            logger.addFilter(_DENY_CONTENT)


class GoogleReviewClient:
    """Ephemeral bounded BOQ samples with one shared admission per page GET.

    No browser, Node runtime, keys, cookies, retries, redirects or persistence.
    """

    def __init__(
        self, calls: AttemptCalls, *, test_transport: httpx.MockTransport | None = None
    ) -> None:
        self._calls = calls
        self._test_transport = test_transport

    def preflight(self) -> None:
        settings = self._calls.review_settings
        if settings.review_provider != "google_reviews":
            raise ReviewPreflightError("configure_GG_REVIEW_PROVIDER")
        if settings.review_timeout_seconds is None:
            raise ReviewPreflightError("configure_GG_REVIEW_TIMEOUT_SECONDS")
        if settings.mode == "diagnostic":
            raise ReviewPreflightError("live_mode_required")
        if (
            settings.mode == "test"
            and type(self._test_transport) is not httpx.MockTransport
        ):
            raise ReviewPreflightError("test_transport_required")
        if settings.mode == "live" and self._test_transport is not None:
            raise ReviewPreflightError("test_transport_forbidden")

    async def _get(
        self,
        branch: MatchedBranch,
        sort: ReviewSort,
        cursor: str,
        seconds: float,
        byte_limit: int,
    ) -> Parsed[_Page | _PageFailure]:
        node: list[JsonValue] = [None, _SORTS[sort]]
        node.extend([None] * 9)
        node.append([branch.feature_id])
        if cursor:
            node.extend([None] * 7 + [cursor])
        else:
            node[9] = 10
        payload = [None, [None] * 9 + [node]]
        _silence_http_content()
        transport = (
            self._test_transport
            if self._test_transport is not None
            else httpx.AsyncHTTPTransport(retries=0, trust_env=False)
        )
        body = bytearray()
        try:
            async with (
                httpx.AsyncClient(
                    transport=transport,
                    follow_redirects=False,
                    trust_env=False,
                    timeout=seconds,
                ) as client,
                client.stream(
                    "GET",
                    _ENDPOINT,
                    params={
                        "msc": "gwsrpc",
                        "reqpld": json.dumps(payload, separators=(",", ":")),
                    },
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; GobbleGobble/0.1)",
                        "Accept": "*/*",
                        "Accept-Encoding": "identity",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Referer": "https://www.google.com/maps/",
                    },
                ) as response,
            ):
                if response.status_code != 200:
                    return Parsed(_PageFailure("access_failure"), Usage(requests=1))
                if response.headers.get(
                    "content-encoding", ""
                ).strip().casefold() not in {"", "identity"}:
                    return Parsed(
                        _PageFailure("unsupported_encoding"), Usage(requests=1)
                    )
                async for chunk in response.aiter_bytes(chunk_size=16384):
                    if len(body) + len(chunk) > byte_limit:
                        return Parsed(
                            _PageFailure("byte_limit", len(body)), Usage(requests=1)
                        )
                    body.extend(chunk)
        except httpx.TimeoutException:
            return Parsed(_PageFailure("call_timeout", len(body)), Usage(requests=1))
        except httpx.HTTPError:
            return Parsed(
                _PageFailure("transport_failure", len(body)), Usage(requests=1)
            )
        return Parsed(_parse_page(bytes(body), branch), Usage(requests=1))

    async def fetch(
        self,
        branch: MatchedBranch,
        *,
        limits: ReviewLimits | None = None,
        sort: ReviewSort = "relevant",
    ) -> ReviewSample:
        if (
            type(branch) is not MatchedBranch
            or _feature_id(branch.feature_id) is None
            or sort not in _SORTS
        ):
            raise ReviewInputError()
        limits = (
            ReviewLimits() if limits is None else ReviewLimits.model_validate(limits)
        )
        self.preflight()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + limits.total_seconds
        reviews: list[GoogleReview] = []
        receipts: list[Receipt] = []
        seen_ids: set[str] = set()
        seen_cursors: set[str] = set()
        cursor = ""
        pages = size = duplicates = invalid = received_rows = 0
        status: SampleStatus = "unavailable"
        termination: Termination = "page_limit"
        for _ in range(limits.max_pages):
            remaining = deadline - loop.time()
            if remaining <= 0:
                termination = "time_limit"
                status = "partial" if reviews else "unavailable"
                break
            allowance = min(limits.max_response_bytes, limits.max_total_bytes - size)
            if allowance <= 0:
                termination = "byte_limit"
                status = "partial" if reviews else "unavailable"
                break

            page_failure: _PageFailure | None = None

            async def invoke(
                seconds: float,
                *,
                remaining: float = remaining,
                cursor: str = cursor,
                allowance: int = allowance,
            ) -> Parsed[_Page]:
                nonlocal page_failure
                try:
                    async with asyncio.timeout(min(seconds, remaining)):
                        parsed = await self._get(
                            branch, sort, cursor, min(seconds, remaining), allowance
                        )
                except TimeoutError:
                    parsed = Parsed(_PageFailure("time_limit"))
                if isinstance(parsed.value, _PageFailure):
                    page_failure = parsed.value
                    diagnostics: dict[Termination, FailureDiagnostic] = {
                        "malformed_response": "invalid_response",
                        "unsupported_encoding": "invalid_response",
                        "byte_limit": "response_limit",
                        "access_failure": "access_failure",
                        "transport_failure": "transport_failure",
                        "time_limit": "operation_timeout",
                        "call_timeout": "operation_timeout",
                    }
                    raise CallFailure(
                        diagnostics.get(page_failure.reason, "invalid_response")
                    )
                return Parsed(parsed.value, parsed.usage)

            result = await self._calls.run(
                CallSpec(
                    capability="reviews",
                    use="api_diagnostic",
                    input_sources=("ordinary_google",),
                ),
                invoke,
            )
            receipts.append(result.receipt)
            if not isinstance(result, Completed):
                termination = (
                    "failed"
                    if result.receipt.outcome == "success"
                    else result.receipt.outcome
                )
                if page_failure is not None:
                    size += page_failure.decoded_bytes
                    termination = page_failure.reason
                status = "partial" if reviews else "unavailable"
                break
            page = result.value
            size += page.decoded_bytes
            pages += 1
            invalid += page.invalid_rows
            received_rows += len(page.rows) + page.invalid_rows
            for review in page.rows:
                if review.review_id in seen_ids:
                    duplicates += 1
                    continue
                seen_ids.add(review.review_id)
                if len(reviews) < limits.max_reviews:
                    reviews.append(review)
            if branch.reported_total == 0 and reviews:
                termination, status = "conflicting_total", "partial"
                break
            if page.cursor and page.cursor in seen_cursors:
                termination, status = (
                    "repeated_cursor",
                    "partial" if reviews else "unavailable",
                )
                break
            if page.invalid_rows or duplicates:
                termination = (
                    "invalid_rows" if page.invalid_rows else "duplicate_reviews"
                )
                status = "partial" if reviews else "unavailable"
                break
            if page.sample_state != "present":
                termination = (
                    "omitted_sample"
                    if page.sample_state == "omitted"
                    else "empty_sample"
                )
                if reviews:
                    status = "partial"
                elif (
                    page.sample_state == "empty"
                    and branch.reported_total == 0
                    and not page.cursor
                ):
                    status, termination = "zero_reviews", "source_exhausted"
                else:
                    status = (
                        "omitted_sample"
                        if page.sample_state == "omitted"
                        else "empty_sample"
                    )
                break
            status = "sample"
            if loop.time() >= deadline:
                termination, status = "time_limit", "partial"
                break
            if len(reviews) >= limits.max_reviews:
                termination = "sample_limit"
                break
            if not page.cursor:
                termination = "source_exhausted"
                break
            seen_cursors.add(page.cursor)
            cursor = page.cursor
        return ReviewSample(
            branch,
            status,
            termination,
            sort,
            limits,
            tuple(reviews),
            tuple(receipts),
            pages,
            size,
            duplicates,
            invalid,
            received_rows,
        )


_FOOD = frozenset(
    re.findall(
        r"[a-z]+",
        "food dish dishes menu tasting meal meals flavor flavors flavour flavours bread dessert desserts pork rice fish beef chicken coffee seafood vegetables vegetable sauce sauces soup meat",
    )
)
_VOCABULARY = _FOOD | frozenset(
    re.findall(
        r"[a-z]+",
        "a an the and or but of to for from with without in on at as was were is are had has have it its this that these those all each every very quite really so just also too more most less some both here actually especially overall not no only texture textures taste tastes tasted delicious tasty good great excellent amazing nice balanced fresh crispy crisp tender soft rich light sweet sour savory savoury spicy salty smoky bland bitter smooth warm hot cold grilled fried cooked cooking baked roasted smoked perfectly well beautifully presentation combination combinations interesting unique lovely wonderful superb exceptional outstanding enjoyable satisfying quality portion portions small generous complex subtle bold creative delicate simple thoughtfully prepared done moist dry crunchy crust center chocolate fruit fruits local seasonal ingredients ingredient appetizing innovative fantastic incredible decent average unpleasant disappointing underwhelming overpowering overwhelming greasy oily burnt stale freshness balance delightful deliciously absolutely delightfully better best s",
    )
)


def minimize_for_model(sample: ReviewSample) -> MinimizedGoogleReviewSample:
    """Select at most three generic food sentences and sixty words in memory.

    Unknown words, names, pronouns about people, digits, contacts and sensitive
    narratives fail the fixed vocabulary.
    Attribution stays in the caller's ephemeral sample, outside model input.
    """
    passages: list[FoodPassage] = []
    selected: set[str] = set()
    words = 0
    for review in sample.reviews[:10]:
        if not review.text:
            continue
        for part in re.split(r"(?<=[.!?])\s+|\n+", review.text):
            sentence = part.strip()
            count = len(sentence.split())
            tokens = set(re.findall(r"[a-z]+", sentence.casefold()))
            if (
                not 5 <= count <= 20
                or words + count > 60
                or len(sentence) > 220
                or not tokens & _FOOD
                or not tokens <= _VOCABULARY
                or not re.fullmatch(r"[A-Za-z][A-Za-z ,;:'()!?._-]+", sentence)
                or re.search(r"[A-Z]", sentence[1:])
                or sentence.casefold() in selected
            ):
                continue
            passages.append(FoodPassage(f"p{len(passages)}", sentence))
            selected.add(sentence.casefold())
            words += count
            break
        if len(passages) == 3:
            break
    return MinimizedGoogleReviewSample(
        tuple(passages),
        sample.status,
        sample.termination,
        sample.sort,
        sample.limits.max_reviews,
        sample.limits.max_pages,
        len(sample.reviews),
        words,
    )
