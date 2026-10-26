# Frontend setup

GG-006.01 creates the SvelteKit package and a minimal static page/layout under `apps/frontend`. It is implemented, awaiting Section B verification at GG-007. The page contains the GobbleGobble title and a short description of its intended purpose. Search controls, recommendations and backend connections are later work.

## Prerequisites

Use Node 24 LTS, or Node 22 at version 22.17 or newer. The manifest accepts `^22.17.0 || >=24.0.0`. The package records npm 10.9.2, which generated the committed lockfile. Check `node --version` and `npm --version` before installation.

The execution environment inspected for GG-006.01 has Node 23.11.0 and npm 10.9.2. Node 23 is outside the declared range and the Svelte Vite plugin's supported range. Select a supported Node runtime before installing or running the frontend. No runtime was installed or changed during this task.

The frontend needs no backend process, database or provider credentials for its current static page. Keep OpenAI and provider keys in the Python backend. No frontend environment file is required.

## Install and start the frontend alone

Run these commands from `apps/frontend`, using a supported Node runtime.

```sh
npm ci
npm run dev
```

`npm ci` installs the exact lockfile and runs `prepare`, which invokes `svelte-kit sync`. A synchronization failure fails installation rather than being suppressed. Generated framework types and configuration are not committed.

`npm run dev` serves the frontend at `http://127.0.0.1:5173/`. This is the expected address, not a server verified during GG-006.01. The command binds to loopback and fails if port 5173 is occupied instead of selecting another port. Stop that foreground process with Ctrl+C. The port matches the backend's existing trusted development origin.

The scoped UI-01 visual preview belongs to GG-006.02 after the light tokens and shell are added. GG-007 owns full-stack startup and browser verification. No development server or browser was started during GG-006.01.

## Package commands

All commands run from `apps/frontend` after installation.

| Command | Purpose and exit behavior |
|---|---|
| `npm run dev` | Foreground frontend development on `127.0.0.1:5173`. An occupied port fails startup. |
| `npm run check` | Synchronizes framework files, then runs `svelte-check` with the strict TypeScript configuration. Failures return nonzero. |
| `npm run build` | Builds the frontend with the Node adapter. Compilation or adapter failure returns nonzero. |
| `npm run preview` | Serves an existing build on `127.0.0.1:4173`. Requires a prior successful build and fails if the port is occupied. |

Check/build/install and runtime compatibility remain unverified until their planned verification tasks. Preview is a local build inspection command, not the future full-app lifecycle command.

## Package and configuration reference

The private ESM package pins SvelteKit 3.1.0, Svelte 5.57.2, Vite 8.3.5, TypeScript 6.0.3, Svelte Vite plugin 7.3.1, Node adapter 6.0.0 and svelte-check 4.7.7. TypeScript 6 satisfies the selected framework's peer range; the registry's newer TypeScript 7 is not selected. `package-lock.json` records the resolved dependency graph and integrity values. Dependencies were resolved from the public npm registry without executing their code or installing `node_modules`.

| File | Responsibility |
|---|---|
| [package.json](../apps/frontend/package.json) and [package-lock.json](../apps/frontend/package-lock.json) | Package identity, commands, exact direct versions and resolved dependencies. |
| [vite.config.ts](../apps/frontend/vite.config.ts) | SvelteKit plugin with the Node adapter and Vite preprocessor. |
| [tsconfig.json](../apps/frontend/tsconfig.json) | Strict typing over application/configuration sources, extending `$app/tsconfig`. |
| [src/app.html](../apps/frontend/src/app.html) | English document template, viewport and framework placeholders. |
| [src/routes/+layout.svelte](../apps/frontend/src/routes/+layout.svelte) | Renders the framework-generated `LayoutProps.children` snippet. |
| [src/routes/+page.svelte](../apps/frontend/src/routes/+page.svelte) | Static root page and document metadata. |

SvelteKit 3 places adapter and preprocessing configuration in the Vite plugin and no longer supports `svelte.config.js`. Its generated TypeScript base is `$app/tsconfig`. These choices follow the [framework migration reference](https://svelte.dev/docs/kit/migrating-to-sveltekit-3), [TypeScript configuration reference](https://svelte.dev/docs/kit/$app-tsconfig) and [generated layout type reference](https://svelte.dev/docs/kit/types). The Node adapter supports the agreed local-host target. No CartCart UI library, font, fixture, test stack or styling system was inherited.

The repository ignores frontend `node_modules`, `.svelte-kit`, `.vite`, `build` and local `.env*` files, while allowing a future `.env.example`. Source, configuration and the lockfile remain eligible for version control. The existing backend and its local settings are unchanged.

## Verification boundary

GG-006.01 resolved the lockfile using `npm install --package-lock-only --ignore-scripts --no-audit --no-fund`. This operation writes dependency metadata without running lifecycle scripts or installing the frontend. It succeeded with the expected Node 23 engine warnings. It is not an installation, compilation or runtime compatibility check.

Task-local evidence is under `data/verification/GG-006.01/attempt-01/`. Source/manifest checks cover exact root dependency agreement, registry provenance/integrity, JSON parsing, output ignore rules and preservation of prior backend files. No application test suite is added for the static placeholder. GG-007 must perform the actual locked installation, framework type generation, `npm run check`, `npm run build` and stack/browser checks on a supported runtime. Record the effective versions and resulting output there.

GG-006.02 owns visual tokens and the light shell. GG-006.03 owns generated API types. GG-006.04 and GG-006.05 own browser transport and the diagnostic observer. GG-006.06 and GG-006.07 own project process lifecycle scripts.

## UI-01 preview now available

GG-006.02 adds the [light shell and visual evidence](FRONTEND_SHELL.md). Its required frontend-only preview used an isolated supported Node runtime, installed the lockfile and ran framework preparation. The default Node23 is unchanged. Actual desktop/phone and dark-preference observations are recorded in the shell reference. Broad checks/build and full-stack verification remain GG-007. Historical GG-006.01 statements above describe that earlier task's uninstalled scaffold.
