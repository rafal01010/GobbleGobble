import argparse
import logging
import math
import sqlite3
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import partial
from time import sleep
from typing import Never
from uuid import uuid4

from app.core.calls import Receipt
from app.core.settings import (
    ConfigurationError,
    DatabaseSettings,
    load_database_settings,
)
from app.db.checkpoints import ContributionRecord, commit_checkpoint
from app.db.completion import NonResultOutcome, complete_without_result
from app.db.events import RunNotFound
from app.db.leases import (
    LeaseBusy,
    LeaseClaim,
    LeaseLost,
    RunNotClaimable,
    claim_run,
    renew_lease,
)
from app.db.migrations import MigrationError, require_current_schema
from app.db.session import session, transaction
from app.schemas.lifecycle import (
    CompletedState,
    InsufficientCoverageOutcome,
    RunSnapshot,
    RunStage,
    TechnicalFailureOutcome,
)

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckpointRecords:
    contributions: tuple[ContributionRecord, ...] = field(default=(), repr=False)
    receipts: tuple[Receipt, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class WorkerStep:
    stage: RunStage
    work: Callable[[], CheckpointRecords] = field(repr=False)


@dataclass(frozen=True)
class WorkerJob:
    steps: tuple[WorkerStep, ...] = field(repr=False)
    outcome: NonResultOutcome = field(repr=False)


def _complete(
    settings: DatabaseSettings, claim: LeaseClaim, outcome: NonResultOutcome
) -> RunSnapshot:
    with session(settings) as connection, transaction(connection):
        snapshot = complete_without_result(connection, claim, outcome=outcome)
    return snapshot


def execute_run(
    settings: DatabaseSettings,
    run_id: str,
    job: WorkerJob,
    *,
    worker_id: str,
    lease_seconds: int = 30,
) -> RunSnapshot:
    """Execute bounded job steps outside storage using the shared leased lifecycle."""
    with session(settings) as connection:
        require_current_schema(connection)
        with transaction(connection):
            claim = claim_run(connection, run_id, worker_id, lease_seconds)
    for step in job.steps:
        with session(settings) as connection, transaction(connection):
            claim = renew_lease(connection, claim, lease_seconds)
        try:
            records = step.work()
        except Exception:
            _logger.error(
                "worker_job_failed", exc_info=RuntimeError("worker_job_failed")
            )
            return _complete(
                settings,
                claim,
                TechnicalFailureOutcome(
                    code="worker_failure",
                    message="Worker execution failed.",
                    retryable=True,
                ),
            )
        with session(settings) as connection, transaction(connection):
            commit_checkpoint(
                connection,
                claim,
                stage=step.stage,
                contributions=records.contributions,
                receipts=records.receipts,
            )
    return _complete(settings, claim, job.outcome)


def _diagnostic_work(delay_seconds: float) -> CheckpointRecords:
    sleep(delay_seconds)
    return CheckpointRecords()


def diagnostic_job(*, delay_seconds: float = 1.0) -> WorkerJob:
    if (
        type(delay_seconds) not in (int, float)
        or not math.isfinite(delay_seconds)
        or not 0 <= delay_seconds <= 5
    ):
        raise ValueError("invalid_diagnostic_delay")
    work = partial(_diagnostic_work, float(delay_seconds))
    return WorkerJob(
        steps=(WorkerStep("composing", work), WorkerStep("publishing", work)),
        outcome=InsufficientCoverageOutcome(
            gaps=("Diagnostic lifecycle check only. No place research was performed.",)
        ),
    )


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        self.exit(2, "diagnostic_argument_error\n")


def _run_id(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("invalid_run_id")
    return value


def _delay(value: str) -> float:
    try:
        result = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("invalid_diagnostic_delay") from None
    if not math.isfinite(result) or not 0 <= result <= 5:
        raise argparse.ArgumentTypeError("invalid_diagnostic_delay")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = _Parser(
        description="Run an explicitly selected diagnostic job without external calls.",
        allow_abbrev=False,
        color=False,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    diagnostic = commands.add_parser(
        "diagnostic",
        help="Exercise the leased lifecycle for one admitted run. No food research.",
        allow_abbrev=False,
        color=False,
    )
    diagnostic.add_argument("--run-id", required=True, type=_run_id)
    diagnostic.add_argument("--step-delay", type=_delay, default=1.0, metavar="SECONDS")
    args = parser.parse_args(argv)
    try:
        job = diagnostic_job(delay_seconds=args.step_delay)
        settings = load_database_settings()
        print("diagnostic_worker_starting", flush=True)
        snapshot = execute_run(
            settings, args.run_id, job, worker_id=uuid4().hex, lease_seconds=30
        )
    except ConfigurationError:
        print("diagnostic_configuration_error", file=sys.stderr)
        return 1
    except RunNotFound:
        print("diagnostic_run_not_found", file=sys.stderr)
        return 3
    except LeaseBusy:
        print("diagnostic_lease_busy", file=sys.stderr)
        return 4
    except RunNotClaimable:
        print("diagnostic_run_not_claimable", file=sys.stderr)
        return 5
    except LeaseLost:
        print("diagnostic_lease_lost", file=sys.stderr)
        return 6
    except MigrationError:
        print("diagnostic_schema_error", file=sys.stderr)
        return 1
    except sqlite3.Error, OSError:
        print("diagnostic_storage_error", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("diagnostic_interrupted", file=sys.stderr)
        return 130
    except Exception:
        _logger.error(
            "diagnostic_internal_error",
            exc_info=RuntimeError("diagnostic_internal_error"),
        )
        return 1
    if isinstance(snapshot.state, CompletedState) and isinstance(
        snapshot.state.outcome, TechnicalFailureOutcome
    ):
        print("diagnostic_worker_failed", file=sys.stderr)
        return 1
    print("diagnostic_worker_completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
