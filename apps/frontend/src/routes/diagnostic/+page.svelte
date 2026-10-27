<script lang="ts">
  import { onMount } from 'svelte';
  import { bootstrapSession, readRun, startRun, watchRun } from '../../api/client';
  import type { StartRunRequest } from '../../api/types';
  import { parseAdmission, parseEvent, parseIdentity, parseRead, type Identity, type Progress, type Snapshot } from './observer';

  const storageKey = 'gobblegobble.diagnostic.v1';
  type Pending = { commandId: string; at: string };
  let identity = $state<Identity | null>(null);
  let pending = $state<Pending | null>(null);
  let snapshot = $state<Snapshot | null>(null);
  let events = $state<Progress[]>([]);
  let connection = $state('Not connected');
  let message = $state('');
  let busy = $state(false);
  let stream: EventSource | null = null;
  let active = false;

  function store(value: unknown) { sessionStorage.setItem(storageKey, JSON.stringify(value)); }

  function pendingFrom(value: unknown): Pending | null {
    if (value === null || typeof value !== 'object' || !('commandId' in value) || !('at' in value)) return null;
    if (typeof value.commandId !== 'string' || !/^[a-f0-9-]{36}$/.test(value.commandId)) return null;
    if (typeof value.at !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(value.at) || !Number.isFinite(Date.parse(value.at))) return null;
    return { commandId: value.commandId, at: value.at };
  }

  function requestFor(item: Pending): StartRunRequest {
    return {
      command_id: item.commandId,
      request: {
        origin: { kind: 'chosen', location: { coordinates: { latitude: 0, longitude: 0 }, timezone: 'UTC', provider_refs: [] } },
        travel: { mode: 'walk', minutes: 15 },
        eat_now_at: item.at
      }
    };
  }

  function subscribe(target: Identity) {
    stream?.close();
    events = [];
    connection = 'Connecting';
    const source = watchRun(target.searchId, target.runId);
    stream = source;
    source.onopen = () => { if (stream === source) connection = 'Connected'; };
    source.onerror = () => { if (stream === source) connection = source.readyState === EventSource.CLOSED ? 'Connection closed. Reconnect to try again.' : 'Connection interrupted. Reconnecting automatically.'; };
    source.onmessage = (event) => {
      if (stream !== source) return;
      const progress = parseEvent(event.data, event.lastEventId, target);
      if (!progress) {
        source.close();
        stream = null;
        connection = 'Stopped';
        message = 'The server sent an invalid progress event. Reconnect to try again.';
        return;
      }
      if (!events.some((entry) => entry.sequence === progress.sequence)) events = [...events, progress];
      if (snapshot && progress.sequence > snapshot.sequence) snapshot = { ...snapshot, ...progress };
      if (progress.terminal) {
        source.close();
        stream = null;
        connection = 'Complete';
      }
    };
  }

  async function reconnect(target: Identity) {
    busy = true;
    message = '';
    try {
      await bootstrapSession();
      const next = parseRead(await readRun(target.searchId, target.runId), target);
      if (!next) throw new Error('invalid response');
      if (active) { snapshot = next; subscribe(target); }
    } catch {
      connection = 'Not connected';
      message = 'Could not load this diagnostic run. Check that the API is running and try reconnecting.';
    } finally { busy = false; }
  }

  async function submit(item: Pending) {
    busy = true;
    message = '';
    try {
      await bootstrapSession();
      const next = parseAdmission(await startRun(requestFor(item)), item.commandId);
      if (!next) throw new Error('invalid response');
      const target = { searchId: next.searchId, runId: next.runId };
      store({ identity: target });
      pending = null;
      identity = target;
      snapshot = next;
      if (active) subscribe(target);
    } catch {
      message = 'The request may have reached the server. Retry this same request to recover it.';
    } finally { busy = false; }
  }

  function create() {
    try {
      const item = { commandId: crypto.randomUUID(), at: new Date().toISOString() };
      store({ pending: item });
      pending = item;
      void submit(item);
    } catch {
      message = 'Could not save the request in this browser tab. No run was started.';
    }
  }

  function forget() {
    try {
      sessionStorage.removeItem(storageKey);
      stream?.close();
      stream = null;
      identity = null;
      snapshot = null;
      events = [];
      message = '';
      connection = 'Not connected';
    } catch { message = 'Could not clear the saved run from this browser tab.'; }
  }

  onMount(() => {
    active = true;
    try {
      const stored = sessionStorage.getItem(storageKey);
      if (stored) {
        const value: unknown = JSON.parse(stored);
        if (value !== null && typeof value === 'object') {
          if ('identity' in value) identity = parseIdentity(value.identity);
          if ('pending' in value) pending = pendingFrom(value.pending);
        }
        if (identity) void reconnect(identity);
        else if (!pending) message = 'Saved diagnostic details are invalid. Clear this tab’s session storage before starting again.';
      }
    } catch { message = 'Could not read diagnostic details from this browser tab.'; }
    return () => { active = false; stream?.close(); stream = null; };
  });
