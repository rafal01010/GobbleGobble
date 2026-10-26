# Set up the backend environment

This procedure installs the Python dependencies for GG-002.01 and prepares local settings for GG-002.02. GG-002.03 adds the [bounded call layer](CALLS.md) for future adapters. GG-002.04 and GG-002.05 add prepared geographic clients. GG-002.06 adds the [web-research client](WEB_RESEARCH_PROBES.md). GG-002.07 adds the [SDK owner and research probe](SDK_PROBE.md). GG-002.08 connects the clients through the [bounded probe command](LIVE_PROBES.md). The API and worker are not implemented yet. Section A verification remains at GG-003.

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

## Run offline section checks

After initialization, run the Section A checks from the repository root:

```sh
scripts/local/verify-section-a.sh
```

The script uses the installed `apps/backend/.venv` interpreter and development tools. It does not install dependencies, synchronize the environment, or make live provider calls. It resolves the backend relative to the script, so you can also invoke it by absolute path from another directory.

The checks run the full offline test suite, Ruff lint and format checks, mypy over `app` and `tests`, and shell syntax validation for `probe-live.sh`. The script stops at the first failure and returns its nonzero exit status. Fix the reported failure and rerun the command. A zero exit status means every offline check passed. Live observations and the Section A decision belong to the [feasibility gate](FEASIBILITY_GATE.md).

## Dependency changes

Edit dependencies through uv from `apps/backend` and review both `pyproject.toml` and `uv.lock`. Keep the lockfile in version control. Routine setup uses the lock rather than selecting newer releases. [uv's locking and syncing documentation](https://docs.astral.sh/uv/concepts/projects/sync/) describes locked setup and deliberate upgrades.

The runtime dependencies cover HTTP requests, the OpenAI client and Agents SDK, typed data and settings, and the FastAPI server. The development group contains pytest, pytest-asyncio, Ruff, and mypy. The [official Agents SDK quickstart](https://developers.openai.com/api/docs/guides/agents/quickstart?lang=python) identifies `openai-agents` as the Python SDK package.

The `app` package contains local settings, the bounded call layer, the prepared [location/discovery](GEOGRAPHY_PROBES.md) and [walking/driving route](ROUTE_PROBES.md) client, the [OpenAI web-research client](WEB_RESEARCH_PROBES.md), the [anonymous Google review client](GOOGLE_REVIEW_PROBES.md), and the [SDK owner/research probe](SDK_PROBE.md). None runs a provider during import or environment setup. The geographic and review clients use the installed HTTPX dependency. Earlier bounded experiments demonstrated Geoapify location/discovery, Sol-based OpenAI access and anonymous Google review retrieval. Full provider selection, route access and current Luna-model verification remain GG-003. Models, credentials and source controls remain governed by the [integration findings](INTEGRATIONS.md) and later tasks. Installing the SDK does not configure its tracing or authorize live research. GG-005.01 uses Python's standard-library `sqlite3` for the [local database foundation](DATABASE.md), with no additional dependency.

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

## Prepare anonymous review configuration

GG-002.06A adds two independent settings to the example inventory. Set `GG_REVIEW_PROVIDER=google_reviews` and a finite positive `GG_REVIEW_TIMEOUT_SECONDS` when preparing the separately requested GG-003 gate. Keep the geography and model settings as configured. Reviews require no Google API key, billing, browser profile or cookies.

Use `load_review_settings` for review-only preflight without geography or model credentials. Use general `load_settings` when the attempt shares review, geography and model calls. Neither loader contacts a service. `GoogleReviewClient.preflight()` reports a fixed missing-selection or missing-timeout error before it can transmit. See the [review probe reference](GOOGLE_REVIEW_PROBES.md) for the interface, bounds and retention decisions.

GG-002.08 left the actual local `.env` unchanged. At GG-003, the missing review fields were explicitly set to `google_reviews` and 30 seconds. The GG-002.08 command accepts typed JSON input, with independent review-only scenarios and explicit live opt-in. After initialization, run `scripts/local/probe-live.sh --help` from the repository root. See the [command reference](LIVE_PROBES.md) for scenario inputs, configuration requirements, request bounds, and exit codes. GG-003 owns behavioral and live verification.

## Prepare local database storage

After initializing the backend, run the explicit migration command from the repository root:

```sh
scripts/local/migrate-db.sh
```

The command loads database settings without provider credentials or model configuration. `GG_DATABASE_PATH` overrides the default repository-local `data/runtime/gobblegobble.sqlite3`. Relative paths resolve against the repository root. See the [database reference](DATABASE.md) for transaction boundaries, migration history, prerequisites and exit behavior. Initialization and imports do not migrate storage automatically.

GG-005.01 implements this command. Empty-database migration, schema inspection and broader persistence verification remain at GG-007. Ownership, run admission, worker leases and event repositories are implemented. HTTP and persistence verification remain GG-007.


## Run the admission API

After explicit database migration, run this from `apps/backend`:

```sh
.venv/bin/uvicorn app.api.application:create_app_from_environment --factory --host 127.0.0.1 --port 8000
```

The factory requires only [API and database settings](SETTINGS.md#independent-api-settings). It does not load provider credentials, invoke research, migrate storage or start a worker. `GET /ready` requires an existing database with current migration history and returns `{"status":"ready"}` on success or a fixed 503 envelope on failure. Importing or constructing the app does not open storage.

The default frontend origins are `http://localhost:5173` and `http://127.0.0.1:5173`. Use a matching Origin for session bootstrap and start. Complete `POST /api/session` before concurrent authenticated work, then retain its cookie. See [HTTP operations and examples](API.md#implemented-http-operations). Readiness, session, admission, status and [live SSE](RUN_EVENTS.md) routes are implemented. HTTP, cookies, ownership, rollback, replay/follow and database behavior await GG-007 verification. The [controlled worker](CONTROLLED_WORKER.md) can explicitly target an admitted run. Real research integration and lifecycle scripts remain later tasks.

## Run a controlled worker

From `apps/backend`, inspect `.venv/bin/python -m app.worker diagnostic --help`. After explicit migration and HTTP admission, run `.venv/bin/python -m app.worker diagnostic --run-id RUN_ID` with the returned run ID. The command loads only database settings, advances one deliberately selected diagnostic run and performs no research calls. Its [command contract](CONTROLLED_WORKER.md) records flags, exit behavior, state sequence and GG-007 verification. This is implemented infrastructure, not a verified worker run or production research integration.

The separate [frontend setup guide](FRONTEND_SETUP.md) records the SvelteKit package and frontend-only commands. Its current static page needs no backend process or provider credentials. Full-stack lifecycle commands remain GG-006.06 and GG-006.07 work.
