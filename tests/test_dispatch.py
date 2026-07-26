"""Tests for the dispatch helper.

Built around stub Commissions that emit a known result. Each test
exercises one slice of dispatch's wrapping behavior: run_id generation,
parent_run_id threading across nested dispatch calls (including under
asyncio.gather), persistence-mode honoring, and overflow_policy
enforcement. dispatch refuses to operate outside a run, so each test
opens a run scope through the shared `open_test_run` harness.
"""

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
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
from vibrantine.dispatch import deposit_llm_trace, dispatch
from vibrantine.persistence import FilesystemBackend
from vibrantine.testing import ScriptedLLM, llm_response, scripted_model

# The `open_test_run` fixture's shape: a factory whose `async with` yields a
# CallContext inside an open run scope.
OpenTestRun = Callable[..., AbstractAsyncContextManager[CallContext]]


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
        persistence_mode: PersistenceMode | None = None,
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

    async def _run(self, input: _Input, ctx: CallContext) -> CommissionResult[_Output]:
        self.seen_ctx = ctx
        return self._scripted


class _Chopper(_Stub):
    """A stub that knows how to chop its own output (truncate_output)."""

    def __init__(self, *, chop_to: str, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._chop_to = chop_to

    def truncate_output(self, output: _Output, max_tokens: int) -> _Output | None:
        return _Output(a=self._chop_to)


class _FailingBackend:
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


class _Raiser(Commission[_Input, _Output]):
    """Raises from _run: a Commission that breaks the errors-as-values rule."""

    name: ClassVar[str] = "raiser"
    description: ClassVar[str] = "test stub that raises"
    input_type: ClassVar[type[BaseModel]] = _Input
    output_type: ClassVar[type[BaseModel]] = _Output

    def __init__(
        self,
        exc: BaseException,
        *,
        persistence_mode: PersistenceMode | None = None,
    ) -> None:
        super().__init__(persistence_mode=persistence_mode)
        self._exc = exc

    async def _run(self, input: _Input, ctx: CallContext) -> CommissionResult[_Output]:
        raise self._exc


class _Emitter(_Stub):
    """Emits one ordinary Commission progress event before succeeding."""

    async def _run(self, input: _Input, ctx: CallContext) -> CommissionResult[_Output]:
        self._emit(ctx, "started")  # pyright: ignore[reportPrivateUsage]
        return await super()._run(input, ctx)


class _SecretTraceStub(_Stub):
    """Deposits a representative secret-bearing raw tool-call trace."""

    async def _run(self, input: _Input, ctx: CallContext) -> CommissionResult[_Output]:
        deposit_llm_trace(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "fetch",
                                "arguments": input.q,
                            }
                        }
                    ],
                }
            ]
        )
        return await super()._run(input, ctx)


def _raise_progress(event: ProgressEvent) -> None:
    raise RuntimeError(f"observer broke during {event.phase}")


class _Parent(Commission[_Input, _Output]):
    """Calls dispatch on a child; for chain-tracking tests."""

    name: ClassVar[str] = "parent"
    description: ClassVar[str] = "parent that dispatches one child"
    input_type: ClassVar[type[BaseModel]] = _Input
    output_type: ClassVar[type[BaseModel]] = _Output

    def __init__(self, child: Commission[_Input, _Output]) -> None:
        super().__init__()
        self._child = child

    async def _run(self, input: _Input, ctx: CallContext) -> CommissionResult[_Output]:
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

    async def _run(self, input: _Input, ctx: CallContext) -> CommissionResult[_Output]:
        await asyncio.gather(
            dispatch(self._a, input, ctx),
            dispatch(self._b, input, ctx),
        )
        return _success_result("from-gather-parent")


# --- run_id + chain tests --------------------------------------------------


async def test_dispatch_generates_run_id_on_result(open_test_run: OpenTestRun) -> None:
    stub = _Stub()
    async with open_test_run() as ctx:
        result = await dispatch(stub, _Input(q="?"), ctx)
    assert result.run_id is not None
    assert len(result.run_id) > 0


async def test_dispatch_parent_run_id_is_none_at_top_level(open_test_run: OpenTestRun) -> None:
    stub = _Stub()
    async with open_test_run() as ctx:
        result = await dispatch(stub, _Input(q="?"), ctx)
    assert result.parent_run_id is None


async def test_dispatch_child_sees_parent_run_id_in_ctx(open_test_run: OpenTestRun) -> None:
    child = _Stub()
    parent = _Parent(child)
    async with open_test_run() as ctx:
        parent_result = await dispatch(parent, _Input(q="?"), ctx)

    assert child.seen_ctx is not None
    assert child.seen_ctx.parent_run_id == parent_result.run_id


async def test_dispatch_gathered_children_share_correct_parent(
    open_test_run: OpenTestRun,
) -> None:
    a, b = _Stub(), _Stub()
    parent = _GatherParent(a, b)
    async with open_test_run() as ctx:
        parent_result = await dispatch(parent, _Input(q="?"), ctx)

    assert a.seen_ctx is not None and b.seen_ctx is not None
    assert a.seen_ctx.parent_run_id == parent_result.run_id
    assert b.seen_ctx.parent_run_id == parent_result.run_id


