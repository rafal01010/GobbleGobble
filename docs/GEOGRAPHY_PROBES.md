# Location and discovery probe reference

GG-002.04 prepares Geoapify location and nearby meal-place requests in `apps/backend/app/providers/geoapify.py`. It passed [Section A feasibility verification at GG-003](FEASIBILITY_GATE.md). These clients do not start an application or run on import. GG-002.05 extends the same client with [walking and driving route probes](ROUTE_PROBES.md). The [bounded probe command](LIVE_PROBES.md) connects these clients for GG-003.

Geoapify is the prepared candidate under the existing [source-use findings](INTEGRATIONS.md#permission-findings). GG-002.04 did not select the final provider or establish account access. A later [bounded experiment](REVIEW_FEASIBILITY.md#licensed-independent-source-validation) successfully used the prepared client for one Cebu City lookup and one nearby meal-place request with the configured key. GG-003 subsequently passed bounded checks in Japan and the Philippines. Configured Google location or discovery providers fail explicitly for the affected operation. There is no provider or fixture fallback.

## Interface and configuration

`GeoapifyClient(calls)` takes one existing `AttemptCalls`. Its read-only `settings` property supplies the same frozen configuration used for receipt identity and timeouts. Location, discovery, and later calls can share that attempt's request allowance and total deadline. The client reads no configuration file.

For real requests, load settings with explicit `mode="live"`. Select `geoapify` for each capability you intend to exercise and provide `GEOAPIFY_API_KEY`. The existing [settings preflight](SETTINGS.md) still requires the other configured fields and live credentials, including OpenAI and the selected route provider. No default selections or new environment variables are added. No local `.env` was created or changed by this task.

`resolve_location(text, limit=5)` returns `Completed` with an ordered tuple of `LocationLead` values, or `Stopped` with the existing safe receipt. All matches remain available to the caller. The client chooses no winner. A valid empty list is a successful empty response, not a transport failure.

`nearby_places(center, radius_m=..., limit=10)` returns an ordered tuple of `MealPlaceLead` values through the same result envelope. `center` is a typed `Point` from the caller or a caller-selected `LocationLead`. Passing a lead preserves its `geoapify_geography` input lineage. Do not discard source lineage when forwarding provider-derived coordinates.

Both lead types preserve the provider place ID, point, optional names and address, and source provenance. Meal leads also preserve returned categories. The provider ID is not a normalized application place identity. Returned source metadata and fixed Geoapify/OpenStreetMap credits travel with the result; they do not enter metadata receipts. Missing source metadata remains explicit.

## Request examples

These interface examples belong inside a later authorized async gate or runner. They have not been executed. They assume configured live settings and contain no credential values.

```python
from app.core.calls import AttemptCalls, Bounds, Completed
from app.core.settings import load_settings
from app.providers.geoapify import GeoapifyClient, Point

settings = load_settings(mode="live")
calls = AttemptCalls(settings, Bounds(max_requests=2, total_seconds=30.0))
client = GeoapifyClient(calls)

locations = await client.resolve_location("Shinjuku Station, Tokyo", limit=5)
if isinstance(locations, Completed):
	matches = locations.value

places = await client.nearby_places(
	Point(latitude=35.6909, longitude=139.7003),
	radius_m=800,
	limit=10,
)
records = [receipt.safe_record() for receipt in calls.receipts]
```

The discovery example uses explicitly supplied coordinates. A caller can instead pass a selected location lead after resolving ambiguity. Only `records` is diagnostic output. Do not print or serialize the lead tuples, requests, raw responses, exceptions, or provenance as diagnostics.

The prepared requests follow the official provider contracts:

| Operation | Endpoint | Parameters |
|---|---|---|
| Forward location lookup | `GET https://api.geoapify.com/v1/geocode/search` | `text`, `format=json`, `limit`, and the configured `apiKey`. |
| Nearby meal-place discovery | `GET https://api.geoapify.com/v2/places` | `categories=catering.restaurant,catering.cafe,catering.fast_food,catering.food_court`, `filter=circle:longitude,latitude,radius`, `bias=proximity:longitude,latitude`, `limit`, and the configured `apiKey`. |

The provider documents [forward geocoding](https://apidocs.geoapify.com/docs/geocoding/) and [nearby Places requests](https://apidocs.geoapify.com/docs/places/). Longitude precedes latitude in the wire parameters. The client keeps that ordering behind named coordinate fields.

## Bounds and diagnostics

Local probe limits are 500 characters of nonblank location text, 1 through 10 location matches, 1 through 20 discovery leads, and a radius of 1 through 5,000 metres. These are implementation limits, not provider maxima or measured product coverage. Coordinates must be finite and within geographic bounds. Boolean counts and invalid inputs are rejected before dispatch.

Each admitted operation performs one cancellable streamed HTTPX GET. Transport retries are zero. Redirects, environment proxies, pagination, polling, background calls, and content tracing are disabled. The callback receives the smaller remaining timeout from `AttemptCalls`; parsing and cleanup stay within that callback. The decoded response body is capped at 1 MiB. Counts above the requested limit and malformed records fail the response instead of silently disappearing.

Preflight and input errors use fixed safe messages. They do not consume a transport allowance. HTTP failures and invalid responses use the existing `Stopped` receipt path. An error response cannot become proof of no nearby places. Usage and cost remain unknown unless actually observed; the client derives neither from result counts. [The call reference](CALLS.md) defines admission, timeout, cancellation, and receipt outcomes.

GET URLs carry credentials and location content. The adapter installs permanent deny filters on the locked direct HTTPX/httpcore source loggers before dispatch. This suppresses their request, connection, response, and error logs across the process, including unrelated calls through those loggers. It does not temporarily change global log levels. Use safe call receipts for diagnostics. New transport dependencies or logger names need review before enabling them.

## Source handling and limitations

Provider values remain ephemeral. The client does not persist raw responses, enable an SDK trace, fetch provenance URLs, or authorize downstream publication or storage. Preserve returned attribution and license information alongside the fixed Geoapify and OpenStreetMap credits. Additional source obligations still apply under the [integration policy](INTEGRATIONS.md#freshness-and-retention).

Discovery yields geographic leads. It does not establish meal service, opening status, menu availability, ratings, review quality, branch/stall normalization, or travel-time eligibility. Radius is a geographic filter, not a walking or driving duration. A capped response may omit suitable outlets. A short or empty response does not establish exhaustive coverage. Local-language and romanized-name quality, worldwide coverage, useful coordinates, and attribution availability remain live observations for GG-003.

Reverse geocoding, destination-time interpretation, broader discovery, and production candidate qualification remain in their scheduled tasks. Routes are documented separately under [GG-002.05](ROUTE_PROBES.md). The [web-research client](WEB_RESEARCH_PROBES.md) is prepared. The [SDK probe](SDK_PROBE.md) and [probe CLI](LIVE_PROBES.md) passed GG-003 bounded feasibility verification.

## Verification boundary

`apps/backend/tests/test_geoapify.py` prepares offline behavioral cases for GG-003 with explicit test mode, a synthetic credential, and an explicit `httpx.MockTransport`. Test mode without that transport cannot send a request. Live mode rejects mock injection. Diagnostic mode cannot dispatch. No fixture is chosen automatically.

GG-003 runs these cases and the existing call-wrapper cases, then exercises authorized real location and discovery calls with the actual settings loader. It must record effective non-secret configuration, provider identity, failures, source credits, timing, and access limitations. This implementation task runs only source review and narrow static/artifact checks. It establishes no measured access, coverage, latency, cost, or provider quality.
