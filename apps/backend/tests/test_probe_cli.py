import asyncio
import json
import logging
import os
import signal
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest

from app import probe_cli
from app.agents.probe import FoodSuggestion, ProbeUnavailable, SDKProbe, SuggestionReady
from app.core.settings import (
    Mode,
    ReviewSettings,
    Settings,
    load_review_settings,
    load_settings,
)
from app.providers.google_reviews import ReviewSample

_PRIVATE = "private CLI input & credential"
_ENV = {
    "GG_LOCATION_PROVIDER": "geoapify",
    "GG_DISCOVERY_PROVIDER": "geoapify",
    "GG_ROUTE_PROVIDER": "geoapify",
    "GG_WEB_PROVIDER": "openai",
    "GG_REVIEW_PROVIDER": "google_reviews",
    "GG_REVIEW_TIMEOUT_SECONDS": "1",
    "GG_OWNER_MODEL": "gpt-4o",
    "GG_RESEARCH_MODEL": "gpt-4o-mini",
    "GG_GEOGRAPHIC_TIMEOUT_SECONDS": "1",
    "GG_MODEL_TIMEOUT_SECONDS": "1",
    "OPENAI_API_KEY": _PRIVATE,
    "GEOAPIFY_API_KEY": _PRIVATE,
}


@pytest.fixture(autouse=True)
def isolated_cli(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in os.environ:
        if name.startswith("GG_") or name in {
            "OPENAI_API_KEY",
            "GOOGLE_MAPS_API_KEY",
            "GEOAPIFY_API_KEY",
        }:
            monkeypatch.delenv(name)
    for name, value in _ENV.items():
        monkeypatch.setenv(name, value)

    def general(*, mode: Mode) -> Settings:
        return load_settings(mode=mode, env_file=None)

    def reviews(*, mode: Mode) -> ReviewSettings:
        return load_review_settings(mode=mode, env_file=None)

    def forbidden(_request: object) -> Any:
        pytest.fail("CLI test attempted an unconfigured request")

    monkeypatch.setattr(probe_cli, "load_settings", general)
    monkeypatch.setattr(probe_cli, "load_review_settings", reviews)
    monkeypatch.setattr(
        httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(forbidden)
    )
    monkeypatch.setattr(
        httpx2, "AsyncHTTPTransport", lambda **_: httpx2.MockTransport(forbidden)
    )
    previous_logging = logging.root.manager.disable
    yield
    logging.disable(previous_logging)


def input_file(tmp_path: Path, **changes: Any) -> Path:
    branch = {
        "query": {"name": _PRIVATE, "full_address": _PRIVATE},
        "candidates": [
            {
                "name": _PRIVATE,
                "full_address": _PRIVATE,
                "feature_id": "0x1:0x2",
                "reported_total": 0,
            }
        ],
    }
    payload = {
        "geographic": {
            "location": _PRIVATE,
            "origin": {"latitude": 14.5, "longitude": 121.0},
            "destination": {"latitude": 14.6, "longitude": 121.1},
        },
        "web_query": _PRIVATE,
        "reviewed": branch,
        "unreviewed": branch,
        "mismatch": {"query": branch["query"], "candidates": []},
        "access_failure": branch,
    }
    path = tmp_path / "private-input.json"
    path.write_text(json.dumps(payload | changes))
    return path


def output(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    captured = capsys.readouterr()
    assert captured.err == ""
    for private in (
        _PRIVATE,
        "private-input.json",
        "private provider body",
        "private malformed body",
        "private-review",
        "The chicken was delicious.",
        "Private cited evidence.",
    ):
        assert private not in captured.out
    return [json.loads(line) for line in captured.out.splitlines()]


def geographic_response(request: httpx.Request) -> httpx.Response:
    body: dict[str, Any]
    if request.url.path == "/v1/geocode/search":
        body = {"results": [{"place_id": _PRIVATE, "lat": 14.5, "lon": 121.0}]}
    elif request.url.path == "/v2/places":
        body = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [121.0, 14.5]},
                    "properties": {
                        "place_id": _PRIVATE,
                        "lat": 14.5,
                        "lon": 121.0,
                        "categories": ["catering.restaurant"],
                    },
                }
            ],
        }
    else:
        assert request.url.path == "/v1/routing"
        body = {
            "results": [{"time": 120.0, "distance": 300.0, "distance_units": "meters"}]
        }
    return httpx.Response(200, json=body)


