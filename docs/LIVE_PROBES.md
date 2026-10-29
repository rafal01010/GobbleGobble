# Run bounded research probes

Use `scripts/local/probe-live.sh` to exercise an individual research capability before connecting it to the application. The tool checks geography, reviews, web research and SDK orchestration. It is not the normal restaurant-search interface, and a successful scenario does not prove recommendation quality or production readiness.

## Prepare and run

Initialize the backend using [local setup](SETUP.md), then run the wrapper from the repository root. It uses `apps/backend/.venv/bin/python`, installs nothing and preserves your working directory for relative input paths.

Help and the scenario inventory need no configuration or input file:

```sh
scripts/local/probe-live.sh --help
scripts/local/probe-live.sh --list
```

Save a UTF-8 JSON input document outside tracked source files. First validate the selected input and local configuration without transmitting:

```sh
scripts/local/probe-live.sh --scenario combined --input /private/tmp/gobble-probe.json
```

A successful preflight prints `diagnostic_ready`. It establishes configuration shape and credential presence, not working credentials or service access. For a deliberate service check, add the explicit live opt-in:

```sh
scripts/local/probe-live.sh --scenario combined --input /private/tmp/gobble-probe.json --live
```

`combined` is the default scenario. There is no fixture fallback, automatic rerun, model substitution or hidden retry. Live calls may incur costs, and local cancellation cannot retract work already dispatched to a provider. Use [settings](SETTINGS.md) for configuration and [integration policy](INTEGRATIONS.md) for source-use and retention requirements.

## Check the production research path

Run the offline research checks from the repository root:

```sh
scripts/local/verify-research.sh
```

The following command checks local configuration without sending a request:

```sh
apps/backend/.venv/bin/python scripts/local/verify-research-live.py
```

Add `--live` to run the bounded Japan and Philippines checks against the configured services. The command requires the configured research model to be `gpt-6-luna` and the review provider to be `google_reviews`. Each case has an eight-request and 120-second limit. The specialist permits up to three model turns with at most 1,024 output tokens per turn. A location without one confident matching target stops that case. A failed branch match stops review retrieval and model calls for that case.

The command reports source-safe receipts, counts, and available usage. It does not save review bodies or model-generated review text. A returned model proposal still needs a source-support assessment. The command does not establish opening hours or complete recommendation quality. See [settings](SETTINGS.md) for service configuration and [source-use policy](INTEGRATIONS.md) for retention limits.

## Choose a scenario

Each invocation selects one scenario. The request inventory below is reduced further by the supplied shared allowance.

| Scenario | Maximum application requests | What a passing scenario establishes |
|---|---|---|
| `geographic` | 4 | Location and meal-place leads exist, and walking/driving estimates are available for the supplied points. |
| `web` | 1 | A completed hosted search returned cited evidence. |
| `reviews-reviewed` | 2 page GETs | The matched branch returned a nonempty `sample`. |
| `reviews-unreviewed` | 2 page GETs | Supplied branch metadata reports integer zero reviews and the returned response corroborates `zero_reviews`. |
| `review-mismatch` | 0 | Supplied query/candidates produce `NoConfidentMatch`. No settings are loaded. |
| `review-access-failure` | 2 page GETs | A corroborated branch actually returns `access_failure` with `unavailable` status. |
| `sdk` | 2 review page GETs and 3 model requests | A fetched sample supports nested SDK execution and a locally validated food suggestion. |
| `combined` | 4 geographic, 2 review, 1 web and 3 SDK model requests | The positive capabilities above succeed using the `reviewed` branch. |

`combined` shares at most ten requests and one monotonic deadline. Every review page and SDK model request consumes its own slot. The web request permits one executed hosted-tool call, reported separately from application transmissions. Returned ignored tool attempts do not count as completed research.

Execution stops after a failed capability. Separate negative scenarios need separate invocations; they have no shared cross-process budget. `review-access-failure` passes only for its actual expected source failure. A successful fetch, transport error, malformed response, empty sample, timeout or exhausted allowance does not satisfy it. The tool never fabricates a failure or target to make a scenario pass.

