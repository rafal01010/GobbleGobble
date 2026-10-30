# Integrations and source policy

This reference records selected providers, source-use requirements and current limits. Location resolution uses the bounded Geoapify client through the [public route](API.md). Bounded geographic and web discovery adapters are available internally. Other clients remain development probes; production research and recommendation publication are not connected yet. Use [settings](SETTINGS.md) for exact configuration and [the probe guide](LIVE_PROBES.md) for opt-in experiments.

## Selected sources

| Capability | Selected path | Limitation |
|---|---|---|
| Location and nearby places | Geoapify geocoding and Places | Geographic leads are not qualified recommendations. |
| Walking and driving | Geoapify point-to-point routing | Estimates do not guarantee door-to-door time or current traffic. |
| Primary review evidence | Bounded anonymous Google client based on YasogaN's protocol | Requires a corroborated branch; undocumented endpoint behavior can change. |
| Supplementary food evidence | OpenAI Responses web search | Citations need checking; search does not grant blanket publisher reuse rights. |
| Model orchestration probe | Agents SDK owner with research agent as a tool | Legacy restrictive passages, not the complete production input path. |

Review retrieval is independent of geography. Geoapify does not replace Google reviews, and generic web research must not silently replace available primary review access. Independent evidence can be a labeled supplement or fallback. Wikivoyage is excluded from primary, supplementary and fallback use because of outdated information.

The anonymous review route needs no Google API key, billing account, cookie or signed-in profile. Bounded comparisons found no extra substantive review text from a signed-in scraper, so no alternate scraper is integrated as a backup. Those finite experiments do not establish future availability, global coverage or recommendation quality.

Official Places and Maps Grounding Lite are different products with separate enablement, billing, model-use, attribution and storage conditions. They are not prerequisites for the selected anonymous route. If introduced, assess each product independently rather than assuming its rules apply to another route.

## Geographic meal discovery

`app.discovery.MealDiscovery` collects Geoapify leads within a caller-supplied radius of 1 to 5,000 metres. It requests restaurants, cafes, fast food and food courts together. These categories can supply meal leads but do not establish that an outlet serves meals now. There is no country, cuisine, review-count or menu filter. Food-court stalls and branch identity still need later research.

