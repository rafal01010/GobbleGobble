# Run event subscription

GG-005.07 implements replay followed by ongoing SSE delivery. It awaits Section B verification at GG-007. The endpoint observes committed events and never admits work or owns the worker's lifetime.

## HTTP contract

`GET /api/searches/{search_id}/runs/{run_id}/events` requires the existing host-only `gg_session` cookie. Bootstrap the session and admit a run through the [API operations](API.md#implemented-http-operations) first. Unknown, foreign and mismatched search/run pairs receive the same fixed 404. Missing or invalid credentials receive 401.

The optional `after_sequence` query parameter defaults to zero. `Last-Event-ID` takes precedence when present, so automatic browser reconnection can advance beyond the cursor in its original URL. Both supplied inputs must be valid even when the header takes precedence. Each accepts one ASCII decimal integer between zero and 9223372036854775807. Empty, signed, fractional, whitespace, non-ASCII, duplicate and oversized values receive the fixed 422 envelope. A cursor beyond the authorized run's current durable sequence also receives 422.

A successful subscription returns `text/event-stream` with `Cache-Control: no-store` and `X-Accel-Buffering: no`. Each application message has a run-scoped sequence ID and the complete stored `RunEvent` as compact JSON. There is no named `event` field, so browser clients use `onmessage`.

```text
id: 1
data: {"schema_version":1,"search_id":"example-search","run_id":"example-run","attempt_id":null,"sequence":1,"occurred_at":"2026-10-26T00:00:00Z","result_version_id":null,"payload":{"kind":"queued"}}

```

The example illustrates framing, not an observed response. An opening comment and idle heartbeat comments contain no event ID. They do not change the reconnect cursor. EventSource clients on the configured frontend origin use `withCredentials: true`. Credentialed CORS permits `Last-Event-ID` for clients that send the header explicitly. No credential or request location appears in the event URL or payload.

Only acceptance of an application event advances the client's cursor. Reconnect strictly after the last accepted sequence. Deduplicate by run identity and sequence because a transport break can cause repeated delivery. The wire format follows the [HTML SSE specification](https://html.spec.whatwg.org/multipage/server-sent-events.html).

## Replay and follow

The API reads and authorizes the first batch before sending headers. It then uses the same durable suffix query for replay and follow. A commit between the first read and response start, or between an empty read and the next poll, remains eligible for the next query. There is no registration window or process-local notification requirement.

Each poll runs in a synchronous threadpool callback that opens and closes its own SQLite connection. A short deferred read transaction holds search authorization, paired run lookup and event reading in one consistent snapshot. It commits and closes before the callback returns. No connection or transaction survives a network send or asynchronous wait. The subscription performs no writes.

Each batch contains at most 100 events. Replay drains available batches without an idle wait. At the tail, the subscription waits 250 milliseconds between polls. An idle comment heartbeat is due every 15 seconds. These values are implementation defaults, not measured latency guarantees. Slow clients hold at most one batch in the application stream; each send applies backpressure.

A single disconnect listener races the stream sender on every ASGI version. Disconnect cancels only the subscription. Cancellation waits for an active database callback to close its connection, then starts no further poll. The existing SQLite lock timeout is five seconds. There is no global watcher registry, per-client durable cursor or worker notification channel.

Before headers, cursor, ownership and storage failures use the [fixed API error envelopes](API.md#implemented-http-operations). After headers, a failure interrupts the transport through the existing sanitized `api_response_interrupted` path. It does not append an error JSON object, invent an event or reset the cursor.

## Limits and deferred verification

GG-005.08 adds [controlled worker completion](CONTROLLED_WORKER.md) and history projection for completed coverage-gap and technical-failure outcomes. The existing stream can drain that completion event and return 204 for a caught-up reconnect. Actual terminal delivery remains unverified until GG-007. Recommendation result publication, no-matches and cancellation retain their own tasks and must extend the shared history projection for their newly reachable states. A client must close EventSource after accepting a terminal event.

Every subscriber polls durable storage independently. The existing event reader validates history with a run-index scan on each poll. Subscriber capacity, contention, event size and delivery timing have not been measured. The implementation adds no retention, pruning, expiry or credential recovery policy.

GG-007 must exercise these scenarios through actual HTTP and durable writers.

1. Subscribe at zero or at the current tail, then commit new events. Confirm delivery without reconnecting and compare every envelope to persisted events.
2. Reconnect after a received sequence, including with an older query cursor and newer `Last-Event-ID`. Confirm ordered suffix replay across attempts without earlier events.
3. Commit between an empty poll and the next poll, replay more than one batch, and connect independent subscribers at different cursors. Confirm no gap or cross-subscriber cursor sharing.
4. Roll back a provisional event. Confirm it is invisible. Disconnect the browser. Confirm the durable run keeps progressing and idle polling stops.
5. Exercise malformed, duplicate and ahead cursors, absent or invalid cookies, foreign or unknown IDs and same-owner mismatched pairs. Confirm the fixed errors arrive before SSE headers.
6. Exercise allowed and denied CORS preflights, storage failures before and after headers, slow readers and API restart. Confirm safe diagnostics, bounded polling and reconnect position.
7. Measure first replay, new-event latency and cleanup timing with effective non-secret configuration. Verify terminal draining and 204 through the GG-005.08 controlled writer and matching history support.

The task-local pure checker is `data/verification/GG-005.07/attempt-01/sanity.py`. Run it from the repository root with `apps/backend/.venv/bin/python`. It needs the installed backend environment, accepts no arguments, performs no database or HTTP work, and exits nonzero on an assertion failure. It does not replace the gate scenarios.
