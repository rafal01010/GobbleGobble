from __future__ import annotations

import argparse
import json

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


def build_openapi_document() -> JsonSchemaValue:
    _, schema = models_json_schema(
        [(model, mode) for model in _ROOT_MODELS for mode in _SCHEMA_MODES],
        ref_template="#/components/schemas/{model}",
    )
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "GobbleGobble backend contract",
            "version": "0.1.0",
            "description": (
                "Payload schemas only. No HTTP routes are implemented or declared."
            ),
        },
        "paths": {},
        "components": {"schemas": schema["$defs"]},
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.schema_export",
        description="Write the schema-only OpenAPI 3.1 contract as JSON to stdout.",
    )
    parser.parse_args()
    print(json.dumps(build_openapi_document(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