The query defaults to three pages of 20 results. Callers can select 1 to 5 pages, 1 to 20 results per page, and a starting offset of 0 to 10,000. These are local execution bounds, not Geoapify coverage guarantees. Requests retain the same category, circle and proximity bias while advancing the [documented Places offset](https://apidocs.geoapify.com/docs/places/). Each page uses the existing shared request allowance and deadline. Full pages continue even when they contain duplicate IDs. The adapter deduplicates exact provider IDs only and keeps the first observed record, including its categories, coordinates, datasource and attribution. It does not merge branches by name or distance.

Each batch retains per-page offsets, returned counts and safe call receipts. A short page yields `short_page`, which means only that this bounded query reached an observed page end. It does not establish complete geographic coverage. A full final page yields `page_limit` and a possible `next_offset`. `provider_stopped` preserves earlier candidates and the failed page's offset for a possible later attempt. That page has an unknown count, never an invented empty result. The local offset ceiling yields `offset_limit` with no further offset. None of these outcomes proves that no meal outlets exist. Continuing requires the same query parameters and available shared allowance. Offset results can change between requests.

Synthetic receipt examples from `tests/test_meal_discovery.py` use a two-result page size. They are offline examples, not measured provider coverage.

| Page offset | Returned IDs | New candidates | Interpretation |
|---|---|---|---|
| 0 | Restaurant, cafe | 2 | Full page, continue. |
| 2 | Same cafe, food court | 1 | Duplicate does not end pagination. |
| 4 | Takeaway | 1 | Short page, observed query end. |

The resulting pool contains four distinct IDs with exact source attribution. A separate full-page example stops at the page cap and resumes at its reported offset. Failure and request-allowance examples retain the successful first page. Provider-wide Geoapify and OpenStreetMap credits remain available even for an empty batch. Candidate content stays outside safe diagnostics. These internal adapters do not yet run for admitted searches or publish recommendations.

## Supplemental web discovery

`app.web_discovery.WebMealDiscovery` makes one request through the existing bounded web-research client. `WebDiscoveryQuery` accepts a locality of up to 200 characters and a lead limit of 1 to 10, defaulting to 5. Its guidance requests English and relevant local-language searches without requiring local-script user input or restricting countries. It includes stalls, food courts, cafes and takeaway. Official outlet or venue pages and independent local food reporting can supply leads beyond map listings. The hosted-call and output-token limits remain those selected on `WebResearchClient`; all work uses that client's shared request allowance and deadline. Input-source lineage remains mandatory and passes through the existing source guard.

The requested answer puts each lead on one `LEAD ` line with source-supported names, available romanization, branch/location clues and a meal-service clue. Missing details must remain unknown. Mapping keeps the exact line excerpt and provider-supplied URL citation metadata, including title, URL, answer-block identity and absolute character offsets. A URL written in answer prose or merely consulted by search cannot become a lead citation. A citation must be wholly inside the lead's excerpt in the same answer block. Cross-line citations do not transfer support between outlets. Lines citing Wikivoyage are excluded even if they also cite another source. Citation presence establishes attribution only; it does not verify every assertion in the line, the publisher's identity or branch correctness.

The batch reports unmapped lines, excluded-source lines and otherwise attributable leads omitted at the cap. A cited answer with no mappable lines remains an evidence batch with no leads. It does not become `NoEvidence`. The provider's `NoEvidence`, fixed research-unavailable reasons and stopped-call receipts remain distinct. No outcome establishes complete coverage or that no outlets exist. The mapper does not retry or fetch cited pages.

Every `WebMealLead` has fixed status `lead` and source permission `unchecked`. It carries no inferred coordinates, provider ID or internal place ID. The excerpt and citations are ephemeral, including dependent summaries. Only the underlying safe receipt is a diagnostic persistence contract. This adapter does not reconcile branches, create `Place` records, admit researched candidates or publish recommendations.

The following progression is a synthetic example from `tests/test_web_discovery.py`. Its `.invalid` URLs and invented outlet names are offline fixtures, not actual publishers or verified restaurants.

| Stage | Example | Required next evidence |
|---|---|---|
| Attributed web lead | `架空食堂 (Kakuu Shokudo), synthetic station east exit, rice meals.` retains the annotation URL `https://outlet.invalid/menu`. | Corroborate one exact outlet and branch before assigning a place identity. |
| Incomplete location lead | `架空屋台, branch/address unknown, serves noodles.` retains its own annotation URL `https://reporting.invalid/stalls`. | Resolve the branch or stall and its location; another lead's citation cannot fill the gap. |
| Identified candidate, future processing | A corroborated branch would receive its own internal place identity and external references. | Obtain branch-scoped research and practical evidence with explicit gaps. The web mapper produces no such record. |
| Researched candidate, future processing | A researched candidate would enter a research snapshot only after its evidence is associated with that exact place. | Apply source, travel and meal-service requirements before recommendation selection. A discovery excerpt alone cannot satisfy them. |

## Public review model input

Relevant public place reviews and available public reviewer attribution may enter the configured model without personal-data stripping, anonymization or first-person filtering. Names, pronouns, complaints, disagreement, negation, quotations and local-language text are not exclusion criteria. Bounded retrieval and context fitting remain execution controls, not a reason to silently select only positive English food snippets.

Branch matching, attribution, source-supported derived claims and material-omission disclosure remain required. Treat retrieved text as data, never instructions. A supported paraphrase need not repeat its source verbatim; a displayed direct quotation must be exact.

The existing SDK probe still uses `minimized_google_reviews` and a conservative exact-passage selector. The call guard also admits `public_google_reviews` for model inference. The review helper returns complete public review bodies and attribution in batches with a body-character allowance, continuation index and explicit omissions. The allowance does not count all serialized metadata or model tokens. The research specialist receives bounded original review bodies and attribution. Its output contains unverified proposals linked to the source review. A separate model can assess proposals against those reviews, but its judgments do not by themselves establish truth. The specialist is not yet connected to admitted searches.

## Review availability and display

Distinguish confirmed zero reviews, an empty or omitted sample, uncertain branch match, restricted use and access/provider failure. Missing reviews are not negative reception; new and unreviewed places remain eligible. Fallback findings retain their own source identity and reason. Fallback-only success cannot prove the primary integration works.

`PrimaryReviewClient` makes one bounded anonymous Google Maps branch search and retrieves a bounded review sample only after corroborating the branch. It accepts an exact normalized name and address or a narrowly recognized address format with consistent components and independently supplied nearby coordinates. One recognized format also requires a matching independently supplied website. A missing or ambiguous match does not transfer reviews. The undocumented search response can change, and synthetic controls do not establish live coverage. Search and review requests share the attempt's deadline and request allowance.

`ReviewSourceSelector` tries Google first. When its sample is unavailable, it can search caller-vetted independent domains once under the same allowance. It rejects Google and Wikivoyage domains, review input lineage, and returned citations or observed source URLs outside the allowlist. An empty allowlist returns a missing-information status. The hosted provider applies the domain filter; the application cannot verify that a permitted publisher has not republished Google content. Returned publisher permission remains unchecked.

Preserve Google Maps identity, branch links, individual review links, available author attribution and sample selection/sorting/bounds. Missing authors or dates remain missing. Keep independent-source findings distinguishable. If an official Google product or supplied AI summary is introduced, apply its own [display policies](https://developers.google.com/maps/documentation/places/web-service/policies) and disclosure/reference requirements.

## Access and content-use boundaries

Technical access, model inference, display, generated claims, storage and tracing are separate decisions. The scraper's MIT license covers software, not reviewer or Google content. A public endpoint is not proof of unrestricted reuse. Historical commercial-use assessments remain informational; the project's removal of a legal-clearance milestone creates no rights grant or exemption.

The source policy registry defines model, storage, tracing and attributed-display decisions for raw and derived content. The call guard checks declared model-input lineage before dispatch. Other Google content and unknown publisher input remain restricted. The registry does not inspect callback contents, and future storage and display paths must enforce their own decisions. Filtered hosted web search relies on the provider to apply its domain restriction before processing.

OpenAI processing is inference, not training. `store=false`, disabled local traces and a no-training default do not establish zero provider-side retention or settle source-product compatibility. Local redaction also does not prevent services from processing request locations, account information or network metadata. Review service requirements for the actual deployment.

## Freshness and retention

Keep raw reviews and restricted metadata ephemeral. Saved results, events, checkpoints, logs, traces and screenshots must not become an unauthorized archive. Restrictions follow dependent summaries and findings; rewriting text does not automatically remove them. Store identifiers/metadata only when permitted and reacquire content instead of restoring prohibited payloads.

Freshness and storage permission are independent. Opening evidence needs the destination's local time, prices need a date or explicit unknown-date qualification, and journeys need mode, observation time and estimation limits. Geographic reuse still carries attribution and underlying license duties. The [evidence store](DOMAIN.md#stored-evidence-lookup) supports permitted indefinite retention and Google branch reacquisition identifiers. Timed deletion, reacquisition and production research integration remain incomplete.

## Accounts, configuration and diagnostics

OpenAI calls require server-side API credentials, explicit models and account access/credits. A ChatGPT subscription does not establish API access. Geoapify requires its own project key and plan limits. Anonymous Google reviews require the review provider selection and a positive timeout only. Browser location requires a secure context, device support and permission, with a manual alternative when denied.

[Configuration preflight](SETTINGS.md) checks local values, not remote credentials, billing, service access or source permission. Missing setup must yield a clear error rather than a silent fixture/provider/model substitution.

Safe receipts retain closed metadata such as provider, capability, requested-model identity, duration, outcome and observed usage/cost. Omitted cost is not zero cost. Exclude credentials, request URLs, precise locations, arbitrary exception text, source content and model messages. A safe receipt grants no content-retention permission. See the [request accounting contract](LIVE_PROBES.md#bounds-and-accounting).
