import type { components, operations } from './contract';

export type StartRunRequest = operations['startRun']['requestBody']['content']['application/json'];
export type StartRunResponse = operations['startRun']['responses'][202]['content']['application/json'];
export type RunReadResponse = operations['readRun']['responses'][200]['content']['application/json'];
export type RunEvent = components['schemas']['RunEvent'];
