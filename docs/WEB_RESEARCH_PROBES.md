# Web-research probe reference

GG-002.06 prepares `apps/backend/app/providers/web_research.py` for OpenAI Responses web search. It returns ephemeral answer blocks, actual URL citations, and observed hosted-search actions through the existing [bounded call layer](CALLS.md). Implementation completion is separate from section verification at GG-003. No live account, model, source, or local configuration is verified yet.

This API client supplies research for the feasibility probe. It does not capture a normal ChatGPT answer or establish the [comparison baseline](EVALUATION.md). The actual Agents SDK owner/tool-agent interaction remains GG-002.07. The executable probe command remains GG-002.08.

## Interface and prerequisites

Backend imports and commands use `apps/backend` as their working directory. The [locked backend environment](BACKEND_SETUP.md), explicit [local settings](SETTINGS.md), and an `AttemptCalls` object are prerequisites. The client reads the attempt's frozen settings. It loads no configuration on import, selects no model, and adds no environment variables or dependencies.

This interface sketch assumes the caller has already supplied settings and explicit limits. It is not an executable probe or a measured response:

```python
from app.core.calls import AttemptCalls, Bounds, Completed
from app.providers.web_research import EvidenceFound, WebLimits, WebResearchClient

calls = AttemptCalls(settings, Bounds(max_requests=request_limit, total_seconds=total_limit))
web = WebResearchClient(
	calls,
	limits=WebLimits(max_output_tokens=output_limit, max_tool_calls=hosted_call_limit),
)
result = await web.search(question, input_sources=())
record = result.receipt.safe_record()
if isinstance(result, Completed) and isinstance(result.value, EvidenceFound):
	blocks = result.value.blocks
	hosted_calls = result.value.hosted_calls
```

`search` requires a nonblank question of at most 2,000 characters and an explicit tuple of imported input origins. An empty tuple declares no imported source content. A question incorporating Geoapify geography declares `geoapify_geography`. Imported Google or unknown-publisher content remains blocked by the shared source gate. The adapter trusts accurate lineage declarations, as the existing call contract requires.

`WebLimits` requires integer `max_output_tokens` in 16 through 4,096 and `max_tool_calls` in 1 through 4. Booleans are invalid limits. The caller supplies both values; these are ceilings rather than chosen local configuration.

Live mode requires `OPENAI_API_KEY` and rejects injected transports. Test mode requires an explicit HTTPX `MockTransport` and a synthetic key. Diagnostic mode cannot transmit. The actual settings loader's live preflight also requires credentials for selected geographic providers. Credentials being present does not establish access, billing, model compatibility, or source permission.

## Request configuration

The client prepares one `POST https://api.openai.com/v1/responses` with the explicit bearer key. It uses the exact configured `GG_RESEARCH_MODEL`, even if that name is omitted from the safe receipt's display vocabulary. No model or fixture fallback exists.

The request has these fixed controls:

| Field | Value |
|---|---|
| `tools` | One `web_search` tool with `external_web_access=true` and `search_context_size="low"`. |
| `tool_choice` | `required`. |
| `include` | `web_search_call.action.sources`. |
| `max_output_tokens`, `max_tool_calls` | The caller's validated limits. |
| `store`, `background`, `stream`, `parallel_tool_calls` | `false`. |

The question is separate from fixed instructions requesting attributable evidence with actual citations. When supported evidence is unavailable, the instructions permit the exact standalone `NO_SUPPORTED_EVIDENCE` sentinel. There is no conversation, previous response, location-default override, direct publisher fetch, or response polling.

