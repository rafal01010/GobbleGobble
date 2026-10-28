from __future__ import annotations

import json
import logging
import sqlite3
from typing import Annotated, Any, Literal

from fastapi import Cookie, Depends, FastAPI, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.security import APIKeyCookie
from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.streaming import (
    EventStreamResponse,
    follow_events,
    read_watch_page,
    terminal_and_caught_up,
    watch_from_wire,
)
from app.core.calls import AttemptCalls, Bounds
from app.core.settings import (
    ApiSettings,
    ConfigurationError,
    DatabaseSettings,
    load_api_settings,
    load_database_settings,
    load_location_settings,
)
from app.db.migrations import MigrationError, require_current_schema
from app.db.ownership import (
    InvalidSession,
    SearchNotFound,
    issue_session,
    require_search_access,
    resolve_session,
)
from app.db.runs import CommandConflict, RunNotFound, admit_start, read_snapshot
from app.db.session import session, transaction
from app.location import LocationResolver
from app.providers.geoapify import GeoapifyClient
from app.schemas.lifecycle import RunReadResponse
from app.schemas.operations import (
    CommandResponse,
    ErrorCode,
    ErrorResponse,
    LocationUnavailableOutcome,
    OperationError,
    ReadRunRequest,
    ResolveLocationRequest,
    ResolveLocationResponse,
    StartRunRequest,
)

_logger = logging.getLogger(__name__)

SESSION_COOKIE = "gg_session"
_cookie = APIKeyCookie(name=SESSION_COOKIE, auto_error=False)
Credential = Annotated[str | None, Security(_cookie)]


