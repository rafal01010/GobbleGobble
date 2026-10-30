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

[Location resolution](../apps/backend/app/location.py) maps Geoapify building and amenity results with valid time zones to specific origins. It preserves multiple matches as typed choices. Administrative areas, streets, unknown result types and missing time zones produce refinement choices with no search origin. A user can select a point choice by passing its origin to the start contract. Current coordinates and comma-separated chosen coordinates keep their exact points during reverse lookup. Provider-supplied time zone accuracy and coverage have not yet been verified live.

Walking defaults to 15 minutes and accepts positive integer minutes. Driving uses exactly 30 integer minutes. Booleans, numeric strings and floating point limits are invalid. There is no country whitelist or schema-imposed maximum walking limit.

Each `Place` identifies one branch, venue or stall within a search. A stall can name a containing venue while retaining its own ID. A chain name is not branch identity. Provider identifiers remain external references, not internal place IDs.

[Outlet reconciliation](../apps/backend/app/identity.py) accepts `IdentityLead` wrappers around original geographic or web leads. Callers assign unique local lead IDs and an explicitly assessed branch, venue or stall kind, or leave the kind unknown. Exact Geoapify IDs supply same-record matches. Other matches require an `IdentityAssertion` scoped to two supplied lead IDs, a citation-bearing `WebMealLead` and the caller's explanation. Citation presence does not establish that the source supports the assertion. The caller remains responsible for that assessment. Names, addresses, coordinates and shared citation URLs never create matches.

`reconcile_outlets` returns ephemeral groups with the original observations and assertions intact. A transitive group with conflicting kinds, explicit distinct-place evidence or different parent claims stays separate. Explicit containment retains a branch or stall's own identity and requires an explicitly identified venue parent. Conflicting or unknown containment remains unresolved. Local `identity_` IDs identify only this result and depend on input order. These groups are not `Place` records, do not infer time zones or practical facts, and do not authorize source retention. Admitted-search and recommendation integration remain incomplete.

Unknown journey/opening facts assert no duration or open/closed conclusion. An estimated journey has mode and finite nonnegative duration, but eligibility still requires comparison with the request. An assessed opening separates venue opening from meal service. An open venue need not be serving food. None of these shapes proves source support or freshness.

## Route evidence and travel limits

[`JourneyQualifier`](../apps/backend/app/journey.py) calls the configured Geoapify routing client with the exact request origin and selected walking or driving mode. The caller supplies an endpoint and its local `identity_id`. The result retains that association, the chosen constraint and the exact endpoint. It does not choose between reconciled lead locations, infer a web lead's coordinates or transfer a venue route to a contained stall. The caller must establish that the endpoint belongs to that outlet.

A `JourneyEstimate` retains the actual `RouteAvailable`, its provider datasource and Geoapify/OpenStreetMap credits, the shared call receipt and an `EstimatedJourney` fact. `within_limit` compares provider seconds with the selected minutes multiplied by 60. Equality qualifies for this comparison. Over-limit estimates remain available with `within_limit=False`. This comparison establishes only the route-duration criterion, not eat-now eligibility or an arrival guarantee.

`observed_at` is the UTC instant when the service receives the parsed outcome. `receipt.started_at` is the separate request-start time. Neither is a provider measurement timestamp or the dining instant. Driving uses the provider's free-flow estimate without live traffic, parking or time to reach a vehicle. Walking pace, access and conditions can change actual travel time. Freshness expiry and reacquisition are not implemented here.

`JourneyUnavailable` retains the selected mode and limit, observation time and an unknown journey with no duration. Its reason distinguishes an empty provider route, a stopped call and unconfigured routing. Stopped calls retain their receipt and its diagnostic, including exhaustion of the shared allowance. Unconfigured routing has no provider observation or receipt; its timestamp records when the local configuration gap was observed. No unavailable result asserts that a place is within the limit or that no nearby places exist. No straight-line distance becomes a journey duration.

The following examples are synthetic outcomes. The focused checks in [`test_journey.py`](../apps/backend/tests/test_journey.py) execute the actual provider client with an HTTP mock and compare literal metrics, routing parameters, source attribution and limit outcomes. Observation timestamps come from the local clock and are checked against the request start and test interval. These checks do not establish current provider access or route quality.

