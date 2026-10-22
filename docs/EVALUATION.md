# Paired quality-comparison protocol

This document defines the GG-001.03 protocol before application outputs exist. GG-001.03 is implemented, awaiting section verification at GG-003. No baseline, paired result, timing, cost, or quality verdict has been captured. The [product contract](PRODUCT.md#quality-and-timing) defines the target. The [source-use findings](INTEGRATIONS.md#permission-findings) constrain all research and retained evidence.

## Comparison target

GobbleGobble must be at least as useful overall as normal ChatGPT with web search for each matched nominal scenario. The comparison concerns a person's decision about where to eat now and what food to get. A scenario cannot pass through an average that hides a material loss in discovery, food usefulness, or supporting information.

The baseline is an actual answer from the normal ChatGPT interface with web search. An API model, simulated transcript, fixture, agent-generated substitute, or remembered answer is not this baseline. A missing ChatGPT session leaves comparative acceptance pending. GG-020.02 provides capture tooling or exact manual operator steps. Actual paired capture and acceptance belong to GG-023.

English output and single-host local operation remain starting assumptions. Research can use local-language evidence and preserve place names. This protocol does not introduce future-date planning or change the product's travel and review rules.

## Predefined scenario classes

The GG-020.01 manifest must contain at least six real pairs across at least three countries. The minimum classes below reserve Japan and two distinct additional countries. Country B and Country C are placeholders for selection in GG-020.01. They are not a country whitelist. Density describes the selected area, not how many places either system happens to return.

| Class | Geographic and travel context | Intended observation |
|---|---|---|
| Q-01 | Dense Japanese area, walking within 15 minutes, chosen location entered without Japanese characters | Japanese-language evidence, distinct outlets, and useful food suggestions near a romanized origin. |
| Q-02 | Dense area in Country B, walking within an adjusted 10-minute limit | The shorter walking constraint and suitable restaurants or cafes. |
| Q-03 | Sparse area in Country C, driving within 30 minutes | Mode-specific journey support, limited coverage, and justified smaller sets. |
| Q-04 | Dense area in Country C, walking within 15 minutes | Food courts, stalls, or takeaway outlets, with venue and outlet identities kept distinct. |
| Q-05 | Dense area in Country B, driving within 30 minutes | Branch identity, driving constraints, and comparative reasons across worthwhile alternatives. |
| Q-06 | Sparse Japanese area, walking within an adjusted 20-minute limit | Local-language research outside a dense center, practical uncertainty, and omission handling. |

Exact origins, countries B and C, coordinates, destination timezones, capture windows, and outlet-coverage expectations remain unset. GG-020.01 fixes them before either system produces candidate answers. It also finalizes human scoring anchors consistent with the criteria below. The nominal set must cover varied meal outlets across restaurants, cafes, stalls, food courts, and takeaway. No scenario requires an unsuitable outlet merely to satisfy a type count.

Do not choose or replace a location because the app already performed well there. Record the manifest revision and selection rationale before runs. Keep failed attempts and any later amendments. A replacement requires a documented input, access, or matching defect and a fresh pair. A weak answer remains a quality finding and cannot be removed by relabeling its scenario.

Rare evidence conditions belong to separately labeled controls. GG-023 includes no-review, mixed-review, relevance-selected negative samples, missing-menu, closed-place, route-unavailable, and provider-outage cases. These controls do not replace any of the six real pairs. Naturally occurring versions can also inform a real comparison without being required to occur on demand.

## Matched inputs and execution

The same user constraints must reach both systems. The app can receive structured controls while ChatGPT receives their equivalent wording. The capture implementation must preserve both submitted representations so matching can be checked.

The shared timestamp records the app's actual eat-now context. Supply that time to ChatGPT as the reference for the pair. It does not require an app clock override or a meal-date control.

| Input or condition | Required match or record |
|---|---|
| Origin | The same resolved coordinates and human-readable place, branch, or address. Record coordinate precision and resolution. For current-location cases, use the browser's actual resolved point for both systems. |
| Eat-now context | The same reference UTC timestamp, destination local date/time, and timezone. Record each actual submission and completion time. Do not use the server timezone as destination time. |
| Travel | The same walking limit or driving-within-30-minutes constraint. A radius or route in another mode is not an equivalent input. |
| Preferences | The same explicitly supplied food preferences and other supported constraints. Nominal initial searches have no extra preference. Any later refinement pair receives the same preference and shared user context. |
| Requested answer | English output, up to three main choices and two to four worthwhile runners-up, useful why-go and food advice, sources, practical qualifications, and relevant complaints. Explain smaller sets from research. Menus, prices, and reviews remain optional. |
| Baseline context | A fresh ordinary ChatGPT conversation with web search. Record visible model/mode, search indication, account settings that affect answers, and any memory or personalization context. Avoid prior answers or app findings in its context. |
| App context | A fresh normal app search using real providers and models. Record run/result/version IDs, code revision or diff identity, effective non-secret settings, bounds, and any usable cached evidence with its age. |

The baseline must receive the product's evidence rules as user requirements. Exact dishes and prices need sources, inferred food needs a label, mixed or unreviewed places remain eligible, and selected negative reviews do not establish uniformly bad reception. This shared brief must not supply candidates, internal prompts, or app research. Search wording, research strategy, sources, and answer layout can differ.

Use the following procedure when GG-023 authorizes live execution.

1. Freeze the manifest, shared brief, comparison criteria, code identity, and effective configuration before either answer. Confirm access, source permissions, and capture prerequisites. This is preparation, not a passing quality verdict.
2. Submit both systems near the shared eat-now reference time. Use a planned maximum five-minute window containing the reference time and both submission times. Record actual differences. This tolerance is a protocol choice, not a measured freshness guarantee.
3. Preserve the first completed answers and all failures. Do not coach one side with the other's results, regenerate until favorable, or give only one side extra clarification. The baseline capture must show actual web-search use through visible interface evidence or available search records. A selected mode label alone does not prove a search occurred.
4. Check matching before scoring. A material opening/service change across the capture interval, wrong origin, different constraints, or uncontrolled prior context makes the pair unmatched. Retain the reason and repeat the whole pair if authorized. A provider error, timeout, or weak answer under valid matched inputs remains an execution or quality finding, not grounds for discarding it.
5. Prepare A/B answer views for a human reviewer. Randomize the assignment and retain the mapping separately. Preserve recommendations, wording, order, qualifications, and usable citations. Remove system-identifying presentation only where attribution requirements permit. Record any blinding limit rather than altering meaning or required credit.
6. Have the reviewer record criterion judgments, concrete supporting passages, source findings, and an overall verdict before revealing the assignment where feasible. A reviewer who operated the runs must disclose that the review was not fully blind. Automated checks and an agent's opinion cannot substitute for the named human judgment.
7. Reveal the assignment and combine the human assessment with matching, factual, timing, and execution records. Record defects, proposed repairs, and affected reruns. A repaired case requires renewed paired evidence on the changed configuration. Earlier failures remain in the record.

Hidden baseline model versions, usage, and account controls must be marked unknown or unavailable rather than guessed. Uncontrolled context that materially affects matching leaves the pair unmatched. Lack of required search or answer evidence leaves it pending. Different internally chosen providers or models do not invalidate a normal-product comparison when the supplied user constraints match.

## Observable quality judgments

For each criterion, the reviewer records whether the app is better, equivalent, worse, or unresolved relative to ChatGPT, with a reason tied to evidence. Honest absence of optional information is not itself a failure. Supported useful detail can still make one answer more helpful. No numeric weights or final scoring form are implemented here. GG-020.01 finalizes scoring without weakening per-scenario acceptance.

| Criterion | Observable judgment and supporting evidence |
|---|---|
| Decision usefulness | Can the person choose a specific place and understand why it fits, how it compares, and which alternatives are worthwhile? Cite actionable reasons, unique branches/outlets, primary/runner membership, and any research-backed explanation for a smaller set. More names alone do not establish usefulness. |
| Food detail | Does the answer help the person decide what food to get? Check concrete sourced dishes or useful cuisine-based suggestions with clear inference labels. Verify exact dishes and prices against the correct branch, source date, and currency. Missing menus or prices alone do not reject a place. |
| Source support | Are the important claims attributable through usable links to relevant evidence? Check claim-to-source support, outlet identity, dates, citation clarity, and stated uncertainty. A search hit or citation count alone does not establish support. Mark inaccessible or inconclusive support unresolved. |
| Practical accuracy | Do branch/location, travel mode, journey estimate, opening/service evidence, destination local time, and estimated arrival support eating now within the selected limit? Check timestamps and source limitations. A clearly labeled route-unconfirmed alternative must not be presented as confirmed within the limit. |
| Complaints and review fairness | Are relevant observed complaints visible, with sample size, dates, and selection limitations? Mixed reviews and unreviewed/new places remain eligible. Verify any uniformly-negative exclusion against the actual sample and selection method. Absence of reviews is an unknown, not a negative judgment. |
| Unjustified omissions | Did either system omit a materially useful supported option or omit decision-relevant food, practical information, or complaints? Examine the union of returned places and independent source findings. Record justified exclusions, duplicate branches, true absence, and insufficient coverage separately. Neither answer is ground truth or an exhaustive list. |
| Timing | Measure each system's elapsed time from submission to its first completed useful answer. Record first visible progress and terminal completion separately when observable. The app target is a useful nominal answer within 180 seconds. A progress event, empty result, or unsupported timeout answer does not satisfy it. |

Source spot checks apply to both answers under the same rules. Inspect every main recommendation's branch, travel and eat-now claims, and food support. Check exact prices and review-based exclusions wherever they appear. Check runners' identity and travel eligibility, plus claims involved in any material omission or disputed comparison. Record the checked claims and unresolved items. Do not infer factual truth from fluent text or a correct output schema.

The human overall verdict is that the app is more useful, at least as useful, less useful, or unresolved for that scenario. It must explain the person's ability to choose and any material loss across the criteria. A material loss in discovery, food detail, or supporting information needs explanation and repair. Per-criterion labels inform the overall verdict but are not a vote or an average.

## Acceptance and failure handling

GG-023 requires all predefined nominal pairs to have valid matching, genuine baseline/search evidence, an attributable human verdict of at least as useful, and no unresolved material quality or factual defect. A favorable average across countries, scenarios, criteria, or attempts cannot cancel a failed case.

Unsupported factual assertions, false travel-limit claims, unjustified review exclusion, or an empty answer when supported options exist fail the gate. A truthful limited/no-match answer needs actual research that explains its outcome. Missing optional menus, prices, or reviews alone does not establish failure. Unknown facts must remain qualified, and unresolved material support keeps acceptance pending.

The nominal useful-answer target is 180 seconds for the app. Record and investigate every miss rather than silently increasing the target. An unresolved miss keeps timing acceptance open. Baseline timing is reported separately and does not relax the app target. No statistical or worldwide guarantee follows from this finite comparison.

Controlled edge cases, desktop/mobile inspection, keyboard use, and affected offline checks are additional GG-023 obligations. Passing controls does not establish live parity, and a live parity verdict does not establish those separate obligations.

## Evidence, timing, and retention

Each later attempt belongs under ignored `data/verification/GG-023/attempt-XX/`. Preserve the following evidence when permitted.

- The frozen scenario manifest and shared brief, actual submitted inputs, matching decision, reference and actual timestamps, destination timezone, and scenario-selection rationale.
- The actual paired outputs or permitted captures with clickable sources, visible baseline model/mode/search evidence, capture method, and disclosed context or blinding limits.
- App code revision or diff identity, effective non-secret provider/model identities and limits, run/result/version IDs, invocation receipts, evidence age/reuse, failures, and retries.
- A named human's criterion judgments and overall per-scenario verdict, A/B mapping, source spot checks, material omissions, and unresolved facts.
- Submission, progress where visible, useful-answer, and terminal timings with units and measurement method. Use a monotonic clock for app elapsed durations and UTC timestamps for cross-system correlation. Identify manually measured baseline times and their precision.
- App token/tool usage and reported or estimated costs, with the price basis and missing items identified. Baseline token usage or marginal cost may be unavailable. Mark it unavailable rather than zero. Human review time is separate from answer latency.
- The defect disposition, related repair IDs, all failed attempts, and the exact configuration used by a successful rerun.

Source policies apply to originals, excerpts, screenshots, normalized views, reviewer notes, and derived findings. Factual freshness and permitted retention are separate. Do not save restricted content merely to make the comparison replayable. Keep only permitted metadata when content retention is prohibited, and record the omission reason and expiry. Omit secrets and private location data from shared reports. Use deliberate public scenario origins for retained manifests.

If restrictions prevent enough permitted evidence from supporting the paired human assessment, record the limitation and keep comparative acceptance pending. Do not silently substitute invented text, an API answer, or unsupported metadata. Later capture and evidence enforcement must follow the [source-use findings](INTEGRATIONS.md#openai-processing-and-diagnostics) and the selected policies established at GG-003 and GG-009.

## Task boundaries and current status

| Task | Owned outcome |
|---|---|
| GG-001.03 | This protocol, observable criteria, predefined scenario classes, and document-local checks. No exact scenario manifest, capture, runner, or live comparison. |
| GG-003 | Section A contract review, including whether each criterion has an observable judgment, plus the separate bounded live feasibility probes. A ChatGPT baseline capture is not required at this gate. |
| GG-020.01 | Exact frozen scenario inputs and finalized human scoring rules consistent with this protocol. |
| GG-020.02 | Genuine baseline capture implementation or exact manual steps, and the blinded comparison form. |
| GG-020.03 | Evaluation execution, explicit offline/live selection, and actual timing, usage, and cost reporting. |
| GG-023 | Authorized real pairs, named human assessments, controls and real interface checks, measurements, defect disposition, and the per-scenario quality verdict. |
| GG-025 | Final acceptance retains the passing GG-023 comparison on the delivered configuration, with renewed evidence for relevant behavior changes. |

The document can be reviewed now. It establishes no measured application capability or parity result. Provider access, permissions, actual ChatGPT access, operational scoring/capture tools, and all live quality evidence remain subject to their scheduled tasks. GG-003 and GG-020 through GG-025 remain incomplete.
