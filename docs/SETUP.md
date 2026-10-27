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

After installation, `npm run check` synchronizes framework files and runs svelte-check. `npm run build` builds with the Node adapter. `npm run preview` serves an existing successful build on `127.0.0.1:4173` and fails if that port is occupied. These commands return nonzero on failure. The check, build and controlled local-stack walkthrough have passed on Node 24.

## Initialize backend storage

From the repository root:

```sh
scripts/local/init-backend.sh
scripts/local/migrate-db.sh
```

Initialization runs `uv sync --locked` in `apps/backend` and creates `.venv`. It starts no services. Use `scripts/local/init-backend.sh --offline` only when the interpreter and dependencies are cached. Optional arguments pass to `uv sync`; `UV_CACHE_DIR` selects a writable cache.

Migration explicitly creates or upgrades the default `data/runtime/gobblegobble.sqlite3`. Set `GG_DATABASE_PATH` to select another durable file; relative paths resolve against the repository root. Migration prints `database_migrations_applied` with the newly applied versions, or `database_schema_current` when no changes are needed. Exit 0 means success, 1 means a configuration/storage/history failure, and 2 means invalid arguments. Fixed error tokens omit paths, credentials and SQL. See [migration guarantees](DATABASE.md#migration-history).

Run other backend commands from `apps/backend`, or select the project explicitly with `uv run --project apps/backend --locked ...`. Keep dependency changes in both `pyproject.toml` and `uv.lock`. To regenerate frontend API types after changing the backend contract, follow [the API generation instructions](API.md#generate-frontend-types).

## Start the API

After migration, run from `apps/backend`:

```sh
.venv/bin/uvicorn app.api.application:create_app_from_environment --factory --host 127.0.0.1 --port 8000
```

`GET /ready` returns `{"status":"ready"}` when existing migration history matches the application. The server does not migrate storage, start a worker or check external services. Its default trusted frontend origins are `http://localhost:5173` and `http://127.0.0.1:5173`.

Use the [API request examples](API.md#expected-request-and-response-examples) to bootstrap an anonymous session and admit a run. Retain the issued cookie. Mutations require exactly one allowed `Origin` header. The visual preview does not yet use the frontend API client.

## Start the local foundation

Install the frontend dependencies, initialize the backend, and migrate storage as described above. Use a supported Node version on `PATH` and install `lsof`. From the repository root, run:

```sh
scripts/local/backend-process.py app start
scripts/local/backend-process.py app status
scripts/local/backend-process.py app stop
scripts/local/backend-process.py app restart
```

Start waits for the API at `127.0.0.1:8000` and the frontend at `127.0.0.1:5173`. It checks that the supervised process owns each listening port. Open `http://127.0.0.1:5173/diagnostic` for the controlled run observer. The normal restaurant-search journey is not connected.

The command starts no diagnostic worker automatically. After admitting a run, start its worker as described below. Stop and restart interrupt supervised diagnostic workers; restart does not start them again. A failed start stops only processes started by that invocation. `app status` reports process liveness, not readiness.

## Manage backend processes

After initialization and migration, run these commands from the repository root:

```sh
scripts/local/backend-process.py api start
scripts/local/backend-process.py api status
scripts/local/backend-process.py api stop
scripts/local/backend-process.py worker start RUN_ID
scripts/local/backend-process.py worker status RUN_ID
scripts/local/backend-process.py worker stop RUN_ID
```

Replace `RUN_ID` with the 32-character lowercase hexadecimal ID of an admitted run. The worker handles that run only. Stopping it interrupts the worker; it does not cancel the run. Starting an already supervised process reports the existing child. Stopping an absent process succeeds. The supervisor retains its own child handle and does not stop processes by name.

A successful individual service start or status reports process liveness, not readiness. Check `http://127.0.0.1:8000/ready` separately. The combined `app start` also waits for HTTP readiness and checks listener ownership. Logs and ownership files live under `data/runtime/processes/`. If ownership files are stale or unreachable, the commands refuse to guess which process to stop. Inspect the files and processes before recovery. A supervisor crash can leave its child running. Unix socket path limits can prevent startup in a deeply nested checkout.

## Observe a diagnostic run

Start the frontend and API after initializing storage. Open `http://127.0.0.1:5173/diagnostic` and select **Create diagnostic run**. The page uses a fixed test location and does not research places. It shows the run ID and the worker command. Run that command in a separate terminal to advance the job.

The page saves the run identity in the current browser tab. Refresh to read the snapshot and replay progress. If admission returns an uncertain result, select **Retry saved request** to reuse the same command. **Forget this run** clears the tab's saved identity; it does not delete or cancel the run.

If you replace the database, its anonymous sessions no longer match existing browser cookies. Remove the `gg_session` cookie for the local hostname in your browser's site-data controls, then retry the saved request. Clear the tab's session storage only when you also want to forget its saved run. A database replacement does not preserve access to runs in the old database.

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

Subscribe through the [event endpoint](API.md) before starting the worker to observe committed progress. Diagnostic completion is not evidence that restaurant research works.

## Verify the local foundation

Initialize the backend and install both frontend dependency sets with `npm ci` and `npm ci --prefix tools/contract` from `apps/frontend`. Select a supported Node runtime on `PATH`. From the repository root, run:

```sh
scripts/local/verify-foundation.sh
```

The default `--offline` mode runs backend tests, lint and type checks, contract examples, documentation-checker tests, API generation, frontend checks and the production build. It installs nothing and starts no server. It makes no provider or model calls. A successful exit establishes component checks, not the browser walkthrough.

To exercise the actual loopback stack, install `lsof` and stop existing local foundation processes. Choose a new output directory under ignored `data/verification/`, then run:

```sh
scripts/local/verify-foundation.sh --stack data/verification/foundation/attempt-01
```

The stack check refuses an existing output directory, occupied ports or supervisor ownership files. It migrates a fresh database in that directory, starts the actual local scripts and uses controlled diagnostic jobs. It checks admission, duplicate/conflicting commands, access isolation, live events, replay, saved terminal state, duplicate startup, failed-start cleanup, restart and recovery after real lease expiry. Recovery adds about 30 seconds. It checks admission below one second and worker-event delivery below two seconds on the idle local host. Delivery timing uses the stored event timestamp through client receipt, a conservative upper bound on delay after commit. The paused-reader case checks continued worker progress separately. These limits are local acceptance targets, not capacity guarantees.

The command stops its supervised processes and writes `report.json` with command results, timings, process identities, received events and persisted state. It does not access the normal runtime database. A cleanup failure is recorded and requires inspection before rerunning. Exit 0 means the selected checks passed. Exit 1 means a check or operation failed. Invalid options or preconditions exit 2. `--help` prints usage without starting services.

For visual verification, start the foundation and open both the main page and `/diagnostic`. Check the light shell at desktop and phone width, create a diagnostic run, start its worker, observe new progress, then refresh and use **Reconnect and replay**. The normal dining journey remains incomplete.

## Configure research and run checks

For deliberate provider probes, copy `apps/backend/.env.example` to `apps/backend/.env` and fill the inputs required by the scenario. The example selects no provider or model and supplies no credentials. [Settings](SETTINGS.md) explains precedence and safe local preflight. A successful preflight does not validate keys, billing or service access. Follow [bounded probe commands](LIVE_PROBES.md) before making a live call, and review [source policy](INTEGRATIONS.md).

After backend initialization, the repository-root command `scripts/local/verify-section-a.sh` runs offline pytest, Ruff, mypy and probe-wrapper shell-syntax checks. Despite its historical name, it is a component-check command. It installs nothing, calls no external service and stops at the first failure. A pass does not prove the complete application journey.

Stop foreground frontend or API processes with Ctrl+C. Use the process commands above for supervised processes. A diagnostic worker exits after its selected job. Production research, cancellation and recommendation publication are not implemented yet.