| Mode and selected limit | Provider response | Local outcome |
|---|---|---|
| Walk, 15 minutes | 900 seconds, 987.25 metres | Estimated, `within_limit=True`, `no_live_traffic` basis. |
| Walk, 15 minutes | 900.1 seconds, 987.25 metres | Estimated, `within_limit=False`. |
| Walk, 5 minutes | 301 seconds, 987.25 metres | Estimated, `within_limit=False`. |
| Drive, 30 minutes | 1800 seconds, 987.25 metres | Estimated, `within_limit=True`, driving limitations retained. |
| Drive, 30 minutes | 1800.1 seconds, 987.25 metres | Estimated, `within_limit=False`. |
| Drive, 30 minutes | Empty route list | Unknown, `provider_returned_no_routes`, successful call receipt retained. |
| Drive, 30 minutes | Malformed response or HTTP 503 | Unknown, `provider_stopped`, failure receipt retained. |

For example, a walking request started at `2026-10-28T04:00:00Z` whose 900-second response was received locally at `2026-10-28T04:00:01Z` has `observed_at=2026-10-28T04:00:01Z`. The two timestamps and duration describe different facts. This is an illustrative timestamp example, not a recorded provider observation.

## Opening and meal-service periods

[`assess_opening`](../apps/backend/app/opening.py) evaluates caller-interpreted `HoursEvidence` against the original `eat_now_at` instant and estimated arrival. The caller supplies the exact destination's IANA timezone, place ID and journey fact. The service does not infer a destination timezone from the origin or establish that the supplied journey belongs to that outlet. Arrival adds elapsed route seconds in UTC before conversion to destination local time. An unknown journey has no estimated arrival.

Each schedule separates venue opening from meal service and retains its supporting evidence IDs. Every referenced evidence record must exist in the supplied `EvidenceCollection` and cover the assessed place. Shared sources and parent venues do not transfer support to a branch or stall. The caller must establish that the cited statement supports the interpreted periods, complete coverage, local-date applicability and exceptions. This service does not parse source prose, verify source truth, grant use permission or acquire hours. It retains ephemeral assertions rather than publishing recommendations.

Weekly periods use Monday `0` through Sunday `6` and integer minutes after midnight. A smaller end minute means the next day. Starts are inclusive and ends exclusive. `0` to `1440` explicitly means all day, while equal endpoints are rejected as ambiguous. Date exceptions replace periods starting on that date, including an empty tuple for no starts. A previous day's overnight period can still continue into that date. A `complete` schedule permits a closed or not-serving conclusion outside its periods. A `partial` schedule supports only positive matches. Inclusive `valid_from` and `valid_through` dates scope each evaluated local date. Callers must restrict those dates or supply exceptions when holidays or seasonal hours are uncertain.

The result preserves separate opening and meal-service facts at each instant. `availability` is `available` only when both facts are positive now and at arrival. A closed or not-serving fact at either instant yields `unavailable`, including a place that opens during the journey. Missing, conflicting, expired or inapplicable hours stay unknown. An explicit evidence `fresh_until` is exclusive and checked at each target instant. Missing expiry remains an explicit qualification, rather than proof of freshness. Retrieval after the request instant is allowed because research normally follows a request. Neither retrieval time nor this evaluation proves historical opening state.

Period boundaries resolve through the destination timezone to UTC with a roundtrip check. Unambiguous periods spanning a clock change cover the actual elapsed interval. A missing spring-forward boundary or a repeated fall-back boundary leaves that period uncertain because the source did not choose an instant. The service does not invent a DST convention. Availability covers only the two assessed instants, not uninterrupted service between them, seating, last orders or time to finish a meal.

The focused cases in [`test_opening.py`](../apps/backend/tests/test_opening.py) exercise the actual evaluator with synthetic evidence. No live hours or provider coverage are established.

| Supplied local hours and assessment | Synthetic outcome |
|---|---|
| Manila daily 09:00 to 22:00, request `2026-10-28T04:00:00Z`, 900-second journey | Now 12:00 and arrival 12:15 at `+08:00`. Opening and meal service separately qualify when both schedules are supplied. |
| Same instant and destination New York | Now 00:00 at `-04:00`. Complete 09:00 to 22:00 opening hours establish closed. |
| Manila opening until 22:00, meal service until 21:55, now 21:45 with 900-second journey | Arrival at 22:00 is closed and not serving. Availability is unavailable. |
| Sunday 22:00 to Monday 02:00, now Monday 01:50 with 600-second journey | Open now and closed at the exclusive 02:00 arrival boundary. |
| New York `2026-03-08T06:50:00Z` plus 1,200 seconds, period 00:00 to 04:00 | Arrival is 03:10 at `-04:00`, after the spring clock jump. |
| New York `2026-11-01T05:50:00Z` plus 1,200 seconds, period 00:00 to 04:00 | Arrival is the second 01:10 at `-05:00`, after the fall clock change. |
| Missing hours, partial hours outside known periods, or a 01:00 to 01:30 boundary on the New York fall-back date | Unknown opening, with no available conclusion. |

