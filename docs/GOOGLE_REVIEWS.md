# Google review source contract

Google reviews are a primary, integral source in GobbleGobble. The selected path uses bounded anonymous retrieval demonstrated through YasogaN. Google billing is excluded. Places with no reviews remain eligible, and absent or unavailable Google evidence triggers labeled fallback. This contract describes required behavior; the reusable client is prepared, while production integration and fallback remain unimplemented.

The [feasibility decision](REVIEW_FEASIBILITY.md) completes GG-009.01B using actual package extraction and the specifically approved Google-to-model pilot. The user removed legal clearance as a completion gate and reaffirmed full application quality. Historical commercial findings remain informational. GG-002.06A completed branch matching over supplied candidates, bounded retrieval and narrow admission for minimized anonymous review evidence. Later two-branch comparisons exercised the prepared client, and the user selected it alone without an alternate Google scraper. The [probe reference](GOOGLE_REVIEW_PROBES.md) records configuration, limits, retention and comparison evidence. Full behavioral and live verification remain at GG-003. Google stays primary, independent sources supplementary only, and Wikivoyage excluded.

## Primary source and fallback

The Google review source is independent of the geography provider. Geoapify may supply coordinates, discovery and journeys while Google supplies review information. Generic web research must not silently replace an available configured Google review path.

These outcomes remain distinct:

- A matched Google place has a reported review count of zero.
- The source returns an empty or omitted sample without establishing zero total reviews.
- No specific branch can be matched confidently.
- Source policy does not permit a requested use.
- Credentials, access, quota, timeout or provider failure prevents retrieval.

Fallback uses permitted independent review or food evidence and keeps the source and reason visible. No-review or unavailable status is not negative reception. If no alternative evidence exists, the app can still recommend a suitable place with explicit missing information. A temporary outage or missing configuration may produce an actionable fallback-only status. It does not establish that the Google integration has passed acceptance.

Google review use must be demonstrated for at least one reviewed place at the relevant gates. A Maps link, synthetic response or fallback-only success cannot satisfy that requirement. Samples must disclose their bounds and sorting without implying complete review history. The selected anonymous path has its own explicit request and page bounds. If the official Places API is later selected, its separate limit is at most five relevance-selected reviews.

## Payment and early feasibility

The user has a debit card and no credit card. Google Cloud Billing, which also bills Google Maps Platform, lists Visa/Mastercard debit cards as supported. Acceptance depends on country/product and the issuer allowing automatic recurring charges. Prepaid cards and debit cards that require in-session verification for each payment are not supported. Documentation support is not proof that the user's particular card has been accepted. [Google payment methods](https://docs.cloud.google.com/billing/docs/how-to/payment-methods), [country-dependent payment options](https://docs.cloud.google.com/billing/docs/resources/currency).

