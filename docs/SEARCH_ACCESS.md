# Anonymous search access

GG-005.02 implements durable anonymous session identity and search authorization in [ownership.py](../apps/backend/app/db/ownership.py). It is implemented, awaiting Section B verification at GG-007. GG-005.03 adds [queued admission and command replay](RUN_ADMISSION.md) in `app/db/runs.py`. HTTP transport and public run-read or watch operations remain later work.

## Ownership model

The server issues an opaque browser credential containing 32 random bytes through `secrets.token_urlsafe(32)`. The database stores only a domain-prefixed SHA-256 digest. A separate server-generated session ID owns searches. Neither a search ID nor a run ID is a credential. Request payloads in the [API contract](API.md) remain unchanged and contain no owner fields.

Migration 2, `create_anonymous_search_ownership`, appends these objects to the [SQLite migration manifest](DATABASE.md#migration-history):

| Object | Stored fields and constraints |
|---|---|
| `anonymous_sessions` | `session_id` is the primary key. `credential_digest` is unique, non-null text with length 64. |
| `search_ownership` | `search_id` is the primary key. `owner_session_id` references `anonymous_sessions.session_id`. Both fields are non-null. |
| `search_ownership_owner_idx` | Index on `search_ownership.owner_session_id`. |

These tables contain no plaintext credentials, locations, requests, runs, or research. A dedicated session record allows identity to exist before the first search and independently of search retention. Migration 1 remains unchanged.

## Helper contracts

All helpers accept the existing caller-owned synchronous SQLite connection. Writes require an active transaction and do not commit it. No helper loads settings, opens storage, performs network work, or logs values. Read cursors close before returning.

| Helper | Result and failure behavior |
|---|---|
| `issue_session(connection)` | Returns `IssuedSession` with a new credential and internal `SessionPrincipal`. Inserts its session record. |
| `resolve_session(connection, credential)` | Validates the supplied value and resolves its digest to `SessionPrincipal`. Malformed and unrecognized values raise `InvalidSession` with `invalid_session`. |
| `create_search(connection, principal)` | Returns `OwnedSearch` with a server-generated search ID bound to the existing internal principal. An unknown principal raises `InvalidSession`. |
| `require_search_access(connection, credential, search_id)` | Resolves the credential and checks the search and owner together. Returns `OwnedSearch`. Both foreign and nonexistent searches raise `SearchNotFound` with `not_found`. Invalid credentials raise `InvalidSession`. |

Credential parsing accepts exactly 43 ASCII URL-safe characters. Only a matching stored digest authenticates a value. A well-formed caller-selected token does not create a session. There is no fallback issuance after an authentication failure.

`SessionPrincipal`, `IssuedSession`, and `OwnedSearch` are frozen internal dataclasses with their fields excluded from representations. They are trusted application values, not request models. Python code can still explicitly inspect or serialize them. Do not serialize them into public payloads or diagnostics. The raw credential is exposed only for deliberate delivery to the browser after commit.

SQLite and programming errors remain operational errors. They do not become not-found responses. Future adapters must sanitize those errors rather than log raw objects or submitted values.

## Service integration examples

These examples describe future service call sites. They do not establish callable endpoints. Use a migrated database from [backend setup](BACKEND_SETUP.md).

For a browser with no credential, issue an identity in a short transaction. Deliver the credential only after successful commit.

```python
from app.db.ownership import issue_session
from app.db.session import session, transaction

with session(settings) as connection, transaction(connection):
	issued = issue_session(connection)
```

For a subsequent start command, use admission to resolve the credential and atomically create the search, run, and owner-scoped command record. An identical retry reuses the existing identities. Deliver the response after commit, and let failures escape the transaction block.

```python
from app.db.runs import admit_start

with session(settings) as connection, transaction(connection):
	response = admit_start(connection, browser_credential, validated_start_command)
```

For access to a search, require both its identity and the browser credential.

```python
from app.db.ownership import require_search_access

with session(settings) as connection:
	search = require_search_access(connection, browser_credential, requested_search_id)
```

The owning browser receives the authorized search identity. A different valid browser credential and an unknown search ID produce the same resource rejection. Missing, malformed, or unrecognized credentials fail before resource lookup. Resource IDs alone never authorize access.

For a future run read, mutation, replay, or watch, first authorize the search. Then select the run with both `search_id = search.search_id` and the requested `run_id`. Authorizing one search must never permit loading a run by run ID alone. The same rule applies when the browser owns both searches. Admission replay already checks the authenticated command owner, search owner, and search/run membership. Migration 3 also enforces these command relationships with composite foreign keys. Public read, mutation, and watch membership enforcement remains later work, with owned, unknown, and foreign-run verification at GG-007.

## Browser transport handoff

GG-005.06 and GG-006.04 own HTTP and browser integration. The intended transport is a host-only HttpOnly cookie with `Path=/` and `SameSite=Lax`, plus `Secure` under HTTPS. Local HTTP development cannot require an HTTPS-only cookie. Mutating browser routes, including session issuance, must require an `Origin` that exactly matches a configured trusted frontend origin. Missing or untrusted origins must fail before a cookie-authenticated write. CORS must allow credentials only for those explicit origins, never a wildcard. Read-only requests must not issue identities or mutate state. The actual cookie name, lifetime, and local origin configuration remain adapter details. No cookie or HTTP origin enforcement is currently implemented.

Only an absent credential permits explicit issuance. A supplied invalid credential must fail, rather than silently become a new owner. Concurrent initial browser requests need one bootstrap operation before authenticated work. Losing the credential loses access through this interface. Expiry, rotation, revocation, cleanup, and account recovery are not implemented.

Keep credentials and sensitive location inputs out of query strings and URLs. Later routes can use opaque resource IDs in paths. Keep location payloads in request bodies and reuse the existing safe diagnostics projections. Ownership helpers accept no location information and emit no logs.

## Evidence and verification boundary

Task evidence belongs in `data/verification/GG-005.02/attempt-01/`. The implementation record in [the plan](../IMPLEMENTATION_PLAN.md#gg-00502-add-anonymous-search-ownership) names actual local checks. GG-007 must execute migration 2 against empty and version-1 databases, inspect the ownership constraints, check restart resolution and rollback, and exercise owned, unknown, and foreign run access through the completed API. Implementation completion does not establish that these database or HTTP behaviors have passed.

The task-local sanity checker runs from the repository root after backend environment setup. It needs no arguments, credentials, or database. It writes source identities into its attempt directory and exits nonzero if an assertion fails.

```sh
apps/backend/.venv/bin/python data/verification/GG-005.02/attempt-01/sanity.py
```

The checker covers import side effects, malformed credentials, transaction guards, representations, migration-1 preservation, unchanged foundation files, and local document targets. It does not execute SQL. Ruff checks and source review supply the remaining task-local evidence.
