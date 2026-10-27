# Architecture

GobbleGobble uses a Python backend, SvelteKit frontend and SQLite on one local host. The current implementation has a visual shell, typed contracts, durable run infrastructure and bounded provider probes. Real research is not connected to admitted searches, and recommendation publication, refinement and recovery remain incomplete.

## Components and ownership

| Source | Responsibility |
|---|---|
| `apps/frontend/src/` | Browser presentation. Currently a stateless light shell with a native preview disclosure. |
| `apps/backend/app/api/` | Anonymous session bootstrap, admission, paired status reads and event streaming. |
| `apps/backend/app/schemas/` | Authoritative request, evidence, recommendation and lifecycle models. |
| `apps/backend/app/db/` | Migrations, ownership, command identity, leases, checkpoints and events. |
| `apps/backend/app/worker.py` | Shared leased runner and explicitly selected diagnostic job. |
| `apps/backend/app/providers/` | Geographic, web-research and anonymous Google review clients. |
| `apps/backend/app/agents/probe.py` | Bounded Agents SDK owner/research experiment. |
| `apps/backend/app/core/` | Configuration, request bounds and safe receipts. |

The backend owns secrets, precise request locations, execution limits and durable state. Browser IDs do not authenticate requests. A server-issued anonymous credential owns searches; run operations also check membership in that search. One current worker lease fences mutations against fresh database time. Transactions close before external work, waiting or streaming.

A browser connection observes work rather than owning its lifetime. Disconnect does not cancel a run or admit another one. Duplicate commands replay existing work; conflicting reuse is explicit. See [the API](API.md), [persistence lifecycle](DATABASE.md) and [domain relationships](DOMAIN.md) for their contracts.

## Intended recommendation flow

1. Resolve current or chosen location, its timezone and a travel limit.
2. Admit queued work promptly and claim it through the leased lifecycle.
3. Let one recommendation owner obtain geography and focused research as evidence gaps warrant.
4. Validate source/branch support, practical eligibility and current ownership.
5. Commit a result version with its matching event before exposing it to the browser.

The owner supplies comparative judgment rather than an arbitrary fixed scoring pipeline. Missing menus, prices or reviews remain qualified gaps. Timeouts and provider outages cannot establish that no places exist. Sources and dependent summaries follow the [integration and retention policy](INTEGRATIONS.md). The normal flow and result publication are not implemented yet; the diagnostic worker demonstrates no food research.

## Frontend design

The shared stylesheet [app.css](../apps/frontend/src/app.css) owns parchment `#f7f7f4`, bone `#f2f1ed`, stone `#cdcdc9`, ink `#26251e`, 4px corners and 4px-based spacing. It declares a light color scheme and uses installed/system font fallbacks. The root layout receives generated `LayoutProps.children` and imports the stylesheet once. No API client or application state is wired yet.

Keep one main task prominent and reveal details when needed. Normal screens use ordinary food, travel, uncertainty and recovery language. Engineering terminology belongs in contributor references and separate diagnostics. There is no generic question textbox. Current/manual location controls retain their intended role. See [product requirements](PRODUCT.md) and [setup](SETUP.md).

## Context and completion requirements

This section defines intended production behavior, not implemented recovery capability. The existing probe has request/turn/byte limits and a restrictive food selector. Full request fitting, shared production accounting and durable context reconstruction remain incomplete.

Permitted research snapshots are reusable state; SDK history is a temporary view. Preserve stable search/place/branch/source identities, favorable food findings, complaints, qualifications, conflicts, viable alternatives and material omission status. A retained source ID alone does not prove the evidence reached the model. Restricted or expired content must be reacquired or marked unavailable, including content embedded in summaries or checkpoints.

Application-built context supplies authoritative location, destination timezone, eat-now time, travel mode/limit and current refinement choice to both owner and research helper. Delegation text cannot overwrite those constraints. Relevant public reviews retain complaints, contrast, quotations, personal references and local-language meaning under [the review-input policy](INTEGRATIONS.md#public-review-model-input).

Fit the complete next request, including instructions, tool definitions, output schema, findings and SDK history. Reserve output and hosted-tool headroom. Byte caps alone do not enforce token limits. Remove duplicates and irrelevant detail, select bounded views, defer retrievable material and record omissions without labeling it reviewed. Preserve SDK call/result relationships. Request-context capacity and total run allowance are separate limits.

Nested research, compaction, recovery and completion share the attempt's existing usage and deadline. They cannot erase consumed work or obtain fresh budgets. Fitting adds no model call by default. Keep local estimates distinct from actual provider usage and log only safe accounting metadata.

Reserve requests, context, output tokens and time for final composition before continuing research. When limits require closure, close tools and compose from retained usable findings. Failed or oversized intermediate replies preserve completed sibling contributions. Supported partial results remain possible; missing optional facts do not require abstention. Irreducible requests and exhausted allowance remain technical outcomes, never false no-matches conclusions.

Malformed or incomplete final output cannot publish a recommendation. A valid response is not rejected solely for reaching its cap. At most one affordable tools-free recovery completion may use prepared context under the same model, constraints, usage and deadline. It cannot restart research. Repeated failure publishes no invalid version. Live recovery and comparisons need explicit authorization; there are no automatic paid retries or silent model upgrades.

### Context acceptance cases

The following identifiers name observable requirements used in contributor checks. They are not assertions that these checks have passed.

| Case | Required observation |
|---|---|
| CTX-01. Oversized evidence | A large result becomes a bounded view with omission status and usable scoped lookup. Previously completed findings remain available. |
| CTX-02. Full-request pressure | Growing history, tool/schema overhead and an injected context-limit error exercise fitting or a bounded stop. SDK relationships remain valid and nested usage stays charged. |
| CTX-03. Failed intermediate | Truncated JSON, incomplete provider output and failed nested research preserve completed sibling findings with a typed gap or stop reason. |
| CTX-04. Decision evidence survives | Favorable food evidence, a complaint, qualifications and a viable late alternative reach the owner with correct identities after reduction. Saved recommendations reflect those findings and current constraints. |
| CTX-05. Reserved synthesis | Research exhaustion, oversized nested output and context-limit errors close research and permit a useful final or partial result when completion is affordable. Spent work remains charged. |
| CTX-06. Final-output recovery | Invalid final output triggers at most one affordable tools-free recovery. Valid output at its cap is accepted. Repeated failure publishes no invalid version. |
| CTX-07. Honest stopping | Irreducible context, no usable evidence, depleted reserve and cancellation have distinct outcomes. They fabricate neither recommendations nor no-matches conclusions. |
| CTX-08. Restored and refined context | Restart, retry and refinement rebuild bounded context from permitted checkpoints with preserved usage and current choice/constraints. Expired or restricted evidence is reacquired or marked unavailable. Prior version identity survives. |
| CTX-09. Quality regression | Context-policy changes retain useful food explanations, comparisons and complaints under the [paired quality assessment](EVALUATION.md). Controlled findings remain separate from live comparisons. |

Inspect actual supported content in contributions and saved recommendations alongside bounds/accounting. Production review validation must show relevant Toyo Eatery and Ippei Honten evidence reaching the research model and supporting its findings without the legacy selector. Use [the quality protocol](EVALUATION.md) when context changes could affect recommendations.