def review_body(*, with_review: bool = False) -> bytes:
    rows: list[list[Any]] = []
    if with_review:
        row: list[Any] = [None] * 30
        row[1], row[5], row[27] = 5, "private-review", "The chicken was delicious."
        row[29] = [
            "Google",
            "https://www.google.com/maps/reviews/?reviewid=private-review",
        ]
        rows.append(row)
    payload: list[Any] = [None] * 11
    payload[10] = [None, None, rows, None, None, None, None]
    return b")]}'\n" + json.dumps([None, payload]).encode()


def test_list_skips_input_and_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GG_LOCATION_PROVIDER", _PRIVATE)
    assert probe_cli.main(["--list", "--input", "/missing/private-input"]) == 0
    records = output(capsys)
    assert [record["scenario"] for record in records] == [
        "geographic",
        "web",
        "reviews-reviewed",
        "reviews-unreviewed",
        "review-mismatch",
        "review-access-failure",
        "sdk",
        "combined",
    ]
    assert records[-1]["planned_max_requests"] == 10


@pytest.mark.parametrize(
    "body,expected",
    [
        (b'{"web_query":"first","web_query":"second"}', "input_invalid"),
        (b'{"bounds":{"max_requests":1,"max_requests":2}}', "input_invalid"),
        (
            b'{"web_query":"private CLI input & credential","unknown":1}',
            "input_invalid",
        ),
        (b'{"bounds":{"max_requests":true}}', "input_invalid"),
        (b'{"bounds":{"total_seconds":NaN}}', "input_invalid"),
        (b"\xff", "input_invalid"),
        (b"x" * 65537, "input_too_large"),
    ],
)
def test_invalid_input_is_closed_and_redacted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], body: bytes, expected: str
) -> None:
    path = tmp_path / "private-input.json"
    path.write_bytes(body)
    assert probe_cli.main(["--scenario", "web", "--input", str(path)]) == 2
    assert output(capsys) == [{"error": expected}]


@pytest.mark.parametrize("missing_path", [False, True])
def test_missing_input_has_safe_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], missing_path: bool
) -> None:
    args = ["--input", str(tmp_path / "private-missing.json")] if missing_path else []
    assert probe_cli.main(args) == 2
    assert output(capsys) == [
        {"error": "input_unreadable" if missing_path else "input_required"}
    ]


def test_diagnostic_overrides_environment_live_mode_and_reports_effective_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GG_MODE", "live")
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.delenv("GEOAPIFY_API_KEY")
    monkeypatch.setenv("GG_MODEL_TIMEOUT_SECONDS", "0.125")
    path = input_file(tmp_path, bounds={"max_requests": 2, "review_pages": 1})
    assert probe_cli.main(["--input", str(path)]) == 0
    records = output(capsys)
    assert records[0]["inventory"]["planned_max_requests"] == 9
    assert records[0]["inventory"]["effective_max_requests"] == 2
    assert records[1]["configuration"]["mode"] == "diagnostic"
    assert records[1]["configuration"]["model_timeout_seconds"] == 0.125
    assert records[1]["configuration"]["openai_key_present"] is False
    assert records[-1] == {
        "status": "diagnostic_ready",
        "transmission": "disabled",
        "credentials": "not_validated",
    }


def test_live_requires_credentials_despite_environment_diagnostic_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GG_MODE", "diagnostic")
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.delenv("GEOAPIFY_API_KEY")
    path = input_file(tmp_path)
    assert (
        probe_cli.main(["--scenario", "geographic", "--input", str(path), "--live"])
        == 3
    )
    assert output(capsys)[-1] == {
        "error": "configuration_invalid",
        "issues": [
            {"variable": "OPENAI_API_KEY", "code": "missing"},
            {"variable": "GEOAPIFY_API_KEY", "code": "missing"},
        ],
    }


def test_invalid_configuration_value_is_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GG_MODEL_TIMEOUT_SECONDS", _PRIVATE)
    path = input_file(tmp_path)
    assert probe_cli.main(["--scenario", "web", "--input", str(path)]) == 3
    assert output(capsys)[-1] == {
        "error": "configuration_invalid",
        "issues": [{"variable": "GG_MODEL_TIMEOUT_SECONDS", "code": "invalid"}],
    }


