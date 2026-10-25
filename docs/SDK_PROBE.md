# SDK owner and research probe

**GG-003 passed, 2026-10-25.** GG-002.07A repaired bounded clause selection and concrete-target validation. Actual Luna owner, nested research and owner requests produced a concrete exact-source suggestion for an independently corroborated English-language branch. The [gate report](FEASIBILITY_GATE.md) preserves Toyo and Ippei unavailable outcomes, source decisions, usage and coverage limits.

GG-002.07 prepares a minimal OpenAI Agents SDK owner that invokes a research agent as a tool. It interprets a supplied Google review sample and returns one food suggestion with exact passage support. Implementation completion is separate from behavioral and live verification at GG-003.

The probe is a feasibility component. The production research agent and adaptive recommendation owner remain GG-010 and GG-012. GG-002.08 connects it through the [bounded probe command](LIVE_PROBES.md). The `sdk` scenario fetches the supplied corroborated reviewed branch before running the SDK. The `combined` scenario shares at most ten requests across geography, reviews, web research, and SDK model calls.

The [production context contract](CONTEXT_MANAGEMENT.md) assigns oversized-output fitting, retained decision evidence, reserved composition and bounded recovery to later tasks. The current probe stops on an oversized continuation. Its completion does not satisfy those production acceptance cases or require reopening GG-002.07.

