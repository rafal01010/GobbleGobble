# Recommendation and refinement contract

GG-004.03 defines recommendation, snapshot and food-choice schemas in [recommendations.py](../apps/backend/app/schemas/recommendations.py). [The plan](../IMPLEMENTATION_PLAN.md#gg-00403-define-recommendation-and-refinement-versions) records completion and verification status. These schemas reuse the [search contract](SEARCH_CONTRACT.md) and [evidence contract](EVIDENCE_CONTRACT.md).

## Required relationship boundary

`RecommendationCollection` is the complete relationship-validation entry point. It contains one `search_id`, one original `SearchRequest`, research snapshots and result versions. Standalone leaf models validate their shapes and local limits. They do not establish reference integrity.

The common request declares the original origin, eat-now instant and travel constraints for the supplied chain. Versions cannot override it. The later caller must load the actual snapshots and versions belonging to that search. Detached records do not independently prove their search ownership. The collection is a structural validation artifact, not a public history API or a storage model.

All new models are strict, frozen, reject extra fields and hide input values in formatted validation errors. Snapshots and versions use distinct `snap_` and `ver_` identifier namespaces. These properties do not authorize logging or persistence of the contained location or evidence data.

## Research snapshots

`ResearchSnapshot` contains `snapshot_id`, `discovery_leads`, `researched_candidates` and an existing `EvidenceCollection`. Both place groups contain concrete `Place` records. IDs are unique and disjoint across the two groups. The evidence collection's declared place catalog must equal their combined IDs.

Only researched candidates can appear in recommendations or support offered food choices. A discovery lead remains ineligible even when its ID exists in the same snapshot. A later snapshot can move a lead into the researched pool. Research storage, contribution records, source admission and production promotion remain later work.

The evidence collection retains its existing source and exact-branch support checks. Shared sources and explicit multi-place findings remain representable. A snapshot does not assert that evidence is current, permitted for display, or semantically true.

## Recommendation versions

`RecommendationVersion` contains `version_id`, `research_snapshot_id`, `lineage`, ordered `primaries`, ordered `runners_up`, optional `shortfall_reason` and finite `food_choices`.

Primaries have at most three members. Runners-up have at most four members. Place IDs are unique within each group and disjoint between groups. Every member resolves to a researched candidate in the version's exact snapshot. Array order is display order.

Fewer than three primaries or fewer than two runners require a nonblank `shortfall_reason`. Two runners satisfy the ordinary runner target. The schema checks the declared explanation, not whether the research justifies its prose. Empty groups remain structurally representable with an explanation. GG-004.04 owns outcome distinctions and contradictory outcome/result combinations.

Each `Recommendation` has `place_id`, `why_to_go` and optional `food_claim_ids`. Claim references must exist in the exact snapshot and explicitly cover that branch. Unknown food claims can describe missing information for that branch. An omitted food-reference list becomes empty. Composition and publication remain responsible for useful and truthful food detail.

## Finite food choices

`FoodChoice` contains a local `choice_id`, its offering `version_id`, a `label` and nonempty `supports`. Every support binds one researched `place_id` to nonempty `food_claim_ids`. Support places and claim references are unique within their respective lists.

Choice support requires sourced or inferred food or menu claims for the exact candidate. Unknown claims and price claims cannot support a food choice. Inferred claims retain the evidence contract's explicit basis. A supported candidate can be outside both displayed recommendation groups. A shared claim must explicitly support every referenced branch.

Choice IDs are unique within an offering version. The same local ID can recur in another version. `OfferedFoodChoiceRef` identifies the choice by the pair `version_id` and `choice_id`. Every offered choice's version must match its containing version.

## Result lineage

The lineage discriminator `kind` has these variants:

- `initial` has no parent or selected choice.
- `food_choice` has `parent_version_id` and a composite `choice` reference.
- `all_options` has `parent_version_id` without an invented food choice.

All referenced snapshots and parents must exist in the supplied collection. Parents can appear before or after children. Self-parenting and lineage cycles are invalid. A food refinement's selected choice must name its exact parent version and a local choice that parent actually offered. Refinement can use a newer snapshot.

A choice offered by an older result cannot substitute for a newer parent's choice even when both use the local ID `noodles`. Historical versions remain valid historical data. These static checks cannot establish the currently active version. Atomic command admission, stale browser response protection, persistence and publication remain later tasks.

## Literal controls

The standard-library [generator](examples/recommendation-contract/generate_examples.py) produces 25 complete synthetic JSON payloads without importing application schemas. Seven accepted controls cover full and smaller groups, a two-runner set, broader-pool inference, exact multi-place support, explicit unknown food gaps and out-of-order lineage using a new snapshot.

| Relationship | Accepted control | Rejected controls |
| --- | --- | --- |
| Group limits and membership | [Three primaries and four runners](examples/recommendation-contract/accepted_full_set.json). | [Fourth primary](examples/recommendation-contract/rejected_four_primaries.json), [fifth runner](examples/recommendation-contract/rejected_five_runners.json), duplicate [primary](examples/recommendation-contract/rejected_duplicate_primary.json) or [runner](examples/recommendation-contract/rejected_duplicate_runner.json), and [overlap](examples/recommendation-contract/rejected_group_overlap.json). |
| Smaller sets | [One explained primary](examples/recommendation-contract/accepted_smaller_set.json) and [two runners](examples/recommendation-contract/accepted_two_runners.json). | [Missing explanation](examples/recommendation-contract/rejected_missing_shortfall.json). |
| Real researched candidates | [Nondisplayed inferred support](examples/recommendation-contract/accepted_broader_pool_inference.json). | [Unknown candidate](examples/recommendation-contract/rejected_unknown_candidate.json) and [discovery-only candidate](examples/recommendation-contract/rejected_discovery_candidate.json). |
| Exact branch food support | [Explicit multi-place support](examples/recommendation-contract/accepted_shared_multiplace.json) and [unknown gap in a recommendation](examples/recommendation-contract/accepted_unknown_gap_and_omitted_food.json). | [Wrong branch](examples/recommendation-contract/rejected_wrong_branch_food_support.json), [nonexistent claim](examples/recommendation-contract/rejected_unknown_food_claim.json), and choice support using [unknown](examples/recommendation-contract/rejected_unknown_only_food_support.json) or [price-only](examples/recommendation-contract/rejected_price_only_food_support.json) claims. |
| Version and parent identity | [New snapshot and out-of-order lineage](examples/recommendation-contract/accepted_new_snapshot_refinement.json). | [Offering-version mismatch](examples/recommendation-contract/rejected_choice_version_mismatch.json), [stale pair with repeated local choice ID](examples/recommendation-contract/rejected_stale_choice_repeated_local_id.json), missing [parent](examples/recommendation-contract/rejected_missing_parent.json) or [snapshot](examples/recommendation-contract/rejected_missing_snapshot.json), [self-parent](examples/recommendation-contract/rejected_self_parent.json), and [cycle](examples/recommendation-contract/rejected_cyclic_lineage.json). |

## Local verification artifacts

The task-local checker at `data/verification/GG-004.03/attempt-01/check_examples.py` parses the seven accepted payloads, checks JSON round trips and verifies eighteen rejections against expected error categories or messages. It also checks literal group ordering, broader-pool membership, retained inference, repeated local IDs and the newer snapshot relationship.

Its backend working-directory command is `.venv/bin/python ../../data/verification/GG-004.03/attempt-01/check_examples.py`. The generator command in the same directory is `.venv/bin/python ../../docs/examples/recommendation-contract/generate_examples.py`.

The checker exports six JSON schemas for `RecommendationCollection`, `ResearchSnapshot`, `RecommendationVersion`, `Recommendation`, `FoodChoice` and `OfferedFoodChoiceRef` into its attempt directory. `artifact-sha256.json` records schema-source, dependency, generator, checker, reference-document, payload and exported-schema identities. The implementation record reports actual checks. These local sanity checks do not pass GG-007 or establish runtime refinement behavior.

Both commands require the installed backend environment and accept no arguments. Neither reads `.env` nor contacts a service. The generator writes its 25 named payloads beside itself. The checker reads those payloads and writes schemas and hashes into its attempt directory. Both exit with code 0 on success and nonzero on a write, validation, assertion or export failure.

The task-local check passed seven accepted payloads with round trips, eighteen expected rejections and six schema exports. Targeted Ruff lint and formatting checks passed. Broad contract and integration verification remains deferred to GG-007.
