import type { StartRunRequest } from './types';

export class ApiError extends Error {
  constructor(readonly status: number) {
    super(`API request failed (${status})`);
    this.name = 'ApiError';
  }
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(path, { ...init, credentials: 'same-origin' });
  if (!response.ok) throw new ApiError(response.status);
  return response;
}

async function json(response: Response): Promise<unknown> {
  return response.json();
}

export async function bootstrapSession(): Promise<void> {
  await request('/api/session', { method: 'POST' });
}

export async function startRun(body: StartRunRequest): Promise<unknown> {
  const response = await request('/api/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  return json(response);
}

function runPath(searchId: string, runId: string): string {
  return `/api/searches/${encodeURIComponent(searchId)}/runs/${encodeURIComponent(runId)}`;
}

export async function readRun(searchId: string, runId: string): Promise<unknown> {
  return json(await request(runPath(searchId, runId)));
}

export function watchRun(searchId: string, runId: string, afterSequence = '0'): EventSource {
  const url = `${runPath(searchId, runId)}/events?after_sequence=${encodeURIComponent(afterSequence)}`;
  return new EventSource(url, { withCredentials: true });
}
