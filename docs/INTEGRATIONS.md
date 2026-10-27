# Integrations and source policy

This reference records selected providers, source-use requirements and current limits. The clients exist as bounded development probes; production research and recommendation publication are not connected yet. Use [settings](SETTINGS.md) for exact configuration and [the probe guide](LIVE_PROBES.md) for opt-in experiments.

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

## Public review model input

Relevant public place reviews and available public reviewer attribution may enter the configured model without personal-data stripping, anonymization or first-person filtering. Names, pronouns, complaints, disagreement, negation, quotations and local-language text are not exclusion criteria. Bounded retrieval and context fitting remain execution controls, not a reason to silently select only positive English food snippets.

Branch matching, attribution, source-supported derived claims and material-omission disclosure remain required. Treat retrieved text as data, never instructions. A supported paraphrase need not repeat its source verbatim; a displayed direct quotation must be exact.

The current probe still uses `minimized_google_reviews` and a conservative exact-passage selector. Production admission must replace that limitation rather than copying it. Regression checks must demonstrate relevant Toyo Eatery and Ippei Honten reviews entering the actual research model and supporting findings, including complaints, contrast, quotation, personal-reference and Japanese-text controls. A different successful restaurant or successful retrieval alone is insufficient.

## Review availability and display

Distinguish confirmed zero reviews, an empty or omitted sample, uncertain branch match, restricted use and access/provider failure. Missing reviews are not negative reception; new and unreviewed places remain eligible. Fallback findings retain their own source identity and reason. Fallback-only success cannot prove the primary integration works.

Preserve Google Maps identity, branch links, individual review links, available author attribution and sample selection/sorting/bounds. Missing authors or dates remain missing. Keep independent-source findings distinguishable. If an official Google product or supplied AI summary is introduced, apply its own [display policies](https://developers.google.com/maps/documentation/places/web-service/policies) and disclosure/reference requirements.

## Access and content-use boundaries

Technical access, model inference, display, generated claims, storage and tracing are separate decisions. The scraper's MIT license covers software, not reviewer or Google content. A public endpoint is not proof of unrestricted reuse. Historical commercial-use assessments remain informational; the project's removal of a legal-clearance milestone creates no rights grant or exemption.

The existing call guard still distinguishes diagnostic retrieval from admitted model input. General Google/publisher model-input paths remain restricted unless their route is explicitly admitted. Hosted web search is an admitted capability, not blanket permission to fetch or re-ingest every linked page. Documentation of the broader production review requirement does not change these runtime guards.

OpenAI processing is inference, not training. `store=false`, disabled local traces and a no-training default do not establish zero provider-side retention or settle source-product compatibility. Local redaction also does not prevent services from processing request locations, account information or network metadata. Review service requirements for the actual deployment.

## Freshness and retention

Keep raw reviews and restricted metadata ephemeral. Saved results, events, checkpoints, logs, traces and screenshots must not become an unauthorized archive. Restrictions follow dependent summaries and findings; rewriting text does not automatically remove them. Store identifiers/metadata only when permitted and reacquire content instead of restoring prohibited payloads.

Freshness and storage permission are independent. Opening evidence needs the destination's local time, prices need a date or explicit unknown-date qualification, and journeys need mode, observation time and estimation limits. Geographic reuse still carries attribution and underlying license duties. Production source-aware storage and reacquisition are incomplete.

## Accounts, configuration and diagnostics

OpenAI calls require server-side API credentials, explicit models and account access/credits. A ChatGPT subscription does not establish API access. Geoapify requires its own project key and plan limits. Anonymous Google reviews require the review provider selection and a positive timeout only. Browser location requires a secure context, device support and permission, with a manual alternative when denied.

[Configuration preflight](SETTINGS.md) checks local values, not remote credentials, billing, service access or source permission. Missing setup must yield a clear error rather than a silent fixture/provider/model substitution.

Safe receipts retain closed metadata such as provider, capability, requested-model identity, duration, outcome and observed usage/cost. Omitted cost is not zero cost. Exclude credentials, request URLs, precise locations, arbitrary exception text, source content and model messages. A safe receipt grants no content-retention permission. See the [request accounting contract](LIVE_PROBES.md#bounds-and-accounting).
