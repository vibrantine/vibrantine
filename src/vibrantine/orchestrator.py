"""Top-level entry points for invoking a commission from outside.

A commission requires a typed input and a CallContext. Callers who only
care about the budget (and optionally the persistence backend) should
not have to construct a CallContext by hand. Routes through `dispatch`
so run_id stamping, parent_run_id threading, overflow enforcement, and
persistence happen uniformly from the top.
"""

import asyncio

from vibrantine.contract import (
    CallContext,
    Commission,
    CommissionResult,
    PersistenceBackend,
)
from vibrantine.dispatch import dispatch


async def run_one[InputT, OutputT](
    commission: Commission[InputT, OutputT],
    input: InputT,
    *,
    budget_usd: float | None = None,
    backend: PersistenceBackend | None = None,
) -> CommissionResult[OutputT]:
    """Run one commission with a default CallContext.

    Capabilities, cancellation, progress, and concurrency take dataclass
    defaults. `backend` (if given) is stuffed into the context so children
    inherit it automatically. Returns the commission's CommissionResult
    unchanged: errors surface as ErrorState in the result, not as raised
    exceptions.
    """
    ctx = CallContext(budget_usd=budget_usd, backend=backend)
    return await dispatch(commission, input, ctx)


def invoke_sync[InputT, OutputT](
    commission: Commission[InputT, OutputT],
    input: InputT,
    *,
    budget_usd: float | None = None,
    backend: PersistenceBackend | None = None,
) -> CommissionResult[OutputT]:
    """Sync wrapper over `run_one`. For scripts, REPL, smoke tests."""
    return asyncio.run(run_one(commission, input, budget_usd=budget_usd, backend=backend))
