from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import parse_qs, quote_plus, urlsplit

import httpx

from app.core.calls import (
    AttemptCalls,
    CallFailure,
    CallSpec,
    Completed,
    FailureDiagnostic,
    Parsed,
    Receipt,
    Stopped,
    Usage,
)
from app.providers.google_reviews import (
    BranchCandidate,
    GoogleReviewClient,
    _silence_http_content,
    _website,
)
from app.schemas.search import Place

# Request layout and record positions adapted from google-maps-scraper, MIT license.
_PB = (
    "!1s{query}!7i20!8i0!10b1!12m6!1m1!18b1!2m1!20e3!6m1!114b1"
    "!17m1!3e1!20m57!2m2!1i203!2i100!3m2!2i4!5b1"
    "!6m6!1m2!1i86!2i86!1m2!1i408!2i240"
    "!7m33!1m3!1e1!2b0!3e3!1m3!1e2!2b1!3e2!1m3!1e2!2b0!3e3"
    "!1m3!1e8!2b0!3e3!1m3!1e10!2b0!3e3!1m3!1e10!2b1!3e2"
    "!1m3!1e10!2b0!3e4!1m3!1e9!2b1!3e2!2b1!9b0"
    "!15m8!1m7!1m2!1m1!1e2!2m2!1i195!2i195!3i20"
)
_FEATURE = re.compile(r"0x[0-9a-fA-F]{1,16}:0x[0-9a-fA-F]{1,16}\Z")


