# Integration candidates and source-use findings

This reference records GG-001.02 findings as of 2026-10-23. It implements the source comparison required by the [product contract](PRODUCT.md). It does not establish live access, select the delivered configuration, or implement an adapter. Section verification and final provider decisions remain at GG-003.

## Proposed paths

Google remains preferred where its capability and terms fit the application. The Google-first candidate is Maps Grounding Lite for model-facing place discovery and journeys. Ordinary Places data is a separate product with different permissions. Neither is approved for this application's model and persistence flow yet. [Google grounding documentation][g-grounding] and [Maps terms][g-terms] establish that distinction.

The proposed alternative is Geoapify for geographic lookup, discovery, and mode-specific routes, plus OpenAI Responses web search for food research. Geoapify explicitly documents AI-agent use and reusable geographic results. This is a plausible path to probe, not a measured coverage or quality finding. Optional menus and reviews come from attributable, permitted sources encountered during research. [Geoapify AI tools][a-ai], [Places reuse][a-places], and [OpenAI web search][o-web] support this proposal.

No provider, model, account entitlement, or key has been tested or configured by GG-001.02. Documentation access was its only external activity. GG-002.02 supplies local settings and credential preflight. GG-002.03 supplies the [bounded call layer and safe receipts](CALLS.md). GG-002.04 prepares the [Geoapify location/discovery client](GEOGRAPHY_PROBES.md). GG-002.05 adds [walking/driving route requests and explicit failures](ROUTE_PROBES.md) in that client. Its Google selections fail explicitly without fallback. Final provider selection, live access, and model compatibility remain GG-003. GG-002.06 prepares [OpenAI web search and citation capture](WEB_RESEARCH_PROBES.md). Its returned evidence remains ephemeral and requires source checks before downstream use. SDK agent wiring and the probe command remain scheduled work.

## Capability comparison

The table separates documented capabilities from suitability still to be demonstrated. Endpoint and tool names identify candidates for GG-002, not new deliverables in this task.

| Capability | Google candidate | Alternative and proposed use | Uncertainty for GG-003 and later gates |
|---|---|---|---|
| Current coordinates | Browser geolocation, independent of Maps API access | The same browser Geolocation API, with manual entry when permission fails. No application API key is required for the browser call. [W3C specification][browser-location] | Requires user permission and a secure context. Accuracy and device availability are not guaranteed. Actual browser acceptance belongs to GG-015 and GG-025. |
| Chosen location and coordinate context | Grounding Lite `resolve_names` supplies place identifiers. Google Geocoding supplies address coordinates and reverse lookup. [Name resolution][g-resolve], [Geocoding][g-geocode] | Geoapify forward and reverse geocoding supplies coordinates, structured addresses, and timezone context. [Geocoding][a-geocode], [API reference][a-geocode-api] | A resolved name is not necessarily a usable coordinate or an unambiguous origin. Test romanized Japanese input, coordinate order, and local-time context. Do not infer the destination timezone from the server. |
| Geographic meal discovery | Places API New text/nearby search supplies identifiers, coordinates, service/opening fields, ratings, and limited reviews. Grounding Lite `search_places` supplies a summary and place sources. [Place resource][g-place], [Grounding search][g-search] | Geoapify Places searches catering categories within a radius or area. Its reference includes restaurant, cafe, fast-food, and food-court categories. [Places API reference][a-places-api] | Stall and branch completeness, missing tags, duplicates, and opening facts require observation. Neither source establishes complete worldwide coverage. |
| Walking and driving journeys | Grounding Lite `compute_routes` provides distance and duration for both modes, without live traffic or navigation. Ordinary Routes `computeRoutes` also exposes mode-specific routes and requested duration fields. [Grounding routes][g-grounding-routes], [Routes][g-routes] | Geoapify Routing supports `mode=walk` and `mode=drive`, returning time and distance. Its default traffic setting is free-flow. [Routing API reference][a-routing-api] | Test both modes and unavailable routes. Distinguish estimates from guarantees. A radius, a straight-line distance, or a walking result cannot establish a 30-minute drive. |
| Additional discovery and live food research | Grounding summaries can supplement Maps facts, subject to their own output restrictions | OpenAI Responses `web_search` supplies sourced answers, URL annotations, and consulted-source metadata. It supports domain controls and live web access. [Web search][o-web] | Verify actual search invocation, useful local-language sources, branch identity, elapsed time, usage, and cost. A cited answer is not proof that every claim is supported. |
| Menus, dishes, and prices | Places food/service attributes and price ranges do not establish an exact menu item or its current price. [Place resource][g-place] | Research an outlet's own menu or another permitted food source through web search. Retain only supported claims under the source's policy. [Web search][o-web] | No universal menu feed is established. Branch, currency, publication date, and eat-now availability can differ. Unsupported food suggestions require an inference label. |
| Reviews and complaints | Places returns at most five reviews, ordered by relevance. Review summaries have regional/language limits and are not guaranteed. [Place resource][g-place], [Review summaries][g-reviews], [Review ordering][g-policies] | Research permitted publisher/reviewer sources through web search. Geoapify is proposed for geography, not as a verified review corpus. | No complete or representative review sample is established. Preserve source, dates, sample selection, and complaints. Missing reviews and a small negative sample cannot establish uniformly bad reviews. |

