# Bounded calls and receipt reference

GG-002.03 adds `apps/backend/app/core/calls.py`. Future geographic and OpenAI adapters use one `AttemptCalls` instance to share a request allowance and total deadline. This module implements no transport, provider client, probe, SDK runner, or persistence. It reads no configuration file and selects no provider or model. GG-002.03 is implemented, awaiting section verification at GG-003.

## Interface

`AttemptCalls(settings, Bounds(max_requests=..., total_seconds=...))` starts an attempt inside a running asyncio event loop. `settings` is the existing typed result of `load_settings`. Both bounds are explicit caller inputs. There are no default allowances or new environment variables. The probe command will declare its inventory in GG-002.08.

GG-002.04 adds a read-only `settings` property returning that frozen configuration. The [geographic client](GEOGRAPHY_PROBES.md) derives its provider and credential from this exact object, keeping its request identity aligned with receipts while sharing the attempt allowance.

`CallSpec` declares `capability`, `use`, `input_sources`, and an optional `model_role`. `attempt.run(spec, invoke_once)` returns `Completed[T]` with an ephemeral value and receipt, or `Stopped` with a receipt. Cancellation records a receipt for an admitted call and propagates `CancelledError`.

These are the closed request vocabularies:

| Field | Values | Meaning |
|---|---|---|
| `capability` | `location`, `discovery`, `route`, `web_research`, `model` | Selects the configured provider and timeout. |
| `use` | `api_diagnostic`, `model_inference` | Declares diagnostic-only response handling or intended model use. |
| `input_sources` | A tuple containing `geoapify_geography`, `ordinary_google`, `grounding_lite`, or `unknown_publisher` | Classifies every imported content source. An empty tuple explicitly declares no imported source content. |
| `model_role` | `owner`, `research`, or absent | Required for model and web-research capabilities. Absent for geographic calls. |

Location, discovery, and route calls resolve their respective provider selections and use `geographic_timeout_seconds`. Web research and model calls use OpenAI and `model_timeout_seconds`. The model role resolves the exact configured model. Safe receipt naming does not alter that value. Receipt identity describes the requested model, not a provider-reported resolved model or proof of model availability.

The callback accepts the effective timeout in seconds and returns `Parsed[T]` containing a value and optional typed usage/cost. The call layer never inspects or serializes the value. `Completed` and `Parsed` omit it from their representations. Only the receipt's `safe_record()` projection is a diagnostic record. Serializing the result object or a raw provider response is outside this interface.

This interface sketch assumes the caller has already loaded settings and supplied its request allowance, total deadline, and async adapter. It is not a runnable probe or measured call:

```python
from app.core.calls import AttemptCalls, Bounds, CallSpec, Completed

attempt = AttemptCalls(
	settings,
	Bounds(max_requests=request_limit, total_seconds=total_limit),
)
result = await attempt.run(
	CallSpec(
		capability="route",
		use="api_diagnostic",
		input_sources=(),
	),
	route_once,
)
record = result.receipt.safe_record()
if isinstance(result, Completed):
	route = result.value
```

## Bounds and lifecycle

`max_requests` is a positive integer and rejects booleans. `total_seconds` is finite and positive. The total monotonic deadline starts at construction. It does not reset between calls. Each invocation receives the smaller of its configured per-call timeout and the remaining attempt time.

Admission checks and request consumption have no suspension point. Sibling tasks can share an attempt on its owning event loop without overspending the last slot. Cross-loop use is invalid. Every admitted attempt consumes one slot even if it fails, times out, or is cancelled. Slots are never refunded.

A callback cannot recursively invoke this call layer. An inherited context guard also rejects child-task nesting. Orchestration belongs outside the callback. Separately metered transmissions require sequential or sibling calls through the same attempt.

The executor checks the deadline after callback completion. Late output cannot become success, including when a callback suppresses timeout cancellation. Deadlines require cooperative async callbacks. A blocked event loop or cancellation-resistant transport can delay return. Local cancellation cannot retract remote work already dispatched or establish whether the provider billed it.

Each callback must perform exactly one transport attempt. Adapters must disable hidden SDK retries, redirects, pagination, polling, reconnects, and background requests, or admit each transmission separately. The generic executor cannot inspect arbitrary callback behavior. An entire agent run cannot be counted as one network request. Hosted provider tool work also needs separate reported usage where available. GG-002.04 prepares these transport controls for Geoapify location/discovery. GG-002.05 reuses them for [walking and driving routes](ROUTE_PROBES.md). GG-002.06 prepares the [OpenAI web-research client](WEB_RESEARCH_PROBES.md) with a separate hosted-tool ceiling and ephemeral observations. Its `Usage.requests` counts the application transmission, not hosted calls. SDK orchestration controls remain GG-002.07. Verification remains GG-003.

`attempt.receipts` returns immutable snapshots of admitted-call completion receipts. The history cannot exceed `max_requests`. Rejections return their receipt through `Stopped` without adding to that history. A task cancelled before `run` begins creates no invocation receipt. Receipts are in completion order and are not durable run events.