The [official web-search guide](https://developers.openai.com/api/docs/guides/tools-web-search) describes live access, required search, citation annotations, and consulted sources. The [Responses command reference](https://developers.openai.com/api/reference/cli/resources/responses/methods/create) documents the hosted-call and output-token ceilings. The installed generated OpenAI API types were also inspected. Compatibility of this complete request with the configured model remains a live gate check.

One admitted call consumes one shared application-request slot. The hosted-call ceiling separately limits built-in tool calls; it does not count or bound every provider-internal fetch. Observed hosted actions remain separate from application transmissions. Query characters, output tokens, response bytes, and elapsed time are bounded. These controls do not guarantee a monetary ceiling or a provider-side stop after local cancellation.

The transport uses the existing HTTPX dependency directly. It disables retries, redirects, environment proxies, and content-bearing HTTP logs. A streamed response has a 1 MiB decompressed-body ceiling and 64 KiB read chunks. The supplied timeout is the smaller of the configured model timeout and remaining attempt time. Parsing and cleanup remain inside the shared deadline. No OpenAI SDK client, Agents runner, or content trace is created by this module.

## Citation-bearing result contract

Citation capture is content-bearing result data. It is distinct from the metadata-only receipt. Each answer block preserves exact text with its original `output_index` and `content_index`. Each URL citation preserves `start_index`, `end_index`, `title`, and `url` from an actual annotation. Offsets remain local to that unchanged text block. They are never recalculated against concatenated text.

Hosted calls preserve provider call identity, output position, status, and the actual action. Search actions retain query metadata and consulted source URLs. Open-page and find-in-page actions retain their corresponding URL metadata. An absent consulted-source list remains unknown; an explicit empty list remains empty. Consulted URLs are not converted into citations. Citation links are not invented from prose or required to match the consulted list.

Private strict response models validate projected fields, HTTP(S) links, citation ranges, statuses, tool counts, and usage. The result separates these outcomes:

| Domain outcome | Meaning |
|---|---|
| `EvidenceFound` | Completed response and messages, completed hosted search, nonempty answer text, and actual URL citation annotations. Citation presence still requires claim-support review. |
| `NoEvidence` | Completed search followed by the exact uncited sentinel. This is provider-reported absence, not exhaustive coverage or proof that no restaurants exist. |
| `ResearchUnavailable` | Fixed refused, incomplete, or provider-failed outcome. No partial answer becomes usable evidence. |
| `Stopped` | Shared admission/transport failure or malformed response. The receipt supplies only fixed outcomes and diagnostics. |

An ordinary uncited narrative, missing actual search, unsupported semantic variant, malformed citation, or exceeded bound is invalid. An empty or malformed response does not become `NoEvidence`. HTTP errors remain errors without interpreting provider prose. Timeout, request-limit, cancellation, and source-blocked behavior comes from the existing call layer. Local cancellation propagates after recording admitted work.

The result also carries ephemeral observations of configured limits and actual hosted-call counts. They do not define another persistence interface. Provider input/output token usage is retained only when supplied and validated. `Usage.requests=1` describes the observed application transmission, never a hosted search count. Missing usage remains unknown. Cost is not invented.

## Source restrictions and diagnostics

The [integration findings](INTEGRATIONS.md#permission-findings) remain authoritative. Evidence has an explicit unchecked source-permission marker. Hosted search access does not authorize publisher scraping, raw-page archival, publication, or downstream text re-ingestion. This client provides no permission-clearing mechanism or content sink. Publication and further model use require the scheduled source checks.

All answer text, titles, URLs, queries, provider identifiers, and action payloads are excluded from result representations. `Receipt.safe_record()` remains the sole diagnostic persistence contract. It retains bounded metadata and excludes those content fields, secrets, raw errors, and precise locations. Do not serialize the entire result with dataclass or model dump helpers. Retaining content for gate evidence also requires a permitted source-specific scope.

A later UI displaying web-derived findings must preserve visible, clickable inline citations. `store=false` controls Responses application storage. It does not establish zero provider retention or disable abuse monitoring and prompt caching. The [OpenAI data-controls guide](https://developers.openai.com/api/docs/guides/your-data) describes those separate controls. Restricted Google content remains blocked regardless of this flag.

## Verification boundary

Prepared synthetic cases belong in `apps/backend/tests/test_web_research.py`. Their execution, the existing call/geographic cases, real response inspection, useful restaurant evidence, source-link and multilingual-offset checks, effective local settings, account/model/tool access, usage, timing, redaction, and cost availability remain GG-003. No actual secret configuration or publisher permission is established by this implementation.

The plan record lists the task-local static checks actually run. No behavioral test, broad suite, build, browser check, provider call, or model call runs during this child. GG-002.07 and all later tasks remain separate requests.
