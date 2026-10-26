# Backend API contract

The Python models under `apps/backend/app/schemas/` are the authoritative payload definitions. [schema_export.py](../apps/backend/app/schema_export.py) exports their shared dependency graph as OpenAPI 3.1.0 JSON. GG-004.05 implements this export interface and documentation, awaiting Section B verification at GG-007.

GG-005.06 implements four HTTP operations in `app/api/application.py`. The canonical document contains their real paths, success and error responses, cookie security metadata, and the retained payload graph. Generated JSON is an output artifact, not a second source to edit. HTTP and database behavior remain unverified until GG-007.

## Export the contract

Use the installed Python environment from [backend setup](BACKEND_SETUP.md). Run these commands from the project root.

```sh
mkdir -p data/verification/api-contract
cd apps/backend
.venv/bin/python -m app.schema_export > ../../data/verification/api-contract/openapi.json
```

The command writes deterministic, indented JSON with a trailing newline to stdout. Shell redirection creates or replaces the named file; its parent directory must exist. The module itself opens no output file. A successful export exits with code 0. Generation or output errors exit nonzero. Do not consume a redirected file after a failed command because shell redirection may leave an empty or partial file.

`--help` prints usage and exits with code 0. No other arguments are accepted. An unknown argument prints an error to stderr and exits with code 2 without JSON on stdout. Use shell redirection to select the output file.

No `.env`, provider credentials, database, server, frontend or network access is required. The command constructs the real app with explicit inert settings without invoking environment loaders or provider clients. It uses the existing Pydantic dependency and adds no package.

`build_openapi_document()` is the Python entry point. Its explicit root registry includes the operation requests and responses, run reads, individual events and event batches. The real app supplies route metadata. Pydantic supplies a shared graph for retained roots. The exporter normalizes that graph through FastAPI's OpenAPI model, requires overlapping component definitions to match exactly, and rejects unresolved references. Validation and serialization modes are generated together, preserving distinct input/output components if the models require them. JSON keys are sorted for repeatable exports. The exported document contains model definitions, not example searches, locations, reviews or secrets.

## Operation payloads

These classes live in [operations.py](../apps/backend/app/schemas/operations.py), except the read and event responses in [lifecycle.py](../apps/backend/app/schemas/lifecycle.py). Request identity fields describe operation context. The implemented start and read routes reuse these contracts. Later adapters must reuse them as well.

| Operation | Request model | Success model |
|---|---|---|
| Resolve current or chosen location | `ResolveLocationRequest` | `ResolveLocationResponse` |
| Start a search | `StartRunRequest` | `CommandResponse` |
| Read a run | `ReadRunRequest` | `RunReadResponse` |
| Watch after an event sequence | `WatchRunRequest` | A stream of `RunEvent` values. `EventBatch` validates supplied ordered batches. |
| Choose a researched food option or all options | `ChooseFoodRequest` | `CommandResponse` |
| Retry an interrupted or failed run | `RetryRunRequest` | `CommandResponse` |
| Cancel a run | `CancelRunRequest` | `CancelRunResponse` |

`ErrorResponse` wraps rejected-operation information. It is separate from a completed run's `technical_failure` research outcome. The HTTP boundary emits fixed messages and never returns framework validation details, submitted input, storage errors or tracebacks. Unknown, unowned and mismatched search/run pairs share the same 404. Credentials are separate from payloads and never belong in URLs.

The [lifecycle contract](LIFECYCLE_CONTRACT.md) defines ready, partial, no-match, insufficient-coverage and technical-failure outcomes, admission versus replay, cancellation dispositions and retained prior results. A partial outcome requires a useful result and limitations. A failure or no-match outcome cannot claim a newly published result. A choice preserves the exact offering-version/choice pair. Retry preserves the original run purpose.

GG-005.03 accepts the existing `StartRunRequest` through `app.db.runs.admit_start` inside a caller-owned transaction. First admission returns `CommandResponse` with accepted queued state, no attempt, and sequence 1 with a matching queued event since GG-005.05. Identical normalized input replays the same identities and the current persisted snapshot. Conflicting command reuse raises `CommandConflict` with fixed message `command_conflict`. The caller delivers only after commit. GG-005.06 exposes this callable through `POST /api/runs` with HTTP 202 for both accepted and replayed responses. Database behavior remains unverified until GG-007. GG-005.04 adds trusted internal [claim, renewal, and fenced running-stage callables](WORKER_LEASES.md). Their credentials are not browser credentials or public API fields. GG-005.05 adds [atomic checkpoints and trusted internal event and work readers](RUN_CHECKPOINTS.md). These repositories add no HTTP routes or export changes.

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

