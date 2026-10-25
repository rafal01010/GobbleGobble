# Evidence, food and review contract

GG-004.02 defines vendor-independent evidence schemas in [evidence.py](../apps/backend/app/schemas/evidence.py). The implementation record and verification status are in [the plan](../IMPLEMENTATION_PLAN.md#gg-00402-define-evidence-food-and-review-types). The [request and place contract](SEARCH_CONTRACT.md) supplies the branch identities these findings describe.

## Sources, evidence and subject places

A source is an attributable document, listing or review source. An evidence record is a finding obtained from that source. A food claim or review finding cites evidence, not the source identifier directly. Source IDs use the `src_` namespace. Evidence IDs use `ev_`. Source URLs and provider identifiers remain separate from both internal identifiers.

Sources have explicit place scopes. Evidence declares its own subject places within that source scope. A shared restaurant-group page can cover two branches while a particular menu finding applies to only one branch. Sharing a source does not transfer every finding to all places mentioned on the page.

Each asserted finding binds its evidence separately for each subject place. Every evidence reference in a binding must cover that exact subject. A statement about two places must declare support for both places. It cannot cite evidence for branch A and rely on branch B merely being present in the same collection.

The collection boundary checks reference uniqueness, existence and category, source/evidence scope and finding support. Review findings also remain attached to their assessed branch, source and declared review sample. These checks validate declared relationships. They do not determine whether the source prose actually supports a dish, price, complaint or paraphrase. Extraction, source admission and publication checks remain later tasks.

## Food claims and gaps

Sourced claims require evidence support. Inferred food claims require support and an explicit nonblank explanation of the inference. Unknown claims record a gap rather than an asserted food fact. Optional menu and price findings retain the same subject relationships as other food findings.

The contract can distinguish an inferred food suggestion from a source-supported observation. It does not turn a review mentioning a dish into proof that the dish is currently on the menu. Exact food availability and price support require the later interpretation checks.

## Review identity and availability

The Google primary source retains branch identifiers independently of review identifiers. Review locators preserve source links and distinguish a review link from a branch-level link. Available publisher and public author attribution remain referenced data, including names and supplied links. Missing author information remains missing.

Review availability distinguishes accessible matched evidence, confirmed zero reviews, no confident branch match, restricted use and provider unavailability. An accessible source can return a populated, empty or omitted sample. Empty and omitted samples do not establish a total of zero.

Reported total reviews, returned sample size, selected or assessed count and selection method remain separate. Sorting, bounds, partial retrieval and omissions qualify the sample. A selected negative sample does not establish the reception of the entire review history. Complaints remain attributable findings rather than a rule that excludes the place.

Independent fallback keeps its own source identity and an explicit reason tied to the Google primary outcome. Alternative food evidence remains usable without being labeled Google review evidence. These records represent outcomes and lineage; they do not attempt retrieval or implement fallback selection.

The [public-review model-input decision](GOOGLE_REVIEWS.md#public-review-model-input-decision-2026-10-25) permits public text and available attribution without personal-information stripping. Names, pronouns, first-person experiences, local languages, adverse findings, negation and quotations are not schema rejection criteria. Retrieved text remains data. The existing feasibility minimizer is unchanged by this task; the production replacement remains GG-009.01 and GG-009.01A.

## Use decisions, freshness and retention

Each source records explicit source-specific decisions for access, display, model input, derived output and tracing. Unknown permission remains unknown. These are backend-owned declarations, not permission grants from model output. Runtime enforcement remains GG-009.01.

Raw content, derived content and identifiers have separate retention decisions. Ephemeral treatment does not authorize durable storage. A permitted retention deadline does not establish that the underlying fact is still current. Factual freshness records retrieval, observation and known freshness limits independently of retention.

The schemas describe these distinctions without evaluating the current clock, scheduling deletion or invalidating dependent claims. Storage and source lookup remain GG-009.02. Freshness and restricted-descendant handling remain GG-009.05. Serialization is not a safe logging or persistence projection. Content and structured errors remain subject to the source and location controls even when their formatted error messages omit input values.

## Verification boundary

Literal synthetic examples under `docs/examples/evidence-contract/` prepare the required same-branch, swapped-branch, shared-source and explicitly multi-place cases for GG-007. They are not real provider receipts or permission findings.

GG-007 owns broad contract verification and the Section B gate. GG-004.03 owns recommendation and refinement versions. GG-004.05 owns authoritative API contract exposure. This child implements no research snapshot, result selection, source retrieval, persistence or browser behavior.

## Schema entry points

`EvidenceCollection` is the relationship-validation boundary. It contains known `place_ids`, sources, attributions, evidence, food claims and review assessments. A standalone `Evidence` or `FoodClaim` validates its own shape; reference integrity requires parsing the containing collection. Later callers must supply the actual known place IDs for the search. This child does not look them up in a database.

| Schema | Relevant fields |
|---|---|
| `Source` | `source_id`, `subject_place_ids`, `publisher`, `source_url`, `origin`, `attribution_ids`, `policy`, `retention`. |
| `Evidence` | `evidence_id`, `source_id`, `subject_place_ids`, `statement`, `locator`, `attribution_ids`, `freshness`. |
| `SubjectSupport` | One `place_id` and nonempty `evidence_ids`. |
| `FoodClaim` | Discriminated sourced/inferred/unknown models. Asserted claims use per-place `subjects`; unknown claims use `subject_place_ids` and `gap`. |
| `ReviewAssessment` | `place_id`, `google`, Google `findings` and optional independent `fallback`. |
| `ReviewSample` | `state`, `sample_size`, `evidence_ids`, `assessed_count`, `assessed_evidence_ids`, `reported_total`, count basis, selection/order, bounds, omissions and partial qualification. |

Google `findings` must cite assessed evidence within the primary sample. Independent review fallback has its own sample. Independent food fallback has its own evidence list. Their findings cannot cite another source or a different branch. A fallback reason must agree with the Google primary state, including empty, omitted or partial samples.

`Freshness.fresh_until` can precede retrieval. The [stale-but-retainable example](examples/evidence-contract/accepted_stale_but_retainable.json) records an old menu whose declared freshness has ended, while its source separately declares a later raw-content retention deadline. Neither field grants permission to present that menu as current.

## Literal relationship controls

All examples use two valid internal branch IDs, `branch_a` and `branch_b`, and illustrative source material. The generator creates full JSON payloads so each case can be inspected and parsed independently.

| Relationship | Accepted control | Rejected control |
|---|---|---|
| Branch-specific menu and price on one shared source | [Scoped menu, price, inference and gap](examples/evidence-contract/accepted_shared_source_scoped_menu_and_price.json). | Swapped [menu](examples/evidence-contract/rejected_swapped_branch_menu.json) or [price](examples/evidence-contract/rejected_swapped_branch_price.json) evidence. |
| Branch-specific complaint | [Attributed Google complaint](examples/evidence-contract/accepted_google_present.json). | [Swapped branch complaint](examples/evidence-contract/rejected_swapped_branch_complaint.json). |
| Explicit multi-place finding | [Per-place support for both branches](examples/evidence-contract/accepted_explicit_multiplace.json). | [Union of different branches' support](examples/evidence-contract/rejected_multisubject_union_of_support.json). |
| Evidence identifier category and existence | The scoped menu control above uses registered `ev_` references. | A [source ID as evidence](examples/evidence-contract/rejected_source_id_as_evidence_id.json) or [nonexistent evidence](examples/evidence-contract/rejected_nonexistent_evidence.json). |
| Review sample membership and branch identity | [Partial sample with a usable complaint](examples/evidence-contract/accepted_partial_with_usable_complaint.json). | Complaint [outside the assessed sample](examples/evidence-contract/rejected_complaint_outside_declared_sample.json) or a [different Google branch locator](examples/evidence-contract/rejected_google_locator_branch_mismatch.json). |
| Independent fallback identity and reason | Labeled independent [food](examples/evidence-contract/accepted_labeled_independent_food_fallback.json) and [review](examples/evidence-contract/accepted_labeled_independent_review_fallback.json) fallback. | [Incorrect fallback reason](examples/evidence-contract/rejected_fallback_reason_mismatch.json) or [Google identity on independent evidence](examples/evidence-contract/rejected_independent_google_identity.json). |

The remaining controls distinguish [empty](examples/evidence-contract/accepted_google_empty.json), [omitted](examples/evidence-contract/accepted_google_omitted.json), [confirmed zero](examples/evidence-contract/accepted_google_zero_review.json), [no match](examples/evidence-contract/accepted_google_no_match.json), [restricted](examples/evidence-contract/accepted_google_restricted.json) and [provider unavailable](examples/evidence-contract/accepted_google_provider_unavailable.json). An [empty sample labeled as confirmed zero](examples/evidence-contract/rejected_empty_sample_as_confirmed_zero.json) and a [boolean zero-review count](examples/evidence-contract/rejected_zero_review_boolean_count.json) are invalid.

## Local evidence and rerun commands

The task-local sanity check passed 13 accepted payloads with roundtrips and 12 expected rejections. It also checked retained branch bindings, food inference/gap states, public author attribution, multilingual negative text with quotation and negation, sample/total distinctions, independent fallback identity and stale retention. Five JSON schemas were exported under ignored `data/verification/GG-004.02/attempt-01/`:

- `EvidenceCollection.schema.json`.
- `Evidence.schema.json`.
- `FoodClaim.schema.json`.
- `ReviewAssessment.schema.json`.
- `Source.schema.json`.

With the installed backend environment, run the checker from `apps/backend`:

```sh
.venv/bin/python ../../data/verification/GG-004.02/attempt-01/check_examples.py
```

It reads the literal payloads without loading `.env` or contacting services. It exports the schemas and source/payload SHA-256 identities in the same attempt directory. It exits with code 0 when all assertions pass and nonzero on a validation, assertion or export failure. `checks.txt` retains the successful output; `work.md` records decisions, initial findings and actual commands. These checks do not pass GG-007.

To regenerate the literal payloads from their synthetic definitions, run this from `apps/backend`:

```sh
.venv/bin/python ../../docs/examples/evidence-contract/generate_examples.py
```

The generator uses only the Python standard library, accepts no arguments and writes the 25 named JSON files beside itself. It imports no application code and does not decide whether a payload is accepted. It exits with code 0 after writing all files or nonzero if a write fails. The separate checker verifies acceptance and rejection.
