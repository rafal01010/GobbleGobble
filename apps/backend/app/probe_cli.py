from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from types import TracebackType
from typing import Annotated, Literal, Never

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from app.agents.probe import (
    ProbeLimits,
    ProbeStopped,
    ProbeUnavailable,
    SDKProbe,
    SuggestionReady,
)
from app.core.calls import AttemptCalls, Bounds, Completed, Receipt, Stopped
from app.core.settings import (
    ConfigurationError,
    ConfigurationIssue,
    ConfigurationVariable,
    ReviewSettings,
    Settings,
    load_review_settings,
    load_settings,
    review_diagnostics,
    safe_diagnostics,
)
from app.providers.geoapify import GeoapifyClient, Point, RouteAvailable
from app.providers.google_reviews import (
    BranchCandidate,
    BranchQuery,
    GoogleReviewClient,
    MatchedBranch,
    NoConfidentMatch,
    ReviewInputError,
    ReviewLimits,
    ReviewSample,
    ReviewSort,
    match_branch,
)
from app.providers.web_research import EvidenceFound, WebLimits, WebResearchClient

_INPUT_BYTES = 65536
type ScenarioName = Literal[
    "geographic",
    "web",
    "reviews-reviewed",
    "reviews-unreviewed",
    "review-mismatch",
    "review-access-failure",
    "sdk",
    "combined",
]
type BranchName = Literal["reviewed", "unreviewed", "mismatch", "access_failure"]


class _Closed(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", hide_input_in_errors=True
    )


_Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]
_Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)
]
_Feature = Annotated[
    str, StringConstraints(pattern=r"^0x[0-9a-fA-F]{1,16}:0x[0-9a-fA-F]{1,16}$")
]


class QueryInput(_Closed):
    name: _Name = Field(repr=False)
    full_address: _Text | None = Field(default=None, repr=False)
    website: _Text | None = Field(default=None, repr=False)
    feature_id: _Feature | None = Field(default=None, repr=False)


class CandidateInput(QueryInput):
    google_maps_url: _Text | None = Field(default=None, repr=False)
    rating: Annotated[float, Field(ge=1, le=5, allow_inf_nan=False)] | None = None
    reported_total: Annotated[int, Field(ge=0)] | None = None


class BranchInput(_Closed):
    query: QueryInput = Field(repr=False)
    candidates: Annotated[tuple[CandidateInput, ...], Field(max_length=20)] = Field(
        repr=False
    )

    def match(self) -> MatchedBranch | NoConfidentMatch:
        return match_branch(
            BranchQuery(**self.query.model_dump()),
            tuple(BranchCandidate(**item.model_dump()) for item in self.candidates),
        )


class PointInput(_Closed):
    latitude: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)] = Field(
        repr=False
    )
    longitude: Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)] = Field(
        repr=False
    )

    def point(self) -> Point:
        return Point(self.latitude, self.longitude)


class GeographicInput(_Closed):
    location: _Name = Field(repr=False)
    origin: PointInput = Field(repr=False)
    destination: PointInput = Field(repr=False)
    radius_m: Annotated[int, Field(ge=1, le=5000)] = 1000
    location_limit: Annotated[int, Field(ge=1, le=10)] = 5
    place_limit: Annotated[int, Field(ge=1, le=20)] = 10


class InputBounds(_Closed):
    max_requests: Annotated[int, Field(ge=1, le=10)] = 10
    total_seconds: Annotated[float, Field(gt=0, le=300, allow_inf_nan=False)] = 180.0
    max_output_tokens: Annotated[int, Field(ge=64, le=2048)] = 512
    review_pages: Annotated[int, Field(ge=1, le=2)] = 2
    review_count: Annotated[int, Field(ge=1, le=20)] = 20
    review_seconds: Annotated[float, Field(gt=0, le=30, allow_inf_nan=False)] = 30.0

    def reviews(self) -> ReviewLimits:
        return ReviewLimits(
            max_pages=self.review_pages,
            max_reviews=self.review_count,
            max_response_bytes=1048576,
            max_total_bytes=2097152,
            total_seconds=self.review_seconds,
        )