## Permission findings

An API response proves access only. These findings separately address model input, display, storage, derived output, and tracing. Conditional means the proposed use remains closed until its stated condition is satisfied.

| Source class | API access | Model input | Attribution and display | Storage | Derived output | Tracing |
|---|---|---|---|---|---|---|
| Ordinary Google Maps APIs, including Places | Requires the enabled service, account, key, billing, and quota | Not cleared for this recommendation flow. The core terms restrict content creation and AI-model improvement. Ordinary API availability does not supply Grounding Lite's exception. [Core terms][g-terms] | Maps attribution and returned provider/author links apply. Places requires application terms/privacy notices. [Places policies][g-policies] | No general database right. Place IDs are exempt from caching restrictions. Other fields require a specific exception. [Places policies][g-policies] | No assumed right to turn reviews or other Maps fields into saved recommendation facts. [Core terms][g-terms] | No raw payloads or content-bearing model traces under the proposed policy. |
| Maps Grounding Lite | Separate enabled service and credentials. [Setup][g-grounding] | Explicit grounding exception, subject to compatible LLM requirements. Google requires no model-side caching, storage, or improvement. [Compatibility][g-grounding] | Associated Maps sources must follow the supported output and be available within one interaction. [Attribution][g-grounding] | Grounded Output has a maximum 30-day cache exception only for the specified evaluation/optimization purposes. It is not blanket durable result storage. [Service terms, section 10][g-service] | Grounding is permitted, but extraction, modification/interspersing, and AI-system improvement restrictions remain. Recombination into structured evidence and comparative cards is unresolved. [Service terms, section 10][g-service] | No assumed permission for hosted traces or saved tool payloads. OpenAI compatibility must be resolved first. |
| Geoapify geographic results | Account, project, API key, and plan limits apply. [Terms][a-terms] | Documented AI-agent uses include place and route results in follow-up reasoning. This supports inference use, not a decision to train models. [AI tools][a-ai] | OpenStreetMap attribution always applies. Geoapify attribution applies on the Free plan. Additional returned data-source requirements also apply. [Terms][a-terms] | Places results may be cached, stored, and redistributed. Geocoding and routing pages also allow result reuse. [Places][a-places], [Geocoding][a-geocode], [Routing][a-routing] | Retain provenance and underlying data licenses. OSM copying/adaptation is permitted with attribution and applicable ODbL obligations. [OSM license][osm-license] | No separate trace-storage entitlement was established. Proposed policy disables content tracing and keeps only safe call metadata. |
| OpenAI hosted web research and third-party pages | API access enables hosted research. It does not establish a separate right to scrape every linked site. [Web search][o-web] | Hosted search is designed to inform model answers. Direct fetching or re-ingestion of a publisher's text requires that source's applicable permission. | Visible, clickable inline citations are required for displayed web-derived information. Preserve publisher and claim links. [Citation requirement][o-web] | No blanket right to archive raw menus, reviews, images, or full pages follows from search access. Unknown publisher retention remains unapproved. | Proposed policy allows concise attributed findings only after checking the source's applicable restrictions. A paraphrase does not erase restrictions on its source. | No publisher text, prompts, or answers in hosted traces under the proposed policy. Sanitized receipts do not establish permission to retain content. |

The Google service terms reviewed here apply to non-EEA billing accounts. An EEA billing address invokes a different agreement. The user's timezone does not establish their billing jurisdiction. This task does not make that account determination. [Applicability][g-service].

Known ordinary Places exceptions include indefinite Place ID storage and up to 30 consecutive days for latitude/longitude. These do not authorize archiving reviews or place descriptions. [Places policies][g-policies], [Service terms, section 14][g-service].

Google's AI review summaries also require full supplied text, the supplied disclosure, a "Review summary" heading, and the prescribed about/report/reviews links. They cannot be treated as an unrestricted replacement for review evidence. [Summary display requirements][g-policies].

