from app.schemas.lifecycle import (
    CancelledState,
    CancellingState,
    CompletedEvent,
    CompletedState,
    InsufficientCoverageOutcome,
    NoMatchesOutcome,
    PartialOutcome,
    QueuedState,
    ReadyOutcome,
    RunEvent,
    RunningState,
    RunOutcome,
    RunSnapshot,
    TechnicalFailureOutcome,
)

_STAGE_MESSAGES = {
    "starting": "Getting your search ready.",
    "discovering": "Looking for places nearby.",
    "researching": "Checking places and what people say about them.",
    "composing": "Choosing places for you.",
    "publishing": "Preparing your results.",
    "recovering": "Continuing your search.",
}


def progress_message(run: RunSnapshot) -> str:
    state = run.state
    if isinstance(state, QueuedState):
        return "Your search is waiting to start."
    if isinstance(state, RunningState):
        return _STAGE_MESSAGES[state.stage]
    if isinstance(state, CancellingState):
        return "Stopping your search."
    if isinstance(state, CancelledState):
        return "Your search was stopped."
    assert isinstance(state, CompletedState)
    outcome = state.outcome
    if isinstance(outcome, ReadyOutcome):
        return "Your recommendations are ready."
    if isinstance(outcome, PartialOutcome):
        return "Your recommendations are ready, but some information is missing."
    if isinstance(outcome, NoMatchesOutcome):
        return "No matching places were found for this search."
    if isinstance(outcome, InsufficientCoverageOutcome):
        return "We could not check enough places. Try searching again."
    assert isinstance(outcome, TechnicalFailureOutcome)
    return (
        "We could not finish your search. Try again."
        if outcome.retryable
        else "We could not finish your search. Try a new search later."
    )


def public_outcome(outcome: RunOutcome) -> RunOutcome:
    if isinstance(outcome, TechnicalFailureOutcome):
        message = (
            "We could not finish your search. Try again."
            if outcome.retryable
            else "We could not finish your search. Try a new search later."
        )
        return TechnicalFailureOutcome(
            code=outcome.code, message=message, retryable=outcome.retryable
        )
    if isinstance(outcome, InsufficientCoverageOutcome):
        return InsufficientCoverageOutcome(
            gaps=("We could not check enough places. Try searching again.",)
        )
    if isinstance(outcome, PartialOutcome):
        return PartialOutcome(
            version_id=outcome.version_id,
            limitations=(
                "Some information is available only temporarily."
                if outcome.limitations
                == ("Some information is available only temporarily.",)
                else "Some information is unavailable.",
            ),
        )
    if isinstance(outcome, NoMatchesOutcome):
        return NoMatchesOutcome(
            assessed_scope="The area and travel limit you selected.",
            explanation="No matching places were found for this search.",
        )
    return outcome


def public_snapshot(snapshot: RunSnapshot) -> RunSnapshot:
    if not isinstance(snapshot.state, CompletedState):
        return snapshot
    return snapshot.model_copy(
        update={"state": CompletedState(outcome=public_outcome(snapshot.state.outcome))}
    )


def public_event(event: RunEvent) -> RunEvent:
    if not isinstance(event.payload, CompletedEvent):
        return event
    return event.model_copy(
        update={
            "payload": CompletedEvent(outcome=public_outcome(event.payload.outcome))
        }
    )