## Supply input

[`ProbeInput`](../apps/backend/app/probe_cli.py) is the authoritative closed schema. Files are limited to 65,536 bytes. Duplicate keys, unknown fields, invalid types, nonfinite numbers and out-of-range bounds fail before transmission. Only the selected scenario's fields are required, but any supplied optional field is validated.

| Top-level field | Used by | Content |
|---|---|---|
| `geographic` | `geographic`, `combined` | `location`, explicit `origin` and `destination`, optional discovery limits. |
| `web_query` | `web`, `combined` | User-authored question, 1 through 2,000 characters. |
| `reviewed` | `reviews-reviewed`, `sdk`, `combined` | Corroborated branch `query` and `candidates`. |
| `unreviewed` | `reviews-unreviewed` | Branch input with observed candidate `reported_total` equal to integer `0`. |
| `mismatch` | `review-mismatch` | Query and actual candidates, possibly an empty list. |
| `access_failure` | `review-access-failure` | Corroborated branch expected to fail access during this invocation. |
| `bounds` | All | Optional execution limits below. |
| `review_sort` | Review scenarios | `relevant`, `newest`, `highest` or `lowest`; default `relevant`. |

`geographic.location` is nonblank and at most 500 characters. Each point has finite `latitude` within -90 through 90 and `longitude` within -180 through 180. Discovery uses the explicit origin; both route modes use the explicit points. Location leads do not silently select a branch or replace a route endpoint.

Optional geographic limits are integer `radius_m` from 1 through 5,000, default 1,000; `location_limit` from 1 through 10, default 5; and `place_limit` from 1 through 20, default 10. This input is a caller-supplied example, not evidence of a route or current meal service:

```json
{
	"geographic": {
		"location": "Shinjuku Station, Tokyo",
		"origin": {"latitude": 35.6909, "longitude": 139.7003},
		"destination": {"latitude": 35.6940, "longitude": 139.7030}
	}
}
```

### Branch identity

Each branch section contains a `query` and at most twenty `candidates`. The query requires `name`; `full_address`, `website` and `feature_id` are optional. Candidates have those fields plus optional `google_maps_url`, `rating` and `reported_total`. Names have at most 500 characters; address, website and URL strings have at most 4,096. Feature IDs use Google's `0x...:0x...` form with 1 through 16 hexadecimal digits per component. Ratings are finite values from 1 through 5; review totals are nonnegative integers.

A confident match needs the same normalized name and exact full address. The alternative needs the same name and website with an explicitly targeted feature ID. Conflicting or multiple eligible identities fail matching. The client does not discover branches by name, refresh supplied metadata or infer branch identity from a shared brand alone.

Replace these placeholders with independently observed Google branch values. This is a structure example, not a live target:

```json
{
	"reviewed": {
		"query": {
			"name": "<observed branch name>",
			"full_address": "<observed full branch address>"
		},
		"candidates": [{
			"name": "<observed branch name>",
			"full_address": "<observed full branch address>",
			"feature_id": "<observed Google feature ID>",
			"google_maps_url": "<observed Google Maps URL>"
		}]
	}
}
```

A supplied zero total alone does not establish zero actual reviews. Returned `sample`, `partial`, `zero_reviews`, `empty_sample`, `omitted_sample` and `unavailable` remain distinct. No confident branch match is a separate local outcome. Missing reviews are not negative reception.

Do not paste retrieved publisher, Google, Grounding Lite or model text into `web_query`; it is a user-authored question without imported-source lineage. Web results are not forwarded into the SDK probe. The CLI reads the input file but does not copy it, print its path or emit its contents.

## Bounds and accounting

