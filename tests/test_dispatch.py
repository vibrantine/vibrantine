"""Tests for the dispatch helper.

Built around stub commissions that emit a known result. Each test
exercises one slice of dispatch's wrapping behavior: run_id generation,
parent_run_id threading across nested dispatch calls (including under
asyncio.gather), persistence-mode honoring, and overflow_policy
enforcement.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, cast

import pytest
from pydantic import BaseModel, Field

from vibrantine.contract import (
    CallContext,
    Commission,
    CommissionResult,
    CostMetrics,
    ErrorState,
    OverflowPolicy,
    PersistenceMode,
    ProgressEvent,
    Provenance,
)
from vibrantine.dispatch import dispatch
from vibrantine.persistence import FilesystemBackend


class _Input(BaseModel):
    q: str = Field(description="prompt-y thing")


class _Output(BaseModel):
    a: str = Field(description="answer-y thing")


def _success_result(answer: str = "ok") -> CommissionResult[_Output]:
    return CommissionResult[_Output](
        status="success",
        output=_Output(a=answer),
        provenance=Provenance(source="stub", fetched_at=datetime.now(UTC), confidence="grounded"),
        cost=CostMetrics(estimated_usd=0.0),
    )


def _failure_result(detail: str = "nope") -> CommissionResult[_Output]:
    return cast(
        CommissionResult[_Output],
        CommissionResult(
            status="failure",
            error=ErrorState(kind="internal", detail=detail, retryable=False),
            provenance=Provenance(
                source="stub",
                fetched_at=datetime.now(UTC),
                confidence="grounded",
            ),
            cost=CostMetrics(estimated_usd=0.0),
        ),
    )


class _Stub(Commission[_Input, _Output]):
    """Returns a pre-scripted result; records the ctx it saw."""

    name: ClassVar[str] = "stub"
    description: ClassVar[str] = "test stub"
    input_type: ClassVar[type[BaseModel]] = _Input
    output_type: ClassVar[type[BaseModel]] = _Output

    def __init__(
        self,
        *,
        result: CommissionResult[_Output] | None = None,
        persistence_mode: PersistenceMode = "off",
        max_output_tokens: int | None = None,
        overflow_policy: OverflowPolicy = "flag",
    ) -> None:
        super().__init__(
            persistence_mode=persistence_mode,
            max_output_tokens=max_output_tokens,
            overflow_policy=overflow_policy,
        )
        self._scripted = result or _success_result()
        self.seen_ctx: CallContext | None = None

    async def invoke(self, input: _Input, ctx: CallContext) -> CommissionResult[_Output]:
        self.seen_ctx = ctx
        return self._scripted


class _Raiser(Commission[_Input, _Output]):
    """Raises from invoke: a commission that breaks the errors-as-values rule."""

    name: ClassVar[str] = "raiser"
    description: ClassVar[str] = "test stub that raises"
    input_type: ClassVar[type[BaseModel]] = _Input
    output_type: ClassVar[type[BaseModel]] = _Output

    def __init__(
        self,
        exc: BaseException,
        *,
        persistence_mode: PersistenceMode = "off",
    ) -> None:
        super().__init__(persistence_mode=persistence_mode)
        self._exc = exc

    async def invoke(self, input: _Input, ctx: CallContext) -> CommissionResult[_Output]:
        raise self._exc


class _Parent(Commission[_Input, _Output]):
    """Calls dispatch on a child; for chain-tracking tests."""

    name: ClassVar[str] = "parent"
    description: ClassVar[str] = "parent that dispatches one child"
    input_type: ClassVar[type[BaseModel]] = _Input
    output_type: ClassVar[type[BaseModel]] = _Output

    def __init__(self, child: Commission[_Input, _Output]) -> None:
        super().__init__()
        self._child = child

    async def invoke(self, input: _Input, ctx: CallContext) -> CommissionResult[_Output]:
        await dispatch(self._child, input, ctx)
        return _success_result("from-parent")


class _GatherParent(Commission[_Input, _Output]):
    """Dispatches two children concurrently via asyncio.gather."""

    name: ClassVar[str] = "gather_parent"
    description: ClassVar[str] = "parent that fans two children"
    input_type: ClassVar[type[BaseModel]] = _Input
    output_type: ClassVar[type[BaseModel]] = _Output

    def __init__(
        self,
        child_a: Commission[_Input, _Output],
        child_b: Commission[_Input, _Output],
    ) -> None:
        super().__init__()
        self._a = child_a
        self._b = child_b

    async def invoke(self, input: _Input, ctx: CallContext) -> CommissionResult[_Output]:
        await asyncio.gather(
            dispatch(self._a, input, ctx),
            dispatch(self._b, input, ctx),
        )
        return _success_result("from-gather-parent")


# --- run_id + chain tests --------------------------------------------------


async def test_dispatch_generates_run_id_on_result() -> None:
    stub = _Stub()
    result = await dispatch(stub, _Input(q="?"), CallContext())
    assert result.run_id is not None
    assert len(result.run_id) > 0


async def test_dispatch_parent_run_id_is_none_at_top_level() -> None:
    stub = _Stub()
    result = await dispatch(stub, _Input(q="?"), CallContext())
    assert result.parent_run_id is None


async def test_dispatch_child_sees_parent_run_id_in_ctx() -> None:
    child = _Stub()
    parent = _Parent(child)
    parent_result = await dispatch(parent, _Input(q="?"), CallContext())

    assert child.seen_ctx is not None
    assert child.seen_ctx.parent_run_id == parent_result.run_id


async def test_dispatch_gathered_children_share_correct_parent() -> None:
    a, b = _Stub(), _Stub()
    parent = _GatherParent(a, b)
    parent_result = await dispatch(parent, _Input(q="?"), CallContext())

    assert a.seen_ctx is not None and b.seen_ctx is not None
    assert a.seen_ctx.parent_run_id == parent_result.run_id
    assert b.seen_ctx.parent_run_id == parent_result.run_id


# --- persistence tests -----------------------------------------------------


async def test_dispatch_off_does_not_call_backend(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path)
    stub = _Stub(persistence_mode="off")
    result = await dispatch(stub, _Input(q="?"), CallContext(backend=backend))

    assert await backend.list_references() == []
    # run_id still stamped (always free), but no record written.
    assert result.run_id is not None


async def test_dispatch_always_persists_every_run(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path)
    stub = _Stub(persistence_mode="always")
    result = await dispatch(stub, _Input(q="?"), CallContext(backend=backend))

    refs = await backend.list_references()
    assert refs == [result.run_id]


async def test_dispatch_dev_persists_every_run(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path)
    stub = _Stub(persistence_mode="dev")
    result = await dispatch(stub, _Input(q="?"), CallContext(backend=backend))

    refs = await backend.list_references()
    assert refs == [result.run_id]


async def test_dispatch_on_failure_persists_failures(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path)
    stub = _Stub(persistence_mode="on_failure", result=_failure_result())
    result = await dispatch(stub, _Input(q="?"), CallContext(backend=backend))

    refs = await backend.list_references()
    assert refs == [result.run_id]


async def test_dispatch_on_failure_skips_successes(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path)
    stub = _Stub(persistence_mode="on_failure")
    await dispatch(stub, _Input(q="?"), CallContext(backend=backend))

    assert await backend.list_references() == []


async def test_dispatch_no_backend_skips_even_with_active_mode(
    tmp_path: Path,
) -> None:
    stub = _Stub(persistence_mode="always")
    # No backend on ctx → nothing to persist to → result still flows.
    result = await dispatch(stub, _Input(q="?"), CallContext())
    assert result.status == "success"


async def test_dispatch_persisted_record_has_chain_and_payloads(
    tmp_path: Path,
) -> None:
    backend = FilesystemBackend(tmp_path)
    child = _Stub(persistence_mode="always")
    parent = _Parent(child)
    parent_result = await dispatch(parent, _Input(q="ask"), CallContext(backend=backend))

    child_refs = await backend.list_references(parent_run_id=parent_result.run_id)
    assert len(child_refs) == 1
    loaded = await backend.load(child_refs[0])
    assert loaded is not None
    assert loaded.parent_run_id == parent_result.run_id
    assert loaded.commission_name == "stub"
    assert loaded.input == {"q": "ask"}
    assert loaded.result["status"] == "success"


class _ExplodingBackend:
    """A PersistenceBackend whose store always raises."""

    async def store(self, record: object) -> None:
        raise OSError("disk full")

    async def load(self, run_id: str) -> None:
        return None

    async def list_references(self, *, parent_run_id: str | None = None) -> list[str]:
        return []

    async def delete(self, run_id: str) -> None:
        return None

    async def delete_older_than(self, cutoff: datetime) -> int:
        return 0


async def test_dispatch_failing_backend_does_not_destroy_the_result() -> None:
    # Persistence is observability: a backend that raises on store must not
    # break errors-as-values or eat the result it was recording.
    events: list[ProgressEvent] = []
    stub = _Stub(persistence_mode="always")
    result = await dispatch(
        stub,
        _Input(q="?"),
        CallContext(backend=_ExplodingBackend(), on_progress=events.append),
    )

    assert result.status == "success"
    assert result.output is not None
    assert any(e.phase == "persist_failed" and "disk full" in (e.detail or "") for e in events)


# --- overflow tests --------------------------------------------------------


async def test_dispatch_no_max_output_tokens_skips_check() -> None:
    stub = _Stub(max_output_tokens=None)
    result = await dispatch(stub, _Input(q="?"), CallContext())
    assert result.status == "success"
    assert result.output is not None


async def test_dispatch_under_cap_returns_unchanged() -> None:
    stub = _Stub(max_output_tokens=1000, overflow_policy="reject")
    result = await dispatch(stub, _Input(q="?"), CallContext())
    assert result.status == "success"


async def test_dispatch_overflow_reject_clears_output_and_fails() -> None:
    big = "x" * 4000  # ~1000 tokens
    stub = _Stub(
        result=_success_result(answer=big),
        max_output_tokens=10,
        overflow_policy="reject",
    )
    result = await dispatch(stub, _Input(q="?"), CallContext())

    assert result.status == "failure"
    assert result.output is None
    assert result.error is not None
    assert result.error.kind == "output_too_large"


async def test_dispatch_overflow_partial_marks_partial_keeps_output() -> None:
    big = "x" * 4000
    stub = _Stub(
        result=_success_result(answer=big),
        max_output_tokens=10,
        overflow_policy="partial",
    )
    result = await dispatch(stub, _Input(q="?"), CallContext())

    assert result.status == "partial"
    assert result.output is not None  # data still usable
    assert result.error is not None
    assert result.error.kind == "output_too_large"


async def test_dispatch_overflow_flag_emits_progress_event_unchanged() -> None:
    events: list[ProgressEvent] = []
    big = "x" * 4000
    stub = _Stub(
        result=_success_result(answer=big),
        max_output_tokens=10,
        overflow_policy="flag",
    )
    result = await dispatch(stub, _Input(q="?"), CallContext(on_progress=events.append))

    assert result.status == "success"
    assert result.output is not None and result.output.a == big
    assert any(e.phase == "output_overflow" for e in events)


async def test_dispatch_overflow_truncate_stub_degrades_to_partial() -> None:
    # truncate_with_reference is a frozen-vocabulary policy whose real mechanic
    # (chop + persist-reference) is a near-term TODO. The stub must be
    # non-breaking: degrade to partial, preserve the output, flag it in the
    # jacket.
    big = "x" * 4000
    stub = _Stub(
        result=_success_result(answer=big),
        max_output_tokens=10,
        overflow_policy="truncate_with_reference",
    )
    result = await dispatch(stub, _Input(q="?"), CallContext())

    assert result.status == "partial"
    assert result.output is not None and result.output.a == big  # output preserved
    assert result.error is not None
    assert result.error.kind == "output_too_large"
    assert "stubbed" in result.error.detail


# --- exception-to-failure tests --------------------------------------------


async def test_dispatch_converts_raised_exception_to_failure() -> None:
    result = await dispatch(_Raiser(ValueError("boom")), _Input(q="?"), CallContext())

    assert result.status == "failure"
    assert result.output is None
    assert result.error is not None
    assert result.error.kind == "internal"
    assert result.error.retryable is False
    assert "ValueError" in result.error.detail
    assert "boom" in result.error.detail
    # stamped like any other wrapped call
    assert result.run_id is not None
    assert result.parent_run_id is None


async def test_dispatch_persists_raised_exception_on_failure(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path)
    raiser = _Raiser(RuntimeError("kaboom"), persistence_mode="on_failure")
    result = await dispatch(raiser, _Input(q="?"), CallContext(backend=backend))

    # The gap this closes: a raising commission still gets its failure recorded.
    assert result.run_id is not None
    refs = await backend.list_references()
    assert refs == [result.run_id]
    loaded = await backend.load(result.run_id)
    assert loaded is not None
    assert loaded.result["status"] == "failure"
    assert loaded.result["error"]["kind"] == "internal"


async def test_dispatch_does_not_swallow_cancellation() -> None:
    # CancelledError is a BaseException, not an Exception: task cancellation must
    # propagate, never be converted to an `internal` failure value.
    with pytest.raises(asyncio.CancelledError):
        await dispatch(_Raiser(asyncio.CancelledError()), _Input(q="?"), CallContext())
