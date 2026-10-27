# GobbleGobble

GobbleGobble is a local app being built to help you choose where to eat now. The intended experience starts with your location and travel limit, then suggests places with reasons to go, food to try and links to the supporting sources.

## Current state

The repository contains a light interface preview and backend foundations for admitting work, saving progress and streaming updates. It also includes a separate diagnostic observer, a controlled worker for testing that lifecycle, and bounded research probes for development.

The local foundation has been exercised through controlled browser jobs, event replay and process restart. The restaurant-search journey is not connected yet. The main page does not submit searches or display food recommendations. Production research, results, refinement and user retry/cancellation are still under development.

## Try the interface

Use Node 24 LTS, or Node 22.17 or newer within the Node 22 release line, and npm. Node 23 is unsupported by the selected frontend tools.

From the repository root:

```sh
cd apps/frontend
npm ci
npm run dev
```

Open [the local preview](http://127.0.0.1:5173/). It runs without the backend, a database or provider credentials. The preview shows the visual direction only. Stop the foreground server with Ctrl+C.

See [local setup](docs/SETUP.md#start-the-local-foundation) for the combined local commands and diagnostic observer, and [architecture](docs/ARCHITECTURE.md#frontend-design) for the interface design.

## Work on the backend

The backend uses Python 3.14, FastAPI and SQLite. Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then follow [local setup](docs/SETUP.md). Initialization and the controlled worker need no provider credentials.

Live research probes contact external services and can incur costs. They require explicit live mode and configured credentials. Start with the [probe guide](docs/LIVE_PROBES.md), rather than treating a probe as the normal application.

Keep secrets in the backend environment. Do not put OpenAI or provider keys in frontend code or commit local environment files.

## Contributor references

Read only the guide relevant to your work.

- To run or configure the project, use [setup](docs/SETUP.md) and [settings](docs/SETTINGS.md).
- To change the implementation, use [architecture](docs/ARCHITECTURE.md), [API](docs/API.md), [domain validation](docs/DOMAIN.md) and [persistence](docs/DATABASE.md).
- To understand intended behavior and assess results, use [product requirements](docs/PRODUCT.md) and [quality evaluation](docs/EVALUATION.md).
- To work with external research, use [source policy](docs/INTEGRATIONS.md), [development probes](docs/LIVE_PROBES.md) and [third-party notices](docs/THIRD_PARTY_NOTICES.md).

After changing public documentation, run `python3 scripts/local/check-docs.py` from the repository root. It checks private-plan references and local links, not readability or runtime behavior.

The project is designed for one local host. It is not yet a finished restaurant-recommendation service or a deployment guide.