| `bounds` field | Default | Allowed values |
|---|---|---|
| `max_requests` | 10 | Integer 1 through 10, reduced to the scenario inventory. |
| `total_seconds` | 180 | Finite positive seconds, at most 300. |
| `max_output_tokens` | 512 | Integer 64 through 2,048 per web or SDK model response. |
| `review_pages` | 2 | Integer 1 through 2. |
| `review_count` | 20 | Integer 1 through 20. |
| `review_seconds` | 30 | Finite positive seconds, at most 30 for the sample. |

Geographic, review-page and direct web responses each have a 1 MiB decoded cap; a complete CLI review sample has a 2 MiB cap. SDK requests are limited to 65,536 bytes, decoded SDK responses to 1 MiB and minimized evidence JSON to 8,192 bytes. A successful SDK interaction uses at most three model requests, two owner turns and one research-agent turn. These controls bound execution, not monetary cost.

The CLI loads the normal backend `.env` and process environment through the [settings loaders](SETTINGS.md). It never chooses or upgrades a model. General scenarios require all provider/model/timeout selections, `OPENAI_API_KEY` in live mode and credentials for every selected geographic provider, even for standalone `web` or `sdk`. Geography execution supports `geoapify` for location, discovery and routes and requires `GEOAPIFY_API_KEY`.

Review execution additionally needs `GG_REVIEW_PROVIDER=google_reviews` and a positive `GG_REVIEW_TIMEOUT_SECONDS`. Review-only scenarios need neither geography/model settings nor OpenAI/Google keys, billing, cookies or a browser profile. Mismatch needs no settings. Preflight finishes before a request consumes allowance.

## Adapter behavior worth knowing

The adapter code is authoritative for complete Python signatures and parser fields. These differences matter when interpreting a probe result or composing a new caller.

| Component | Interface and consequential limits |
|---|---|
| [Geography and routes](../apps/backend/app/providers/geoapify.py) | `GeoapifyClient.resolve_location`, `nearby_places` and `route` return typed ephemeral results through `Completed` or `Stopped`. Forward selected provider leads intact to preserve provenance and input lineage. Discovery produces leads, not qualified recommendations. |
| Routes | `walk` and `drive` consume separate requests. `RouteAvailable` has provider duration/distance; an empty successful response gives `RouteUnavailable`. HTTP or malformed responses remain `Stopped`, never proof of unreachability or a zero-duration route. Driving uses free-flow traffic, not live traffic. |
| [Google reviews](../apps/backend/app/providers/google_reviews.py) | `match_branch` precedes `GoogleReviewClient.fetch`. Each page is metered separately. Duplicate IDs/cursors, byte caps and partial retrieval retain explicit limits/reasons. A failed later page cannot present earlier records as a complete sample. |
| [Web research](../apps/backend/app/providers/web_research.py) | The configured research model uses hosted web search. `EvidenceFound` preserves citations; `NoEvidence` is distinct from `ResearchUnavailable` or a failed request. Citation presence does not prove correctness or publisher permission. Some ignored provider-action shapes, including `find_in_page` without a URL, still reject as `invalid_response`. |
| [SDK probe](../apps/backend/app/agents/probe.py) | `SDKProbe.run` lets one owner invoke one research agent as an SDK tool. `SuggestionReady`, `ProbeUnavailable` and `ProbeStopped` are distinct. It validates one exact food target and supporting passage, not the production recommendation set. |
| [Shared calls](../apps/backend/app/core/calls.py) | `AttemptCalls` owns the request allowance and monotonic deadline. Every admitted failure, timeout or cancellation consumes a slot; slots are not refunded. Rejected admissions retain a separate receipt. |

The review transport uses fresh clients without inherited cookies and accepts identity-encoded responses only; compressed responses are rejected before decoding. For direct review-client callers, `ReviewLimits` defaults to two pages, twenty reviews and thirty seconds. Its validated ceilings allow five pages, fifty reviews and sixty seconds, with up to 1 MiB per response and 5 MiB total. The CLI intentionally uses the narrower bounds above. Reviews retain branch/source identity and available author attribution for ephemeral inspection; candidate aggregates are not freshly measured totals or representative sentiment.

