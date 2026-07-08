"""Dispatch helper for invoking Commissions through the framework.

Every Commission invocation that crosses a contract boundary (top-level
from `run_one`, child invocations from Python coordinators, tool
dispatches from `run_llm_loop` and the LLM-tool wrapper) calls
`dispatch`. This is where:

  - a `run_id` (UUID4) is generated for the call
  - `parent_run_id` is threaded automatically via `ContextVar` (so
    `asyncio.gather` over child dispatches keeps the chain correct)
  - `overflow_policy` is enforced on the returned result
  - recording is decided and honored: the node's explicit
    `persistence_mode` wins, a node with no opinion (None) follows the
    caller's `CallContext.record` default, and the record is written
    through `CallContext.backend` if one is wired
  - the LLM loop's transcript is collected: dispatch hangs a context-local
    trace mailbox before calling `_run`, `run_llm_loop` deposits its
    message history into it on the way out, and whatever landed is written
    to `PersistedRecord.llm_trace`. Same ContextVar mechanism as the run_id
    chain, so nesting and `asyncio.gather` keep every trace with its own
    run. The trace serves the recorder, never the caller: it lands only in
    the record, and a parent Commission sees nothing but the child's result.
  - a raised exception from a misbehaving `_run` is converted to an
    `internal` failure result, so errors-as-values holds at the boundary
    even when a Commission breaks the contract

`Commission._run` is the author's override hook; this module is its one
sanctioned caller. The underscore is what keeps everyone else routing
through here.
"""

import contextvars
import logging
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import BaseModel

from vibrantine.contract import (
    CallContext,
    Commission,
    CommissionResult,
    CommissionStatus,
    CostMetrics,
    ErrorState,
    PersistedRecord,
    PersistenceMode,
    ProgressEvent,
    Provenance,
    estimate_tokens,
)

# Standard library logging: the framework emits, the application decides the
# volume (e.g. `logging.basicConfig(level=logging.INFO)` for a console view).
# One line per completed call at INFO; call starts at DEBUG; contract breaches
# and persistence trouble at WARNING. No handlers are installed here, ever.
logger = logging.getLogger(__name__)

_current_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_vibrantine_current_run_id",
    default=None,
)

# The trace mailbox: one fresh box per dispatched call, context-local so
# nested and gathered runs each see only their own. `None` outside any
# dispatch, so a deposit from an unwrapped run goes nowhere, harmlessly.
_trace_box: contextvars.ContextVar[list[list[dict[str, Any]]] | None] = contextvars.ContextVar(
    "_vibrantine_trace_box",
    default=None,
)


def deposit_llm_trace(messages: list[dict[str, Any]]) -> None:
    """Drop an LLM message history into the current call's trace mailbox.

    `run_llm_loop` calls this on its way out (success or failure); a custom
    Commission that runs its own LLM calls may do the same to have its
    transcript recorded. The trace serves the recorder, never the caller: a
    deposit lands only in this call's own `PersistedRecord.llm_trace`, and
    outside any dispatch it goes nowhere, harmlessly.
    """
    box = _trace_box.get()
    if box is not None:
        box.append(messages)


