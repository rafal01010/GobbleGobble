# Agent context and completion contract

This reference defines planned production behavior for GobbleGobble's research tool-agent, recommendation owner and recovery paths. It records lessons from CartCart and assigns acceptance cases to existing tasks in [the implementation plan](../IMPLEMENTATION_PLAN.md). These requirements are not implemented or verified by this document.

Recoverable context-size and intermediate-output failures must preserve usable research and permit a supported final or partial recommendation when sufficient evidence and completion allowance remain. Cancellation, source restrictions, an irreducible request or exhausted total resources can still require an honest technical outcome. A context failure must never become evidence that no suitable places exist.

## CartCart findings and their application

| Inspected CartCart finding | GobbleGobble requirement |
|---|---|
| Real requests exceeded their per-call allowance while cumulative allowance remained. JSON whitespace reductions alone did not solve the accumulated tool-output problem. [Incident and fitting policy](../../CartCart/docs/CONTEXT_MANAGEMENT.md#automatic-request-fitting), [model boundary](../../CartCart/apps/backend/app/agents/context_management.py). | Fit the complete next model request, including instructions and schemas. Account for per-request context separately from cumulative usage. |
| A real recommendation lost useful dated positive excerpts during compaction even though the brief and handoffs survived. [Recorded failure and repair](../../CartCart/docs/TASK_216_LIVE_ACCEPTANCE.md#actual-failures-and-repairs). | Verify that favorable findings, complaints, qualifications and meaningful alternative places survive reduction and reach final composition. |
| SDK history requires related response items and tool call/result pairs to remain consistent. CartCart archives complete exchanges and supplies bounded views. [Exchange compaction](../../CartCart/apps/backend/app/agents/request_compaction.py). | Preserve SDK protocol relationships when fitting history. Use scoped evidence references and bounded views without replaying every raw intermediate. |
| CartCart reserves completion capacity and can close tools before owner synthesis. Invalid final output has bounded recovery under the existing allowance. [Completion implementation](../../CartCart/apps/backend/app/agents/context_management.py), [output failure investigation](../../CartCart/docs/TASK_224.md). | Reserve requests, context, output tokens and time for final composition. Recover without restarting research or resetting spent usage. |
| A faithful request can remain impossible to fit. CartCart tests that case as a technical failure. [Impossible-bound controls](../../CartCart/apps/backend/tests/test_task226_policy.py). | Retain permitted progress and report the real limitation. Do not discard essential constraints or invent a successful result to force completion. |

These references were inspected as source, prepared tests and historical reports on 2026-10-24. They are not new CartCart runtime validation. GobbleGobble retains one final owner and a research agent as a tool. This contract adds no specialist handoff hierarchy or CartCart spending defaults.

## Retained findings and model-facing context

The research snapshot is the source of reusable permitted findings. The SDK conversation is a temporary view of that state. Each contribution keeps stable search, place, branch and source identities, useful food findings, source qualifications, conflicts, known gaps and the status of omitted material. Relevant findings from viable places outside the visible shortlist remain available.

A role receives the current request constraints and a bounded view of findings relevant to its question. Original location, destination timezone, eat-now time, travel mode and limit, and the current offered refinement choice retain their authoritative versions. Model-generated delegation text cannot overwrite them. The tool-agent receives application-built scoped context rather than depending on the owner to repeat every relevant fact in a tool argument.

Public place reviews do not require personal-information minimization before model input under the user's 2026-10-25 revision. Context fitting must not reuse the feasibility probe's personal-reference, fixed-vocabulary, English-only or positive-only filters. Preserve relevant complaints, contrasts, negation, quotations and local-language findings. Use batching and source lookup with explicit omissions when review material exceeds context limits. Such execution bounds are not a privacy filter. GG-011 must prove the production research agent receives and interprets Toyo and Ippei evidence under this contract.

Bounded results identify selected, deferred, unavailable and unreviewed material. Scoped lookup supports bounded continuation or focus reads using stable IDs. An omitted passage is not evidence of absence. A retained source link alone is not proof that its useful contents reached the final owner.

The [Google review contract](GOOGLE_REVIEWS.md) and [source-use decisions](INTEGRATIONS.md) govern raw and derived content, including snapshots, summaries, checkpoints, events and diagnostics. Prohibited source text cannot be archived or copied into a summary as a workaround. Restricted or expired evidence requires permitted reacquisition or an explicit gap. CartCart's archival implementation grants no GobbleGobble retention permission.

## Complete-request fitting and shared accounting

Every production model request, including nested research and completion requests, uses a shared context boundary. The boundary measures instructions, tool definitions, output schemas, supplied findings and SDK history before dispatch. It reserves output and any configured hosted-tool headroom. Local estimates and actual provider usage remain separately labeled. Byte caps are transport controls, not a complete token-context policy.

Fitting first removes duplicates and irrelevant detail, then selects bounded evidence views or defers retrievable material. It preserves current user constraints, branch/source identity, material positive and adverse findings, qualifications and alternative coverage needed for the requested comparison. It keeps SDK call/result and reasoning relationships valid. It never labels omitted material as reviewed.

All nested work consumes the attempt's existing allowance. Child agents, compaction, recovery and completion cannot create fresh budgets, erase consumed usage or bypass deadlines. Fitting adds no model call by default. GG-010.04 records actual configuration, estimates, usage, reduction reason, omitted counts and fixed failure categories without source-bearing logs. Numerical limits remain implementation decisions supported by gate evidence.

## Research closure and final output

Before further research, the owner preserves an enforceable reserve for a complete final request and response. When context, turns, requests or time require closure, tools close and the owner composes from retained usable findings and current constraints. No additional search, helper delegation or hidden lookup can consume the reserved completion allowance.

An oversized, malformed, truncated or failed research reply has a typed outcome. Completed sibling contributions remain usable. A failed investigation does not automatically fail the whole run. GG-010.04 handles fitting and bounded research outcomes. GG-012.02 owns the decision to continue affordable research or close into final composition.

A supported partial recommendation remains available when evidence warrants fewer choices or disclosed gaps. Missing optional menus, prices or reviews do not alone prevent completion. Publication still applies the existing evidence, practical-eligibility and version rules.

Incomplete provider output and malformed final JSON cannot be published as a successful recommendation. A completed valid response is not rejected solely because it reaches an output cap. One tools-free owner recovery completion may use the prepared context if the configured reserve and remaining deadline permit it. It keeps the configured model, current constraints and consumed usage, and cannot restart research. Another invalid completion or insufficient allowance produces a truthful technical outcome with permitted progress retained.

That recovery call is accounted application work, not a transport retry. Its live execution must be included in a separately requested gate's explicit scenario and call inventory. Implementation tasks make no live calls. Development failures do not trigger automatic paid reruns, model upgrades or limit increases. The [development cost policy](../IMPLEMENTATION_PLAN.md#development-model-cost-policy) continues to apply.

## Acceptance cases and ownership

The CTX identifiers below name controlled acceptance scenarios. They add no executable queue tasks. Implementation children prepare the behavior and useful cases. Their named section gates execute the checks. GG-011 verifies research outputs without requiring the unfinished production recommendation owner. GG-015 checks CTX-07 context-stopping outcomes; end-to-end cancellation remains GG-019.

| Case | Required observation | Implementation owner | Verification gate |
|---|---|---|---|
| CTX-01. Oversized evidence | A large research result produces a bounded view with omission status and usable scoped lookup. Previously completed findings remain available. | GG-010.01, GG-010.02, GG-010.03, GG-010.04 | GG-011 |
| CTX-02. Full-request pressure | Growing history, large tool/schema overhead and an injected provider context-limit error exercise fitting or an explicit bounded stop. SDK relationships remain valid and nested usage remains charged. | GG-010.04 | GG-011 |
| CTX-03. Failed intermediate | Truncated JSON, incomplete provider output and a failed nested research invocation leave completed sibling findings usable and expose a typed gap or stop reason. | GG-010.03, GG-010.04 | GG-011 |
| CTX-04. Decision evidence survives | Under forced context reduction, distinctive favorable food evidence, a material complaint, source qualifications and a viable late alternative reach the owner with correct identities. The persisted recommendation reflects those findings and current location/travel constraints. | GG-012.02, GG-012.03 | GG-015 |
| CTX-05. Reserved synthesis | Research turn exhaustion, cumulative research allowance exhaustion, an oversized nested result and an injected context-limit error close further research and permit a useful final or partial result when completion remains affordable. Spent work stays charged. | GG-012.02 | GG-015 |
| CTX-06. Final-output recovery | Incomplete or malformed final output triggers at most one affordable tools-free recovery completion. Valid completed output at its cap is accepted. Repeated invalid output remains a technical failure and publishes no invalid version. | GG-012.02 | GG-015 |
| CTX-07. Honest stopping | Irreducible required context, no usable evidence, depleted completion allowance and cancellation produce their correct outcomes. They do not fabricate a recommendation or a no-matches conclusion. | GG-012.02 | GG-015, with end-to-end cancellation at GG-019 |
| CTX-08. Restored and refined context | Restart and retry rebuild bounded context from permitted checkpoints with preserved usage. Refinement uses the current choice and constraints. Expired or restricted evidence is reacquired or marked unavailable. Prior results retain their version identity. | GG-018.01, GG-018.02 and existing refinement children | GG-019 |
| CTX-09. Quality regression | Reuse affected CTX evidence after context-policy changes and inspect useful food explanations, comparisons and complaints alongside the paired quality assessment. Forced controls remain separate from real ChatGPT comparisons. | Existing quality-tuning children | GG-023 |

Controlled cases use the installed SDK with deterministic local transport, plus the normal persistence and result API where implemented. They assert supported content in returned contributions and saved recommendations as well as bounds, statuses and accounting. A smaller request, passing schema or list of retained IDs alone cannot establish decision-evidence preservation.

Gate evidence records the code/configuration identity, configured bounds, before/after estimates, observed request bytes and provider usage where available, closure/recovery counts and the final result status. Content inspection and retained artifacts follow source policy. Synthetic findings and usage remain labeled as controls. Normal bounded live gate scenarios establish real source/model usefulness and source-to-owner delivery separately.

## Current implementation boundary

GG-002.07 prepares one owner/research SDK interaction with minimized Google food passages, bounded bytes/tokens/turns and exact passage checks. Its oversized-continuation case expects a stopped result. This is an unexecuted prepared control, not production context recovery or proof of the CTX cases above. See [the SDK probe reference](SDK_PROBE.md).

GG-010 and GG-012 implement the production contracts in sequence. GG-018 adds durable continuation. GG-011, GG-015, GG-019 and GG-023 own their respective verification. This document changes no runtime settings, completion state, task order or live-call authorization.
