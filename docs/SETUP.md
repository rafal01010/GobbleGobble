# Run GobbleGobble locally

The frontend currently shows a visual preview. The backend provides admission, saved progress, event streaming and a diagnostic worker. The normal restaurant-search journey is not connected yet. You can run the preview alone; exercising the diagnostic lifecycle also needs the API and database.

## Prerequisites

- For the frontend, use Node 24 LTS or Node 22.17 or newer within the Node 22 release line. Node 23 is unsupported. The manifest records npm 10.9.2 and the committed lockfile fixes dependency versions.
- For the backend, install [uv](https://docs.astral.sh/uv/) and use a POSIX shell. The project selects Python 3.14.7 through `apps/backend/.python-version`. Initial setup needs network access unless the interpreter and packages are cached.

The preview, API, database and diagnostic worker require no research-provider credentials. Keep secrets in the Python backend, never in frontend code or a committed environment file.

## Open the frontend preview

From the repository root:

```sh
cd apps/frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173/`. The server binds to loopback and fails if that port is occupied. Installation runs `svelte-kit sync`; synchronization errors are not suppressed. No frontend environment file or backend process is needed for this static shell.

After installation, `npm run check` synchronizes framework files and runs svelte-check. `npm run build` builds with the Node adapter. `npm run preview` serves an existing successful build on `127.0.0.1:4173` and fails if that port is occupied. These commands return nonzero on failure. A scoped visual preview has run; broad type-check, production-build and full-stack verification remain incomplete.

## Initialize backend storage

From the repository root:

```sh
scripts/local/init-backend.sh
scripts/local/migrate-db.sh
```

Initialization runs `uv sync --locked` in `apps/backend` and creates `.venv`. It starts no services. Use `scripts/local/init-backend.sh --offline` only when the interpreter and dependencies are cached. Optional arguments pass to `uv sync`; `UV_CACHE_DIR` selects a writable cache.

Migration explicitly creates or upgrades the default `data/runtime/gobblegobble.sqlite3`. Set `GG_DATABASE_PATH` to select another durable file; relative paths resolve against the repository root. Migration prints `database_migrations_applied` with the newly applied versions, or `database_schema_current` when no changes are needed. Exit 0 means success, 1 means a configuration/storage/history failure, and 2 means invalid arguments. Fixed error tokens omit paths, credentials and SQL. See [migration guarantees](DATABASE.md#migration-history).

Run other backend commands from `apps/backend`, or select the project explicitly with `uv run --project apps/backend --locked ...`. Keep dependency changes in both `pyproject.toml` and `uv.lock`.

## Start the API

After migration, run from `apps/backend`:

```sh
.venv/bin/uvicorn app.api.application:create_app_from_environment --factory --host 127.0.0.1 --port 8000
```

`GET /ready` returns `{"status":"ready"}` when existing migration history matches the application. The server does not migrate storage, start a worker or check external services. Its default trusted frontend origins are `http://localhost:5173` and `http://127.0.0.1:5173`.

Use the [API request examples](API.md#expected-request-and-response-examples) to bootstrap an anonymous session and admit a run. Retain the issued cookie. Mutations require exactly one allowed `Origin` header. The current frontend does not consume these endpoints.

## Run a diagnostic job

After admission, replace `RUN_ID` with the returned run ID and run from `apps/backend`:

```sh
.venv/bin/python -m app.worker diagnostic --run-id RUN_ID
```

This intentionally advances only that run, without place research or model calls. The new-run sequence is queued, starting, composing, publishing, then completed with an insufficient-coverage outcome saying that no place research was performed. Existing history continues across an expired-lease reclaim; the worker does not reset event positions.

The optional `--step-delay SECONDS` accepts finite values from 0 through 5 and defaults to one second before each of two checkpoints. The CLI generates a worker ID and uses a 30-second lease. It does not scan a queue, admit a new run or migrate storage. `--help` and `diagnostic --help` need no settings or database.

`diagnostic_worker_starting` appears before a claim is attempted. Only `diagnostic_worker_completed` means terminal completion committed. A repeated completed target is rejected rather than restarted.

| Exit | Meaning and fixed error token |
|---|---|
| 0 | Help or committed diagnostic completion. |
| 1 | Configuration, schema, storage, internal or job failure. Tokens are `diagnostic_configuration_error`, `diagnostic_schema_error`, `diagnostic_storage_error`, `diagnostic_internal_error` or `diagnostic_worker_failed`. |
| 2 | Invalid arguments, `diagnostic_argument_error`. |
| 3 | Unknown target, `diagnostic_run_not_found`. |
| 4 | Live competing claim, `diagnostic_lease_busy`. |
| 5 | Terminal or unclaimable target, `diagnostic_run_not_claimable`. |
| 6 | Lost or expired claim, `diagnostic_lease_lost`. |
| 130 | Keyboard interruption, `diagnostic_interrupted`. |

Subscribe through the [event endpoint](API.md) before starting the worker to observe committed progress. The API/database/worker flow still needs integrated runtime verification. Diagnostic completion is not evidence that restaurant research works.

## Configure research and run checks

For deliberate provider probes, copy `apps/backend/.env.example` to `apps/backend/.env` and fill the inputs required by the scenario. The example selects no provider or model and supplies no credentials. [Settings](SETTINGS.md) explains precedence and safe local preflight. A successful preflight does not validate keys, billing or service access. Follow [bounded probe commands](LIVE_PROBES.md) before making a live call, and review [source policy](INTEGRATIONS.md).

After backend initialization, the repository-root command `scripts/local/verify-section-a.sh` runs offline pytest, Ruff, mypy and probe-wrapper shell-syntax checks. Despite its historical name, it is a component-check command. It installs nothing, calls no external service and stops at the first failure. A pass does not prove the complete application journey.

Stop foreground frontend/API processes with Ctrl+C. Stop only a process you started; never terminate other projects' Python or Node processes by name. A diagnostic worker exits after its selected job. Full-app start/stop/restart scripts, production research, cancellation and recommendation publication are not implemented yet.
