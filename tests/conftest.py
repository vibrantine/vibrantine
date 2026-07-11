"""Shared test harness: open a run without going through run_one.

Most tests exercise a Commission end-to-end and should just call `run_one`,
which builds the run's Gatekeeper like production. The fixture here serves
the tests that probe interior machinery (`run_llm_loop` called directly,
dispatch mechanics mid-run): it opens a real run scope (a Gatekeeper, the
run ContextVar, a context carrying both) without the entry point, because
`dispatch` refuses to operate outside a run. Zero public surface; tests may
touch internals, the library may not.
"""

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest

from vibrantine._gatekeeper import Gatekeeper, RunCancel, current_gatekeeper
from vibrantine.contract import (
    NEVER_CANCELLED,
    CallContext,
    CancelToken,
    CapabilitySet,
    PersistenceBackend,
    PersistenceMode,
    ProgressEvent,
)


@asynccontextmanager
async def open_run(
    *,
    budget_usd: float | None = None,
    spend_limit_usd: float | None = None,
    max_llm_calls: int | None = None,
    time_limit_seconds: float | None = None,
    concurrency: int = 16,
    capabilities: CapabilitySet | None = None,
    cancel: CancelToken | None = None,
    on_progress: Callable[[ProgressEvent], None] | None = None,
    backend: PersistenceBackend | None = None,
    record: PersistenceMode | None = None,
) -> AsyncGenerator[CallContext]:
    """Open a run scope and yield a context carrying it, mirroring run_one.

    Deliberately unlike run_one, every fuse defaults to off (`budget_usd`
    here is only the root allocation grant; arm the spend fuse explicitly
    with `spend_limit_usd`), so an interior test never trips a fuse it
    didn't arm and allocation tests stay pure.
    """
    gatekeeper = Gatekeeper(
        max_llm_calls=max_llm_calls,
        time_limit_seconds=time_limit_seconds,
        spend_limit_usd=spend_limit_usd,
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
        yield ctx
    finally:
        current_gatekeeper.reset(token)


@pytest.fixture
def open_test_run() -> Callable[..., "AbstractAsyncContextManager[CallContext]"]:
    """The `open_run` harness as a fixture, for tests outside this module."""
    return open_run
