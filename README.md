# GobbleGobble

GobbleGobble is a planned local app for choosing where to eat now using real location and food research.

The [product and delivery contract](docs/PRODUCT.md) records the agreed journey, scope, and acceptance target. The backend Python environment is available. The API, worker, and website are not implemented yet.

Follow [the backend setup instructions](docs/BACKEND_SETUP.md) to install the locked dependencies with `scripts/local/init-backend.sh`. Setup requires uv and does not need provider credentials or make research calls.

The [settings reference](docs/SETTINGS.md) documents typed local configuration, explicit live/diagnostic/test modes, and credential preflight. `apps/backend/.env.example` lists the inputs without selecting providers or models. Preflight checks local readiness without contacting services.

The [call and receipt reference](docs/CALLS.md) describes shared request/time bounds, source-use admission, and metadata-only diagnostics. The call layer is implemented, awaiting verification at GG-003.

The [location and discovery probe reference](docs/GEOGRAPHY_PROBES.md) describes the prepared Geoapify client, request examples, provider IDs, attribution, and limitations. The [route probe reference](docs/ROUTE_PROBES.md) describes prepared walking and driving estimates and explicit unavailable/failure outcomes. The [web-research probe reference](docs/WEB_RESEARCH_PROBES.md) describes prepared OpenAI search requests, exact citation capture, and source-use limits. Full Section A verification remains pending. The early review investigation below records limited live evidence. SDK probes and the probe command remain future tasks.

The [Google-review contract](docs/GOOGLE_REVIEWS.md) makes Google reviews a required primary source, with labeled fallback for missing or unavailable reviews. GG-009.01B is complete. Its selected access path is bounded anonymous Google retrieval demonstrated through YasogaN, without Google billing. The reusable [Google review probe](docs/GOOGLE_REVIEW_PROBES.md) is prepared in GG-002.06A. Production integration and fallback remain later tasks.

The [review-source feasibility decision](docs/REVIEW_FEASIBILITY.md) records actual YasogaN 3.1.0 execution with 38 distinct reviews across relevance and newest samples for a corroborated Toyo Eatery branch. The specifically approved Google-to-model pilot also passed eight bounded traceability and limitation checks. The user removed legal clearance as a completion gate and reaffirmed full application quality. Historical legal findings are retained without claiming clearance. GG-002.06A prepares the reusable Python adapter and narrow minimized-review admission. Behavioral and live verification remain at GG-003. Google stays primary, other sources supplementary only, and Wikivoyage excluded.

The selected review path is the Python and HTTPX adapter based on YasogaN 3.1.0. Two bounded comparisons with signed-in Scraper Pro returned the same 20 newest review IDs, order and ratings for Toyo Eatery and Ippei Honten. Pro supplied no extra substantive review text and took about 15 and 23 times longer. The user chose the anonymous client alone, with no scraper backup. Pro's temporary code, runtime and throwaway profile were removed. [The comparison record](docs/GOOGLE_REVIEW_PROBES.md#temporary-signed-in-pro-comparison) links the saved outputs. Production integration remains GG-009.01A.

The ignored `apps/backend/.env` now supplies the initial provider/model choices, timeouts, and user-supplied Geoapify key. The OpenAI key is available in the process environment. Live preflight passes. Configured OpenAI access and two bounded Geoapify location/discovery calls succeeded. The historical anonymous extractor needed no Google credentials. The new client requires the two independent review settings documented in the [probe reference](docs/GOOGLE_REVIEW_PROBES.md). See the [actual evidence and accounting](docs/REVIEW_FEASIBILITY.md#actual-evidence-and-experiment-accounting).

The [integration comparison](docs/INTEGRATIONS.md) records proposed provider paths, source-use restrictions, and account requirements. Live feasibility and provider selection remain at GG-003.

The [paired quality-comparison protocol](docs/EVALUATION.md) defines matched inputs, scenario classes, observable criteria, and per-scenario acceptance against normal ChatGPT with web search. Actual baseline capture and live comparison remain future work at GG-023.

The [architecture and ownership reference](docs/ARCHITECTURE.md) maps the proposed modules, adaptive recommendation owner, bounded research tool-agent, durable worker, evidence relationships, and open decisions to their implementation tasks. The backend environment, local settings, bounded call layer, geographic client with location, discovery, and route requests, the web-research client, and the prepared Google review client exist so far.

Wikivoyage is excluded from source candidates because of outdated information. Its earlier experiment remains [historical evidence](docs/REVIEW_FEASIBILITY.md#licensed-independent-source-validation) only. Google reviews remain mandatory and primary. Other supplementary sources may be assessed individually for freshness, usefulness and permitted use.

The approved Google-to-model pilot fetched ten distinct reviews, selected one seven-word food passage and passed eight traceability/limitation checks in 5.15 seconds using Sol. [The verified record](docs/REVIEW_FEASIBILITY.md#verified-approved-pilot-and-current-disposition) distinguishes this narrow result from semantic quality and commercial clearance. Current local model settings use Luna for development; the Sol receipt is historical evidence.
