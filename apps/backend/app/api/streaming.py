from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from time import monotonic

import anyio
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from app.core.settings import DatabaseSettings
from app.db.events import read_events
from app.db.ownership import require_search_access
from app.db.runs import read_snapshot
from app.db.session import session
from app.schemas.lifecycle import (
    CancelledState,
    CompletedState,
    EventBatch,
    RunEvent,
    RunSnapshot,
)
from app.schemas.operations import WatchRunRequest

_BATCH_SIZE = 100
_POLL_SECONDS = 0.25
_HEARTBEAT_SECONDS = 15.0
_MAX_SEQUENCE = 2**63 - 1


@dataclass(frozen=True, repr=False)
class WatchPage:
    run: RunSnapshot
    batch: EventBatch


def watch_from_wire(
    search_id: str,
    run_id: str,
    query_values: Sequence[str],
    header_values: Sequence[str],
) -> WatchRunRequest:
    if len(query_values) > 1 or len(header_values) > 1:
        raise HTTPException(422)
    cursors = []
    for values in (query_values, header_values):
        if values:
            value = values[0]
            if not 1 <= len(value) <= 19 or any(c < "0" or c > "9" for c in value):
                raise HTTPException(422)
            cursor = int(value)
            if cursor > _MAX_SEQUENCE:
                raise HTTPException(422)
            cursors.append(cursor)
    try:
        return WatchRunRequest(
            search_id=search_id,
            run_id=run_id,
            after_sequence=cursors[-1] if cursors else 0,
        )
    except ValidationError:
        raise HTTPException(422) from None


def read_watch_page(
    settings: DatabaseSettings, credential: str, query: WatchRunRequest
) -> WatchPage:
    with session(settings) as connection:
        connection.execute("BEGIN").close()
        owned = require_search_access(connection, credential, query.search_id)
        snapshot = read_snapshot(
            connection, search_id=owned.search_id, run_id=query.run_id
        )
        if query.after_sequence > snapshot.last_event_sequence:
            raise HTTPException(422)
        batch = read_events(
            connection,
            query.run_id,
            after_sequence=query.after_sequence,
            limit=_BATCH_SIZE,
        )
        page = WatchPage(run=snapshot, batch=batch)
        connection.execute("COMMIT").close()
    return page


def event_frame(event: RunEvent) -> str:
    return f"id: {event.sequence}\ndata: {event.model_dump_json()}\n\n"


def terminal_and_caught_up(page: WatchPage, cursor: int) -> bool:
    return (
        isinstance(page.run.state, CompletedState | CancelledState)
        and cursor == page.run.last_event_sequence
    )


async def follow_events(
    settings: DatabaseSettings,
    credential: str,
    query: WatchRunRequest,
    page: WatchPage,
) -> AsyncIterator[str]:
    cursor = query.after_sequence
    heartbeat_at = monotonic()
    yield ": connected\n\n"
    while True:
        for event in page.batch.events:
            yield event_frame(event)
            cursor = event.sequence
            heartbeat_at = monotonic()
        if terminal_and_caught_up(page, cursor):
            return
        if cursor == page.run.last_event_sequence:
            await anyio.sleep(_POLL_SECONDS)
            if monotonic() - heartbeat_at >= _HEARTBEAT_SECONDS:
                yield ": keep-alive\n\n"
                heartbeat_at = monotonic()
        page = await run_in_threadpool(
            read_watch_page,
            settings,
            credential,
            query.model_copy(update={"after_sequence": cursor}),
        )


class EventStreamResponse(StreamingResponse):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async with anyio.create_task_group() as tasks:

            async def stream() -> None:
                await self.stream_response(send)
                tasks.cancel_scope.cancel()

            tasks.start_soon(stream)
            await self.listen_for_disconnect(receive)
            tasks.cancel_scope.cancel()
        if self.background is not None:
            await self.background()