</script>

<svelte:head><title>Diagnostic observer | GobbleGobble</title></svelte:head>

<section class="diagnostic" aria-labelledby="diagnostic-title">
  <h1 id="diagnostic-title">Diagnostic run observer</h1>
  <p>This separate page checks the local job lifecycle. It uses a fixed test location and does not research places or give food advice.</p>
  {#if !identity && !pending}
    <button class="primary-action" onclick={create} disabled={busy || !!message}>Create diagnostic run</button>
  {:else if pending}
    <button class="primary-action" onclick={() => pending && submit(pending)} disabled={busy}>Retry saved request</button>
  {/if}
  {#if message}<p class="notice" role="alert">{message}</p>{/if}
  {#if identity}
    <div class="panel">
      <h2>Current run</h2>
      <p>Run ID: <code>{identity.runId}</code></p>
      <p>Search ID: <code>{identity.searchId}</code></p>
      <p>Snapshot: {snapshot?.state ?? 'Unavailable'}</p>
      <p>Event connection: {connection}</p>
      <button onclick={() => identity && reconnect(identity)} disabled={busy}>Reconnect and replay</button>
      <button onclick={forget} disabled={busy}>Forget this run</button>
      <p>To advance this run, start the diagnostic worker from <code>apps/backend</code> with the run ID shown above.</p>
      <code class="command">.venv/bin/python -m app.worker diagnostic --run-id {identity.runId}</code>
    </div>
    <div class="panel" aria-live="polite">
      <h2>Progress events</h2>
      {#if events.length === 0}<p>No events received yet.</p>{:else}
        <ol>{#each events as event (event.sequence)}<li value={event.sequence}>{event.state}</li>{/each}</ol>
      {/if}
    </div>
  {/if}
</section>

<style>
  .diagnostic { display: grid; gap: 1rem; }
  h1 { font-size: clamp(2rem, 5vw, 3rem); }
  h2 { font-size: 1.2rem; margin: 0 0 0.5rem; }
  .diagnostic > .primary-action { justify-self: start; margin-top: 0; }
  .panel { display: grid; gap: 0.75rem; padding: 1.5rem; background: var(--surface-panel); border: 1px solid var(--border-color); border-radius: var(--radius-panel); }
  .notice { color: #9a2900; }
  code { overflow-wrap: anywhere; }
  .command { display: block; }
  ol { margin: 0; padding-left: 1.5rem; }
  li { padding-block: 0.125rem; }
  button:not(.primary-action) { justify-self: start; padding: 0.5rem 0.75rem; border: 1px solid var(--border-color); border-radius: var(--radius-control); background: var(--surface-canvas); color: inherit; cursor: pointer; }
  button:disabled { opacity: 0.55; cursor: default; }
</style>
