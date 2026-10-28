import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.api.application import create_app, location_resolver
from app.core.calls import AttemptCalls, Bounds
from app.core.settings import ApiSettings, DatabaseSettings, Settings
from app.db.migrations import migrate
from app.location import LocationResolver
from app.providers.geoapify import GeoapifyClient
from app.schemas.operations import ResolveLocationResponse

ORIGIN = "http://frontend.test"


def lead(
    identifier: str = "landmark-1", *, result_type: str = "amenity"
) -> dict[str, object]:
    return {
        "place_id": identifier,
        "lat": 14.584,
        "lon": 120.981,
        "name": "Illustrative landmark",
        "formatted": f"Illustrative landmark {identifier}, Manila, Philippines",
        "result_type": result_type,
        "timezone": {"name": "Asia/Manila"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("location", "results", "expected_status"),
    [
        (
            {"kind": "current", "coordinates": {"latitude": 14.6, "longitude": 121.0}},
            [lead()],
            "resolved",
        ),
        ({"kind": "chosen", "query": "14.6, 121.0"}, [lead()], "resolved"),
        ({"kind": "chosen", "query": "Rizal Monument Manila"}, [lead()], "resolved"),
        (
            {"kind": "chosen", "query": "Central Station"},
            [lead("station-a"), lead("station-b")],
            "choices",
        ),
        (
            {"kind": "chosen", "query": "Philippines"},
            [lead(result_type="country")],
            "choices",
        ),
        ({"kind": "chosen", "query": "Unmatched landmark"}, [], "no_match"),
    ],
    ids=(
        "current-point",
        "chosen-coordinates",
        "english-landmark",
        "ambiguous",
        "country",
        "no-match",
    ),
)
async def test_resolution_http_examples(
    tmp_path: Path,
    location: dict[str, object],
    results: list[dict[str, object]],
    expected_status: str,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"results": results})

    async def resolver() -> LocationResolver:
        settings = Settings.model_construct(
            mode="test",
            location_provider="geoapify",
            discovery_provider="geoapify",
            route_provider="geoapify",
            web_provider="openai",
            owner_model="unused-owner",
            research_model="unused-research",
            geographic_timeout_seconds=1.0,
            model_timeout_seconds=1.0,
            geoapify_api_key=SecretStr("synthetic-location-key"),
        )
        calls = AttemptCalls(settings, Bounds(max_requests=1, total_seconds=1.0))
        return LocationResolver(
            GeoapifyClient(calls, test_transport=httpx.MockTransport(respond))
        )

    database = DatabaseSettings(database_path=tmp_path / "location.sqlite3")
    migrate(database)
    app = create_app(
        api_settings=ApiSettings(trusted_frontend_origins=(ORIGIN,)),
        database_settings=database,
    )
    app.dependency_overrides[location_resolver] = resolver
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as client:
        await client.post("/api/session", headers={"Origin": ORIGIN})
        response = await client.post(
            "/api/locations/resolve",
            headers={"Origin": ORIGIN},
            json={"location": location},
        )
        if location.get("query") == "Central Station":
            selected_origin = response.json()["outcome"]["choices"][0]["origin"]
            started = await client.post(
                "/api/runs",
                headers={"Origin": ORIGIN},
                json={
                    "command_id": "chosen-origin-example",
                    "request": {
                        "origin": selected_origin,
                        "eat_now_at": "2026-10-28T12:00:00+08:00",
                    },
                },
            )
            assert started.status_code == 202
            assert started.json()["command"]["run"]["state"]["status"] == "queued"
    assert response.status_code == 200
    outcome = ResolveLocationResponse.model_validate_json(response.content).outcome
    assert outcome.status == expected_status
    assert len(requests) == 1
    if location["kind"] == "current" or location.get("query") == "14.6, 121.0":
        assert outcome.status == "resolved"
        assert outcome.origin.location.coordinates.model_dump() == {
            "latitude": 14.6,
            "longitude": 121.0,
        }
        assert outcome.origin.kind == location["kind"]
        assert outcome.origin.location.name == "14.6, 121.0"
        assert requests[0].url.path == "/v1/geocode/reverse"
    elif location.get("query") == "Philippines":
        assert outcome.model_dump(mode="json") == {
            "status": "choices",
            "message": "Choose a specific point or narrow the location query.",
            "choices": [
                {
                    "kind": "refine_query",
                    "label": "Illustrative landmark landmark-1, Manila, Philippines",
                    "reason": "broad_area",
                }
            ],
        }
    elif location.get("query") == "Central Station":
        assert outcome.status == "choices"
        assert len(outcome.choices) == 2
        assert [
            choice.origin.location.provider_refs[0].provider_id
            for choice in outcome.choices
            if choice.kind == "point"
        ] == ["station-a", "station-b"]
    elif expected_status == "resolved":
        assert outcome.status == "resolved"
        assert outcome.origin.location.timezone == "Asia/Manila"
        assert requests[0].url.params["text"] == "Rizal Monument Manila"
    assert "synthetic-location-key" not in json.dumps(response.json())
