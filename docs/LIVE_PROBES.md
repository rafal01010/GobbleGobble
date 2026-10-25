# Bounded probe command

The [GG-003 report](FEASIBILITY_GATE.md) records live observations, the route-unit repair, the completed GG-002.07A evidence-selection repair and the passed feasibility gate. CLI `passed` means its structural scenario checks succeeded. It does not override the gate's acceptance conditions. `scripts/local/verify-section-a.sh` runs the offline behavioral and static checks without provider calls.

GG-002.08 connects the prepared clients through `scripts/local/probe-live.sh`. Implementation is complete separately from behavioral and live verification at GG-003. This task does not execute credentialed calls.

## Commands and prerequisites

The wrapper uses the installed `apps/backend/.venv/bin/python`. Run [backend initialization](BACKEND_SETUP.md) first. It sets the backend import path and preserves the caller's working directory, including relative `--input` paths. It does not install dependencies or synchronize the lockfile.

These commands display help and the scenario inventory without loading settings or reading input:

```sh
scripts/local/probe-live.sh --help
scripts/local/probe-live.sh --list
```

Use one closed JSON input file. Without `--live`, the command validates the selected inputs and local configuration and prints `diagnostic_ready`. It does not construct a client or transmit. Credential presence is reported but credential validity remains unknown.

```sh
scripts/local/probe-live.sh --scenario combined --input /private/tmp/gobble-probe.json
```

At the separately requested GG-003 gate, this explicit opt-in permits real requests after local preflight:

```sh
scripts/local/probe-live.sh --scenario combined --input /private/tmp/gobble-probe.json --live
```

The default scenario is `combined`. There is no fixture fallback, retry, model substitution, or automatic rerun. Provider responses determine success. Empty, partial, blocked, malformed, or timed-out results do not become positive evidence.

## Scenario inventory

Each run selects one scenario. The registry declares these maximum transmitted requests before the shared allowance is applied:

| Scenario | Maximum requests | Successful observed result | Settings loader |
|---|---|---|---|
| `geographic` | 4 | Location and nearby meal leads exist. Walking and driving routes are available. | General |
| `web` | 1 | A completed search supplies cited evidence. Reported no evidence is a failed scenario. | General |
| `reviews-reviewed` | 2 pages | A nonempty review sample has `sample` status. | Reviews only |
| `reviews-unreviewed` | 2 pages | Supplied Google candidate metadata reports zero reviews, and an empty source response corroborates `zero_reviews`. | Reviews only |
| `review-mismatch` | 0 | Supplied query and candidates produce an actual `NoConfidentMatch`. | None |
| `review-access-failure` | 2 pages | A matched supplied branch actually returns `access_failure` with `unavailable` status. | Reviews only |
| `sdk` | 2 review pages plus 3 model requests | A fetched sample supports an actual SDK owner, nested research tool-agent, and final validated suggestion. | General |
| `combined` | 4 geographic, 2 review pages, 1 web, 3 SDK model requests | All positive capability results above succeed using the `reviewed` branch. | General |

`combined` has one `AttemptCalls` allowance of at most 10 requests and one monotonic deadline. Each review page and each SDK model request consumes that allowance. The hosted web tool has a separate ceiling of one executed call within the single web request. It is not an extra client transmission. Hosted counts are reported separately from receipts. Provider-reported ignored tool attempts remain distinguishable from executed work.

The effective request allowance is the smaller of the selected inventory and `bounds.max_requests`. A lower allowance can stop the scenario. It never increases automatically. Execution stops after a failed capability, so later capabilities may not run. Separate negative and unreviewed scenarios require separate invocations and do not share a cross-process allowance.

`review-mismatch` returns `observed_expected_failure` only for the actual local matching result. It never fetches or loads settings. `review-access-failure` returns that status only after the observed source failure matches the requested case. A successful fetch, empty sample, malformed response, transport failure, timeout, or request-limit rejection fails that negative scenario. The command never generates a bad target or synthetic failure to make it pass.

