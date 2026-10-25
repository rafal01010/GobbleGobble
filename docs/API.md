# Backend API contract

The Python models under `apps/backend/app/schemas/` are the authoritative payload definitions. [schema_export.py](../apps/backend/app/schema_export.py) exports their shared dependency graph as OpenAPI 3.1.0 JSON. GG-004.05 implements this export interface and documentation, awaiting Section B verification at GG-007.

The current document contains `components.schemas` and an empty `paths` object. No HTTP handlers, app factory, authentication transport, status-code mapping or running API exist yet. Operation names below identify payload contracts, not callable endpoints. Generated JSON is an output artifact, not a second source to edit.

## Export the contract

Use the installed Python environment from [backend setup](BACKEND_SETUP.md). Run these commands from the project root.

```sh
mkdir -p data/verification/api-contract
cd apps/backend
.venv/bin/python -m app.schema_export > ../../data/verification/api-contract/openapi.json
```

The command writes deterministic, indented JSON with a trailing newline to stdout. Shell redirection creates or replaces the named file; its parent directory must exist. The module itself opens no output file. A successful export exits with code 0. Generation or output errors exit nonzero. Do not consume a redirected file after a failed command because shell redirection may leave an empty or partial file.

`--help` prints usage and exits with code 0. No other arguments are accepted. An unknown argument prints an error to stderr and exits with code 2 without JSON on stdout. Use shell redirection to select the output file.

No `.env`, provider credentials, database, server, frontend or network access is required. The command imports schema modules without loading runtime settings or provider clients. It uses the existing Pydantic dependency and adds no package.

`build_openapi_document()` is the Python entry point. Its explicit root registry includes the operation requests and responses, run reads, individual events and event batches. Pydantic generates the nested domain components and references in one shared graph. Validation and serialization modes are generated together, preserving distinct input/output components if the models require them. JSON keys are sorted for repeatable exports. The exported document contains model definitions, not example searches, locations, reviews or secrets.

## Operation payloads

These classes live in [operations.py](../apps/backend/app/schemas/operations.py), except the read and event responses in [lifecycle.py](../apps/backend/app/schemas/lifecycle.py). Request identity fields describe operation context. Later route adapters must map their actual path, query and body inputs to the same contract without introducing competing wire definitions.

| Operation | Request model | Success model |
|---|---|---|
| Resolve current or chosen location | `ResolveLocationRequest` | `ResolveLocationResponse` |
| Start a search | `StartRunRequest` | `CommandResponse` |
| Read a run | `ReadRunRequest` | `RunReadResponse` |
| Watch after an event sequence | `WatchRunRequest` | A stream of `RunEvent` values. `EventBatch` validates supplied ordered batches. |
| Choose a researched food option or all options | `ChooseFoodRequest` | `CommandResponse` |
| Retry an interrupted or failed run | `RetryRunRequest` | `CommandResponse` |
| Cancel a run | `CancelRunRequest` | `CancelRunResponse` |

`ErrorResponse` wraps rejected-operation information. It is separate from a completed run's `technical_failure` research outcome. Future HTTP adapters must translate framework validation failures into the declared envelope and select status codes. Unknown or unowned resources must not expose another search's data. An ID is not authorization; anonymous ownership enforcement and transport remain GG-005.02.

The [lifecycle contract](LIFECYCLE_CONTRACT.md) defines ready, partial, no-match, insufficient-coverage and technical-failure outcomes, admission versus replay, cancellation dispositions and retained prior results. A partial outcome requires a useful result and limitations. A failure or no-match outcome cannot claim a newly published result. A choice preserves the exact offering-version/choice pair. Retry preserves the original run purpose.

## Domain definitions and relationships