Each admitted transport callback must perform exactly one transmission. Retries, redirects and unmetered pagination/background work are disabled. A nested `AttemptCalls.run` callback is rejected. The SDK runner sits outside those callbacks and meters each model request separately. `AttemptCalls` belongs to its constructing event loop; its shared deadline never resets. Late output cannot become success even if a callback suppressed cancellation.

Diagnostic mode cannot transmit. Test mode needs an explicit mock transport and synthetic credentials; live mode rejects test transport. The SDK uses the locked HTTPX2 transport, while geographic, review and direct web clients use HTTPX. Content-bearing HTTP/SDK logging is suppressed; the SDK probe's logger filters also affect other clients in that process.

### Legacy review selector

The current SDK probe calls `minimize_for_model`, which examines at most twenty fetched reviews and selects up to three positive English food passages totaling sixty words. A passage is an exact eligible sentence or delimiter-bounded clause of 5 through 20 words and at most 220 characters. Conservative vocabulary, quotation and context filters can exclude complaints, negation, local-language text and otherwise useful evidence.

No selectable passage means unavailable food evidence for this probe, not no food or negative reception. It makes no model call. A successful suggestion must equal a permitted concrete food target and retain exact supporting text. This restrictive selector is a feasibility limitation. The broader public-review input and supported-paraphrase requirements in [integration policy](INTEGRATIONS.md) must replace it in the production path, which remains incomplete.

## Read output safely

The command emits newline-delimited JSON containing inventory, bounded non-secret configuration, fixed result codes, `Receipt.safe_record()` projections and hosted-tool counts. Configuration reports presence/configured flags rather than credentials. Receipts keep safe model identity where recognized, timing, admission/outcome, fixed diagnostics and observed usage/cost. Unknown usage/cost remains `null`; a request count is not a price estimate.

No raw source/model text, precise location, branch/candidate content, source URLs, input paths, arbitrary exception text or credential values are emitted. Admitted and rejected receipts remain available when execution stops or is interrupted. Do not serialize entire samples, results, SDK run objects or arbitrary model dumps as diagnostics. Source content, prompts and derived findings stay ephemeral unless source policy explicitly permits retention. Tracing and sensitive trace data are disabled; Responses use `store=false`, which does not promise zero provider-side retention.

| Exit | Meaning |
|---|---|
| `0` | Help/inventory, local diagnostic readiness, a positive scenario passed, or the expected negative case was observed. Read the status record. |
| `1` | Unexpected failure; only fixed `probe_failed` diagnostics reach output. |
| `2` | Invalid arguments/input, missing selected input or uncorroborated required branch. |
| `3` | Configuration failed, with recognized fields and fixed issue codes. |
| `4` | Scenario failed or admission stopped. |
| `130` | Interrupted. Dispatched remote work may still complete. |

Wrapper failures before Python starts retain their shell exit status. A receipt reporting HTTP success alone does not mean that scenario checks passed. `Stopped` outcome codes distinguish failed calls, call/attempt timeout, cancellation, request/source rejection and nested-call rejection; inspect the fixed diagnostic rather than treating them as empty research.

The CLI intentionally omits the food suggestion and source bodies. To assess usefulness, inspect permitted ephemeral client results together with branch identity, attribution, actual source support and sample limits. Do not add an unrestricted dump to obtain them.

## Verification boundary

After backend setup, `scripts/local/verify-section-a.sh` runs the existing offline component checks without provider calls. Focused source/transport controls live in `apps/backend/tests/test_calls.py`, `test_geoapify.py`, `test_geoapify_routes.py`, `test_google_reviews.py`, `test_web_research.py` and `test_sdk_probe.py`.

A fresh live check must use the installation's configured services and independently observed branch inputs. Inspect actual results and permitted attribution alongside safe receipts, timing, usage and cost. A structural `passed` status, old successful call or cited answer does not establish current access, complete coverage, valid food recommendations or the normal application journey. Keep controlled outcomes distinct from naturally observed live results.
