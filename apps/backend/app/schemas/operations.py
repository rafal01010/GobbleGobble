from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.lifecycle import (
    AttemptId,
    CancelledState,
    CancellingState,
    CommandId,
    CompletedState,
    EventCursor,
    RunId,
    RunSnapshot,
)
from app.schemas.recommendations import OfferedFoodChoiceRef, VersionId
from app.schemas.search import Coordinates, NonBlankText, SearchOrigin, SearchRequest


class _Model(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class CurrentLocationInput(_Model):
    kind: Literal["current"] = "current"
    coordinates: Coordinates = Field(repr=False)


class ChosenLocationInput(_Model):
    kind: Literal["chosen"] = "chosen"
    query: NonBlankText = Field(repr=False)


type LocationInput = Annotated[
    CurrentLocationInput | ChosenLocationInput, Field(discriminator="kind")
]


class ResolveLocationRequest(_Model):
    location: LocationInput = Field(repr=False)


class ResolvedLocationOutcome(_Model):
    status: Literal["resolved"] = "resolved"
    origin: SearchOrigin = Field(repr=False)


class LocationNoMatchOutcome(_Model):
    status: Literal["no_match"] = "no_match"
    message: NonBlankText = Field(repr=False)


class LocationUnavailableOutcome(_Model):
    status: Literal["provider_unavailable"] = "provider_unavailable"
    message: NonBlankText = Field(repr=False)
    retryable: bool


type LocationResolutionOutcome = Annotated[
    ResolvedLocationOutcome | LocationNoMatchOutcome | LocationUnavailableOutcome,
    Field(discriminator="status"),
]


class ResolveLocationResponse(_Model):
    outcome: LocationResolutionOutcome = Field(repr=False)


class StartRunRequest(_Model):
    command_id: CommandId = Field(repr=False)
    request: SearchRequest = Field(repr=False)


class ReadRunRequest(_Model):
    search_id: NonBlankText = Field(repr=False)
    run_id: RunId = Field(repr=False)


class WatchRunRequest(ReadRunRequest):
    after_sequence: EventCursor = 0


class FoodChoiceSelection(_Model):
    kind: Literal["food_choice"] = "food_choice"
    choice: OfferedFoodChoiceRef = Field(repr=False)


class AllOptionsSelection(_Model):
    kind: Literal["all_options"] = "all_options"
    parent_version_id: VersionId = Field(repr=False)


type ChoiceSelection = Annotated[
    FoodChoiceSelection | AllOptionsSelection, Field(discriminator="kind")
]


class ChooseFoodRequest(ReadRunRequest):
    command_id: CommandId = Field(repr=False)
    selection: ChoiceSelection = Field(repr=False)


class RetryRunRequest(ReadRunRequest):
    command_id: CommandId = Field(repr=False)
    expected_attempt_id: AttemptId | None = Field(repr=False)


class CancelRunRequest(ReadRunRequest):
    command_id: CommandId = Field(repr=False)
    expected_attempt_id: AttemptId | None = Field(repr=False)


class AcceptedCommand(_Model):
    disposition: Literal["accepted"] = "accepted"
    command_id: CommandId = Field(repr=False)
    run: RunSnapshot = Field(repr=False)

    @model_validator(mode="after")
    def queued_admission(self) -> AcceptedCommand:
        if self.run.state.status != "queued":
            raise ValueError("first command admission requires queued state")
        return self


class ReplayedCommand(_Model):
    disposition: Literal["replayed"] = "replayed"
    command_id: CommandId = Field(repr=False)
    run: RunSnapshot = Field(repr=False)


type CommandDisposition = Annotated[
    AcceptedCommand | ReplayedCommand, Field(discriminator="disposition")
]


class CommandResponse(_Model):
    command: CommandDisposition = Field(repr=False)


class CancelRunResponse(_Model):
    command_id: CommandId = Field(repr=False)
    disposition: Literal[
        "requested", "already_cancelling", "already_cancelled", "already_completed"
    ]
    run: RunSnapshot = Field(repr=False)

    @model_validator(mode="after")
    def cancellation_disposition(self) -> CancelRunResponse:
        match self.disposition:
            case "requested" | "already_cancelling":
                valid = isinstance(self.run.state, CancellingState)
            case "already_cancelled":
                valid = isinstance(self.run.state, CancelledState)
            case "already_completed":
                valid = isinstance(self.run.state, CompletedState)
        if not valid:
            raise ValueError("cancel disposition contradicts current run state")
        return self


type ErrorCode = Literal[
    "invalid_request",
    "not_found",
    "command_conflict",
    "stale_version",
    "invalid_state",
    "attempt_conflict",
    "unavailable",
]


class OperationError(_Model):
    code: ErrorCode
    message: NonBlankText = Field(repr=False)
    retryable: bool


class ErrorResponse(_Model):
    error: OperationError = Field(repr=False)
