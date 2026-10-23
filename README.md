# GobbleGobble

GobbleGobble is a planned local app for choosing where to eat now using real location and food research.

The [product and delivery contract](docs/PRODUCT.md) records the agreed journey, scope, and acceptance target. The backend Python environment is available. The API, worker, and website are not implemented yet.

Follow [the backend setup instructions](docs/BACKEND_SETUP.md) to install the locked dependencies with `scripts/local/init-backend.sh`. Setup requires uv and does not need provider credentials or make research calls.

The [settings reference](docs/SETTINGS.md) documents typed local configuration, explicit live/diagnostic/test modes, and credential preflight. `apps/backend/.env.example` lists the inputs without selecting providers or models. Preflight checks local readiness without contacting services.

The [call and receipt reference](docs/CALLS.md) describes shared request/time bounds, source-use admission, and metadata-only diagnostics. The call layer is implemented, awaiting verification at GG-003.

The [location and discovery probe reference](docs/GEOGRAPHY_PROBES.md) describes the prepared Geoapify client, request examples, provider IDs, attribution, and limitations. The [route probe reference](docs/ROUTE_PROBES.md) describes prepared walking and driving estimates and explicit unavailable/failure outcomes. The [web-research probe reference](docs/WEB_RESEARCH_PROBES.md) describes prepared OpenAI search requests, exact citation capture, and source-use limits. Live access remains unverified. SDK probes and the probe command remain future tasks.

The [integration comparison](docs/INTEGRATIONS.md) records proposed provider paths, source-use restrictions, and account requirements. Live feasibility and provider selection remain at GG-003.

The [paired quality-comparison protocol](docs/EVALUATION.md) defines matched inputs, scenario classes, observable criteria, and per-scenario acceptance against normal ChatGPT with web search. Actual baseline capture and live comparison remain future work at GG-023.

The [architecture and ownership reference](docs/ARCHITECTURE.md) maps the proposed modules, adaptive recommendation owner, bounded research tool-agent, durable worker, evidence relationships, and open decisions to their implementation tasks. The backend environment, local settings, bounded call layer, geographic client with location, discovery, and route requests, and the web-research client exist so far.