## Input document

`app.probe_cli.ProbeInput` is the authoritative closed schema. The UTF-8 JSON document is limited to 65,536 bytes. Unknown fields, duplicate keys, invalid types, nonfinite numbers, and bounds outside the declared ranges fail before any transmission. Only selected scenario fields are required. The parser validates any optional fields that are present.

All fields below are optional at the top level. A selected scenario requires its corresponding section:

| Field | Required by | Shape |
|---|---|---|
| `geographic` | `geographic`, `combined` | `location`, `origin`, `destination`, and optional geographic limits |
| `web_query` | `web`, `combined` | User-authored question of 1 through 2,000 characters |
| `reviewed` | `reviews-reviewed`, `sdk`, `combined` | `query` and `candidates` |
| `unreviewed` | `reviews-unreviewed` | `query` and `candidates`, with corroborated candidate `reported_total` equal to integer `0` |
| `mismatch` | `review-mismatch` | Supplied `query` and actual candidates, possibly an empty list |
| `access_failure` | `review-access-failure` | `query` and `candidates` for a corroborated branch that actually fails access at the gate |
| `bounds` | None | Optional limits listed below |
| `review_sort` | None | `relevant`, `newest`, `highest`, or `lowest`. Default `relevant`. |

`geographic.location` is a nonempty string of at most 500 characters. `origin` and `destination` each contain finite numeric `latitude` in -90 through 90 and `longitude` in -180 through 180. Discovery centers on the explicit origin. Both route modes use those explicit points. Returned lead ordering never selects a branch or route endpoint. Location resolution can return several leads and does not establish a unique identity or a relationship to the supplied route points.

Geographic limits are integer `radius_m` in 1 through 5,000, default 1,000, `location_limit` in 1 through 10, default 5, and `place_limit` in 1 through 20, default 10.

Each branch `query` has required `name`, plus optional `full_address`, `website`, and `feature_id`. Each `candidates` list contains at most 20 objects with those same fields plus optional `google_maps_url`, `rating`, and `reported_total`. Names are nonempty and at most 500 characters. Address, website, and URL strings are nonempty and at most 4,096 characters. Feature IDs must have Google's `0x...:0x...` form, with 1 through 16 hexadecimal digits per component. Ratings are finite numbers in 1 through 5. Reported totals are nonnegative integers.

The supplied candidate metadata must come from an honestly observed Google branch. Matching performs no discovery or metadata refresh. Exact names require corroboration by the exact full address, or a matching website together with the explicitly targeted feature ID. Conflicting or ambiguous identity fails before a fetch. A zero supplied total alone never establishes zero actual reviews. See the [matching contract](GOOGLE_REVIEW_PROBES.md).

This schematic JSON shows the `reviewed` section's structure. Replace every placeholder with independently observed values before using it. It is not a live target or proof of current metadata:

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

Do not paste retrieved Google, Grounding Lite, publisher, or model text into `web_query`. It is a user-authored question with no imported-source lineage. Web results stay outside SDK input. Only the existing review minimizer's bounded food passages enter the SDK probe.

Keep input files outside tracked source files. The CLI reads the supplied file but never writes it, copies its content, or prints its path.

## Bounds and non-secret settings

| `bounds` field | Default | Allowed values |
|---|---|---|
| `max_requests` | 10 | Integer 1 through 10, further reduced to the scenario inventory |
| `total_seconds` | 180.0 | Finite positive number at most 300 |
| `max_output_tokens` | 512 | Integer 64 through 2,048 per web or SDK model response |
| `review_pages` | 2 | Integer 1 through 2 |
| `review_count` | 20 | Integer 1 through 20 |
| `review_seconds` | 30.0 | Finite positive number at most 30 for the complete sample |