class ReadyResponse(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    status: Literal["ready"] = "ready"


def start_from_wire(value: object) -> StartRunRequest:
    if isinstance(value, StartRunRequest):
        return StartRunRequest.model_validate(value)
    try:
        encoded = json.dumps(value, allow_nan=False)
    except TypeError, ValueError:
        raise ValueError("invalid_request") from None
    return StartRunRequest.model_validate_json(encoded)


StartBody = Annotated[StartRunRequest, BeforeValidator(start_from_wire)]


async def location_resolver() -> LocationResolver | None:
    try:
        settings = load_location_settings()
    except ConfigurationError:
        return None
    calls = AttemptCalls(
        settings,
        Bounds(max_requests=1, total_seconds=settings.geographic_timeout_seconds),
    )
    return LocationResolver(GeoapifyClient(calls))


def error_response(
    status: int, code: ErrorCode, message: str, *, retryable: bool = False
) -> JSONResponse:
    body = ErrorResponse(
        error=OperationError(code=code, message=message, retryable=retryable)
    )
    return JSONResponse(
        status_code=status,
        content=body.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )


def _exception_response(error: Exception) -> JSONResponse:
    if isinstance(error, InvalidSession):
        return error_response(401, "invalid_request", "invalid_session")
    if isinstance(error, SearchNotFound | RunNotFound):
        return error_response(404, "not_found", "not_found")
    if isinstance(error, CommandConflict):
        return error_response(409, "command_conflict", "command_conflict")
    if isinstance(error, sqlite3.Error | MigrationError | OSError):
        return error_response(503, "unavailable", "storage_unavailable", retryable=True)
    return error_response(500, "unavailable", "internal_error")


def mutation_origin_allowed(headers: Headers, origins: tuple[str, ...]) -> bool:
    supplied = headers.getlist("origin")
    return len(supplied) == 1 and supplied[0] in origins


class ApiBoundary:
    def __init__(self, app: ASGIApp, *, origins: tuple[str, ...]) -> None:
        self.app = app
        self.origins = origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope["method"] in ("POST", "PUT", "PATCH", "DELETE") and not (
            mutation_origin_allowed(Headers(scope=scope), self.origins)
        ):
            await error_response(403, "invalid_request", "origin_not_allowed")(
                scope, receive, send
            )
            return
        started = False

        async def no_store_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        try:
            await self.app(scope, receive, no_store_send)
        except Exception as error:
            _logger.error(
                "api_request_failed", exc_info=RuntimeError("api_request_failed")
            )
            # Starlette's outer error handler rethrows raw exceptions to the server.
            if started:
                raise RuntimeError("api_response_interrupted") from None
            await _exception_response(error)(scope, receive, no_store_send)


class ApiCORSMiddleware(CORSMiddleware):
    def preflight_response(self, request_headers: Headers) -> Response:
        response = super().preflight_response(request_headers)
        if response.status_code >= 400 or len(request_headers.getlist("origin")) != 1:
            safe = error_response(400, "invalid_request", "cors_not_allowed")
            for name, value in response.headers.items():
                if name.lower() not in ("content-length", "content-type"):
                    safe.headers[name] = value
            return safe
        response.headers["Cache-Control"] = "no-store"
        return response


class _Application(FastAPI):
    def openapi(self) -> dict[str, Any]:
        from app.schema_export import compose_openapi_document

        if self.openapi_schema is None:
            self.openapi_schema = compose_openapi_document(self)
        return self.openapi_schema


def create_app(
    *, api_settings: ApiSettings, database_settings: DatabaseSettings
) -> FastAPI:
    app = _Application(
        title="GobbleGobble backend contract", version="0.1.0", debug=False
    )
    app.add_middleware(ApiBoundary, origins=api_settings.trusted_frontend_origins)
    app.add_middleware(
        ApiCORSMiddleware,
        allow_origins=api_settings.trusted_frontend_origins,
        allow_credentials=True,
        allow_methods=("GET", "POST"),
        allow_headers=("Content-Type", "Last-Event-ID"),
    )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        return error_response(422, "invalid_request", "invalid_request")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        if error.status_code == 404:
            return error_response(404, "not_found", "not_found")
        if error.status_code == 405:
            response = error_response(405, "invalid_request", "method_not_allowed")
            if error.headers and "Allow" in error.headers:
                response.headers["Allow"] = error.headers["Allow"]
            return response
        return error_response(error.status_code, "invalid_request", "invalid_request")

    errors: dict[int | str, dict[str, Any]] = {
        status: {"model": ErrorResponse}
        for status in (400, 401, 403, 404, 405, 409, 422, 500, 503)
    }

    @app.get(
        "/ready", response_model=ReadyResponse, responses=errors, operation_id="ready"
    )
    def ready() -> ReadyResponse:
        with session(database_settings) as connection:
            require_current_schema(connection)
        return ReadyResponse()

    @app.post(
        "/api/session",
        status_code=204,
        responses=errors,
        operation_id="bootstrapSession",
    )
    def bootstrap(
        request: Request,
        credential: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> Response:
        issued = None
        with session(database_settings) as connection:
            if credential is None:
                with transaction(connection):
                    issued = issue_session(connection)
            else:
                resolve_session(connection, credential)
        response = Response(status_code=204)
        if issued is not None:
            response.set_cookie(
                SESSION_COOKIE,
                issued.credential,
                secure=request.url.scheme == "https"
                or api_settings.session_cookie_secure,
                httponly=True,
                samesite="lax",
                path="/",
            )
        return response

    @app.post(
        "/api/locations/resolve",
        response_model=ResolveLocationResponse,
        responses=errors,
        operation_id="resolveLocation",
    )
    async def resolve_location(
        command: ResolveLocationRequest,
        credential: Credential,
        resolver: Annotated[LocationResolver | None, Depends(location_resolver)],
    ) -> ResolveLocationResponse:
        if credential is None:
            raise InvalidSession()
        with session(database_settings) as connection:
            resolve_session(connection, credential)
        if resolver is None:
            return ResolveLocationResponse(
                outcome=LocationUnavailableOutcome(
                    message="Location lookup is not configured.", retryable=False
                )
            )
        return await resolver.resolve(command.location)

    @app.post(
        "/api/runs",
        status_code=202,
        response_model=CommandResponse,
        responses=errors,
        operation_id="startRun",
    )
    def start(command: StartBody, credential: Credential) -> CommandResponse:
        if credential is None:
            raise InvalidSession()
        with session(database_settings) as connection, transaction(connection):
            result = admit_start(connection, credential, command)
        return result

    @app.get(
        "/api/searches/{search_id}/runs/{run_id}",
        response_model=RunReadResponse,
        responses=errors,
        operation_id="readRun",
    )
    def read(search_id: str, run_id: str, credential: Credential) -> RunReadResponse:
        if credential is None:
            raise InvalidSession()
        try:
            query = ReadRunRequest(search_id=search_id, run_id=run_id)
        except ValidationError:
            raise HTTPException(422) from None
        with session(database_settings) as connection:
            owned = require_search_access(connection, credential, query.search_id)
            snapshot = read_snapshot(
                connection, search_id=owned.search_id, run_id=query.run_id
            )
        return RunReadResponse(run=snapshot)

    @app.get(
        "/api/searches/{search_id}/runs/{run_id}/events",
        response_class=Response,
        response_model=None,
        responses={
            **errors,
            200: {
                "description": "Replay after the cursor, then follow committed run events.",
                "content": {
                    "text/event-stream": {
                        "schema": {"$ref": "#/components/schemas/RunEvent"}
                    }
                },
            },
            204: {"description": "Terminal run with no events after the cursor."},
        },
        operation_id="watchRun",
        openapi_extra={
            "parameters": [
                {
                    "name": "after_sequence",
                    "in": "query",
                    "required": False,
                    "description": "Initial cursor. Defaults to 0. One ASCII decimal value.",
                    "schema": {"type": "integer", "minimum": 0, "maximum": 2**63 - 1},
                },
                {
                    "name": "Last-Event-ID",
                    "in": "header",
                    "required": False,
                    "description": "Reconnect cursor. Overrides after_sequence when supplied.",
                    "schema": {"type": "string", "pattern": "^[0-9]{1,19}$"},
                },
            ]
        },
    )
    async def watch(
        request: Request, search_id: str, run_id: str, credential: Credential
    ) -> Response:
        if credential is None:
            raise InvalidSession()
        query = watch_from_wire(
            search_id,
            run_id,
            request.query_params.getlist("after_sequence"),
            request.headers.getlist("last-event-id"),
        )
        first_page = await run_in_threadpool(
            read_watch_page, database_settings, credential, query
        )
        if terminal_and_caught_up(first_page, query.after_sequence):
            return Response(status_code=204)
        return EventStreamResponse(
            follow_events(database_settings, credential, query, first_page),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return app


def create_app_from_environment() -> FastAPI:
    return create_app(
        api_settings=load_api_settings(), database_settings=load_database_settings()
    )
