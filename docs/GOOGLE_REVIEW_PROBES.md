# Google review probe reference

GG-002.06A completed `app/providers/google_reviews.py` for the live feasibility gate. No review or model calls ran during that child. Later separately authorized comparisons exercised the client for Toyo Eatery and Ippei Honten, as recorded below. Full section verification remains GG-003. The [source contract](GOOGLE_REVIEWS.md) records the Google-primary requirement and current project policy.

## Access path and ownership

The client uses the anonymous Google `GetLocalBoqProxy` route demonstrated during GG-009.01B. It adapts the installed YasogaN `google-maps-review-scraper` 3.1.0 request layout and field extraction into Python and HTTPX. The [third-party notice](THIRD_PARTY_NOTICES.md) preserves the upstream MIT license. No Node runtime, browser profile, API key, Google billing account or cookie is required.

The package itself is not the production dependency. Its empty-result defaults, silent partial arrays, duplicate pagination and accumulated-record page option do not provide the required failure semantics or bounds. The Python client owns those decisions. The anonymous protocol is undocumented by Google and may change. The later two-branch comparison demonstrates bounded access through this adapter, without establishing broad reliability or completing GG-003.

## Temporary signed-in Pro comparison

After GG-002.06A, the user separately authorized a temporary comparison with `georgekhananaev/google-reviews-scraper-pro` v1.3.1 at revision `3805a1e75e66fab287ad61e9a43b790a8ab8ce9f`. The experiment ran the pinned Pro scraper in an isolated runtime with a dedicated signed-in throwaway profile. After reviewing the results, the user chose YasogaN alone and rejected backups for now. The executable Pro sources, browser runners, setup scripts, temporary runtime, dedicated throwaway profile and cache have been removed. Saved review outputs and reports remain. Pro and `google-reviews-pp-cli` are not application dependencies or production backups. The [removal receipt](../data/verification/GG-002.06A1/pro-removal.json) records the cleanup. Earlier retained-state checks describe the state before this final user instruction.

Each measured sample requests the newest 20 reviews from the same branch. Anonymous retrieval allows two pages and 30 seconds. Pro allows three scrolls and 120 seconds, verifies branch identity and sign-in through the UI, and confirms the upstream newest-sort action. The browser uses the installed drivers without setup downloads during retrieval. Cookies are excluded from the instrument's output. Actual review fields are retained in ignored local JSON and Markdown with mode `0600` because the user requested manual Maps inspection. No model calls run.

| Branch | Anonymous time | Pro time | Observed comparison |
| --- | --- | --- | --- |
| Toyo Eatery, Makati | 2.810 seconds | 42.276 seconds | Same 20 IDs, order, ratings and display texts. |
| Ippei Honten, Kitakyushu, near Kokura Station | 1.922 seconds | 44.317 seconds | Same 20 IDs, order and ratings. Nineteen display texts match. Pro adds only a trailing space and ellipsis to the remaining text. |

Display comparison converts literal HTML line breaks and entities and normalizes Unicode NFC and line endings. Raw source values remain in both exports. The Ippei sample has 18 nonempty texts per scraper. Neither sample demonstrates extra substantive review text, food subratings, or owner replies from Pro. Pro exposes photo references and heuristic language labels. Its relative date labels and estimates do not establish exact equivalence with the anonymous epoch dates. Browser network counts and byte totals remain unknown.

The [Ippei outputs](../data/verification/GG-002.06A1/attempt-02/review-output.md), [JSON export](../data/verification/GG-002.06A1/attempt-02/review-output.json), [comparison report](../data/verification/GG-002.06A1/attempt-02/comparison-report.json), and [checkpoint](../data/verification/GG-002.06A1/attempt-02/checkpoint.md) preserve the second test. [Toyo outputs](../data/verification/GG-002.06A1/attempt-01/review-output.md) remain unchanged. The research directory label `GG-002.06A1` does not admit a new executable plan task. GG-002.07 remains unstarted.

These two bounded samples do not establish global equivalence or fallback reliability during an outage. They provide no demonstrated substantive advantage that justifies integrating signed-in Pro as a backup. Production fallback and the remaining GG-003 verification stay deferred under the plan.

## Independent configuration