@pytest.mark.parametrize(
    "empty,exit_code,result",
    [(False, 0, "geographic_ready"), (True, 4, "location_no_leads")],
)
def test_geographic_exit_reflects_adapter_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    empty: bool,
    exit_code: int,
    result: str,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return (
            httpx.Response(200, json={"results": []})
            if empty
            else geographic_response(request)
        )

    monkeypatch.setattr(
        httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(respond)
    )
    path = input_file(tmp_path)
    assert (
        probe_cli.main(["--scenario", "geographic", "--input", str(path), "--live"])
        == exit_code
    )
    records = output(capsys)
    assert records[1]["configuration"]["mode"] == "live"
    assert records[-1] == {
        "status": "scenario_failed" if empty else "passed",
        "results": [result],
        "hosted_web": None,
    }
    assert [
        record["receipt"]["capability"] for record in records if "receipt" in record
    ] == (["location"] if empty else ["location", "discovery", "route", "route"])
    assert len(requests) == (1 if empty else 4)


@pytest.mark.parametrize("matched,exit_code", [(False, 0), (True, 4)])
def test_mismatch_scenario_requires_a_real_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], matched: bool, exit_code: int
) -> None:
    path = input_file(tmp_path)
    if matched:
        payload = json.loads(path.read_text())
        payload["mismatch"] = payload["reviewed"]
        path.write_text(json.dumps(payload))
    assert (
        probe_cli.main(
            ["--scenario", "review-mismatch", "--input", str(path), "--live"]
        )
        == exit_code
    )
    assert output(capsys)[-1] == {
        "status": "scenario_failed" if matched else "observed_expected_failure",
        "result": "unexpected_match" if matched else "no_candidates",
    }


@pytest.mark.parametrize(
    "scenario,status,body,exit_code,expected_status,expected_result",
    [
        ("reviews-unreviewed", 200, review_body(), 0, "passed", "reviews_zero_reviews"),
        (
            "reviews-unreviewed",
            200,
            review_body(with_review=True),
            4,
            "scenario_failed",
            "reviews_partial",
        ),
        (
            "review-access-failure",
            403,
            b"private provider body",
            0,
            "observed_expected_failure",
            "reviews_unavailable",
        ),
        (
            "review-access-failure",
            200,
            b"private malformed body",
            4,
            "scenario_failed",
            "reviews_unavailable",
        ),
    ],
)
def test_review_exit_distinguishes_expected_failure_from_other_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    scenario: str,
    status: int,
    body: bytes,
    exit_code: int,
    expected_status: str,
    expected_result: str,
) -> None:
    monkeypatch.setenv("GG_LOCATION_PROVIDER", _PRIVATE)
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.delenv("GEOAPIFY_API_KEY")
    monkeypatch.setattr(
        httpx,
        "AsyncHTTPTransport",
        lambda **_: httpx.MockTransport(
            lambda request: httpx.Response(status, content=body)
        ),
    )
    path = input_file(tmp_path)
    assert (
        probe_cli.main(["--scenario", scenario, "--input", str(path), "--live"])
        == exit_code
    )
    records = output(capsys)
    assert records[-1]["status"] == expected_status
    assert records[-1]["results"][0] == expected_result
    receipts = [record["receipt"] for record in records if "receipt" in record]
    assert len(receipts) == 1
    assert receipts[0]["capability"] == "reviews"
    assert receipts[0]["admitted"] is True


@pytest.mark.parametrize("evidence", [False, True])
def test_web_exit_requires_supported_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    evidence: bool,
) -> None:
    sent: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "id": "private-search",
                        "type": "web_search_call",
                        "status": "completed",
                        "action": {"type": "search", "queries": [_PRIVATE]},
                    },
                    {
                        "id": "private-web-message",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Private cited evidence."
                                if evidence
                                else "NO_SUPPORTED_EVIDENCE",
                                "annotations": [
                                    {
                                        "type": "url_citation",
                                        "title": _PRIVATE,
                                        "url": "https://private.invalid/evidence",
                                        "start_index": 0,
                                        "end_index": 7,
                                    }
                                ]
                                if evidence
                                else [],
                            }
                        ],
                    },
                ],
            },
        )

    monkeypatch.setattr(
        httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(respond)
    )
    path = input_file(tmp_path, bounds={"max_output_tokens": 128})
    assert probe_cli.main(["--scenario", "web", "--input", str(path), "--live"]) == (
        0 if evidence else 4
    )
    records = output(capsys)
    assert records[-1] == {
        "status": "passed" if evidence else "scenario_failed",
        "results": ["web_evidence" if evidence else "web_no_evidence"],
        "hosted_web": {
            "observed_tool_calls": 1,
            "completed_tool_calls": 1,
            "completed_search_actions": 1,
            "ignored_tool_calls": 0,
        },
    }
    assert len(sent) == 1
    assert sent[0]["max_tool_calls"] == 1
    assert sent[0]["max_output_tokens"] == 128


