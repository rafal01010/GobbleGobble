from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.schemas.recommendations import RecommendationCollection, VersionId
from app.schemas.search import NonBlankText

type RunId = NonBlankText
type AttemptId = NonBlankText
type CommandId = NonBlankText
type EventSequence = Annotated[int, Field(strict=True, ge=1)]
type EventCursor = Annotated[int, Field(strict=True, ge=0)]
type Limitations = Annotated[tuple[NonBlankText, ...], Field(min_length=1)]
type OperationKind = Literal["initial", "food_choice", "all_options"]
type RunStage = Literal[
    "starting", "discovering", "researching", "composing", "publishing", "recovering"
]
type FailureCode = Literal[
    "provider_unavailable",
    "model_unavailable",
    "execution_limit",
    "worker_failure",
    "internal_error",
]


class _Model(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class ReadyOutcome(_Model):
    kind: Literal["ready"] = "ready"
    version_id: VersionId = Field(repr=False)


class PartialOutcome(_Model):
    kind: Literal["partial"] = "partial"
    version_id: VersionId = Field(repr=False)
    limitations: Limitations = Field(repr=False)


class NoMatchesOutcome(_Model):
    kind: Literal["no_matches"] = "no_matches"
    assessed_scope: NonBlankText = Field(repr=False)
    explanation: NonBlankText = Field(repr=False)


class InsufficientCoverageOutcome(_Model):
    kind: Literal["insufficient_coverage"] = "insufficient_coverage"
    gaps: Limitations = Field(repr=False)


class TechnicalFailureOutcome(_Model):
    kind: Literal["technical_failure"] = "technical_failure"
    code: FailureCode
    message: NonBlankText = Field(repr=False)
    retryable: bool


type RunOutcome = Annotated[
    ReadyOutcome
    | PartialOutcome
    | NoMatchesOutcome
    | InsufficientCoverageOutcome
    | TechnicalFailureOutcome,
    Field(discriminator="kind"),
]


class QueuedState(_Model):
    status: Literal["queued"] = "queued"


class RunningState(_Model):
    status: Literal["running"] = "running"
    stage: RunStage


class CancellingState(_Model):
    status: Literal["cancelling"] = "cancelling"


class CompletedState(_Model):
    status: Literal["completed"] = "completed"
    outcome: RunOutcome = Field(repr=False)


class CancelledState(_Model):
    status: Literal["cancelled"] = "cancelled"


type RunState = Annotated[
    QueuedState | RunningState | CancellingState | CompletedState | CancelledState,
    Field(discriminator="status"),
]


class RunSnapshot(_Model):
    search_id: NonBlankText = Field(repr=False)
    run_id: RunId = Field(repr=False)
    operation: OperationKind
    attempt_id: AttemptId | None = Field(repr=False)
    state: RunState = Field(repr=False)
    last_event_sequence: EventCursor

    @model_validator(mode="after")
    def attempt_matches_state(self) -> RunSnapshot:
        if isinstance(self.state, QueuedState) and self.attempt_id is not None:
            raise ValueError("queued state has no current attempt")
        if (
            isinstance(self.state, RunningState | CompletedState)
            and self.attempt_id is None
        ):
            raise ValueError("running or completed state requires an attempt")
        return self


class RunReadResponse(_Model):
    """Validate supplied results for this run's display context, not global admission."""

    run: RunSnapshot = Field(repr=False)
    recommendations: RecommendationCollection | None = Field(default=None, repr=False)
    display_version_id: VersionId | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def displayed_result(self) -> RunReadResponse:
        versions = {}
        if self.recommendations is not None:
            if self.recommendations.search_id != self.run.search_id:
                raise ValueError("recommendation collection belongs to another search")
            versions = {
                version.version_id: version for version in self.recommendations.versions
            }
        if self.display_version_id is not None:
            version = versions.get(self.display_version_id)
            if version is None:
                raise ValueError(
                    "display version is absent from supplied recommendations"
                )
            if not (version.primaries or version.runners_up):
                raise ValueError("display version requires nonempty recommendations")
        if (
            isinstance(self.run.state, CompletedState)
            and isinstance(self.run.state.outcome, ReadyOutcome | PartialOutcome)
            and self.display_version_id != self.run.state.outcome.version_id
        ):
            raise ValueError("successful outcome must match the display version")
        return self


class QueuedEvent(_Model):
    kind: Literal["queued"] = "queued"


class ProgressEvent(_Model):
    kind: Literal["progress"] = "progress"
    stage: RunStage


class CancellingEvent(_Model):
    kind: Literal["cancelling"] = "cancelling"


class CompletedEvent(_Model):
    kind: Literal["completed"] = "completed"
    outcome: RunOutcome = Field(repr=False)


class CancelledEvent(_Model):
    kind: Literal["cancelled"] = "cancelled"


type EventPayload = Annotated[
    QueuedEvent | ProgressEvent | CancellingEvent | CompletedEvent | CancelledEvent,
    Field(discriminator="kind"),
]


class RunEvent(_Model):
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    search_id: NonBlankText = Field(repr=False)
    run_id: RunId = Field(repr=False)
    attempt_id: AttemptId | None = Field(repr=False)
    sequence: EventSequence
    occurred_at: AwareDatetime = Field(repr=False)
    result_version_id: VersionId | None = Field(repr=False)
    payload: EventPayload = Field(repr=False)

    @model_validator(mode="after")
    def payload_identity(self) -> RunEvent:
        if isinstance(self.payload, QueuedEvent) and self.attempt_id is not None:
            raise ValueError("queued event has no current attempt")
        if (
            isinstance(self.payload, ProgressEvent | CompletedEvent)
            and self.attempt_id is None
        ):
            raise ValueError("progress or completed event requires an attempt")
        expected_version = None
        if isinstance(self.payload, CompletedEvent) and isinstance(
            self.payload.outcome, ReadyOutcome | PartialOutcome
        ):
            expected_version = self.payload.outcome.version_id
        if self.result_version_id != expected_version:
            raise ValueError("event result version differs from its published outcome")
        return self


class EventBatch(_Model):
    search_id: NonBlankText = Field(repr=False)
    run_id: RunId = Field(repr=False)
    events: tuple[RunEvent, ...] = Field(repr=False)

    @model_validator(mode="after")
    def ordered_same_run(self) -> EventBatch:
        previous = 0
        for event in self.events:
            if event.search_id != self.search_id or event.run_id != self.run_id:
                raise ValueError("event batch mixes search or run identities")
            if event.sequence <= previous:
                raise ValueError("event sequences must be strictly increasing")
            previous = event.sequence
        return self
