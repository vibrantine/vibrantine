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
from typing import ClassVar, cast

import pytest
from openai import AsyncOpenAI
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
from vibrantine.testing import ScriptedLLM, llm_response

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


async def test_dispatch_no_opinion_and_no_ctx_record_stays_off(
    tmp_path: Path, open_test_run: OpenTestRun
) -> None:
    backend = FilesystemBackend(tmp_path)
    stub = _Stub()  # persistence_mode None (no opinion), no ctx.record either
    async with open_test_run(backend=backend) as ctx:
        await dispatch(stub, _Input(q="?"), ctx)

    assert await backend.list_references() == []


async def test_ctx_record_switches_on_the_whole_tree(
    tmp_path: Path, open_test_run: OpenTestRun
) -> None:
    # The caller's record= default reaches every no-opinion node, parent and
    # child alike, with no per-node flipping; the record stores the effective
    # mode the node ran under.
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


async def test_explicit_off_vetoes_ctx_record(tmp_path: Path, open_test_run: OpenTestRun) -> None:
    backend = FilesystemBackend(tmp_path)
    stub = _Stub(persistence_mode="off")
    async with open_test_run(backend=backend, record="always") as ctx:
        await dispatch(stub, _Input(q="?"), ctx)

    assert await backend.list_references() == []


async def test_explicit_mode_beats_ctx_record(tmp_path: Path, open_test_run: OpenTestRun) -> None:
    # Node says on_failure, caller says always: the node's word is kept, so
    # this success is not recorded.
    backend = FilesystemBackend(tmp_path)
    stub = _Stub(persistence_mode="on_failure")
    async with open_test_run(backend=backend, record="always") as ctx:
        await dispatch(stub, _Input(q="?"), ctx)

    assert await backend.list_references() == []


async def test_dispatch_no_backend_skips_even_with_active_mode(
    tmp_path: Path, open_test_run: OpenTestRun
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


async def test_dispatch_failing_backend_does_not_destroy_the_result(
    open_test_run: OpenTestRun,
) -> None:
    # Persistence is observability: a backend that raises on store must not
    # break errors-as-values or eat the result it was recording.
    events: list[ProgressEvent] = []
    stub = _Stub(persistence_mode="always")
    async with open_test_run(backend=_ExplodingBackend(), on_progress=events.append) as ctx:
        result = await dispatch(stub, _Input(q="?"), ctx)

    assert result.status == "success"
    assert result.output is not None
    assert any(e.phase == "persist_failed" and "disk full" in (e.detail or "") for e in events)


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
    probe = _LoopProbe(client=cast(AsyncOpenAI, fake))

    with caplog.at_level(logging.INFO, logger="vibrantine"):
        async with open_test_run() as ctx:
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
    probe = _LoopProbe(client=cast(AsyncOpenAI, fake))

    with caplog.at_level(logging.WARNING, logger="vibrantine"):
        async with open_test_run() as ctx:
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
    probe = _LoopProbe(client=cast(AsyncOpenAI, fake), persistence_mode="always")

    async with open_test_run(backend=backend) as ctx:
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
    probe = _LoopProbe(client=cast(AsyncOpenAI, fake), persistence_mode="on_failure")

    async with open_test_run(backend=backend) as ctx:
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
    child = _LoopProbe(client=cast(AsyncOpenAI, child_fake), persistence_mode="always")
    parent_fake = ScriptedLLM(
        [
            llm_response(tool_calls=[("p1", "loop_probe", {"q": "child-q"})]),
            llm_response(tool_calls=[("p2", "conclude", {"a": "parent-done"})]),
        ]
    )
    parent = _LoopProbe(
        client=cast(AsyncOpenAI, parent_fake),
        toolbox=(child,),
        persistence_mode="always",
    )

    async with open_test_run(backend=backend) as ctx:
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
