export type Identity = { searchId: string; runId: string };
export type Snapshot = Identity & { sequence: number; state: string; terminal: boolean };
export type Progress = { sequence: number; state: string; terminal: boolean };

type ObjectValue = Record<string, unknown>;

function object(value: unknown): ObjectValue | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? Object.fromEntries(Object.entries(value))
    : null;
}

function id(value: unknown): value is string {
  return typeof value === 'string' && /^[a-f0-9]{32}$/.test(value);
}

function sequence(value: unknown, minimum: number): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum;
}

const stages = new Set(['starting', 'discovering', 'researching', 'composing', 'publishing', 'recovering']);
const outcomes = new Set(['ready', 'partial', 'no_matches', 'insufficient_coverage', 'technical_failure']);

function state(value: unknown, field: 'status' | 'kind'): { state: string; terminal: boolean } | null {
  const item = object(value);
  if (!item) return null;
  const kind = item[field];
  if (kind === 'queued') return { state: 'Queued', terminal: false };
  if (kind === 'running' || kind === 'progress') {
    if (typeof item.stage !== 'string' || !stages.has(item.stage)) return null;
    return { state: `Running: ${item.stage}`, terminal: false };
  }
  if (kind === 'cancelling') return { state: 'Cancelling', terminal: false };
  if (kind === 'cancelled') return { state: 'Cancelled', terminal: true };
  if (kind === 'completed') {
    const outcome = object(item.outcome);
    if (!outcome || typeof outcome.kind !== 'string' || !outcomes.has(outcome.kind)) return null;
    return { state: `Completed: ${outcome.kind}`, terminal: true };
  }
  return null;
}

export function parseSnapshot(value: unknown): Snapshot | null {
  const run = object(value);
  if (!run || !id(run.search_id) || !id(run.run_id) || !sequence(run.last_event_sequence, 0)) return null;
  const parsed = state(run.state, 'status');
  return parsed ? { searchId: run.search_id, runId: run.run_id, sequence: run.last_event_sequence, ...parsed } : null;
}

export function parseAdmission(value: unknown, commandId: string): Snapshot | null {
  const response = object(value);
  const command = object(response?.command);
  if (!command || command.command_id !== commandId || (command.disposition !== 'accepted' && command.disposition !== 'replayed')) return null;
  return parseSnapshot(command.run);
}

export function parseRead(value: unknown, identity: Identity): Snapshot | null {
  const response = object(value);
  const snapshot = parseSnapshot(response?.run);
  return snapshot && snapshot.searchId === identity.searchId && snapshot.runId === identity.runId ? snapshot : null;
}

export function parseEvent(data: string, eventId: string, identity: Identity): Progress | null {
  let value: unknown;
  try { value = JSON.parse(data); } catch { return null; }
  const event = object(value);
  if (!event || event.schema_version !== 1 || event.search_id !== identity.searchId || event.run_id !== identity.runId || !sequence(event.sequence, 1) || String(event.sequence) !== eventId) return null;
  const parsed = state(event.payload, 'kind');
  return parsed ? { sequence: event.sequence, ...parsed } : null;
}

export function parseIdentity(value: unknown): Identity | null {
  const item = object(value);
  return item && id(item.searchId) && id(item.runId) ? { searchId: item.searchId, runId: item.runId } : null;
}