class ProbeInput(_Closed):
    bounds: InputBounds = InputBounds()
    geographic: GeographicInput | None = Field(default=None, repr=False)
    web_query: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
        ]
        | None
    ) = Field(default=None, repr=False)
    reviewed: BranchInput | None = Field(default=None, repr=False)
    unreviewed: BranchInput | None = Field(default=None, repr=False)
    mismatch: BranchInput | None = Field(default=None, repr=False)
    access_failure: BranchInput | None = Field(default=None, repr=False)
    review_sort: ReviewSort = "relevant"


@dataclass(frozen=True)
class Scenario:
    name: ScenarioName
    settings: Literal["general", "reviews", "none"]
    geographic_calls: int = 0
    review_pages: int = 0
    web_requests: int = 0
    sdk_requests: int = 0
    branch: BranchName | None = None

    def inventory(self, bounds: InputBounds) -> dict[str, object]:
        reviews = min(self.review_pages, bounds.review_pages)
        planned = (
            self.geographic_calls + reviews + self.web_requests + self.sdk_requests
        )
        return {
            "scenario": self.name,
            "settings": self.settings,
            "geographic_requests": self.geographic_calls,
            "review_page_requests": reviews,
            "web_requests": self.web_requests,
            "sdk_model_requests": self.sdk_requests,
            "planned_max_requests": planned,
            "effective_max_requests": min(bounds.max_requests, planned),
            "max_hosted_web_calls": self.web_requests,
        }


SCENARIOS = {
    item.name: item
    for item in (
        Scenario("geographic", "general", geographic_calls=4),
        Scenario("web", "general", web_requests=1),
        Scenario("reviews-reviewed", "reviews", review_pages=2, branch="reviewed"),
        Scenario("reviews-unreviewed", "reviews", review_pages=2, branch="unreviewed"),
        Scenario("review-mismatch", "none", branch="mismatch"),
        Scenario(
            "review-access-failure", "reviews", review_pages=2, branch="access_failure"
        ),
        Scenario("sdk", "general", review_pages=2, sdk_requests=3, branch="reviewed"),
        Scenario("combined", "general", 4, 2, 1, 3, "reviewed"),
    )
}


class _InputError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        self.exit(2, "invalid_arguments; use --help\n")


def _emit(record: dict[str, object]) -> None:
    print(json.dumps(record, sort_keys=True), flush=True)


def _read_input(path: Path | None) -> ProbeInput:
    if path is None:
        raise _InputError("input_required")
    try:
        with path.open("rb") as stream:
            body = stream.read(_INPUT_BYTES + 1)
    except OSError:
        raise _InputError("input_unreadable") from None
    if len(body) > _INPUT_BYTES:
        raise _InputError("input_too_large")
    try:
        json.loads(body, object_pairs_hook=_unique_keys)
        return ProbeInput.model_validate_json(body)
    except ValidationError, ValueError, RecursionError:
        raise _InputError("input_invalid") from None


