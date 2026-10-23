# Set up the backend environment

This procedure installs the Python dependencies for GG-002.01 and prepares local settings for GG-002.02. GG-002.03 adds the [bounded call layer](CALLS.md) for future adapters. GG-002.04 and GG-002.05 add prepared geographic clients. GG-002.06 adds the [web-research client](WEB_RESEARCH_PROBES.md). The API, worker, and executable probe command are not implemented yet. Section A verification remains at GG-003.

## Prerequisites

Use a POSIX shell and install [uv](https://docs.astral.sh/uv/). The setup baseline is uv 0.12.5 and CPython 3.14.7. The backend's `.python-version` selects that Python release, and `pyproject.toml` limits the project to Python 3.14.

Allow access to the package index and distribution hosts on the first installation. If Python 3.14.7 is missing, uv can download it when automatic Python downloads are enabled. No provider account or API key is needed for dependency installation.

## Initialize the environment

From the repository root, run:

```sh
scripts/local/init-backend.sh
```

The script resolves `apps/backend` relative to its own location and runs `uv sync --locked` there. You can invoke it by absolute path from another directory. Optional arguments pass through to `uv sync`.

Successful setup exits with code 0 and creates `apps/backend/.venv` with the runtime and development dependencies in `apps/backend/uv.lock`. Repeating the command synchronizes the same environment. It does not start any processes or load application credentials.

The script uses uv's normal cache and Python installation locations. To use a different writable cache, set `UV_CACHE_DIR` for the command:

```sh
UV_CACHE_DIR=/private/tmp/gobblegobble-uv-cache scripts/local/init-backend.sh
```

If uv is missing, the shell reports that command as unavailable. Interpreter, download, dependency, and stale-lockfile errors retain uv's nonzero exit status. Fix the reported prerequisite and run the same command again. Setup does not silently regenerate a stale lockfile.

## Run backend commands

Run future backend commands from `apps/backend`, or select that project explicitly from the repository root:

```sh
uv run --project apps/backend --locked python --version
```

The expected interpreter version is Python 3.14.7. Do not use the repository root as an independent Python project.

To reuse an already populated cache without network access, pass `--offline` to the initializer. This fails if a required interpreter or artifact is unavailable locally.

```sh
scripts/local/init-backend.sh --offline
```

## Dependency changes

Edit dependencies through uv from `apps/backend` and review both `pyproject.toml` and `uv.lock`. Keep the lockfile in version control. Routine setup uses the lock rather than selecting newer releases. [uv's locking and syncing documentation](https://docs.astral.sh/uv/concepts/projects/sync/) describes locked setup and deliberate upgrades.

The runtime dependencies cover HTTP requests, the OpenAI client and Agents SDK, typed data and settings, and the planned FastAPI server. The development group contains pytest, pytest-asyncio, Ruff, and mypy. The [official Agents SDK quickstart](https://developers.openai.com/api/docs/guides/agents/quickstart?lang=python) identifies `openai-agents` as the Python SDK package.

The `app` package contains local settings, the bounded call layer, and the prepared [location/discovery](GEOGRAPHY_PROBES.md) and [walking/driving route](ROUTE_PROBES.md) client, plus the [OpenAI web-research client](WEB_RESEARCH_PROBES.md). None runs a provider during import or environment setup. Database libraries can be selected with the persistence implementation. The geographic client uses the installed HTTPX dependency. Provider selection and live access remain unverified. Models, credentials, and source permissions remain governed by the [integration findings](INTEGRATIONS.md) and later tasks. Installing the SDK does not configure its tracing or authorize live research.

## Configure local settings

From `apps/backend`, copy the configuration inventory:

```sh
cp .env.example .env
```

Edit `.env` using the [settings reference](SETTINGS.md#variables). Uncomment and fill the required provider selections, owner/research model IDs, and positive timeout values. For live preflight, add the OpenAI key and the keys for your selected geographic providers. Use credentials from your actual accounts. Leave unused keys absent. `.env` is ignored by Git.

The example contains no chosen providers, model IDs, timeout numbers, or credentials. Copying it alone is incomplete setup. Consult the [integration account requirements](INTEGRATIONS.md#credential-and-account-requirements) before choosing services. Google configuration does not resolve its source-permission conditions.

To inspect local readiness without contacting providers, run this from `apps/backend` after initialization:

```sh
uv run --locked python - <<'PY'
from dataclasses import asdict
import json
from app.core.settings import ConfigurationError, load_settings, safe_diagnostics

try:
	settings = load_settings(mode="live")
except ConfigurationError as error:
	print(str(error))
	raise SystemExit(1) from None
print(json.dumps(asdict(safe_diagnostics(settings)), indent=2))
PY
```

A successful command exits with code 0 and prints the explicit safe projection. It contains no credential values or model text. A configuration error prints recognized field names and fixed issue codes, then exits with code 1. Fix those fields and repeat the same command. This command checks local configuration only. It does not validate credentials against a service.

Diagnostic mode permits missing credentials but still requires provider selections, model IDs, and timeouts. Test mode marks controlled caller configuration and supplies no fixtures. Pass either mode explicitly in Python. The [settings reference](SETTINGS.md) records file precedence, overrides, errors, and the implementation boundary. Broad configuration and live checks remain at GG-003.