# --- result boundary tests -------------------------------------------------


async def test_dispatch_rejects_a_bypass_built_malformed_result(
    open_test_run: OpenTestRun,
    tmp_path: Path,
) -> None:
    malformed = CommissionResult[_Output].model_construct(
        status="success",
        output=None,
        error=None,
        provenance=Provenance(
            source="malformed",
            fetched_at=datetime.now(UTC),
            confidence="grounded",
        ),
        cost=CostMetrics(estimated_usd=0.0),
    )
    backend = FilesystemBackend(tmp_path / "records")
    stub = _Stub(result=malformed, persistence_mode="always")

    async with open_test_run(backend=backend) as ctx:
        result = await dispatch(stub, _Input(q="?"), ctx)

    assert result.status == "failure"
    assert result.output is None
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "success results require output and forbid error" in result.error.detail
    assert result.run_id is not None
    record = await backend.load(result.run_id)
    assert record is not None
    assert record.result["status"] == "failure"
    assert record.result["output"] is None
    assert record.result["error"]["kind"] == "internal"


# --- persistence tests -----------------------------------------------------


async def test_dispatch_off_does_not_call_backend(
    tmp_path: Path, open_test_run: OpenTestRun
) -> None:
    backend = FilesystemBackend(tmp_path)
    stub = _Stub(persistence_mode="off")
    async with open_test_run(backend=backend) as ctx:
        result = await dispatch(stub, _Input(q="?"), ctx)

    assert await backend.list_references() == []
    # run_id still stamped (always free), but no record written.
    assert result.run_id is not None


async def test_dispatch_always_persists_every_run(
    tmp_path: Path, open_test_run: OpenTestRun
) -> None:
    backend = FilesystemBackend(tmp_path)
    stub = _Stub(persistence_mode="always")
    async with open_test_run(backend=backend) as ctx:
        result = await dispatch(stub, _Input(q="?"), ctx)

    refs = await backend.list_references()
    assert refs == [result.run_id]


async def test_dispatch_dev_persists_every_run(tmp_path: Path, open_test_run: OpenTestRun) -> None:
    backend = FilesystemBackend(tmp_path)
    stub = _Stub(persistence_mode="dev")
    async with open_test_run(backend=backend) as ctx:
        result = await dispatch(stub, _Input(q="?"), ctx)

    refs = await backend.list_references()
    assert refs == [result.run_id]


async def test_dispatch_on_failure_persists_failures(
    tmp_path: Path, open_test_run: OpenTestRun
) -> None:
    backend = FilesystemBackend(tmp_path)
    stub = _Stub(persistence_mode="on_failure", result=_failure_result())
    async with open_test_run(backend=backend) as ctx:
        result = await dispatch(stub, _Input(q="?"), ctx)

    refs = await backend.list_references()
    assert refs == [result.run_id]


async def test_dispatch_on_failure_skips_successes(
    tmp_path: Path, open_test_run: OpenTestRun
) -> None:
    backend = FilesystemBackend(tmp_path)
    stub = _Stub(persistence_mode="on_failure")
    async with open_test_run(backend=backend) as ctx:
        await dispatch(stub, _Input(q="?"), ctx)

    assert await backend.list_references() == []


async def test_dispatch_no_opinion_with_a_backend_defaults_to_always(
    tmp_path: Path, open_test_run: OpenTestRun
) -> None:
    # Wiring a backend is the "I care about logs" signal: silence on both
    # the node and record= means "always", and keeping less is the active
    # choice (record="off"/"dev"). Ruled 2026-07-12.
    backend = FilesystemBackend(tmp_path)
    stub = _Stub()  # persistence_mode None (no opinion), no run policy either
    async with open_test_run(backend=backend) as ctx:
        result = await dispatch(stub, _Input(q="?"), ctx)

    assert await backend.list_references() == [result.run_id]
    loaded = await backend.load(cast("str", result.run_id))
    assert loaded is not None
    assert loaded.mode == "always"


async def test_run_record_switches_on_the_whole_tree(
    tmp_path: Path, open_test_run: OpenTestRun
) -> None:
    # The application's record= policy reaches every node, parent and child,
    # with no per-node flipping; the record stores the effective mode.
    backend = FilesystemBackend(tmp_path)
    child = _Stub()
    parent = _Parent(child)

    async with open_test_run(backend=backend, record="always") as ctx:
        parent_result = await dispatch(parent, _Input(q="?"), ctx)

    assert await backend.list_references() == [parent_result.run_id]
    child_refs = await backend.list_references(parent_run_id=parent_result.run_id)
    assert len(child_refs) == 1
    loaded = await backend.load(child_refs[0])
    assert loaded is not None
    assert loaded.mode == "always"


