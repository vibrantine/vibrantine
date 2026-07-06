"""Dispatch helper for invoking Commissions through the framework.

Every Commission invocation that crosses a contract boundary (top-level
from `run_one`, child invocations from Python coordinators, tool
dispatches from `run_llm_loop` and the LLM-tool wrapper) calls
`dispatch`. This is where:

  - a `run_id` (UUID4) is generated for the call
  - `parent_run_id` is threaded automatically via `ContextVar` (so
    `asyncio.gather` over child dispatches keeps the chain correct)
  - `overflow_policy` is enforced on the returned result
  - `persistence_mode` is honored: the record is written through
    `CallContext.backend` if one is wired
  - a raised exception from a misbehaving `invoke` is converted to an
    `internal` failure result, so errors-as-values holds at the boundary
    even when a Commission breaks the contract

Calling `commission.invoke` directly bypasses all of this; don't.
"""

import contextvars
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

_current_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_vibrantine_current_run_id",
    default=None,
)


async def dispatch[InputT, OutputT](
    commission: Commission[InputT, OutputT],
    input: InputT,
    ctx: CallContext,
    *,
    llm_trace: list[dict[str, Any]] | None = None,
) -> CommissionResult[OutputT]:
    """Invoke `commission` with framework wrapping."""
    parent = _current_run_id.get()
    my_run_id = str(uuid.uuid4())

    # The Commission body sees its parent's run_id via ctx; it does not see
    # its own (dispatch stamps that onto the result after invoke returns).
    ctx_for_invoke = replace(ctx, parent_run_id=parent)

    token = _current_run_id.set(my_run_id)
    try:
        result = await commission.invoke(input, ctx_for_invoke)
    except Exception as exc:
        # Errors are values: a Commission that *raises* instead of returning a
        # failure (a custom-invoke bug, a third-party Commission) has broken the
        # contract. Convert it here so the exception can't escape `run_one`, and
        # so the failure still flows through stamping + persistence below.
        # CancelledError is a BaseException, not an Exception; task
        # cancellation deliberately propagates rather than being swallowed.
        result = _exception_to_failure(commission, exc)
    finally:
        _current_run_id.reset(token)

    result = _apply_overflow_policy(result, commission, ctx_for_invoke)
    result = result.model_copy(update={"run_id": my_run_id, "parent_run_id": parent})

    if ctx.backend is not None and _should_persist(commission.persistence_mode, result.status):
        record = _build_record(
            run_id=my_run_id,
            parent_run_id=parent,
            commission=commission,
            mode=commission.persistence_mode,
            input=input,
            result=result,
            ctx=ctx_for_invoke,
            llm_trace=llm_trace,
        )
        try:
            await ctx.backend.store(record)
        except Exception as exc:
            # Persistence is observability, not the work itself: a failing
            # backend (disk full, a third-party implementation bug) must not
            # destroy the result it was recording or let an exception cross
            # the run_one boundary. Surface it through on_progress and return
            # the result anyway.
            if ctx.on_progress is not None:
                ctx.on_progress(
                    ProgressEvent(
                        commission_name=commission.name,
                        phase="persist_failed",
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                )

    return result


# --- Exception handling ---------------------------------------------------


def _exception_to_failure[OutputT](
    commission: Commission[Any, OutputT],
    exc: Exception,
) -> CommissionResult[OutputT]:
    """Convert a raised exception into a structured `internal` failure.

    Upholding errors-as-values is the author's job, but dispatch is the seam
    that *guarantees* it: a raising `invoke` becomes a failure result rather
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
) -> CommissionResult[OutputT]:
    if commission.max_output_tokens is None or result.output is None:
        return result

    estimated = _estimate_output_tokens(result.output)
    if estimated <= commission.max_output_tokens:
        return result

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
        )
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
        )
    if policy == "flag":
        if ctx.on_progress is not None:
            ctx.on_progress(
                ProgressEvent(
                    commission_name=commission.name,
                    phase="output_overflow",
                    detail=f"~{estimated} tokens / cap {cap}",
                )
            )
        return result
    if policy == "truncate_with_reference":
        # STUB: the full mechanic is a near-term TODO.
        # The real mechanic chops the output to fit, persists the full output via
        # the backend, and embeds its run_id as a reference. The chop step needs
        # Commission-specific knowledge (how to shrink a typed OutputT without
        # making it invalid or self-contradicting: a `summary_text` vs a
        # `list[Claim]` vs an opaque payload), which wants a real consumer to
        # design against. Until that authoring interface lands we degrade to
        # `partial`: the full output is preserved and the jacket flags that real
        # truncation-with-reference is pending. Non-breaking, never silent.
        return result.model_copy(
            update={
                "status": "partial",
                "error": ErrorState(
                    kind="output_too_large",
                    detail=(
                        f"Output of ~{estimated} tokens exceeds cap of {cap}. "
                        f"truncate_with_reference is stubbed: returning the full "
                        f"output as partial (chop + persist-reference mechanic "
                        f"pending)."
                    ),
                    retryable=False,
                ),
            }
        )
    return result  # unreachable; appeases exhaustiveness checks


def _estimate_output_tokens(output: Any) -> int:
    """Size a typed output with the contract's char-per-token heuristic."""
    if isinstance(output, BaseModel):
        return estimate_tokens(output.model_dump_json())
    return estimate_tokens(str(output))