## Candidate assembly and practical qualification

[`assemble_candidates`](../apps/backend/app/candidates.py) combines an original `SearchRequest`, `IdentityReconciliation`, explicit `CandidateBinding` records and an `EvidenceCollection`. A binding associates one reconciled outlet identity with one resolved `Place`, an optional journey assessment and source-linked local hours. This mapping is a caller assessment. It does not independently establish source truth or identify a web lead.

Bindings must have unique known identity IDs and unique place IDs within the supplied evidence catalog. The place kind must match the reconciled outlet kind. Parent identities must have their own binding, and their place IDs must match the child's `parent_place_id`. Provider-backed locations must match an observed lead point, and supplied Geoapify references must belong to that outlet. A provider endpoint from another outlet is rejected even when coordinates coincide. Point-only endpoints require the explicitly bound coordinates. Web-only resolved places depend on the caller's declared location association.

Journey checks compare the bound identity, original origin coordinates, travel mode and limit, destination coordinates, and the route's mode, duration, distance and estimate basis. The assembler retains the original route assessment, receipt and provenance. It computes opening and meal service itself from the matched journey, original dining instant, exact place timezone and scoped schedules. A stale or different non-unknown journey or opening fact already on the supplied `Place` is rejected. The projected place has the assembled journey and opening-now fact, while `CandidateView.opening` retains both now and arrival facts and their source-linked schedules.

The result separates `qualified`, `unconfirmed` and `excluded` tuples. Input order is preserved within each group. Each view retains an independent `travel` state and reason codes.

| Practical facts | Candidate outcome |
|---|---|
| Route estimate at or below the selected limit, open and serving now and at arrival, no identity conflict | `qualified` with `travel=within_limit`. |
| Verified route, missing or uncertain service information | `unconfirmed` with `travel=within_limit` and `service_unconfirmed`. |
| Unavailable or absent route, no known closure or service exclusion | `unconfirmed` with `travel=unconfirmed` and `route_unconfirmed`. No arrival is invented. |
| Known over-limit route | `excluded` with `over_travel_limit`. |
| Known closed or not serving now or at arrival | `excluded`, including when the route is unavailable. The reason names the fact and instant. |
| Conflicting or unresolved identity assertions affecting the outlet | `unconfirmed` with `identity_unconfirmed`, unless a known practical exclusion already requires `excluded`. |

The result retains the complete supplied identity reconciliation and evidence catalog, including source declarations, qualifications and unknown facts. Outlets without bindings remain in `unresolved_outlets`, including web-only leads without resolved locations or kinds. Empty groups establish no coverage claim. These ephemeral views support further research. They do not create researched snapshots, rank food quality, publish recommendations, grant source-use permission or establish live provider coverage. The focused synthetic checks in [`test_candidates.py`](../apps/backend/tests/test_candidates.py) exercise assembly without external calls.

## Evidence and branch attribution

A source is an attributable document, listing or review origin. Evidence is a finding from that source. Food/review findings cite evidence IDs, not source IDs. The `src_` and `ev_` namespaces are distinct from URLs and provider identifiers.

Sources declare place scopes, and each evidence record declares its own subject places within that scope. A shared restaurant-group page may cover two branches while one menu finding covers only one. Sharing a page never transfers every finding to every branch.

Asserted findings bind evidence separately for each subject place. Every referenced evidence record must cover that exact subject. A two-place claim needs explicit support for both places; collecting separate A and B evidence in one array does not establish support for both. `EvidenceCollection` checks uniqueness, existence, identifier category, declared scope and review-sample membership.

Sourced food claims require support. Inferred claims also need a nonblank explanation of the inference. Unknown claims record a gap rather than an asserted food fact. Optional menus, prices and complaints retain the same exact-subject checks. A review mentioning a dish does not establish that it is currently on the menu.

These validators check declared relationships, not whether prose actually supports a dish, price or paraphrase. Production interpretation and publication must establish that separately.

