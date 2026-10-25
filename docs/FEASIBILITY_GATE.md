# Section A feasibility gate

GG-003 **passed on 2026-10-25** after the admitted GG-002.07A prerequisite repair. Section A is verified at this gate. The first attempt was blocked on useful SDK food advice; those failed observations remain below. No Section B task started.

## Subsequent production requirement

On 2026-10-25 the user removed personal-information minimization for public-review model input and required replacing this probe's restrictive review filter. The records below remain historical feasibility evidence, including the failed Toyo and Ippei selections. They do not make the filter acceptable for production. GG-009.01/GG-009.01A, GG-009.04 and GG-010.03 own the replacement. GG-011 must retest both restaurants through the actual production research agent, without substituting the successful additional branch. [The revised public-review contract](GOOGLE_REVIEWS.md#public-review-model-input-decision-2026-10-25) records the change. Runtime code is unchanged by this plan revision.

## Repair and renewed acceptance

GG-002.07A is complete. Diagnosis found concrete food mentions in twenty Toyo reviews but no eligible concrete whole sentence. The repair retains exact delimiter-bounded clauses, explicit permitted concrete food targets and original source offsets. It inspects at most twenty fetched reviews while keeping model input at three passages and sixty words. Generic targets fail output validation. Negative, ambiguous and quoted context is conservatively excluded. Review-wide quote ranges prevent a quoted middle sentence from losing its attribution during sentence splitting. No privacy-vocabulary expansion or model change shipped.

The repaired Toyo relevance, newest and highest samples all returned `no_food_passages`, with no model requests. A short-span diagnostic and an English noodle-vocabulary trial also supplied no safe alternative. Neither trial changed the application. The source-context census did not justify relaxing the relevance candidate's negative/contrast and punctuation guards. These unavailable outcomes remain valid limitations, not successful suggestions.

One additional technical control was declared before viewing its reviews: Raising Cane's Chicken Fingers at 1501 Broadway, New York. Its [official branch page](https://locations.raisingcanes.com/ny/new-york/1501-broadway) identifies the address and links [Google CID 6570192765218223320](https://www.google.com/maps?cid=6570192765218223320). A bounded Google response confirmed feature identifier `0x0:0x5b2e038274ac74d8`. The supplied name/address came from the official location page; Google did not independently refresh or confirm those fields. This is official-page corroboration plus Google identifier confirmation.

The unchanged selector admitted one seven-word passage from twenty relevance-selected reviews over two pages. Actual `gpt-6-luna` owner, nested research and owner requests completed with a concrete food target, exact permitted-target membership, exact quote, exact original source slice and source attribution link. Available author names and links remained outside model input. The validated owner preserved the research contribution. This satisfies narrow useful-food feasibility without claiming comparative recommendation quality.

The three model requests reported 821 input and 106 output tokens. Their request durations totalled 5.899 seconds, or 8.287 seconds including both review requests. These are sums of request durations, not independently measured process elapsed time. Monetary cost was not returned and actual charges remain unknown. No automatic model retry or Sol upgrade ran. The observer's older `positive_food_language` shortlist returned false because it omits descriptors accepted by the selector's attached-positive-target rule. Exact target support passed under the declared selector contract; the old shortlist is not a general sentiment test.

Final offline verification passed **427 tests**, Ruff, formatting, normal mypy across eighteen files and shell syntax. The source review found no remaining scope blocker. Comment review found no open flags. Failing tests, quotation regressions, all unavailable live outcomes, final checks, decisions and source hashes are retained under `data/verification/GG-002.07A/attempt-01/` and `data/verification/GG-003/attempt-02/`. Unchanged successful geography, web research and failure-control evidence from attempt-01 was reused. The two completion checkboxes are GG-002.07A and GG-003 only.

Production multilingual research, broader food vocabulary, source fallback and comparative quality remain assigned to their existing later tasks. Finite context rules are not a general semantic-safety proof. The conservative English selector still rejects useful-looking real material; that coverage limit is explicit and does not disqualify a place. The app, API, database, worker and browser journey remain unimplemented.

## Observed capabilities

