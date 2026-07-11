"""Top-level entry points for invoking a Commission from outside.

`run_one` is the only way into a run, and `dispatch` is the only way around
inside one; each refuses the other's job. Every run gets one internal
control object (the Gatekeeper), created here, carried to every node by
reference inside the CallContext, consulted by every governed LLM call and
by `dispatch`. The caller configures it entirely through keyword arguments
below; it has no public name and is never handed out.

Routes through `dispatch` so run_id stamping, parent_run_id threading,
overflow enforcement, and persistence happen uniformly from the top.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

from vibrantine._gatekeeper import Gatekeeper, RunCancel, current_gatekeeper
from vibrantine.contract import (
    NEVER_CANCELLED,
    CallContext,
    CancelToken,
    CapabilitySet,
    Commission,
    CommissionResult,
    CostMetrics,
    ErrorState,
    PersistenceBackend,
    PersistenceMode,
    ProgressEvent,
    Provenance,
)
from vibrantine.dispatch import dispatch

# The always-on LLM-call backstop: high enough that no legitimate run has
# hit it, low enough that a runaway loop (including one on a free or local
# model the spend fuse cannot see) stops the same day it starts. None
# disables it, deliberately.
DEFAULT_MAX_LLM_CALLS = 1_000

# The room's default chair count: provider calls in flight across the whole
# tree. High enough not to throttle legitimate parallelism or trip provider
# rate limits in normal use.
DEFAULT_CONCURRENCY = 16


async def run_one[InputT, OutputT](
    commission: Commission[InputT, OutputT],
    input: InputT,
    *,
    # money: one number, two jobs (root grant AND spend fuse)
    budget_usd: float | None = None,
    # fuses
    max_llm_calls: int | None = DEFAULT_MAX_LLM_CALLS,
    time_limit_seconds: float | None = None,
    # the room
    concurrency: int = DEFAULT_CONCURRENCY,
    # authority
    capabilities: CapabilitySet | None = None,
    # control and observability
    cancel: CancelToken | None = None,
    on_progress: Callable[[ProgressEvent], None] | None = None,
    backend: PersistenceBackend | None = None,
    record: PersistenceMode | None = None,
) -> CommissionResult[OutputT]:
    """Run one Commission as a complete, governed run.

    Hello world stays one line: `run_one(commission, input)` gets the
    call-count backstop, a room of 16, no budget, no deadline. `budget_usd`
    is one number doing two jobs: the root's allocated grant (debited down
    the tree as today) and the run's spend fuse (a running observed total
    used only as a trip, never as a ledger nodes read). A fuse trip flips
    the run's one stop signal (the same cancellation path every node already
    checks), refuses new provider calls, lets in-flight calls finish and
    count, and surfaces at the root as a `run_halted` failure whose cost
    field reports true total spend. Returns the Commission's
    CommissionResult unchanged otherwise: errors surface as ErrorState in
    the result, not as raised exceptions.

    Called inside a run, this refuses: a nested run_one would spawn a fresh
    run object and silently escape every fuse. Any Commission that works as
    a principal works as a child through `dispatch`, same run, no special
    design.
    """
    if current_gatekeeper.get() is not None:
        raise RuntimeError(
            "run_one was called inside a run; a nested run_one would spawn "
            "a fresh run object and silently escape every fuse. You are "
            "inside a run; use dispatch."
        )
    config_error = _config_error(
        budget_usd=budget_usd,
        max_llm_calls=max_llm_calls,
        time_limit_seconds=time_limit_seconds,
        concurrency=concurrency,
    )
    if config_error is not None:
        return _config_failure(commission, config_error)

    gatekeeper = Gatekeeper(
        max_llm_calls=max_llm_calls,
        time_limit_seconds=time_limit_seconds,
        spend_limit_usd=budget_usd,
        concurrency=concurrency,
    )
    ctx = CallContext(
        budget_usd=budget_usd,
        capabilities=capabilities if capabilities is not None else CapabilitySet(),
        cancel=RunCancel(cancel if cancel is not None else NEVER_CANCELLED, gatekeeper),
        on_progress=on_progress,
        backend=backend,
        record=record,
        _gatekeeper=gatekeeper,
    )
    token = current_gatekeeper.set(gatekeeper)
    try:
        result = await dispatch(commission, input, ctx)
    finally:
        current_gatekeeper.reset(token)

    # The root rewrite: only the root speaks run_halted; mid-tree nodes ride
    # the ordinary cancellation path. A root that still concluded (success,
    # or partial with usable output) keeps its result: winding down and
    # concluding with what it has is the designed response to a trip, not a
    # failure to override. The trip stays observable in the call log.
    if gatekeeper.tripped is not None and result.status == "failure":
        result = result.model_copy(
            update={
                "output": None,
                "error": gatekeeper.final_error(result.run_id),
                # True total spend, from the door's settled observations:
                # this is what makes "every dollar reported" checkable even
                # when parts of the tree were torn down mid-flight.
                "cost": CostMetrics(estimated_usd=gatekeeper.observed_spend_usd),
            }
        )
    return result


def invoke_sync[InputT, OutputT](
    commission: Commission[InputT, OutputT],
    input: InputT,
    *,
    budget_usd: float | None = None,
    max_llm_calls: int | None = DEFAULT_MAX_LLM_CALLS,
    time_limit_seconds: float | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    capabilities: CapabilitySet | None = None,
    cancel: CancelToken | None = None,
    on_progress: Callable[[ProgressEvent], None] | None = None,
    backend: PersistenceBackend | None = None,
    record: PersistenceMode | None = None,
) -> CommissionResult[OutputT]:
    """Sync wrapper over `run_one`. For scripts, REPL, smoke tests."""
    return asyncio.run(
        run_one(
            commission,
            input,
            budget_usd=budget_usd,
            max_llm_calls=max_llm_calls,
            time_limit_seconds=time_limit_seconds,
            concurrency=concurrency,
            capabilities=capabilities,
            cancel=cancel,
            on_progress=on_progress,
            backend=backend,
            record=record,
        )
    )


def _config_error(
    *,
    budget_usd: float | None,
    max_llm_calls: int | None,
    time_limit_seconds: float | None,
    concurrency: int,
) -> str | None:
    """A run configuration the Gatekeeper cannot honor, named; None when fine."""
    if concurrency < 1:
        return f"concurrency must be at least 1, got {concurrency}."
    if max_llm_calls is not None and max_llm_calls < 1:
        return (
            f"max_llm_calls must be at least 1, got {max_llm_calls}; "
            f"pass None to disable the call-count fuse."
        )
    if time_limit_seconds is not None and time_limit_seconds <= 0:
        return f"time_limit_seconds must be positive, got {time_limit_seconds}."
    if budget_usd is not None and budget_usd < 0:
        return f"budget_usd must be non-negative, got {budget_usd}."
    return None


def _config_failure[InputT, OutputT](
    commission: Commission[InputT, OutputT],
    detail: str,
) -> CommissionResult[OutputT]:
    """A validation failure result for a bad run configuration.

    Errors are values at this boundary too: nothing has run, so the failure
    is built here rather than raised.
    """
    return cast(
        CommissionResult[OutputT],
        CommissionResult(
            status="failure",
            error=ErrorState(kind="validation", detail=detail, retryable=False),
            provenance=Provenance(
                source=f"{commission.name}:run_one",
                fetched_at=datetime.now(UTC),
                confidence="grounded",
            ),
            cost=CostMetrics(estimated_usd=0.0),
        ),
    )