Event sequence starts at 1 and is scoped to a run across retry attempts. A watch cursor can be 0 and resumes strictly after that sequence. Booleans are invalid sequence values. GG-005.07 implements [replay followed by live SSE](RUN_EVENTS.md). The SSE `id` is the sequence within that run's stream, and `data` is the event JSON. HTTP and database behavior remain unverified until GG-007.

Events exclude request locations, provider bodies and complete evidence collections. Typed text fields still require safe runtime projection and source-specific retention decisions. OpenAPI does not enforce that prose is safe or supported.

## Handoff to routes and frontend generation

GG-005.06 implements admission and read routes. GG-005.07 implements replay followed by live SSE. Location resolution, refinement and recovery routes follow their own tasks. Route declarations import the same Pydantic models as the retained graph, including `RunEvent` for streaming. Incompatible schema definitions fail export rather than overwrite each other.

Keep one export command and one authoritative document. Do not maintain a hand-written OpenAPI path document alongside route declarations.

GG-006.03 runs after frontend initialization and backend route declaration. It owns the frontend generation command, generator choice, generated file location and consuming imports. That task must use the canonical export, record source/export provenance and generate wire types rather than copy interfaces by hand. It must check that the document now includes actual declared routes and that streaming event types are available. No frontend toolchain or generated TypeScript is required by GG-004.05.

## Local evidence and deferred verification

The task-local checker is `data/verification/GG-004.05/attempt-01/check_export.py`. From `apps/backend`, run it with the installed environment.

```sh
.venv/bin/python ../../data/verification/GG-004.05/attempt-01/check_export.py
```