`GG_REVIEW_PROVIDER=google_reviews` selects the anonymous review source. `GG_REVIEW_TIMEOUT_SECONDS` is an explicit finite positive timeout per page request. Both settings are independent of location, discovery, route, web and model selections. Neither has a default selection or value.

`load_review_settings(mode=..., env_file=...)` loads only review configuration from the normal backend dotenv path and process environment. It needs no geography selection, model ID or credential. Environment values override the file. Unknown dotenv names and malformed files still fail with fixed configuration errors. Review-client preflight requires the review selection and timeout before transmission. The existing `load_settings` also accepts these optional fields for shared geography, review and model attempts.

The current local `.env` was not changed in this task. Historical anonymous access does not imply that the new review selection is configured. Add the two variables when preparing the separately requested GG-003 gate. The example file documents the inventory without selecting a source.

Diagnostic mode cannot transmit. Test mode requires an explicitly supplied HTTPX mock transport, and live mode forbids that test transport. There is no fixture or provider fallback. Review-only attempts cannot perform model or geography calls. The [settings reference](SETTINGS.md) records the full configuration boundary.

## Branch matching

`BranchQuery` names the intended branch. `BranchCandidate` supplies a Google Maps URL or feature ID and available Google name, address, website, aggregate rating and reported review total. `match_branch(query, candidates)` returns `MatchedBranch` or `NoConfidentMatch` before retrieval.

A confident match requires the same normalized name and exact full address. The alternative requires the same name and website with an explicitly targeted feature ID. Conflicting URL/feature identities or multiple eligible identities cannot become a confident branch. Matching does not treat a shared brand name or website alone as a branch match.

Candidates and corroboration come from the caller. This client does not discover branches by name or independently fetch branch details. The endpoint requires a specific feature ID. Source URLs remain tied to that identity, and ambiguous candidates require further research in later integration tasks. Candidate aggregates retain their supplied-candidate basis. They are not freshly measured by the review endpoint.

## Request bounds and accounting

`GoogleReviewClient` uses one `AttemptCalls` instance. `fetch` accepts a matched branch, `ReviewLimits` and requested sorting. The client sends GET requests only to the fixed Google endpoint. Each page is a separate admitted `reviews` call with provider `google_reviews`. The shared allowance and total monotonic deadline also bound pagination. No review request can become an unmetered nested call.

The local limits have these defaults and hard validation ceilings. The shared `Bounds` request allowance and deadline can stop the operation sooner.

| `ReviewLimits` field | Default | Accepted bound |
|---|---|---|
| `max_pages` | 2 | 1 through 5 page GETs |
| `max_reviews` | 20 | 1 through 50 selected reviews |
| `max_response_bytes` | 1 MiB | 1 byte through 1 MiB of accepted identity-encoded body per response |
| `max_total_bytes` | 2 MiB | 1 byte through 5 MiB across the fetch |
| `total_seconds` | 30 seconds | Finite number greater than zero and at most 60 seconds |

Sorting accepts `relevant`, `newest`, `highest` and `lowest`. These are requested source orderings, not a guarantee of independently verified order. Rating-selected samples require particular caution when interpreting sentiment.

The initial protocol page requests ten records. Continuation pages can return more than ten, so retained sample size and fetched row counts are distinct. Page count, decoded response bytes, record selection and total elapsed time have explicit ceilings. Duplicate IDs are counted without entering the returned sample twice. Repeated cursors stop pagination with an explicit limitation.

Retries, redirects and environment proxies are disabled. Requests use fresh clients without inherited cookies. Requests send `Accept-Encoding: identity`. Compressed responses are rejected as `unsupported_encoding` before body decoding. Accepted uncompressed responses are streamed through a byte cap, so decompression cannot allocate an oversized body ahead of the guard. HTTP content logging is suppressed. HTTP failures, unsupported encoding, malformed layouts and deadline failures do not become valid empty results. A failed later page preserves earlier usable evidence only with a partial status and its termination reason.

The [call reference](CALLS.md) defines the closed metadata receipts. They omit request URLs, feature IDs, cursors, author information, text, keys and arbitrary exception messages. A receipt records an admitted call even if it fails. Unknown cost stays unknown. Returned values are ephemeral, and are excluded from object representations.

## Evidence and availability

A sample preserves Google Maps identity, branch source links, requested sorting and limits, per-page receipt timestamps, available rating, reported total and individual review attribution. It discloses that the returned selection is a sample. It does not claim the entire corpus or representative sentiment.

