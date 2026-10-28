from __future__ import annotations

import argparse
import json

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute
from pydantic import BaseModel
from pydantic.json_schema import JsonSchemaValue

from app.schemas.lifecycle import EventBatch, RunEvent, RunReadResponse
from app.schemas.operations import (
    CancelRunRequest,
    CancelRunResponse,
    ChooseFoodRequest,
    CommandResponse,
    ErrorResponse,
    ReadRunRequest,
    ResolveLocationRequest,
    ResolveLocationResponse,
    RetryRunRequest,
    StartRunRequest,
    WatchRunRequest,
)

_ROOT_MODELS: tuple[type[BaseModel], ...] = (
    ResolveLocationRequest,
    ResolveLocationResponse,
    StartRunRequest,
    ReadRunRequest,
    WatchRunRequest,
    ChooseFoodRequest,
    RetryRunRequest,
    CancelRunRequest,
    CommandResponse,
    CancelRunResponse,
    ErrorResponse,
    RunReadResponse,
    RunEvent,
    EventBatch,
)


def compose_openapi_document(app: FastAPI) -> JsonSchemaValue:
    anchors = []
    for model in _ROOT_MODELS:

        def anchor(body: object) -> None:
            pass

        anchor.__annotations__["body"] = model
        anchors.append(
            APIRoute(
                f"/__contract__/{model.__name__}",
                anchor,
                methods=["POST"],
                response_model=model,
                operation_id=f"contract{model.__name__}",
            )
        )
    # One schema graph keeps shared input/output aliases consistent across roots.
    document = get_openapi(
        title=app.title, version=app.version, routes=[*app.routes, *anchors]
    )
    for route in anchors:
        del document["paths"][route.path]
    _require_resolved_references(document)
    return document


def _require_resolved_references(document: JsonSchemaValue) -> None:
    def visit(value: object) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str):
                if not reference.startswith("#/"):
                    raise ValueError("external_openapi_reference")
                target = document
                for part in reference[2:].split("/"):
                    key = part.replace("~1", "/").replace("~0", "~")
                    if not isinstance(target, dict) or key not in target:
                        raise ValueError("unresolved_openapi_reference")
                    target = target[key]
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document)


def build_openapi_document() -> JsonSchemaValue:
    from app.api.application import create_app
    from app.core.settings import ApiSettings, DatabaseSettings

    app = create_app(
        api_settings=ApiSettings.model_construct(),
        database_settings=DatabaseSettings.model_construct(),
    )
    return app.openapi()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.schema_export",
        description="Write the backend OpenAPI 3.1 contract as JSON to stdout.",
    )
    parser.parse_args()
    print(json.dumps(build_openapi_document(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