These are live observations, separate from the offline controls below. Evidence is retained locally under `data/verification/GG-003/attempt-01/`. Earlier failures remain alongside successful reruns.

| Scenario | Observation | Evidence |
|---|---|---|
| Kokura Station, Kitakyushu, Japan | One location lead, ten named catering leads. Walking estimate 277.973 seconds over 310 metres; driving estimate 146.135 seconds over 1,066 metres. | `japan-live-fixed.jsonl` |
| Greenbelt, Makati, Philippines | One location lead, ten named catering leads. Walking estimate 495.209 seconds over 475 metres; driving estimate 170.353 seconds over 1,123 metres. | `philippines-live.jsonl` |
| Ippei Honten anonymous Google reviews | Twenty reviews across two pages, eighteen nonempty texts, available author names and links, Google source links, no duplicate or invalid rows. No passage passed the conservative English minimizer. SDK returned `no_food_passages` without model calls. | `sdk-live.jsonl` |
| Ippei Honten Luna web research | One completed hosted search, one citation, branch and food terms present. No Wikivoyage citation. 12,959 input and 273 output tokens, 6.981 seconds. This is cited retrieval evidence, not independent factual-quality acceptance. | `web-live.jsonl` |
| Toyo Eatery anonymous reviews and Luna SDK | Twenty reviews across two pages. One seven-word passage admitted. Actual owner, nested research agent and owner requests completed. Exact quote and food-phrase support passed. Combined SDK usage was 807 input and 78 output tokens. | `toyo-sdk-live.jsonl` |
| Toyo semantic inspection rerun | One page, ten reviews, the same one-passage allowance. Actual nested SDK completed, with positive language and no detected negative language, but the suggested phrase contained no concrete food noun or tasting-menu term from the probe vocabulary. | `toyo-sdk-semantic-live.jsonl` |

The geographic estimates have no live traffic. Their provider and source credits remain attached. These finite samples do not establish worldwide coverage, opening status, eat-now suitability or complete branch discovery.

The Toyo SDK sequence took 4.828 seconds of model request time, or 6.241 seconds including review requests. The narrower semantic rerun used the same 807 input and 78 output tokens and took 3.885 seconds of model request time. API receipts reported no monetary amounts. Actual charges and an authoritative cost estimate remain unknown; no price or zero-cost claim is inferred from missing cost fields. No model upgrade was attempted because the observed limitation is evidence selection and semantic usefulness, not demonstrated Luna inability.

Across the attempt, seven model API requests reported 14,573 input and 429 output tokens. The network-enabled CLI runs produced twenty-three receipts, including the first route failure. Two additional bounded route-diagnosis requests and three credential-free connectivity checks are recorded separately. The sandbox DNS failure is a separate failed admission. These counts include the failed observations and semantic rerun rather than only successful scenarios.

## First-attempt blocker and admitted repair

The CLI reported `passed` for the structurally valid Toyo SDK interaction. GG-003 remained unchecked at the first attempt because that status did not prove the gate's useful source-backed food suggestion. A fixed-vocabulary semantic check found only generic food language. This check is narrower than a human quality review and does not establish that the recommendation is useful. The Japanese sample also supplied no admissible passage. No output was fabricated or substituted.

The user admitted **GG-002.07A, preserve useful food evidence in the bounded SDK probe**, on 2026-10-25. It depends on GG-002.08 and precedes GG-003. Its original scope was:

- Determine whether concrete food evidence is absent from the fetched sample or lost during minimization, using ephemeral review material and content-free diagnostics.
- Select a bounded evidence shape that preserves useful food support and source attribution while retaining privacy, source-admission and tracing controls.
- Distinguish a generic food observation from an actionable suggestion. Do not reinterpret missing evidence as negative reception.
- Add behavioral controls and document any necessary source-policy changes explicitly. Reuse the successful geography and access receipts when unchanged.

The completed repair changed evidence selection and agent output validation within that scope. The queue now contains 97 tasks, with existing IDs preserved. Later production research still owns multilingual evidence, broader coverage, adaptation and comparative recommendation quality.

## Configuration and source decisions