async def dispatch[InputT, OutputT](
    commission: Commission[InputT, OutputT],
    input: InputT,
    ctx: CallContext,
) -> CommissionResult[OutputT]:
    """Invoke `commission` with framework wrapping."""
    parent = _current_run_id.get()
    my_run_id = str(uuid.uuid4())

    # The Commission body sees its parent's run_id via ctx; it does not see
    # its own (dispatch stamps that onto the result after _run returns).
    ctx_for_run = replace(ctx, parent_run_id=parent)

    # A fresh mailbox for this call; whatever the interior deposits (the LLM
    # loop's transcript, on success or failure) is collected after _run and
    # written to the record. The finally re-hangs the caller's box, so a
    # deposit from a parent's own loop still lands with the parent.
    box: list[list[dict[str, Any]]] = []
    trace_token = _trace_box.set(box)
    token = _current_run_id.set(my_run_id)
    logger.debug("%s started run_id=%s parent=%s", commission.name, my_run_id, parent)
    try:
        # Dispatch is the hook's one sanctioned caller; the protected access
        # is the design, not a shortcut.
        result = await commission._run(input, ctx_for_run)  # pyright: ignore[reportPrivateUsage]
    except Exception as exc:
        # Errors are values: a Commission that *raises* instead of returning a
        # failure (a custom-_run bug, a third-party Commission) has broken the
        # contract. Convert it here so the exception can't escape `run_one`, and
        # so the failure still flows through stamping + persistence below.
        # CancelledError is a BaseException, not an Exception; task
        # cancellation deliberately propagates rather than being swallowed.
        logger.warning(
            "%s raised %s (converted to a failure result): %s",
            commission.name,
            type(exc).__name__,
            exc,
        )
        result = _exception_to_failure(commission, exc)
    finally:
        _current_run_id.reset(token)
        _trace_box.reset(trace_token)

    # A custom _run may run several LLM loops in sequence; the raw-JSON v1
    # trace is their message histories concatenated in run order.
    llm_trace = [message for deposit in box for message in deposit] or None

    # Stamp before the overflow policy runs, so a truncate_with_reference
    # detail can name the run_id the full output is persisted under.
    result = result.model_copy(update={"run_id": my_run_id, "parent_run_id": parent})
    result, full_result = _apply_overflow_policy(result, commission, ctx_for_run)
    logger.info(
        "%s finished status=%s cost=$%.6f run_id=%s",
        commission.name,
        result.status,
        result.cost.estimated_usd,
        my_run_id,
    )

    # Effective recording mode: the node's explicit persistence_mode wins;
    # a node with no opinion (None) follows the caller's ctx.record default;
    # silence on both sides means off.
    mode = commission.persistence_mode
    if mode is None:
        mode = ctx.record if ctx.record is not None else "off"

    # truncate_with_reference forces this run's record on: the chopped
    # envelope references the full output by run_id, so the full (pre-chop)
    # result must be the one persisted or the reference points at nothing.
    # The record's mode is "always" because the policy, not the recording
    # configuration, demanded it.
    if full_result is not None:
        mode = "always"

    if ctx.backend is not None and _should_persist(mode, result.status):
        record = _build_record(
            run_id=my_run_id,
            parent_run_id=parent,
            commission=commission,
            mode=mode,
            input=input,
            result=full_result if full_result is not None else result,
            ctx=ctx_for_run,
            llm_trace=llm_trace,
        )
        try:
            await ctx.backend.store(record)
        except Exception as exc:
            # Persistence is observability, not the work itself: a failing
            # backend (disk full, a third-party implementation bug) must not
            # destroy the result it was recording or let an exception cross
            # the run_one boundary. Surface it through on_progress and the
            # log, and return the result anyway.
            logger.warning(
                "persistence backend failed for %s run_id=%s: %s: %s",
                commission.name,
                my_run_id,
                type(exc).__name__,
                exc,
            )
            if ctx.on_progress is not None:
                ctx.on_progress(
                    ProgressEvent(
                        commission_name=commission.name,
                        phase="persist_failed",
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                )
            if full_result is not None:
                # The chopped envelope's reference now dangles; returning it
                # would silently lose data. Fall back to the full output as
                # partial, the same non-breaking degradation the policy uses
                # everywhere it cannot complete its mechanic.
                result = _overflow_degrade(
                    full_result,
                    _estimate_output_tokens(full_result.output),
                    cast(int, commission.max_output_tokens),
                    f"the persistence backend failed while storing the full "
                    f"version ({type(exc).__name__}: {exc})",
                )

    return result


# --- Exception handling ---------------------------------------------------


def _exception_to_failure[OutputT](
    commission: Commission[Any, OutputT],
    exc: Exception,
) -> CommissionResult[OutputT]:
    """Convert a raised exception into a structured `internal` failure.

    Upholding errors-as-values is the author's job, but dispatch is the seam
    that *guarantees* it: a raising `_run` becomes a failure result rather
    than propagating out of `run_one`. Cost is reported as $0; any spend
    before the raise unwound with the stack and is unrecoverable here; the real
    remedy is Commissions returning failures instead of raising.
    """
    return cast(
        CommissionResult[OutputT],
        CommissionResult(
            status="failure",
            error=ErrorState(
                kind="internal",
                detail=(f"Commission {commission.name!r} raised {type(exc).__name__}: {exc}"),
                retryable=False,
            ),
            provenance=Provenance(
                source=f"{commission.name}:dispatch",
                fetched_at=datetime.now(UTC),
                confidence="grounded",
            ),
            cost=CostMetrics(estimated_usd=0.0),
        ),
    )


# --- Persistence policy ---------------------------------------------------


def _should_persist(mode: PersistenceMode, status: CommissionStatus) -> bool:
    if mode == "off":
        return False
    if mode == "on_failure":
        return status in ("failure", "partial")
    return True  # dev, always


def _build_record[InputT, OutputT](
    *,
    run_id: str,
    parent_run_id: str | None,
    commission: Commission[InputT, OutputT],
    mode: PersistenceMode,
    input: InputT,
    result: CommissionResult[OutputT],
    ctx: CallContext,
    llm_trace: list[dict[str, Any]] | None,
) -> PersistedRecord:
    return PersistedRecord(
        run_id=run_id,
        parent_run_id=parent_run_id,
        commission_name=commission.name,
        mode=mode,
        created_at=datetime.now(UTC),
        input=_serialize(input),
        result=result.model_dump(),
        ctx_snapshot={
            "budget_usd": ctx.budget_usd,
            "capabilities": (
                None if ctx.capabilities.tools is None else sorted(ctx.capabilities.tools)
            ),
            "concurrency": ctx.concurrency,
            "parent_run_id": ctx.parent_run_id,
        },
        llm_trace=llm_trace,
    )


def _serialize(value: Any) -> dict[str, Any]:
    """Pydantic if it can, opaque dict fallback otherwise."""
    if isinstance(value, BaseModel):
        return value.model_dump()
    return {"value": str(value)}


# --- Overflow policy ------------------------------------------------------


def _apply_overflow_policy[InputT, OutputT](
    result: CommissionResult[OutputT],
    commission: Commission[InputT, OutputT],
    ctx: CallContext,
) -> tuple[CommissionResult[OutputT], CommissionResult[OutputT] | None]:
    """Enforce the Commission's output cap on the result.

    Returns `(result_to_return, full_result_to_persist)`. The second element
    is non-None only when `truncate_with_reference` chopped: it is the full,
    pre-chop result that dispatch must persist under this run's run_id, or
    the reference embedded in the returned envelope points at nothing.
    """
    output = result.output
    if commission.max_output_tokens is None or output is None:
        return result, None

    estimated = _estimate_output_tokens(output)
    if estimated <= commission.max_output_tokens:
        return result, None

    cap = commission.max_output_tokens
    policy = commission.overflow_policy

    if policy == "reject":
        return result.model_copy(
            update={
                "status": "failure",
                "output": None,
                "error": ErrorState(
                    kind="output_too_large",
                    detail=(f"Output of ~{estimated} tokens exceeds cap of {cap}."),
                    retryable=False,
                ),
            }
        ), None
    if policy == "partial":
        return result.model_copy(
            update={
                "status": "partial",
                "error": ErrorState(
                    kind="output_too_large",
                    detail=(
                        f"Output of ~{estimated} tokens exceeds cap of {cap}; returned as partial."
                    ),
                    retryable=False,
                ),
            }
        ), None
    if policy == "flag":
        if ctx.on_progress is not None:
            ctx.on_progress(
                ProgressEvent(
                    commission_name=commission.name,
                    phase="output_overflow",
                    detail=f"~{estimated} tokens / cap {cap}",
                )
            )
        return result, None
    if policy == "truncate_with_reference":
        return _truncate_with_reference(result, output, commission, ctx, estimated, cap)
    return result, None  # unreachable; appeases exhaustiveness checks


def _truncate_with_reference[InputT, OutputT](
    result: CommissionResult[OutputT],
    output: OutputT,
    commission: Commission[InputT, OutputT],
    ctx: CallContext,
    estimated: int,
    cap: int,
) -> tuple[CommissionResult[OutputT], CommissionResult[OutputT] | None]:
    """Chop the output; hand the full result back for forced persistence.

    Three conditions must hold, or the policy degrades to `partial` with the
    full output preserved (non-breaking, never silent): a backend to persist
    the full version, a Commission whose `truncate_output` knows how to chop
    its own output type, and a chop that actually fits the cap.
    """
    if ctx.backend is None:
        return _overflow_degrade(
            result,
            estimated,
            cap,
            "no persistence backend is wired, so the full output cannot be persisted for reference",
        ), None
    chopped = commission.truncate_output(output, cap)
    if chopped is None:
        return _overflow_degrade(
            result,
            estimated,
            cap,
            f"{commission.name} does not implement truncate_output",
        ), None
    chopped_size = _estimate_output_tokens(chopped)
    if chopped_size > cap:
        return _overflow_degrade(
            result,
            estimated,
            cap,
            f"truncate_output returned ~{chopped_size} tokens, still above the cap",
        ), None
    truncated = result.model_copy(
        update={
            "status": "partial",
            "output": chopped,
            "error": ErrorState(
                kind="output_too_large",
                detail=(
                    f"Output of ~{estimated} tokens exceeds cap of {cap}; "
                    f"truncated to ~{chopped_size} tokens. The full output is "
                    f"persisted under run_id {result.run_id}; load it from the "
                    f"persistence backend."
                ),
                retryable=False,
            ),
        }
    )
    return truncated, result


def _overflow_degrade[OutputT](
    result: CommissionResult[OutputT],
    estimated: int,
    cap: int,
    reason: str,
) -> CommissionResult[OutputT]:
    """truncate_with_reference's fallback: full output as `partial`, flagged."""
    return result.model_copy(
        update={
            "status": "partial",
            "error": ErrorState(
                kind="output_too_large",
                detail=(
                    f"Output of ~{estimated} tokens exceeds cap of {cap}; "
                    f"returned in full as partial because {reason}."
                ),
                retryable=False,
            ),
        }
    )


def _estimate_output_tokens(output: Any) -> int:
    """Size a typed output with the contract's char-per-token heuristic."""
    if isinstance(output, BaseModel):
        return estimate_tokens(output.model_dump_json())
    return estimate_tokens(str(output))