Places API requires billing enabled on the project and authenticated requests. Having a key or staying below a usage allowance does not establish billing readiness. [Places usage and billing](https://developers.google.com/maps/documentation/places/web-service/usage-and-billing).

GG-009.01B completed the access and review-to-model investigation. No Google billing account or payment change is required for the selected anonymous route. The exact reason for the earlier account/card failure remains unverified and does not block the chosen access path.

## Attribution and display

The application must preserve source identity, source links and available attribution, and explain sample selection. The following product-specific requirements apply when the named Google API product is used, not automatically to the selected anonymous endpoint.

The [Places policies](https://developers.google.com/maps/documentation/places/web-service/policies) require visible Google Maps attribution, clear separation from other content, review-author credit, access to each review's supplied Google Maps source link, and a notice explaining ordering/filtering. Available author resources include an avatar, name and profile link. Applicable regional requirements, including returned French visit dates, also need implementation.

If the app displays Google's supplied AI review summary, its separate rules require the full summary, the supplied disclosure, a review-summary heading and prescribed reference/report/reviews links. Ordinary review retrieval does not require adding that optional product. A future official Places integration would need to document its field mask and product. The selected anonymous endpoint has no Places field mask.

## Storage and reacquisition

The [Maps terms, section 3.2.3](https://cloud.google.com/maps-platform/terms) restrict copying, saving and caching Google content except for explicit exceptions. The Places policies permit indefinite storage of place IDs. The [service terms, section 14.3](https://cloud.google.com/maps-platform/terms/maps-service-terms) permit caching Places latitude/longitude for up to 30 consecutive days. That coordinate exception is not a 30-day review-storage allowance.

The initial design keeps review text and restricted metadata ephemeral. Saved results, events, checkpoints, traces, screenshots and diagnostic evidence must not become a review archive. Store only specifically permitted identifiers and metadata. Reacquire review content on demand rather than restoring prohibited payloads from SQLite or browser storage. A fresh response is not permission to retain it. A rewritten summary is not automatically exempt from its source's restrictions.

## Model use and supported workarounds

API access and display do not by themselves settle model input, generated findings, or storage permission. The ordinary Maps terms restrict creation of content from Google content. Maps Grounding Lite has separate, conditional grounding provisions and additional output/caching restrictions under [section 10 of the service terms](https://cloud.google.com/maps-platform/terms/maps-service-terms). It is not an interchangeable entitlement for Places review text.

The completed investigation compared official products, anonymous access, third-party providers and independent sources. The selected path is bounded anonymous Google retrieval, with minimized model input. API access and display rules below remain conditional reference information, not a reason to block this path under the current project requirements.

GG-009.01B explicitly includes access outside the official API. Investigate existing extraction tools, public-page scraping or browser extraction, third-party Google review providers, search-index access, and manual imports or user-supplied reviews. The user's yt-dlp transcription example describes the access-method analogy, not a Google review tool recommendation. Compare availability, payment, branch identity, coverage, sample ordering, freshness, reliability, maintenance and automation suitability. Assess the terms and evidence applicable to each route's access, model input, generated recommendations, attribution, storage and tracing. Do not assume Places API terms apply identically to every route or that non-API access is unrestricted. Technical extraction and supported downstream use require separate findings. The investigation also assesses independent-source viability with no Google services, including coverage gaps and any required product changes. These are research candidates, not approved integrations. Selected validation stays within the task's existing limits; substantial extraction or integration work requires a separate task and any primary-source change requires the reviewed plan decision.

The standard non-EEA service terms allow Places use without a map, but restrict use with a non-Google map. Places UI Kit has a specific exception with its own conditions. Using Geoapify as a backend provider is separate from displaying a non-Google map. The selected UI and billing agreement must be checked. [Service terms, sections 14 and 15](https://cloud.google.com/maps-platform/terms/maps-service-terms).

The cited service terms apply to non-EEA billing accounts. EEA billing accounts have different terms and potentially different functionality. Billing jurisdiction and exact account/product permissions remain unknown; the user's timezone does not establish them.

## Scheduled implementation and evidence

| Task | Required result |
|---|---|
| GG-009.01B | First resolve payment/access, review-to-LLM feasibility, supported workarounds and alternatives, with bounded evidence before provider-specific implementation. |
| GG-002.06A | Prepare bounded anonymous Google review retrieval and the narrow source-admission change under the corrected scope. |
| GG-003 | Prove real branch-matched samples, model use and prepared failure cases before production integration. |
| GG-009.01A | Integrate the Google review source for normalized candidate branches. |
| GG-009.01C | Implement bounded fallback and source selection from that decision. |
| GG-011 | Verify primary retrieval, branch/sample accuracy, permitted uses and controlled fallback states. |
| GG-014.05 and GG-015 | Display source-aware details and verify attribution, availability states and fresh reacquisition through the browser. |
| GG-019 and GG-025 | Verify recovery and final delivery without retaining prohibited content or hiding a missing Google integration. |

GG-009.01B and GG-002.06A are complete. The latter prepared the client without live calls; the separately authorized comparisons ran afterward. Later tasks retain their own implementation and verification conditions. Production independent-evidence fallback remains GG-009.01C. Existing legal findings remain historical under the current project requirements. API-specific constraints in this document apply if that API is used; they are not the selected anonymous route contract.

## Commercial feasibility continuation

The earlier [commercial source-flow assessment](REVIEW_FEASIBILITY.md#commercial-assessment-and-concrete-unblock-conditions) is retained as historical evidence. The user removed legal clearance as a completion gate. That correction does not establish legal clearance. Technical source quality, attribution, privacy, retention and reliability requirements remain. No new configuration is needed for the demonstrated anonymous access.

The historical independent-source experiment does not satisfy the Google-primary requirement. The proposed primary-source amendment was rejected. Other sources may be considered as supplements only.

Wikivoyage is explicitly excluded from source candidates because of outdated information. Its retained experiment is historical only. Other supplementary sources remain eligible for assessment without replacing mandatory primary Google reviews.

## Approved research-pilot result

The specifically approved Google-to-model pilot passed bounded traceability checks for one minimized Toyo Eatery sample. [The verified result](REVIEW_FEASIBILITY.md#verified-approved-pilot-and-current-disposition) records ten unique retrieved reviews, one seven-word passage, one then-configured Sol call and eight passing checks. This is research-use evidence, not permission for the full commercial flow or independent recommendation-quality verification. The source guard now admits only the separately declared minimized anonymous-review lineage. Ordinary Google and other restricted inputs remain blocked. Both local model roles now use Luna by user direction; the Sol result must not be relabeled or automatically repeated.