[`interpret_food`](../apps/backend/app/food_claims.py) adds food claims to an evidence collection. It copies each caller-classified source statement and its branch-specific evidence reference. For an inferred suggestion, it reproduces the complete cited statement, records the basis, and says that current availability and price are unconfirmed. It does not accept a free-text inference that could add an unsupported dish or price. Menu and price gaps can be recorded without inventing a value. The caller must classify the source and assess whether its statement is relevant to food. This function does not establish source truth, freshness, or permission to use or retain source content. It is not connected to the research agent.

### Freshness and retention of derived content

[`assess_artifacts`](../apps/backend/app/evidence_validity.py) evaluates declared evidence dependencies for claims, summaries, choices, results, events, and traces. Each artifact must identify evidence or another artifact with evidence. The evaluator rejects missing, cyclic, or wrong-branch dependencies. Its result reports freshness, retention, and source-use permission separately. An expiry is exclusive: content expires at the stated instant. Missing freshness is unconfirmed. Retention of a stale observation does not make it current.

Call the evaluator for each intended display, storage, or trace use. A display needs a publisher and HTTPS source URL. Google review evidence also needs a named author attribution. The evaluator relies on the caller to supply complete lineage and the correct source class. It does not inspect the artifact's text or establish that evidence supports it. Time-limited content is not accepted for storage because automatic deletion is not implemented.

[`lookup_fresh_evidence`](../apps/backend/app/db/evidence.py) returns current stored evidence and findings and lists stale and unconfirmed finding IDs. It checks freshness only, not permission. `lookup_evidence` remains a historical lookup and does not filter stale records. The recommendation publication path is not implemented and does not yet call the evaluator. Current status events and receipts contain no research text, and the SDK probe disables content tracing.

[`project_evidence`](../apps/backend/app/evidence_projection.py) builds an ephemeral display view for one place. It checks each finding's actual evidence dependencies for freshness and source-use permission, and includes scoped evidence IDs, source IDs, publisher, and HTTPS links. Review citations include the author and distinguish review links from branch links. Inferred findings are labeled. The view replaces omitted or unconfirmed information with short, fixed explanations and includes sample counts only when it can display an associated finding. It does not copy raw source payloads or policy diagnostics. Links with credentials, query strings, fragments, or unsafe schemes are omitted. This conservative rule can omit valid provider links. Callers must check that finding text and publisher or author names are accurate and safe to publish. This function is not connected to result rendering.

## Reviews, fallback and use policy

[`interpret_reviews`](../apps/backend/app/review_interpretation.py) attaches caller-interpreted findings to one Google review assessment. Each finding records a topic, polarity, source review, and whether its statement is a paraphrase or a quotation. The result retains the original review evidence and sample limits, including selection method, partial status, and whether the sample represents a complete history. A mixed sample does not automatically exclude a place. The function accepts a confirmed zero-review outcome with no findings.

Quotation statements must appear exactly in the original review and have a named or linked author attribution. Branch and assessed-sample relationships are checked against the evidence collection. The caller must verify that paraphrases, polarity, reported speech, and quoted excerpts preserve the review's meaning and context. Review findings retain polarity and wording labels in the collection, permitted storage, and display projection. A quotation must match its attributed evidence. Existing findings without these labels retain an unclear polarity and unspecified wording. The function grants no permission to store or display material and is not connected to the research agent.

Google branch identity and individual review identity are separate. Locators distinguish review-level links from branch links and preserve available publisher/author attribution. Missing attribution remains missing.

The model distinguishes matched accessible evidence, confirmed zero reviews, no confident branch match, restricted use and provider failure. An accessible sample may be populated, empty or omitted. Empty/omitted samples never establish a total of zero. Reported total, returned size, assessed count, selection/order, bounds, partial retrieval and omissions remain distinct facts. Selected negative reviews do not establish uniformly negative reception.

Google findings must cite assessed evidence from the declared primary sample and branch. Independent review fallback has its own sample; independent food fallback has its own evidence list. Both retain source identity and a reason consistent with the Google outcome. Fallback evidence cannot masquerade as Google reviews or cite another branch's sample.

Source declarations separately govern access, display, model input, derived output and tracing. Raw content, derived content and identifiers have distinct retention decisions. Retention permission is not factual freshness. These records do not schedule deletion, test the current clock or grant rights because a model supplied them. Follow [integration and source-use policy](INTEGRATIONS.md).

### Stored evidence lookup