## OpenAI processing and diagnostics

The OpenAI API does not use API data for model training or improvement by default. That is separate from storage. Default abuse-monitoring logs may retain content for up to 30 days, with exceptions. Responses can retain application state. `store=false` does not itself remove abuse logs or prompt caches. Zero Data Retention requires approval and has model/feature limitations. Prompt caching can persist model state, so even a ZDR entitlement alone does not settle Google's no-caching condition. [OpenAI data controls][o-data].

The Agents SDK enables tracing by default and can record tool outputs and model calls. The proposed initial setting disables content-bearing tracing and uses sanitized local call receipts instead. This is a requirement for future probe implementation, not a setting enabled by this document. [SDK observability][o-observability].

Google model compatibility remains unresolved. A successful request, `store=false`, a no-training default, or disabled SDK traces alone cannot close it. GG-003 needs evidence for the exact account, model, caching, and retention combination before sending restricted Maps content into a model. Otherwise, that gate can select the permitted alternative.

GG-002.03 implements receipts with provider identity, safely named requested model identity where available, capability, timestamp, elapsed time, outcome, usage, and cost where available. Unreviewed model text is explicitly omitted and never changes the configured model used by an adapter. Receipts omit keys, authorization headers, request URLs, precise user locations, arbitrary exception text, and response content. The [call reference](CALLS.md) defines the exact projection, bounds, and source-use admission. Provider retry/tracing controls remain with the scheduled adapters. Saved outputs, checkpoints, events, screenshots, and evaluation captures inherit source restrictions. Their broader enforcement remains GG-009. Metadata receipts grant no payload retention or reuse permission.

## Credential and account requirements