def _unique_keys(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _validate_input(
    scenario: Scenario, inputs: ProbeInput
) -> MatchedBranch | NoConfidentMatch | None:
    if scenario.geographic_calls and inputs.geographic is None:
        raise _InputError("geographic_required")
    if scenario.web_requests and inputs.web_query is None:
        raise _InputError("web_query_required")
    if scenario.branch is None:
        return None
    branch_input = getattr(inputs, scenario.branch)
    if branch_input is None:
        raise _InputError(f"{scenario.branch}_required")
    try:
        match = branch_input.match()
    except ReviewInputError:
        raise _InputError("branch_input_invalid") from None
    if scenario.name != "review-mismatch" and isinstance(match, NoConfidentMatch):
        raise _InputError("branch_not_corroborated")
    if (
        scenario.name == "reviews-unreviewed"
        and isinstance(match, MatchedBranch)
        and match.reported_total != 0
    ):
        raise _InputError("unreviewed_reported_total_zero_required")
    return match


def _settings(scenario: Scenario, *, live: bool) -> Settings | ReviewSettings | None:
    mode: Literal["live", "diagnostic"] = "live" if live else "diagnostic"
    if scenario.settings == "none":
        return None
    settings = (
        load_review_settings(mode=mode)
        if scenario.settings == "reviews"
        else load_settings(mode=mode)
    )
    issues: list[ConfigurationIssue] = []
    if scenario.geographic_calls and isinstance(settings, Settings):
        providers: tuple[tuple[ConfigurationVariable, str], ...] = (
            ("GG_LOCATION_PROVIDER", settings.location_provider),
            ("GG_DISCOVERY_PROVIDER", settings.discovery_provider),
            ("GG_ROUTE_PROVIDER", settings.route_provider),
        )
        for variable, provider in providers:
            if provider != "geoapify":
                issues.append(ConfigurationIssue(variable, "invalid"))
    if scenario.review_pages:
        if settings.review_provider != "google_reviews":
            issues.append(ConfigurationIssue("GG_REVIEW_PROVIDER", "missing"))
        if settings.review_timeout_seconds is None:
            issues.append(ConfigurationIssue("GG_REVIEW_TIMEOUT_SECONDS", "missing"))
    if issues:
        raise ConfigurationError(tuple(issues))
    _emit(
        {
            "configuration": asdict(
                safe_diagnostics(settings)
                if isinstance(settings, Settings)
                else review_diagnostics(settings)
            )
        }
    )
    return settings


def _remember_rejection[T](
    result: Completed[T] | Stopped, rejected: list[Receipt]
) -> None:
    if not result.receipt.admitted:
        rejected.append(result.receipt)


async def _geographic(
    calls: AttemptCalls, inputs: GeographicInput, rejected: list[Receipt]
) -> str:
    client = GeoapifyClient(calls)
    location = await client.resolve_location(
        inputs.location, limit=inputs.location_limit
    )
    _remember_rejection(location, rejected)
    if not isinstance(location, Completed):
        return "geographic_stopped"
    if not location.value:
        return "location_no_leads"
    places = await client.nearby_places(
        inputs.origin.point(), radius_m=inputs.radius_m, limit=inputs.place_limit
    )
    _remember_rejection(places, rejected)
    if not isinstance(places, Completed):
        return "geographic_stopped"
    if not places.value:
        return "discovery_no_leads"
    for mode in ("walk", "drive"):
        route = await client.route(
            inputs.origin.point(), inputs.destination.point(), mode=mode
        )
        _remember_rejection(route, rejected)
        if not isinstance(route, Completed):
            return "geographic_stopped"
        if not isinstance(route.value, RouteAvailable):
            return "route_unavailable"
    return "geographic_ready"


async def _execute(
    scenario: Scenario,
    inputs: ProbeInput,
    settings: Settings | ReviewSettings,
    branch: MatchedBranch | None,
) -> int:
    planned = (
        scenario.geographic_calls
        + min(scenario.review_pages, inputs.bounds.review_pages)
        + scenario.web_requests
        + scenario.sdk_requests
    )
    calls = AttemptCalls(
        settings,
        Bounds(
            max_requests=min(inputs.bounds.max_requests, planned),
            total_seconds=inputs.bounds.total_seconds,
        ),
    )
    rejected: list[Receipt] = []
    results: list[str] = []
    hosted: dict[str, object] | None = None
    success = True
    try:
        if scenario.geographic_calls and inputs.geographic is not None:
            outcome = await _geographic(calls, inputs.geographic, rejected)
            results.append(outcome)
            success = outcome == "geographic_ready"
        sample: ReviewSample | None = None
        if success and scenario.review_pages and branch is not None:
            sample = await GoogleReviewClient(calls).fetch(
                branch, limits=inputs.bounds.reviews(), sort=inputs.review_sort
            )
            rejected.extend(
                receipt for receipt in sample.receipts if not receipt.admitted
            )
            if scenario.name == "review-access-failure":
                success = (
                    sample.termination == "access_failure"
                    and sample.status == "unavailable"
                )
            elif scenario.name == "reviews-unreviewed":
                success = sample.status == "zero_reviews"
            else:
                success = sample.status == "sample" and bool(sample.reviews)
            results.append(f"reviews_{sample.status}")
            results.append(f"review_termination_{sample.termination}")
        if success and scenario.web_requests and inputs.web_query is not None:
            result = await WebResearchClient(
                calls,
                limits=WebLimits(
                    max_output_tokens=inputs.bounds.max_output_tokens, max_tool_calls=1
                ),
            ).search(inputs.web_query, input_sources=())
            _remember_rejection(result, rejected)
            success = isinstance(result, Completed) and isinstance(
                result.value, EvidenceFound
            )
            if isinstance(result, Completed):
                observation = result.value.observation
                results.append(f"web_{observation.outcome}")
                hosted = {
                    "observed_tool_calls": observation.observed_tool_calls,
                    "completed_tool_calls": observation.completed_tool_calls,
                    "completed_search_actions": observation.completed_search_actions,
                    "ignored_tool_calls": observation.ignored_tool_calls,
                }
            else:
                results.append("web_stopped")
        if success and scenario.sdk_requests and sample is not None:
            sdk_result = await SDKProbe(
                calls,
                limits=ProbeLimits(max_output_tokens=inputs.bounds.max_output_tokens),
            ).run(sample)
            success = isinstance(sdk_result, SuggestionReady)
            if isinstance(sdk_result, ProbeStopped):
                if not sdk_result.receipt.admitted:
                    rejected.append(sdk_result.receipt)
                results.append(f"sdk_{sdk_result.receipt.outcome}")
            elif isinstance(sdk_result, ProbeUnavailable):
                results.append(f"sdk_{sdk_result.reason}")
            else:
                results.append("sdk_suggestion")
    finally:
        unique = {
            receipt.local_request_id: receipt
            for receipt in (*calls.receipts, *rejected)
        }
        for receipt in unique.values():
            _emit({"receipt": receipt.safe_record()})
    status = (
        "observed_expected_failure"
        if success and scenario.name == "review-access-failure"
        else "passed"
        if success
        else "scenario_failed"
    )
    _emit({"status": status, "results": results, "hosted_web": hosted})
    return 0 if success else 4


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        description="Bounded external capability probes. No transmission without --live."
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="show scenario inventory without reading input or configuration",
    )
    parser.add_argument("--scenario", choices=tuple(SCENARIOS), default="combined")
    parser.add_argument(
        "--input", type=Path, help="closed JSON input file, at most 65536 bytes"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="permit real requests after all local preflight checks",
    )
    args = parser.parse_args(argv)
    if args.list:
        for item in SCENARIOS.values():
            _emit(item.inventory(InputBounds()))
        return 0
    scenario = SCENARIOS[args.scenario]
    logging.disable(logging.CRITICAL)
    try:
        inputs = _read_input(args.input)
        _emit(
            {
                "inventory": scenario.inventory(inputs.bounds),
                "bounds": inputs.bounds.model_dump(),
                "review_max_response_bytes": 1048576,
                "review_max_total_bytes": 2097152,
            }
        )
        branch = _validate_input(scenario, inputs)
        settings = _settings(scenario, live=args.live)
        if not args.live:
            _emit(
                {
                    "status": "diagnostic_ready",
                    "transmission": "disabled",
                    "credentials": "not_validated",
                }
            )
            return 0
        if scenario.name == "review-mismatch":
            passed = isinstance(branch, NoConfidentMatch)
            _emit(
                {
                    "status": "observed_expected_failure"
                    if passed
                    else "scenario_failed",
                    "result": branch.reason
                    if isinstance(branch, NoConfidentMatch)
                    else "unexpected_match",
                }
            )
            return 0 if passed else 4
        if settings is None or isinstance(branch, NoConfidentMatch):
            raise _InputError("input_invalid")
        return asyncio.run(_execute(scenario, inputs, settings, branch))
    except _InputError as error:
        _emit({"error": error.code})
        return 2
    except ConfigurationError as error:
        _emit(
            {
                "error": "configuration_invalid",
                "issues": [asdict(issue) for issue in error.issues],
            }
        )
        return 3
    except KeyboardInterrupt:
        _emit({"error": "interrupted"})
        return 130


def _unhandled_error(
    _kind: type[BaseException], _error: BaseException, _traceback: TracebackType | None
) -> None:
    _emit({"error": "probe_failed"})


if __name__ == "__main__":
    sys.excepthook = _unhandled_error
    sys.exit(main())
