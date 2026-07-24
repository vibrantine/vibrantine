"""Top-level entry points for invoking a Commission from outside.

`run_commission` is the only way into a run, and `dispatch` is the only way around
inside one; each refuses the other's job. Every run gets one internal
control object (the Gatekeeper), created here, carried to every node by
reference inside the CallContext, consulted by every governed LLM call and
by `dispatch`. The caller configures it entirely through keyword arguments
below; it has no public name and is never handed out.

Routes through `dispatch` so run_id stamping, parent_run_id threading,
overflow enforcement, and persistence happen uniformly from the top.
"""

import asyncio
import logging
import math
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, cast

from vibrantine._gatekeeper import (
    Gatekeeper,
    RunCancel,
    RunConfigError,
    build_catalog,
    current_gatekeeper,
)
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
from vibrantine.models import Model

logger = logging.getLogger(__name__)

# The always-on LLM-call backstop: high enough that no legitimate run has
# hit it, low enough that a runaway loop (including one on a free or local
# model the spend fuse cannot see) stops the same day it starts. None
# disables it, deliberately.
DEFAULT_MAX_LLM_CALLS = 1_000

# The room's default chair count: provider calls in flight across the whole
# tree. High enough not to throttle legitimate parallelism or trip provider
# rate limits in normal use.
DEFAULT_CONCURRENCY = 16