[`store_evidence`](../apps/backend/app/db/evidence.py) accepts a validated collection, an owned search and a trusted source-class decision for each source. For admitted independent sources, it stores evidence and findings only when raw content, derived content and identifiers all have indefinite retention and derived output is allowed. Unknown and time-limited content is skipped because scheduled deletion is not implemented. Source classification and subject associations are upstream assertions; the store cannot prove their truth or detect copied restricted content.

For Google sources, the store retains only permitted numeric CIDs or feature IDs with their place association. It does not store review text, author details, source URLs or dependent findings. A Google place ID alone is not retained. [`lookup_evidence`](../apps/backend/app/db/evidence.py) returns source context for the requested place and returns evidence and findings only for their explicitly associated subjects. A shared page does not transfer one branch's menu or complaint to another branch. Restricted content must be reacquired when needed. The store is not yet connected to production research.

[`ResearchTools`](../apps/backend/app/research_tools.py) binds a research task to an owned search, a snapshot, and a fixed set of places. It pages permitted, attempt-local Google review evidence with continuation IDs and reports the number of policy-restricted items. It returns the original source identity, attribution, and available sample details for a single place. A separate sample page identifies which reviews were assessed. Long review text is returned in bounded pieces with a character offset and an omitted-character count. It reports stale, unknown-freshness, restricted, unavailable, and oversized content without returning that content. Retained independent findings remain restricted for model use under the current source policy. Opaque, attempt-local handles allow scoped lookup without exposing stored finding IDs or content. The tool checks freshness at each read. The research agent can register these tools when supplied with a bound instance. Its source classes and source declarations are trusted inputs from the server. The agent keeps valid proposals when a sibling proposal has an invalid source reference, and reports the omissions. When review context does not fit in the tool result, the review ID remains available for a bounded source lookup. A separate model can assess proposals against the original reviews; its decisions are judgments and require independent review before use as verified claims.

[`prepare_review_tool`](../apps/backend/app/agents/research.py) registers a place research agent as an SDK tool. It sends the original matched public review text and attribution with the typed search request. The agent receives a bounded sample with omissions and continuation information. An oversized review has a reacquisition handle. `read_review_chunk` reads its original text in bounded pieces while the sample is available. It can use scoped evidence tools when supplied. Returned proposals keep review references, sentiment, and quotation or inference labels. Every proposal remains unverified until a caller records a source-support assessment. Exact quotations must match an attributed portion supplied to the agent, but the match does not verify their interpretation. The tool returns the original review alongside each proposal so a reviewer can inspect its context. The returned contributions are ephemeral and cannot be retained without a source-policy decision. Earlier contributions survive a later helper failure. The agent does not publish recommendations or establish current menu availability. It is not connected to admitted searches.

The production review requirement permits relevant public text and attribution, including complaints, personal references and local languages. The legacy probe's restrictive minimizer is not that production policy. Retrieval/admission/fallback integration remains incomplete.

## Recommendation owner

[`prepare_owner`](../apps/backend/app/agents/owner.py) binds the configured SDK owner to a search request and a research snapshot. It supplies scoped evidence, eligible food-claim IDs, geographic lookup and prepared place-research tools. Candidate details and claims are available only when their declared source permits model input. The caller is responsible for source classifications and for constructing research tools from the same owned search. Before each specialist model call, the owner requires the selected review to match fresh snapshot evidence for its branch, source link and full text. Missing or ambiguous matches produce a gap. A specialist with nested evidence lookup must use the owner’s bound research tools.

[`execute_owner`](../apps/backend/app/agents/owner_execution.py) shares one attempt budget with nested tools. It reserves capacity for a final response, closes research when needed and retains bounded observations for composition. It rechecks retained evidence before each model call and removes content that has expired or become restricted. If a final response is malformed or incomplete, it can make one tools-free recovery when the remaining allowance permits. Exhausted capacity returns a technical failure with retained progress; it does not establish that no places match. Retained observations are ephemeral and must follow the source retention policy. The executor is not connected to the normal search worker.

The model returns an `OwnerDraft` with ordered selections and food-claim references. [`validate_owner_draft`](../apps/backend/app/agents/owner_draft.py) binds the search, request, snapshot and version supplied by the server, then applies the existing recommendation relationship checks. The executor also rejects claim IDs that were not shown to the owner for that place or no longer pass source and freshness checks. These checks do not establish that explanatory prose is supported by its cited sources. Publication validation and normal result rendering are separate work.

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
