"""Shared test harness: open a run without going through run_commission.

Most tests exercise a Commission end-to-end and should just call `run_commission`,
which builds the run's Gatekeeper like production. The fixture here serves
the tests that probe interior machinery (`run_llm_loop` called directly,
dispatch mechanics mid-run): it opens a real run scope (a Gatekeeper, the
run ContextVar, a context carrying both) without the entry point, because
`dispatch` refuses to operate outside a run. Zero public surface; tests may
touch internals, the library may not.
"""

from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest

from vibrantine._gatekeeper import Gatekeeper, RunCancel, build_catalog, current_gatekeeper
from vibrantine.contract import (
    NEVER_CANCELLED,
    CallContext,
    CancelToken,
    CapabilitySet,
    PersistenceBackend,
    PersistenceMode,
    ProgressEvent,
)
from vibrantine.models import Model


@asynccontextmanager
async def open_run(
    *,
    budget_usd: float | None = None,
    spend_limit_usd: float | None = None,
    models: Sequence[Model] = (),
    default_model: str | None = None,
    max_llm_calls: int | None = None,
    time_limit_seconds: float | None = None,
    concurrency: int = 16,
    tool_ceiling: frozenset[str] | None = None,
    capabilities: CapabilitySet | None = None,
    cancel: CancelToken | None = None,
    on_progress: Callable[[ProgressEvent], None] | None = None,
    on_llm_call: Callable[[dict[str, object]], None] | None = None,
    on_dispatch: Callable[[dict[str, object]], None] | None = None,
    backend: PersistenceBackend | None = None,
    record: PersistenceMode | None = None,
) -> AsyncGenerator[CallContext]:
    """Open a run scope and yield a context carrying it, mirroring run_commission.

    Deliberately unlike run_commission, every fuse defaults to off (`budget_usd`
    here is only the root allocation grant; arm the spend fuse explicitly
    with `spend_limit_usd`), so an interior test never trips a fuse it
    didn't arm and allocation tests stay pure. `models=` builds the run's
    catalog exactly like run_commission (empty auto-registers the system default);
    register `testing.scripted_model(...)` entries here for fake providers.
    """
    catalog, default = build_catalog(models, default_model)
    gatekeeper = Gatekeeper(
        catalog=catalog,
        default_model=default,
        max_llm_calls=max_llm_calls,
        time_limit_seconds=time_limit_seconds,
        spend_limit_usd=spend_limit_usd,
        concurrency=concurrency,
        tool_ceiling=tool_ceiling,
        on_call=on_llm_call,
        on_dispatch=on_dispatch,
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
        yield ctx
    finally:
        current_gatekeeper.reset(token)
        await gatekeeper.aclose()


@pytest.fixture
def open_test_run() -> Callable[..., "AbstractAsyncContextManager[CallContext]"]:
    """The `open_run` harness as a fixture, for tests outside this module."""
    return open_run