Review records preserve available author name and profile/source links for attribution. Avatars, owner replies, review photos and separately extracted contributor identifiers are excluded. Review IDs are used ephemerally for deduplication. Missing attribution is explicit and never replaced with an invented author. Decimal-string publication dates are converted only under supported 10, 13, 16 or 19 digit epoch shapes, with the inferred unit basis retained. Implausible or unsupported dates remain uninterpreted. Source text may be translated or truncated by Google, and the anonymous endpoint does not prove original-language completeness.

These result states are separate:

| State | Meaning |
|---|---|
| `sample` | Usable attributable records were returned within the declared selection. The result remains a sample. |
| `partial` | Some usable records exist, but retrieval or structural limitations prevented the requested result. |
| `zero_reviews` | The matched branch explicitly reports zero and returned evidence does not contradict it. This is based on supplied candidate metadata. |
| `empty_sample` | An explicit empty sample does not establish a zero total. |
| `omitted_sample` | The recognized response omits the sample field. No zero-total conclusion follows. |
| `unavailable` | Access, parsing or execution failure prevents usable retrieval. The termination reason preserves the cause. |

No confident branch match is a separate matching result. Malformed responses, duplicate rows, repeated cursors and partial retrieval have explicit reasons or counts. Absence of reviews is not negative reception. The client does not substitute independent web evidence or implement production fallback. GG-009.01C owns that behavior.

## Minimized model input and retention

`minimize_for_model(sample)` produces `MinimizedGoogleReviewSample` from conservative food passages. The selector examines at most ten reviews and admits at most three passages totaling sixty words. Each passage has 5 through 20 words. The payload carries `minimized_google_reviews` lineage and the sample's ordering and limitations. It excludes author names, profile links, source URLs, feature IDs, review IDs and raw response structures. A conservative English food vocabulary rejects personal narrative and unfamiliar wording rather than claiming general anonymization. Non-English or more detailed food descriptions can yield no selected passage. This limits the prepared minimizer, not a place's eligibility. An empty minimized selection is an honest no-food-evidence outcome.

The call layer admits that declared lineage to OpenAI model inference under the current project decision. Ordinary Google, Grounding Lite and unknown publisher inputs remain blocked. Mixed input still fails when any source is restricted. The lineage declaration is a trusted adapter contract, as with existing geographic inputs. It does not inspect arbitrary model prompts.

Only `Receipt.safe_record()` is a persistence or diagnostic interface. Do not dump samples, minimize results, response bodies, model prompts or derived recommendations into logs, traces, screenshots, SQLite, browser storage or verification artifacts. Reacquire source content when needed. The prepared client stores no raw or derived content. SDK tracing and the actual model flow remain GG-002.07; admission itself enables neither. Provider-side processing is separate from local retention controls.

## Prepared GG-003 scenarios

`apps/backend/tests/test_google_reviews.py` and the added call-layer cases prepare offline verification. GG-003 must exercise these alongside the existing Section A cases:

- Review-only dotenv/environment precedence, invalid configuration and clear missing-selection preflight without geography or keys.
- Confident and ambiguous branches, conflicting feature IDs and unsupported source URLs.
- Attribution-bearing records, missing optional fields, decimal-string timestamps, sample sorting and candidate aggregate provenance.
- Confirmed zero, explicit empty and omitted samples, malformed envelopes and invalid records.
- Pagination, excess fetched rows, duplicate records, repeated cursors and partial retrieval after failure.
- Request/page/byte/deadline limits, compressed-response rejection, HTTP failures, timeouts, no cookies/retries/redirects/proxies, and content-free diagnostics.
- Conservative model minimization, blocked restricted or mixed lineage, and admitted minimized Google evidence.

The gate must also retrieve a real branch-matched sample using the actual local settings, inspect available attribution and links without retaining review content, and pass minimized evidence through the prepared configured model flow. Historical Sol pilot evidence remains historical. Luna is the current development default and is not silently upgraded. Fallback-only operation cannot satisfy the primary Google demonstration.

This task runs source review and narrow static checks only. It does not establish live transport access, branch coverage across countries, date correctness across all response variants, semantic support, representative sentiment, current menu information or recommendation quality. SDK orchestration, the executable probe command, production source enforcement and the normal application journey remain their scheduled tasks.
