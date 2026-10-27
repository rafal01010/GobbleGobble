# Domain validation

The Pydantic models under `apps/backend/app/schemas/` define the application's data contracts. This reference explains relationships that a field list or generated TypeScript type cannot express. For HTTP operations and contract export, see [API](API.md). For the intended dining experience, see [product requirements](PRODUCT.md).

The models exist, but production research, recommendation publication and refinement are not connected yet. A valid payload is not proof of source truth, permissions, freshness, ownership or successful execution.

## Validation boundaries

| Source module | Boundary to use |
|---|---|
| [search.py](../apps/backend/app/schemas/search.py) | `SearchRequest` and individual resolved `Place` records. |
| [evidence.py](../apps/backend/app/schemas/evidence.py) | `EvidenceCollection` for source, branch, evidence and finding relationships. |
| [recommendations.py](../apps/backend/app/schemas/recommendations.py) | `RecommendationCollection` for snapshots, selections, food choices and lineage. |
| [lifecycle.py](../apps/backend/app/schemas/lifecycle.py) | `RunReadResponse` for a run's supplied display-result relationships; `EventBatch` for ordered same-run events. |
| [operations.py](../apps/backend/app/schemas/operations.py) | Request and response envelopes, including operations whose routes are not implemented yet. |

Parse the containing relationship boundary, not just a leaf record. A standalone food claim cannot establish whether its evidence exists. Callers must load the real search's records; the schemas do not query storage or authenticate supplied identifiers.

Models are strict and frozen, reject extra fields and hide input values in formatted validation messages. Numeric constraints reject invalid types and non-finite values. These protections do not make serialization, structured validation errors or contained location/review data safe for logs or persistence. Nullable fields without defaults remain required in JSON and need an explicit value or null.

## Location and practical facts

A `SearchRequest` preserves current versus chosen origin, a resolved location, travel limits and an aware `eat_now_at` instant. Locations carry bounded finite coordinates, a valid IANA timezone and optional display/provider references. Latitude is within ±90 and longitude within ±180. The location's timezone supplies local dining context; server time does not. Schema validation cannot establish that a timezone matches coordinates or that an instant is current.

Walking defaults to 15 minutes and accepts positive integer minutes. Driving uses exactly 30 integer minutes. Booleans, numeric strings and floating point limits are invalid. There is no country whitelist or schema-imposed maximum walking limit.

Each `Place` identifies one branch, venue or stall within a search. A stall can name a containing venue while retaining its own ID. A chain name is not branch identity. Provider identifiers remain external references, not internal place IDs. Normalization must still perform matching, deduplication and parent-reference checks.

Unknown journey/opening facts assert no duration or open/closed conclusion. An estimated journey has mode and finite nonnegative duration, but eligibility still requires comparison with the request. An assessed opening separates venue opening from meal service. An open venue need not be serving food. None of these shapes proves source support or freshness.

## Evidence and branch attribution

A source is an attributable document, listing or review origin. Evidence is a finding from that source. Food/review findings cite evidence IDs, not source IDs. The `src_` and `ev_` namespaces are distinct from URLs and provider identifiers.

Sources declare place scopes, and each evidence record declares its own subject places within that scope. A shared restaurant-group page may cover two branches while one menu finding covers only one. Sharing a page never transfers every finding to every branch.

Asserted findings bind evidence separately for each subject place. Every referenced evidence record must cover that exact subject. A two-place claim needs explicit support for both places; collecting separate A and B evidence in one array does not establish support for both. `EvidenceCollection` checks uniqueness, existence, identifier category, declared scope and review-sample membership.

Sourced food claims require support. Inferred claims also need a nonblank explanation of the inference. Unknown claims record a gap rather than an asserted food fact. Optional menus, prices and complaints retain the same exact-subject checks. A review mentioning a dish does not establish that it is currently on the menu.

These validators check declared relationships, not whether prose actually supports a dish, price or paraphrase. Production interpretation and publication must establish that separately.

## Reviews, fallback and use policy

Google branch identity and individual review identity are separate. Locators distinguish review-level links from branch links and preserve available publisher/author attribution. Missing attribution remains missing.

The model distinguishes matched accessible evidence, confirmed zero reviews, no confident branch match, restricted use and provider failure. An accessible sample may be populated, empty or omitted. Empty/omitted samples never establish a total of zero. Reported total, returned size, assessed count, selection/order, bounds, partial retrieval and omissions remain distinct facts. Selected negative reviews do not establish uniformly negative reception.

Google findings must cite assessed evidence from the declared primary sample and branch. Independent review fallback has its own sample; independent food fallback has its own evidence list. Both retain source identity and a reason consistent with the Google outcome. Fallback evidence cannot masquerade as Google reviews or cite another branch's sample.