async def run_commission[InputT, OutputT](
    commission: Commission[InputT, OutputT],
    input: InputT,
    *,
    # money: one number, two jobs (root grant AND spend fuse)
    budget_usd: float | None = None,
    # the model catalog: defined once, referenced by name from every node
    models: Sequence[Model] = (),
    default_model: str | None = None,
    # fuses
    max_llm_calls: int | None = DEFAULT_MAX_LLM_CALLS,
    time_limit_seconds: float | None = None,
    # the room
    concurrency: int = DEFAULT_CONCURRENCY,
    # authority
    tool_ceiling: Sequence[str] | None = None,
    capabilities: CapabilitySet | None = None,
    # control and observability
    cancel: CancelToken | None = None,
    on_progress: Callable[[ProgressEvent], None] | None = None,
    on_llm_call: Callable[[dict[str, Any]], None] | None = None,
    on_dispatch: Callable[[dict[str, Any]], None] | None = None,
    backend: PersistenceBackend | None = None,
    record: PersistenceMode | None = None,
) -> CommissionResult[OutputT]:
    """Run one Commission as a complete, governed run.

    Hello world stays one line: `run_commission(commission, input)` gets the system
    default model, the call-count backstop, a room of 16, no budget, no
    deadline. `models=` is the run's catalog: define each model once; every
    Commission references an entry by name or takes the run default (the
    single entry when there is exactly one, else the system default unless
    `default_model=` names another; unknown names fail fast). `budget_usd`
    is one number doing two jobs: the root's allocated grant (debited down
    the tree as today) and the run's spend fuse (a running observed total
    used only as a trip, never as a ledger nodes read). A fuse trip flips
    the run's one stop signal (the same cancellation path every node already
    checks), refuses new provider calls, lets in-flight calls finish and
    count, and surfaces at the root as a `run_halted` failure whose cost
    field reports true total spend. Returns the Commission's
    CommissionResult unchanged otherwise: errors surface as ErrorState in
    the result, not as raised exceptions.

    Called inside a run, this refuses: a nested run_commission would spawn a fresh
    run object and silently escape every fuse. Any Commission that works as
    a principal works as a child through `dispatch`, same run, no special
    design.
    """
    if current_gatekeeper.get() is not None:
        raise RuntimeError(
            "run_commission was called inside a run; a nested run_commission would spawn "
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
    try:
        catalog, default = build_catalog(models, default_model)
    except RunConfigError as exc:
        return _config_failure(commission, str(exc))

    gatekeeper = Gatekeeper(
        catalog=catalog,
        default_model=default,
        max_llm_calls=max_llm_calls,
        time_limit_seconds=time_limit_seconds,
        spend_limit_usd=budget_usd,
        concurrency=concurrency,
        # An LLM-exposure ceiling, tree-wide and immutable: the effective
        # menu everywhere becomes toolbox ∩ branch grant ∩ this set. It
        # bounds what a model may be offered; branch-varying authority stays
        # with capabilities, the distributed grant.
        tool_ceiling=None if tool_ceiling is None else frozenset(tool_ceiling),
        # The provider-call log's live accessor: one dict per settled or
        # refused call. Retrieval after the run is `rows = []` plus
        # `on_llm_call=rows.append`; no new public type.
        on_call=on_llm_call,
        # The dispatch register's live twin: one dict per finished or
        # refused dispatch, the run's node ledger as it grows.
        on_dispatch=on_dispatch,
        # Persistence is application run policy retained by the private
        # runtime. It never enters the Commission-facing CallContext.
        backend=backend,
        record=record,
    )
    ctx = CallContext(
        budget_usd=budget_usd,
        capabilities=capabilities if capabilities is not None else CapabilitySet(),
        cancel=RunCancel(cancel if cancel is not None else NEVER_CANCELLED, gatekeeper),
        on_progress=on_progress,
        _gatekeeper=gatekeeper,
    )
    token = current_gatekeeper.set(gatekeeper)
    try:
        result = await dispatch(commission, input, ctx)
    finally:
        current_gatekeeper.reset(token)
        # The run vended its provider clients; the run closes them. After
        # dispatch returns, every in-flight call has settled, so nothing
        # still needs a connection.
        await gatekeeper.aclose()

    # The run_halted rewrite happens inside the root's own dispatch (causal,
    # trip-descended failures only, before the record is persisted), so the
    # result returned here already tells the final story.

    return result


def run_commission_sync[InputT, OutputT](
    commission: Commission[InputT, OutputT],
    input: InputT,
    *,
    budget_usd: float | None = None,
    models: Sequence[Model] = (),
    default_model: str | None = None,
    max_llm_calls: int | None = DEFAULT_MAX_LLM_CALLS,
    time_limit_seconds: float | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    tool_ceiling: Sequence[str] | None = None,
    capabilities: CapabilitySet | None = None,
    cancel: CancelToken | None = None,
    on_progress: Callable[[ProgressEvent], None] | None = None,
    on_llm_call: Callable[[dict[str, Any]], None] | None = None,
    on_dispatch: Callable[[dict[str, Any]], None] | None = None,
    backend: PersistenceBackend | None = None,
    record: PersistenceMode | None = None,
) -> CommissionResult[OutputT]:
    """Sync wrapper over `run_commission`. For scripts, REPL, smoke tests."""
    return asyncio.run(
        run_commission(
            commission,
            input,
            budget_usd=budget_usd,
            models=models,
            default_model=default_model,
            max_llm_calls=max_llm_calls,
            time_limit_seconds=time_limit_seconds,
            concurrency=concurrency,
            tool_ceiling=tool_ceiling,
            capabilities=capabilities,
            cancel=cancel,
            on_progress=on_progress,
            on_llm_call=on_llm_call,
            on_dispatch=on_dispatch,
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
    if not _is_positive_int(concurrency):
        return f"concurrency must be a positive integer, got {concurrency}."
    if max_llm_calls is not None and not _is_positive_int(max_llm_calls):
        return (
            f"max_llm_calls must be a positive integer, got {max_llm_calls}; "
            f"pass None to disable the call-count fuse."
        )
    if time_limit_seconds is not None and (
        not math.isfinite(time_limit_seconds) or time_limit_seconds <= 0
    ):
        return f"time_limit_seconds must be positive, got {time_limit_seconds}."
    if budget_usd is not None and (not math.isfinite(budget_usd) or budget_usd < 0):
        return f"budget_usd must be non-negative, got {budget_usd}."
    return None


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


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
                source=f"{commission.name}:run_commission",
                fetched_at=datetime.now(UTC),
                confidence="grounded",
            ),
            cost=CostMetrics(estimated_usd=0.0),
        ),
    )