@pytest.mark.parametrize("unavailable", [False, True])
def test_sdk_exit_uses_semantic_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    unavailable: bool,
) -> None:
    async def controlled_sdk_result(
        self: SDKProbe, sample: ReviewSample
    ) -> SuggestionReady | ProbeUnavailable:
        if unavailable:
            return ProbeUnavailable("no_food_passages", ())
        return SuggestionReady(
            FoodSuggestion(
                food="chicken", passage_label="p0", quote="The chicken was delicious."
            ),
            sample.branch,
            (),
        )

    monkeypatch.setattr(SDKProbe, "run", controlled_sdk_result)
    monkeypatch.setattr(
        httpx,
        "AsyncHTTPTransport",
        lambda **_: httpx.MockTransport(
            lambda request: httpx.Response(200, content=review_body(with_review=True))
        ),
    )
    path = input_file(tmp_path)
    payload = json.loads(path.read_text())
    payload["reviewed"]["candidates"][0]["reported_total"] = 1
    path.write_text(json.dumps(payload))
    assert probe_cli.main(["--scenario", "sdk", "--input", str(path), "--live"]) == (
        4 if unavailable else 0
    )
    records = output(capsys)
    assert records[-1] == {
        "status": "scenario_failed" if unavailable else "passed",
        "results": [
            "reviews_sample",
            "review_termination_source_exhausted",
            "sdk_no_food_passages" if unavailable else "sdk_suggestion",
        ],
        "hosted_web": None,
    }
    assert [
        record["receipt"]["capability"] for record in records if "receipt" in record
    ] == ["reviews"]


@pytest.mark.parametrize("expired", [False, True])
def test_combined_shares_request_limit_and_deadline_across_providers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    expired: bool,
) -> None:
    hosts: list[str] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == "api.geoapify.com":
            return geographic_response(request)
        assert request.url.host == "www.google.com"
        await asyncio.sleep(10)
        return httpx.Response(200, content=review_body())

    monkeypatch.setattr(
        httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(respond)
    )
    path = input_file(
        tmp_path,
        bounds={
            "max_requests": 10 if expired else 4,
            "total_seconds": 0.2 if expired else 1.0,
        },
    )
    assert probe_cli.main(["--input", str(path), "--live"]) == 4
    records = output(capsys)
    receipts = [record["receipt"] for record in records if "receipt" in record]
    assert [receipt["capability"] for receipt in receipts] == [
        "location",
        "discovery",
        "route",
        "route",
        "reviews",
    ]
    assert [receipt["outcome"] for receipt in receipts] == ["success"] * 4 + [
        "attempt_timeout" if expired else "request_limit"
    ]
    assert receipts[-1]["admitted"] is expired
    assert len({receipt["local_request_id"] for receipt in receipts}) == 5
    assert hosts == ["api.geoapify.com"] * 4 + (["www.google.com"] if expired else [])
    assert records[-1]["status"] == "scenario_failed"
    assert records[-1]["results"][0:2] == ["geographic_ready", "reviews_unavailable"]


def test_interrupt_emits_cancelled_receipt_before_exit_130(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def interrupt(request: httpx.Request) -> httpx.Response:
        signal.raise_signal(signal.SIGINT)
        await asyncio.sleep(0)
        return geographic_response(request)

    monkeypatch.setattr(
        httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(interrupt)
    )
    path = input_file(tmp_path)
    assert (
        probe_cli.main(["--scenario", "geographic", "--input", str(path), "--live"])
        == 130
    )
    records = output(capsys)
    assert records[-1] == {"error": "interrupted"}
    receipts = [record["receipt"] for record in records if "receipt" in record]
    assert len(receipts) == 1
    assert receipts[0]["outcome"] == "cancelled"
    assert receipts[0]["admitted"] is True
    assert receipts[0]["capability"] == "location"