These are setup requirements for later live tasks. Their completion and the local secret configuration were not inspected. `OPENAI_API_KEY` is the SDK convention. GG-002.02 formalizes `GOOGLE_MAPS_API_KEY`, `GEOAPIFY_API_KEY`, capability-specific provider selections, model IDs, and timeouts in the [settings inventory](SETTINGS.md#variables). Local preflight checks presence and syntax without establishing service access or source permission.

| Service | Required account and setup | Local inputs | Access still to establish |
|---|---|---|---|
| OpenAI | API project, server-side API key, available billing/credits, and access to the selected owner/research models and web search. [API quickstart][o-start] | `OPENAI_API_KEY`, explicit owner/research model IDs, and bounded request/usage settings | Model/tool access, rate limits, budget, and any required retention entitlement. ChatGPT subscription access alone is not evidence of API access. |
| Google Grounding Lite | Cloud project, billing, enabled Maps Grounding Lite service, restricted API key or OAuth credentials. [Official setup][g-grounding] | `GOOGLE_MAPS_API_KEY` for the planned server-side `X-Goog-Api-Key` path. OAuth loading is not implemented. | Billing jurisdiction, actual service access, compatible LLM conditions, output handling, and permitted retention. No demo-key result can establish the delivered account's access. |
| Ordinary Google APIs, if selected | Individually enabled Places, Geocoding, or Routes service and appropriate credential/quota configuration | `GOOGLE_MAPS_API_KEY` with only selected services enabled | Existing Grounding Lite access does not establish access or permission for these separate products. Field selection and product billing need confirmation. [Places setup][g-setup], [Routes setup][g-routes-setup] |
| Geoapify alternative | Registered account, project, API key, and an applicable plan. Free currently advertises 3,000 credits/day and up to five requests/second. [Pricing][a-pricing] | `GEOAPIFY_API_KEY` and capability selections. Endpoint, rate, and call-limit enforcement belongs to later probe tasks. | Confirm credits and relevant endpoint availability. The Free plan's 15-minute isochrone limit is not a 15-minute point-to-point routing limit. A paid 30-minute isochrone is not inherently required to check a drive. |
| Publisher menus/reviews | Source-specific permission and access requirements. No universal credential or licensed review feed is established | Source URL and policy decision, without secrets | Paywalls, logins, robots/access restrictions, retention, and reuse conditions remain source-specific. Optional unavailable evidence is reported as unavailable. |
| Browser location | Supported browser, secure context, and user permission. [Geolocation][browser-location] | Coordinates and accuracy from the browser | Denial/manual fallback and actual device coordinates remain browser-gate checks. |

Geoapify's privacy policy says it records request information including bodies, headers, IP addresses, and timestamps. Successful request records are generally retained no longer than 24 hours. Local redaction does not prevent provider-side processing. [Provider privacy policy][a-privacy].

## Freshness and retention

Factual freshness and retention permission are separate. A stored result can be legally reusable but too old for an eat-now claim. A current fact can still be prohibited from storage. Later evidence policies need both a freshness decision and a retention decision for each source and its derived claims.

Opening/service evidence needs the destination's local date and time. Exact menu prices need their actual source date or an explicit unknown-date qualification. Journey records need their mode, observation time, and estimation limits. This document does not invent freshness windows before the feasibility probe.

The proposed default for an unknown source policy is no raw-content persistence and no content tracing. Unknown model or derived-output permission also prevents that source from entering model research or publication. Omitting raw text alone does not clear those uses. A restriction or expiry follows all dependent summaries, checkpoints, events, and saved results. The OSM license and provider terms continue to apply to reusable geographic records. [OSM license][osm-license], [Geoapify terms][a-terms].

## Open decisions and completion boundaries

| Decision | Current finding | Resolution task and required evidence |
|---|---|---|
| Google model compatibility | Conditional, not cleared. Default OpenAI retention and caching prevent an assumption of compatibility. | GG-003 records the actual account/model controls and a supported permission conclusion, or selects the alternative. |
| Google output reuse | Structured extraction, comparative composition, durable results, and refinement are not cleared by the limited grounding exception. | GG-003 establishes the permitted use for the intended flow. GG-009 implements the chosen policy. A substantial architecture repair needs a separately requested stable backfill. |
| Geographic fallback | Geoapify documents the required capabilities, inference use, attribution, and reusable geographic results. | GG-002 prepares bounded probes. GG-003 establishes actual location/discovery/route access in multiple countries, including Japan. |
| Food and review sources | Hosted web research is a candidate. No universal menu feed, representative review corpus, or publisher archive permission is established. | GG-003 demonstrates useful permitted food research. GG-009 records per-source policy and sample limits. Later live gates verify optional-information behavior. |
| Operational limits | No latency, cost, completeness, or recommendation-quality measurement exists. | GG-003 records real usage/timing and setup gaps. GG-015 and GG-023 verify the normal journey and quality target. |

GG-001.02 is a documentation implementation. GG-003 must remain unchecked if an essential chosen capability lacks access or permission. Google reviews can remain unavailable when the selected permitted path still supports the product. No live provider/model probes, application tests, builds, or browser checks belong to this task. The [implementation plan](../IMPLEMENTATION_PLAN.md#follow-the-ordered-executable-checklist) records current task status.

[g-grounding]: https://developers.google.com/maps/ai/grounding-lite
[g-search]: https://developers.google.com/maps/ai/grounding-lite/reference/mcp/search_places
[g-resolve]: https://developers.google.com/maps/ai/grounding-lite/reference/mcp/resolve_names
[g-grounding-routes]: https://developers.google.com/maps/ai/grounding-lite/reference/mcp/compute_routes
[g-terms]: https://cloud.google.com/maps-platform/terms
[g-service]: https://cloud.google.com/maps-platform/terms/maps-service-terms
[g-policies]: https://developers.google.com/maps/documentation/places/web-service/policies
[g-place]: https://developers.google.com/maps/documentation/places/web-service/reference/rest/v1/places
[g-reviews]: https://developers.google.com/maps/documentation/places/web-service/review-summaries
[g-geocode]: https://developers.google.com/maps/documentation/geocoding/guides-v3/requests-geocoding
[g-routes]: https://developers.google.com/maps/documentation/routes/compute_route_directions
[g-setup]: https://developers.google.com/maps/documentation/places/web-service/get-api-key
[g-routes-setup]: https://developers.google.com/maps/documentation/routes/get-api-key
[a-ai]: https://www.geoapify.com/mcp/
[a-terms]: https://www.geoapify.com/terms-and-conditions/
[a-places]: https://www.geoapify.com/places-api/
[a-places-api]: https://apidocs.geoapify.com/docs/places/
[a-geocode]: https://www.geoapify.com/geocoding-api/
[a-geocode-api]: https://apidocs.geoapify.com/docs/geocoding/
[a-routing]: https://www.geoapify.com/routing-api/
[a-routing-api]: https://apidocs.geoapify.com/docs/routing/
[a-pricing]: https://www.geoapify.com/pricing/
[a-privacy]: https://www.geoapify.com/privacy-policy/
[osm-license]: https://www.openstreetmap.org/copyright
[o-web]: https://developers.openai.com/api/docs/guides/tools-web-search
[o-data]: https://developers.openai.com/api/docs/guides/your-data
[o-observability]: https://developers.openai.com/api/docs/guides/agents/integrations-observability
[o-start]: https://developers.openai.com/api/docs/quickstart
[browser-location]: https://www.w3.org/TR/geolocation/