| Authoritative module | Definitions and reference |
|---|---|
| [search.py](../apps/backend/app/schemas/search.py) | Location, origin, travel, search request and places. See [search contract](SEARCH_CONTRACT.md). |
| [evidence.py](../apps/backend/app/schemas/evidence.py) | Sources, use permissions, retention, freshness, evidence, food claims and review samples. See [evidence contract](EVIDENCE_CONTRACT.md). |
| [recommendations.py](../apps/backend/app/schemas/recommendations.py) | Research snapshots, recommendations, finite choices and version lineage. See [recommendation contract](RECOMMENDATION_CONTRACT.md). |
| [lifecycle.py](../apps/backend/app/schemas/lifecycle.py) | States, outcomes, supplied display-result validation and compact events. See [lifecycle contract](LIFECYCLE_CONTRACT.md). |
| [operations.py](../apps/backend/app/schemas/operations.py) | Location resolution, command, read, watch, retry, cancellation and error envelopes. |

Pydantic validators remain authoritative for cross-field and cross-record invariants. JSON Schema and generated frontend types do not fully encode branch support, candidate membership, lineage cycles, result-reference equality or strictly increasing event sequences. Validate incoming data with the applicable backend relationship boundary. Source truth, current permissions, freshness, active versions, actual ownership and atomic publication also require later runtime checks.

Fields declared nullable without a default remain required in JSON. For example, `RunSnapshot.attempt_id`, `RunEvent.result_version_id` and retry/cancel `expected_attempt_id` require an explicit value or null. A null attempt represents unclaimed work; a null event result version represents no new publication. Null does not mean that a field can be omitted.

## Events and version identities

`RunEvent.schema_version` is the wire format version, currently the strict integer 1. `result_version_id` identifies a published recommendation version and must match a ready or partial completion outcome. It is explicitly null otherwise. `search_id`, `run_id` and nullable `attempt_id` identify execution context.

Event sequence starts at 1 and is scoped to a run across retry attempts. A watch cursor can be 0 and resumes strictly after that sequence. Booleans are invalid sequence values. SSE framing and live delivery remain GG-005.07. The intended SSE `id` is the sequence within that run's stream, and `data` is the event JSON. Replaying events alone does not implement the required ongoing subscription.

Events exclude request locations, provider bodies and complete evidence collections. Typed text fields still require safe runtime projection and source-specific retention decisions. OpenAPI does not enforce that prose is safe or supported.

## Handoff to routes and frontend generation

GG-005.06 and GG-005.07 implement actual admission, read and event routes. Location resolution, refinement and recovery routes follow their own tasks. Their route declarations must import these same Pydantic classes. When routes exist, extend the canonical export entry point to obtain paths and HTTP metadata from the real FastAPI app and retain needed components such as `RunEvent` for streaming. Reconcile shared components through Pydantic's shared graph; incompatible definitions must not overwrite each other silently.

Keep one export command and one authoritative document. Do not maintain a hand-written OpenAPI path document alongside route declarations. Empty paths in this task are intentional and do not establish an HTTP client contract.

GG-006.03 runs after frontend initialization and backend route declaration. It owns the frontend generation command, generator choice, generated file location and consuming imports. That task must use the canonical export, record source/export provenance and generate wire types rather than copy interfaces by hand. It must check that the document now includes actual declared routes and that streaming event types are available. No frontend toolchain or generated TypeScript is required by GG-004.05.

## Local evidence and deferred verification

The task-local checker is `data/verification/GG-004.05/attempt-01/check_export.py`. From `apps/backend`, run it with the installed environment.

```sh
.venv/bin/python ../../data/verification/GG-004.05/attempt-01/check_export.py
```

It exercises the real export command and writes generated JSON, checks and artifact identities into its attempt directory. The command requires no arguments or credentials and exits nonzero if its assertions fail. The implementation record in [the plan](../IMPLEMENTATION_PLAN.md#gg-00405-document-and-expose-the-authoritative-backend-contract) records actual checks. GG-007 owns broad schema-export and documentation-consistency verification. No live API, frontend generation or application delivery is established here.
