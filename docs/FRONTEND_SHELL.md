# Light shell preview

GG-006.02 implements the global theme and basic shell. Its frontend-only visual checks passed. Section B remains unverified until GG-007.

The running UI-01 preview is at http://127.0.0.1:5173/. Inspect desktop spacing, narrow the browser to phone width, and try the dark “About this preview” disclosure with the keyboard. Search, food recommendations, API types, sessions and the diagnostic observer are not ready.

## Reference mapping

| Role | Token/value | Reference |
|---|---|---|
| Canvas | `--color-parchment`, `#f7f7f4` | Parchment canvas. |
| Disclosed notice | `--color-bone`, `#f2f1ed` | Bone surface. |
| Borders | `--color-stone`, `#cdcdc9` | Stone hairline. |
| Text and primary control | `--color-ink`, `#26251e` | Ink text and dark primary action. |
| Reserved accent | `--color-ember`, `#f54e00` | Optional text accent, unused in this shell. |
| Corners | `--radius-control` and `--radius-panel`, `4px` | Agreed 4px geometry. |
| Spacing | `--space-*`, multiples of 4px | Reference spacing scale. |
| Fonts | Installed Inter, system UI, Apple and Segoe UI fallbacks | Reference substitute fonts. No font download. |

`src/app.css` owns these tokens, explicit canvas/text colors and `color-scheme: light`. The root layout imports it once and preserves generated `LayoutProps.children`. The static route contains no textbox, network client or application state. The native details/summary control discloses one honest preview notice without JavaScript state. It is not a search control.

## CartCart direction

The user requested CartCart's experience and flow with substantially less input and no general question textbox. Source review covered CartCart's home state transitions, AppHeader, PromptSurface, ProcessingState and RecommendationResult. The useful pattern is one prominent task with details revealed when needed. Its shopping intake, dark theme, forms, libraries and state machine are not inherited.

The initial shell had too many small labels. User feedback led to removal of the eyebrow, header preview label, footer and feedback-instruction section. The final shell has quiet branding, a centered heading/subtitle and one collapsed preview disclosure. Feedback instructions stay in this document and the handoff. Later entry/progress/results tasks should retain this restraint while meeting their own acceptance conditions. Existing location requirements remain unchanged.

## Preview environment and observations

The machine's default Node 23 remains unchanged. An official Node 24.22.1 ARM archive was downloaded into `/private/tmp/gobblegobble-node24`, checked against the vendor SHA256 and extracted without a global installation. It supplies npm 11.19.1 for this session. The locked dependencies were installed only to enable this task's expressly required preview. `npm ci` and its `svelte-kit sync` preparation succeeded. npm reported an optional fsevents install-script approval notice; no approval was granted. HMR and the actual preview worked.

To restart this session's preview from `apps/frontend`, while the temporary runtime remains available:

```sh
PATH=/private/tmp/gobblegobble-node24/node-v24.22.1-darwin-arm64/bin:$PATH npm run dev
```

With a supported Node already selected, use the standard `npm run dev` command from [frontend setup](FRONTEND_SETUP.md). The current frontend process listens only on loopback port 5173. Its recorded listener PID is 7565. To stop it after review, verify that PID still belongs to this project's Vite process before terminating it. Process lifecycle scripts remain later work.

Actual Chrome inspection covered desktop, 390px and 320px widths. Neither phone width overflowed horizontally. The native summary was keyboard reachable after the skip link, showed a 2px ink focus outline with 4px offset and opened/closed with Enter. The phone action measured 47px high. Under browser-emulated dark preference, the final expanded disclosure still had light color scheme, parchment canvas, bone panel and ink action. Temporary viewport overrides were reset. The emulation tab was closed and a fresh clean preview left open. The tab error/warning log query returned no entries during inspection.

Evidence is under `data/verification/GG-006.02/attempt-01/`, including final desktop/phone/dark screenshots, computed metrics, baseline identities and source/comment reviews. Earlier screenshots describe the superseded text-heavy version. Browser resource enumeration was unavailable through the read-only interface; no complete network trace is claimed. Source contains no backend/provider client or remote font import, and none was invoked by the task.

No broad svelte-check, production build, full-stack suite, database, worker, provider or model call ran. GG-007 retains section verification. GG-006.03 has not started.