The actual loader resolved `geoapify` for location, discovery and routes, `openai` for web research, and `gpt-6-luna` for both owner and research. Geographic, model and review timeouts are 30 seconds. The OpenAI and Geoapify credentials are present. No Google API key or billing is used. Gate preflight found missing review fields; the ignored backend `.env` now explicitly selects `GG_REVIEW_PROVIDER=google_reviews` and `GG_REVIEW_TIMEOUT_SECONDS=30`. Credential values and model selections were unchanged.

Geoapify is the selected geographic path supported by the live samples. Bounded anonymous Google retrieval remains the required primary review source, independent of geography. OpenAI Responses web search remains supplementary research. These paths are verified for Section A feasibility, with the finite coverage and quality limits above.

The [source-use findings](INTEGRATIONS.md), [Google contract](GOOGLE_REVIEWS.md), and [historical access assessment](REVIEW_FEASIBILITY.md) remain applicable. The user's removal of legal clearance as a completion gate does not establish legal clearance. Attribution, explicit retention and technical requirements remain in force. Personal-information minimization for public-review model input was subsequently removed by the revision above. Ordinary Google, Grounding Lite and unknown-publisher model inputs retain their existing guards. Only the declared minimized anonymous review lineage entered the SDK.

Review branch identity reuses prior corroboration for [Ippei Honten](https://www.google.com/maps?cid=13076016945527970146) and [Toyo Eatery](https://www.google.com/maps?cid=462274759707113327). Ippei matched the supplied exact name and full address. Toyo matched the supplied exact name, official website and targeted feature ID. Review retrieval does not refresh branch metadata. Source names, links and available author attribution were checked in memory, outside model input. Sampling was bounded to at most twenty reviews and two pages, not a complete or representative history.

Review text, authors, quotations, minimized passages and generated content were not newly retained. The sample-output evidence records its schema, exact-support checks and source identifiers, with content omitted under the existing retention rule. Web response content also remained ephemeral. Raw web evidence was not fed into the SDK. Responses used `store=false`; SDK tracing and sensitive trace capture were disabled. These local settings do not eliminate remote provider processing or retention.

Zero-review, empty/omitted samples, no-match, access/quota failures, timeout and restricted-use distinctions were exercised through separately labeled offline controls. They are not receipts of naturally occurring live zero-review or access-failure branches. Production source fallback remains GG-009.01C. No invented live branch was used to manufacture a negative result.

## Repairs and repeatable checks

The first broad run had 326 passing cases and one failing SDK assertion. The installed SDK omits `parallel_tool_calls` for a tool-free research request. The corrected test requires disabled parallel calls on tool-bearing requests and an empty research tool list. No application control was weakened.

The first network-enabled Japan run resolved and discovered places but rejected the route response. The live metric token was `meters`; the adapter and its fixtures incorrectly required `Meters`. The adapter now accepts the strict lowercase metric token and rejects imperial and incorrect-case units. See the [Geoapify routing contract](https://apidocs.geoapify.com/docs/routing/) and [route reference](ROUTE_PROBES.md). The initial sandbox attempt also failed DNS resolution. Neither failure was reported as provider success.

Run the offline section checks from any working directory:

```sh
/absolute/path/to/GobbleGobble/scripts/local/verify-section-a.sh
```

The [setup guide](BACKEND_SETUP.md) records prerequisites and exit behavior. The script performs no live requests. Tests use explicit controlled transports. They cover the actual CLI entry point, input/configuration rejection and redaction, success/failure exits, receipts, request/deadline bounds and interruption. SDK controls cover nested invocation, altered output, invalid tool use, tracing, cancellation and deadline behavior. Final command results and file identities belong to the local gate evidence directory.

To inspect fresh real behavior later, use [the explicit live probe command](LIVE_PROBES.md) with independently corroborated inputs and bounded settings. `observe_cli.py` in this attempt wraps the actual CLI and clients to record content-free observations. Its food-vocabulary and sentiment checks are diagnostics, not a general semantic evaluator. Do not use its CLI exit status as the gate verdict.

No application API, database, worker, frontend, browser journey or ChatGPT quality comparison was delivered or verified here. Those remain assigned to later tasks. GG-003 must pass before Section B begins.