It exercises the real export command and writes generated JSON, checks and artifact identities into its attempt directory. The command requires no arguments or credentials and exits nonzero if its assertions fail. The implementation record in [the plan](../IMPLEMENTATION_PLAN.md#gg-00405-document-and-expose-the-authoritative-backend-contract) records actual checks. GG-007 owns broad schema-export and documentation-consistency verification. That earlier checker describes the schema-only GG-004.05 artifact. The current GG-005.06 checker is described below. Frontend generation and application delivery remain later work.


## Implemented HTTP operations

Run the factory using [backend setup](BACKEND_SETUP.md#run-the-admission-api). `create_app(api_settings=..., database_settings=...)` accepts explicit frozen settings. `create_app_from_environment()` loads only API and database settings. Import, construction and export perform no database access or migrations.

| Method and path | Success | Behavior |
|---|---|---|
| `GET /ready` | 200 `{"status":"ready"}` | Opens existing storage and requires the complete migration history with matching checksums. Does not repair storage, issue a session, call providers or claim worker readiness. |
| `POST /api/session` | 204, empty body | Explicitly bootstraps an absent cookie. Resolves a supplied cookie without rotation. An invalid or empty cookie receives 401. |
| `POST /api/runs` | 202 `CommandResponse` | Commits queued admission or replays the current durable snapshot without waiting for research. |
| `GET /api/searches/{search_id}/runs/{run_id}` | 200 `RunReadResponse` | Authorizes the search, then selects the run by both IDs. No result publication or recommendation storage is implemented. |
| `GET /api/searches/{search_id}/runs/{run_id}/events` | 200 `text/event-stream` | Replays after a sequence, then polls for new committed events. See the [cursor, framing and follow contract](RUN_EVENTS.md). A caught-up completed coverage-gap or failure run returns 204 with GG-005.08 writer/history support. Actual transport verification remains GG-007. |

Bootstrap must finish before concurrent authenticated requests. The browser sends credentials on subsequent requests. Cookies are host-only `gg_session` with `HttpOnly`, `Path=/` and `SameSite=Lax`. `Secure` is set for HTTPS requests or `GG_SESSION_COOKIE_SECURE=true`. There is no `Domain`, persistent expiry, rotation or recovery mechanism. Trust the server's configured request scheme, not arbitrary forwarded headers.

Every POST, PUT, PATCH or DELETE request requires exactly one `Origin` matching a configured frontend origin before body parsing or storage access. Missing, duplicate, `null` and unmatched origins receive 403. Credentialed CORS permits only configured origins, GET and POST, and safelisted headers including Content-Type, plus Last-Event-ID. Rejected preflights receive 400 in the same error envelope. All responses include `Cache-Control: no-store`.

| HTTP status | Error code and fixed message | Retryable |
|---|---|---|
| 400 | `invalid_request`, `cors_not_allowed` | false |
| 401 | `invalid_request`, `invalid_session` | false |
| 403 | `invalid_request`, `origin_not_allowed` | false |
| 404 | `not_found`, `not_found` | false |
| 405 | `invalid_request`, `method_not_allowed` | false |
| 409 | `command_conflict`, `command_conflict` | false |
| 422 | `invalid_request`, `invalid_request` | false |
| 500 | `unavailable`, `internal_error` | false |
| 503 | `unavailable`, `storage_unavailable` | true |

The boundary catches unexpected application exceptions before Starlette can rethrow them to server error logging. Before response headers start, it emits a fixed envelope. Its only caught-error log is `api_request_failed` with a newly constructed synthetic exception containing no original traceback, exception chain, input, credential or SQL. Once headers have started, a replacement envelope is impossible. The boundary raises only the fixed `api_response_interrupted` diagnostic with the original exception chain suppressed so the server terminates the interrupted transport. Configure external server and proxy logging separately if used. Every connection and transaction stays inside one synchronous handler invocation or one threadpool polling callback. Commit and connection closure precede cookie, response or stream-frame delivery.

## Expected request and response examples

These literal examples describe the implemented contract. They are not executed HTTP evidence. Resource IDs and the cookie marker are illustrative. The actual session credential is server-generated and delivered only through Set-Cookie.

```http
POST /api/session HTTP/1.1
Host: localhost:8000
Origin: http://localhost:5173
```

```http
HTTP/1.1 204 No Content
Set-Cookie: gg_session=<server-generated-credential>; HttpOnly; Path=/; SameSite=lax
Cache-Control: no-store
```

```http
POST /api/runs HTTP/1.1
Host: localhost:8000
Origin: http://localhost:5173
Cookie: gg_session=<server-generated-credential>
Content-Type: application/json

{"command_id":"controlled-start-1","request":{"origin":{"kind":"current","location":{"coordinates":{"latitude":14.6,"longitude":121.0},"timezone":"Asia/Manila","provider_refs":[]}},"travel":{"mode":"walk","minutes":15},"eat_now_at":"2026-10-26T12:00:00+08:00"}}
```

```http
HTTP/1.1 202 Accepted
Content-Type: application/json
Cache-Control: no-store

{"command":{"disposition":"accepted","command_id":"controlled-start-1","run":{"search_id":"example-search","run_id":"example-run","operation":"initial","attempt_id":null,"state":{"status":"queued"},"last_event_sequence":1}}}
```

An identical command returns 202 with `disposition: "replayed"`, the same identities, and the current persisted state. Conflicting reuse returns 409 with `{"error":{"code":"command_conflict","message":"command_conflict","retryable":false}}`.

```http
GET /api/searches/example-search/runs/example-run HTTP/1.1
Host: localhost:8000
Cookie: gg_session=<server-generated-credential>
```

```http
HTTP/1.1 200 OK
Content-Type: application/json
Cache-Control: no-store

{"run":{"search_id":"example-search","run_id":"example-run","operation":"initial","attempt_id":null,"state":{"status":"queued"},"last_event_sequence":1},"recommendations":null,"display_version_id":null}
```

Missing or invalid credentials receive 401 with `{"error":{"code":"invalid_request","message":"invalid_session","retryable":false}}`. A foreign search, missing run or mismatched pair receives 404 with `{"error":{"code":"not_found","message":"not_found","retryable":false}}`, including when one browser owns both searches. Invalid JSON or strict model input receives 422 with `{"error":{"code":"invalid_request","message":"invalid_request","retryable":false}}`. Unavailable or stale storage receives 503 with `{"error":{"code":"unavailable","message":"storage_unavailable","retryable":true}}`.

## Task-local API evidence

From the repository root, run `apps/backend/.venv/bin/python data/verification/GG-005.06/attempt-01/sanity.py`. This pure checker covers settings, strict JSON admission, fixed envelopes, four route declarations, cookie security metadata, retained graph references, collision rejection and Origin guard logic. Database opening is prohibited during the check. No HTTP or ASGI requests, SQL, migrations, worker, provider or model calls run. GG-007 must execute actual admission, rollback, readiness, cookie, preflight, access, invalid-input and restart scenarios. GG-005.07 adds [live SSE](RUN_EVENTS.md) with its own pure checker. GG-005.08 adds [explicit controlled worker execution](CONTROLLED_WORKER.md). Production research and later user-command routes remain later tasks.