async def test_run_record_always_overrides_node_off(
    tmp_path: Path, open_test_run: OpenTestRun
) -> None:
    backend = FilesystemBackend(tmp_path)
    stub = _Stub(persistence_mode="off")
    async with open_test_run(backend=backend, record="always") as ctx:
        result = await dispatch(stub, _Input(q="?"), ctx)

    assert await backend.list_references() == [result.run_id]
    loaded = await backend.load(cast("str", result.run_id))
    assert loaded is not None
    assert loaded.mode == "always"


async def test_run_record_always_overrides_node_on_failure(
    tmp_path: Path, open_test_run: OpenTestRun
) -> None:
    backend = FilesystemBackend(tmp_path)
    stub = _Stub(persistence_mode="on_failure")
    async with open_test_run(backend=backend, record="always") as ctx:
        result = await dispatch(stub, _Input(q="?"), ctx)

    assert await backend.list_references() == [result.run_id]


async def test_run_record_off_overrides_node_always(
    tmp_path: Path, open_test_run: OpenTestRun
) -> None:
    backend = FilesystemBackend(tmp_path)
    stub = _Stub(persistence_mode="always")
    async with open_test_run(backend=backend, record="off") as ctx:
        await dispatch(stub, _Input(q="?"), ctx)

    assert await backend.list_references() == []


async def test_dispatch_no_backend_skips_even_with_active_mode(
    open_test_run: OpenTestRun,
) -> None:
    stub = _Stub(persistence_mode="always")
    # No backend on ctx → nothing to persist to → result still flows.
    async with open_test_run() as ctx:
        result = await dispatch(stub, _Input(q="?"), ctx)
    assert result.status == "success"


async def test_dispatch_persisted_record_has_chain_and_payloads(
    tmp_path: Path, open_test_run: OpenTestRun
) -> None:
    backend = FilesystemBackend(tmp_path)
    child = _Stub(persistence_mode="always")
    parent = _Parent(child)
    async with open_test_run(backend=backend) as ctx:
        parent_result = await dispatch(parent, _Input(q="ask"), ctx)

    child_refs = await backend.list_references(parent_run_id=parent_result.run_id)
    assert len(child_refs) == 1
    loaded = await backend.load(child_refs[0])
    assert loaded is not None
    assert loaded.parent_run_id == parent_result.run_id
    assert loaded.commission_name == "stub"
    assert loaded.input == {"q": "ask"}
    assert loaded.result["status"] == "success"


async def test_persistence_is_full_fidelity_for_secret_bearing_input_and_trace(
    tmp_path: Path,
    open_test_run: OpenTestRun,
) -> None:
    backend = FilesystemBackend(tmp_path)
    secret = '{"headers":{"Authorization":"Bearer diagnostic-secret"}}'
    stub = _SecretTraceStub(persistence_mode="always")

    async with open_test_run(backend=backend) as ctx:
        result = await dispatch(stub, _Input(q=secret), ctx)

    assert result.run_id is not None
    loaded = await backend.load(result.run_id)
    assert loaded is not None
    assert loaded.input["q"] == secret
    assert loaded.llm_trace is not None
    assert loaded.llm_trace[0]["tool_calls"][0]["function"]["arguments"] == secret


async def test_dispatch_failing_backend_does_not_destroy_the_result(
    open_test_run: OpenTestRun,
) -> None:
    # Persistence is observability: a backend that raises on store must not
    # break errors-as-values or eat the result it was recording.
    events: list[ProgressEvent] = []
    stub = _Stub(persistence_mode="always")
    async with open_test_run(backend=_FailingBackend(), on_progress=events.append) as ctx:
        result = await dispatch(stub, _Input(q="?"), ctx)

    assert result.status == "success"
    assert result.output is not None
    assert any(e.phase == "persist_failed" and "disk full" in (e.detail or "") for e in events)


