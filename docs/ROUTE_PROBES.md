# Walking and driving route probes

GG-002.05 prepares point-to-point route requests in `apps/backend/app/providers/geoapify.py`. Implementation awaits section verification at GG-003. No route has been measured by this task. The probe command remains GG-002.08.

## Interface and configuration

`GeoapifyClient.route(origin, destination, mode=...)` uses the existing `AttemptCalls` supplied to the client. Each endpoint accepts a caller-supplied `Point`, a caller-selected `LocationLead`, or a caller-selected `MealPlaceLead`. The mode is explicitly `walk` or `drive`. Forward provider leads intact so both endpoint provenance and `geoapify_geography` input lineage survive. Two independent caller points declare no imported source.

Select `GG_ROUTE_PROVIDER=geoapify` and supply `GEOAPIFY_API_KEY` for the prepared routing implementation. The [settings preflight](SETTINGS.md) still requires all configured fields and the other selected live credentials. Unsupported Google routing selections raise the fixed `unsupported_provider` preflight error. No automatic provider or fixture fallback exists. This task creates no local `.env`, selects no current configuration, and adds no environment variables or dependencies.

The result is `Completed[RouteAvailable | RouteUnavailable] | Stopped`.

| Value | Meaning |
|---|---|
| `RouteAvailable` | One validated provider route with `duration_seconds`, `distance_m`, requested `mode`, original `origin` and `destination`, and `provenance`. |
| `RouteUnavailable` | A successful response contains an empty `results` list. It carries the requested endpoints, mode, provenance, and fixed reason `provider_returned_no_routes`. It has no duration or distance. |
| `Stopped` | No usable route answer was obtained because the request failed or a call bound rejected it. Inspect only `receipt.safe_record()` for diagnostics. |

Available estimates carry the fixed basis `no_live_traffic`. Driving explicitly requests free-flow traffic. Walking does not send a traffic option. Neither provides live traffic, an arrival guarantee, parking time, or a door-to-door access guarantee. The receipt's UTC `started_at` records when the request began. No factual freshness window is established yet.

Route provenance supplies Geoapify and OpenStreetMap credits and preserves optional returned route datasource metadata. The original leads retain their provider place IDs and datasource metadata. No route ID is invented. Keep attribution with any later permitted display or reuse. Only safe receipt projections are diagnostics. Route values, coordinates, source metadata, URLs, raw responses, and exception text remain ephemeral content under the [source-use findings](INTEGRATIONS.md#permission-findings).

## Request and response contract

The request follows the [official Geoapify routing reference](https://apidocs.geoapify.com/docs/routing/), inspected on 2026-10-23.

| Field | Wire value |
|---|---|
| Method and endpoint | `GET https://api.geoapify.com/v1/routing` |
| `waypoints` | `origin_latitude,origin_longitude|destination_latitude,destination_longitude` |
| `mode` | `walk` or `drive` |
| `format` | `json` |
| `units` | `metric` |
| `traffic` | `free_flow` for driving. Omitted for walking. |
| `apiKey` | The configured credential. Omitted from diagnostics. |

Routing waypoints use latitude before longitude. This differs from the longitude-first Places filter. The client owns wire ordering.

A JSON response requires a `results` list containing zero or one route. A route requires finite, nonnegative `time` and `distance`, and `distance_units="Meters"`. Time maps directly to seconds and distance maps directly to metres. Extra geometry, legs, instructions, and unrelated fields are ignored. Missing metrics, wrong units, invalid numbers, malformed JSON, or multiple unexpected routes fail validation. A valid provider-supplied zero remains zero. No distance or duration is calculated locally.

These synthetic contract examples contain no actual route observations:

```json
{"results": [{"time": 540.5, "distance": 680.0, "distance_units": "Meters"}]}
```

```json
{"results": []}
```

## Failure mapping and bounds

| Observation | Outcome |
|---|---|
| Valid nonempty success response | `Completed(RouteAvailable(...))` with a successful call receipt. |
| Valid empty success response | `Completed(RouteUnavailable(...))` with a successful call receipt. This establishes only that the response returned no route. |
| Any non-2xx response, including 400 and 404 | `Stopped` with `outcome="failed"` and `diagnostic="unknown_failure"`. No error body or prose is interpreted as proof of unreachability. |
| Malformed or incompatible successful response | `Stopped` with `outcome="failed"` and `diagnostic="invalid_response"`. |
| Transport failure | `Stopped` with the existing fixed failure diagnostic. |
| Deadline or exhausted request allowance | Existing `call_timeout`, `attempt_timeout`, or `request_limit` receipt. |
| Cancellation | An admitted cancellation receipt is recorded and cancellation propagates to the caller. |
| Invalid endpoint or mode | Fixed `GeoapifyInputError` before admission. |
| Missing credential, unsupported provider, or incompatible execution mode | Fixed `GeoapifyPreflightError` before admission. |

The provider documents an error envelope with `statusCode`, `error`, and `message`. It does not define a stable unavailable-route code in the inspected reference. An HTTP error therefore remains a failed request. Empty results do not establish that every provider or travel option is unavailable. Neither outcome becomes a zero-duration route or a no-matches food result.

Each operation uses the [existing transport controls](GEOGRAPHY_PROBES.md#bounds-and-diagnostics). One admitted operation makes one cancellable streamed GET, with retries, redirects, and environment proxies disabled. The decoded response cap is 1 MiB. The geographic timeout is capped by the shared attempt deadline. Walking and driving consume separate request admissions. Usage and USD cost remain unknown unless observed.

Real transport requires explicit live mode. Test mode requires an injected `httpx.MockTransport` and a synthetic key. Diagnostic mode cannot transmit, and live mode rejects test transport. The existing permanent HTTPX/httpcore source-log filters apply to routing too. No content tracing or payload persistence is added.

## Unexecuted caller example

This example belongs inside an authorized async gate or runner. It assumes real configured settings and contains no credential values. It has not been executed.

```python
from app.core.calls import AttemptCalls, Bounds, Completed
from app.core.settings import load_settings
from app.providers.geoapify import GeoapifyClient, Point, RouteAvailable

settings = load_settings(mode="live")
calls = AttemptCalls(settings, Bounds(max_requests=2, total_seconds=30.0))
client = GeoapifyClient(calls)
origin = Point(latitude=35.6909, longitude=139.7003)
destination = Point(latitude=35.6940, longitude=139.7030)

walking = await client.route(origin, destination, mode="walk")
driving = await client.route(origin, destination, mode="drive")
if isinstance(walking, Completed) and isinstance(walking.value, RouteAvailable):
	walking_seconds = walking.value.duration_seconds
records = [receipt.safe_record() for receipt in calls.receipts]
```

The coordinates are caller inputs, not evidence of available routes. Only `records` is diagnostic output. A later caller can pass selected leads instead of independent coordinates. Qualification against adjustable walking minutes or a 30-minute drive belongs to GG-008.05 and later product integration.

## Verification boundary

Prepared controlled cases live in `apps/backend/tests/test_geoapify_routes.py`. Their execution, a real walking route, a real driving route, a controlled unavailable response, actual mode-specific estimates, source attribution, access, timing, and failure/redaction controls remain GG-003. The existing location/discovery and bounded-call cases also remain at that gate. This child does not run behavioral tests, broad checks, or live provider/model calls. Worldwide coverage and practical route quality remain unmeasured.