The user removed public-review personal-information minimization on 2026-10-25. The interfaces below still describe the existing feasibility probe. Its restricted vocabulary and exact target list must not become the production research input policy. GG-009.01A and GG-010.03 replace that path, and GG-011 must retest Toyo and Ippei with relevant public review text. Supported findings retain source references without requiring every finding to be a verbatim food phrase. [The public-review contract](GOOGLE_REVIEWS.md#public-review-model-input-decision-2026-10-25) is the current requirement.

## Interface and outcomes

Imports use `apps/backend` as the working directory. This interface sketch assumes configured settings and an ephemeral sample from the review client. It makes no call by itself.

```python
from app.agents.probe import ProbeLimits, SDKProbe, SuggestionReady
from app.core.calls import AttemptCalls, Bounds

calls = AttemptCalls(settings, Bounds(max_requests=request_limit, total_seconds=total_limit))
probe = SDKProbe(calls, limits=ProbeLimits(max_output_tokens=output_limit))
result = await probe.run(sample)
if isinstance(result, SuggestionReady):
	suggestion = result.suggestion
	branch = result.branch
records = [receipt.safe_record() for receipt in result.receipts]
```

`ProbeLimits.max_output_tokens` is an integer in 64 through 2,048. It applies to each model response. Successful output contains one `food` phrase, a `passage_label` and the exact supporting `quote`. The food must equal one of the minimizer's exact permitted concrete targets. Generic food praise and unsupported targets fail validation. The owner must preserve the validated research contribution. These content fields are ephemeral.

| Result | Meaning |
|---|---|
| `SuggestionReady` | The SDK nested invocation and final passage support passed local validation. The branch remains attached outside model input. |
| `ProbeUnavailable` | No usable food passage, invalid model output or SDK failure. It does not mean that no place qualifies. |
| `ProbeStopped` | Request admission, source admission, transport or deadline stopped the run. The fixed receipt records the cause. |

Diagnostic mode cannot transmit. Test mode requires an explicit `httpx2.MockTransport` and the exact synthetic test key. Live mode rejects injected transports and requires the configured OpenAI key. Fixed preflight codes distinguish setup errors. The probe loads no settings, credentials or fixtures itself. `receipts` contains admitted requests from this probe invocation. A rejected admission appears separately as `ProbeStopped.receipt` because the shared executor does not add rejections to admitted history.

## Agent ownership and source input

The owner retains responsibility for the final suggestion. Its research capability uses the SDK's `Agent.as_tool` mechanism. The nested run returns structured food evidence to the owner. This follows the [official SDK orchestration guide](https://developers.openai.com/api/docs/guides/agents/orchestration).

The caller supplies a `ReviewSample` from the [Google review client](GOOGLE_REVIEW_PROBES.md). The probe applies `minimize_for_model` internally. The minimizer inspects at most twenty already fetched reviews and selects at most three positively supported concrete-food passages and sixty words. It preserves a full eligible sentence or an exact delimiter-bounded clause after conservative context screening. The fixed privacy vocabulary is unchanged. Branch names, addresses, reviewer identities, raw reviews and source URLs stay outside model input. Retrieved passages are untrusted data.

The model receives passage labels, bounded food text, exact permitted concrete targets and sample limitations. Ephemeral source review indices and source offsets remain outside its input. It cannot infer a current menu, opening status, route, representative reception or an exhaustive history from this sample. No usable minimized passage means no model call. Missing food evidence does not disqualify the place.

The probe does not re-ingest the [web client's](WEB_RESEARCH_PROBES.md) unchecked publisher text. Production source selection and fallback remain scheduled work. Ordinary Google, Grounding Lite and unknown-publisher inputs remain blocked by the existing call policy.

## Bounds and accounting

One shared `AttemptCalls` owns the request allowance and monotonic deadline. Each model request consumes its own allowance through a model wrapper. The SDK runner itself is outside the transport callback, so nested agent execution does not create an illegal nested `AttemptCalls.run`.

A successful run requires an owner request, one nested research request and an owner composition request. The research run permits one turn. The owner permits two turns. Only one research invocation is accepted. Lower remaining allowances stop work without automatic retry or model substitution.

The SDK transport uses the existing locked HTTPX2 2.16.0 dependency, now declared directly. The geographic, review and direct web clients continue to use HTTPX. No dependency version changed.

The probe uses the exact configured owner and research models. Local development currently selects Luna for both roles. This task makes no claim about Luna access or capability. The [settings reference](SETTINGS.md) and [development cost policy](../IMPLEMENTATION_PLAN.md#development-model-cost-policy) govern the gate.

OpenAI client retries and Agents SDK model retries are disabled separately. Requests use nonstream Responses without server conversation state. Each request is limited to 65,536 bytes. Each decoded response is limited to 1,048,576 bytes. The minimized evidence JSON is limited to 8,192 bytes. Output tokens and elapsed time also have explicit ceilings. These are execution bounds rather than a monetary ceiling. A local timeout cannot guarantee cancellation of dispatched provider work.

## Privacy and retained evidence

Both owner and nested runs disable tracing and sensitive trace data. The probe preserves fixed failure state separately because the installed SDK redacts custom exceptions under this configuration. The [official SDK observability guide](https://developers.openai.com/api/docs/guides/agents/integrations-observability) describes the SDK's tracing behavior. Responses use `store=false` and disable background execution. These settings do not promise zero provider retention, prevent abuse monitoring or eliminate prompt caching.

Source text, model output, branch identity and links remain ephemeral. Content fields do not appear in result representations. Only existing `Receipt.safe_record()` projections and fixed result status codes are diagnostic evidence. The probe suppresses content-bearing SDK and HTTP loggers process-wide, including their other clients. Never dump the entire result or retain SDK run objects.

Branch attribution stays attached to the supplied sample and returned suggestion outside model inference. Preserve the Google Maps source identity, branch link, review links and available attribution when inspecting a permitted ephemeral result. Passage support establishes traceability, not semantic recommendation quality. GG-003 must inspect the actual suggestion and source together.

## Verification boundary

GG-003 must demonstrate an actual owner invocation and nested research invocation with the configured models. It must inspect passage support, branch attribution, safe receipts, usage, elapsed time and available cost. Prepared offline cases cover request limits, bad output, missing evidence, nested invocation, retry suppression, transport controls and safe diagnostics.

The SDK implementation task performs source review and task-local static sanity checks only. GG-003 subsequently passed behavioral tests, real SDK model calls and broad offline verification, with its limits recorded in the gate report. The companion CLI emits fixed outcomes and safe receipts without printing the suggestion or supporting content. GG-003 must inspect permitted ephemeral source and model objects separately. No application recommendation endpoint or browser behavior is delivered here.