Source declarations separately govern access, display, model input, derived output and tracing. Raw content, derived content and identifiers have distinct retention decisions. Retention permission is not factual freshness. These records do not schedule deletion, test the current clock or grant rights because a model supplied them. Follow [integration and source-use policy](INTEGRATIONS.md).

The production review requirement permits relevant public text and attribution, including complaints, personal references and local languages. The legacy probe's restrictive minimizer is not that production policy. Retrieval/admission/fallback integration remains incomplete.

## Research and recommendation versions

`ResearchSnapshot` separates discovery leads from researched candidates. Their place IDs must be unique and disjoint, and the evidence catalog must equal their combined IDs. Only researched candidates can appear in selections or support food choices. Mere presence as a discovery lead is insufficient.

`RecommendationCollection` holds one search identity, its original request, snapshots and versions. Versions cannot override the location, dining instant or travel constraint. Each selection resolves to the version's exact snapshot and branch-specific food claims. Unknown food claims may describe missing information in a recommendation.

A version has at most three primaries and four runners-up. IDs are unique within and disjoint across the groups. Order is display order. Fewer than three primaries or two runners requires a nonblank `shortfall_reason`; the validator does not judge its justification. Empty explained groups are structurally possible, but cannot satisfy a ready/partial display outcome.

A finite food choice binds researched candidates to sourced or inferred food/menu claims. Unknown claims and price-only claims cannot support a choice. Supporting candidates may be outside the displayed shortlist. Shared claims must explicitly support every referenced branch.

Snapshots use `snap_` IDs and result versions use `ver_` IDs. A choice is identified by its offering `version_id` plus local `choice_id`. The same local ID can recur in another version. Refinement must name the exact parent's offered pair, not a same-named choice from an older result.

Lineage distinguishes initial results, food-choice refinement and return to all options. Referenced parents/snapshots must exist; self-parenting and cycles fail. Parents may appear after children, and refinement can use a newer snapshot. Static lineage validation does not establish the currently active version or prevent stale browser commands; those are runtime responsibilities.

## Run outcomes and events

Search, run, attempt and recommendation-version IDs identify different things. Retry preserves the run's original purpose under a new attempt. An attempt is absent before claim; running and completed snapshots require one. Queued, running, cancelling, completed and cancelled are modeled states. Cancellation is outside the research outcome union.

| Completed outcome | Required meaning |
|---|---|
| `ready` | Useful result with a published version. A justified smaller set can qualify. |
| `partial` | Useful published result plus nonempty material limitations. |
| `no_matches` | Assessed scope and explanation supporting no suitable match; no new version. |
| `insufficient_coverage` | Nonempty gaps preventing a useful result or a no-match conclusion; no new version. |
| `technical_failure` | Bounded failure code, safe message and retryability; no new version. |

Timeouts and provider failures cannot establish no matches. A failed/cancelled refinement can retain an earlier permitted display result without claiming new publication. `RunReadResponse` requires referenced display versions in a same-search collection; ready/partial outcomes must identify that displayed version and a nonempty selection. It does not authenticate records or identify a globally active version.

`RunEvent.schema_version` is strict integer 1. Sequences are positive and run-scoped across attempts; cursors can be zero. Booleans are invalid counters. A ready/partial completion's `result_version_id` must match its outcome; it is explicitly null otherwise. Events exclude request coordinates, provider bodies, raw reviews and complete evidence collections, but text still needs safe projection.

`EventBatch` rejects mixed run/search identities and duplicate/decreasing sequences. Gaps are valid in a supplied suffix; this does not prove durable completeness or atomic publication. [Persistence](DATABASE.md) owns those guarantees, and [SSE](API.md#event-stream) defines reconnect transport. Current writers support queued/running and completed coverage-gap/technical-failure paths only. Other modeled transitions still lack writers.

## Contract examples

Committed [search](examples/search-contract/), [evidence](examples/evidence-contract/), [recommendation](examples/recommendation-contract/) and [lifecycle](examples/lifecycle-contract/) examples are synthetic complete payloads. Useful checks include swapped-branch menu/complaint support, source IDs used as evidence, empty samples mislabeled zero, discovery-only recommendations, stale offering-version pairs, lineage cycles and contradictory outcome/result references. Their accepted counterparts preserve valid shared-source and multi-place findings.

The generators beside the example files rebuild them. Search examples are directly committed; evidence/recommendation generators use standard Python, while the lifecycle generator uses the installed backend environment. Generating JSON or parsing a model does not verify provider accuracy, source permission, current freshness, HTTP ownership or the real dining journey.