Each review response has a fixed 1 MiB decoded limit. The complete review sample has a fixed 2 MiB limit. Geographic and direct web responses each have a fixed 1 MiB decoded limit. Each SDK request has a 65,536-byte limit and each decoded SDK response a 1 MiB limit. Minimized SDK evidence is at most 8,192 bytes, three passages, and sixty words. Successful SDK interaction uses at most three model requests. The SDK owner permits two turns and the research agent one turn. These limits, request ceilings, and deadlines bound work, not monetary cost or remote cancellation after dispatch.

Settings come from the existing [settings loaders](SETTINGS.md), including the backend `.env` and process-environment precedence. The CLI does not select or override a model.

General settings require `GG_LOCATION_PROVIDER`, `GG_DISCOVERY_PROVIDER`, `GG_ROUTE_PROVIDER`, `GG_WEB_PROVIDER`, `GG_OWNER_MODEL`, `GG_RESEARCH_MODEL`, `GG_GEOGRAPHIC_TIMEOUT_SECONDS`, and `GG_MODEL_TIMEOUT_SECONDS`. Live general loading requires `OPENAI_API_KEY` and the credential for every selected geographic provider, even for standalone `web` and `sdk`. Geographic execution supports only `geoapify` for all three geographic providers and therefore requires `GEOAPIFY_API_KEY`.

Review execution additionally requires `GG_REVIEW_PROVIDER=google_reviews` and finite positive `GG_REVIEW_TIMEOUT_SECONDS`. Review-only scenarios use `load_review_settings` and require no geographic providers, models, OpenAI key, Google API key, billing, cookies, or browser profile. The mismatch scenario requires no settings. All selected settings and branch inputs pass local preflight before a request can consume allowance.

Diagnostic mode reports only fixed provider choices, timeout values, model-configured flags, and credential-presence flags. Live receipts report configured model identities only through the existing safe projection. Missing or unrecognized identities remain explicitly omitted. Available usage and observed provider cost are reported in receipts. Unknown usage or cost remains `null`.

## Output and exit status

Output is newline-delimited JSON. Its records contain the declared inventory, bounded non-secret settings, `Receipt.safe_record()` projections, fixed result codes, and hosted-call counts. Rejected admissions are retained alongside admitted receipts without duplication. Receipts remain available if execution stops or is interrupted. No raw source text, model output, location text, coordinates, branch names, candidate text, source links, file paths, arbitrary exception messages, or credential values are emitted. Logging is disabled for this standalone process.

| Exit code | Meaning |
|---|---|
| `0` | Inventory/help displayed, local diagnostic readiness, positive scenario passed, or the requested negative case was actually observed. Read the status record to distinguish them. |
| `1` | An unexpected exception reached the CLI boundary. The exception hook prints only fixed `probe_failed` diagnostics. |
| `2` | Invalid CLI arguments, unreadable/invalid input, missing scenario input, or uncorroborated required branch. |
| `3` | Configuration failed. `issues` lists recognized configuration fields and fixed missing/invalid/unreadable codes. |
| `4` | Scenario failed or admission stopped. |
| `130` | Interrupted. Dispatched remote work may still finish. |

Wrapper failures before Python starts, including a missing initialized interpreter, retain the shell's failure status. An HTTP receipt with `success` alone does not establish scenario success. The CLI also checks the parsed geographic, review, web, and SDK outcomes in memory.

## GG-003 verification boundary

Help and static sanity checks establish command availability only. GG-003 subsequently verified the documented offline controls and bounded live capabilities. The gate report records exactly which scenarios ran and their coverage limits. Run each selected scenario with independently observed branch inputs and inspect whether its actual outcome matches the scenario.

The CLI intentionally does not display source content or the suggestion. GG-003 must separately inspect permitted ephemeral source and model objects through the existing client interfaces. That inspection must check branch identity, review attribution, exact passage support, cited web evidence, and semantic food quality together. Retain only permitted receipt projections and fixed diagnostic codes. Do not dump results, persist source/model content, or add an unrestricted content-output flag. A CLI `passed` record confirms its structural checks, not completion of the full source-quality gate.