async def test_raising_progress_callback_cannot_change_commission_result(
    open_test_run: OpenTestRun,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    async with open_test_run(on_progress=_raise_progress) as ctx:
        result = await dispatch(_Emitter(), _Input(q="?"), ctx)

    assert result.status == "success"
    assert "on_progress callback raised RuntimeError (ignored)" in caplog.text


async def test_raising_progress_callback_cannot_escape_persistence_failure(
    open_test_run: OpenTestRun,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    stub = _Stub(persistence_mode="always")
    async with open_test_run(backend=_FailingBackend(), on_progress=_raise_progress) as ctx:
        result = await dispatch(stub, _Input(q="?"), ctx)

    assert result.status == "success"
    assert "observer broke during persist_failed" in caplog.text


# --- overflow tests --------------------------------------------------------


async def test_dispatch_no_max_output_tokens_skips_check(open_test_run: OpenTestRun) -> None:
    stub = _Stub(max_output_tokens=None)
    async with open_test_run() as ctx:
        result = await dispatch(stub, _Input(q="?"), ctx)
    assert result.status == "success"
    assert result.output is not None


async def test_dispatch_under_cap_returns_unchanged(open_test_run: OpenTestRun) -> None:
    stub = _Stub(max_output_tokens=1000, overflow_policy="reject")
    async with open_test_run() as ctx:
        result = await dispatch(stub, _Input(q="?"), ctx)
    assert result.status == "success"


async def test_dispatch_overflow_reject_clears_output_and_fails(
    open_test_run: OpenTestRun,
) -> None:
    big = "x" * 4000  # ~1000 tokens
    stub = _Stub(
        result=_success_result(answer=big),
        max_output_tokens=10,
        overflow_policy="reject",
    )
    async with open_test_run() as ctx:
        result = await dispatch(stub, _Input(q="?"), ctx)

    assert result.status == "failure"
    assert result.output is None
    assert result.error is not None
    assert result.error.kind == "output_too_large"


async def test_dispatch_overflow_partial_marks_partial_keeps_output(
    open_test_run: OpenTestRun,
) -> None:
    big = "x" * 4000
    stub = _Stub(
        result=_success_result(answer=big),
        max_output_tokens=10,
        overflow_policy="partial",
    )
    async with open_test_run() as ctx:
        result = await dispatch(stub, _Input(q="?"), ctx)

    assert result.status == "partial"
    assert result.output is not None  # data still usable
    assert result.error is not None
    assert result.error.kind == "output_too_large"


async def test_dispatch_overflow_flag_emits_progress_event_unchanged(
    open_test_run: OpenTestRun,
) -> None:
    events: list[ProgressEvent] = []
    big = "x" * 4000
    stub = _Stub(
        result=_success_result(answer=big),
        max_output_tokens=10,
        overflow_policy="flag",
    )
    async with open_test_run(on_progress=events.append) as ctx:
        result = await dispatch(stub, _Input(q="?"), ctx)

    assert result.status == "success"
    assert result.output is not None and result.output.a == big
    assert any(e.phase == "output_overflow" for e in events)


async def test_raising_progress_callback_cannot_escape_overflow_flag(
    open_test_run: OpenTestRun,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    big = "x" * 4000
    stub = _Stub(
        result=_success_result(answer=big),
        max_output_tokens=10,
        overflow_policy="flag",
    )
    async with open_test_run(on_progress=_raise_progress) as ctx:
        result = await dispatch(stub, _Input(q="?"), ctx)

    assert result.status == "success"
    assert result.output is not None and result.output.a == big
    assert "observer broke during output_overflow" in caplog.text


async def test_dispatch_truncate_chops_and_persists_full_output(
    tmp_path: Path, open_test_run: OpenTestRun
) -> None:
    # The real mechanic: the envelope carries the chopped output as partial
    # with the run_id reference in the detail; the persisted record carries
    # the full pre-chop result, forced on (mode "always") even though neither
    # the node nor the caller asked for recording.
    backend = FilesystemBackend(tmp_path)
    big = "x" * 4000
    chopper = _Chopper(
        chop_to="short",
        result=_success_result(answer=big),
        max_output_tokens=10,
        overflow_policy="truncate_with_reference",
    )
    async with open_test_run(backend=backend) as ctx:
        result = await dispatch(chopper, _Input(q="?"), ctx)

    assert result.status == "partial"
    assert result.output is not None and result.output.a == "short"
    assert result.error is not None
    assert result.error.kind == "output_too_large"
    assert result.run_id is not None and result.run_id in result.error.detail

    record = await backend.load(result.run_id)
    assert record is not None
    assert record.mode == "always"
    assert record.result["status"] == "success"  # the run as it actually went
    assert record.result["output"]["a"] == big  # full version, reachable


async def test_dispatch_truncate_without_backend_degrades_to_partial(
    open_test_run: OpenTestRun,
) -> None:
    # No backend means the full version cannot be persisted, so a reference
    # would point at nothing. Degrade: full output as partial, never silent.
    big = "x" * 4000
    chopper = _Chopper(
        chop_to="short",
        result=_success_result(answer=big),
        max_output_tokens=10,
        overflow_policy="truncate_with_reference",
    )
    async with open_test_run() as ctx:
        result = await dispatch(chopper, _Input(q="?"), ctx)

    assert result.status == "partial"
    assert result.output is not None and result.output.a == big  # output preserved
    assert result.error is not None
    assert result.error.kind == "output_too_large"
    assert "backend" in result.error.detail


async def test_dispatch_truncate_with_run_record_off_degrades_to_partial(
    tmp_path: Path, open_test_run: OpenTestRun
) -> None:
    backend = FilesystemBackend(tmp_path)
    big = "x" * 4000
    chopper = _Chopper(
        chop_to="short",
        result=_success_result(answer=big),
        max_output_tokens=10,
        overflow_policy="truncate_with_reference",
    )
    async with open_test_run(backend=backend, record="off") as ctx:
        result = await dispatch(chopper, _Input(q="?"), ctx)

    assert result.status == "partial"
    assert result.output is not None and result.output.a == big
    assert result.error is not None
    assert "explicitly disabled recording" in result.error.detail
    assert await backend.list_references() == []


async def test_dispatch_truncate_keeps_run_on_failure_mode(
    tmp_path: Path, open_test_run: OpenTestRun
) -> None:
    backend = FilesystemBackend(tmp_path)
    big = "x" * 4000
    chopper = _Chopper(
        chop_to="short",
        result=_success_result(answer=big),
        persistence_mode="off",
        max_output_tokens=10,
        overflow_policy="truncate_with_reference",
    )
    async with open_test_run(backend=backend, record="on_failure") as ctx:
        result = await dispatch(chopper, _Input(q="?"), ctx)

    assert result.status == "partial"
    assert result.run_id is not None
    record = await backend.load(result.run_id)
    assert record is not None
    assert record.mode == "on_failure"
    assert record.result["output"]["a"] == big


async def test_dispatch_truncate_without_hook_degrades_to_partial(
    tmp_path: Path, open_test_run: OpenTestRun
) -> None:
    # The base truncate_output declines (returns None): only the author knows
    # how to shrink a typed output without invalidating it. Degrade as above.
    backend = FilesystemBackend(tmp_path)
    big = "x" * 4000
    stub = _Stub(
        result=_success_result(answer=big),
        max_output_tokens=10,
        overflow_policy="truncate_with_reference",
    )
    async with open_test_run(backend=backend) as ctx:
        result = await dispatch(stub, _Input(q="?"), ctx)

    assert result.status == "partial"
    assert result.output is not None and result.output.a == big
    assert result.error is not None
    assert result.error.kind == "output_too_large"
    assert "truncate_output" in result.error.detail


async def test_dispatch_truncate_oversized_chop_degrades_to_partial(
    tmp_path: Path, open_test_run: OpenTestRun
) -> None:
    # A chop that still exceeds the cap is an authoring bug; trust the cap,
    # not the chop, and degrade rather than return an oversized "truncation".
    backend = FilesystemBackend(tmp_path)
    big = "x" * 4000
    chopper = _Chopper(
        chop_to="y" * 2000,  # ~500 tokens, still over the cap of 10
        result=_success_result(answer=big),
        max_output_tokens=10,
        overflow_policy="truncate_with_reference",
    )
    async with open_test_run(backend=backend) as ctx:
        result = await dispatch(chopper, _Input(q="?"), ctx)

    assert result.status == "partial"
    assert result.output is not None and result.output.a == big
    assert result.error is not None
    assert result.error.kind == "output_too_large"
    assert "still above the cap" in result.error.detail


async def test_dispatch_truncate_store_failure_falls_back_to_full_output(
    open_test_run: OpenTestRun,
) -> None:
    # If the forced store fails, the chopped envelope's reference dangles;
    # returning it would silently lose data. Fall back to the full output.
    events: list[ProgressEvent] = []
    big = "x" * 4000
    chopper = _Chopper(
        chop_to="short",
        result=_success_result(answer=big),
        max_output_tokens=10,
        overflow_policy="truncate_with_reference",
    )
    async with open_test_run(backend=_FailingBackend(), on_progress=events.append) as ctx:
        result = await dispatch(chopper, _Input(q="?"), ctx)

    assert result.status == "partial"
    assert result.output is not None and result.output.a == big  # nothing lost
    assert result.error is not None
    assert result.error.kind == "output_too_large"
    assert "persistence backend failed" in result.error.detail
    assert any(e.phase == "persist_failed" for e in events)


# --- exception-to-failure tests --------------------------------------------


async def test_dispatch_converts_raised_exception_to_failure(
    open_test_run: OpenTestRun,
) -> None:
    async with open_test_run() as ctx:
        result = await dispatch(_Raiser(ValueError("boom")), _Input(q="?"), ctx)

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


async def test_dispatch_persists_raised_exception_on_failure(
    tmp_path: Path, open_test_run: OpenTestRun
) -> None:
    backend = FilesystemBackend(tmp_path)
    raiser = _Raiser(RuntimeError("kaboom"), persistence_mode="on_failure")
    async with open_test_run(backend=backend) as ctx:
        result = await dispatch(raiser, _Input(q="?"), ctx)

    # The gap this closes: a raising Commission still gets its failure recorded.
    assert result.run_id is not None
    refs = await backend.list_references()
    assert refs == [result.run_id]
    loaded = await backend.load(result.run_id)
    assert loaded is not None
    assert loaded.result["status"] == "failure"
    assert loaded.result["error"]["kind"] == "internal"


async def test_dispatch_does_not_swallow_cancellation(open_test_run: OpenTestRun) -> None:
    # CancelledError is a BaseException, not an Exception: task cancellation must
    # propagate, never be converted to an `internal` failure value.
    async with open_test_run() as ctx:
        with pytest.raises(asyncio.CancelledError):
            await dispatch(_Raiser(asyncio.CancelledError()), _Input(q="?"), ctx)


# --- stdlib logging tests ---------------------------------------------------


async def test_run_emits_stdlib_logs(
    caplog: pytest.LogCaptureFixture, open_test_run: OpenTestRun
) -> None:
    # The framework emits through standard logging at its choke points; an
    # application that sets a level sees every call and every LLM turn with
    # zero vibrantine-specific setup. INFO = one line per LLM round-trip
    # (from the loop) plus one line per completed call (from dispatch).
    fake = ScriptedLLM([llm_response(tool_calls=[("c1", "conclude", {"a": "done"})])])
    probe = _LoopProbe()

    with caplog.at_level(logging.INFO, logger="vibrantine"):
        async with open_test_run(models=[scripted_model(fake)]) as ctx:
            await dispatch(probe, _Input(q="hi"), ctx)

    assert "LLM turn model=" in caplog.text
    assert "loop_probe finished status=success" in caplog.text


async def test_conclude_validation_failure_logs_a_warning(
    caplog: pytest.LogCaptureFixture, open_test_run: OpenTestRun
) -> None:
    # The live-debugging lesson: a conclude that keeps failing validation must
    # be visible without persistence set up.
    fake = ScriptedLLM(
        [
            llm_response(tool_calls=[("c1", "conclude", {"wrong_field": 1})]),
            llm_response(tool_calls=[("c2", "conclude", {"a": "ok"})]),
        ]
    )
    probe = _LoopProbe()

    with caplog.at_level(logging.WARNING, logger="vibrantine"):
        async with open_test_run(models=[scripted_model(fake)]) as ctx:
            result = await dispatch(probe, _Input(q="hi"), ctx)

    assert result.status == "success"
    assert "conclude args failed to validate as _Output" in caplog.text


# --- llm_trace mailbox tests ------------------------------------------------


class _LoopProbe(Commission[_Input, _Output]):
    """A basic LLM-loop Commission for trace tests; toolbox set per instance."""

    name: ClassVar[str] = "loop_probe"
    description: ClassVar[str] = "Test probe riding the default LLM loop."
    input_type: ClassVar[type[BaseModel]] = _Input
    output_type: ClassVar[type[BaseModel]] = _Output
    system_prompt: ClassVar[str | None] = "Conclude."

    def build_user_message(self, input: _Input, ctx: CallContext) -> str:
        return input.q


async def test_loop_trace_lands_in_the_record(tmp_path: Path, open_test_run: OpenTestRun) -> None:
    backend = FilesystemBackend(tmp_path)
    fake = ScriptedLLM([llm_response(tool_calls=[("c1", "conclude", {"a": "done"})])])
    probe = _LoopProbe(persistence_mode="always")

    async with open_test_run(backend=backend, models=[scripted_model(fake)]) as ctx:
        result = await dispatch(probe, _Input(q="hello"), ctx)

    assert result.run_id is not None
    loaded = await backend.load(result.run_id)
    assert loaded is not None
    assert loaded.llm_trace is not None
    assert [m["role"] for m in loaded.llm_trace[:2]] == ["system", "user"]
    assert loaded.llm_trace[1]["content"] == "hello"
    # The concluding assistant turn is part of the transcript.
    assert any(m.get("tool_calls") for m in loaded.llm_trace)


async def test_loop_trace_survives_a_failed_run(tmp_path: Path, open_test_run: OpenTestRun) -> None:
    # Failure traces are the ones worth autopsying: two free-text replies end
    # the run as an internal failure, and the transcript (including the
    # corrective nudge) must still reach the record.
    backend = FilesystemBackend(tmp_path)
    fake = ScriptedLLM([llm_response(content="prose"), llm_response(content="more prose")])
    probe = _LoopProbe(persistence_mode="on_failure")

    async with open_test_run(backend=backend, models=[scripted_model(fake)]) as ctx:
        result = await dispatch(probe, _Input(q="hi"), ctx)

    assert result.status == "failure"
    assert result.run_id is not None
    loaded = await backend.load(result.run_id)
    assert loaded is not None
    assert loaded.llm_trace is not None
    nudges = [
        m for m in loaded.llm_trace if m["role"] == "user" and "conclude" in str(m["content"])
    ]
    assert nudges, "the corrective nudge should appear in the recorded transcript"


async def test_nested_traces_stay_with_their_own_records(
    tmp_path: Path, open_test_run: OpenTestRun
) -> None:
    # A parent loop dispatches a child loop mid-run. Each record must carry
    # exactly its own transcript: the mailbox re-hangs the parent's box after
    # the child call, so nothing merges or crosses.
    backend = FilesystemBackend(tmp_path)
    child_fake = ScriptedLLM([llm_response(tool_calls=[("cc", "conclude", {"a": "child-done"})])])
    child = _LoopProbe(model="fixture/child", persistence_mode="always")
    parent_fake = ScriptedLLM(
        [
            llm_response(tool_calls=[("p1", "loop_probe", {"q": "child-q"})]),
            llm_response(tool_calls=[("p2", "conclude", {"a": "parent-done"})]),
        ]
    )
    parent = _LoopProbe(
        model="fixture/parent",
        toolbox=(child,),
        persistence_mode="always",
    )

    async with open_test_run(
        backend=backend,
        models=[
            scripted_model(parent_fake, id="fixture/parent"),
            scripted_model(child_fake, id="fixture/child"),
        ],
        default_model="fixture/parent",
    ) as ctx:
        result = await dispatch(parent, _Input(q="parent-q"), ctx)

    assert result.status == "success", result.error
    parent_record = await backend.load(result.run_id or "")
    assert parent_record is not None and parent_record.llm_trace is not None
    child_ids = await backend.list_references(parent_run_id=result.run_id)
    assert len(child_ids) == 1
    child_record = await backend.load(child_ids[0])
    assert child_record is not None and child_record.llm_trace is not None

    # Child transcript: system, user, concluding assistant. Its user turn is
    # the tool-call args the parent sent, never the parent's own message.
    assert len(child_record.llm_trace) == 3
    assert child_record.llm_trace[1]["content"] == "child-q"
    assert "parent-q" not in str(child_record.llm_trace)

    # Parent transcript: system, user, delegating assistant, tool result,
    # concluding assistant. The child's *result* appears (that's the envelope
    # crossing the boundary); the child's interior turns do not.
    assert len(parent_record.llm_trace) == 5
    assert parent_record.llm_trace[1]["content"] == "parent-q"
    assert parent_record.llm_trace[3]["role"] == "tool"


# --- cost-honesty tests -----------------------------------------------------
# The rollup invariant ("children's dollars roll up structurally") is
# enforced code on the basic path and author discipline on the custom path.
# Dispatch observes the discipline: it compares the envelope's reported cost
# against the provider spend the run witnessed in the node's own subtree and
# warns on a material under-report. Log only; nothing branches.

# A single scripted turn at these counts prices to $0.20 at the fixture
# rates, far above the check's float-noise epsilon.
_SPENDY_IN_TOKENS = 100_000
_SPENDY_OUT_TOKENS = 50_000


class _ForgetfulParent(Commission[_Input, _Output]):
    """Dispatches an LLM child and reports $0: skips the rollup discipline."""

    name: ClassVar[str] = "forgetful_parent"
    description: ClassVar[str] = "custom parent that forgets to sum child cost"
    input_type: ClassVar[type[BaseModel]] = _Input
    output_type: ClassVar[type[BaseModel]] = _Output

    def __init__(self, child: Commission[_Input, _Output]) -> None:
        super().__init__()
        self._child = child

    async def _run(self, input: _Input, ctx: CallContext) -> CommissionResult[_Output]:
        await dispatch(self._child, input, ctx)
        return _success_result("forgot-the-cost")  # cost stays $0.0


class _SummingParent(Commission[_Input, _Output]):
    """Dispatches children and sums their envelope costs: the discipline kept.

    `extra_usd` models a cost the door never saw (a priced external API);
    over-reporting is legal and must stay silent.
    """

    name: ClassVar[str] = "summing_parent"
    description: ClassVar[str] = "custom parent that sums child cost"
    input_type: ClassVar[type[BaseModel]] = _Input
    output_type: ClassVar[type[BaseModel]] = _Output

    def __init__(
        self,
        *children: Commission[_Input, _Output],
        extra_usd: float = 0.0,
    ) -> None:
        super().__init__()
        self._children = children
        self._extra_usd = extra_usd

    async def _run(self, input: _Input, ctx: CallContext) -> CommissionResult[_Output]:
        results = await asyncio.gather(*[dispatch(child, input, ctx) for child in self._children])
        total = sum(r.cost.estimated_usd for r in results) + self._extra_usd
        return CommissionResult[_Output](
            status="success",
            output=_Output(a="summed"),
            provenance=Provenance(
                source="summing_parent", fetched_at=datetime.now(UTC), confidence="grounded"
            ),
            cost=CostMetrics(estimated_usd=total),
        )


class _SpendThenRaiseParent(Commission[_Input, _Output]):
    """Dispatches a spending child, then raises: exercises the backstop."""

    name: ClassVar[str] = "spend_then_raise"
    description: ClassVar[str] = "custom parent that raises after child spend"
    input_type: ClassVar[type[BaseModel]] = _Input
    output_type: ClassVar[type[BaseModel]] = _Output

    def __init__(self, child: Commission[_Input, _Output]) -> None:
        super().__init__()
        self._child = child

    async def _run(self, input: _Input, ctx: CallContext) -> CommissionResult[_Output]:
        await dispatch(self._child, input, ctx)
        raise RuntimeError("boom after spending")


def _spendy_conclude() -> SimpleNamespace:
    return llm_response(
        tool_calls=[("c1", "conclude", {"a": "done"})],
        in_tokens=_SPENDY_IN_TOKENS,
        out_tokens=_SPENDY_OUT_TOKENS,
    )


async def test_under_reported_cost_logs_a_warning(
    caplog: pytest.LogCaptureFixture, open_test_run: OpenTestRun
) -> None:
    fake = ScriptedLLM([_spendy_conclude()])
    parent = _ForgetfulParent(_LoopProbe())

    with caplog.at_level(logging.WARNING, logger="vibrantine"):
        async with open_test_run(models=[scripted_model(fake)]) as ctx:
            result = await dispatch(parent, _Input(q="hi"), ctx)

    # Observation only: the result flows through untouched.
    assert result.status == "success"
    assert result.cost.estimated_usd == 0.0
    assert "forgetful_parent" in caplog.text
    assert "under-reports" in caplog.text


async def test_summed_cost_logs_no_warning(
    caplog: pytest.LogCaptureFixture, open_test_run: OpenTestRun
) -> None:
    fake = ScriptedLLM([_spendy_conclude()])
    parent = _SummingParent(_LoopProbe())

    with caplog.at_level(logging.WARNING, logger="vibrantine"):
        async with open_test_run(models=[scripted_model(fake)]) as ctx:
            result = await dispatch(parent, _Input(q="hi"), ctx)

    assert result.status == "success"
    assert result.cost.estimated_usd > 0.0
    assert "under-reports" not in caplog.text


async def test_over_reported_cost_is_legal(
    caplog: pytest.LogCaptureFixture, open_test_run: OpenTestRun
) -> None:
    # An author may add costs the provider door never saw (a priced external
    # API); only under-reporting is a discipline breach.
    fake = ScriptedLLM([_spendy_conclude()])
    parent = _SummingParent(_LoopProbe(), extra_usd=5.0)

    with caplog.at_level(logging.WARNING, logger="vibrantine"):
        async with open_test_run(models=[scripted_model(fake)]) as ctx:
            result = await dispatch(parent, _Input(q="hi"), ctx)

    assert result.status == "success"
    assert "under-reports" not in caplog.text


async def test_basic_loop_rollup_passes_the_check(
    caplog: pytest.LogCaptureFixture, open_test_run: OpenTestRun
) -> None:
    # The basic path sums children automatically; a nested loop tree must
    # sail through the check silently, or every well-formed Commission would
    # cry wolf.
    child_fake = ScriptedLLM([_spendy_conclude()])
    child = _LoopProbe(model="fixture/child")
    parent_fake = ScriptedLLM(
        [
            llm_response(
                tool_calls=[("p1", "loop_probe", {"q": "child-q"})],
                in_tokens=_SPENDY_IN_TOKENS,
                out_tokens=_SPENDY_OUT_TOKENS,
            ),
            llm_response(
                tool_calls=[("p2", "conclude", {"a": "parent-done"})],
                in_tokens=_SPENDY_IN_TOKENS,
                out_tokens=_SPENDY_OUT_TOKENS,
            ),
        ]
    )
    parent = _LoopProbe(model="fixture/parent", toolbox=(child,))

    with caplog.at_level(logging.WARNING, logger="vibrantine"):
        async with open_test_run(
            models=[
                scripted_model(parent_fake, id="fixture/parent"),
                scripted_model(child_fake, id="fixture/child"),
            ],
            default_model="fixture/parent",
        ) as ctx:
            result = await dispatch(parent, _Input(q="parent-q"), ctx)

    assert result.status == "success", result.error
    assert "under-reports" not in caplog.text


async def test_backstop_envelope_is_exempt_from_the_check(
    caplog: pytest.LogCaptureFixture, open_test_run: OpenTestRun
) -> None:
    # A raising _run gets a framework-built envelope whose cost is a
    # documented best-effort floor (the node's own door calls; children's
    # spend excluded). Warning about a shortfall the framework itself chose
    # would blame the author for a ruled behavior.
    fake = ScriptedLLM([_spendy_conclude()])
    parent = _SpendThenRaiseParent(_LoopProbe())

    with caplog.at_level(logging.WARNING, logger="vibrantine"):
        async with open_test_run(models=[scripted_model(fake)]) as ctx:
            result = await dispatch(parent, _Input(q="hi"), ctx)

    assert result.status == "failure"
    assert result.error is not None and result.error.kind == "internal"
    assert "under-reports" not in caplog.text


async def test_gathered_siblings_do_not_witness_each_other(
    caplog: pytest.LogCaptureFixture, open_test_run: OpenTestRun
) -> None:
    # The witness is keyed through the register's subtree edges, not a
    # spend-before/spend-after delta: two honest siblings spending in
    # parallel must not see each other's dollars and warn falsely.
    fake = ScriptedLLM([_spendy_conclude(), _spendy_conclude()])
    parent = _SummingParent(_LoopProbe(), _LoopProbe())

    with caplog.at_level(logging.WARNING, logger="vibrantine"):
        async with open_test_run(models=[scripted_model(fake)]) as ctx:
            result = await dispatch(parent, _Input(q="hi"), ctx)

    assert result.status == "success"
    assert "under-reports" not in caplog.text