@dataclass(frozen=True)
class BranchSearchPage:
    status: Literal[
        "candidates",
        "malformed_response",
        "unsupported_encoding",
        "access_failure",
        "quota",
        "transport_failure",
        "byte_limit",
    ]
    candidates: tuple[BranchCandidate, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class BranchSearchFailed:
    reason: str
    receipt: Receipt


def _candidate_website(value: str | None) -> str | None:
    if value is None or len(value) > 4096:
        return None
    try:
        url = urlsplit(value)
        if url.path == "/url" and (
            not url.netloc
            and not url.scheme
            or url.scheme == "https"
            and url.hostname in {"google.com", "www.google.com"}
            and url.username is None
            and url.password is None
            and url.port in {None, 443}
        ):
            targets = parse_qs(url.query).get("q", [])
            if len(targets) != 1:
                return None
            value = targets[0]
        return value if _website(value) else None
    except ValueError:
        return None


def _parse(body: bytes) -> BranchSearchPage:
    try:
        text = body.decode("utf-8")
        if text.startswith(")]}'"):
            text = text[4:].lstrip("\r\n")
        payload = json.loads(text)
        if isinstance(payload, dict) and isinstance(payload.get("d"), str):
            payload = json.loads(payload["d"])
    except UnicodeDecodeError, ValueError, RecursionError:
        return BranchSearchPage("malformed_response")
    if not isinstance(payload, list):
        return BranchSearchPage("malformed_response")
    pending = [payload]
    candidates: list[BranchCandidate] = []
    while pending:
        node = pending.pop()
        if not isinstance(node, list):
            continue
        if (
            len(node) > 50
            and isinstance(node[10], str)
            and _FEATURE.fullmatch(node[10])
            and isinstance(node[11], str)
            and node[11].strip()
            and len(node[11]) <= 500
            and int(node[10].split(":")[1], 16) != 0
        ):
            address = node[2]
            full_address: str | None
            if (
                isinstance(address, list)
                and address
                and all(isinstance(part, str) and part.strip() for part in address)
            ):
                full_address = ", ".join(address)
            else:
                full_address = next(
                    (
                        node[index]
                        for index in (39, 18)
                        if isinstance(node[index], str) and node[index].strip()
                    ),
                    None,
                )
            rating = total = None
            stats = node[4]
            if isinstance(stats, list):
                if (
                    len(stats) > 7
                    and type(stats[7]) in (float, int)
                    and 1 <= stats[7] <= 5
                ):
                    rating = float(stats[7])
                if len(stats) > 8 and type(stats[8]) is int and stats[8] >= 0:
                    total = stats[8]
            website = (
                node[7][0]
                if isinstance(node[7], list) and node[7] and isinstance(node[7][0], str)
                else None
            )
            if full_address is not None and len(full_address) > 4096:
                return BranchSearchPage("malformed_response")
            if website is not None and len(website) > 4096:
                return BranchSearchPage("malformed_response")
            point = node[9]
            coordinates = None
            if (
                isinstance(point, list)
                and len(point) > 3
                and all(
                    type(v) in (int, float) and math.isfinite(v) for v in point[2:4]
                )
                and abs(point[2]) <= 90
                and abs(point[3]) <= 180
            ):
                coordinates = (float(point[2]), float(point[3]))
            candidates.append(
                BranchCandidate(
                    node[11],
                    feature_id=node[10],
                    full_address=full_address,
                    website=_candidate_website(website),
                    rating=rating,
                    reported_total=total,
                    coordinates=coordinates,
                )
            )
            if len(candidates) > 20:
                return BranchSearchPage("malformed_response")
        else:
            pending.extend(reversed(node))
    if not candidates:
        return BranchSearchPage("malformed_response")
    return BranchSearchPage("candidates", tuple(candidates))


class GoogleBranchSearch:
    def __init__(
        self, calls: AttemptCalls, *, test_transport: httpx.MockTransport | None = None
    ) -> None:
        self._calls = calls
        self._test_transport = test_transport
        self._reviews = GoogleReviewClient(calls, test_transport=test_transport)

    async def search(
        self, place: Place
    ) -> Completed[BranchSearchPage] | Stopped | BranchSearchFailed:
        self._reviews.preflight()
        if not place.name or not place.location.formatted_address:
            raise ValueError("branch lookup requires name and full address")
        query = f"{place.name} {place.location.formatted_address}"
        if len(query) > 1000:
            raise ValueError("branch lookup query exceeds limit")
        spec = CallSpec(capability="reviews", use="api_diagnostic", input_sources=())

        failure: BranchSearchPage | None = None

        async def request(seconds: float) -> Parsed[BranchSearchPage]:
            _silence_http_content()
            transport = self._test_transport or httpx.AsyncHTTPTransport(
                retries=0, trust_env=False
            )
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
                        "https://www.google.com/search",
                        params={
                            "tbm": "map",
                            "hl": "en",
                            "q": query,
                            "pb": _PB.format(query=quote_plus(query)),
                        },
                        headers={
                            "User-Agent": "Mozilla/5.0 (compatible; GobbleGobble/0.1)",
                            "Accept-Encoding": "identity",
                        },
                    ) as response,
                ):
                    if response.status_code != 200:
                        return Parsed(
                            BranchSearchPage(
                                "quota"
                                if response.status_code == 429
                                else "access_failure"
                            ),
                            Usage(requests=1),
                        )
                    if response.headers.get(
                        "content-encoding", ""
                    ).strip().casefold() not in {"", "identity"}:
                        return Parsed(
                            BranchSearchPage("unsupported_encoding"),
                            Usage(requests=1),
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=16384):
                        if len(body) + len(chunk) > 1048576:
                            return Parsed(
                                BranchSearchPage("byte_limit"), Usage(requests=1)
                            )
                        body.extend(chunk)
                    return Parsed(_parse(bytes(body)), Usage(requests=1))
            except httpx.TimeoutException:
                raise TimeoutError from None
            except httpx.HTTPError:
                return Parsed(BranchSearchPage("transport_failure"), Usage(requests=1))

        async def invoke(seconds: float) -> Parsed[BranchSearchPage]:
            nonlocal failure
            parsed = await request(seconds)
            if parsed.value.status != "candidates":
                failure = parsed.value
                diagnostics: dict[str, FailureDiagnostic] = {
                    "malformed_response": "invalid_response",
                    "unsupported_encoding": "invalid_response",
                    "access_failure": "access_failure",
                    "quota": "access_failure",
                    "transport_failure": "transport_failure",
                    "byte_limit": "response_limit",
                }
                raise CallFailure(diagnostics[parsed.value.status])
            return parsed

        result = await self._calls.run(spec, invoke)
        if isinstance(result, Stopped) and failure is not None:
            return BranchSearchFailed(failure.status, result.receipt)
        return result
