from __future__ import annotations

import argparse
import json

from fastapi import FastAPI
from fastapi.openapi.models import OpenAPI
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel
from pydantic.json_schema import JsonSchemaMode, JsonSchemaValue, models_json_schema

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
_SCHEMA_MODES: tuple[JsonSchemaMode, ...] = ("validation", "serialization")


def compose_openapi_document(app: FastAPI) -> JsonSchemaValue:
    document = get_openapi(title=app.title, version=app.version, routes=app.routes)
    _, graph = models_json_schema(
        [(model, mode) for model in _ROOT_MODELS for mode in _SCHEMA_MODES],
        ref_template="#/components/schemas/{model}",
    )
    retained = OpenAPI.model_validate(
        {
            "openapi": "3.1.0",
            "info": {"title": app.title, "version": app.version},
            "paths": {},
            "components": {"schemas": graph["$defs"]},
        }
    ).model_dump(by_alias=True, exclude_none=True)
    schemas = document.setdefault("components", {}).setdefault("schemas", {})
    for name, definition in retained["components"]["schemas"].items():
        if name in schemas and schemas[name] != definition:
            raise ValueError("incompatible_openapi_component")
        schemas[name] = definition
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
