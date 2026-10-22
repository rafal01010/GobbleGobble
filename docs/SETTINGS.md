# Backend settings reference

GG-002.02 adds local settings and credential preflight in `apps/backend/app/core/settings.py`. The supported entry point is `load_settings`. It parses configuration without contacting a provider. The [backend setup guide](BACKEND_SETUP.md#configure-local-settings) describes local setup.

## Sources and precedence

The default file is `apps/backend/.env`, resolved relative to the settings module rather than the working directory. Process environment values override file values. There are no default provider selections, model IDs, or timeout values. The initializer does not load settings.

`load_settings(mode=..., env_file=...)` accepts a different dotenv path. A relative override is relative to the caller's working directory. `env_file=None` disables file loading but still reads the process environment. An absent file is allowed when the environment supplies the required configuration. Unknown dotenv fields and invalid or unreadable files produce configuration errors. Unrelated process environment variables do not become settings.

This uses the installed [Pydantic settings source behavior](https://docs.pydantic.dev/latest/concepts/pydantic_settings/). The settings boundary converts validation failures into value-free errors.

## Variables

All names use the exact spelling below. `apps/backend/.env.example` contains the same inventory with commented, empty assignments. Copying the example selects no provider and supplies no credentials.

| Variable | Accepted input | Requirement |
|---|---|---|
| `GG_LOCATION_PROVIDER` | `google_grounding_lite`, `google_geocoding`, or `geoapify` | Required in every mode. |
| `GG_DISCOVERY_PROVIDER` | `google_grounding_lite`, `google_places`, or `geoapify` | Required in every mode. |
| `GG_ROUTE_PROVIDER` | `google_grounding_lite`, `google_routes`, or `geoapify` | Required in every mode. |
| `GG_WEB_PROVIDER` | `openai` | Required in every mode. |
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

`safe_diagnostics(settings)` returns an explicit dataclass projection. It includes the resolved mode, provider identifiers, numeric timeouts, model-presence flags, and credential-presence flags. It omits model text and credential values. Generic settings dumps are not the diagnostic interface.

For example, a required but absent OpenAI key produces `OPENAI_API_KEY: missing`. An invalid dotenv file produces `configuration: invalid`. These examples describe the error contract, not the current local configuration.

This redacted diagnostic example replaces configured identifiers and numbers with descriptions. Presence flags disclose no credential value:

```json
{
	"mode": "diagnostic",
	"location_provider": "<selected location provider>",
	"discovery_provider": "<selected discovery provider>",
	"route_provider": "<selected route provider>",
	"web_provider": "openai",
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

Configured timeouts are inputs for future adapters. This task does not enforce request deadlines or call budgets. GG-002.03 adds bounded call receipts. GG-002.04 through GG-002.08 add provider clients, SDK wiring, and the probe command. In particular, loading settings does not disable SDK tracing or implement source-use enforcement.

Local readiness proves neither key validity nor billing, enabled APIs, quota, model availability, route coverage, or source permission. Google choices remain conditional under the [integration findings](INTEGRATIONS.md). GG-003 retains the missing-key, environment-override, effective-settings, and bounded live verification cases. Model access, operational timeout choices, costs, and timings remain unverified until that gate.