## Receipt shape

`Receipt.safe_record()` projects only the declared metadata. It accepts no arbitrary diagnostic dictionary. The receipt contains local request identity, provider/capability, declared use/input-source classes, requested model metadata, UTC timestamp, monotonic duration, admission status, outcome, fixed diagnostic, and optional usage/cost.

The model name is retained only through exact membership in the installed OpenAI SDK's `ChatModel` literal vocabulary, with configured credential values excluded. `model_identity` is `named`, `omitted`, or `not_applicable`. An unknown configured name remains usable by the future adapter but appears as `requested_model=null` with `model_identity="omitted"`. SDK vocabulary membership is a safe label rule, not a model-selection or support recommendation. It can include unavailable or retired names.

Unknown usage and cost remain absent. The wrapper does not assume zero usage after failure, timeout, or cancellation and invents no pricing. Optional numeric metadata must pass its typed boundary before retention. The adapter is responsible for reporting provider observations accurately.

`Usage` contains optional nonnegative integer `input_tokens`, `output_tokens`, and `requests`. `Cost.usd` is a finite nonnegative `Decimal` containing an observed provider amount. The safe projection represents it as a decimal string. These fields contain no locally assumed rate or estimated bill.

These are the terminal outcome values:

| Outcome | Interpretation |
|---|---|
| `success` | The callback returned an accepted typed response within its deadline. |
| `failed` | The callback failed or returned an invalid response. Only a fixed diagnostic survives. |
| `call_timeout` | The configured per-call timeout elapsed. |
| `attempt_timeout` | The shared attempt deadline elapsed or had already elapsed before admission. |
| `cancelled` | An admitted invocation received caller cancellation. Cancellation propagates. |
| `request_limit` | No request slot remained. The callback was not invoked. |
| `source_blocked` | The declared source use was prohibited. The callback was not invoked. |
| `nested_call` | A callback attempted a nested call. The nested callback was not invoked. |

Fixed diagnostics are `none`, `unknown_failure`, and `invalid_response`. Exception objects, exception strings, HTTP bodies, response IDs, URLs, authorization headers, prompts, food/review text, and precise locations never enter the projection. A local UUID is generated for each receipt. It is not a provider request identifier.

This schema-only example shows an omitted model identity and unavailable measurements. Angle-bracket descriptions replace identifiers and numbers. It is not a valid serialized receipt or a live-call result:

```json
{
	"local_request_id": "<locally generated UUID>",
	"provider": "openai",
	"capability": "web_research",
	"use": "model_inference",
	"input_sources": [],
	"model_role": "research",
	"requested_model": null,
	"model_identity": "omitted",
	"started_at": "<UTC timestamp>",
	"duration_seconds": "<monotonic elapsed seconds>",
	"admitted": true,
	"outcome": "failed",
	"diagnostic": "unknown_failure",
	"usage": null,
	"cost": null
}
```

## Source-use boundary

The [integration findings](INTEGRATIONS.md#permission-findings) remain the policy record. The initial guard uses those findings without claiming completed publisher-policy enforcement.

| Declared source or use | Admission rule |
|---|---|
| Google geographic output | API diagnostics only while model compatibility and output reuse remain unresolved. |
| Ordinary Google or Grounding Lite content entering a model | Blocked, including when OpenAI is the called provider. |
| Unknown publisher content entering a model | Blocked. Hosted search access does not authorize direct text re-ingestion. |
| Geoapify geography entering a model | Allowed under the documented inference finding. Attribution and underlying license duties remain. |
| Hosted OpenAI web research | Allowed as a capability. Returned claims still require source checks before publication or retention. |
| Mixed imported sources | Every source must be permitted. One restricted source blocks the call. |

Input-source declarations are a trusted adapter contract. The executor does not inspect prompts or response text. An API diagnostic result carries no permission to send its content into a model later. Source restrictions continue to apply to every downstream use.

The module has no content sink or trace exporter. It neither enables nor configures the Agents SDK. SDK tracing, Responses storage options, provider-side logging, attribution display, publisher-specific retention, and dependent-claim restrictions require their scheduled implementations. A safe receipt grants none of those permissions. GG-009 owns the broader evidence-policy boundary.

## Verification boundary

GG-002.03 passed source review and narrow import, syntax, Ruff lint/format, mypy, and document-artifact checks. `apps/backend/tests/test_calls.py` contains offline behavioral cases for the section gate. Those cases have not been executed during this child.

At GG-003, run the offline cases from `apps/backend`, inspect timeout/failure/redaction behavior, then verify the prepared adapters under that gate's bounded live authorization. The gate must also establish actual account access, model identity, effective configuration, retry suppression, SDK tracing/storage controls, provider-side restrictions, usage, timing, and any unavailable measurements. No live calls or provider/model selections are established by this module.
