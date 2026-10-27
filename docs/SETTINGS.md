# Backend settings reference

`apps/backend/app/core/settings.py` parses local configuration and exposes explicit credential preflight without contacting a provider. General research configuration uses `load_settings`. Independent API, database and review-only loaders avoid requiring unrelated credentials. See [backend setup](SETUP.md#configure-research-and-run-checks) for local configuration.

## Sources and precedence

The default file is `apps/backend/.env`, resolved relative to the settings module rather than the working directory. Process environment values override file values. There are no default provider selections, model IDs, or timeout values. `load_database_settings` has a default durable path. Optional review fields also have a credential-free `load_review_settings` entry point. The initializer does not load settings.

`load_settings(mode=..., env_file=...)` accepts a different dotenv path. A relative override is relative to the caller's working directory. `env_file=None` disables file loading but still reads the process environment. An absent file is allowed when the environment supplies the required configuration. Unknown dotenv fields and invalid or unreadable files produce configuration errors. Unrelated process environment variables do not become settings.

This uses the installed [Pydantic settings source behavior](https://docs.pydantic.dev/latest/concepts/pydantic_settings/). The settings boundary converts validation failures into value-free errors.

## Variables

The table describes general `load_settings` requirements unless a specialized rule is stated. Review-only and database-only loading are described below. All names use the exact spelling below. `apps/backend/.env.example` contains the same inventory with commented, empty assignments. Copying the example selects no provider and supplies no credentials.

| Variable | Accepted input | Requirement |
|---|---|---|
| `GG_TRUSTED_FRONTEND_ORIGINS` | JSON array of unique exact HTTP(S) origins. No wildcard, credentials, path, query, fragment, trailing slash, default port or blank list. | API-only. Defaults to `["http://localhost:5173","http://127.0.0.1:5173"]`. |
| `GG_SESSION_COOKIE_SECURE` | Boolean | API-only. Defaults to false. HTTPS always sets Secure; true also forces it on HTTP. |
| `GG_DATABASE_PATH` | Local filesystem path without colon syntax or NUL characters. Blank values, `:memory:`, and URIs are rejected. | Optional for database-only loading. Defaults to `<repository>/data/runtime/gobblegobble.sqlite3`. General and review settings ignore this field. |
| `GG_LOCATION_PROVIDER` | `google_grounding_lite`, `google_geocoding`, or `geoapify` | Required in every mode. |
| `GG_DISCOVERY_PROVIDER` | `google_grounding_lite`, `google_places`, or `geoapify` | Required in every mode. |
| `GG_ROUTE_PROVIDER` | `google_grounding_lite`, `google_routes`, or `geoapify` | Required in every mode. |
| `GG_WEB_PROVIDER` | `openai` | Required in every mode for general settings. |
| `GG_REVIEW_PROVIDER` | `google_reviews` | Optional in general settings. Required by review-client preflight. |
| `GG_REVIEW_TIMEOUT_SECONDS` | Finite number greater than zero, in seconds | Optional in general settings. Required by review-client preflight. |
| `GG_OWNER_MODEL` | Nonblank model ID, with surrounding whitespace removed | Required in every mode. No model is selected by code. |
| `GG_RESEARCH_MODEL` | Nonblank model ID, with surrounding whitespace removed | Required in every mode. No model is selected by code. |
| `GG_GEOGRAPHIC_TIMEOUT_SECONDS` | Finite number greater than zero, in seconds | Required in every mode. Intended for geographic requests. |
| `GG_MODEL_TIMEOUT_SECONDS` | Finite number greater than zero, in seconds | Required in every mode. Intended for model requests. |
| `OPENAI_API_KEY` | Nonblank secret | Required in live mode. |
| `GOOGLE_MAPS_API_KEY` | Nonblank secret | Required in live mode when any geographic selection uses a Google provider. |
| `GEOAPIFY_API_KEY` | Nonblank secret | Required in live mode when any geographic selection uses Geoapify. |

The unselected geographic key is optional. A mixed provider configuration requires both geographic keys. Empty or whitespace-only credentials count as missing. Nonblank credentials retain their original characters. Provider credentials use [Pydantic `SecretStr`](https://docs.pydantic.dev/latest/api/types/#pydantic.types.SecretStr) and do not appear in the settings representation.

## Explicit modes

The caller must pass `mode="live"`, `mode="diagnostic"`, or `mode="test"`. There is no supported mode environment variable. The loader supplies the mode directly, so local configuration cannot change a live call into another mode.

| Mode | Local checks | Meaning |
|---|---|---|
| `live` | Validates all selections, model IDs, timeouts, and selected credentials | Establishes local configuration readiness only. |
| `diagnostic` | Validates selections, model IDs, and timeouts without requiring credentials | Supports explicit inspection through safe diagnostics. |
| `test` | Uses the same validation as diagnostic mode | Marks a caller's controlled test configuration. It supplies no fixture or client. |

Missing provider selections, model IDs, or timeouts raise `ConfigurationError` in every mode. Diagnostic mode reports those missing fields as issues rather than returning a partial settings object. No mode makes network requests. Future probes and clients remain responsible for obeying the resolved mode. Settings do not silently choose fixtures or another provider.

## Python interface and errors

`load_settings` returns a frozen typed `Settings` object. Future clients can read the exact configured model IDs and secret objects from it. Direct construction of `Settings` bypasses the loader's preflight contract and is not the supported application entry point.

`ConfigurationError.issues` contains recognized variable names and fixed issue codes. Its printable message omits submitted values, unknown key names, parser text, and chained exception output. File-level errors use the fixed `configuration` label. A failed live preflight raises an error rather than returning another mode.

`safe_diagnostics(settings)` returns an explicit dataclass projection. It includes the resolved mode, provider identifiers, numeric timeouts, model-presence flags, and credential-presence flags. It includes the optional review provider and timeout. `review_diagnostics(settings)` projects only mode, review provider and review timeout for review-only inspection. Both interfaces omit model text and credential values. Generic settings dumps are not the diagnostic interface.

For example, a required but absent OpenAI key produces `OPENAI_API_KEY: missing`. An invalid dotenv file produces `configuration: invalid`. These examples describe the error contract, not the current local configuration.

This redacted diagnostic example replaces configured identifiers and numbers with descriptions. Presence flags disclose no credential value:

```json
{
	"mode": "diagnostic",
	"location_provider": "<selected location provider>",
	"discovery_provider": "<selected discovery provider>",
	"route_provider": "<selected route provider>",
	"web_provider": "openai",
	"review_provider": null,
	"review_timeout_seconds": null,
	"owner_model_configured": true,
	"research_model_configured": true,
	"geographic_timeout_seconds": "<configured positive seconds>",
	"model_timeout_seconds": "<configured positive seconds>",
	"openai_key_present": false,
	"google_maps_key_present": false,
	"geoapify_key_present": false
}
```

These are API usage examples, not evidence that the local environment is configured:

```python
from dataclasses import asdict
from app.core.settings import ConfigurationError, load_settings, safe_diagnostics

try:
	settings = load_settings(mode="live")
except ConfigurationError as error:
	print(str(error))
else:
	print(asdict(safe_diagnostics(settings)))
```

```python
from app.core.settings import load_settings

settings = load_settings(mode="test", env_file=None)
```

The second example still requires selections, model IDs, and timeouts in the process environment. It does not read a local dotenv file or create a test client.

## Completion boundary

The [bounded call layer](LIVE_PROBES.md#bounds-and-accounting) combines configured timeouts with caller-supplied request allowances and a total deadline. Loading settings invokes no calls and does not configure SDK tracing.

Local readiness proves neither key validity nor billing, enabled APIs, quota, model availability, route coverage or source permission. Validate the selected configuration through an explicitly bounded live probe when needed. The anonymous Google review path needs no Google credential or billing; [integration findings](INTEGRATIONS.md) describe conditional API alternatives.

## Independent anonymous reviews

`load_review_settings(mode=..., env_file=...)` returns `ReviewSettings`. The loader uses the same dotenv path, environment precedence, fixed errors and explicit modes. It reads review settings without requiring geography, web, model or credential configuration. Known non-review dotenv fields are ignored for this entry point, while unknown names or a malformed file still fail. Review-provider selection and timeout remain optional during loading so diagnostic inspection can report an unconfigured source. `GoogleReviewClient.preflight()` requires both before transmission.

Anonymous reviews need no Google key, account, billing or cookies. Selecting `google_reviews` does not add a geographic credential requirement. General `load_settings` keeps its existing geography and OpenAI preflight. A review-only `AttemptCalls` accepts `ReviewSettings` and can execute only review calls. Shared attempts use the general settings plus the two review fields. The [review probe reference](LIVE_PROBES.md#adapter-behavior-worth-knowing) records the client contract and bounded probe scenarios.

Model IDs and provider selections come from the reader's configuration. Loading configuration invokes no network or model operation and does not select a fallback model.

## Independent local database settings

`load_database_settings(env_file=...)` returns a frozen `DatabaseSettings` with one `database_path: Path` field. It uses the same dotenv parsing, known-name admission, process precedence, and value-free `ConfigurationError` contract. It requires no mode, provider, model, timeout, or credential. Each loader projects admitted dotenv values to its own declared fields. Unknown dotenv names still fail for every loader.

`GG_DATABASE_PATH` defaults to `<repository>/data/runtime/gobblegobble.sqlite3`. Relative database paths resolve against the repository root. Absolute paths remain absolute. The loader resolves the path without creating a directory or file. Empty or whitespace-only values, colon syntax including memory and URI names, and NUL characters produce `GG_DATABASE_PATH: invalid`. Opening a directory fails at the database boundary.

The database loader ignores known provider fields even when they have invalid values. General and review-only loaders ignore the database field. Process environment overrides the dotenv database path. A configured database path does not change general live credential preflight.

The [database reference](DATABASE.md) defines connections, transactions, the migration command, and its exit codes. Database settings loading and imports perform no database or network operations.


## Independent API settings

`load_api_settings(env_file=...)` returns frozen `ApiSettings` with `trusted_frontend_origins` and `session_cookie_secure`. It shares the dotenv registry, process precedence and value-free `ConfigurationError` behavior. It requires no mode, provider, credential, model or timeout. Other loaders ignore these known API variables, while unknown dotenv names still fail. Loading API settings does not edit `.env`.

Origins use canonical lowercase HTTP(S) scheme and host, with an optional nondefault port. They must be unique concrete origins without user information, paths, query, fragment, wildcard, whitespace or trailing slash. Exact comparison does not normalize incoming headers. Example configuration is `GG_TRUSTED_FRONTEND_ORIGINS=["https://example.test"]`. Invalid fields report `GG_TRUSTED_FRONTEND_ORIGINS: invalid` or `GG_SESSION_COOKIE_SECURE: invalid` without their values.

`session_cookie_secure=false` permits local HTTP development. HTTPS requests still receive Secure cookies. Setting it to true forces Secure even for HTTP requests, where browsers may withhold the cookie. It does not enable TLS or configure trusted proxy headers. Cookie name, path and SameSite policy are fixed in [the API contract](API.md#implemented-http-operations).

`create_app` accepts explicit API and database settings without calling loaders. Schema export supplies inert model defaults without evaluating settings environment sources. Runtime startup uses `create_app_from_environment`, which calls only the API and database loaders. Neither startup path migrates or opens storage. Readiness performs the existing-schema check when requested. Integrated HTTP and database behavior still needs verification.
