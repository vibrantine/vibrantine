"""Tests for the orchestrator entry point."""

from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import httpx
from pydantic import BaseModel

from vibrantine.contract import (
    CallContext,
    Commission,
    CommissionResult,
    CostMetrics,
    Provenance,
)
from vibrantine.orchestrator import run_one
from vibrantine.persistence import FilesystemBackend
from vibrantine.tools.fetch import FetchInput, FetchTool


def _handler_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="hi", headers={"content-type": "text/plain"})


def _handler_500(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, text="boom")


class _BudgetProbeInput(BaseModel):
    pass


class _BudgetProbeOutput(BaseModel):
    seen_budget: float | None


class _BudgetProbeCommission(Commission[_BudgetProbeInput, _BudgetProbeOutput]):
    """Records the budget visible inside its CallContext."""

    name: ClassVar[str] = "budget_probe"
    description: ClassVar[str] = "Test commission that echoes ctx.budget_usd."
    input_type: ClassVar[type] = _BudgetProbeInput
    output_type: ClassVar[type] = _BudgetProbeOutput

    async def invoke(
        self,
        input: _BudgetProbeInput,
        ctx: CallContext,
    ) -> CommissionResult[_BudgetProbeOutput]:
        return CommissionResult[_BudgetProbeOutput](
            status="success",
            output=_BudgetProbeOutput(seen_budget=ctx.budget_usd),
            provenance=Provenance(
                source="budget_probe",
                fetched_at=datetime.now(UTC),
                confidence="grounded",
            ),
            cost=CostMetrics(estimated_usd=0.0),
        )


async def test_run_one_success_through_fetch_commission() -> None:
    commission = FetchTool(transport=httpx.MockTransport(_handler_ok))

    result = await run_one(
        commission,
        FetchInput(url="https://example.test/hi"),
        budget_usd=0.0,
    )

    assert result.status == "success"
    assert result.error is None
    assert result.output is not None
    assert result.output.content == "hi"


async def test_run_one_returns_errorstate_in_result_not_exception() -> None:
    commission = FetchTool(transport=httpx.MockTransport(_handler_500))

    result = await run_one(
        commission,
        FetchInput(url="https://example.test/explode"),
        budget_usd=0.0,
    )

    assert result.status == "failure"
    assert result.output is None
    assert result.error is not None
    assert result.error.kind == "internal"
    assert result.error.retryable is True


async def test_run_one_passes_budget_into_callcontext() -> None:
    result = await run_one(
        _BudgetProbeCommission(),
        _BudgetProbeInput(),
        budget_usd=1.23,
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.output.seen_budget == 1.23


async def test_run_one_threads_backend_so_dispatch_persists(
    tmp_path: Path,
) -> None:
    backend = FilesystemBackend(tmp_path)

    result = await run_one(
        _BudgetProbeCommission(persistence_mode="always"),
        _BudgetProbeInput(),
        backend=backend,
    )

    assert result.run_id is not None
    refs = await backend.list_references()
    assert refs == [result.run_id]


async def test_run_one_no_backend_skips_persistence(tmp_path: Path) -> None:
    # Same commission, but with no backend wired — nothing should be stored.
    backend = FilesystemBackend(tmp_path)

    result = await run_one(
        _BudgetProbeCommission(persistence_mode="always"),
        _BudgetProbeInput(),
        # backend deliberately omitted
    )

    assert result.run_id is not None  # run_id still stamped
    assert await backend.list_references() == []
